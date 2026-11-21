import { beforeEach, describe, expect, it, vi } from "vitest";

type Handler = (event: { payload: unknown }) => void;
const handlers = new Map<string, Handler[]>();
const invoked: [string, unknown][] = [];
let invokeImpl: (cmd: string, args?: unknown) => unknown = () => undefined;

vi.mock("@tauri-apps/api/event", () => ({
  listen: async (name: string, handler: Handler) => {
    handlers.set(name, [...(handlers.get(name) ?? []), handler]);
    return () => handlers.set(name, (handlers.get(name) ?? []).filter((h) => h !== handler));
  },
}));
vi.mock("@tauri-apps/api/core", () => ({
  invoke: async (cmd: string, args?: unknown) => {
    invoked.push([cmd, args]);
    return invokeImpl(cmd, args);
  },
}));

const { createTauriTransport } = await import("../src/api/tauri");
const fire = (name: string, payload: unknown) => (handlers.get(name) ?? []).forEach((h) => h({ payload }));

beforeEach(() => {
  handlers.clear();
  invoked.length = 0;
  invokeImpl = () => undefined;
});

describe("Tauri-Transport", () => {
  it("hört erst zu und startet dann den Ereignisstrom", async () => {
    const events: string[] = [];
    const connection: boolean[] = [];
    const stop = createTauriTransport().subscribe(
      (e) => events.push(e.event),
      (up) => connection.push(up),
    );
    await vi.waitFor(() => expect(invoked.map(([c]) => c)).toEqual(["events_start"]));
    fire("hud://connection", { connected: true, problem: null });
    fire("hud://server", { event: "snapshot", data: {} });
    expect(events).toEqual(["snapshot"]);
    expect(connection).toEqual([true]);
    stop();
    await vi.waitFor(() => expect(handlers.get("hud://server")).toEqual([]));
  });

  it("ordnet Chat-Ereignisse über die selbst vergebene Nummer zu, auch frühe", async () => {
    invokeImpl = (cmd, args) => {
      if (cmd === "chat_start") {
        const { id } = args as { id: number };
        fire("hud://chat", { id: id + 99, event: "delta", data: { text: "fremd" } });
        fire("hud://chat", { id, event: "delta", data: { text: "Hallo" } }); // vor der Rückkehr
        setTimeout(() => fire("hud://chat_end", id), 0);
      }
    };
    const events: unknown[] = [];
    await createTauriTransport().chat("Hi", "s1", (e) => events.push(e.data));
    expect(events).toEqual([{ text: "Hallo" }]);
    const start = invoked.find(([c]) => c === "chat_start")?.[1] as { id: number; message: string; sessionId: string };
    expect(start).toMatchObject({ message: "Hi", sessionId: "s1" });
    expect(Number.isSafeInteger(start.id)).toBe(true);
    expect(handlers.get("hud://chat")).toEqual([]);
  });

  it("Not-Aus bricht den Chat ab; ohne Antwort gilt der Not-Aus als gescheitert", async () => {
    const controller = new AbortController();
    invokeImpl = (cmd, args) => {
      if (cmd === "chat_cancel") fire("hud://chat_end", (args as { id: number }).id);
      if (cmd === "stop") return new Promise(() => {}); // Core hängt
    };
    const transport = createTauriTransport();
    const chat = transport.chat("x", null, () => {}, controller.signal);
    await vi.waitFor(() => expect(invoked.some(([c]) => c === "chat_start")).toBe(true));
    controller.abort();
    await chat;
    expect(invoked.map(([c]) => c)).toContain("chat_cancel");
    expect(await transport.stop(20)).toBe(false);
  });

  it("meldet Overlay, Hinweise und belegte Kürzel vom Start", async () => {
    invokeImpl = (cmd) =>
      cmd === "shortcut_problems" ? ["Not-Aus-Kürzel nicht verfügbar"] : cmd === "overlay_state" ? false : undefined;
    const seen: string[] = [];
    createTauriTransport().desktop?.({
      overlay: (on) => seen.push(`overlay:${on}`),
      notice: (text) => seen.push(text),
      stopResult: (ok) => seen.push(`stop:${ok}`),
    });
    await vi.waitFor(() => expect(seen).toContain("Not-Aus-Kürzel nicht verfügbar"));
    await vi.waitFor(() => expect(handlers.get("hud://overlay")?.length).toBe(1));
    expect(seen).toContain("overlay:false"); // Stand nach Neuladen der Seite
    fire("hud://overlay", true);
    fire("hud://stop_result", false);
    expect(seen.slice(-2)).toEqual(["overlay:true", "stop:false"]);
  });
});
