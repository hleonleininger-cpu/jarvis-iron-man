# Plan M3 – HUD

Stand: 21.11.2026, Entwurf der Nachtschicht vom 20.11. Maßgeblich: `docs/MASTERAUFTRAG.md`,
`docs/CHECKLISTE.md` (Abschnitt M3, zehn Nummern), `docs/STAND.md`. Nach der Freigabe-Regel von
Master (18.11.): Plan schreiben, aus Masters Sicht prüfen lassen, bei "perfekt" in der folgenden
Nacht bauen. Heute Nacht wird nichts davon gebaut. Das Prüfergebnis steht am Ende der Datei.

## 1. Ziel und Maßstab
Ein Fenster im Iron-Man-Stil, das live zeigt, was Jarvis hört, denkt und tut: Zustand (Schlaf,
wartet, hört zu, denkt, spricht), laufende Werkzeuge, offene Rückfragen, Termine, Systemlast. Es
ist ein dünner Client wie `jarvis_voice`: es kennt nur die HTTP-Schnittstelle des Core. Läuft
auf Masters Windows-PC, der Core bleibt auf `localhost`/Tailnet (ADR-003). Master kann jederzeit
stoppen (Not-Aus, ADR-018) und sieht sofort, wenn Mikrofon oder Bildschirmlesen aktiv sind.

Nicht Teil von M3: neue Fähigkeiten des Agenten, Sprachlogik (bleibt in `jarvis_voice`), Handy-
Oberfläche (die schlichte Web-Oberfläche bleibt als Ausweichweg bestehen).

## 2. Entscheidungen, die schon feststehen
- Tauri 2 mit React und TypeScript (Masters Vorgabe, Checkliste M3.2). Das Frontend wird mit Vite
  gebaut und liegt in einem neuen Ordner `hud/` im Repo (nicht in `core/`).
- Das HUD ersetzt die Web-Oberfläche nicht. `api/web/` bleibt (ADR-010) und dient dem Handy und
  als Ausweichweg, wenn Node/Rust auf dem Rechner fehlen.
- Keine Cloud, keine CDN-Dateien, keine Schriften von außen. Alles wird mitgebaut.
- Das HUD hat keine eigene Logik für Rechte. Rückfragen, Policy und Audit laufen wie bisher im Core.

## 3. Was dem Core fehlt (kleine Erweiterung)
Heute liefert `/v1/events` nur Erinnerungen und Rückfragen, `/v1/chat` einen Strom je Anfrage. Ein
HUD braucht einen Strom über alles, auch über Anfragen anderer Clients (zum Beispiel der Stimme).

- **Neuer Endpunkt `GET /v1/status` (SSE, mit Zugangstoken wie die übrigen):** schickt beim Verbinden
  den aktuellen Stand (`snapshot`), danach Ereignisse:
  `state` (Zustand des Sprachclients), `tool_start`/`tool_end` (Werkzeugname, Risikoklasse,
  Dauer, Ergebnis ok/Fehler/abgelehnt), `thinking` (Agent arbeitet, Schrittzähler), `confirm_open`/
  `confirm_done`, `stop` (Not-Aus ausgelöst), `mic` (an/aus, Pegel grob), `screen_read` (an/aus).
- **Datenschutz:** der Strom enthält nie Nachrichtentext und nie Werkzeugergebnisse, nur Art, Name,
  Dauer. Das ist dieselbe Regel wie beim Audit. Den Text einer Unterhaltung zeigt nur der
  Chat-Bereich des Clients, der sie selbst geführt hat.
- **Quelle der Ereignisse:** ein kleiner Verteiler `StatusBus` in `jarvis_core/agent` (ein Satz
  Warteschlangen wie beim `ConfirmBroker`), gespeist aus dem vorhandenen `event_stream_handler`
  des Agenten, dem `McpHub` (Werkzeugaufrufe) und `/v1/voice/event`. Kein zweites
  State-System: der Bus hält nur den letzten Stand je Zustand im Arbeitsspeicher.
- **Sprachclient meldet Zustand:** `CoreClient.voice_event` kennt bisher nur Diktat-Ereignisse;
  er bekommt `state` und `mic` dazu. Das ist eine Erweiterung der vorhandenen Methode, keine
  neue Verbindung. Der Pegel für den Visualizer (M3.4) kommt nicht über den Core, sondern aus dem
  Mikrofon des HUD-Prozesses selbst, nur wenn das Fenster "zuhört" gezeigt wird (siehe 5, M3.4).

