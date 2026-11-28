// Hilfen für die 3D-Ansicht (M3.10), ohne three.js: rein und testbar.

import type { Ring } from "./state";

export type ThreeD = "auto" | "an" | "aus";

/** 3D nur, wenn eingeschaltet, oder bei "auto" ab Profil M (Profil S und unbekannt: aus). */
export function wants3d(setting: ThreeD, profile: string | null): boolean {
  if (setting === "an") return true;
  if (setting === "aus") return false;
  return profile === "M" || profile === "L";
}

export interface Rotation {
  x: number; // Neigung, begrenzt, damit der Ring nie auf dem Kopf steht
  y: number; // Drehung, beliebig
}

export const MAX_TILT = 1.2;

/** Mausziehen dreht die Ansicht: waagerecht um die Hochachse, senkrecht neigen. */
export function dragRotation(previous: Rotation, dx: number, dy: number): Rotation {
  const x = Math.max(-MAX_TILT, Math.min(MAX_TILT, previous.x + dy * 0.01));
  return { x, y: previous.y + dx * 0.01 };
}

export const RING_COLORS: Record<Ring, number> = {
  offline: 0x5a6b78,
  sleep: 0x3a6d8a,
  idle: 0x3fd0ff,
  listening: 0x4dffb0,
  thinking: 0x3fd0ff,
  speaking: 0x3fd0ff,
  confirm: 0xffa63c,
  dictation: 0xffa63c,
};

/** Drehtempo je Zustand (Umdrehungen je Sekunde, grob): denken schneller, schlafen fast still. */
export function spinSpeed(ring: Ring): number {
  switch (ring) {
    case "thinking":
      return 0.9;
    case "listening":
    case "speaking":
      return 0.45;
    case "offline":
    case "sleep":
      return 0.03;
    default:
      return 0.15;
  }
}
