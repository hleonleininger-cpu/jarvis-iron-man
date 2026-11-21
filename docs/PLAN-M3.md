# Plan M3 – HUD

Stand: 20.11.2026, Entwurf der Nachtschicht vom 20.11. Maßgeblich: `docs/MASTERAUFTRAG.md`,
`docs/CHECKLISTE.md` (Abschnitt M3, zehn Nummern), `docs/STAND.md`. Nach der Freigabe-Regel von
Master (18.11.): Plan schreiben, aus Masters Sicht prüfen lassen, bei "perfekt" in der folgenden
Nacht bauen. Heute Nacht wird nichts davon gebaut. Das Prüfergebnis steht am Ende der Datei.

## 1. Ziel und Maßstab
Ein Fenster im Iron-Man-Stil, das live zeigt, was Jarvis hört, denkt und tut: Zustand (Schlaf,
wartet, hört zu, denkt, spricht; "hört" heißt hier der Zustand, nicht das Transkript), laufende Werkzeuge, offene Rückfragen, Termine, Systemlast. Es
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
- **Herkunft (Prüfrunde 1, Fund 1):** `same_site` im Core lehnt Anfragen mit fremdem `Origin` ab
  (CSRF-Schutz, ADR-018). Das bleibt unverändert. Deshalb gehen alle Core-Aufrufe der Desktop-App
  aus dem Rust-Teil (Tauri-Befehle mit `reqwest`, Ereignisse an die Oberfläche), nie aus der
  Webview; es gibt dann keinen Browser-Origin. Im Dev-Modus (Block A, `npm run dev`) entfernt ein
  Vite-Proxy `Origin` und `Sec-Fetch-*`; er gilt nur lokal beim Entwickeln.
- **Token/Konfiguration (Fund 8):** Das HUD läuft nur auf dem PC und spricht `127.0.0.1:<Port>`
  (Port als Startparameter, Standard aus `jarvis.example.toml`). Dort ist laut ADR-011 kein Token
  nötig; ist `require_api_token` gesetzt (bewusst, nicht Standard; die Anleitung sagt das, auch wegen
  Autostart), fragt das HUD beim Start danach und der Rust-Teil
  hält es nur im Arbeitsspeicher. Damit entfallen `keyring` und `toml` im Rust-Teil.
- **Reihenfolge der Dokumente:** ADR-024 "HUD" entsteht in Block A (Build-Kette nur in `hud/`,
  `api/web` bleibt Ausweichweg, Herkunft, CSP, Kürzel).
- Das HUD hat keine eigene Logik für Rechte. Rückfragen, Policy und Audit laufen wie bisher im Core.

## 3. Was dem Core fehlt (kleine Erweiterung)
Heute liefert `/v1/events` nur Erinnerungen und Rückfragen, `/v1/chat` einen Strom je Anfrage. Ein
HUD braucht einen Strom über alles, auch über Anfragen anderer Clients (zum Beispiel der Stimme).

- **Entschieden (Prüfrunde 2, Fund 3): die Status-Ereignisse kommen in den vorhandenen Strom
  `GET /v1/events`.** Kein zweiter Dauerstrom, kleinerer Diff; Rückfragen und Erinnerungen laufen
  dort ohnehin. `api/web/app.js` übergeht unbekannte Ereignisse (if/else-Kette, beim Bau geprüft,
  ohne eigenen Test: die Web-Oberfläche hat keine JS-Tests). Beim Verbinden
  sendet der Strom zusätzlich einen `snapshot` (aktueller Stand, inkl. Profil S/M/L), danach:
  `state` (Zustand des Sprachclients), `tool_start`/`tool_end` (Lauf-ID, Werkzeugname,
  Risikoklasse, Dauer, Ergebnis ok/Fehler/abgelehnt), `thinking` (Schrittzähler), `stop`
  (Not-Aus ausgelöst), `mic` (an/aus, dazu ein Lebenszeichen alle ca. 5 s; bleibt es aus, zeigt
  das HUD "unbekannt"), `level` (Pegel, siehe M3.4), `screen_read` (an/aus; Vertrag, den M4.6/M4.8
  später nur noch speisen).
