# JARVIS (jarvis-iron-man)

Lokaler KI-Assistent: Python-Core mit FastAPI-Server und CLI, Modelle über Ollama (0 € Cloud-Kosten).

## Start
```bash
cd core
uv sync
uv run jarvis doctor            # Hardware, Ollama, empfohlenes Modell
ollama pull qwen3.5:4b          # Beispiel: Befehl aus jarvis doctor übernehmen
uv run jarvis chat "Wer bist du?"
uv run jarvis serve             # nur 127.0.0.1
uv run jarvis tools list        # MCP-Werkzeuge mit Risikoklasse
uv run jarvis secrets set apple_caldav   # Apple-ID + App-Passwort -> Schlüsselbund
uv run jarvis secrets status
uv run jarvis evals list        # Eval-Aufgaben
uv run jarvis evals run --model qwen3.5:4b --runs 3   # Werkzeugwahl messen
uv run jarvis audit verify
```

## Werkzeuge (MCP)
In `core/jarvis.toml` deklarierte MCP-Server (Vorlage: `core/jarvis.example.toml`) stellen
Werkzeuge bereit, z. B. `get_weather` (Open-Meteo, ohne Schlüssel). Vor jedem Aufruf prüft die
Policy die Risikoklasse; Aufrufe stehen im Audit-Log (nur Werkzeugname, keine Argumente).
`jarvis chat "Wie ist das Wetter in Dortmund?"` ruft das Werkzeug über das lokale Modell auf.
Siehe ADR-004.

**Kalender (iCloud):** `list_events` (READ) liest Termine, `create_event` (CONFIRM) legt nach
Vorschau und Rückfrage `[j/N]` an. API: SSE-Event `confirm`, Antwort per
`POST /v1/confirm/{id}`. Zugangsdaten nur im OS-Schlüsselbund. Siehe ADR-005/006.

## Evals
`jarvis evals run` misst, wie zuverlässig das lokale Modell Werkzeuge wählt (Aufgaben in
`evals/tasks.toml`, echte MCP-Server mit Attrappen, kein Netz außer Ollama). Ergebnisse:
`evals/results/`. Siehe ADR-007.

## Server
- `GET /health`
- `POST /v1/chat` mit `{"message": "..."}` → Server-Sent Events (`delta`, `tool`, `notice`, `confirm`, `done`, `error`)

## Konfiguration
`core/jarvis.toml` (Vorlage: `core/jarvis.example.toml`) und Umgebungsvariablen `JARVIS_*`
(z. B. `JARVIS_MODEL`). Umgebung schlägt Datei. `host = 0.0.0.0` wird abgelehnt.

## Qualität
```bash
cd core
uv run pytest            # Live-Test gegen Ollama wird übersprungen, wenn es nicht läuft
uv run ruff check && uv run ruff format --check
uv run pyright
```

Entscheidungen: `docs/adr/`. Arbeitsregeln: `CLAUDE.md`.
