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
  nötig; ist `require_api_token` gesetzt, gibt Master das Token beim Start ein und der Rust-Teil
  hält es nur im Arbeitsspeicher. Damit entfallen `keyring` und `toml` im Rust-Teil.
- **Reihenfolge der Dokumente:** ADR-024 "HUD" entsteht in Block A (Build-Kette nur in `hud/`,
  `api/web` bleibt Ausweichweg, Herkunft, CSP, Kürzel).
- Das HUD hat keine eigene Logik für Rechte. Rückfragen, Policy und Audit laufen wie bisher im Core.

## 3. Was dem Core fehlt (kleine Erweiterung)
Heute liefert `/v1/events` nur Erinnerungen und Rückfragen, `/v1/chat` einen Strom je Anfrage. Ein
HUD braucht einen Strom über alles, auch über Anfragen anderer Clients (zum Beispiel der Stimme).

- **Zuerst prüfen, ob `/v1/events` erweitert werden kann** (kleinerer Diff als ein neuer Endpunkt;
  Test, dass `api/web/app.js` unbekannte Ereignisse ignoriert). Nur wenn das nicht trägt: neuer
  Endpunkt `GET /v1/status` (SSE, mit Zugangstoken wie die übrigen). Er schickt beim Verbinden den
  aktuellen Stand (`snapshot`, inkl. Profil S/M/L), danach Ereignisse:
  `state` (Zustand des Sprachclients), `tool_start`/`tool_end` (Lauf-ID, Werkzeugname,
  Risikoklasse, Dauer, Ergebnis ok/Fehler/abgelehnt), `thinking` (Schrittzähler), `stop`
  (Not-Aus ausgelöst), `mic` (an/aus, dazu ein Lebenszeichen alle ca. 5 s; bleibt es aus, zeigt
  das HUD "unbekannt"), `level` (Pegel, siehe M3.4), `screen_read` (an/aus; Vertrag, den M4.6/M4.8
  später nur noch speisen).
- **Rückfragen bleiben auf dem vorhandenen Weg** (`ConfirmationBroker`, `/v1/events`, ADR-005):
  `confirm_open/confirm_done` werden nicht im Bus doppelt geführt. Der Statusstrom selbst bleibt
  inhaltsfrei; die Rückfrage-Karte bekommt `preview`/`question` weiterhin aus dem autorisierten
  Rückfrage-Weg.
- **Datenschutz:** der Strom enthält nie Nachrichtentext und nie Werkzeugergebnisse, nur Art, Name,
  Dauer. Das ist dieselbe Regel wie beim Audit. Den Text einer Unterhaltung zeigt nur der
  Chat-Bereich des Clients, der sie selbst geführt hat.
- **Quelle der Ereignisse:** Werkzeug-Ereignisse kommen nur aus `McpHub.gated_call` (nur dort sind
  Dauer, Ergebnis und "abgelehnt" bekannt). Damit die Schichten stimmen (`mcp_hub` darf nicht
  nach oben importieren), liegt der kleine Verteiler `StatusBus` neben `audit` und wird dem Hub
  als Beobachter injiziert. Der Sprachclient meldet `state`/`mic`/`level` über `/v1/voice/event`.
  Kein zweites State-System: der Bus hält nur den letzten Stand je Zustand im Arbeitsspeicher.
- **Sprachclient meldet Zustand:** `CoreClient.voice_event` kennt bisher nur Diktat-Ereignisse;
  er bekommt `state`, `mic` und `level` dazu. Zustandswechsel und Mikrofon an/aus kommen ins Audit
  (nur Ereignisart). Pegelwerte (höchstens 5-10 Hz, 4-8 Stufen) und Lebenszeichen gehen über
  einen eigenen Pfad ohne Audit je Wert, sonst flutete das Audit. Das HUD öffnet nie selbst ein
  Mikrofon.

