# CLAUDE.md — JARVIS (jarvis-iron-man)

## Mission
Persönlicher, lokal laufender KI-Assistent: Python-Core-Server, dünne Clients, MCP-Werkzeuge.
Jede Änderung verbessert das bestehende System, erweitert es nie chaotisch.
Das alte Repo `jarvis-os` ist nur Referenz und wird nicht verändert.

## Arbeitsmodus (immer in dieser Reihenfolge)
1. Repository analysieren.
2. Bestehende Architektur verstehen.
3. Kleinste saubere Lösung wählen.
4. Implementieren.
5. Lint: `uv run ruff check` und `uv run ruff format --check`.
6. Typen: `uv run pyright`.
7. Tests: `uv run pytest`, danach direkte Funktionstests.
8. Ergebnis berichten.
9. Auf Freigabe warten.

(Befehle im Ordner `core/` ausführen.)

## Git-Regeln
Niemals automatisch committen, pushen, force pushen, Branch wechseln oder das GitHub-Repo
anlegen — außer auf ausdrückliche Anweisung.

## Architektur-Regeln
- Schichten in `jarvis_core`: config, hardware, audit, policy, agent, api, cli.
  `api` und `cli` dürfen `agent` nutzen, nie umgekehrt.
- Vorhandene Architektur und Patterns erweitern, minimale Diffs, keine toten Dateien.
- Keine parallelen State-Systeme.
- Keine neuen Abhängigkeiten ohne Rückfrage.
- Nur localhost/Tailnet (ADR-003), keine Cloud-Modelle, keine Secrets in Repo, Logs oder Audit.
- Audit-Einträge enthalten nie Nachrichteninhalte.

## Qualitätsregeln
Vor jeder Fertigmeldung: pytest, ruff, pyright. Danach berichten:
1. geänderte Dateien
2. Testergebnisse
3. Risiken
4. Empfehlung zum Git-Checkpoint

## Kommunikation
Selbstständig arbeiten, kleine technische Entscheidungen selbst treffen, nur bei echten
Architekturfragen nachfragen. Am Ende kurz und präzise berichten.
