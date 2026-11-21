/// <reference types="vite/client" />
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Listener, Transport } from "../src/api/transport";
import { App } from "../src/App";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

class FakeTransport implements Transport {
  listener: Listener = () => {};
  connection: (up: boolean, problem?: string) => void = () => {};
  confirmed: [string, boolean][] = [];
  confirmResult = true;
  stops = 0;
  stopResult = true;
  chats: string[] = [];

  subscribe(onEvent: Listener, onConnection: (up: boolean, problem?: string) => void) {
    this.listener = onEvent;
    this.connection = onConnection;
    return () => {};
  }
  async chat(message: string, _session: string | null, onEvent: Listener) {
    this.chats.push(message);
    onEvent({ event: "delta", data: { text: "<b>kein HTML</b>" } });
    onEvent({ event: "done", data: { session_id: "s1" } });
  }
  async confirm(id: string, approve: boolean) {
    this.confirmed.push([id, approve]);
    return this.confirmResult;
  }
  async stop() {
    this.stops += 1;
    return this.stopResult;
  }
}

let container: HTMLDivElement;
let root: Root;
let transport: FakeTransport;

beforeEach(() => {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  transport = new FakeTransport();
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

const render = () => act(() => root.render(<App transport={transport} now={() => 1000} />));
const send = (event: string, data: Record<string, unknown>) => act(() => transport.listener({ event, data }));
const button = (text: string) =>
  [...container.querySelectorAll("button")].find((b) => b.textContent === text) as HTMLButtonElement;

describe("HUD", () => {
  it("zeigt Verbindung, Zustand und laufende Werkzeuge", () => {
    render();
    expect(container.textContent).toContain("KEINE VERBINDUNG");
    act(() => transport.connection(true));
    send("state", { state: "HÖRT ZU" });
    send("tool_start", { call: 1, tool: "get_weather", risk: "READ" });
    expect(container.textContent).toContain("Core verbunden");
    expect(container.textContent).toContain("HÖRT ZU");
    expect(container.querySelector(".act-running .act-tool")?.textContent).toBe("get_weather");
    send("tool_end", { call: 1, tool: "get_weather", risk: "READ", status: "ok", ms: 1500 });
    expect(container.querySelector(".act-ok .act-ms")?.textContent).toBe("1.5 s");
  });

  it("Rückfrage: Ja schickt die Antwort und schließt die Karte", async () => {
    render();
    send("confirm", { id: "r1", tool: "create_event", preview: "Zahnarzt <script>", question: "Anlegen?", risk: "WRITE_REVERSIBLE" });
    expect(container.querySelector(".preview")?.textContent).toBe("Zahnarzt <script>");
    expect(container.querySelector("script")).toBeNull();
    await act(async () => button("Ja").click());
    expect(transport.confirmed).toEqual([["r1", true]]);
    expect(container.querySelector(".confirm")).toBeNull();
  });

  it("Rückfrage: kommt die Antwort nicht an, bleibt die Karte und Master erfährt es", async () => {
    render();
    transport.confirmResult = false;
    send("confirm", { id: "r2", tool: "create_event", preview: "x", question: "Anlegen?", risk: "WRITE_REVERSIBLE" });
    await act(async () => button("Nein").click());
    expect(container.querySelector(".confirm")).not.toBeNull();
    expect(container.querySelector(".msg-error")?.textContent).toContain("nicht beim Core an");
  });

  it("Not-Aus per Knopf und per Strg+.; Hinweis, wenn der Core nicht antwortet", async () => {
    render();
    await act(async () => button("NOT-AUS").click());
    expect(transport.stops).toBe(1);
    transport.stopResult = false;
    await act(async () => {
      document.dispatchEvent(new KeyboardEvent("keydown", { key: ".", ctrlKey: true }));
    });
    expect(transport.stops).toBe(2);
    expect(container.textContent).toContain("jarvis stop");
  });

  it("Gespräch: Antworten erscheinen als Text, nie als HTML", async () => {
    render();
    const input = container.querySelector('input[aria-label="Nachricht an Jarvis"]') as HTMLInputElement;
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set;
      setter?.call(input, "Hallo Jarvis");
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await act(async () => button("Senden").click());
    expect(transport.chats).toEqual(["Hallo Jarvis"]);
    expect(container.querySelector(".msg-jarvis")?.textContent).toBe("<b>kein HTML</b>");
    expect(container.querySelector(".msg-jarvis b")).toBeNull();
  });
});

describe("Block C", () => {
  it("zeigt das Augensymbol, solange Jarvis den Bildschirm liest", () => {
    render();
    expect(container.querySelector(".eye")).toBeNull();
    send("screen_read", { on: true });
    expect(container.querySelector(".eye")?.textContent).toContain("liest Bildschirm");
    send("screen_read", { on: false });
    expect(container.querySelector(".eye")).toBeNull();
  });

  it("die Startanimation endet nach der Verbindung und lässt sich abschalten", async () => {
    vi.useFakeTimers();
    try {
      render();
      expect(container.querySelector(".boot")).not.toBeNull();
      act(() => transport.connection(true));
      await act(async () => vi.advanceTimersByTime(2000));
      expect(container.querySelector(".boot")).toBeNull();
      // abschalten: Einstellung wird gespeichert und gilt beim nächsten Start
      const toggle = [...container.querySelectorAll("label")].find((l) => l.textContent?.includes("Startanimation"))?.querySelector("input");
      act(() => toggle?.click());
      expect(JSON.parse(localStorage.getItem("jarvis.hud.settings") ?? "{}").boot).toBe(false);
      act(() => root.unmount());
      root = createRoot(container);
      render();
      expect(container.querySelector(".boot")).toBeNull();
    } finally {
      vi.useRealTimers();
      localStorage.clear();
    }
  });
});

describe("Sicherheit im Quelltext", () => {
  it("nutzt nirgends HTML aus Zeichenketten oder eval", () => {
    const sources = import.meta.glob("../src/**/*.{ts,tsx}", { query: "?raw", import: "default", eager: true });
    expect(Object.keys(sources).length).toBeGreaterThan(5);
    for (const [file, text] of Object.entries(sources)) {
      expect(text, file).not.toMatch(/dangerouslySetInnerHTML|innerHTML|outerHTML|insertAdjacentHTML|\beval\(|new Function/);
    }
  });
});

