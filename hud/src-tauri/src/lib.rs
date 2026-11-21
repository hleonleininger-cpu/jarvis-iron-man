//! JARVIS HUD als Desktop-App (M3.2, M3.5, Rest von M0.5). Der Rust-Teil spricht mit dem Core,
//! die Oberfläche bekommt nur Ereignisse (ADR-024). Systemweit: Not-Aus Strg+Alt+N, Overlay
//! Strg+Alt+O. Tray-Menü mit HUD zeigen, Overlay, Not-Aus, Autostart, Beenden. Das Schließen des
//! Fensters versteckt es nur: Kürzel und Not-Aus bleiben aktiv, Beenden geht über das Tray.

mod core;
mod sse;

use std::collections::hash_map::Entry;
use std::collections::HashMap;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use serde::Serialize;
use tauri::async_runtime::JoinHandle;
use tauri::menu::{CheckMenuItem, Menu, MenuItem, PredefinedMenuItem};
use tauri::tray::TrayIconBuilder;
use tauri::{AppHandle, Emitter, Manager, State, WindowEvent};
use tauri_plugin_autostart::{MacosLauncher, ManagerExt};
use tauri_plugin_global_shortcut::{GlobalShortcutExt, Shortcut, ShortcutState};

use crate::core::{local_base, Core, DEFAULT_CORE_URL};
use crate::sse::ServerEvent;

const STOP_TIMEOUT: Duration = Duration::from_secs(5);
const RETRY_MIN: Duration = Duration::from_secs(1);
const RETRY_MAX: Duration = Duration::from_secs(10);
/// N wie Not-Aus: auf deutscher Tastatur ohne AltGr-Zeichen. Windows meldet AltGr als Strg+Alt;
/// Strg+Alt+Q (= AltGr+Q = "@") würde jedes getippte @ schlucken. Strg+Alt+F12 öffnet auf vielen
/// Windows-Rechnern die Intel-Grafiksteuerung und ist unter X11 ein Konsolenwechsel.
pub const DEFAULT_STOP_KEY: &str = "ctrl+alt+n";
pub const OVERLAY_KEY: &str = "ctrl+alt+o";
/// Kürzel des Sprachclients (M2.13): das HUD darf sie nie belegen.
const VOICE_KEYS: [&str; 4] = ["ctrl+alt+j", "ctrl+alt+s", "ctrl+alt+x", "ctrl+alt+d"];
/// Tasten mit AltGr-Zeichen auf der deutschen Tastatur (@ € µ ² ³ { [ ] } \ ~ |).
const ALTGR_KEYS: [&str; 22] = [
    "q",
    "e",
    "m",
    "2",
    "3",
    "7",
    "8",
    "9",
    "0",
    "digit2",
    "digit3",
    "digit7",
    "digit8",
    "digit9",
    "digit0",
    "minus",
    "ß",
    "bracketright",
    "+",
    "intlbackslash",
    "<",
    "keyq",
];

struct Hud {
    core: Arc<Core>,
    /// Der laufende Ereignisstrom; ein neuer Aufruf (Neuladen der Seite) ersetzt ihn.
    events: Mutex<Option<JoinHandle<()>>>,
    chats: Mutex<HashMap<u64, JoinHandle<()>>>,
    overlay: AtomicBool,
    stop_key: Mutex<Option<Shortcut>>,
    /// Anzeige des Not-Aus-Kürzels im Tray, z. B. "ctrl+alt+n".
    stop_label: Mutex<String>,
    overlay_key: Mutex<Option<Shortcut>>,
    /// Kürzel, die sich nicht registrieren ließen (z. B. von einem anderen Programm belegt).
    problems: Mutex<Vec<String>>,
}

#[derive(Clone, Serialize)]
struct Connection {
    connected: bool,
    problem: Option<String>,
}

#[derive(Clone, Serialize)]
struct ChatEvent {
    id: u64,
    event: String,
    data: serde_json::Map<String, serde_json::Value>,
}

