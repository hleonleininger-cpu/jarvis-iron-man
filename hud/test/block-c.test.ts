import { describe, expect, it } from "vitest";
import type { ServerEvent } from "../src/api/sse";
import { bars, BAR_COUNT } from "../src/components/Visualizer";
import { DEFAULT_SETTINGS, loadSettings, saveSettings } from "../src/settings";
import { createSounds } from "../src/sound";
import { initialState, reducer, type Action } from "../src/state";

const server = (event: string, data: Record<string, unknown> = {}, now = 1000): Action => ({
  type: "server",
  event: { event, data } as ServerEvent,
  now,
});

describe("Pegel und Augensymbol im Zustand", () => {
  it("merkt sich den Pegel als Stufe 0..7 mit Quelle und Zeit", () => {
    const state = reducer(initialState, server("level", { level: 12, source: "speaker" }, 500));
    expect(state.level).toEqual({ value: 7, source: "speaker", at: 500 });
    expect(state.voiceSeenAt).toBe(500); // Pegel gilt auch als Lebenszeichen
  });

  it("zeigt das Lesen des Bildschirms an und vergisst es mit einem leeren Snapshot", () => {
    let state = reducer(initialState, server("screen_read", { on: true }));
    expect(state.screenRead).toBe(true);
    state = reducer(state, server("snapshot", {}));
    expect(state.screenRead).toBe(false);
    expect(reducer(initialState, server("snapshot", { screen_read: { on: true } })).screenRead).toBe(true);
  });
});

describe("Visualizer", () => {
  it("ohne Pegel oder nach dem Abklingen: keine Ausschläge", () => {
    expect(bars(0, 0, 0).every((b) => b === 0)).toBe(true);
    expect(bars(7, 600, 0).every((b) => b === 0)).toBe(true);
  });

  it("lauter heißt längere Balken, immer zwischen 0 und 1", () => {
    const quiet = bars(2, 0, 0);
    const loud = bars(7, 0, 0);
    expect(loud).toHaveLength(BAR_COUNT);
    expect(Math.max(...loud)).toBeGreaterThan(Math.max(...quiet));
    expect(loud.every((b) => b >= 0 && b <= 1)).toBe(true);
  });
});

describe("Einstellungen", () => {
  it("fallen bei fehlendem, kaputtem oder gesperrtem Speicher auf die Standardwerte zurück", () => {
    expect(loadSettings(undefined)).toEqual(DEFAULT_SETTINGS);
    expect(loadSettings({ getItem: () => "{kaputt" })).toEqual(DEFAULT_SETTINGS);
    expect(loadSettings({ getItem: () => { throw new Error("gesperrt"); } })).toEqual(DEFAULT_SETTINGS);
    expect(loadSettings({ getItem: () => '{"boot":false,"volume":7}' })).toEqual({ ...DEFAULT_SETTINGS, boot: false });
    expect(() => saveSettings(DEFAULT_SETTINGS, { setItem: () => { throw new Error("voll"); } })).not.toThrow();
  });
});

describe("Töne", () => {
  it("bleiben ohne Audio-Unterstützung still, ohne Fehler", () => {
    expect(() => createSounds(() => null).play("boot", 0.5)).not.toThrow();
  });

  it("spielen eine kurze Tonfolge, bei Lautstärke 0 gar nichts", () => {
    let oscillators = 0;
    const param = { value: 0, setValueAtTime() {}, linearRampToValueAtTime() {}, exponentialRampToValueAtTime() {} };
    const context = {
      currentTime: 0,
      destination: {},
      state: "running",
      resume: async () => {},
      createGain: () => ({ gain: param, connect: (target: unknown) => target }),
      createOscillator: () => {
        oscillators += 1;
        return { type: "", frequency: { value: 0 }, connect: (target: unknown) => target, start() {}, stop() {} };
      },
    };
    const sounds = createSounds(() => context as never);
    sounds.play("stop", 0);
    expect(oscillators).toBe(0);
    sounds.play("confirm", 0.5);
    expect(oscillators).toBe(2);
    context.state = "suspended"; // Autoplay-Sperre: verwerfen, nicht aufstauen
    sounds.play("boot", 0.5);
    expect(oscillators).toBe(2);
  });
});
