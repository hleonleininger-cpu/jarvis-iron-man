import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";
import type { Transport } from "./api/transport";
import { ActivityList } from "./components/ActivityList";
import { BootSequence } from "./components/BootSequence";
import { ChatPanel } from "./components/ChatPanel";
import { ConfirmCard } from "./components/ConfirmCard";
import { StateRing } from "./components/StateRing";
import { SettingsMenu } from "./components/SettingsMenu";
import { StopButton } from "./components/StopButton";
import { TileGrid } from "./components/TileGrid";
import { Visualizer } from "./components/Visualizer";
import { loadSettings, saveSettings, type Settings } from "./settings";
import { createSounds, type SoundName } from "./sound";
import { focusOf, overlayPanels } from "./situation";
import { initialState, micOf, reducer, ringOf, type Ring } from "./state";
import { parseTiles, type Tiles } from "./tiles";

const TICK_MS = 2000; // prüft, ob der Sprachclient noch lebt
const TILES_MS = 60_000; // Kacheln einmal je Minute (der Core holt Termine/Aufgaben nur alle 5 min)
const STOP_NOTICE_MS = 4000;

export function App({ transport, now = Date.now }: { transport: Transport; now?: () => number }) {
  const [state, dispatch] = useReducer(reducer, initialState);
  const [clock, setClock] = useState(now);
  const chatAbort = useRef<AbortController | null>(null);
  const [overlay, setOverlay] = useState(false);
  const [tiles, setTiles] = useState<Tiles | null>(null);
  const [tilesProblem, setTilesProblem] = useState<string | null>(null);
  const [settings, setSettings] = useState<Settings>(loadSettings);
  const reducedMotion = useMemo(() => globalThis.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false, []);
  const [booting, setBooting] = useState(() => settings.boot && !reducedMotion);
  const endBoot = useCallback(() => setBooting(false), []);
  const sounds = useMemo(() => createSounds(), []);
  const soundRef = useRef(settings);
  useEffect(() => {
    soundRef.current = settings;
  }, [settings]);
  const play = useCallback((name: SoundName) => {
    if (soundRef.current.sound) sounds.play(name, soundRef.current.volume);
  }, [sounds]);

  const changeSettings = (next: Settings) => {
    setSettings(next);
    saveSettings(next);
  };

  useEffect(
    () =>
      transport.subscribe(
        (event) => dispatch({ type: "server", event, now: now() }),
        (connected, problem) => dispatch({ type: "connection", connected, problem }),
      ),
    [transport, now],
  );

  // Nur in der Desktop-App: Overlay, Hinweise (belegtes Kürzel), Not-Aus per Kürzel oder Tray.
  useEffect(
    () =>
      transport.desktop?.({
        overlay: setOverlay,
        notice: (text) => dispatch({ type: "notice", text }),
        stopResult: (ok) => dispatch({ type: "stop_result", ok, now: now() }),
      }),
    [transport, now],
  );

  useEffect(() => {
    // Im Overlay ist das Fenster durchsichtig: dafür muss auch der Seitenhintergrund weg.
    document.documentElement.classList.toggle("overlay", overlay);
  }, [overlay]);

  // Live-Kacheln (M3.6): beim Start und dann einmal je Minute. Fehler lassen den alten Stand stehen.
  useEffect(() => {
    let active = true;
    const load = () =>
      transport
        .tiles()
        .then((raw) => {
          if (!active) return;
          setTiles(parseTiles(raw));
          setTilesProblem(null);
        })
        .catch((error: unknown) => {
          if (active) setTilesProblem(error instanceof Error ? error.message : "Kacheln nicht erreichbar.");
        });
    void load();
    const timer = setInterval(() => void load(), TILES_MS);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [transport]);

  useEffect(() => {
    const timer = setInterval(() => setClock(now()), TICK_MS);
    return () => clearInterval(timer);
  }, [now]);

  const stop = useCallback(() => {
    chatAbort.current?.abort();
    void transport.stop().then((ok) => dispatch({ type: "stop_result", ok, now: now() }));
  }, [transport, now]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.ctrlKey && event.key === ".") {
        event.preventDefault();
        stop();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [stop]);

  const send = (message: string) => {
    const controller = new AbortController();
    chatAbort.current = controller;
    dispatch({ type: "chat_start", message });
    void transport
      .chat(message, state.sessionId, (event) => dispatch({ type: "chat", event }), controller.signal)
      .finally(() => dispatch({ type: "chat_end" }));
  };

  const answer = (id: string, approve: boolean) => {
    // Die Karte verschwindet erst, wenn der Core die Antwort angenommen hat.
    void transport.confirm(id, approve).then((ok) => dispatch({ type: ok ? "confirm_answered" : "confirm_failed", id }));
  };

  const current = Math.max(clock, now());
  const stopped = state.stoppedAt !== null && current - state.stoppedAt < STOP_NOTICE_MS;
  const ring = ringOf(state, current);
  // Situatives HUD (M3.7): was gerade wichtig ist, steht vorn; im Overlay nur das.
  const { focus, why } = focusOf({ state, tiles, now: current });
  const shown = overlay ? overlayPanels(focus) : { activity: true, tiles: true };

  // Töne (M3.3) bei Übergängen: verbunden, Zuhören beginnt, neue Rückfrage, Not-Aus, Fehler.
  const previous = useRef<{ connected: boolean; ring: Ring; confirms: number; stoppedAt: number | null; failed: boolean }>({
    connected: false,
    ring: "offline",
    confirms: 0,
    stoppedAt: null,
    failed: false,
  });
  useEffect(() => {
    const before = previous.current;
    if (state.connected && !before.connected) play("boot");
    if (ring === "listening" && before.ring !== "listening") play("listen");
    if (state.confirms.length > before.confirms) play("confirm");
    if (state.stoppedAt !== null && state.stoppedAt !== before.stoppedAt) play("stop");
    if (state.stopFailed && !before.failed) play("error");
    previous.current = {
      connected: state.connected,
      ring,
      confirms: state.confirms.length,
      stoppedAt: state.stoppedAt,
      failed: state.stopFailed,
    };
  }, [state.connected, ring, state.confirms.length, state.stoppedAt, state.stopFailed, play]);

  return (
    <div className={overlay ? "hud overlay" : "hud"}>
      {booting ? <BootSequence connected={state.connected} onDone={endBoot} /> : null}
      <header className="top">
        <h1>J.A.R.V.I.S.</h1>
        <span className={`link link-${state.connected ? "up" : "down"}`}>
          {state.connected ? "Core verbunden" : (state.problem ?? "Verbinde …")}
        </span>
        {state.screenRead ? (
          <span className="eye" role="status" title="Jarvis liest gerade den Bildschirm">
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path d="M1 12s4-7 11-7 11 7 11 7-4 7-11 7S1 12 1 12z" />
              <circle cx="12" cy="12" r="3.5" />
            </svg>
            liest Bildschirm
          </span>
        ) : null}
        <SettingsMenu settings={settings} onChange={changeSettings} />
        <StopButton onStop={stop} failed={state.stopFailed} />
      </header>
      {stopped ? (
        <div className="stopped" role="alert">
          Not-Aus: alles angehalten.
        </div>
      ) : null}
      <main className={`focus-${focus}`} data-why={why}>
        <div className="center">
          <StateRing ring={ring} mic={micOf(state, current)}>
            <Visualizer level={state.level} now={now} />
          </StateRing>
          <div className="confirms">
            {state.confirms.map((item) => (
              <ConfirmCard key={item.id} item={item} onAnswer={answer} />
            ))}
          </div>
        </div>
        {shown.activity ? <ActivityList activities={state.activities} focused={focus === "activity"} /> : null}
        {overlay ? null : <ChatPanel messages={state.messages} busy={state.chatting} onSend={send} />}
      </main>
      {shown.tiles ? <TileGrid tiles={tiles} focus={focus} problem={tilesProblem} /> : null}
    </div>
  );
}
