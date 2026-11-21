import { describe, expect, it } from "vitest";
import type { ServerEvent } from "../src/api/sse";
import { dragRotation, MAX_TILT, RING_COLORS, spinSpeed, wants3d } from "../src/hologram";
import { loadSettings } from "../src/settings";
import { initialState, reducer } from "../src/state";

describe("3D-Ansicht (M3.10)", () => {
  it("automatisch nur ab Profil M; Profil S und unbekannt: aus", () => {
    expect(wants3d("auto", "S")).toBe(false);
    expect(wants3d("auto", null)).toBe(false);
    expect(wants3d("auto", "M")).toBe(true);
    expect(wants3d("auto", "L")).toBe(true);
    expect(wants3d("an", "S")).toBe(true);
    expect(wants3d("aus", "L")).toBe(false);
  });

  it("Ziehen dreht, die Neigung bleibt begrenzt", () => {
    const turned = dragRotation({ x: 0, y: 0 }, 100, 0);
    expect(turned.y).toBeCloseTo(1);
    expect(dragRotation({ x: 0, y: 0 }, 0, 10_000).x).toBe(MAX_TILT);
    expect(dragRotation({ x: 0, y: 0 }, 0, -10_000).x).toBe(-MAX_TILT);
  });

  it("jeder Zustand hat Farbe und Tempo; denken ist schneller als ruhen", () => {
    for (const ring of Object.keys(RING_COLORS) as (keyof typeof RING_COLORS)[]) {
      expect(spinSpeed(ring)).toBeGreaterThan(0);
    }
    expect(spinSpeed("thinking")).toBeGreaterThan(spinSpeed("idle"));
  });

  it("Profil kommt aus Snapshot und Ereignis; Einstellung wird geprüft gelesen", () => {
    const event = (name: string, data: Record<string, unknown>) => ({
      type: "server" as const,
      event: { event: name, data } as ServerEvent,
      now: 0,
    });
    expect(reducer(initialState, event("snapshot", { profile: { name: "L" } })).profile).toBe("L");
    expect(reducer(initialState, event("profile", { name: "S" })).profile).toBe("S");
    expect(loadSettings({ getItem: () => '{"threeD":"an"}' }).threeD).toBe("an");
    expect(loadSettings({ getItem: () => '{"threeD":"vielleicht"}' }).threeD).toBe("auto");
  });
});
