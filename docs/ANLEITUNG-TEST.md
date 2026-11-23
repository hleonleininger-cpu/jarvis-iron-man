# Testanleitung M0 bis M3

Für deinen Windows-PC. Plane 1 bis 2 Stunden ein; du musst nicht alles an einem Stück machen.
Jeder Teil endet mit "Was du mir schickst". Schick mir Fehler einfach in den Chat, am besten mit
der Nummer des Schritts (z. B. "T3.4 geht nicht: ...") und der Fehlermeldung. Keine Inhalte aus
Kalender oder Notizen nötig.

Alle Befehle in der **Eingabeaufforderung (cmd)** im Ordner `core` des Repos, wenn nichts anderes
dasteht.

## T0 Vorbereitung (einmalig, ca. 10 Minuten)

1. Neuesten Stand holen (im Repo-Ordner):

```bash
git fetch origin
```

```bash
git checkout claude/gallant-carson-tp966a
```

```bash
git pull
```

2. In den Ordner `core` wechseln und die Pakete aktualisieren:

```bash
cd core
```

```bash
uv sync
```

3. Ollama muss laufen (Symbol unten rechts). Falls `jarvis.toml` fehlt: `uv run jarvis init`.
4. Prüfen:

```bash
uv run jarvis doctor
```

Erwartet: Hardware, Profil (S/M/L), Ollama erreichbar, Modell vorhanden. Fehlt das Modell, zeigt
`doctor` den `ollama pull ...`-Befehl.

**Was du mir schickst:** die Ausgabe von `jarvis doctor`, falls etwas rot ist.

## T1 Grundlagen (M0, ca. 15 Minuten)

1. Server starten (Fenster offen lassen):

```bash
uv run jarvis serve --open
```

   Der Browser öffnet die schlichte Web-Oberfläche.
2. Frage stellen: "Wie wird das Wetter morgen?" Erwartet: Antwort mit Werkzeug "get_weather".
3. Rückfrage: "Trag mir morgen um 10 Uhr Zahnarzt ein." Erwartet: Karte mit Vorschau und Ja/Nein.
   Einmal **Nein** drücken (es wird nichts angelegt). Wenn du iCloud nicht eingerichtet hast,
   kommt eine Fehlermeldung; das ist dann richtig.
4. **Not-Aus:** eine längere Frage stellen ("Erklär mir ausführlich Photosynthese") und sofort den
   roten Knopf oder Strg+. drücken. Erwartet: Antwort bricht ab, Hinweis "Not-Aus".
5. Not-Aus von außen: in einem zweiten Fenster (Ordner `core`):

```bash
uv run jarvis stop
```

6. Autostart (optional):

```bash
uv run jarvis autostart an
```

   Abmelden und wieder anmelden: JARVIS startet von selbst. Wieder aus mit
   `uv run jarvis autostart aus`.

**Was du mir schickst:** welcher Schritt nicht wie beschrieben lief.

## T2 Gehirn und Gedächtnis (M1, ca. 15 Minuten)

Im Browser oder mit `uv run jarvis chat` (Dialog, `/ende` beendet):

1. "Merk dir, dass ich Handball spiele." Danach Server neu starten (Strg+C, wieder
   `uv run jarvis serve --open`) und fragen: "Was weißt du über mich?" Erwartet: Handball.
2. "Vergiss das mit dem Handball." Erwartet: Rückfrage mit Vorschau, nach Ja ist es weg.
3. Persona: JARVIS spricht dich mit "Master" an und sagt dazu, ob etwas live, gespeichert oder
   seine Einschätzung ist.
4. Planer: `/plan Erstelle mir einen Lernplan für Mathe für drei Tage` zeigt Schritte und eine
   Selbstprüfung.
5. Simulation: `/simulation Was passiert, wenn ich jeden Tag eine Stunde mehr lerne?` (nur
   lesende Werkzeuge, Ergebnis als Einschätzung gekennzeichnet).

**Was du mir schickst:** ob die Antworten brauchbar sind (kleine lokale Modelle sind hier die
größte Unbekannte) und wie lange eine Antwort ungefähr dauert.

## T3 Stimme (M2, ca. 30 Minuten)

Server aus T1 muss laufen. Ein zweites Fenster im Ordner `core`:

1. Einmalig Pakete und Modelle (einige Hundert MB, kostenlos):

```bash
uv sync --extra voice
```

```bash
uv run jarvis voice setup
```

2. Prüfen (jede Zeile soll mit OK beginnen):

```bash
uv run jarvis voice doctor
```

   Falsches Mikrofon oder Lautsprecher: in `jarvis.toml` unter `[voice]` `mic_device = "..."`
   bzw. `speaker_device = "..."` mit einem Teil des Gerätenamens eintragen.
3. Antwortzeit messen:

```bash
uv run jarvis voice bench
```

4. In `jarvis.toml` unter `[voice]` `enabled = true` setzen, dann:

```bash
uv run jarvis voice run
```

   - "Hey Jarvis" sagen, Ton abwarten, fragen ("Wie spät ist es?"). Danach ohne Wake Word
     nachfragen ("Und morgen?"), solange das Mikrofon noch offen ist.
   - "Stopp" beendet das Sprechen. "Not-Aus" löst den Not-Aus des Cores aus.
   - "Gute Nacht, Jarvis" schließt das Mikrofon. Wecken mit **Strg+Alt+J**.
