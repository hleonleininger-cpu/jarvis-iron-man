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
   Das gibt die Zeiten je Stufe aus und schätzt die Zeit bis zum ersten Ton. Ziel auf einem Rechner
   ohne Grafikkarte: höchstens 6 Sekunden. Bitte schick mir die Zahlen (nur Zeiten, keine Inhalte).
5. **Ohne Mikrofon ausprobieren:** `uv run jarvis voice text`
   Eingetippte Zeilen gelten als gesprochen; so siehst du, wie kurz JARVIS für die Stimme antwortet.
6. **Mit Mikrofon:** In `jarvis.toml` einschalten (`[voice]` mit `enabled = true`), dann
   `uv run jarvis voice run`.
   - "Hey Jarvis" sagen: ein kurzer Ton, dann sprechen. Nach der Antwort bleibt das Mikrofon
     8 Sekunden offen (Folgefrage ohne Wake Word).
   - "Stopp" beendet das Sprechen. "Not-Aus" löst den Not-Aus des ganzen Cores aus.
   - "Gute Nacht, Jarvis" schließt das Mikrofon ganz. "Flüstere" macht Antworten leise und kurz,
     "Normal sprechen" stellt es zurück.
   - Strg+C beendet den Sprachclient.
7. **Mit Kopfhörern** kannst du in `[voice]` `barge_in = "voice"` setzen: dann unterbricht deine
   Stimme JARVIS mitten im Satz. Mit Lautsprechern bleibt es bei `key` (Tastenkürzel folgt in Block C).

**Was ich von dir wissen will:** Reagiert das Wake Word zuverlässig? Erkennt er dich richtig
(Deutsch und Englisch)? Klingt die Stimme brauchbar? Wie lang ist die Pause bis zur Antwort
(`bench`-Zahlen)? Was nervt?

**Bekannte Grenzen:** Das Wake-Word-Modell ist ein Standardmodell und kann bei Akzent oder Lärm
schwächeln (`wake_threshold` in `[voice]` einstellbar). Ohne Grafikkarte dauert die Erkennung
und die Antwort des Modells einige Sekunden. Die Satzende-Erkennung kann bei langen Denkpausen zu
früh abschneiden.