## 4. Aufbau von `hud/`
```
hud/
  package.json, vite.config.ts, tsconfig.json   (Vite, React 18, TypeScript streng)
  src/            React-Oberfläche: Kacheln, Zustandsring, Aktivitätsliste, Rückfrage-Karte
  src/api/        Client für /v1/status, /v1/chat, /v1/confirm, /v1/stop (SSE selbst gelesen)
  src/theme/      Farben, Schrift, Animationen als CSS-Variablen
  src-tauri/      Rust-Teil: Fenster, Tray, globale Not-Aus-Taste, Overlay-Modus
```
Neue Abhängigkeiten (Rückfrage entfällt, weil sie hier im Plan stehen und Master den Plan freigibt):
Frontend: `react`, `react-dom`, `vite`, `typescript`, `vitest`, `@testing-library/react`;
M3.10 zusätzlich `three`. Rust: `tauri` 2 mit den Plugins `global-shortcut` und `shell` nicht;
nur was die Nummern brauchen. Versionen werden festgeschrieben (`package-lock.json`,
`Cargo.lock`), keine Wildcards.

## 5. Nummern und Blöcke
Ein Block je Nacht, jeder mit Commit, Tests, CI und Anleitung.

### Block A (Nacht 1): Strom, Oberfläche, Aktivität
- **Core:** `StatusBus`, `GET /v1/status`, Tests mit Attrappen (Datenschutz-Test: kein Text im
  Strom, Strom bricht bei Token-Fehler mit 401 ab, Verbindung hält Leerlauf aus).
- **M3.1 Oberfläche im Iron-Man-Stil:** Vite-Projekt, Zustandsring, Aktivitätsliste, Chat-Bereich,
  Rückfrage-Karte, dunkles HUD-Thema mit Cyan/Orange, Schrift mitgeliefert. Läuft zuerst im
  Browser gegen den Core (`npm run dev`), noch ohne Tauri.
- **M3.8 Anzeige, was Jarvis tut, mit Abbrechen-Knopf:** Aktivitätsliste aus `tool_*`-Ereignissen,
  roter Knopf ruft `POST /v1/stop`.
- Tests: Vitest für Reducer und Strom-Parser, Komponententests mit Attrappen-Strom. Linux-CI führt
  `npm ci`, `npm run lint`, `npm run typecheck`, `npm test` aus (neuer Job `hud`).

### Block B (Nacht 2): Desktop-App
- **M3.2 Tauri 2:** Fenster lädt das gebaute Frontend, Core-Adresse und Token kommen aus
  `jarvis.toml` bzw. dem Schlüsselbund über den bestehenden Weg (keine Secrets im Repo oder in
  der App-Datei). Tray-Symbol, "beim Start von Windows starten" wie bei Core (M0.6) als Schalter.
- **Systemweite Not-Aus-Taste** (Strg+Alt+X steht schon für den Sprachclient; im HUD gleiches
  Kürzel, wenn der Sprachclient nicht läuft), erfüllt den Rest von M0.5.
- **M3.5 Transparentes Overlay:** Fenster ohne Rahmen, durchsichtig, immer im Vordergrund,
  Klicks gehen durch, Umschalten per Tray und Kürzel. Standard ist AUS (Fenstermodus).
- Windows-CI: `cargo check` und `cargo clippy` für `src-tauri`; ein echter Fenstertest ist nur
  am PC möglich (Anleitung).

### Block C (Nacht 3): Wirkung
- **M3.3 Boot-Sequenz und Sounds:** kurze Animation beim Start (abschaltbar), Töne als mitgelieferte
  kleine Dateien, Lautstärke einstellbar, "leise" respektiert Masters Systemton.
- **M3.4 Audio-Visualizer:** zwei Quellen: Zuhören = Pegel aus dem Sprach-Zustandsstrom (grob,
  wenige Stufen), Sprechen = Pegel des abgespielten Tons, den der Sprachclient grob mitmeldet.
  Der Ring bewegt sich auch ohne Daten (Ruhe-Atmen), damit nichts "hängt". Das HUD öffnet selbst
  kein Mikrofon.
- **M3.9 Augensymbol:** erscheint, solange `screen_read` aktiv ist. Bildschirmlesen selbst
  gehört zu M7; bis dahin zeigt das Symbol nie etwas an und ist nur durch Test mit Attrappe
  belegt. Ehrlich im Bericht als "vorbereitet" vermerken.

