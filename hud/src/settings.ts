// Einstellungen des HUD, nur in diesem Browser bzw. dieser App gespeichert. Speicher kann fehlen
// oder gesperrt sein; dann gelten die Standardwerte.

import type { ThreeD } from "./hologram";

export interface Settings {
  boot: boolean; // Startanimation
  sound: boolean; // Töne
  volume: number; // 0..1
  threeD: ThreeD; // 3D-Ansicht (M3.10): "auto" = ab Profil M
}

export const DEFAULT_SETTINGS: Settings = { boot: true, sound: true, volume: 0.4, threeD: "auto" };
const KEY = "jarvis.hud.settings";

export function loadSettings(storage: Pick<Storage, "getItem"> | undefined = globalThis.localStorage): Settings {
  try {
    const raw: unknown = JSON.parse(storage?.getItem(KEY) ?? "null");
    if (typeof raw !== "object" || raw === null) return DEFAULT_SETTINGS;
    const value = raw as Partial<Record<keyof Settings, unknown>>;
    const threeD = value.threeD === "an" || value.threeD === "aus" || value.threeD === "auto" ? value.threeD : DEFAULT_SETTINGS.threeD;
    return {
      threeD,
      boot: typeof value.boot === "boolean" ? value.boot : DEFAULT_SETTINGS.boot,
      sound: typeof value.sound === "boolean" ? value.sound : DEFAULT_SETTINGS.sound,
      volume:
        typeof value.volume === "number" && value.volume >= 0 && value.volume <= 1
          ? value.volume
          : DEFAULT_SETTINGS.volume,
    };
  } catch {
    return DEFAULT_SETTINGS;
  }
}

export function saveSettings(settings: Settings, storage: Pick<Storage, "setItem"> | undefined = globalThis.localStorage): void {
  try {
    storage?.setItem(KEY, JSON.stringify(settings));
  } catch {
    // gesperrter Speicher: Einstellung gilt nur bis zum Schließen
  }
}
