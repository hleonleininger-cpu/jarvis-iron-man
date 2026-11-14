# ADR-014: Termine ändern und löschen

**Status:** akzeptiert

## Kontext
Der Kalender-Server konnte Termine lesen und anlegen (ADR-005/006). Der Nutzer will auch sagen
können "Verschiebe den Zahnarzt auf Donnerstag" oder "Lösch das Training morgen". Das ändert
bzw. vernichtet bestehende Daten in iCloud; ein Fehler des Modells (falscher Termin, falsches
Datum) darf deshalb nie ohne Rückfrage durchgehen.

## Entscheidung
- **Zwei neue Werkzeuge:** `update_event` (WRITE_REVERSIBLE) und `delete_event` (IRREVERSIBLE),
  beide CONFIRM mit eigener Vorschau (`preview_update_event`, `preview_delete_event`). Die
  Vorschau liest den Termin frisch aus iCloud und zeigt, was wirklich dort steht:
  "Termin ändern: alt → neu" bzw. "Termin löschen: …", jeweils mit Kalender. Ein neuer
  Notiztext steht ausgeschrieben in der Vorschau.
- **Termin-ID:** `list_events` gibt je Termin `id` zurück, in der Form
  `<Kalenderordner>/<Datei>.ics`. Kurz, weil kleine Modelle sie abschreiben müssen, und ohne die
  Kontonummer aus dem iCloud-Pfad. Der Server hält keinen Zustand: aus der ID wird die Adresse
  neu gebildet, und zwar nur für Dateien direkt in einem eigenen Terminkalender (bei
  `--kalender` nur in diesem). Alles andere (`..`, fremde Adressen, Unterordner, kodierte
  Schrägstriche) wird abgelehnt, bevor eine Anfrage an eine Termindatei hinausgeht.
- **Kein blindes Überschreiben:** Ändern und Löschen senden `If-Match` mit dem ETag vom Lesen.
  Hat sich der Termin inzwischen anderswo geändert, antwortet iCloud mit 412 und JARVIS meldet
  das, statt zu überschreiben.
- **Nur das Genannte ändert sich:** Die Termindatei wird nicht neu erzeugt, sondern
  zeilenweise angepasst. UID, Erinnerungen (VALARM), Teilnehmer und unbekannte Felder bleiben.
  Ohne neue Zeit bleiben auch DTSTART/DTEND samt Zeitzone unangetastet. Ein neuer Start ohne
  Ende behält die Dauer. SEQUENCE wird hochgezählt.
- **Leere Angaben heißen "unverändert":** Kleine Modelle füllen ungenutzte Parameter gern mit
  `""`. Ort oder Notiz werden nur mit dem ausdrücklichen Wert `-` entfernt; die Vorschau sagt
  es dann ("Ort entfernen", "Notiz entfernen").
- **Ganztägige Termine:** `ende` ist beim Anlegen und Ändern der letzte Tag, so wie
  `list_events` ihn nennt (in der Termindatei steht das exklusive Ende einen Tag später).
  Vorher wurde ein vom Modell genanntes Enddatum als exklusiv gelesen, der Termin war einen
  Tag zu kurz.
- **Serien bleiben tabu:** Termine mit RRULE oder RECURRENCE-ID und Dateien mit mehreren
  Terminen werden weder geändert noch gelöscht. `list_events` markiert sie mit `serie: true`.
- **Schreibgeschützte Kalender:** Ein 403 beim Schreiben wird als "Kalender lässt keine
  Änderungen zu" gemeldet, nicht als falsches Passwort.

## Konsequenzen
- Zwischen Vorschau und Ausführung wird der Termin erneut gelesen. Ändert ihn in diesen Sekunden
  jemand anderes, wirkt die bestätigte Änderung auf den neuen Stand (nur die genannten Felder);
  die Rückmeldung nennt dann den tatsächlichen alten und neuen Stand.
- Gelöschte Termine sind weg; iCloud hat für Kalender eine eigene Wiederherstellung
  (icloud.com → Datenwiederherstellung), JARVIS selbst kann nichts zurückholen.
- Liefert iCloud ausnahmsweise kein ETag, wird ohne `If-Match` geschrieben.
- Einzelne Termine einer Serie zu verschieben oder ganze Serien zu löschen geht weiterhin nur in
  der Kalender-App.
- Wer das nicht will, entfernt `update_event`/`delete_event` aus `jarvis.toml`; nicht
  deklarierte Werkzeuge bekommt das Modell nie zu sehen.
