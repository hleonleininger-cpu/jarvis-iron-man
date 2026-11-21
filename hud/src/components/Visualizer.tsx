import { useEffect, useRef } from "react";

export const BAR_COUNT = 48;
const DECAY_MS = 500; // ohne neue Meldung fällt der Ausschlag in einer halben Sekunde auf null

/** Länge der Balken (0..1) aus Pegel (0..7), Alter der Meldung und Zeit. Rein, also testbar. */
export function bars(level: number, ageMs: number, timeMs: number, count = BAR_COUNT): number[] {
  const strength = (Math.max(0, Math.min(7, level)) / 7) * Math.max(0, 1 - ageMs / DECAY_MS);
  return Array.from({ length: count }, (_, i) => strength * (0.55 + 0.45 * Math.abs(Math.sin(i * 1.7 + timeMs / 180))));
}

export interface LevelSignal {
  value: number;
  source: "mic" | "speaker";
  at: number;
}

/** Balkenkranz um den Ring (M3.4): Zuhören grün, Sprechen cyan. Die Daten kommen nur vom
 *  Sprachclient (gedrosselt); das HUD öffnet nie ein Mikrofon. */
export function Visualizer({ level, now = Date.now }: { level: LevelSignal | null; now?: () => number }) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const latest = useRef(level);
  useEffect(() => {
    latest.current = level;
  }, [level]);

  useEffect(() => {
    const element = canvas.current;
    const context = element?.getContext?.("2d") ?? null;
    if (!element || !context) return; // z. B. in Tests ohne Canvas
    const still = globalThis.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;
    let frame = 0;
    const draw = () => {
      const size = element.width;
      context.clearRect(0, 0, size, size);
      const signal = latest.current;
      if (signal) {
        const t = now();
        const lengths = bars(signal.value, t - signal.at, still ? 0 : t);
        context.strokeStyle = signal.source === "mic" ? "#4dffb0" : "#3fd0ff";
        context.lineWidth = size / 160;
        context.lineCap = "round";
        const center = size / 2;
        const inner = size * 0.47;
        lengths.forEach((length, i) => {
          if (length < 0.02) return;
          const angle = (i / lengths.length) * Math.PI * 2 - Math.PI / 2;
          const outer = inner + length * size * 0.03 + size * 0.004;
          context.beginPath();
          context.moveTo(center + Math.cos(angle) * inner, center + Math.sin(angle) * inner);
          context.lineTo(center + Math.cos(angle) * outer, center + Math.sin(angle) * outer);
          context.stroke();
        });
      }
      frame = requestAnimationFrame(draw);
    };
    frame = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(frame);
  }, [now]);

  return <canvas ref={canvas} className="visualizer" width={640} height={640} aria-hidden="true" />;
}
