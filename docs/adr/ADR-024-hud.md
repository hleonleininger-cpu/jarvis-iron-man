# ADR-024: HUD (Desktop-Oberfläche)

**Status:** akzeptiert (Block A: Status-Ereignisse und Oberfläche im Browser; Tauri folgt in
Block B). Plan: `docs/PLAN-M3.md`.

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
- **Schrift:** keine Schriftdatei; Bahnschrift ist unter Windows eingebaut, sonst Systemschrift.

## Konsequenzen
- Zwei Oberflächen bis M8 (HUD am PC, schlichte Web-Oberfläche fürs Handy).
- Ein neuer CI-Job `hud` (Linux: Lint, Typen, Tests, Bau).
- Das Hardware-Profil steht noch nicht im `snapshot`: `detect_hardware` startet Unterprozesse
  und gehört nicht in jeden Verbindungsaufbau. Gebraucht wird es erst für M3.10.
