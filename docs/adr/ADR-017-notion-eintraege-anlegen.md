# ADR-017: Notion, neue Einträge anlegen

**Status:** akzeptiert (ersetzt in Teilen ADR-008)

## Kontext
ADR-008 hielt Notion strikt lesend, weil die JARVIS-OS-Daten geschützt sind. Der Nutzer will
JARVIS aber auch sagen können "Leg in Mission Control den Eintrag 'Steuer machen' an" und hat
am 12.11. entschieden: Schreiben ja, aber nur mit Vorschau und Rückfrage.

## Entscheidung
- **Genau eine Schreibaktion: neue Seite anlegen.** Werkzeug `notion_add_entry(datenbank, titel,
  notiz)`, Risikoklasse WRITE_REVERSIBLE, also CONFIRM: Vorschau ("Notion: neuer Eintrag in
  'mission_control': Titel, Notiz: ...") und Rückfrage, erst bei Ja wird geschrieben. Das
  Vorschau-Werkzeug `preview_notion_add_entry` schreibt nichts, prüft aber vorher Token,
  Zugriff und Titel-Spalte, damit der Nutzer nicht für etwas gefragt wird, das ohnehin scheitert.
- **Zugriffsgrenzen im Client** (`client.py`): zwei Allowlists. Lesen wie bisher (Datenbank
  abfragen, Seite, Blöcke) plus das Schema einer Datenbank (`GET /data_sources/{id}`, für den
  Namen der Titel-Spalte). Schreiben: ausschließlich `POST /pages`. Alles andere (PATCH, DELETE,
  PUT, Kommentare, Suche, Nutzer, Blöcke anhängen, `POST` auf eine vorhandene Seite) wird vor dem
  Netz verworfen. Tests belegen das, auch dass es im Paket keinen Code zum Ändern, Archivieren
  oder Löschen gibt.
- **Nur Konfiguriertes:** angelegt wird ausschließlich in Datenbanken aus `NOTION_DB_<NAME>`.
- **Inhalt:** Titel (höchstens 200 Zeichen) in der Titel-Spalte, Notiz (höchstens 2000 Zeichen)
  als Absätze. Keine weiteren Eigenschaften (Status, Fälligkeit ...): die Spaltennamen sind je
  Datenbank verschieden, und ein falsch gesetzter Wert wäre schwerer zu bemerken als ein
  fehlender.
- **Fehler:** Lehnt Notion das Anlegen ab (403/404), steht dort, dass die Integration die
  Berechtigung "Inhalte einfügen" und Zugriff auf die Datenbank braucht. Diagnosezeilen enthalten
  weiter nur Schritt, Status und Notion-Fehlercode, nie Titel, Text oder Token.
- **Audit:** je Aufruf eine Zeile (Werkzeug, Risikoklasse, Entscheidung, Ergebnis), keine Inhalte.
- **Evals:** Die Notion-Attrappe protokolliert Anlegen nur (JSON-Zeile), nichts geht ins Netz.

## Konsequenzen
- Ein angelegter Eintrag lässt sich von JARVIS nicht mehr ändern oder löschen; das geht in Notion
  (Papierkorb). Fehlerhafte Einträge entstehen nur nach bestätigter Vorschau.
- Gegen die echte Notion-API ist das Anlegen nicht automatisch getestet (kein Zugang in den
  Tests); die Anfrage folgt der Notion-Dokumentation für `data_source_id` als Elternobjekt und
  sollte beim ersten Einsatz mit einem Testeintrag geprüft werden.
- Wer nur lesen will, lässt `notion_add_entry` in `jarvis.toml` weg: dann sieht das Modell das
  Werkzeug nie.