- **Rückfragen bleiben auf dem vorhandenen Weg** (`ConfirmationBroker`, `/v1/events`, ADR-005):
  `confirm_open/confirm_done` werden nicht im Bus doppelt geführt. Der Statusstrom selbst bleibt
  inhaltsfrei; die Rückfrage-Karte bekommt `preview`/`question` weiterhin aus dem autorisierten
  Rückfrage-Weg.
- **Datenschutz:** die neuen Status-Ereignisse enthalten nie Nachrichtentext und nie Werkzeug-
  ergebnisse, nur Art, Name, Dauer (die Regel gilt je Ereignistyp; Rückfragen und Erinnerungen
  bleiben wie bisher). Das ist dieselbe Regel wie beim Audit. Den Text einer Unterhaltung zeigt nur der
  Chat-Bereich des Clients, der sie selbst geführt hat.
- **Quelle der Ereignisse:** Werkzeug-Ereignisse kommen nur aus `McpHub.gated_call` (nur dort sind
  Dauer, Ergebnis und "abgelehnt" bekannt). Damit die Schichten stimmen (`mcp_hub` darf nicht
  nach oben importieren), liegt der kleine Verteiler `StatusBus` in einem eigenen Modul
  `jarvis_core/status.py` und wird dem Hub als Beobachter injiziert. `thinking` und `stop` meldet
  `ChatService` (agent darf auf die untere Schicht zugreifen). Der Sprachclient meldet
  `state`/`mic`/`level`/`heartbeat` über `/v1/voice/event`.
  Kein zweites State-System: der Bus hält nur den letzten Stand je Zustand im Arbeitsspeicher.
- **Sprachclient meldet Zustand:** `CoreClient.voice_event` kennt bisher nur Diktat-Ereignisse;
  er bekommt `state`, `mic` und `level` dazu. Zustandswechsel und Mikrofon an/aus kommen ins Audit
  (nur Ereignisart). Pegelwerte (höchstens 5-10 Hz, 4-8 Stufen) und Lebenszeichen gehen über
  denselben Endpunkt `POST /v1/voice/event` mit den Arten `level` und `heartbeat`, die nicht ins Audit
  gehen (`state` und `mic` gehen hinein); das `Literal` in `VoiceEvent` wird erweitert, ein Test
  belegt, dass `level`/`heartbeat` keinen Audit-Eintrag erzeugen. Sonst flutete das Audit. Das HUD öffnet nie selbst ein
  Mikrofon.

## 4. Aufbau von `hud/`
```
hud/
  package.json, vite.config.ts, tsconfig.json   (Vite, React 18, TypeScript streng)
  src/            React-Oberfläche: Kacheln, Zustandsring, Aktivitätsliste, Rückfrage-Karte
  src/api/        Transportschnittstelle mit zwei Umsetzungen: `fetch` (Dev-Modus, SSE selbst
                  gelesen) und `invoke`/`listen` (unter Tauri). Sie liegt schon in Block A, damit
                  Block B nur die zweite Umsetzung ergänzt. Aufrufe: /v1/events, /v1/chat,
                  /v1/confirm, /v1/stop, /v1/tiles.
  src/theme/      Farben, Schrift, Animationen als CSS-Variablen
  src-tauri/      Rust-Teil: Fenster, Tray, globale Not-Aus-Taste, Overlay-Modus
```
Neue Abhängigkeiten (vollständige Liste; nichts anderes kommt ohne Rückfrage hinzu):

