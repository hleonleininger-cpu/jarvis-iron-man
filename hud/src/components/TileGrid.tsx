import type { Focus } from "../situation";
import type { Missing, Tiles } from "../tiles";

function Empty({ tile }: { tile: Missing }) {
  return <p className="empty">{tile.grund}</p>;
}

function Bar({ label, value }: { label: string; value: number }) {
  const width = Math.max(0, Math.min(100, value));
  return (
    <div className="bar">
      <span>{label}</span>
      <div className="bar-track">
        <div className={`bar-fill${width >= 85 ? " bar-high" : ""}`} style={{ width: `${width}%` }} />
      </div>
      <span className="bar-value">{Math.round(value)} %</span>
    </div>
  );
}

const time = new Intl.DateTimeFormat("de-DE", { weekday: "short", hour: "2-digit", minute: "2-digit", timeZone: "Europe/Berlin" });
const day = new Intl.DateTimeFormat("de-DE", { weekday: "short", day: "2-digit", month: "2-digit", timeZone: "Europe/Berlin" });

/** "2026-11-20" -> "20.11." (ohne Umweg über Zeitzonen). */
function dueDate(iso: string): string {
  const [, month, dayOfMonth] = iso.split("-");
  return month && dayOfMonth ? `${dayOfMonth}.${month}.` : iso;
}

function when(start: string, allDay: boolean): string {
  const date = new Date(start);
  if (Number.isNaN(date.getTime())) return "";
  return allDay ? `${day.format(date)} ganztägig` : time.format(date);
}

/** Live-Kacheln (M3.6). Jede Kachel zeigt "keine Daten" mit Grund, nie einen erfundenen Wert. */
export function TileGrid({
  tiles,
  focus,
  problem,
  only = null,
}: {
  tiles: Tiles | null;
  focus: Focus;
  problem: string | null;
  /** Im Overlay nur eine Kachel (M3.7: nur das Gebrauchte). */
  only?: "termine" | null;
}) {
  if (!tiles) {
    return (
      <section className="tiles" aria-label="Lage">
        <p className="empty">{problem ?? "Kacheln werden geladen …"}</p>
      </section>
    );
  }
  const { system, termine, missionen, fokus } = tiles;
  return (
    <section className="tiles" aria-label="Lage">
      {only ? null : <article className="panel tile">
        <h2>System</h2>
        {system.ok ? (
          <>
            <Bar label="CPU" value={system.cpu} />
            <Bar label="RAM" value={system.ram} />
            {system.akku ? (
              <p className="tile-note">
                Akku {system.akku.prozent} %{system.akku.laedt ? " · lädt" : ""}
              </p>
            ) : null}
          </>
        ) : (
          <Empty tile={system} />
        )}
      </article>}
      <article className={`panel tile${focus === "termin" ? " tile-focus" : ""}`}>
        <h2>Termine</h2>
        {termine.ok ? (
          termine.eintraege.length === 0 ? (
            <p className="empty">Keine Termine in den nächsten Tagen.</p>
          ) : (
            <ul>
              {termine.eintraege.map((e) => (
                <li key={`${e.start}-${e.titel}`}>
                  <span className="tile-time">{when(e.start, e.ganztaegig)}</span> {e.titel}
                  {e.ort ? <span className="tile-note"> · {e.ort}</span> : null}
                </li>
              ))}
            </ul>
          )
        ) : (
          <Empty tile={termine} />
        )}
      </article>
      {only ? null : <article className="panel tile">
        <h2>Missionen</h2>
        {missionen.ok ? (
          missionen.eintraege.length === 0 ? (
            <p className="empty">Keine offenen Aufgaben.</p>
          ) : (
            <ul>
              {missionen.eintraege.map((t, index) => (
                <li key={index} className={t.ueberfaellig ? "overdue" : undefined}>
                  {t.text}
                  {t.faellig ? <span className="tile-note"> · fällig {dueDate(t.faellig)}</span> : null}
                </li>
              ))}
              {missionen.anzahl > missionen.eintraege.length ? (
                <li className="tile-note">und {missionen.anzahl - missionen.eintraege.length} weitere</li>
              ) : null}
            </ul>
          )
        ) : (
          <Empty tile={missionen} />
        )}
      </article>}
      {only ? null : <article className="panel tile">
        <h2>Fokus</h2>
        <Empty tile={fokus} />
      </article>}
      {!tiles.aktuell && tiles.stand ? <p className="tile-stand">Stand {new Date(tiles.stand).toLocaleTimeString("de-DE")}</p> : null}
    </section>
  );
}
