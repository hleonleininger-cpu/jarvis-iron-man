import type { ReactNode } from "react";
import { RING_LABELS, type Ring } from "../state";

interface Props {
  ring: Ring;
  mic: "an" | "aus" | "unbekannt";
  children?: ReactNode;
}

/** Der große Ring in der Mitte: Zustand als Farbe und Bewegung, dazu Klartext. */
export function StateRing({ ring, mic, children }: Props) {
  return (
    <section className={`ring ring-${ring}`} aria-label="Zustand">
      {children}
      <svg viewBox="0 0 200 200" role="img" aria-hidden="true">
        <circle className="ring-outer" cx="100" cy="100" r="92" />
        <circle className="ring-sweep" cx="100" cy="100" r="80" />
        <circle className="ring-inner" cx="100" cy="100" r="62" />
        <circle className="ring-core" cx="100" cy="100" r="30" />
      </svg>
      <div className="ring-label" role="status">
        {RING_LABELS[ring]}
      </div>
      <div className={`mic mic-${mic === "an" ? "on" : mic === "aus" ? "off" : "unknown"}`}>
        Mikrofon: {mic}
        {mic === "an" ? <span className="mic-hint"> · aus mit Strg+Alt+M</span> : null}
      </div>
    </section>
  );
}