| Paket | Zweck | Nummer |
|---|---|---|
| npm: `react`, `react-dom` | Oberfläche | M3.1 |
| npm: `vite`, `@vitejs/plugin-react`, `typescript` | Bauen | M3.1 |
| npm: `vitest`, `jsdom` | Tests (Komponententests direkt mit `react-dom` und `act`; `@testing-library/react` entfällt, es zöge `@testing-library/dom` nach) | M3.1 |
| npm: `@types/react`, `@types/react-dom` | Typen zu React 18 (Nachtrag beim Bau) | M3.1 |
| npm: `eslint`, `typescript-eslint`, `eslint-plugin-react-hooks` | Lint (CI) | M3.1 |
| npm: `@tauri-apps/cli`, `@tauri-apps/api` | Desktop-App | M3.2 |
| npm: `three` | 3D | M3.10 |
| Cargo: `tauri` (Feature `tray-icon`), `tauri-build` | Fenster, Tray | M3.2 |
| Cargo: `tauri-plugin-global-shortcut` | Not-Aus-Kürzel, Overlay-Umschalter | M3.2/M3.5 |
| Cargo: `tauri-plugin-autostart` | Start mit Windows (Schalter) | M3.2 |
| Cargo: `reqwest` (`default-features = false`, Features `json`, `stream`; kein TLS, nur `http://127.0.0.1`), `futures-util` | Core-Aufrufe und SSE-Ströme lesen im Rust-Teil | M3.2 |
| Cargo: `serde`, `serde_json` | Tauri-Befehle, Ereignisdaten | M3.2 |
| Python: `psutil` (nur falls die `hardware`-Schicht CPU/RAM nicht schon liefert) | System-Kachel | M3.6 |

Das Plugin `shell` wird ausdrücklich nicht verwendet. Töne und Schrift: nur eigene oder frei
lizenzierte Dateien (CC0/OFL), keine Filmsounds. Versionen sind festgeschrieben
(`package-lock.json`, `Cargo.lock`), keine Wildcards.

## 5. Nummern und Blöcke
Ein Block je Nacht, jeder mit Commit, Tests, CI und Anleitung.

### Block A (Nacht 1): Strom, Oberfläche, Aktivität
- **Core:** `StatusBus` (`jarvis_core/status.py`), Erweiterung von `/v1/events` und `/v1/voice/event`, Tests mit Attrappen (Datenschutz-Test: kein Text im
  Strom, Strom bricht bei Token-Fehler mit 401 ab, Verbindung hält Leerlauf aus).
- **M3.1 Oberfläche im Iron-Man-Stil:** Vite-Projekt, Zustandsring, Aktivitätsliste, Chat-Bereich,
  Rückfrage-Karte, dunkles HUD-Thema mit Cyan/Orange, Schrift mitgeliefert. Läuft zuerst im
  Browser gegen den Core (`npm run dev`), noch ohne Tauri.
- **M3.8 Anzeige, was Jarvis tut, mit Abbrechen-Knopf:** Aktivitätsliste aus `tool_*`-Ereignissen,
  roter Knopf ruft `POST /v1/stop`.
- Tests: Vitest für Reducer und Strom-Parser, Komponententests mit Attrappen-Strom. Linux-CI führt
  `npm ci`, `npm run lint`, `npm run typecheck`, `npm test` aus (neuer Job `hud`).

### Block B (Nacht 2): Desktop-App
- **M3.2 Tauri 2:** Fenster lädt das gebaute Frontend; Core-Aufrufe nur aus dem Rust-Teil (siehe 2).
  Keine Secrets im Repo oder in der App-Datei. Tray-Symbol, "beim Start von Windows starten"
  (wie beim Core, M0.9) als Schalter.
- **Systemweite Not-Aus-Taste:** eigenes, einstellbares Kürzel (Standard Strg+Alt+Q; beim Bau
  geändert auf Strg+Alt+N, siehe §16), geprüft gegen
  Kollision mit den Kürzeln des Sprachclients (J/M/X/D; Strg+Alt+X ist dort "unterbrechen", nicht
  Core-Not-Aus). Der Handler sitzt im Rust-Teil und schickt `POST /v1/stop` direkt, auch bei
  hängender Webview. Reagiert der Core nach 5 s nicht, zeigt das HUD den Hinweis auf `jarvis stop`
  (ohne `shell`-Plugin). Erfüllt den Rest von M0.5. Die HUD-Leiste zeigt Strg+Alt+M (Mikrofon aus)
  des Sprachclients an; das HUD selbst kann das Mikrofon nicht schalten.
- **M3.5 Transparentes Overlay:** Fenster ohne Rahmen, durchsichtig, immer im Vordergrund,
  Klicks gehen durch, Umschalten per Tray und Kürzel. Standard ist AUS (Fenstermodus). Im Overlay
  ist der rote Knopf nicht klickbar: Not-Aus und Verlassen gehen dort über Kürzel und Tray;
  ein weiteres Kürzel schaltet den Klick-Durchlass um (Halten-Tasten kennt `global-shortcut` nicht).
  Ist ein Kürzel schon von einem anderen Programm belegt, meldet das HUD sichtbar "Not-Aus-Kürzel
  nicht verfügbar" (mit Test).
