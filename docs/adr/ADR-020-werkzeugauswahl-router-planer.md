# ADR-020: Werkzeugauswahl, Modell-Router und Aufgabenplaner

**Status:** akzeptiert

## Kontext
Mit jedem Werkzeug wächst der Prompt, und kleine lokale Modelle verlieren den Überblick
(Checkliste M1.4). Master hat Platz für zwei Ollama-Modelle (M1.1) und will mehrstufige Aufträge
(M1.3) und Simulationen (M1.15). Alles bleibt lokal (ADR-003) und läuft über den vorhandenen
Agenten, den Hub und die Policy.

## Entscheidung
- **Werkzeugauswahl (M1.4):** `ToolSelector` bettet Werkzeugbeschreibungen und die Anfrage per
  Ollama-Embedding ein und bietet nur die `tool_select_limit` passendsten Werkzeuge an. Kurze
  Folgefragen werden mit der Frage davor bewertet. Das ändert nur das Angebot an das Modell:
  `gated_call` prüft jeden Aufruf weiter (Policy, Rückfrage, Audit), ein nicht angebotenes
  Werkzeug wird als unbekannt abgelehnt. Fällt das Embedding-Modell aus, werden alle Werkzeuge
  angeboten (60 s Pause bis zum nächsten Versuch). Standard: aus (`tool_select_limit = 0`).
- **Router (M1.1):** Nur mit `model_fast` in `jarvis.toml`. Das schnelle Modell bekommt kurze
  Fragen (bis 160 Zeichen), die zu keinem Werkzeug passen (Ähnlichkeit unter `router_tool_match`),
  und dann ohne Werkzeuge. Alles andere, jede unklare Lage und jeder Plan geht an das starke
  Modell (`model_strong`, Alias für `model`). Im Stream und im Audit stehen nur Stufe und
  Modellname, nie Inhalte.
- **Planer (M1.3):** Ausdrücklich ausgelöst mit `/plan <Auftrag>`. Das Modell schreibt eine
  nummerierte Liste (höchstens 6 Schritte), jeder Schritt läuft als eigener Agent-Lauf mit den
  Werkzeugen und einem eigenen Anfrage-Limit, am Ende prüft das Modell das Ergebnis selbst. Der
  Plan, jeder Schritt und die Prüfung kommen als `Progress`-Ereignis im Stream (API-Ereignis
  `progress`, Web, CLI). Läuft im selben Task wie jede Anfrage, deshalb wirkt der Not-Aus.
  Der Planer wird absichtlich nicht automatisch erraten: ein Fehlalarm kostet viele Modellaufrufe.
  Der Verlauf der Sitzung wird für Planläufe nicht fortgeschrieben.
- **Simulationen (M1.15):** `/simulation <Frage>` oder "Was passiert, wenn …" laufen als Plan,
  aber mit nur lesenden Werkzeugen (Risikoklasse READ). Das Ergebnis trägt den Hinweis, dass es
  eine Einschätzung ist.

## Folgen
- Schwellen (Ähnlichkeit, Kürze) hängen vom Embedding-Modell ab und sind nur mit dem echten
  Modell prüfbar; sie sind einstellbar bzw. leicht änderbar.
- Ohne zweites Modell und ohne `tool_select_limit` ändert sich nichts am bisherigen Ablauf.