/// Normalisiert und prüft das Not-Aus-Kürzel (einstellbar über JARVIS_HUD_STOP_KEY).
pub fn stop_key_from(raw: Option<&str>) -> Result<String, String> {
    let key = raw
        .unwrap_or(DEFAULT_STOP_KEY)
        .trim()
        .to_lowercase()
        .replace(' ', "");
    if VOICE_KEYS.contains(&key.as_str()) || key == OVERLAY_KEY {
        return Err(format!(
            "Das Not-Aus-Kürzel {key} ist schon belegt (Sprachclient/Overlay)."
        ));
    }
    let parts: Vec<&str> = key.split('+').collect();
    // Strg UND Alt: sonst würden systemweit Kürzel wie Strg+C oder Alt+F4 gekapert.
    if !(parts.contains(&"ctrl") && parts.contains(&"alt")) {
        return Err(format!(
            "Das Not-Aus-Kürzel {key} braucht Strg und Alt (z. B. ctrl+alt+n)."
        ));
    }
    let last = parts.last().copied().unwrap_or_default();
    if !parts.contains(&"shift") && ALTGR_KEYS.contains(&last) {
        return Err(format!(
            "Das Not-Aus-Kürzel {key} ist auf deutscher Tastatur ein AltGr-Zeichen (z. B. @) und würde beim Tippen auslösen."
        ));
    }
    key.parse::<Shortcut>()
        .map_err(|_| format!("Ungültiges Kürzel: {key}"))?;
    Ok(key)
}

fn emit<T: Serialize + Clone>(app: &AppHandle, name: &str, payload: T) {
    let _ = app.emit(name, payload);
}

// ------------------------------------------------------------------------------------ Befehle
#[tauri::command]
/// Startet den Ereignisstrom. Ein erneuter Aufruf (Neuladen der Seite, Absturz der Webview)
/// beendet den alten Strom und die Chats der alten Seite und verbindet neu; der Core schickt
/// dann einen frischen `snapshot`.
fn events_start(app: AppHandle, hud: State<'_, Hud>) {
    for (_, task) in hud.chats.lock().expect("chats").drain() {
        task.abort();
    }
    let core = hud.core.clone();
    let task = tauri::async_runtime::spawn(async move {
        let mut delay = RETRY_MIN;
        loop {
            let opened = AtomicBool::new(false);
            let result = core
                .stream(
                    core.events_request(),
                    |event: ServerEvent| emit(&app, "hud://server", event),
                    || {
                        opened.store(true, Ordering::SeqCst);
                        emit(
                            &app,
                            "hud://connection",
                            Connection {
                                connected: true,
                                problem: None,
                            },
                        );
                    },
                )
                .await;
            let problem = match result {
                Ok(()) => "Die Verbindung zum Core ist abgerissen.".to_string(),
                Err(problem) => problem,
            };
            if opened.load(Ordering::SeqCst) {
                delay = RETRY_MIN;
            }
            emit(
                &app,
                "hud://connection",
                Connection {
                    connected: false,
                    problem: Some(problem),
                },
            );
            tokio::time::sleep(delay).await;
            delay = (delay * 2).min(RETRY_MAX);
        }
    });
    if let Some(old) = hud.events.lock().expect("events").replace(task) {
        old.abort();
    }
}