- **SSE im Rust-Teil:** Rust liest `/v1/events` und `/v1/chat`, reicht sie als Tauri-Ereignisse an
  die Oberfläche weiter und bricht sie beim Not-Aus und beim Schließen des Fensters ab.
- Windows-CI: `npm ci && npm run build` (Tauri prüft `frontendDist`), dann `cargo check` und
  `cargo clippy` für `src-tauri`, mit `Swatinem/rust-cache`; der Job läuft nur bei Änderungen unter
  `hud/` (Minuten sparen, falls das Repo privat ist). Ein echter Fenstertest ist nur am PC möglich.

### Block C (Nacht 3): Wirkung
- **M3.3 Boot-Sequenz und Sounds:** kurze Animation beim Start (abschaltbar), Töne als mitgelieferte
  kleine Dateien, Lautstärke einstellbar mit Stumm-Schalter (der Systemton ist in der Webview nicht lesbar).
- **M3.4 Audio-Visualizer:** beide Pegel (Zuhören und Sprechen) meldet der Sprachclient gedrosselt
  als `level` (siehe 3). Ohne Daten atmet der Ring ruhig, damit nichts "hängt"; ohne laufenden
  Sprachclient zeigt er nur den Zustand und sagt das. Das HUD öffnet selbst kein Mikrofon.
- **M3.9 Augensymbol:** erscheint, solange `screen_read` aktiv ist. Bildschirmlesen selbst
  gehört zu M4.6 (Privatsphäre-Schalter M4.8); bis dahin zeigt das Symbol nie etwas an und ist nur durch Test mit Attrappe
  belegt. Ehrlich im Bericht als "vorbereitet" vermerken.

### Block D (Nacht 4): Kacheln und Lage
- **M3.6 Live-Kacheln:** ein lesender Endpunkt `GET /v1/tiles` (Cache ca. 60 s, Audit ohne Inhalte);
  das HUD ruft keine MCP-Werkzeuge selbst. Kachelabrufe laufen über denselben Weg wie Anfragen
  (Anfrage-Sperre, `gated_call`, Policy READ, der Not-Aus bricht sie ab) und reihen sich hinter
  laufende Anfragen ein; solange gesperrt ist, liefert der Cache den letzten Stand mit Zeitstempel.
  Kein zweiter Hub. System (CPU/RAM aus der `hardware`-Schicht, sonst
  `psutil`; greift M4.9 nur minimal vor), Termine (nächste drei, aus dem Cache, nie je Frame
  gepollt), Missionen (offene Aufgaben aus dem Notizen-Server), Fokus (ADR-016 kennt kein
  "aktives Projekt": entweder dort definieren oder die Kachel zeigt "keine Daten"). Jede Kachel
  zeigt "keine Daten", wenn die Quelle fehlt, und nie einen erfundenen Wert.
- **M3.7 Situatives HUD:** Regeln in einer Tabelle (`hud/src/situation.ts`): zum Beispiel Rückfrage
  offen = Rückfrage groß, Werkzeug läuft = Aktivität vorn, Termin in 10 Minuten = Termin-Kachel,
  sonst ruhig. Regeln sind Daten, per Test belegt.

### Block E (Nacht 5, zuletzt): 3D
- **M3.10 Drehbare 3D-Ansichten:** `three`, nur für Zustandsring/Systemkachel als Modell, per
  Maus drehbar, auf Profil-S-Rechnern abschaltbar (Standard aus bei Profil S; das Profil kommt im `snapshot`).

## 6. Sicherheit und Datenschutz
- Strom ohne Inhalte (siehe 3). Test, der prüft, dass kein Feld mit Text/Ergebnis vorkommt.
- Das HUD speichert nichts außer Fensterposition und Einstellungen (Overlay, Sounds), lokal.
- Content-Security-Policy in Tauri: `default-src 'self'`, Webview ohne Netzwerkzugriff nach außen
  (`connect-src ipc: http://ipc.localhost`, nur die Tauri-IPC; Core-Aufrufe laufen im Rust-Teil;
  Wert steht fest in `tauri.conf.json`, ein Test prüft ihn). Keine Fernladung, kein `eval`, kein `dangerouslySetInnerHTML`
  (Lint-Regel plus Test).
