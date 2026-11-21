import { useEffect, useState } from "react";

const LINES = ["J.A.R.V.I.S. startet", "Verbindung zum Core", "Sensoren und Werkzeuge", "Bereit"];
const MIN_MS = 1800; // so lange läuft die Animation mindestens
const MAX_MS = 4000; // und höchstens, auch ohne Verbindung

/** Startanimation (M3.3). Abschaltbar in den Einstellungen; ein Klick überspringt sie. */
export function BootSequence({ connected, onDone }: { connected: boolean; onDone: () => void }) {
  const [elapsed, setElapsed] = useState(false);

  useEffect(() => {
    const minimum = setTimeout(() => setElapsed(true), MIN_MS);
    const maximum = setTimeout(onDone, MAX_MS);
    return () => {
      clearTimeout(minimum);
      clearTimeout(maximum);
    };
  }, [onDone]);

  useEffect(() => {
    if (elapsed && connected) onDone();
  }, [elapsed, connected, onDone]);

  return (
    <div className="boot" role="presentation" onClick={onDone}>
      <div className="boot-ring" />
      <ol>
        {LINES.map((line, index) => (
          <li key={line} style={{ animationDelay: `${index * 0.4}s` }}>
            {line}
          </li>
        ))}
      </ol>
    </div>
  );
}