## 4. Aufbau von `hud/`
```
hud/
  package.json, vite.config.ts, tsconfig.json   (Vite, React 18, TypeScript streng)
  src/            React-Oberfläche: Kacheln, Zustandsring, Aktivitätsliste, Rückfrage-Karte
  src/api/        Client für /v1/status, /v1/chat, /v1/confirm, /v1/stop (SSE selbst gelesen)
  src/theme/      Farben, Schrift, Animationen als CSS-Variablen
  src-tauri/      Rust-Teil: Fenster, Tray, globale Not-Aus-Taste, Overlay-Modus
```
Neue Abhängigkeiten (vollständige Liste; nichts anderes kommt ohne Rückfrage hinzu):

| Paket | Zweck | Nummer |
|---|---|---|
| npm: `react`, `react-dom` | Oberfläche | M3.1 |
| npm: `vite`, `@vitejs/plugin-react`, `typescript` | Bauen | M3.1 |
| npm: `vitest`, `jsdom`, `@testing-library/react` | Tests | M3.1 |
| npm: `eslint`, `typescript-eslint`, `eslint-plugin-react-hooks` | Lint (CI) | M3.1 |
| npm: `@tauri-apps/cli`, `@tauri-apps/api` | Desktop-App | M3.2 |
| npm: `three` | 3D | M3.10 |
| Cargo: `tauri` (Feature `tray-icon`), `tauri-build` | Fenster, Tray | M3.2 |
| Cargo: `tauri-plugin-global-shortcut` | Not-Aus-Kürzel, Overlay-Umschalter | M3.2/M3.5 |
| Cargo: `tauri-plugin-autostart` | Start mit Windows (Schalter) | M3.2 |
| Cargo: `reqwest` (nur localhost, rustls) | Core-Aufrufe aus dem Rust-Teil | M3.2 |
| Python: `psutil` (nur falls die `hardware`-Schicht CPU/RAM nicht schon liefert) | System-Kachel | M3.6 |

Das Plugin `shell` wird ausdrücklich nicht verwendet. Töne und Schrift: nur eigene oder frei
lizenzierte Dateien (CC0/OFL), keine Filmsounds. Versionen sind festgeschrieben
(`package-lock.json`, `Cargo.lock`), keine Wildcards.

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
- **M3.2 Tauri 2:** Fenster lädt das gebaute Frontend; Core-Aufrufe nur aus dem Rust-Teil (siehe 2).
  Keine Secrets im Repo oder in der App-Datei. Tray-Symbol, "beim Start von Windows starten"
  (wie beim Core, M0.9) als Schalter.
- **Systemweite Not-Aus-Taste:** eigenes, einstellbares Kürzel (Standard Strg+Alt+Q), geprüft gegen
  Kollision mit den Kürzeln des Sprachclients (J/M/X/D; Strg+Alt+X ist dort "unterbrechen", nicht
  Core-Not-Aus). Der Handler sitzt im Rust-Teil und schickt `POST /v1/stop` direkt, auch bei
  hängender Webview. Reagiert der Core nach 5 s nicht, zeigt das HUD den Hinweis auf `jarvis stop`
  (ohne `shell`-Plugin). Erfüllt den Rest von M0.5. Die HUD-Leiste zeigt Strg+Alt+M (Mikrofon aus)
  des Sprachclients an; das HUD selbst kann das Mikrofon nicht schalten.
- **M3.5 Transparentes Overlay:** Fenster ohne Rahmen, durchsichtig, immer im Vordergrund,
  Klicks gehen durch, Umschalten per Tray und Kürzel. Standard ist AUS (Fenstermodus). Im Overlay
  ist der rote Knopf nicht klickbar: Not-Aus und Verlassen gehen dort über Kürzel und Tray;
  optional schaltet das Halten von Strg den Klick-Durchlass kurz aus.
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
  das HUD ruft keine MCP-Werkzeuge selbst. System (CPU/RAM aus der `hardware`-Schicht, sonst
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
  (`connect-src 'none'`; Core-Aufrufe laufen im Rust-Teil, `connect-src` steht fest in der
  Konfiguration, nicht zur Laufzeit). Keine Fernladung, kein `eval`, kein `dangerouslySetInnerHTML`
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
CI-Details). Runde 2 steht aus.