### Block D (Nacht 4): Kacheln und Lage
- **M3.6 Live-Kacheln:** System (CPU/RAM/Akku über ein kleines Core-Werkzeug `system_status`, rein
  lesend, Risiko READ), Termine (vorhandener Kalender-Server, nächste drei), Fokus (aktives
  Projekt aus M-Projekte, falls vorhanden), Missionen (offene Aufgaben aus dem Notizen-Server).
  Jede Kachel zeigt "keine Daten", wenn die Quelle fehlt, und nie einen erfundenen Wert.
- **M3.7 Situatives HUD:** Regeln in einer Tabelle (`hud/src/situation.ts`): zum Beispiel Rückfrage
  offen = Rückfrage groß, Werkzeug läuft = Aktivität vorn, Termin in 10 Minuten = Termin-Kachel,
  sonst ruhig. Regeln sind Daten, per Test belegt.

### Block E (Nacht 5, zuletzt): 3D
- **M3.10 Drehbare 3D-Ansichten:** `three`, nur für Zustandsring/Systemkachel als Modell, per
  Maus drehbar, auf Profil-S-Rechnern abschaltbar (Standard aus, wenn WebGL langsam ist).

## 6. Sicherheit und Datenschutz
- Strom ohne Inhalte (siehe 3). Test, der prüft, dass kein Feld mit Text/Ergebnis vorkommt.
- Das HUD speichert nichts außer Fensterposition und Einstellungen (Overlay, Sounds), lokal.
- Content-Security-Policy in Tauri: `default-src 'self'`, Verbindung nur zur eingestellten
  Core-Adresse (`connect-src`). Keine Fernladung, kein `eval`, kein `dangerouslySetInnerHTML`
  (Lint-Regel plus Test).
- Das Token liegt im Schlüsselbund (ADR-006) und im Arbeitsspeicher, nie in `localStorage`
  der App und nie im Log.
- Mikrofon und Kamera öffnet das HUD nie. Mikrofon-Anzeige kommt vom Sprachclient.
- Not-Aus hat Vorrang: der Knopf ist in jedem Zustand sichtbar und wirkt auch, wenn der Strom
  abgerissen ist (direkter POST).
- Overlay ist standardmäßig AUS und lässt sich per Tray und Kürzel sofort schließen.

## 7. Tests und Prüfung
- Core: pytest wie bisher (Bus, Endpunkt, Datenschutz, Token), ruff, pyright.
- HUD: Vitest, ESLint, `tsc --noEmit`. Neuer CI-Job `hud` auf Linux; auf Windows zusätzlich
  `cargo check`/`clippy`.
- Handarbeit am PC (Anleitung `docs/ANLEITUNG-HUD.md`): Fenster, Tray, Overlay, Kürzel, Sounds,
  Verhalten beim Ausfall des Core. Wirkung und Optik kann nur Master beurteilen.
- Prüfrunde je Block durch eine Opus-Untersession (höchstens eine je Block).

## 8. Voraussetzungen auf Masters PC (nur für Block B ff.)
Node.js 20 LTS oder neuer, Rust (rustup, stable), Microsoft WebView2 (auf Windows 11 vorhanden),
Visual Studio Build Tools (C++). Die Anleitung nennt jeden Schritt mit Befehl. Block A läuft
bereits mit Node allein im Browser, damit Master früh etwas sieht.

## 9. Risiken
- Rust-/Tauri-Bau ist auf dem Linux-Server nicht vollständig prüfbar (Fenster, Tray,
  Overlay-Klickdurchlass): nur am PC.
- Iron-Man-Optik ist Geschmack; erste Fassung bewusst schlicht und anpassbar (Theme-Variablen).
- Viele neue Abhängigkeiten (npm, Cargo): Lockfiles, `npm audit`/`cargo audit` in CI als Warnung.
- Bildschirmlesen (M3.9) ist ohne M7 nicht echt prüfbar.
- Zeit: fünf Nächte, nicht eine. Jeder Block ist für sich lauffähig und liefert Master etwas
  Sichtbares.

## 10. Offene Fragen (nur wenn die Prüfung sie nicht auflöst)
- Soll das HUD den Sprachclient selbst starten/beenden können? (Vorschlag: nein, erst M3.2 stabil.)
- Farbwelt: klassisch Cyan/Orange oder rot-gold wie Mark III? (Vorschlag: Cyan, Theme tauschbar.)

## 11. Prüfergebnis
(folgt: Opus-Prüfung aus Masters Sicht, höchstens zwei Runden)
