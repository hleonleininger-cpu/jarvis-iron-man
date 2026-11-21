# ADR-024: HUD (Desktop-Oberfläche)

**Status:** akzeptiert (Block A: Status-Ereignisse und Oberfläche im Browser; Block B:
Desktop-App mit Tauri 2). Plan: `docs/PLAN-M3.md`.

## Kontext
M3 verlangt eine Oberfläche im Iron-Man-Stil, die live zeigt, was JARVIS hört, denkt und tut,
mit Abbrechen-Knopf. Die bisherige Web-Oberfläche (ADR-010) ist bewusst schlicht und ohne
Build-Kette. Master will Tauri 2 mit React (Masterauftrag §4); das HUD-Frontend soll später (M8)
auch die Handy-App (PWA) werden, dann entfällt `api/web`.

## Entscheidung
- **Eigener Ordner `hud/`:** Vite, React 18, TypeScript (streng). Die Build-Kette gilt nur dort;
  `core/` und `api/web` bleiben ohne Node. Das HUD ist ein dünner Client: es kennt nur die
  HTTP-Schnittstelle des Core.
- **Status-Ereignisse im vorhandenen Strom `/v1/events`:** kein zweiter Dauerstrom. Beim
  Verbinden kommt `snapshot` (letzter Stand von Sprachzustand, Mikrofon, Denken), danach
  `state`, `mic`, `level`, `heartbeat`, `thinking`, `tool_start`, `tool_end`, `stop`,
  `screen_read`. Ältere Clients übergehen unbekannte Ereignisse (`api/web/app.js` tut das).
- **Inhaltsfrei per Bauart:** `jarvis_core/status.py` (`StatusBus`) erlaubt je Art nur feste
  Felder (Name, Risikoklasse, Ergebnis, Dauer, Zahlen), Texte höchstens 64 Zeichen. Nachrichten,
  Werkzeugargumente und -ergebnisse kommen nie hinein. Rückfragen (mit Vorschau) und Erinnerungen
  laufen wie bisher über ihren autorisierten Weg (ADR-005, ADR-013).
- **Quelle der Werkzeug-Ereignisse:** nur `McpHub.gated_call`, denn nur dort sind Ergebnis und
  Dauer bekannt. Der Bus liegt in einem eigenen Modul unter allen Schichten und wird dem Hub
  übergeben; `ChatService` meldet `thinking` und `stop`.
- **Sprachclient:** meldet `state` und `mic` (Audit, nur die Art), `level` und `heartbeat` (nur
  HUD, sonst flutete das Audit) an `POST /v1/voice/event`. Ohne Meldung seit 12 s zeigt das HUD
  Zustand und Mikrofon als "unbekannt" (Absturz erkennbar). Das HUD öffnet nie ein Mikrofon.
- **Herkunft:** Der CSRF-Schutz des Core (fremder `Origin` wird abgelehnt, ADR-018) bleibt
  unverändert. Im Entwicklungsmodus (`npm run dev`, nur 127.0.0.1) prüft zuerst eine Middleware
  (`hud/src/dev/originGuard.ts`): nur Anfragen ohne Browser-Herkunft oder mit Origin
  `http://127.0.0.1:5173`/`http://localhost:5173` und `Sec-Fetch-Site` `same-origin`/`none`
  kommen durch, alle anderen bekommen 403. Erst danach entfernt der Proxy `Origin` und
  `Sec-Fetch-*`. `server.cors` ist aus, damit keine fremde Seite Antworten des Dev-Servers lesen
  kann. Ohne diese Prüfung hätte jede Webseite über den Proxy den Not-Aus auslösen oder Anfragen
  stellen können (Fund der Prüfrunde, behoben). Die Desktop-App (Block B) ruft den Core aus dem
  Rust-Teil.
- **Content-Security-Policy:** Der gebaute Stand (`npm run build`) bekommt eine strenge CSP als
  Meta-Tag (`default-src 'self'`, keine Fremdquellen). Im Entwicklungsmodus geht das nicht, Vite
  braucht dort Inline-Skripte. Die CSP der Desktop-App kommt mit Block B in `tauri.conf.json`.
- **Kürzel:** Das HUD kennt in Block A nur Strg+. (Not-Aus, solange das Fenster den Fokus hat).
  Das systemweite Not-Aus-Kürzel folgt mit Block B (PLAN-M3 §5).
- **Keine HTML-Einfügung:** Text nur als Text (React), ESLint verbietet
  `dangerouslySetInnerHTML`, `innerHTML`, `eval`; ein Test prüft den Quelltext.
- **npm:** `hud/.npmrc` setzt `legacy-peer-deps=true`. npm 10 (mit Node 22) bricht bei den
  optionalen Peer-Abhängigkeiten von vitest ab (`reading 'edgesOut'`). Die nötigen Peers (vite,
  jsdom) stehen ausdrücklich in `package.json`; Versionen sind fest (`package-lock.json`) und
  waren beim Einbau mindestens zwei Wochen alt.
