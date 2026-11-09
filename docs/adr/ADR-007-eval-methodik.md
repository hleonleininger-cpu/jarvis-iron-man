# ADR-007: Eval-Methodik (messen statt glauben)

**Status:** akzeptiert

## Kontext
Kleine lokale Modelle (z. B. `qwen3.5:4b`) wählen Werkzeuge nicht immer zuverlässig, und die
Ausgaben schwanken von Lauf zu Lauf. Die Modellwahl je Hardware-Profil soll auf Zahlen beruhen.

## Entscheidung
`jarvis evals run` lässt feste deutsche Aufgaben (`evals/tasks.toml`, Daten statt Code) gegen den
**echten** Agent-/Hub-/Policy-Pfad und ein gewähltes Ollama-Modell laufen.

- **Attrappen statt Netz:** Die echten MCP-Server laufen als stdio-Prozesse, nur ihre Netzschicht
  ist ersetzt (`mcp_servers/doubles/`). Werkzeugnamen, Beschreibungen, Validierung, Vorschau und
  Risikoklassen sind identisch zum Betrieb. Die Kalender-Attrappe protokolliert Schreibaufrufe
  (PUT), statt zu schreiben. Außer Ollama braucht der Lauf kein Netz, keine Zugangsdaten.
- **Geprüft wird nicht der Text, sondern:** welche Werkzeuge im Audit-Log stehen (genau die
  erwarteten, sonst Fehlschlag), wie viele Schreibaufrufe die Attrappe erreichten, ob eine
  Bestätigung angefragt wurde. Bei Antwort „Nein“ gilt jeder Schreibaufruf als Fehlschlag.
  Der Antworttext zählt nur für optionale Muster der Aufgabe (`answer_any`, `answer_forbid`,
  z. B. echte Werte vorhanden, keine erfundenen Werte) und dafür, dass eine Antwort kam.
- **Nichtdeterminismus:** Jede Aufgabe läuft N-mal (Standard 3). Ausgewiesen werden Erfolge x/N
  je Aufgabe, getrennt nach Werkzeug / Sicherheit / Antwort, und gesamt. Ein Durchlauf besteht nur,
  wenn alle drei Aspekte stimmen.
- **Fehler:** Wirft ein Durchlauf einen Fehler oder überschreitet die Zeit, zählt er als
  Fehlschlag, der Lauf geht weiter. Fällt Ollama aus oder fehlt das Modell, wird der gesamte Lauf
  abgebrochen und **kein** Teilergebnis gespeichert.
- **Ergebnisse:** `evals/results/<Zeit>-<Modell>.json` (je Durchlauf aufgerufene Werkzeuge,
  Schreibaufrufe, Gründe, gekürzte Antwort). Sie sind Messwerte für diese Hardware und Version.

## Konsequenzen
- Neue Aufgaben kommen ohne Codeänderung dazu. Die Aufgaben sollten Fälle aus dem echten Betrieb
  abbilden (jeder beobachtete Fehlgriff wird zur Aufgabe).
- Die Attrappen müssen mit den echten Servern mitwachsen; Werkzeug- und Risikodefinition der
  Evals (`eval_servers`) folgt `jarvis.example.toml` und muss bei Änderungen dort nachgezogen werden.
- Kleine Stichproben (N = 3) zeigen Tendenzen, keine Signifikanz; für Modellvergleiche `--runs`
  erhöhen.
- Evals sind nicht Teil von CI (brauchen Ollama); der Mechanismus selbst ist mit einem
  skriptbaren Testmodell getestet.
