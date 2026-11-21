import type { Activity } from "../state";

const STATUS_TEXT: Record<string, string> = {
  läuft: "läuft …",
  ok: "fertig",
  error: "Fehler",
  timeout: "Zeit abgelaufen",
  blocked: "gesperrt",
  denied: "abgelehnt",
  aborted: "abgebrochen",
  invalid: "ungültig",
};

/** Was Jarvis gerade tut und zuletzt getan hat (M3.8): nur Werkzeugname, Risiko, Ergebnis. */
export function ActivityList({ activities }: { activities: Activity[] }) {
  return (
    <section className="panel activity" aria-label="Aktivität">
      <h2>Aktivität</h2>
      {activities.length === 0 ? (
        <p className="empty">Noch keine Werkzeuge benutzt.</p>
      ) : (
        <ol>
          {activities.map((a) => (
            <li key={a.call} className={`act act-${a.status === "läuft" ? "running" : a.status}`}>
              <span className="act-tool">{a.tool}</span>
              <span className={`risk risk-${a.risk}`}>{a.risk}</span>
              <span className="act-status">{STATUS_TEXT[a.status] ?? a.status}</span>
              {a.ms !== null ? <span className="act-ms">{(a.ms / 1000).toFixed(1)} s</span> : null}
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}
