# ADR-009: Gesprächsverlauf nur im Arbeitsspeicher

**Status:** akzeptiert

## Kontext
Bis M1d stand jede Anfrage für sich. Rückfragen ("und morgen?", "trag das ein") gingen ins Leere,
weil das Modell die vorige Antwort nicht kannte. Gleichzeitig gilt: Nachrichteninhalte stehen nie
im Audit-Log, und der Core soll keine zweite Zustandsablage bekommen.

## Entscheidung
Der Agent führt je **Sitzung** einen Verlauf (`jarvis_core/agent/sessions.py`, `SessionStore`):

- **Nur im Arbeitsspeicher.** Nichts wird auf die Platte geschrieben; mit dem Prozess endet der
  Verlauf. Das Audit-Log bleibt unverändert (eine Zeile je Anfrage, ohne Inhalte).
- **Ganze Runden.** Gespeichert wird eine Runde (Frage, Werkzeugaufrufe, Antwort) erst, wenn sie
  vollständig beantwortet ist. Fehlgeschlagene oder abgebrochene Runden hinterlassen nichts.
- **Begrenzt.** Höchstens `history_max_turns` Runden und `history_max_chars` Zeichen je Sitzung
  (älteste Runden fallen zuerst weg), höchstens 32 Sitzungen, 6 Stunden ohne Nutzung = vergessen.
- **Kontextfenster.** Je Anfrage wird nur so viel Verlauf mitgeschickt (jüngste ganze Runden),
  wie neben Systemanweisung, Werkzeugdefinitionen, Frage und 1024 Tokens Antwort-Reserve in
  `context_tokens` passt (Schätzung 3 Zeichen je Token). Grund: Ollama kürzt über `/v1` still
  auf seinen Server-Standard (4096), und zwar von vorn. Ist schon die Anfrage allein zu groß,
  kommt ein Hinweis.
- **Systemprompt je Anfrage.** Er wird als `instructions` übergeben, gilt also jedes Mal neu
  (aktuelles Datum) und liegt nicht im Verlauf.
- **Kanäle.**
  - CLI: `jarvis chat` ohne Argument startet einen Dialog (`/neu`, `/ende`).
    `jarvis chat "..."` bleibt eine Einzelanfrage ohne Verlauf.
  - API: `POST /v1/chat` nimmt optional `session_id`; ohne Angabe beginnt ein neues Gespräch. Die
    ID steht im `done`-Ereignis. `DELETE /v1/sessions/{id}` vergisst eine Sitzung.
  - Evals laufen ohne Sitzung, jede Aufgabe für sich.

## Begründung
- Kleinste Lösung, die Rückfragen ermöglicht; keine neue Abhängigkeit, kein neues Speicherformat.
- Inhalte verlassen den Prozess nicht; die Zusage "keine Inhalte im Audit/Log" bleibt prüfbar.
- Das Zeichenbudget schützt kleine lokale Modelle davor, dass alte Werkzeugergebnisse das
  Kontextfenster füllen.

## Konsequenzen
- Nach einem Neustart des Servers beginnt jedes Gespräch neu.
- Sitzungs-IDs dürfen vom Client gewählt werden (`[A-Za-z0-9_-]{1,64}`). Sie sind kein
  Zugriffsschutz; der Server ist nur über localhost/Tailnet erreichbar (ADR-003).
- Ein dauerhaftes Gedächtnis (über Neustarts hinweg) bräuchte einen eigenen ADR.
