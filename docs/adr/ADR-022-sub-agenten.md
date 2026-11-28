# ADR-022: Sub-Agenten

**Status:** akzeptiert

## Kontext
Mehrteilige Aufträge sprengen das kleine Kontextfenster lokaler Modelle, wenn alles in einem
Gespräch steht (Checkliste M1.2). Der Planer (ADR-020) arbeitet Schritte nacheinander im selben
Zusammenhang ab; manche Aufträge bestehen aber aus unabhängigen Teilen.

## Entscheidung
- **Auslöser:** nur ausdrücklich, `/team <Auftrag>`. Kein automatisches Erraten.
- **Ablauf:** Der Orchestrator (starkes Modell) zerlegt den Auftrag in höchstens 4 unabhängige
  Teilaufgaben. Jede bearbeitet ein Sub-Agent mit **eigenem Kontext** (nur seine Teilaufgabe,
  kein Gesprächsverlauf, nichts von den anderen) und **eigenem Werkzeug-Teilset** (per
  Embedding gewählt, ADR-020). Er gibt einen Kurzbericht (höchstens 600 Zeichen) zurück. Danach
  fasst der Orchestrator zusammen; nur diese Zusammenfassung wird als Text gestreamt.
- **Ein Modell = nacheinander.** `subagent_parallel = 1` ist der Standard. Die Ausführung
  (`subagents.run_all`) nimmt eine Parallelität bis 4 entgegen, für den Fall, dass mehrere Modelle
  zugleich antworten können; sie hält die Reihenfolge der Berichte und bricht bei einem Fehler
  alle anderen ab.
- **Sicherheit unverändert:** Jeder Werkzeugaufruf läuft durch den Hub (Policy, Rückfrage, Audit).
  Das Teilset erweitert nie das Angebot der Anfrage (`McpHub.toolsets_for` schneidet mit der
  Auswahl). Lokales Schreiben (Notizen, Gedächtnis) fragt nach, weil Sub-Agenten Fremdinhalt
  lesen. Der Not-Aus bricht alle laufenden Sub-Agenten ab (gleicher Task). Jeder Lauf hat sein
  eigenes Anfrage-Limit. Berichte stehen im nächsten Schritt als "nur Daten".
- **Keine Cloud:** Sub-Agenten laufen nie über die Cloud-Stufe (ADR-021).

## Folgen
- Mit einem lokalen Modell bringt `/team` vor allem saubere, kleine Kontexte, keine Zeitersparnis.
- Wie gut kleine Modelle in unabhängige Teile zerlegen, ist nur mit dem echten Modell prüfbar.
