# ADR-005: Allgemeiner Bestätigungs-Fluss (CONFIRM)

**Status:** akzeptiert

## Kontext
Liefert `policy.decide` für ein Werkzeug CONFIRM (z. B. Termin anlegen), darf nichts passieren,
bevor der Nutzer ausdrücklich zugestimmt hat. M1a lehnte CONFIRM nur ab.

## Entscheidung
Ein einziger, werkzeugunabhängiger Fluss im Core:

1. Das Gate im MCP-Hub hält den Aufruf an und holt eine **Vorschau**. Optional nennt
   `jarvis.toml` je Werkzeug ein Vorschau-Werkzeug (`previews`), das nichts schreibt und dem
   Modell nie angeboten wird; sonst zeigt der Hub Werkzeugname und Argumente.
   Meldet die Vorschau einen Fehler (ungültige Eingabe), geht der Fehler ans Modell, ohne Rückfrage.
2. Die Anfrage geht als Ereignis an den Kanal; der `ConfirmationBroker` wartet auf Ja/Nein
   (Timeout 120 s = Nein).
   - **CLI:** interaktive Rückfrage `Anlegen? [j/N]`; nur j/ja gilt als Ja.
   - **API:** SSE-Event `confirm` mit ID, Vorschau und Frage; Antwort per
     `POST /v1/confirm/{id}` mit `{"approve": true|false}`.
3. Nur nach Ja wird mit **denselben** Argumenten ausgeführt. Nein, Timeout, Abbruch oder fehlender
   Kanal: nichts passiert.

**Audit (nur Metadaten):** zwei Zeilen: `CONFIRM/pending`, dann `CONFIRMED/ok|error` oder
`DENIED/denied|aborted`. Ohne Bestätigungskanal `CONFIRM/blocked`, bei ungültiger Eingabe
`CONFIRM/invalid`.

## Konsequenzen
- Der Agent läuft in einem eigenen Task, damit die Anfrage ausgeliefert wird, während der
  Werkzeugaufruf wartet.
- Neue CONFIRM-Werkzeuge brauchen nur Risikoklasse (und optional ein Vorschau-Werkzeug).
- Das Modell muss relative Zeitangaben selbst in konkrete Daten übersetzen (Systemprompt nennt das
  heutige Datum); die Vorschau zeigt das Ergebnis, der Nutzer prüft es vor dem Schreiben.
- Offen: Bestätigung per Handy/Push (späterer Client), Mehrfach-Bestätigungen in einer Anfrage.
