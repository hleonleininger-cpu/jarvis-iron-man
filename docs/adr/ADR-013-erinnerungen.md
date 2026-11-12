# ADR-013: Erinnerungen mit Meldung zur Uhrzeit

**Status:** akzeptiert

## Kontext
Der Nutzer will sagen können "Erinnere mich um 18 Uhr an den Müll" und zur Uhrzeit eine Meldung
bekommen, solange JARVIS auf dem PC läuft. Werkzeuge sind MCP-Server (ADR-004); der Core startet
sie je Anfrage und hat sonst keine Verbindung zu ihnen.

## Entscheidung
- **Ablage im Notiz-Server (ADR-012):** `add_note` bekommt `erinnern_um` (JJJJ-MM-TTTHH:MM,
  Europe/Berlin, muss in der Zukunft liegen). Kein eigener Server, keine zweite Datei.
- **Abholen über ein internes Werkzeug:** `take_due_reminders` gibt fällige, offene Erinnerungen
  zurück und markiert sie in derselben Transaktion als zugestellt (jede genau einmal, auch bei
  zwei Abholern). In `jarvis.toml` steht es unter `reminders = "take_due_reminders"`; dem Modell
  wird es nie angeboten.
- **Wächter im Core** (`agent/reminders.py`): hält eine eigene, dauerhafte Verbindung zu den
  Servern mit `reminders` und fragt alle 20 s nach, aber **nur, wenn jemand zuhört** (sonst ginge
  eine Erinnerung verloren; sie kommt dann beim nächsten Zuhörer, verspätet).
- **Zustellung:**
  - Server: `GET /v1/events` (Server-Sent Events, Token wie ADR-011, Ping alle 15 s) mit
    `ready` und `reminder`. Die Web-Oberfläche hört dauerhaft zu, zeigt eine Meldung und, wenn
    erlaubt, eine Systembenachrichtigung.
  - CLI-Dialog: ein Hintergrund-Thread gibt Erinnerungen zwischen den Eingaben aus.
- **Audit:** nur Zustellungen (Kanal `system`, `take_due_reminders`, WRITE_LOCAL), nicht jede
  Abfrage; nie der Text.

## Konsequenzen
- Erinnerungen kommen nur, solange `jarvis serve` mit offener Web-Oberfläche oder ein CLI-Dialog
  läuft. Keine Push-Nachrichten aufs ausgeschaltete Handy.
- iOS zeigt Systembenachrichtigungen nur für installierte Web-Apps; sonst bleibt die Meldung in
  der Seite.
- Wiederkehrende Erinnerungen gibt es noch nicht.
