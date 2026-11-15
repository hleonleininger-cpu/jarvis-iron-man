# JARVIS (jarvis-iron-man)

Lokaler KI-Assistent: Python-Core mit FastAPI-Server und CLI, Modelle über Ollama (0 € Cloud-Kosten).

## Start
```bash
cd core
uv sync
uv run jarvis init              # legt jarvis.toml aus der Vorlage an (überschreibt nie)
uv run jarvis doctor            # Hardware, Ollama, empfohlenes Modell
ollama pull qwen3.5:4b          # Beispiel: Befehl aus jarvis doctor übernehmen
uv run jarvis chat "Wer bist du?"   # Einzelanfrage
uv run jarvis chat              # Dialog mit Gesprächsverlauf (/neu, /ende)
uv run jarvis serve --open      # nur 127.0.0.1; öffnet die Web-Oberfläche im Browser
uv run jarvis tools list        # MCP-Werkzeuge mit Risikoklasse
uv run jarvis secrets set apple_caldav   # Apple-ID + App-Passwort -> Schlüsselbund
uv run jarvis secrets set notion          # Notion-Integration-Token
uv run jarvis secrets status
uv run jarvis secrets generate api_token  # Token für Zugriff übers Tailnet (iPhone)
uv run jarvis secrets delete notion       # Zugangsdaten wieder entfernen
uv run jarvis evals list        # Eval-Aufgaben
uv run jarvis evals run --model qwen3.5:4b --runs 3   # Werkzeugwahl messen
uv run jarvis audit verify
```

## Werkzeuge (MCP)
In `core/jarvis.toml` deklarierte MCP-Server (Vorlage: `core/jarvis.example.toml`) stellen
Werkzeuge bereit, z. B. `get_weather` (Open-Meteo, ohne Schlüssel; aktuelle Werte und
Vorhersage für heute und die nächsten 3 Tage). Vor jedem Aufruf prüft die
Policy die Risikoklasse; Aufrufe stehen im Audit-Log (nur Werkzeugname, keine Argumente).
`jarvis chat "Wie ist das Wetter in Dortmund?"` ruft das Werkzeug über das lokale Modell auf.
Siehe ADR-004.

**Kalender (iCloud):** `list_events` (READ) liest Termine aus allen Kalendern, `create_event`
(CONFIRM) legt nach Vorschau und Rückfrage `[j/N]` im ersten bzw. gewählten Kalender an; die
Vorschau nennt Datum mit Jahr und Zielkalender und warnt bei Terminen in der Vergangenheit.
`update_event` und `delete_event` (beide CONFIRM) ändern bzw. löschen einen einzelnen Termin:
die Vorschau zeigt alt → neu, überschrieben wird nur, wenn der Termin seit dem Lesen unverändert
ist, und Serientermine bleiben unangetastet ("Verschiebe den Zahnarzt auf Donnerstag 16 Uhr").
Zeiträume: heute, morgen, übermorgen, woche, nächste woche, ein Datum oder von/bis (max. 92 Tage).
API: SSE-Event `confirm`, Antwort per `POST /v1/confirm/{id}`. Zugangsdaten nur im
OS-Schlüsselbund. Siehe ADR-005/006/014.

**Websuche (DuckDuckGo):** `web_search` (READ) liefert bis zu 5 Treffer mit Titel, Adresse und
Kurztext, ohne Konto und Schlüssel. Nur der Suchbegriff geht ins Netz, nie der Gesprächsverlauf;
Treffer gelten als Fremdtext und JARVIS nennt die Quelle. In `jarvis.toml` abschaltbar
(`[mcp_servers.websearch]` entfernen). Siehe ADR-015.

**Ideen, Aufträge, Entwürfe (lokal):** `add_idea`, `add_lead`, `set_project_status`,
`save_draft` (WRITE_LOCAL) und `list_ideas`/`list_leads` (READ) für Nebeneinkünfte: Ideen für
Produkte und Apps, Aufträge mit Frist und Betrag samt Summen, Entwürfe als Textdateien in
`~/.jarvis/entwuerfe/`. JARVIS veröffentlicht, sendet und bezahlt nichts. Siehe ADR-016.

**Notizen und Aufgaben (lokal):** `add_note` und `complete_note` (WRITE_LOCAL: ohne Rückfrage,
aber im Audit-Log), `list_notes` (READ).
Gespeichert in `~/.jarvis/notizen.sqlite3`, kein Netz. Siehe ADR-012.

**Erinnerungen:** "Erinnere mich um 18 Uhr an …" speichert eine Notiz mit Zeitpunkt; JARVIS
meldet sie zur Uhrzeit in der Web-Oberfläche (auf Wunsch als Systembenachrichtigung) oder im
CLI-Dialog, solange er läuft. Siehe ADR-013.

**Notion:** `notion_search`, `notion_query`, `notion_get` (READ) lesen; `notion_add_entry`
(CONFIRM) legt nach Vorschau und Rückfrage einen NEUEN Eintrag (Titel, optional Text) in einer
konfigurierten Datenbank an. Ändern, Archivieren und Löschen gibt es im Code nicht. Token im
Schlüsselbund, Datenbank-IDs in `jarvis.toml` (`[mcp_servers.notion] env`); zum Anlegen braucht die
Integration in Notion die Berechtigung "Inhalte einfügen". In der Vorlage auskommentiert: nach
`jarvis secrets set notion` und dem Eintragen der IDs einschalten. Siehe ADR-008/017.

