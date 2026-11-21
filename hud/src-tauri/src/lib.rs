//! JARVIS HUD als Desktop-App (M3.2, M3.5, Rest von M0.5). Der Rust-Teil spricht mit dem Core,
//! die Oberfläche bekommt nur Ereignisse (ADR-024). Systemweit: Not-Aus Strg+Alt+Q, Overlay
//! Strg+Alt+O. Tray-Menü mit HUD zeigen, Overlay, Not-Aus, Autostart, Beenden.

mod core;
mod sse;

use std::collections::HashMap;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use serde::Serialize;
use tauri::async_runtime::JoinHandle;
use tauri::menu::{CheckMenuItem, Menu, MenuItem, PredefinedMenuItem};
use tauri::tray::TrayIconBuilder;
use tauri::{AppHandle, Emitter, Manager, State};
use tauri_plugin_autostart::{MacosLauncher, ManagerExt};
use tauri_plugin_global_shortcut::{GlobalShortcutExt, Shortcut, ShortcutState};

use crate::core::{local_base, Core, DEFAULT_CORE_URL};
use crate::sse::ServerEvent;

const STOP_TIMEOUT: Duration = Duration::from_secs(5);
const RETRY_MIN: Duration = Duration::from_secs(1);
const RETRY_MAX: Duration = Duration::from_secs(10);
pub const DEFAULT_STOP_KEY: &str = "ctrl+alt+q";
pub const OVERLAY_KEY: &str = "ctrl+alt+o";
/// Kürzel des Sprachclients (M2.13): das HUD darf sie nie belegen.
const VOICE_KEYS: [&str; 4] = ["ctrl+alt+j", "ctrl+alt+m", "ctrl+alt+x", "ctrl+alt+d"];

struct Hud {
    core: Arc<Core>,
    events_started: AtomicBool,
    chats: Mutex<HashMap<u64, JoinHandle<()>>>,
    overlay: AtomicBool,
    stop_key: Mutex<Option<Shortcut>>,
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
    if !(key.contains("ctrl") || key.contains("alt") || key.contains("super")) {
        return Err(format!(
            "Das Not-Aus-Kürzel {key} braucht Strg, Alt oder die Windows-Taste."
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
fn events_start(app: AppHandle, hud: State<'_, Hud>) {
    if hud.events_started.swap(true, Ordering::SeqCst) {
        return; // läuft schon (z. B. nach Neuladen der Seite): einfach weiter empfangen
    }
    let core = hud.core.clone();
    tauri::async_runtime::spawn(async move {
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
    if hud.chats.lock().expect("chats").contains_key(&id) {
        return Err(format!("Chat {id} läuft schon."));
    }
    let core = hud.core.clone();
    // Sperre über das Starten hinweg: ein sehr schneller Chat trägt sich sonst aus, bevor er
    // eingetragen ist, und bliebe als Leiche in der Liste.
    let mut chats = hud.chats.lock().expect("chats");
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
    chats.insert(id, task);
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
fn set_token(hud: State<'_, Hud>, token: Option<String>) {
    hud.core.set_token(token);
}

#[tauri::command]
fn shortcut_problems(hud: State<'_, Hud>) -> Vec<String> {
    hud.problems.lock().expect("problems").clone()
}

#[tauri::command]
fn overlay_set(app: AppHandle, hud: State<'_, Hud>, on: bool) {
    set_overlay(&app, &hud, on);
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
    match stop_key_from(std::env::var("JARVIS_HUD_STOP_KEY").ok().as_deref()) {
        Ok(key) => match key.parse::<Shortcut>() {
            Ok(shortcut) if app.global_shortcut().register(shortcut).is_ok() => {
                *hud.stop_key.lock().expect("key") = Some(shortcut);
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
    let stop = MenuItem::with_id(app, "stop", "NOT-AUS (Strg+Alt+Q)", true, None::<&str>)?;
    let start = CheckMenuItem::with_id(
        app,
        "autostart",
        "Mit Windows starten",
        true,
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
    let base = match local_base(&raw) {
        Ok(base) => base,
        Err(problem) => {
            eprintln!("{problem}");
            std::process::exit(2);
        }
    };
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
            events_started: AtomicBool::new(false),
            chats: Mutex::new(HashMap::new()),
            overlay: AtomicBool::new(false),
            stop_key: Mutex::new(None),
            overlay_key: Mutex::new(None),
            problems: Mutex::new(Vec::new()),
        })
        .setup(|app| {
            let handle = app.handle().clone();
            build_tray(&handle)?;
            let hud = app.state::<Hud>();
            register_shortcuts(&handle, &hud);
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            events_start,
            chat_start,
            chat_cancel,
            confirm,
            stop,
            set_token,
            shortcut_problems,
            overlay_set
        ])
        .run(tauri::generate_context!())
        .expect("JARVIS HUD konnte nicht starten");
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn stop_key_defaults_and_rejects_collisions() {
        assert_eq!(stop_key_from(None).unwrap(), "ctrl+alt+q");
        assert_eq!(stop_key_from(Some("Ctrl + Alt + P")).unwrap(), "ctrl+alt+p");
        assert!(stop_key_from(Some("ctrl+alt+x")).is_err()); // Sprachclient "unterbrechen"
        assert!(stop_key_from(Some("ctrl+alt+o")).is_err()); // Overlay
        assert!(stop_key_from(Some("shift+q")).is_err()); // ohne Strg/Alt: tippt man versehentlich
        assert!(stop_key_from(Some("ctrl+alt+")).is_err());
    }
}
