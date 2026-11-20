# Anleitung: Sprachsteuerung ausprobieren (M2, Block B)

Alle Befehle im Ordner `core` auf deinem Windows-PC, in der Eingabeaufforderung. JARVIS-Server
(`uv run jarvis serve`) und Ollama müssen laufen, wenn JARVIS antworten soll.

1. **Pakete installieren (einmalig, einige Hundert MB):**
   `uv sync --extra voice`
2. **Modelle laden (einmalig, ca. 290 MB, kostenlos):**
   `uv run jarvis voice setup`
   Es zeigt vorher, was geladen wird, und fragt nach. Danach sind alle Dateien per Prüfsumme geprüft.
3. **Alles prüfen:** `uv run jarvis voice doctor`
   Du siehst Hardware, Pakete, Mikrofone und Lautsprecher, Modelle und eine Funktionsprobe
   (JARVIS spricht einen Satz und erkennt ihn selbst wieder). Jede Zeile soll mit OK beginnen.
   Steht bei "Eingabegerät" etwas Falsches, trage in `jarvis.toml` unter `[voice]`
   `mic_device = "Teil des Gerätenamens"` und `speaker_device = "..."` ein.
4. **Antwortzeit messen:** `uv run jarvis voice bench`
   Das gibt die Zeiten je Stufe aus und schätzt die Zeit bis zum ersten Ton. Dabei stellt es dem
   Core einmal die Frage "Sag kurz Hallo." (neue Sitzung, sie bleibt nur im Arbeitsspeicher). Ziel auf einem Rechner
   ohne Grafikkarte: höchstens 6 Sekunden. Bitte schick mir die Zahlen (nur Zeiten, keine Inhalte).
5. **Ohne Mikrofon ausprobieren:** `uv run jarvis voice text`
   Eingetippte Zeilen gelten als gesprochen; so siehst du, wie kurz JARVIS für die Stimme antwortet.
6. **Mit Mikrofon:** In `jarvis.toml` einschalten (`[voice]` mit `enabled = true`), dann
   `uv run jarvis voice run`.
   - "Hey Jarvis" sagen: ein kurzer Ton, dann sprechen. Nach der Antwort bleibt das Mikrofon
     8 Sekunden offen (Folgefrage ohne Wake Word).
   - "Stopp" beendet das Sprechen. "Not-Aus" löst den Not-Aus des ganzen Cores aus.
   - "Gute Nacht, Jarvis" schließt das Mikrofon ganz. Aufwecken geht erst mit dem Tastenkürzel
     (kommt in Block C); bis dahin hilft nur Strg+C und `uv run jarvis voice run` neu starten. "Flüstere" macht Antworten leise und kurz,
     "Normal sprechen" stellt es zurück.
   - Strg+C beendet den Sprachclient.
7. **Mit Kopfhörern** kannst du in `[voice]` `barge_in = "voice"` setzen: dann unterbricht deine
   Stimme JARVIS mitten im Satz. Mit Lautsprechern bleibt es bei `key`: Unterbrechen per Tastenkürzel kommt in Block C; bis dahin
hilft "Stopp", sobald das Mikrofon zuhört.
8. **Andere Stimme:** Dateien im Piper-Format (`.onnx` und `.onnx.json`) in den Modellordner legen und
   in `[voice]` unter `tts_voice_en` bzw. `tts_voice_de` den Dateinamen ohne `.onnx` eintragen.
   Fehlt die Datei, sagt JARVIS es mit dem Hinweis auf `jarvis voice setup`.

## Block C: Tastenkürzel, Klatschen, Diktat, Stimmerkennung, Gespräch davor

- **Tastenkürzel** (laufen, solange `jarvis voice run` läuft; ändern in `[voice]`, leer = aus):
  Strg+Alt+J = sofort zuhören (wie das Wake Word, weckt auch aus dem Schlaf), Strg+Alt+M =
  Mikrofon ganz schließen oder wieder öffnen, Strg+Alt+X = Sprechen und laufende Anfrage beenden,
  Strg+Alt+D = Diktat an/aus. Auf dem iPhone gibt es keine Kürzel; das kommt mit M8.
- **Doppelklatschen** (`clap_wake = true`, `clap_threshold` 0,05 bis 0,95): zweimal kurz
  klatschen weckt wie das Wake Word. Es spricht auch auf andere kurze Knalle an, deshalb ist es aus.
  Im Schlafmodus ist das Mikrofon zu: dort weckt nur Strg+Alt+J.
- **Diktat:** "Diktiermodus an" (oder Strg+Alt+D). Das Gesprochene wird als Text in das gerade aktive
  Fenster getippt, ohne Zwischenablage und ohne Enter, Tab oder Steuerzeichen. Es endet mit
  "Diktat Ende", Strg+Alt+D, Fensterwechsel (nur unter Windows erkannt), "Gute Nacht" oder nach `dictation_idle_seconds` (60 s)
  Stille. Der Core protokolliert nur Beginn und Ende, nie den Text. Die Anzeige zeigt DIKTAT.
- **Stimmerkennung** (nur deine Stimme zählt): 1. `uv run jarvis voice setup --nur speaker_titanet`
  (40 MB), 2. `uv run jarvis voice anlernen` (fünf Sätze vorlesen; gespeichert wird nur ein Vektor,
  kein Audio), 3. in `[voice]` `speaker_check = true`. Die Ausgabe nennt, wie ähnlich deine
  Aufnahmen dem Profil sind; `speaker_threshold` (Standard 0,50) sollte darunter liegen. Zu kurze
  Äußerungen (unter 0,5 s) werden abgelehnt. `uv run jarvis voice vergessen` löscht das Profil.
  Bestätigen per Stimme (`confirm_by_voice = true`) geht nur mit eingeschalteter Sprecherprüfung,
  nur für anlegen/ändern, die sich rückgängig machen lassen, und nur mit der Phrase "Ja,
  bestätigt". Das ist keine Sicherheit gegen Aufnahmen oder gute Nachahmer.
- **Gespräch vor dem Wake Word** (`pre_roll_seconds`, z. B. 8): die letzten Sekunden vor "Hey
  Jarvis" bleiben nur im Arbeitsspeicher und gehen in die Erkennung ("Wie spät ist es ... Hey
  Jarvis"). Standard ist 0 (aus), weil das Mikrofon dann ständig mithört. Nichts davon wird
  gespeichert; im Schlaf und nach der Nutzung wird der Puffer geleert.

**Was ich von dir wissen will:** Reagiert das Wake Word zuverlässig? Erkennt er dich richtig
(Deutsch und Englisch)? Klingt die Stimme brauchbar? Wie lang ist die Pause bis zur Antwort
(`bench`-Zahlen)? Was nervt?

**Bekannte Grenzen:** Das Wake-Word-Modell ist ein Standardmodell und kann bei Akzent oder Lärm
schwächeln (`wake_threshold` in `[voice]` einstellbar). Ohne Grafikkarte dauert die Erkennung
und die Antwort des Modells einige Sekunden. Die Satzende-Erkennung kann bei langen Denkpausen zu
früh abschneiden. Die Stimmerkennung ist mit synthetischen Stimmen geprüft, nicht mit echten
Menschen: bitte teste, ob sie dich zuverlässig erkennt (zu viele Ablehnungen: Schwelle senken).