#[tauri::command]
/// `id` vergibt die Oberfläche selbst: so kann sie Ereignisse zuordnen, die schon kommen, bevor
/// dieser Aufruf zurückkehrt. Eine bereits laufende Nummer wird abgelehnt.
fn chat_start(
    app: AppHandle,
    hud: State<'_, Hud>,
    id: u64,
    message: String,
    session_id: Option<String>,
) -> Result<(), String> {
    let core = hud.core.clone();
    // Prüfen und Eintragen unter einer Sperre, auch über das Starten hinweg: ein sehr schneller
    // Chat trägt sich sonst aus, bevor er eingetragen ist, und bliebe als Leiche in der Liste.
    let mut chats = hud.chats.lock().expect("chats");
    let Entry::Vacant(slot) = chats.entry(id) else {
        return Err(format!("Chat {id} läuft schon."));
    };
    let task = tauri::async_runtime::spawn(async move {
        let request = core.chat_request(&message, session_id.as_deref());
        let result = core
            .stream(
                request,
                |event: ServerEvent| {
                    emit(
                        &app,
                        "hud://chat",
                        ChatEvent {
                            id,
                            event: event.event,
                            data: event.data,
                        },
                    )
                },
                || {},
            )
            .await;
        if let Err(problem) = result {
            let mut data = serde_json::Map::new();
            data.insert("message".into(), problem.into());
            emit(
                &app,
                "hud://chat",
                ChatEvent {
                    id,
                    event: "error".into(),
                    data,
                },
            );
        }
        emit(&app, "hud://chat_end", id);
        if let Some(hud) = app.try_state::<Hud>() {
            hud.chats.lock().expect("chats").remove(&id);
        }
    });
    slot.insert(task);
    Ok(())
}

#[tauri::command]
fn chat_cancel(app: AppHandle, hud: State<'_, Hud>, id: u64) {
    if let Some(task) = hud.chats.lock().expect("chats").remove(&id) {
        task.abort();
        emit(&app, "hud://chat_end", id);
    }
}

#[tauri::command]
async fn confirm(hud: State<'_, Hud>, id: String, approve: bool) -> Result<bool, String> {
    Ok(hud.core.confirm(&id, approve).await)
}

#[tauri::command]
async fn stop(app: AppHandle, hud: State<'_, Hud>) -> Result<bool, String> {
    Ok(emergency_stop(&app, &hud).await)
}

#[tauri::command]
fn shortcut_problems(hud: State<'_, Hud>) -> Vec<String> {
    hud.problems.lock().expect("problems").clone()
}

#[tauri::command]
fn overlay_state(hud: State<'_, Hud>) -> bool {
    hud.overlay.load(Ordering::SeqCst)
}

// ---------------------------------------------------------------------------- Not-Aus, Overlay
/// Not-Aus: bricht die eigenen Chat-Ströme ab und ruft POST /v1/stop direkt aus Rust, also auch
/// dann, wenn die Oberfläche hängt. Ergebnis geht als Ereignis an die Oberfläche.
async fn emergency_stop(app: &AppHandle, hud: &Hud) -> bool {
    let chats: Vec<(u64, JoinHandle<()>)> = hud.chats.lock().expect("chats").drain().collect();
    for (id, task) in chats {
        task.abort();
        emit(app, "hud://chat_end", id);
    }
    let ok = hud.core.stop(STOP_TIMEOUT).await;
    emit(app, "hud://stop_result", ok);
    ok
}

fn set_overlay(app: &AppHandle, hud: &Hud, on: bool) {
    hud.overlay.store(on, Ordering::SeqCst);
    if let Some(window) = app.get_webview_window("main") {
        // Overlay: rahmenlos, immer vorn, Klicks gehen durch. Not-Aus und Verlassen dann über
        // Kürzel und Tray (der rote Knopf ist im Overlay nicht klickbar).
        let _ = window.set_decorations(!on);
        let _ = window.set_always_on_top(on);
        let _ = window.set_ignore_cursor_events(on);
        if on {
            let _ = window.unminimize();
            let _ = window.show();
        }
    }
    emit(app, "hud://overlay", on);
}

fn toggle_overlay(app: &AppHandle) {
    if let Some(hud) = app.try_state::<Hud>() {
        let on = !hud.overlay.load(Ordering::SeqCst);
        set_overlay(app, &hud, on);
    }
}

fn spawn_stop(app: &AppHandle) {
    let app = app.clone();
    tauri::async_runtime::spawn(async move {
        if let Some(hud) = app.try_state::<Hud>() {
            emergency_stop(&app, &hud).await;
        }
    });
}

fn show_main(app: &AppHandle) {
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.show();
        let _ = window.unminimize();
        let _ = window.set_focus();
    }
}