- **Desktop-App (Block B):** `hud/src-tauri`. Alle Core-Aufrufe laufen im Rust-Teil (`reqwest`
  ohne TLS und ohne Proxy, nur `http://127.0.0.1|localhost|[::1]`, geprüft in `core.rs`; eine
  andere Adresse beendet die App mit Fehler). Die SSE-Ströme liest Rust und reicht sie als
  Tauri-Ereignisse an die Oberfläche. Die Webview darf nur die eigenen Befehle aufrufen und
  Ereignisse empfangen (`capabilities/default.json`), CSP `connect-src ipc: http://ipc.localhost`.
  Die Chat-Nummer vergibt die Oberfläche, damit frühe Ereignisse zuordenbar sind.
- **Kürzel und Tray:** Strg+Alt+N Not-Aus (N wie Not-Aus) (einstellbar über `JARVIS_HUD_STOP_KEY`; verlangt
  Strg und Alt, nie eines der Kürzel des Sprachclients J/S/X/D, nie eine Taste mit AltGr-Zeichen
  der deutschen Tastatur), Strg+Alt+O Overlay. Windows meldet AltGr als Strg+Alt: das geplante
  Strg+Alt+Q wäre AltGr+Q = "@" gewesen und hätte jedes getippte @ geschluckt (Fund der
  Prüfrunde Block B). Strg+Alt+F12 schied ebenfalls aus: unter Windows öffnet es auf vielen
  Rechnern die Intel-Grafiksteuerung, unter X11 ist es ein Konsolenwechsel. Aus demselben Grund wie bei Q ist das Stumm-Kürzel des Sprachclients jetzt Strg+Alt+S
  statt Strg+Alt+M (= µ). Der Not-Aus-Handler sitzt in Rust
  und ruft `POST /v1/stop` direkt, wirkt also auch bei hängender Oberfläche. Lässt sich ein
  Kürzel nicht registrieren, meldet das HUD es sichtbar. Tray: HUD zeigen, Overlay, Not-Aus,
  "Mit Windows starten" (Standard aus, nur in der installierten App), Beenden. Das Schließen des
  Fensters versteckt es nur, damit Not-Aus-Kürzel und Tray nicht still verschwinden.
- **Overlay:** rahmenlos, immer vorn, Klicks gehen durch; ohne Gesprächsfeld. Der rote Knopf ist
  dort nicht klickbar, Not-Aus und Verlassen gehen über Kürzel und Tray.
- **Token:** Das HUD sendet noch kein Zugangstoken. Mit `require_api_token = true` bekommt es
  401 (auch der Not-Aus aus dem HUD); es zeigt das, und `jarvis stop` hilft weiter. Standard ist
  localhost ohne Token. Eine Token-Abfrage (nur im Arbeitsspeicher) kommt, wenn sie gebraucht wird.
- **Neuladen der Seite:** `events_start` ersetzt den alten Strom und beendet Chats der alten
  Seite; der Core schickt einen frischen `snapshot`. Chat-Nummern sind zufällig, der
  Overlay-Zustand wird nachgefragt.
- **Robustheit:** keine HTTP-Umleitungen (sonst könnte ein lokaler Fremdprozess auf einen
  anderen Host umleiten), Lese-Frist 45 s im Strom (der Core pingt alle 15 s), eine nicht lokale
  `JARVIS_CORE_URL` beendet die App nicht still, sondern wird angezeigt.
- **CSP doppelt vermieden:** Das CSP-Meta aus dem Vite-Bau entfällt im Tauri-Bau, dort gilt nur
  die CSP aus `tauri.conf.json` (ein Test prüft sie).
- **Kacheln (M3.6):** `GET /v1/tiles`. Systemwerte aus `psutil` (nur Zahlen). Termine und
  Aufgaben über `McpHub.read_tool`: nur als READ deklarierte Werkzeuge, Kanal `hud`, Policy und
  Audit wie immer, ohne Argumente und Ergebnis. Abruf über `ChatService.idle_turn`: nur wenn
  keine Anfrage läuft oder wartet (sonst der letzte Stand mit Zeitstempel), unter der
  Anfrage-Sperre und mit dem Hub der Anfragen, der Not-Aus bricht ihn ab. Höchstens alle fünf
  Minuten, damit Audit und iCloud nicht jede Minute belastet werden. Die Kacheln tragen Inhalte
  (Termintitel, Aufgaben) und gehen deshalb nur über diesen autorisierten Endpunkt, nie über den
  Statusstrom.
- **Situatives HUD (M3.7):** feste Regelliste in `hud/src/situation.ts`; im Overlay nur das
  Gebrauchte.
- **3D (M3.10):** three.js, nur nachgeladen, wenn die 3D-Ansicht an ist (eigener Baustein);
  ohne WebGL zurück zum flachen Ring.
- **Schrift:** keine Schriftdatei; Bahnschrift ist unter Windows eingebaut, sonst Systemschrift.

## Konsequenzen
- Zwei Oberflächen bis M8 (HUD am PC, schlichte Web-Oberfläche fürs Handy).
- Ein neuer CI-Job `hud` (Linux: Lint, Typen, Tests, Bau).
- Das Hardware-Profil (S/M/L) ermittelt der Core einmal im Hintergrund beim Start und gibt es
  als Ereignis `profile` und im `snapshot` mit (M3.10: 3D automatisch erst ab Profil M).