**Not-Aus:** Roter Knopf "Not-Aus" oder Strg+. in der Web-Oberfläche, `POST /v1/stop` oder
`uv run jarvis stop` brechen sofort alle laufenden und wartenden Anfragen, offene Rückfragen und
Werkzeuge ab. Antwortet der Server nicht mehr, beendet `jarvis stop` seinen Prozess. Siehe ADR-018.

**Diagnose:** Was die Werkzeug-Server auf stderr schreiben (z. B. `[caldav:EVENTS] status=401`),
steht in `data/logs/<server>.log`, jeweils nur vom letzten Lauf.

## Evals
`jarvis evals run` misst, wie zuverlässig das lokale Modell Werkzeuge wählt (Aufgaben in
`evals/tasks.toml`, echte MCP-Server mit Attrappen, kein Netz außer Ollama). Ergebnisse:
`evals/results/`. Siehe ADR-007.

## Gesprächsverlauf
`jarvis chat` ohne Argument führt einen Dialog: JARVIS kennt die vorigen Runden, bis `/neu` oder
`/ende`. Der Verlauf liegt nur im Arbeitsspeicher und ist begrenzt (`history_max_turns`,
`history_max_chars`); im Audit-Log stehen weiterhin keine Inhalte. Siehe ADR-009.

## Web-Oberfläche und iPhone
`jarvis serve` liefert unter `/` einen Chat im Browser aus (Verlauf, Werkzeuge, Rückfragen mit
Ja/Nein). Fürs iPhone den Server an die Tailnet-Adresse binden (`host = "100.x.y.z"` in
`jarvis.toml`), einmal `uv run jarvis secrets generate api_token` ausführen und im iPhone-Browser
`http://<tailnet-name>:8765/` öffnen; beim ersten Senden fragt die Seite nach dem Token.
Ohne Token startet ein nicht-lokaler Server nicht. Siehe ADR-010/011.

**Autostart unter Windows:** `uv run jarvis autostart an` (im Ordner `core`) legt die Startdatei
`JARVIS.cmd` im Autostart-Ordner des Nutzers an: JARVIS startet dann bei jeder Anmeldung in einem
minimierten Fenster. `uv run jarvis autostart status` zeigt den Stand, `uv run jarvis autostart aus`
entfernt die Datei. Braucht keine Administratorrechte.

## Server
- `GET /` Web-Client, `GET /health`
- `POST /v1/chat` mit `{"message": "...", "session_id": "..."}` → Server-Sent Events (`delta`,
  `tool`, `notice`, `confirm`, `done`, `error`). `session_id` ist optional; `done` nennt die ID,
  mit der das Gespräch fortgesetzt wird. Während Funkstille (z. B. offene Rückfrage) kommt alle
  15 s ein Kommentar `: ping`, damit Clients die Verbindung nicht trennen.
- `POST /v1/confirm/{id}` mit `{"approve": true|false}` beantwortet eine Rückfrage.
- `DELETE /v1/sessions/{id}` vergisst ein Gespräch.
- `GET /v1/events` → Server-Sent Events für Meldungen ohne Anfrage (`ready`, `reminder`).
- Es läuft immer nur eine Anfrage zugleich; bis zu 3 warten (mit Hinweis), weitere werden mit
  `error` abgewiesen. Der Server antwortet nur unter seiner eigenen Adresse (Host-Header-Prüfung),
  bei Tailnet-Bindung auch unter dem MagicDNS-Namen (`*.ts.net`).

## Konfiguration
`core/jarvis.toml` (Vorlage: `core/jarvis.example.toml`) und Umgebungsvariablen `JARVIS_*`
(z. B. `JARVIS_MODEL`). Umgebung schlägt Datei. `host = 0.0.0.0` wird abgelehnt.
Die Datei wird im aktuellen Ordner gesucht; `JARVIS_CONFIG=/pfad/zur/jarvis.toml` nennt einen
anderen Ort. `jarvis doctor` zeigt, welche Datei gilt, welche MCP-Server deklariert und welche
Zugangsdaten gesetzt sind. Ein relativer `data_dir` gilt relativ zur Konfigurationsdatei.

**Kontextfenster:** Ollama nutzt über seine OpenAI-Schnittstelle nur seinen Server-Standard
(4096 Tokens) und kürzt längere Prompts still von vorn, also zuerst die Systemanweisung. JARVIS
schickt deshalb nur so viel Verlauf mit, wie neben Systemanweisung, Werkzeugen und einer Reserve
für die Antwort in `context_tokens` passt. Die Werkzeuge der Vorlage brauchen allein rund 3000
Tokens, deshalb: Ollama mit `OLLAMA_CONTEXT_LENGTH=8192` starten (für längere Gespräche 16384) und
`context_tokens` auf denselben Wert setzen; `jarvis doctor` zeigt beides an.

**Wohnort:** `home_location = "Dortmund"` nennt den Wohnort: Fragen ohne Ort (z. B. "Wie ist das Wetter?")
beziehen sich dann darauf.

**Modell-Endpunkt:** `ollama_base_url` muss auf diesen Rechner, das Heimnetz oder das Tailnet zeigen (keine
Cloud-Modelle); Anfragen an Ollama gehen nie über einen System-Proxy und brechen nach 300 s
Funkstille ab.

## Qualität
```bash
cd core
uv run pytest            # Live-Tests werden übersprungen, wenn der Dienst nicht erreichbar ist
uv run pytest -m "not live"   # wie in der CI: ohne echtes Netz, Ollama, iCloud, Notion
uv run ruff check && uv run ruff format --check
uv run pyright
```
Die CI führt die Tests unter Linux und Windows aus.

Entscheidungen: `docs/adr/`. Arbeitsregeln: `CLAUDE.md`.
