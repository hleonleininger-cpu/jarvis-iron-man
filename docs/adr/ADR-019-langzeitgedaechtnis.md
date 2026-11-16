# ADR-019: Langzeitgedächtnis und Tagesjournal

**Status:** akzeptiert (Stand: Speicher, Stichwortsuche, Journal; Embeddings folgen mit M1.8)

## Kontext
Der Masterauftrag (M1.7–M1.12) verlangt ein Gedächtnis, das Neustarts überlebt: "Merk dir, dass
mein Lieblingsverein der BVB ist" soll auch morgen gelten. ADR-009 hält den Gesprächsverlauf
bewusst nur im Arbeitsspeicher; daran ändert sich nichts. Das Gedächtnis ist etwas anderes:
nur, was der Nutzer ausdrücklich merken lässt.

## Entscheidung
- **Eigener lokaler MCP-Server `memory`** (`mcp_servers/memory`, Muster wie `notes`, ADR-012):
  SQLite in `~/.jarvis/gedaechtnis.sqlite3`, kein Netz, nur dieser Server liest und schreibt die
  Datei. Kein zweites State-System: Notizen bleiben im Notiz-Server, der Rohverlauf im Speicher.
- **Werkzeuge:** `remember(text, art)` (WRITE_LOCAL), `recall(frage)` und `list_memories(art)`
  (READ), `forget(id)` (IRREVERSIBLE, also mit Vorschau und Rückfrage, `preview_forget` zeigt den
  Text), `journal_add(text)` (WRITE_LOCAL) und `journal_read(tag)` (READ).
- **Nur Ausdrückliches:** Die Werkzeugbeschreibung weist das Modell an, nur zu merken, was der
  Nutzer sagen lässt. Das Modell speichert nichts von sich aus.
- **Arten:** fakt, vorliebe, person, projekt, sonstiges. Gleiche Texte (ohne Rücksicht auf
  Groß-/Kleinschreibung) werden nicht doppelt gespeichert; Länge höchstens 1000 Zeichen.
- **Suche (Stand jetzt):** Stichwortsuche über Wörter der Frage ohne Füllwörter; bis zu 5
  Treffer, die besten zuerst. Das Ergebnis sagt "Stichwortsuche". Jeder Eintrag hat schon einen
  Platz für ein Embedding (M1.8), damit die Suche später ohne Umbau semantisch werden kann.
- **Vergessen:** `forget` löscht Zeile samt Embedding und meldet die Eintragszahl vorher und
  nachher; der Test belegt, dass der Text nach dem Löschen nicht mehr in der Datenbankdatei steht.
- **Tagesjournal:** Eine Markdown-Datei je Tag in `~/.jarvis/journal/` (`JJJJ-MM-TT.md`), Einträge
  mit Uhrzeit. Der Dateiname entsteht nur aus einem geprüften Datum: kein Pfadausbruch.
- **Audit:** nur Werkzeugname, Risikoklasse, Entscheidung, Ergebnis; nie Inhalte.

## Konsequenzen
- Das Gedächtnis liegt unverschlüsselt als Datei auf dem PC (wie Notizen). Backup ist Sache des
  Nutzers; automatische Backups sind M13.3.
- Ein Eintrag im Journal ist unabhängig vom Gedächtnis: `forget` löscht keine Journalzeilen.
- Die Stichwortsuche findet Synonyme nicht ("Fußballclub" statt "Verein"); das behebt M1.8.
- Wer das nicht will, entfernt `[mcp_servers.memory]` aus `jarvis.toml`.