- Ein Token (nur falls `require_api_token`) liegt im Arbeitsspeicher des Rust-Teils, nie in
  `localStorage` und nie im Log.
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
- Tauri-Bau auf Linux wäre mit `libwebkit2gtk-4.1-dev` möglich; wir bauen trotzdem nur unter
  Windows (Zielsystem, weniger CI-Aufwand). Fenster, Tray und Overlay-Klickdurchlass sind nur
  am PC prüfbar.
- Iron-Man-Optik ist Geschmack; erste Fassung bewusst schlicht und anpassbar (Theme-Variablen).
- Viele neue Abhängigkeiten (npm, Cargo): Lockfiles, `npm audit`/`cargo audit` in CI als Warnung.
- Bildschirmlesen (M3.9) ist ohne M4.6 nicht echt prüfbar.
- Zeit: fünf Nächte, nicht eine. Jeder Block ist für sich lauffähig und liefert Master etwas
  Sichtbares.

## 10. Offene Fragen (nur wenn die Prüfung sie nicht auflöst)
- Bleiben dauerhaft zwei Oberflächen (HUD und `api/web`), oder wird das HUD-Frontend in M8 auch
  die PWA und `api/web` entfällt? (Blockiert Block A nicht.)
- Soll das HUD den Sprachclient selbst starten/beenden können? (Vorschlag: nein, erst M3.2 stabil.)
- Farbwelt: klassisch Cyan/Orange oder rot-gold wie Mark III? (Vorschlag: Cyan, Theme tauschbar.)

## 11. Prüfergebnis
Runde 1 (Opus): NACHBESSERN, 5 hohe, 8 mittlere, 8 niedrige Funde. Alle eingearbeitet (Herkunft
über den Rust-Teil, eigenes Not-Aus-Kürzel, Pegel nur vom Sprachclient und gedrosselt, M3.9 an
M4.6/M4.8, vollständige Abhängigkeitsliste, `/v1/tiles`, Bus-Schichtung, Token im Speicher, ADR-024,
CI-Details). Runde 2 (Opus): NACHBESSERN (knapp), keine hohen Funde mehr, 4 mittlere, 6 niedrige. Alle
eingearbeitet (Transportschicht mit zwei Umsetzungen, vollständige Cargo-Liste, `/v1/events`
statt neuem Endpunkt, `/v1/tiles` über denselben Weg wie Anfragen, `status.py`, Pegel ohne Audit,
CSP `ipc:`, Overlay-Umschalter, Kürzel-Fehlermeldung, Token-Hinweis). Zwei Runden sind
verbraucht, ein ausdrückliches "perfekt" gab es nicht: nach der Freigabe-Regel baue ich M3 daher
nicht von selbst, sondern frage Master in der Besprechung um sein Okay.

## 12. Entscheidungen von Master (21.11., Besprechung)
- Block A wird gebaut (Okay trotz fehlendem "perfekt": die verbleibenden Funde waren mittel/niedrig und sind eingearbeitet).
- Das HUD-Frontend wird später (M8) auch die Handy-App (PWA); `api/web` entfällt dann. Block A bleibt davon unberührt.
- Korrektur 21.11.: Das Repo ist laut GitHub privat (nicht öffentlich). Seit 21.11. startet GitHub keine CI-Jobs mehr (Zahlung fehlgeschlagen oder Ausgabenlimit erreicht); Entscheidung bei Master. Der Tauri-Job läuft nur bei Änderungen unter `hud/`, reine Doku-Commits lösen keine CI aus.

## 13. Bau Block A (21.11., Ausnahme-Arbeitstag)
- Gebaut: `jarvis_core/status.py`, Werkzeug-Ereignisse aus `gated_call`, `thinking`/`stop` aus
  `ChatService`, `snapshot` und Status in `/v1/events`, `VoiceEvent` mit `state`/`mic`/`level`/
  `heartbeat`, Sprachclient meldet Zustand, Mikrofon und Lebenszeichen; `hud/` mit Oberfläche,
  Transportschicht (fetch), Tests, CI-Job `hud`, ADR-024, `docs/ANLEITUNG-HUD.md`.
