// Zustand des HUD als reine Funktion (Reducer): Ereignisse vom Core rein, Anzeige raus.
// Alles hier ist ohne Browser testbar.

import type { ServerEvent } from "./api/transport";

export const MAX_ACTIVITIES = 30;
export const MAX_MESSAGES = 200;
/** Ohne Meldung des Sprachclients so lange gilt sein Zustand als unbekannt (Absturz). */
export const VOICE_STALE_MS = 12_000;

export interface Activity {
  call: number;
  tool: string;
  risk: string;
  status: string; // "läuft" oder das Ergebnis wie im Audit: ok, error, blocked, denied, ...
  ms: number | null;
}

export interface ConfirmItem {
  id: string;
  tool: string;
  preview: string;
  question: string;
  risk: string;
}

export interface Message {
  role: "me" | "jarvis" | "info" | "error";
  text: string;
}

export interface HudState {
  connected: boolean;
  problem: string | null;
  voiceState: string | null;
  mic: boolean | null;
  voiceSeenAt: number | null;
  /** Letzter Pegel vom Sprachclient (M3.4): nur eine Stufe 0..7, nie Ton. */
  level: { value: number; source: "mic" | "speaker"; at: number } | null;
  /** Jarvis liest gerade den Bildschirm (M3.9; gespeist ab M4.6). */
  screenRead: boolean;
  thinking: boolean;
  activities: Activity[];
  confirms: ConfirmItem[];
  messages: Message[];
  sessionId: string | null;
  chatting: boolean;
  stoppedAt: number | null;
  stopFailed: boolean;
}

export const initialState: HudState = {
  connected: false,
  problem: null,
  voiceState: null,
  mic: null,
  voiceSeenAt: null,
  level: null,
  screenRead: false,
  thinking: false,
  activities: [],
  confirms: [],
  messages: [],
  sessionId: null,
  chatting: false,
  stoppedAt: null,
  stopFailed: false,
};

export type Action =
  | { type: "connection"; connected: boolean; problem?: string | undefined }
  | { type: "server"; event: ServerEvent; now: number }
  | { type: "chat_start"; message: string }
  | { type: "chat"; event: ServerEvent }
  | { type: "chat_end" }
  | { type: "confirm_answered"; id: string }
  | { type: "confirm_failed"; id: string }
  | { type: "notice"; text: string }
  | { type: "stop_result"; ok: boolean; now: number };

const str = (value: unknown, fallback = ""): string => (typeof value === "string" ? value : fallback);
const num = (value: unknown): number | null => (typeof value === "number" ? value : null);

function addMessage(messages: Message[], message: Message): Message[] {
  return [...messages, message].slice(-MAX_MESSAGES);
}

function confirmOf(data: Record<string, unknown>): ConfirmItem {
  return {
    id: str(data.id),
    tool: str(data.tool),
    preview: str(data.preview),
    question: str(data.question),
    risk: str(data.risk),
  };
}

function withConfirm(confirms: ConfirmItem[], item: ConfirmItem): ConfirmItem[] {
  if (!item.id || confirms.some((c) => c.id === item.id)) return confirms;
  return [...confirms, item];
}

function applyServer(state: HudState, { event, data }: ServerEvent, now: number): HudState {
  switch (event) {
    case "snapshot": {
      // Kommt bei jedem (Neu-)Verbinden und ersetzt den alten Stand: nach einem Abriss oder
      // Neustart des Core gelten alte Rückfragen, Werkzeuge und Denk-Zustände nicht mehr. Offene
      // Rückfragen schickt der Core direkt danach erneut; Werkzeug-Nummern beginnen neu.
      const voice = data.state as Record<string, unknown> | undefined;
      const mic = data.mic as Record<string, unknown> | undefined;
      const thinking = data.thinking as Record<string, unknown> | undefined;
      const screen = data.screen_read as Record<string, unknown> | undefined;
      return {
        ...state,
        voiceState: voice ? str(voice.state) || null : null,
        mic: mic && typeof mic.on === "boolean" ? mic.on : null,
        // Ein Snapshot sagt nichts darüber, ob der Sprachclient noch lebt: erst seine nächste
        // Meldung zählt.
        thinking: thinking?.active === true,
        screenRead: screen?.on === true,
        level: null,
        confirms: [],
        activities: [],
      };
    }
    case "state":
      return { ...state, voiceState: str(data.state) || null, voiceSeenAt: now };
    case "mic":
      return { ...state, mic: data.on === true, voiceSeenAt: now };
    case "heartbeat":
      return { ...state, voiceSeenAt: now };
    case "level": {
      const value = num(data.level);
      if (value === null) return { ...state, voiceSeenAt: now };
      const source = data.source === "speaker" ? "speaker" : "mic";
      return { ...state, voiceSeenAt: now, level: { value: Math.max(0, Math.min(7, value)), source, at: now } };
    }
    case "screen_read":
      return { ...state, screenRead: data.on === true };
    case "thinking":
      return { ...state, thinking: data.active === true };
    case "tool_start": {
      const activity: Activity = {
        call: num(data.call) ?? 0,
        tool: str(data.tool, "?"),
        risk: str(data.risk),
        status: "läuft",
        ms: null,
      };
      return { ...state, activities: [activity, ...state.activities].slice(0, MAX_ACTIVITIES) };
    }
    case "tool_end": {
      const call = num(data.call);
      const done = { status: str(data.status, "?"), ms: num(data.ms) };
      const known = state.activities.some((a) => a.call === call);
      const activities = known
        ? state.activities.map((a) => (a.call === call ? { ...a, ...done } : a))
        : [{ call: call ?? 0, tool: str(data.tool, "?"), risk: str(data.risk), ...done }, ...state.activities];
      return { ...state, activities: activities.slice(0, MAX_ACTIVITIES) };
    }
    case "stop":
      return { ...state, stoppedAt: now, thinking: false };
    case "confirm":
      return { ...state, confirms: withConfirm(state.confirms, confirmOf(data)) };
    case "confirm_done":
      return { ...state, confirms: state.confirms.filter((c) => c.id !== str(data.id)) };
    case "reminder":
      return { ...state, messages: addMessage(state.messages, { role: "info", text: `Erinnerung: ${str(data.text)}` }) };
    default:
      return state; // unbekannte Ereignisse (neuere Core-Version) einfach übergehen
  }
}

