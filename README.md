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
uv run jarvis secrets set notion          # Notion-Integration-Token (nur Lesen)
uv run jarvis secrets status
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
Zeiträume: heute, morgen, übermorgen, woche, nächste woche, ein Datum oder von/bis (max. 92 Tage).
API: SSE-Event `confirm`, Antwort per `POST /v1/confirm/{id}`. Zugangsdaten nur im
OS-Schlüsselbund. Siehe ADR-005/006.

**Notizen und Aufgaben (lokal):** `add_note` und `complete_note` (CONFIRM), `list_notes` (READ).
Gespeichert in `~/.jarvis/notizen.sqlite3`, kein Netz. Siehe ADR-012.

**Notion (nur Lesen):** `notion_search`, `notion_query`, `notion_get` (alle READ). Token im
Schlüsselbund, Datenbank-IDs in `jarvis.toml` (`[mcp_servers.notion] env`). Es gibt keinen
Schreib-Endpunkt im Code. Siehe ADR-008.

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

**Autostart unter Windows** (Aufgabenplanung, einmalig in einer Eingabeaufforderung; Pfad anpassen):
```bat
schtasks /Create /TN "JARVIS" /SC ONLOGON /TR "cmd /c cd /d C:\Pfad\jarvis-iron-man\core && uv run jarvis serve"
```
Entfernen mit `schtasks /Delete /TN "JARVIS"`.

## Server
- `GET /` Web-Client, `GET /health`
- `POST /v1/chat` mit `{"message": "...", "session_id": "..."}` → Server-Sent Events (`delta`,
  `tool`, `notice`, `confirm`, `done`, `error`). `session_id` ist optional; `done` nennt die ID,
  mit der das Gespräch fortgesetzt wird. Während Funkstille (z. B. offene Rückfrage) kommt alle
  15 s ein Kommentar `: ping`, damit Clients die Verbindung nicht trennen.
- `POST /v1/confirm/{id}` mit `{"approve": true|false}` beantwortet eine Rückfrage.
- `DELETE /v1/sessions/{id}` vergisst ein Gespräch.
- Es läuft immer nur eine Anfrage zugleich; bis zu 3 warten (mit Hinweis), weitere werden mit
  `error` abgewiesen. Der Server antwortet nur unter seiner eigenen Adresse (Host-Header-Prüfung),
  bei Tailnet-Bindung auch unter dem MagicDNS-Namen (`*.ts.net`).

## Konfiguration
`core/jarvis.toml` (Vorlage: `core/jarvis.example.toml`) und Umgebungsvariablen `JARVIS_*`
(z. B. `JARVIS_MODEL`). Umgebung schlägt Datei. `host = 0.0.0.0` wird abgelehnt.
Die Datei wird im aktuellen Ordner gesucht; `JARVIS_CONFIG=/pfad/zur/jarvis.toml` nennt einen
anderen Ort. `jarvis doctor` zeigt, welche Datei gilt, welche MCP-Server deklariert und welche
Zugangsdaten gesetzt sind. Ein relativer `data_dir` gilt relativ zur Konfigurationsdatei.
`home_location = "Dortmund"` nennt den Wohnort: Fragen ohne Ort (z. B. "Wie ist das Wetter?")
beziehen sich dann darauf.
`ollama_base_url` muss auf diesen Rechner, das Heimnetz oder das Tailnet zeigen (keine
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

Entscheidungen: `docs/adr/`. Arbeitsregeln: `CLAUDE.md`.
