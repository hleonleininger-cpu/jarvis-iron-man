# ADR-016: Ideen, Aufträge und Entwürfe (lokal)

**Status:** akzeptiert

## Kontext
Der Nutzer will mit JARVIS Nebeneinkünfte aufbauen (digitale Produkte, kleine Apps,
Freelance-Aufträge). JARVIS verdient dabei nichts selbstständig: Bezahlen, Handeln und
Veröffentlichen bleiben gesperrt (Risikoklasse FINANCIAL = DENY, ausgehende Nachrichten nur mit
Bestätigung). Gebraucht wird ein Ort, an dem Ideen, Aufträge und Entwürfe nicht verloren gehen.

## Entscheidung
- **Neuer lokaler MCP-Server `projekte`** nach dem Muster von `notes` (ADR-012): SQLite in
  `~/.jarvis/projekte.sqlite3`, kein Netz, nur dieser Server liest und schreibt die Datei.
- **Werkzeuge:** `add_idea`/`list_ideas` (Kategorie produkt, app, auftrag; Stand neu,
  in_arbeit, veroeffentlicht, verworfen), `add_lead`/`list_leads` (Kunde, Beschreibung, Frist,
  Betrag; Stand offen, angebot, gewonnen, bezahlt, abgelehnt), `set_project_status`,
  `save_draft`. `list_leads` nennt die Summe der bezahlten und der gewonnenen, noch nicht
  bezahlten Aufträge und markiert offene Aufträge mit überschrittener Frist.
- **Risikoklasse WRITE_LOCAL** wie bei Notizen: ohne Rückfrage, aber im Audit-Log (nur der
  Werkzeugname, nie Kunden, Beträge oder Texte).
- **Entwürfe** (Gliederung, Produkttext, Angebot) landen als Markdown-Datei in
  `~/.jarvis/entwuerfe/`. Der Dateiname wird aus dem Titel abgeleitet (nur a–z, 0–9, Bindestrich,
  keine Pfade, keine Windows-Gerätenamen), eine vorhandene Datei wird nie überschrieben
  (`-2`, `-3`, ...), Größe höchstens 50.000 Zeichen.
- **Beträge** werden als Cent gespeichert (Decimal, kaufmännisch gerundet), zwischen 0 und
  1.000.000 Euro; `NaN`, negative und unlesbare Werte werden abgelehnt.
- **Fristen-Erinnerung** läuft über die vorhandenen Erinnerungen (ADR-013): zur Frist legt
  JARVIS zusätzlich eine Notiz mit `erinnern_um` an. Es gibt keine zweite Erinnerungslogik.
- **Systemprompt:** JARVIS hält Ideen und Aufträge fest und legt Entwürfe ab; veröffentlicht,
  gesendet oder bezahlt wird nichts.

## Konsequenzen
- Wer das nicht braucht, entfernt den Block `[mcp_servers.projekte]` aus `jarvis.toml`.
- Kein Austausch mit Notion oder dem Kalender; die Daten bleiben auf dem Rechner. Backup ist
  Sache des Nutzers (eine Datei und ein Ordner).
- Ob eine Idee Geld bringt, entscheidet der Markt, nicht JARVIS: das Werkzeug hilft beim Ordnen
  und Vorbereiten, es verspricht keine Einnahmen.
