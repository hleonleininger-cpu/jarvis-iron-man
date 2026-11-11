# ADR-004: MCP-Client-Hub statt eigener Werkzeug-Registry

**Status:** akzeptiert

## Kontext
Ab M1a nutzt der Agent Werkzeuge (erstes: Wetter). Jeder Aufruf muss durch die Policy und ins
Audit-Log. Eine eigene Registry würde pro Werkzeug eigenen Adaptercode im Core verlangen.

## Entscheidung
`jarvis_core/mcp_hub/` ist ein MCP-Client: Er startet die in `jarvis.toml` deklarierten
MCP-Server (stdio, eigene Prozesse), lädt deren Werkzeugbeschreibungen und reicht sie an den
Pydantic-AI-Agent. Eigene Server liegen in `mcp_servers/` und kennen den Core nicht.

- **Eine Quelle für Metadaten:** Die Risikoklasse je Werkzeug steht nur in `jarvis.toml`
  (`[mcp_servers.<name>.tools]`). Undeklarierte Werkzeuge werden dem Modell nicht angeboten und
  bei einem Aufruf abgelehnt (Fail-closed). Die Deklaration gilt je Server: Ein Server kann kein
  Werkzeug anbieten, das nur für einen anderen deklariert ist. Werkzeugnamen müssen über alle
  Server eindeutig sein (Konfigurationsfehler sonst).
- **Ein Gate:** Jeder Aufruf läuft über `McpHub.gated_call`: `policy.decide(risk, context)`.
  ALLOW führt aus, CONFIRM wird bis M1b mit klarer Meldung abgelehnt, DENY abgelehnt.
- **Audit:** Je Aufruf eine Zeile mit Werkzeugname, Risikoklasse, Entscheidung und Ergebnis.
  Keine Argumente, keine Ergebnisinhalte. Unbekannte Werkzeugnamen des Modells werden ebenfalls
  protokolliert.
- **Ausfall:** Startet ein Server nicht, meldet der Core das und antwortet ohne dessen Werkzeuge.
  Fehlt ein deklariertes Werkzeug, gibt es einen Hinweis. Antwortet ein Werkzeug 90 s nicht, endet
  der Aufruf mit `timeout` im Audit; das Modell erfährt, dass kein Ergebnis vorliegt.
- **Eine Anfrage zugleich:** Der Hub trägt genau eine laufende Anfrage; der Agent reiht weitere
  ein und meldet das dem Wartenden. Die Server-Prozesse enden mit der Anfrage.

## Begründung
- Neue Fähigkeiten sind neue MCP-Server, kein Core-Umbau; Standard statt Eigenbau (ADR-002).
- Prozessgrenze: Ein fehlerhaftes oder bösartiges Werkzeug läuft nicht im Core-Prozess.
- Policy und Audit sitzen an genau einer Stelle und lassen sich nicht pro Werkzeug vergessen.

## Konsequenzen
- Neue Abhängigkeit: Extra `mcp` von `pydantic-ai-slim` (MCP-Client) und das `mcp`-SDK für die
  eigenen Server.
- Server werden derzeit pro Anfrage gestartet (einfach, robust, ca. 1 s Latenz); dauerhafte
  Verbindungen sind eine spätere Optimierung.
- Das Audit-Schema bekam die Spalten `tool`, `risk`, `decision`; M0-Datenbanken werden beim
  Öffnen ergänzt und bleiben verifizierbar.
- Risikoklasse wird aus der Konfiguration vertraut, nicht aus der Selbstauskunft des Servers.