- Abweichungen: `hud/.npmrc` mit `legacy-peer-deps` (npm-10-Fehler, ADR-024); keine Schriftdatei
  (Bahnschrift ist unter Windows eingebaut); Hardware-Profil noch nicht im `snapshot` (erst M3.10);
  der Sprachclient meldet `level` noch nicht (kommt mit M3.4); `vite.config.ts` ist von `tsc`
  ausgenommen (bräuchte Node-Typen), ESLint prüft sie.
- Prüfrunde Block A (Opus, 21.11.): 1 hoher, 3 mittlere, 8 niedrige Funde, alle behoben. Hoch: der
  Dev-Proxy entfernte die Herkunft bedingungslos, damit hätte jede Webseite über ihn den Not-Aus
  auslösen und Anfragen stellen können; jetzt Herkunftsprüfung vor dem Proxy, `cors` aus, am
  laufenden System mit 403 belegt. Mittel: `thinking` bei überlappenden Anfragen (jetzt Menge
  aktiver Läufe), HUD-Zustand nach Neuverbindung (Snapshot ersetzt alles), Meldeschleife des
  Sprachclients (je Art nur die neueste Meldung, Frist 2 s).

## 14. Bau Block B (21.11., Ausnahme-Arbeitstag)
- Gebaut: `hud/src-tauri` (Rust: Befehle `events_start`, `chat_start`, `chat_cancel`, `confirm`,
  `stop`, `set_token`, `shortcut_problems`, `overlay_set`; Tray; Kürzel; Overlay; Autostart),
  Tauri-Transport im Frontend (`src/api/tauri.ts`), Workflow `hud-tauri.yml` (Windows, nur bei
  Änderungen unter `hud/`), Anleitung, ADR-024.
- Abweichungen: `tokio` direkt mit Feature `time` (steckt über tauri ohnehin im Baum, nur für das
  Warten beim Wiederverbinden); die Oberfläche fragt noch nicht nach einem Token (localhost ohne
  Token ist Standard).
- Geprüft: `cargo test` (6), `cargo clippy -D warnings`, `npx tauri build --no-bundle` unter Linux,
  App unter Xvfb gegen einen Test-Core gestartet: Verbindung, Status, Rückfrage, Strg+Alt+Q löst den
  Not-Aus aus (Audit-Eintrag). Windows-Bau und Overlay-Durchsichtigkeit nur am PC.

## 15. Bau Block C (21.11., Ausnahme-Arbeitstag)
- Gebaut: Pegel im Sprachclient (`audio.level`, `_report_level`, je Quelle gedrosselt),
  Pegel und `screen_read` im HUD-Zustand, `Visualizer` (Canvas, ohne Pegel still),
  `BootSequence`, Töne per Web Audio (`sound.ts`, keine Dateien), Einstellungen (`settings.ts`,
  `SettingsMenu`), Augensymbol.
- Abweichungen: Töne werden erzeugt statt als Dateien mitgeliefert (keine Lizenzfrage); der Pegel
  der Ausgabe wird je Satzstück gemessen (grob, wie geplant).

## 16. Prüfrunde Block B (Opus, 21.11.)
2 hohe, 5 mittlere, 7 niedrige Funde; behoben:
- Hoch: Strg+Alt+Q ist auf deutscher Tastatur AltGr+Q (@) und hätte jedes @ geschluckt. Neuer
  Standard Strg+Alt+N (Strg+Alt+F12 schied aus: Intel-Grafiksteuerung unter Windows, Konsolenwechsel
  unter X11, beim Test unter Xvfb aufgefallen); das Kürzel verlangt Strg und Alt und darf keine AltGr-Taste sein.
  Derselbe Fehler im Sprachclient (M2): Stumm-Kürzel jetzt Strg+Alt+S statt Strg+Alt+M (µ);
  `valid_hotkey` und `VoiceConfig` lehnen AltGr-Tasten ab. Plan §5 war hier fehlerhaft.
