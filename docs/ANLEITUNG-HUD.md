# Anleitung: HUD ausprobieren (M3, Block A)

Das HUD läuft in diesem Schritt noch im Browser (die Desktop-App mit Tauri folgt in Block B).
Es zeigt den Zustand von JARVIS, laufende Werkzeuge, Rückfragen und hat einen Not-Aus-Knopf.

## Einmalig: Node.js installieren
1. Node.js 22 LTS von nodejs.org herunterladen und installieren (Standardoptionen genügen).
2. Neue Eingabeaufforderung öffnen und prüfen:

```bash
node --version
```

Es sollte `v22.` oder höher erscheinen.

## Starten
1. JARVIS wie gewohnt starten (in einer eigenen Eingabeaufforderung):

```bash
jarvis serve
```

2. In einer zweiten Eingabeaufforderung in den Ordner `hud` des Repos wechseln und einmalig die
   Pakete holen:

```bash
npm ci --ignore-scripts
```

3. Das HUD starten:

```bash
npm run dev
```

4. Im Browser `http://127.0.0.1:5173` öffnen. Oben links sollte "Core verbunden" stehen.

Optional, um den Sprachzustand zu sehen: in einer dritten Eingabeaufforderung `jarvis voice run`.
Der Ring zeigt dann "HÖRT ZU", "DENKT", "SPRICHT" usw., darunter, ob das Mikrofon offen ist.

## Was du prüfen kannst
- Eine Frage im Feld "Gespräch" stellen, die ein Werkzeug braucht ("Wie wird das Wetter?"). In
  "Aktivität" erscheint das Werkzeug mit "läuft …", danach "fertig" und die Dauer.
- Etwas anlegen lassen ("Trag mir morgen 10 Uhr Zahnarzt ein"). Es erscheint eine orange
  Rückfrage-Karte mit Vorschau; "Ja" oder "Nein".
- Not-Aus: roter Knopf oben rechts oder Strg+. bricht alles ab. Antwortet der Core gar nicht,
  zeigt das HUD den Hinweis auf `jarvis stop`.
- Sprachclient beenden, während das HUD offen ist: nach etwa 12 Sekunden steht beim Mikrofon
  "unbekannt".

## Wenn etwas nicht geht
- "Der Core ist nicht erreichbar": `jarvis serve` läuft nicht oder auf einem anderen Port. Ein
  anderer Port geht mit `set JARVIS_CORE_URL=http://127.0.0.1:PORT` vor `npm run dev`.
- "Der Core verlangt ein Zugangstoken": in `jarvis.toml` ist `require_api_token = true` gesetzt.
  Das HUD spricht in Block A nur mit localhost ohne Token.
- "Der Core lehnt die Herkunft ab": das HUD wurde nicht über `npm run dev` geöffnet.

# Desktop-App (M3, Block B)

Die Desktop-App ist dasselbe HUD als eigenes Fenster mit Tray-Symbol, systemweitem Not-Aus und
Overlay. Sie spricht nur mit JARVIS auf diesem Rechner.

## Einmalig: Werkzeuge installieren
1. Rust: von rustup.rs `rustup-init.exe` laden und mit den Standardoptionen ausführen.
2. Visual Studio Build Tools: beim Installer "Desktopentwicklung mit C++" anhaken (das sind ein
   paar Gigabyte, 30 bis 60 Minuten).
3. WebView2 ist unter Windows 11 schon da.
4. Neue Eingabeaufforderung öffnen und prüfen:

```bash
cargo --version
```

## Starten
1. JARVIS wie gewohnt starten (`jarvis serve`).
2. Im Ordner `hud`, falls noch nicht geschehen:

```bash
npm ci --ignore-scripts
```

3. Die App bauen und starten (beim ersten Mal dauert das Kompilieren 5 bis 15 Minuten):

```bash
npm run tauri dev
```

Für einen Installer statt des Entwicklungsmodus:

```bash
npm run tauri build
```

Der Installer liegt danach in `hud\src-tauri\target\release\bundle\nsis\`.

## Was du prüfen kannst
- Fenster mit "Core verbunden", Tray-Symbol unten rechts (Ring-Symbol).
- Strg+Alt+Q löst den Not-Aus aus, auch wenn ein anderes Programm vorne ist.
- Strg+Alt+O schaltet das Overlay ein: rahmenlos, durchsichtig, immer vorne, Klicks gehen durch.
  Noch einmal Strg+Alt+O (oder Tray: "Overlay an/aus") schaltet es aus.
- Tray: "Mit Windows starten" an und wieder aus (Standard ist aus).
- Belegt ein anderes Programm Strg+Alt+Q, steht ein Hinweis im Gespräch. Dann ein anderes Kürzel
  setzen, z. B. `set JARVIS_HUD_STOP_KEY=ctrl+alt+p` vor `npm run tauri dev`.
- JARVIS läuft auf einem anderen Port: `set JARVIS_CORE_URL=http://127.0.0.1:PORT`. Andere
  Rechner lehnt die App ab.