5. Tastenkürzel (laufen, solange `voice run` läuft):
   - **Strg+Alt+J** sofort zuhören, **Strg+Alt+S** Mikrofon zu/auf (neu, vorher Strg+Alt+M),
     **Strg+Alt+X** Sprechen unterbrechen, **Strg+Alt+D** Diktat an/aus.
   - Gegenprobe: in einem Textfeld `@`, `€` und `µ` tippen (AltGr+Q, AltGr+E, AltGr+M). Es darf
     nichts von JARVIS auslösen.
6. Diktat: Notepad öffnen, Strg+Alt+D, einen Satz sprechen. Erwartet: der Text erscheint in
   Notepad, ohne Enter. "Diktat Ende" beendet es; ein Fensterwechsel auch.
7. Optional Stimmerkennung (nur deine Stimme zählt):

```bash
uv run jarvis voice setup --nur speaker_titanet
```

```bash
uv run jarvis voice anlernen
```

   Dann `speaker_check = true` in `[voice]`, `voice run` neu starten. Eine andere Person sagt "Hey
   Jarvis, wie spät ist es?": JARVIS soll nicht antworten.

**Was du mir schickst:** die Zahlen aus `voice bench`; wie zuverlässig "Hey Jarvis" reagiert; ob
Deutsch und Englisch richtig erkannt werden; wie die Stimme klingt; ob die Stimmerkennung dich
zuverlässig annimmt und andere ablehnt.

## T4 HUD im Browser (M3, ca. 15 Minuten)

Server aus T1 läuft, am besten auch `voice run` aus T3.

1. Einmalig Node.js 22 LTS von nodejs.org installieren. Dann ein neues Fenster im Ordner `hud`
   (nicht `core`):

```bash
npm ci --ignore-scripts
```

```bash
npm run dev
```

2. Im Browser `http://127.0.0.1:5173` öffnen. Erwartet: kurze Startanimation, dann
   "Core verbunden".
3. Ring: "Hey Jarvis" sagen. Der Ring zeigt HÖRT ZU (grün), dann DENKT, dann SPRICHT, dazu
   Balken um den Ring. Darunter "Mikrofon: an". Sprachclient beenden: nach etwa 12 Sekunden steht
   "Mikrofon: unbekannt".
4. Aktivität: im Feld "Gespräch" "Wie wird das Wetter?" fragen. Rechts erscheint "get_weather",
   erst "läuft …", dann "fertig" mit Dauer.
5. Rückfrage: "Trag mir morgen 10 Uhr Zahnarzt ein." Orange Karte, Nein drücken.
6. Not-Aus: roter Knopf oder Strg+. während einer langen Antwort.
7. Kacheln unten: System (CPU, RAM), Termine, Missionen (nur Aufgaben mit Fälligkeit oder
   Erinnerung), Fokus ("Noch kein aktives Projekt festgelegt.").
8. Einstellungen (oben rechts): Töne, Lautstärke, 3D-Ansicht auf "an" stellen. Der Ring wird
   3D und lässt sich mit der Maus drehen.

**Was du mir schickst:** wie es aussieht und wirkt (Iron-Man-Gefühl ja/nein), ob die Töne passen,
was fehlt oder stört. Ein Bildschirmfoto hilft.

## T5 Desktop-App (M3, ca. 30 Minuten, davon viel Wartezeit)

1. Einmalig: Rust von rustup.rs (`rustup-init.exe`, Standardoptionen) und die Visual Studio
   Build Tools mit "Desktopentwicklung mit C++" (einige GB). Neues Fenster öffnen und prüfen:

```bash
cargo --version
```

2. Im Ordner `hud` (beim ersten Mal 5 bis 15 Minuten Kompilieren):

```bash
npm run tauri dev
```

3. Prüfen:
   - Fenster mit "Core verbunden", Ring-Symbol im Tray (unten rechts).
   - **Strg+Alt+N** löst den Not-Aus aus, auch wenn ein anderes Programm vorne ist.
   - **Strg+Alt+O** schaltet das Overlay ein: rahmenlos, durchsichtig, immer vorne, Klicks gehen
     durch. Noch einmal Strg+Alt+O schaltet es aus.
   - Das X schließt nur das Fenster; JARVIS HUD läuft im Tray weiter. Zurück über Tray, "HUD
     zeigen". Ganz beenden: Tray, "Beenden".
   - Gegenprobe: in einem Textfeld `@` tippen (AltGr+Q). Es darf keinen Not-Aus auslösen.
4. Optional einen Installer bauen (lädt beim ersten Mal das Werkzeug NSIS aus dem Netz):

```bash
npm run tauri build
```

   Der Installer liegt danach in `hud\src-tauri\target\release\bundle\nsis\`. Nur in der
   installierten App lässt sich im Tray "Mit Windows starten" einschalten.

**Was du mir schickst:** ob der Bau durchläuft (falls nicht: die letzten 30 Zeilen der Ausgabe),
ob Overlay und Kürzel wie beschrieben wirken.

## Wenn etwas hängt

- Alles stoppen: `uv run jarvis stop` (im Ordner `core`).
- HUD zeigt "Der Core ist nicht erreichbar": `jarvis serve` läuft nicht.
- HUD zeigt "Der Core verlangt ein Zugangstoken": in `jarvis.toml` steht
  `require_api_token = true`; für den Test entfernen (auf diesem Rechner ist kein Token nötig).
- Ausführliche Anleitungen: `docs/ANLEITUNG-STIMME.md`, `docs/ANLEITUNG-HUD.md`, `README.md`.

## Danach

Für jeden Teil, der bei dir läuft, setzt du in `docs/CHECKLISTE.md` die Nummern auf `[x]` (oder
sagst mir, welche, dann mache ich das). Fehler behebe ich vor allem anderen.