- Hoch: Fenster schließen beendete die App samt Not-Aus-Kürzel; jetzt versteckt X nur.
- Mittel: doppelte CSP (Vite-Meta im Tauri-Bau entfällt, Test der Tauri-CSP), Neuladen der Seite
  (Strom wird ersetzt, zufällige Chat-Nummern, Overlay-Zustand nachgefragt), Token (toter Befehl
  entfernt, Grenze dokumentiert), Autostart nur in der installierten App, falsche Adresse wird
  angezeigt statt still zu beenden, Lese-Frist 45 s.
- Niedrig: Prüfen und Eintragen der Chats unter einer Sperre, keine Umleitungen, Overlay holt ein
  minimiertes Fenster zurück, CI mit Rechte- und Zeitgrenze, Anleitung (PowerShell, X versteckt,
  Grenzen).
- Abweichung vom Plan: kein eigenes Kürzel für den Klick-Durchlass; das Overlay schaltet ihn mit
  ein und aus (Strg+Alt+O), das genügt zum Hinein- und Herauskommen.

## 17. Bau Block D (21.11., Ausnahme-Arbeitstag)
- Gebaut: `agent/tiles.py` (`TileService`, `events_tile`, `tasks_tile`, `system_tile`),
  `McpHub.read_tool`/`has_read_tool`, `ChatService.idle_turn`, `GET /v1/tiles`, `psutil==7.2.2`;
  HUD: `tiles.ts`, `situation.ts`, `TileGrid`, Abruf je Minute; Tauri-Befehl `tiles`.
- Abweichungen: Termine und Aufgaben höchstens alle 5 Minuten statt 60 s (Audit, iCloud); die
  Kachel "Fokus" zeigt "keine Daten" (ADR-016 kennt kein aktives Projekt).
- Geprüft: 8 Core-Tests (Kalender-Attrappe, echter Notizen-Server, Sperre, Not-Aus, Zwischenspeicher),
  HUD-Tests für Lesen, Regeln und Anzeige, Bildschirmfoto mit echten Kacheln im Browser.

## 18. Bau Block E (21.11., Ausnahme-Arbeitstag)
- Gebaut: `Hologram.tsx` (three.js, nachgeladen), `hologram.ts` (Regeln, Drehen, Farben), Einstellung
  3D-Ansicht (automatisch/an/aus), Profil im Snapshot (Core, einmal beim Start im Hintergrund).
- Abhängigkeiten: `three@0.187.0` (im Plan), dazu `@types/three@0.187.0` (Typen, Nachtrag).
- Geprüft: HUD-Tests (Regeln, Ziehen, Rückfall ohne WebGL), Bildschirmfoto in Chromium mit
  Software-WebGL nach Ziehen.

## 19. Prüfrunde Block C/D (Opus, 21.11.)
1 hoher, 7 mittlere, 7 niedrige Funde; behoben:
- Hoch: Der Kachel-Abruf hielt die Anfrage-Sperre und startete alle Werkzeug-Server; eine
  Sprachfrage wartete still mit (bei hängendem iCloud Minuten). Jetzt: Anfragen haben Vorrang und
  brechen den Abruf ab, je Werkzeug 10 s, nur die nötigen Server.
- Mittel: `/v1/tiles` antwortet sofort aus dem Zwischenspeicher; Termine mit Uhrzeit vor
  ganztägigen (sonst verdrängt ein Geburtstag den Zahnarzt); Ausgabepegel über die ganze
  Satzdauer im Takt der Wiedergabe; Not-Aus während der Startanimation sichtbar und Escape
  überspringt; kein HUD-Ton beim Zuhören (Echo ins Mikrofon); im Overlay nur die Termin-Kachel;
  Missionen nur mit Fälligkeit oder Erinnerung (keine Merkzettel auf dem Bildschirm).
- Niedrig: Töne bei Autoplay-Sperre verwerfen; Pegelkranz zeichnet nur bei frischem Pegel;
  Zeitüberschreitung als "timeout" im Audit; Systemkachel übersteht kaputte Sensoren; Termine ohne
  Ende gelten eine Stunde; nach Abbruch erst nach 60 s neu laden, Fehler ins Log.
- Dokumentierte Abweichungen: Kacheln nutzen `read_tool` statt `gated_call` (kein Modell, keine
  Statusmeldung je Abruf); sie warten nie auf die Sperre; "Termin gleich" gilt 15 statt 10 Minuten
  vorher.