// ------------------------------------------------------------------------------- Kürzel, Tray
fn register_shortcuts(app: &AppHandle, hud: &Hud) {
    let mut problems = Vec::new();
    if let Err(problem) =
        local_base(&std::env::var("JARVIS_CORE_URL").unwrap_or_else(|_| DEFAULT_CORE_URL.into()))
    {
        problems.push(problem);
    }
    match stop_key_from(std::env::var("JARVIS_HUD_STOP_KEY").ok().as_deref()) {
        Ok(key) => match key.parse::<Shortcut>() {
            Ok(shortcut) if app.global_shortcut().register(shortcut).is_ok() => {
                *hud.stop_key.lock().expect("key") = Some(shortcut);
                *hud.stop_label.lock().expect("label") = key.clone();
            }
            _ => problems.push(format!(
                "Not-Aus-Kürzel {key} nicht verfügbar (von einem anderen Programm belegt?). Not-Aus über das Tray-Symbol oder den roten Knopf."
            )),
        },
        Err(problem) => problems.push(problem),
    }
    match OVERLAY_KEY.parse::<Shortcut>() {
        Ok(shortcut) if app.global_shortcut().register(shortcut).is_ok() => {
            *hud.overlay_key.lock().expect("key") = Some(shortcut);
        }
        _ => problems.push(format!(
            "Overlay-Kürzel {OVERLAY_KEY} nicht verfügbar. Overlay über das Tray-Symbol."
        )),
    }
    for problem in &problems {
        emit(app, "hud://notice", problem.clone());
    }
    *hud.problems.lock().expect("problems") = problems;
}

fn on_shortcut(app: &AppHandle, shortcut: &Shortcut, state: ShortcutState) {
    if state != ShortcutState::Pressed {
        return;
    }
    let Some(hud) = app.try_state::<Hud>() else {
        return;
    };
    let is_stop = hud.stop_key.lock().expect("key").as_ref() == Some(shortcut);
    let is_overlay = hud.overlay_key.lock().expect("key").as_ref() == Some(shortcut);
    if is_stop {
        spawn_stop(app);
    } else if is_overlay {
        toggle_overlay(app);
    }
}

fn build_tray(app: &AppHandle) -> tauri::Result<()> {
    let autostart = app.autolaunch().is_enabled().unwrap_or(false);
    let show = MenuItem::with_id(app, "show", "HUD zeigen", true, None::<&str>)?;
    let overlay = MenuItem::with_id(
        app,
        "overlay",
        "Overlay an/aus (Strg+Alt+O)",
        true,
        None::<&str>,
    )?;
    let stop_label = match app
        .try_state::<Hud>()
        .and_then(|hud| hud.stop_label.lock().ok().map(|l| l.clone()))
    {
        Some(label) => format!("NOT-AUS ({label})"),
        None => "NOT-AUS".to_string(),
    };
    let stop = MenuItem::with_id(app, "stop", stop_label, true, None::<&str>)?;
    // Im Entwicklungsmodus (`tauri dev`) nicht: Windows startete sonst die Debug-Exe ohne Vite.
    let installed = !cfg!(debug_assertions);
    let start = CheckMenuItem::with_id(
        app,
        "autostart",
        if installed {
            "Mit Windows starten"
        } else {
            "Mit Windows starten (nur installierte App)"
        },
        installed,
        autostart,
        None::<&str>,
    )?;
    let quit = MenuItem::with_id(app, "quit", "Beenden", true, None::<&str>)?;
    let separator = PredefinedMenuItem::separator(app)?;
    let menu = Menu::with_items(app, &[&show, &overlay, &stop, &separator, &start, &quit])?;
    let mut tray = TrayIconBuilder::with_id("jarvis")
        .tooltip("JARVIS HUD")
        .menu(&menu);
    if let Some(icon) = app.default_window_icon() {
        tray = tray.icon(icon.clone());
    }
    let toggle = start.clone();
    tray.on_menu_event(move |app, event| match event.id.as_ref() {
        "show" => show_main(app),
        "overlay" => toggle_overlay(app),
        "stop" => spawn_stop(app),
        "autostart" => {
            let manager = app.autolaunch();
            let enabled = manager.is_enabled().unwrap_or(false);
            let _ = if enabled {
                manager.disable()
            } else {
                manager.enable()
            };
            let _ = toggle.set_checked(manager.is_enabled().unwrap_or(false));
        }
        "quit" => app.exit(0),
        _ => {}
    })
    .build(app)?;
    Ok(())
}

