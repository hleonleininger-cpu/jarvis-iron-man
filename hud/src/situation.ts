// Situatives HUD (M3.7): welche Anzeige gerade wichtig ist. Die Regeln sind Daten (eine Liste,
// die von oben nach unten gilt), damit sie sich leicht ändern und testen lassen.

import type { HudState } from "./state";
import type { EventEntry, Tiles } from "./tiles";

export type Focus = "confirm" | "activity" | "termin" | "ruhe";

export interface Context {
  state: HudState;
  tiles: Tiles | null;
  now: number;
}

export interface Rule {
  focus: Focus;
  why: string;
  when: (context: Context) => boolean;
}

export const SOON_MS = 15 * 60_000; // ein Termin in den nächsten 15 Minuten
export const STARTED_MS = 5 * 60_000; // oder seit höchstens 5 Minuten

/** Der nächste Termin, der gleich beginnt (ganztägige zählen nicht). */
export function soonEvent(tiles: Tiles | null, now: number): EventEntry | null {
  if (!tiles || !tiles.termine.ok) return null;
  for (const entry of tiles.termine.eintraege) {
    if (entry.ganztaegig) continue;
    const start = Date.parse(entry.start);
    if (Number.isFinite(start) && start - now <= SOON_MS && now - start <= STARTED_MS) return entry;
  }
  return null;
}

export const RULES: Rule[] = [
  { focus: "confirm", why: "Rückfrage offen", when: ({ state }) => state.confirms.length > 0 },
  {
    focus: "activity",
    why: "Jarvis arbeitet",
    when: ({ state }) => state.thinking || state.activities.some((a) => a.status === "läuft"),
  },
  { focus: "termin", why: "Termin gleich", when: ({ tiles, now }) => soonEvent(tiles, now) !== null },
];

export function focusOf(context: Context): { focus: Focus; why: string } {
  const rule = RULES.find((r) => r.when(context));
  return rule ? { focus: rule.focus, why: rule.why } : { focus: "ruhe", why: "nichts los" };
}

/** Was im Overlay sichtbar ist: nur das Gebrauchte. Im Fenster ist immer alles sichtbar. */
export function overlayPanels(focus: Focus): { activity: boolean; tiles: boolean } {
  return { activity: focus === "activity", tiles: focus === "termin" };
}
