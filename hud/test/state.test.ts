import { describe, expect, it } from "vitest";
import type { ServerEvent } from "../src/api/sse";
import {
  initialState,
  micOf,
  reducer,
  ringOf,
  VOICE_STALE_MS,
  type Action,
  type HudState,
} from "../src/state";

const server = (event: string, data: Record<string, unknown> = {}, now = 1000): Action => ({
  type: "server",
  event: { event, data } as ServerEvent,
  now,
});
const run = (...actions: Action[]): HudState => actions.reduce(reducer, initialState);
const connected: Action = { type: "connection", connected: true };

describe("Status-Ereignisse", () => {
  it("führt Werkzeugaufrufe von Start bis Ende", () => {
    const state = run(
      server("tool_start", { call: 1, tool: "get_weather", risk: "READ" }),
      server("tool_start", { call: 2, tool: "create_event", risk: "WRITE_REVERSIBLE" }),
      server("tool_end", { call: 1, tool: "get_weather", risk: "READ", status: "ok", ms: 420 }),
    );
    expect(state.activities.map((a) => [a.call, a.status, a.ms])).toEqual([
      [2, "läuft", null],
      [1, "ok", 420],
    ]);
  });

  it("zeigt ein Ende auch ohne bekannten Start (HUD später verbunden)", () => {
    const state = run(server("tool_end", { call: 7, tool: "lesen", risk: "READ", status: "denied", ms: 3 }));
    expect(state.activities[0]).toMatchObject({ call: 7, tool: "lesen", status: "denied" });
  });

  it("begrenzt die Liste", () => {
    const actions = Array.from({ length: 40 }, (_, i) => server("tool_start", { call: i, tool: "t", risk: "READ" }));
    expect(run(...actions).activities).toHaveLength(30);
  });

  it("öffnet und schließt Rückfragen, auch wenn sie anderswo beantwortet wurden", () => {
    const confirm = { id: "r1", tool: "create_event", preview: "Termin", question: "Anlegen?", risk: "WRITE_REVERSIBLE" };
    let state = run(server("confirm", confirm), server("confirm", confirm));
    expect(state.confirms).toHaveLength(1);
    state = reducer(state, server("confirm_done", { id: "r1", approved: true }));
    expect(state.confirms).toEqual([]);
  });

  it("übergeht unbekannte Ereignisse", () => {
    expect(run(server("neu_in_spaeterer_version", { x: 1 }))).toEqual(initialState);
  });
});

describe("Ring und Mikrofon", () => {
  it("zeigt den Zustand des Sprachclients, solange er lebt", () => {
    const state = run(connected, server("state", { state: "HÖRT ZU" }, 1000), server("mic", { on: true }, 1000));
    expect(ringOf(state, 2000)).toBe("listening");
    expect(micOf(state, 2000)).toBe("an");
  });

  it("vergisst den Sprachclient, wenn er schweigt (Absturz)", () => {
    const state = run(connected, server("state", { state: "SPRICHT" }, 1000), server("mic", { on: true }, 1000));
    const later = 1000 + VOICE_STALE_MS + 1;
    expect(ringOf(state, later)).toBe("idle");
    expect(micOf(state, later)).toBe("unbekannt");
  });

  it("ein Snapshot allein gilt nicht als Lebenszeichen", () => {
    const state = run(connected, server("snapshot", { state: { state: "HÖRT ZU" }, mic: { on: true } }));
    expect(state.voiceState).toBe("HÖRT ZU");
    expect(micOf(state, 1000)).toBe("unbekannt");
    expect(ringOf(reducer(state, server("heartbeat", {}, 1000)), 1500)).toBe("listening");
  });

  it("ohne Sprachclient: Denken und Rückfragen kommen vom Core", () => {
    expect(ringOf(run(connected, server("thinking", { active: true, run: 1 })), 1000)).toBe("thinking");
    const asking = run(connected, server("confirm", { id: "x", tool: "t", preview: "", question: "", risk: "READ" }));
    expect(ringOf(asking, 1000)).toBe("confirm");
    expect(ringOf(initialState, 1000)).toBe("offline");
  });

  it("der Not-Aus beendet das Denken", () => {
    const state = run(connected, server("thinking", { active: true, run: 1 }), server("stop", { count: 1 }, 5000));
    expect(state.thinking).toBe(false);
    expect(state.stoppedAt).toBe(5000);
  });
});

describe("Gespräch", () => {
  it("hängt Textstücke an die laufende Antwort und merkt sich die Sitzung", () => {
    const chat = (event: string, data: Record<string, unknown>): Action => ({ type: "chat", event: { event, data } });
    const state = run(
      { type: "chat_start", message: "Wie wird das Wetter?" },
      chat("delta", { text: "Son" }),
      chat("delta", { text: "nig." }),
      chat("done", { session_id: "abc", neu: true }),
      { type: "chat_end" },
    );
    expect(state.messages).toEqual([
      { role: "me", text: "Wie wird das Wetter?" },
      { role: "jarvis", text: "Sonnig." },
    ]);
    expect(state.sessionId).toBe("abc");
    expect(state.chatting).toBe(false);
  });
});
