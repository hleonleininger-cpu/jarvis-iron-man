# ADR-012: Notizen und Aufgaben lokal (MCP-Server `notes`)

**Status:** akzeptiert

## Kontext
Ein Alltagsassistent muss sich Dinge merken können ("Merk dir: Milch kaufen", "Was muss ich
noch erledigen?"). Notion ist bewusst nur lesend (ADR-008), der Gesprächsverlauf endet mit dem
Prozess (ADR-009).

## Entscheidung
Ein eigener MCP-Server `mcp_servers/notes` speichert Einträge in einer lokalen SQLite-Datei
(Standard `~/.jarvis/notizen.sqlite3`, änderbar mit `--datei`). Kein Netz, keine Zugangsdaten.

| Werkzeug | Risikoklasse | Wirkung |
|---|---|---|
| `add_note(text, faellig?)` | WRITE_REVERSIBLE (CONFIRM) | Eintrag anlegen, optional fällig am JJJJ-MM-TT |
| `list_notes(filter?, erledigte_einbeziehen?)` | READ | offene Einträge, fällige zuerst, überfällige markiert |
| `complete_note(id)` | WRITE_REVERSIBLE (CONFIRM) | Eintrag abhaken |

Schreibende Werkzeuge haben Vorschau-Werkzeuge (`preview_add_note`, `preview_complete_note`) und
laufen über den Bestätigungsfluss (ADR-005). Gelöscht wird nichts; erledigte Einträge bleiben
abrufbar.

## Konsequenzen
- Inhalte liegen nur in der Notiz-Datei; das Audit-Log enthält wie immer nur Werkzeugnamen.
- Jede neue Notiz kostet eine Rückfrage. Ob Notizen ohne Rückfrage gespeichert werden dürfen,
  wäre eine Policy-Änderung (eigene Risikoklasse) und ist offen.
- Evals nutzen den echten Server mit einer Wegwerf-Datei; Schreibaufrufe zählen mit.