// ---------------------------------------------------------------------------------- Start
#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let raw = std::env::var("JARVIS_CORE_URL").unwrap_or_else(|_| DEFAULT_CORE_URL.into());
    // Eine nicht lokale Adresse beendet die App nicht still (im fertigen Bau gibt es keine
    // Konsole): sie startet, verbindet sich nicht und zeigt den Grund.
    let base = local_base(&raw);
    tauri::Builder::default()
        .plugin(
            tauri_plugin_global_shortcut::Builder::new()
                .with_handler(|app, shortcut, event| on_shortcut(app, shortcut, event.state()))
                .build(),
        )
        // Autostart standardmäßig AUS; nur der Tray-Schalter ändert es.
        .plugin(tauri_plugin_autostart::init(
            MacosLauncher::LaunchAgent,
            None,
        ))
        .manage(Hud {
            core: Arc::new(Core::new(base)),
            events: Mutex::new(None),
            stop_label: Mutex::new(String::new()),
            chats: Mutex::new(HashMap::new()),
            overlay: AtomicBool::new(false),
            stop_key: Mutex::new(None),
            overlay_key: Mutex::new(None),
            problems: Mutex::new(Vec::new()),
        })
        .setup(|app| {
            let handle = app.handle().clone();
            let hud = app.state::<Hud>();
            register_shortcuts(&handle, &hud);
            build_tray(&handle)?;
            Ok(())
        })
        // X versteckt das Fenster nur: Tray, Not-Aus-Kürzel und Overlay bleiben aktiv.
        .on_window_event(|window, event| {
            if let WindowEvent::CloseRequested { api, .. } = event {
                api.prevent_close();
                let _ = window.hide();
            }
        })
        .invoke_handler(tauri::generate_handler![
            events_start,
            chat_start,
            chat_cancel,
            confirm,
            stop,
            shortcut_problems,
            overlay_state
        ])
        .run(tauri::generate_context!())
        .expect("JARVIS HUD konnte nicht starten");
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn stop_key_defaults_and_rejects_collisions() {
        assert_eq!(stop_key_from(None).unwrap(), "ctrl+alt+n");
        assert_eq!(stop_key_from(Some("Ctrl + Alt + P")).unwrap(), "ctrl+alt+p");
        assert!(stop_key_from(Some("ctrl+alt+x")).is_err()); // Sprachclient "unterbrechen"
        assert!(stop_key_from(Some("ctrl+alt+o")).is_err()); // Overlay
        assert!(stop_key_from(Some("shift+q")).is_err()); // ohne Strg/Alt: tippt man versehentlich
        assert!(stop_key_from(Some("ctrl+c")).is_err()); // gekaperte Systemkürzel
        assert!(stop_key_from(Some("alt+f4")).is_err());
        assert!(stop_key_from(Some("ctrl+alt+")).is_err());
    }

    #[test]
    fn stop_key_rejects_altgr_characters_on_german_keyboards() {
        for key in [
            "ctrl+alt+q",
            "ctrl+alt+e",
            "ctrl+alt+m",
            "ctrl+alt+7",
            "ctrl+alt+0",
        ] {
            assert!(
                stop_key_from(Some(key)).is_err(),
                "{key} ist ein AltGr-Zeichen"
            );
        }
        assert!(stop_key_from(Some("ctrl+alt+shift+q")).is_ok()); // AltGr+Shift+Q tippt nichts
    }
}
