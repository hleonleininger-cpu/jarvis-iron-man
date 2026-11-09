# ADR-002: MCP als Werkzeugstandard

**Status:** akzeptiert (Umsetzung ab einem späteren Meilenstein)

## Kontext
JARVIS soll Fähigkeiten (Kalender, Dateien, Notizen …) über austauschbare Werkzeuge erhalten,
ohne den Core bei jedem Werkzeug umzubauen.

## Entscheidung
Werkzeuge werden als MCP-Server (Model Context Protocol) angebunden. Der Core ist MCP-Client.
In M0 gibt es bewusst noch keine Werkzeuge; nur das Policy-Gerüst existiert.

## Begründung
- Offener Standard, viele fertige Server, sprachunabhängig.
- Werkzeuge laufen in eigenen Prozessen und lassen sich einzeln absichern oder entfernen.

## Konsequenzen
- Jeder Werkzeugaufruf bekommt eine `RiskClass` und läuft durch `policy.decide(...)`.
  FINANCIAL ist standardmäßig DENY; alle schreibenden oder sendenden Klassen verlangen CONFIRM.
- Jeder Aufruf wird im Audit-Log (ohne Inhalte) festgehalten.
- Neue Abhängigkeiten (MCP-SDK) erst nach Rückfrage.