function applyChat(state: HudState, { event, data }: ServerEvent): HudState {
  switch (event) {
    case "delta": {
      const text = str(data.text);
      const last = state.messages.at(-1);
      if (last?.role === "jarvis") {
        const messages = [...state.messages.slice(0, -1), { ...last, text: last.text + text }];
        return { ...state, messages };
      }
      return { ...state, messages: addMessage(state.messages, { role: "jarvis", text }) };
    }
    case "notice":
      return { ...state, messages: addMessage(state.messages, { role: "info", text: str(data.message) }) };
    case "progress":
      return {
        ...state,
        messages: addMessage(state.messages, { role: "info", text: `${str(data.kind)}: ${str(data.text)}` }),
      };
    case "error":
      return { ...state, messages: addMessage(state.messages, { role: "error", text: str(data.message) }) };
    case "confirm":
      return { ...state, confirms: withConfirm(state.confirms, confirmOf(data)) };
    case "done": {
      const id = str(data.session_id) || null;
      const forgotten = state.sessionId !== null && data.neu === true;
      const messages = forgotten
        ? addMessage(state.messages, {
            role: "info",
            text: "JARVIS kannte das bisherige Gespräch nicht mehr (z. B. nach einem Neustart).",
          })
        : state.messages;
      return { ...state, sessionId: id, messages };
    }
    default:
      return state; // "tool" zeigt die Aktivitätsliste schon über den Status-Strom
  }
}

export function reducer(state: HudState, action: Action): HudState {
  switch (action.type) {
    case "connection":
      return { ...state, connected: action.connected, problem: action.connected ? null : (action.problem ?? null) };
    case "server":
      return applyServer(state, action.event, action.now);
    case "chat_start":
      return { ...state, chatting: true, messages: addMessage(state.messages, { role: "me", text: action.message }) };
    case "chat":
      return applyChat(state, action.event);
    case "chat_end":
      return { ...state, chatting: false };
    case "confirm_answered":
      return { ...state, confirms: state.confirms.filter((c) => c.id !== action.id) };
    case "notice":
      return { ...state, messages: addMessage(state.messages, { role: "info", text: action.text }) };
    case "confirm_failed":
      return {
        ...state,
        messages: addMessage(state.messages, {
          role: "error",
          text: "Die Antwort auf die Rückfrage kam nicht beim Core an (abgelaufen oder keine Verbindung).",
        }),
      };
    case "stop_result":
      return action.ok
        ? { ...state, stoppedAt: action.now, stopFailed: false }
        : { ...state, stopFailed: true };
  }
}

/** Ist der Sprachclient in den letzten Sekunden zu hören gewesen? */
export function voiceAlive(state: HudState, now: number): boolean {
  return state.voiceSeenAt !== null && now - state.voiceSeenAt < VOICE_STALE_MS;
}

export type Ring = "offline" | "sleep" | "idle" | "listening" | "thinking" | "speaking" | "confirm" | "dictation";

/** Was der große Ring zeigt. Der Sprachclient hat Vorrang, solange er lebt; sonst der Core. */
export function ringOf(state: HudState, now: number): Ring {
  if (!state.connected) return "offline";
  if (voiceAlive(state, now)) {
    switch (state.voiceState) {
      case "SCHLAF":
        return "sleep";
      case "HÖRT ZU":
        return "listening";
      case "DENKT":
        return "thinking";
      case "SPRICHT":
        return "speaking";
      case "WARTET AUF JA/NEIN":
        return "confirm";
      case "DIKTAT":
        return "dictation";
    }
  }
  if (state.confirms.length > 0) return "confirm";
  return state.thinking ? "thinking" : "idle";
}

export const RING_LABELS: Record<Ring, string> = {
  offline: "KEINE VERBINDUNG",
  sleep: "SCHLAF",
  idle: "BEREIT",
  listening: "HÖRT ZU",
  thinking: "DENKT",
  speaking: "SPRICHT",
  confirm: "WARTET AUF JA/NEIN",
  dictation: "DIKTAT",
};

/** Mikrofon-Anzeige: an, aus oder unbekannt (Sprachclient läuft nicht oder ist abgestürzt). */
export function micOf(state: HudState, now: number): "an" | "aus" | "unbekannt" {
  if (!voiceAlive(state, now) || state.mic === null) return "unbekannt";
  return state.mic ? "an" : "aus";
}
