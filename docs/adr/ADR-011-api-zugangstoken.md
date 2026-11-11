# ADR-011: Zugangstoken für die API außerhalb von localhost

**Status:** akzeptiert

## Kontext
ADR-003 erlaubt die Bindung an eine Tailnet-Adresse und kündigt Authentifizierung mit dem ersten
Client an. Mit dem Web-Client (ADR-010) ist dieser Client da. Ohne Schutz könnte jedes Gerät im
Tailnet JARVIS bedienen, Kalender und Notion lesen und Termine bestätigen.

## Entscheidung
- `/v1/*` verlangt `Authorization: Bearer <token>`, sobald der Server nicht nur auf Loopback
  lauscht. `require_api_token = true|false` in `jarvis.toml` erzwingt bzw. erlaubt es abweichend.
- Das Token liegt im OS-Schlüsselbund (Secret `api_token`, ADR-006). `jarvis secrets generate
  api_token` erzeugt 256 Bit Zufall, legt sie ab und zeigt das Token einmal an.
- `jarvis serve` startet ohne Token nicht, wenn eines nötig ist, und nennt den Befehl.
- Vergleich in konstanter Zeit (`hmac.compare_digest`); Antwort 401 mit `WWW-Authenticate: Bearer`.
- Offen bleiben `/`, die statischen Dateien und `/health`: sie enthalten keine Nutzerdaten.

## Konsequenzen
- Auf localhost ändert sich nichts.
- Token-Wechsel: neu erzeugen, Server neu starten, im Browser einmal neu eingeben.
- Das Token steht im `localStorage` des Browsers; wer das Gerät entsperrt hat, hat Zugang. Für
  ein persönliches iPhone ist das vertretbar.
