import { describe, expect, it } from "vitest";
import type { ServerEvent } from "../src/api/sse";
import { focusOf, overlayPanels, soonEvent } from "../src/situation";
import { initialState, reducer, type Action } from "../src/state";
import { parseTiles } from "../src/tiles";

const NOW = Date.parse("2026-11-21T15:00:00+01:00");
const server = (event: string, data: Record<string, unknown> = {}): Action => ({
  type: "server",
  event: { event, data } as ServerEvent,
  now: NOW,
});
const tilesWith = (start: string, ganztaegig = false) =>
  parseTiles({ termine: { ok: true, eintraege: [{ titel: "Zahnarzt", start, ganztaegig }] } });

describe("Kacheln lesen", () => {
  it("macht aus Unerwartetem 'keine Daten', nie erfundene Werte", () => {
    const tiles = parseTiles({ system: { ok: true, cpu: "viel" }, termine: "kaputt", missionen: { ok: false } });
    expect(tiles.system).toEqual({ ok: true, cpu: 0, ram: 0, akku: null });
    expect(tiles.termine).toEqual({ ok: false, grund: "keine Daten" });
    expect(tiles.missionen).toEqual({ ok: false, grund: "keine Daten" });
    expect(parseTiles(null).fokus.ok).toBe(false);
  });
});

describe("Situatives HUD", () => {
  it("ein Termin in den nächsten 15 Minuten ist 'gleich', ganztägige und ferne nicht", () => {
    expect(soonEvent(tilesWith("2026-11-21T15:10:00+01:00"), NOW)?.titel).toBe("Zahnarzt");
    expect(soonEvent(tilesWith("2026-11-21T14:57:00+01:00"), NOW)).not.toBeNull(); // läuft seit 3 min
    expect(soonEvent(tilesWith("2026-11-21T15:30:00+01:00"), NOW)).toBeNull();
    expect(soonEvent(tilesWith("2026-11-21", true), NOW)).toBeNull();
  });

  it("Vorrang: Rückfrage vor Arbeit vor Termin vor Ruhe", () => {
    const soon = tilesWith("2026-11-21T15:05:00+01:00");
    const calm = { state: initialState, tiles: null, now: NOW };
    expect(focusOf(calm).focus).toBe("ruhe");
    expect(focusOf({ ...calm, tiles: soon }).focus).toBe("termin");
    const working = reducer(initialState, server("tool_start", { call: 1, tool: "t", risk: "READ" }));
    expect(focusOf({ state: working, tiles: soon, now: NOW }).focus).toBe("activity");
    const asking = reducer(working, server("confirm", { id: "r", tool: "t", preview: "", question: "", risk: "WRITE_REVERSIBLE" }));
    expect(focusOf({ state: asking, tiles: soon, now: NOW })).toEqual({ focus: "confirm", why: "Rückfrage offen" });
  });

  it("im Overlay erscheint nur das Gebrauchte", () => {
    expect(overlayPanels("ruhe")).toEqual({ activity: false, tiles: false });
    expect(overlayPanels("activity")).toEqual({ activity: true, tiles: false });
    expect(overlayPanels("termin")).toEqual({ activity: false, tiles: true });
  });
});
