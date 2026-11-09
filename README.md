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
uv run jarvis audit verify
```

## Server
- `GET /health`
- `POST /v1/chat` mit `{"message": "..."}` → Server-Sent Events (`delta`, `done`, `error`)

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
