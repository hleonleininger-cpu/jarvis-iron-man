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
