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
| `add_note(text, faellig?)` | WRITE_LOCAL (ALLOW) | Eintrag anlegen, optional fällig am JJJJ-MM-TT |
| `list_notes(filter?, erledigte_einbeziehen?)` | READ | offene Einträge, fällige zuerst, überfällige markiert |
| `complete_note(id)` | WRITE_LOCAL (ALLOW) | Eintrag abhaken |

**Ohne Rückfrage (Entscheidung des Nutzers, 12.11.):** Notizen schreiben nur in eine lokale Datei,
sind umkehrbar und haben keine Außenwirkung. Dafür gibt es die Risikoklasse `WRITE_LOCAL`, die
die Policy ohne Rückfrage erlaubt; jeder Aufruf steht wie immer im Audit-Log (nur Werkzeugname).
Die Vorschau-Werkzeuge (`preview_add_note`, `preview_complete_note`) bleiben: wer doch gefragt
werden will, setzt in `jarvis.toml` `WRITE_REVERSIBLE`. Gelöscht wird nichts; erledigte Einträge
bleiben abrufbar.

## Konsequenzen
- Inhalte liegen nur in der Notiz-Datei; das Audit-Log enthält wie immer nur Werkzeugnamen.
- `WRITE_LOCAL` ist nur für Werkzeuge gedacht, die ausschließlich lokale, eigene Daten ändern.
  Alles mit Außenwirkung (Kalender, Notion, Nachrichten) bleibt bei CONFIRM.
- Evals nutzen den echten Server mit einer Wegwerf-Datei; Schreibaufrufe zählen mit.
