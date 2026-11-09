# ADR-001: Kernsprache Python

**Status:** akzeptiert

## Kontext
JARVIS braucht einen lokal laufenden Core (Server + CLI), der Modelle über Ollama und später
Werkzeuge über MCP anspricht. Das Budget ist 0 €, die Hardware unbekannt, ein Einzelentwickler.

## Entscheidung
Der Core ist in Python 3.12 geschrieben (uv, src-Layout, Paket `jarvis_core`). Server: FastAPI.
Modellzugriff: Pydantic AI. Qualität: ruff, pyright (strict), pytest.

## Begründung
- Reifstes Ökosystem für Agenten, MCP-SDKs und lokale Modelle.
- Strikte Typisierung (pyright strict, Pydantic) fängt Fehler früh ab.
- uv macht Installation und Reproduzierbarkeit auf Windows und macOS einfach.

## Konsequenzen
- Geringere Rohleistung als kompilierte Sprachen; für I/O-gebundenen Betrieb unkritisch.
- Clients (z. B. iPhone) bleiben dünn und sprechen nur die HTTP/SSE-Schnittstelle an.
- Schichten: `config`, `hardware`, `audit`, `policy`, `agent`, `api`, `cli`.
  `api` und `cli` dürfen `agent` nutzen, nie umgekehrt.
