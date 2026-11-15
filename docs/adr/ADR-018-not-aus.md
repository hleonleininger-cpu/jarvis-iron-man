# ADR-018: Not-Aus

**Status:** akzeptiert

## Kontext
Der Masterauftrag verlangt einen Not-Aus, der alles sofort stoppt und auch funktioniert, wenn der
Rest hängt (Checkliste M0.5). Bisher gab es nur "Stopp" in der Web-Oberfläche: Es beendet die
eigene laufende Anfrage dieses Browsers, aber weder wartende Anfragen noch die eines anderen
Geräts, und es hilft nicht, wenn der Server selbst nicht mehr antwortet.

## Entscheidung
- **Eine Stelle im Core:** `ChatService.emergency_stop()` bricht alle laufenden und wartenden
  Anfragen ab, beantwortet offene Rückfragen mit Nein (`ConfirmationBroker.reject_all()`) und
  beendet damit die Werkzeug-Server der Anfrage (der Hub räumt beim Abbruch auf). Es wartet auf
  nichts und braucht die Anfrage-Sperre nicht.
- **Drei Auslöser:**
  - `POST /v1/stop` (mit Token, wo der Server eines verlangt),
  - roter Knopf "Not-Aus" und Tastenkürzel Strg+. (Mac: Cmd+.) in der Web-Oberfläche,
  - Befehl `jarvis stop` in der Eingabeaufforderung.
- **Wenn der Server hängt:** `jarvis stop` wartet 5 Sekunden auf die Antwort. Kommt keine,
  beendet es den Server-Prozess über die Prozessnummer, die `jarvis serve` beim Start in
  `data/jarvis.pid` schreibt (und beim Beenden löscht).
- **Zuhörer** bekommen die feste Meldung "Not-Aus: Die Anfrage wurde abgebrochen." und das
  Ereignis-Ende; die abgebrochene Runde landet nicht im Gesprächsverlauf.
- **Audit:** ein Eintrag `emergency_stop` (Kanal, Ergebnis `ok` oder `idle`), ohne Inhalte; die
  abgebrochenen Anfragen stehen wie jeder Abbruch als `aborted` im Log.
- Erinnerungen laufen weiter: Sie sind keine Aktion, sondern nur eine Abfrage der eigenen Datei.

## Konsequenzen
- Der Not-Aus kann ein Werkzeug, das seine Schreibanfrage schon an iCloud oder Notion
  abgeschickt hat, nicht zurückholen. Er verhindert alles, was noch nicht hinausgegangen ist.
- Eine systemweite Taste außerhalb des Browsers gibt es noch nicht; sie kommt mit der
  Desktop-App (M3). Bis dahin: Knopf, Strg+. im Browserfenster oder `jarvis stop`.
- Wird der Server über die Prozessnummer beendet, ist der Gesprächsverlauf weg (er liegt nur im
  Arbeitsspeicher, ADR-009), und JARVIS muss neu gestartet werden.
- Im CLI-Dialog (`jarvis chat`) bleibt Strg+C der Abbruch.
