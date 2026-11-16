# ADR-019: Langzeitgedächtnis und Tagesjournal

**Status:** akzeptiert (Stand: Speicher, semantische Suche, Journal; Vault und Pflege folgen)

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
- **Suche (M1.8):** Bei jedem `remember` wird der Text über Ollama (`/v1/embeddings`, Standard
  `nomic-embed-text`) in einen Vektor verwandelt und im Eintrag gespeichert (float32 im BLOB).
  `recall` bettet die Frage genauso ein und nimmt die ähnlichsten Einträge (Kosinus-Ähnlichkeit
  mindestens 0,5, bis zu 5 Treffer); Stichwort-Treffer kommen immer dazu, auch für Einträge ohne
  Embedding. Das Ergebnis sagt `semantisch` oder `Stichwortsuche`. Gerechnet wird mit
  `sqlite-vec` (`vec_distance_cosine`, einzige neue Abhängigkeit, von Master am 16.11.
  freigegeben); lässt es sich auf einem Rechner nicht laden, rechnet Python dasselbe (ein Test
  belegt, dass beide gleich ordnen).
- **Nur lokal:** Der Embedder spricht nur mit Loopback, privaten und Tailnet-Adressen (ADR-003),
  ohne System-Proxy; eine andere Adresse wird beim Start abgelehnt.
- **Ohne Modell:** Fehlt das Embedding-Modell oder ist Ollama nicht erreichbar, wird trotzdem
  gespeichert (Eintrag ohne Embedding) und `recall` fällt auf Stichwörter zurück, mit Hinweis
  `ollama pull nomic-embed-text`. Vektoren verschiedener Modelle werden nie verglichen.
- **Vergessen (M1.11):** `forget` löscht Zeile samt Embedding und meldet die Eintragszahl vorher
  und nachher. SQLite läuft mit `secure_delete`: Gelöschtes wird überschrieben und steht nicht
  mehr in der Datei (ein Test prüft die Bytes der Datei ohne VACUUM). Vault-Stücke "vergisst"
  man, indem man die Datei löscht; der nächste Lauf entfernt sie aus dem Index. Journalzeilen sind
  eigene Einträge und bleiben (Textdatei, jederzeit von Hand zu ändern).
- **Pflege (M1.12):** `maintain_memory` (und `jarvis memory-pflege`, und im Hintergrund bei jedem
  Start von `jarvis serve`) entfernt Dubletten (der älteste Eintrag bleibt), holt fehlende oder
  veraltete Embeddings nach (höchstens 300 je Lauf) und gleicht Vault und Notizen ab. Es löscht
  nie etwas anderes als doppelte Gedächtniseinträge.
- **Vault (M1.10):** Das Werkzeug `vault_index` (ohne Parameter: das Modell wählt nie einen Ordner)
  liest Markdown- und Textdateien (`.md`, `.markdown`, `.txt`) aus den Ordnern, die in `jarvis.toml`
  mit `--vault` ausdrücklich erlaubt sind, und die offenen Notizen (`--notizen`, nur lesend über
  eine schreibgeschützte Verbindung). Sie werden in Stücke von etwa 600 Zeichen geteilt und wie
  Gedächtniseinträge durchsucht; Treffer nennen Datei bzw. "Notiz". Es wird nur ein Index geführt,
  die Originale bleiben unverändert. Sicherheit: Links aus dem Ordner hinaus werden nicht
  verfolgt (aufgelöster Pfad muss unter dem Ordner liegen), versteckte Ordner, Binärdateien,
  Dateien über 500 KB und mehr als 2000 Dateien werden übersprungen. Geänderte Dateien werden
  neu eingelesen (Zeitstempel und Größe), verschwundene aus dem Index entfernt. Embeddings
  höchstens 300 Stücke je Lauf (CPU ist langsam), der Rest beim nächsten Aufruf. PDF folgt mit M5.4.
- **Tagesjournal:** Eine Markdown-Datei je Tag in `~/.jarvis/journal/` (`JJJJ-MM-TT.md`), Einträge
  mit Uhrzeit. Der Dateiname entsteht nur aus einem geprüften Datum: kein Pfadausbruch.
- **Audit:** nur Werkzeugname, Risikoklasse, Entscheidung, Ergebnis; nie Inhalte.

## Konsequenzen
- Das Gedächtnis liegt unverschlüsselt als Datei auf dem PC (wie Notizen). Backup ist Sache des
  Nutzers; automatische Backups sind M13.3.
- Ein Eintrag im Journal ist unabhängig vom Gedächtnis: `forget` löscht keine Journalzeilen.
- Die Schwelle 0,5 für `nomic-embed-text` ist eine Annahme, die nur mit dem echten Modell auf
  Masters PC geprüft werden kann (Konstante `MIN_SIMILARITY` in `store.py`).
- Wer das nicht will, entfernt `[mcp_servers.memory]` aus `jarvis.toml`.
