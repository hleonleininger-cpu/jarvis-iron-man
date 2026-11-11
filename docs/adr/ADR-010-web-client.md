# ADR-010: Web-Client vom Core ausgeliefert

**Status:** akzeptiert

## Kontext
Bisher war JARVIS nur über die CLI oder rohe HTTP-Aufrufe nutzbar. Gewünscht ist ein Zugang aus
dem Browser, auf dem PC wie auf dem iPhone (über das Tailnet, ADR-003). Ein eigener Client
mit Build-Kette oder App wäre für den Anfang zu schwer.

## Entscheidung
Der Core liefert unter `/` einen dünnen Web-Client aus (`jarvis_core/api/web/`: `index.html`,
`app.js`, `app.css`). Er nutzt nur die vorhandene API:

- `POST /v1/chat` per `fetch` und liest die Server-Sent Events selbst (EventSource kann kein POST),
  Pings (`: ping`) werden übergangen.
- Rückfragen erscheinen als Karte mit Vorschau und den Knöpfen Ja/Nein (`POST /v1/confirm/{id}`).
- Die `session_id` aus `done` führt das Gespräch fort; „Neues Gespräch“ leert die Anzeige sofort
  und ruft im Hintergrund `DELETE /v1/sessions/{id}` auf.
- Ein Zugangstoken (ADR-011) fragt der Client beim ersten 401 ab und merkt es sich im Browser
  (`localStorage`).

Sicherheit:
- Kein Build, keine Abhängigkeiten, kein CDN: der Client lädt nichts von außen.
- Strenge Content-Security-Policy (`default-src 'none'`, Skripte/Stile nur vom Core,
  `frame-ancestors 'none'`), `nosniff`, `no-referrer`.
- Modell- und Werkzeugtext wird nur per `textContent` eingefügt, nie als HTML (Test prüft, dass
  `innerHTML`/`eval` nicht vorkommen).

## Konsequenzen
- Läuft auf dem iPhone im Browser (Tailnet-Adresse), ohne App-Store.
- Kein Markdown-Rendering: Antworten erscheinen als Text mit Zeilenumbrüchen.
- Getestet mit TestClient (Header, Auslieferung) und einmalig per Headless-Chromium Ende-zu-Ende.
