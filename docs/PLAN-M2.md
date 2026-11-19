# Plan M2 – Stimme

Stand: 18.11.2026, Entwurf der Nachtschicht. Maßgeblich: `docs/MASTERAUFTRAG.md`, `docs/CHECKLISTE.md`
(Abschnitt M2, 13 Nummern), `docs/STAND.md`. Freigabe-Regel von Master (18.11.): Plan schreiben, aus
Masters Sicht prüfen lassen, bei "perfekt" ohne Freigabe bauen. Prüfergebnis: siehe Ende der Datei.

## 1. Ziel und Maßstab
"Hey Jarvis" freihändig sprechen, JARVIS antwortet mit Stimme, lässt sich unterbrechen, versteht
Folgefragen. Alles lokal und kostenlos. Auf dem kleinsten Profil (S, nur CPU) nutzbar, auf Profil
M/L schneller und besser. Antwortzeit wird gemessen und im Bericht genannt (Masterauftrag §9).
Gebaut und mit Attrappen getestet wird auf dem Linux-Server; Mikrofon, Lautsprecher, Wake-Word-
Güte und Latenz kann nur Master an seinem PC prüfen (dafür gibt es `jarvis voice doctor` und eine
Anleitung).

## 2. Architektur (Bestand erweitern, nichts neu schreiben)
- **Dünner Client:** neues Paket `core/src/jarvis_voice/`. Es spricht nur über die vorhandene
  HTTP-Schnittstelle mit dem Core (`/v1/chat` als SSE-Strom, `/v1/confirm/{id}`, `/v1/stop`). Der
  Core bleibt unverändert lokal; Mikrofon und Lautsprecher liegen im Client-Prozess
  (`jarvis voice`), nie im Server.
- **Optionale Abhängigkeiten:** Die schweren Sprachpakete stehen in einer eigenen Gruppe
  (`uv sync --extra voice`), nicht in den Standardabhängigkeiten. CI (Linux und Windows) und der
  Server installieren sie nicht; alle Tests laufen mit Attrappen. Die Adapter importieren die
  echten Bibliotheken erst bei Gebrauch und melden fehlende Pakete verständlich.
- **Austauschbare Teile (Protokolle):** `AudioSource`, `AudioSink`, `WakeWordDetector`,
  `VoiceActivity` (Sprache ja/nein), `TurnDetector` (Ende des Satzes), `SpeechToText`,
  `TextToSpeech`, `SpeakerGate`. Die Gesprächslogik (`VoiceSession`, Zustandsautomat) kennt nur
  diese Protokolle und ist deshalb vollständig mit Attrappen testbar.
- **Abweichung vom Zielbild, begründet:** Die Checkliste nennt Pipecat (M2.4). Die Recherche
  (18.11.) zeigt: Pipecat ist ein Async-Framework für Cloud-Pipelines mit vielen Pflicht-
  abhängigkeiten (openai, Pillow, aiohttp, PyAudio ohne gesichertes Py3.12-Windows-Wheel, das
  Smart-Turn-Extra zieht torch und transformers) und passt schlecht zu "nur lokal" (ADR-003) und
  zu kleinen Rechnern. Stattdessen: eigene schlanke Schleife mit `sounddevice` und ONNX-Runtime
  (Silero-VAD und Smart Turn laufen direkt als ONNX, ohne PyTorch). Die Modelle (Silero VAD,
  Smart Turn, openWakeWord, faster-whisper, Piper) sind dieselben, die Checkliste verlangt; nur
  das Rahmenwerk entfällt. Weil die Protokolle sauber getrennt sind, kann ein Pipecat-Adapter
  später ergänzt werden. Das Abweichen wird als ADR-023 festgehalten.
- **Core-Änderung (klein):** `/v1/chat` bekommt das optionale Feld `voice: true`. Dann nutzt der
  Core den Kanal `voice` im Audit und hängt dem Systemprompt eine Sprech-Regel an (kurze Sätze,
  keine Listen/Markdown/URLs, Zahlen und Uhrzeiten ausgesprochen, höchstens drei Sätze, bei
  mehr Inhalt anbieten, es aufzuschreiben). Zusätzlich liefert der Kanal `voice` keine
  Bestätigungen automatisch (siehe §4).
- **Config** `[voice]` in `jarvis.toml` (alles aus, bis eingeschaltet): `enabled`, `wake_word`,
  `language` (auto/de/en), `stt_model`, `tts_voice_de`, `tts_voice_en`, `confirm_by_voice`
  (Standard false), `follow_up_seconds` (8), `models_dir`, Geräte-Namen.

## 3. Nummern, Blöcke, Reihenfolge
**Block A – Gesprächskern (komplett testbar auf Linux, Attrappen)**
- Paket, Protokolle, `VoiceSession`-Zustandsautomat (SCHLAF, HORCHT_AUF_WAKE, ZUHÖREN, DENKEN,
  SPRECHEN, FOLGEFRAGE), Core-Client (SSE, Stopp), Satzzerlegung fürs Sprechen (der Text des
  Core wird satzweise an die Sprachausgabe gegeben, sobald ein Satz fertig ist), Core-Änderung
  `voice`, Config, CLI `jarvis voice` (Grundgerüst), Statusanzeige.
- **M2.4** Sprechpausen: Silero-VAD (Sprache/Stille) plus Smart-Turn (Satz zu Ende?) als
  Protokoll `TurnDetector`; Attrappen-Tests mit künstlichen Audiosignalen.
- **M2.5** Unterbrechen: Ist JARVIS am Sprechen und die VAD hört Master (nicht das Echo), stoppt
  die Ausgabe sofort (Sink wird geleert), die laufende Core-Anfrage wird mit `/v1/stop` beendet
  und Master wird gehört.
- **M2.6** Folgefragen: Nach der Antwort bleibt das Mikrofon `follow_up_seconds` offen, ohne Wake
  Word; danach zurück zu HORCHT_AUF_WAKE. Gleiche `session_id` für den Gesprächszusammenhang.
- **M2.7** Deutsch/Englisch automatisch: Spracherkennung von Whisper je Äußerung, die
  Antwortsprache folgt der erkannten Sprache; Auswahl der Piper-Stimme je Sprache.
- **M2.8** Echo-Schutz (Stufe 1, robust): Halbduplex. Während JARVIS spricht, wird das Wake Word
  ignoriert und die Aufnahme nur für die Unterbrechungs-Erkennung genutzt, die ein gemessenes
  Echo-Profil (Ausgabepegel gegen Eingang, Verzögerung) abzieht bzw. nur bei deutlich lauterer
  Sprache auslöst. Echte Echounterdrückung (AEC) ist als Folgearbeit vermerkt und nur mit
  Masters Geräten (Lautsprecher/Kopfhörer) sinnvoll prüfbar: Kopfhörer-Betrieb ist der
  empfohlene Weg für Barge-in. Ehrlich im Bericht: bei Lautsprechern kann das Unterbrechen
  unzuverlässig sein.
- **M2.11** Schlafmodus ("Gute Nacht, Jarvis": Mikrofon wird ganz geschlossen, nur Tastenkürzel
  weckt) und Flüstermodus ("Flüstere": leise, kurze Antworten, empfindlichere Aufnahme).
- Sprachbefehle des Kerns (feste Phrasen, ohne Modell): "Stopp"/"Not-Aus" (ruft `/v1/stop`),
  "Gute Nacht", "Flüstern an/aus", "Ja"/"Nein" bei Rückfragen.

**Block B – echte Engines für Windows (Adapter; Hardware nur bei Master prüfbar)**
- **M2.1** Wake Word: openWakeWord, vorgefertigtes Modell "hey jarvis", ONNX-Betrieb.
- **M2.2** Spracheingabe: faster-whisper. Profil S (CPU): `small`, int8, `language=None`;
  Profil M: `medium` oder `large-v3-turbo` int8 auf CPU; Profil L (NVIDIA): `large-v3-turbo`
  float16. Modellwahl über `jarvis doctor`-Profil, einstellbar.
- **M2.3** Sprachausgabe: Piper (`piper-tts`, GPL-3, nur lokaler Gebrauch). Standardstimmen:
  Englisch `en_GB-northern_english_male` (britischer Butler-Ton, Lizenz CC-BY-SA 4.0), Deutsch
  `de_DE-thorsten` (CC0). Keine Nachbildung einer echten bekannten Stimme (Masterauftrag §7).
  Namensnennung der Stimmen in `docs/STIMMEN.md`.
- Echte Audio-Ein-/Ausgabe über `sounddevice`, ONNX-Adapter für Silero und Smart Turn.
- **`jarvis voice setup`:** lädt die Modelle ausdrücklich und sichtbar herunter (Größe vorher
  anzeigen, 0 €, Hugging Face und GitHub), prüft SHA-256 und legt sie in `data/voice/`. Das ist
  der einzige Netzzugriff der Sprachfunktion; im Betrieb spricht der Client nur mit dem Core.
- **`jarvis voice doctor`:** listet Audiogeräte, prüft Pakete und Modelle, macht einen
  Mikrofon-Pegeltest und einen Sprechtest.
- **`jarvis voice bench`:** misst die Antwortzeit je Stufe (Wake, Erkennung, Core bis zum ersten
  Satz, erste Audioausgabe) mit Zeitstempeln, ohne Inhalte, und gibt Mittelwerte aus. Diese Zahlen
  gehören in den Bericht (§9).

**Block C – Zusätze (je klein, Sicherheit zuerst)**
- **M2.9** Stimmerkennung (nur Master): `sherpa-onnx` (ONNX, ohne PyTorch) mit einem
  Sprecher-Embedding-Modell. `jarvis voice anlernen` nimmt wenige Sätze auf und speichert nur
  den Vektor (kein Audio) lokal in `data/voice/sprecher.json`; `jarvis voice vergessen` löscht
  ihn. Mit Sprecherprüfung an, ignoriert JARVIS andere Stimmen. Schwelle einstellbar. Ehrlich:
  keine Sicherheitsgarantie gegen Aufnahmen/Nachahmer.
- **M2.10** Wake Word mitten im Satz: Ringpuffer der letzten 10 Sekunden nur im Arbeitsspeicher
  (nie auf Platte, nie im Log); wird das Wake Word erkannt, geht der Satz davor mit in die
  Erkennung. Nur lokal, nur wenige Sekunden.
- **M2.12** Diktiermodus: "Diktiermodus an" (oder Tastenkürzel) schreibt das Gesprochene als
  Text in das gerade aktive Fenster (pynput-Tastaturanschläge bzw. Zwischenablage), sichtbar
  angezeigt, mit "Diktat Ende" oder demselben Kürzel beendbar. Läuft ganz ohne Core-Anfrage.
- **M2.13** Start per Tastenkürzel (Standard Strg+Alt+J) oder Doppelklatschen (reine
  Signalverarbeitung mit numpy: zwei Pegelspitzen im Abstand von 0,15 bis 0,5 s).

Reihenfolge: A (Nacht 1 bis 2), B (Nacht 3), C (Nacht 4). Je Block: Tests, ruff, pyright, CI
Linux und Windows, Prüfrunde (Opus-Untersession, höchstens eine je Block), Anleitung,
`[~]` in der Checkliste, STAND. Die Nummern M2.1, M2.2, M2.3 bleiben `[~]` mit dem Vermerk
"nur am PC prüfbar".

## 4. Sicherheit und Datenschutz (Masterauftrag §6)
- **Mikrofon sichtbar aktiv und sofort abschaltbar:** Der Client zeigt in der Konsole jederzeit
  den Zustand (SCHLAF / WARTET / HÖRT ZU / DENKT / SPRICHT) und gibt beim Aufwachen und bei
  Aufnahmebeginn einen kurzen Ton. Abschalten: "Gute Nacht" (Mikrofon-Stream wird geschlossen,
  nicht nur ignoriert), Tastenkürzel Strg+Alt+M (harte Stummschaltung), `jarvis voice stop`,
  und der Not-Aus des Core (`/v1/stop`) beendet auch das Sprechen.
- **Kein Audio auf der Platte:** Aufnahmen bleiben im Arbeitsspeicher und werden nach der
  Erkennung verworfen. Im Log und im Audit stehen nie Gesprächsinhalte oder Transkripte, nur
  Zustände und Zeiten.
- **Bestätigungen per Stimme:** Der Core fragt bei Riskantem nach (WRITE_REVERSIBLE usw.). Der
  Client liest die Vorschau vor. Per Stimme ("Ja") wird nur bestätigt, wenn `confirm_by_voice`
  ausdrücklich eingeschaltet ist UND die Sprecherprüfung (M2.9) an ist. Sonst bleibt die Frage
  offen und erscheint in der Web-Oberfläche (auch am Handy); nach dem Zeitlimit gilt Nein.
- **Nur localhost/Tailnet:** Der Client verbindet sich nur mit der konfigurierten Core-Adresse
  (gleiche Prüfung wie `ollama_base_url`) und nutzt das API-Token aus dem Schlüsselbund. Modelle
  werden nur bei `jarvis voice setup` geladen.
- **Sprecher-Vektor:** biometrisches Datum, nur lokal, löschbar, nie im Log.
- **Diktat und Tastenkürzel:** Das Diktat tippt nur, wenn Master es ausdrücklich eingeschaltet
  hat und zeigt das an; es führt nie Befehle aus. Globale Kürzel nur die drei genannten.
- **Lizenzen:** Piper ist GPL-3 (nur lokale Nutzung, der Code wird nicht weitergegeben),
  openWakeWord-Modelle CC BY-NC-SA (privater Gebrauch). Beides in ADR-023 festgehalten.

## 5. Neue Abhängigkeiten (Gruppe `voice`, nur auf Masters PC)
`sounddevice`, `numpy`, `onnxruntime`, `openwakeword`, `faster-whisper`, `piper-tts`,
`sherpa-onnx`, `pynput`. Kein PyTorch, kein Pipecat. Alle kostenlos. Die Kernabhängigkeiten
bleiben unverändert (CI-Zeit, Server).

## 6. Dateien
Neu: `core/src/jarvis_voice/{__init__,protocols,session,core_client,sentences,commands,config,
indicator,setup,doctor,bench,cli}.py`, `jarvis_voice/engines/{wake_openwakeword,stt_faster_whisper,
tts_piper,vad_silero,turn_smart,speaker_sherpa,audio_sounddevice,hotkey,clap}.py`,
`core/tests/test_voice_*.py`, `docs/adr/ADR-023-sprachclient.md`, `docs/STIMMEN.md`.
Geändert: `api/app.py` (Feld `voice`), `agent/service.py` (Sprech-Regel, Kanal `voice`),
`config/settings.py` (`[voice]`), `cli/main.py` (Befehl `voice`), `pyproject.toml` (Gruppe
`voice`), `jarvis.example.toml`, `docs/CHECKLISTE.md`, `docs/STAND.md`.

## 7. Tests (kein Mikrofon nötig)
Zustandsautomat mit Attrappen (Folgefrage, Zeitablauf, Unterbrechen, Schlaf, Flüstern,
Not-Aus-Phrase); künstliche Audiosignale für VAD-Schwellen, Doppelklatsch, Echo-Pegel;
Satzzerlegung; Core-Client gegen eine Test-API (SSE, Stopp, Bestätigung); Sprach-Wahl de/en;
Bestätigung per Stimme nur mit Sprecherprüfung; Datenschutz (kein Audio auf Platte, keine
Inhalte im Log/Audit); fehlende Pakete geben klare Meldungen; Konfigurationsprüfung (Core-Adresse
nur lokal). Echte Engines: nur Import- und Konfigurationstests, ein `live`-Test, der sich ohne
Pakete überspringt.

## 8. Risiken (offen genannt)
- Wake-Word-Trefferquote, Erkennungsgüte, Sprachqualität und Latenz sind nur an Masters PC
  messbar. Die Zahlen liefert `jarvis voice bench`.
- Barge-in mit Lautsprechern ohne echte Echounterdrückung kann unzuverlässig sein; Kopfhörer
  empfohlen. AEC ist Folgearbeit, falls nötig.
- openWakeWord ruht seit 2024 (Version 0.6.0); das vorgefertigte "hey jarvis" ist ein Standard-
  modell und kann bei Akzent/Rauschen schwächeln. Ausweichen: Schwelle einstellbar.
- Whisper auf CPU (Profil S) braucht für `small` je Äußerung grob 1 bis 3 s; auf GPU
  deutlich weniger. Die GPU wird zugleich vom Chat-Modell genutzt (VRAM-Konkurrenz auf L).
- Piper-Stimmen: Lizenz von `alan` und `jenny_dioco` ungeprüft, deshalb `northern_english_male`.
- Wochenlimit: Block A ist die größte Arbeit; wird es knapp, wird auf mehr Nächte gestreckt.

## 9. Was ich von Master brauche (erst zum Testen, nicht zum Bauen)
Windows-PC mit Mikrofon und Lautsprecher/Kopfhörer; `uv sync --extra voice`; danach
`jarvis voice setup`, `jarvis voice doctor`, `jarvis voice bench`. Die genaue Anleitung kommt mit
Block B.

---

## 10. Prüfrunde 1 (Opus, aus Masters Sicht) und Änderungen am Plan
Urteil der Prüfung: NICHT BAUEN, solange die Pipecat-Frage offen ist. Alle anderen Funde waren im
Plan behebbar und sind hier eingearbeitet. **Die Änderungen dieses Abschnitts gelten vor dem Rest
der Datei.**

**Offene Entscheidung für Master (Pipecat, M2.4).** Die Checkliste sagt "über Pipecat". Der
Masterauftrag verlangt bei Abweichungen Masters Entscheidung. Deshalb wird der Teil von M2.4, der die
Engine betrifft (Silero-VAD und Smart Turn), **nicht gebaut**, bis Master entschieden hat:
- Option 1 (Checkliste wörtlich): Pipecat-Analyzer für Silero-VAD und Smart Turn nutzen
  (`pipecat-ai`, nur diese Bausteine, nicht die ganze Pipeline). Preis: größere Installation
  (u. a. openai, Pillow, aiohttp), Pipecat-API ändert oft, PyAudio nicht nötig (Ton läuft über
  sounddevice).
- Option 2 (schlank): Silero-VAD und Smart Turn direkt als ONNX mit onnxruntime, kein Pipecat.
Alles andere in M2 hängt davon nicht ab: Die Gesprächslogik arbeitet gegen das Protokoll
`TurnDetector`; die Engine ist ein Adapter. M2.4 bleibt `[ ]`, bis entschieden und gebaut ist.
Empfehlung: Option 1 testen, falls `LocalSmartTurnAnalyzerV3` ohne torch läuft, sonst Option 2.

**Eingearbeitete Funde**
1. *Bestätigungen am Handy:* Heute geht das `confirm`-Ereignis nur an den Strom der anfragenden
   Sitzung. Core-Änderung in Block A: offene Rückfragen werden zusätzlich über `/v1/events` an alle
   verbundenen Clients gesendet, die Web-Oberfläche zeigt sie dort an; `/v1/confirm/{id}` bleibt
   die einzige Antwortstelle. Mit Tests.
2. *Diktiermodus (M2.12):* Start und Ende kommen ins Audit (ohne Inhalt). Keine Zwischenablage
   (Windows-Verlauf/Cloud-Sync), nur Tastenanschläge. Es werden nie Enter, Tab oder
   Steuerzeichen gesendet. Das Diktat endet, wenn das aktive Fenster wechselt. Es läuft nur nach
   ausdrücklichem Einschalten und mit sichtbarer Anzeige.
3. *Bestätigen per Stimme:* nur für `WRITE_REVERSIBLE`, nur mit eingeschaltetem
   `confirm_by_voice` UND Sprecherprüfung, nur die feste Phrase "Ja, bestätigt" innerhalb von
   10 Sekunden. `IRREVERSIBLE`, `EXTERNAL_COMMUNICATION` und alles Löschende/Senden werden nie per
   Stimme bestätigt, sondern nur auf dem Bildschirm oder am Handy.
4. *Kein versteckter Netzzugriff:* Der Client setzt `HF_HUB_OFFLINE=1`, die Engines bekommen nur
   lokale Dateipfade (keine Modellnamen zum Nachladen), `openwakeword.utils.download_models()` wird
   nur von `jarvis voice setup` aufgerufen. SHA-256-Werte stehen fest im Repo
   (`jarvis_voice/models.toml`). Test: im Betrieb schlägt jeder Socket-Aufruf nach außen fehl.
5. *Installierbarkeit:* `openwakeword` wird nur unter Windows installiert
   (`sys_platform == 'win32'`, dort nur onnxruntime statt tflite); `piper-tts>=1.3`. In Block A,
   Schritt 1, wird die Gruppe `voice` in der Windows-CI installiert und per Import-Test geprüft
   (`uv lock` und `uv sync --extra voice` müssen auf beiden Systemen laufen), bevor weiter
   gebaut wird.
6. *Paketierung:* `jarvis_voice` kommt in `packages` (hatch); die Gruppe ist
   `[project.optional-dependencies] voice` (damit `--extra voice` geht). `jarvis voice` im Core-CLI
   importiert `jarvis_voice` erst beim Aufruf (kein Import-Zyklus, Core läuft ohne Sprachpakete);
   zusätzlich ein eigener Einstieg `jarvis-voice`. Engine-Adapter bekommen
   `# pyright: reportMissingImports=false`, weil die Pakete auf Linux/CI nicht installiert sind.
7. *Umfang:* Block A und B liefern zuerst einen durchgehenden Weg Wake → Erkennung → Core →
   Piper, damit Master früh am PC testen kann. M2.9 (Sprecher), M2.10 (Ringpuffer) und M2.12
   (Diktat) bilden Block C und werden im Bericht vorher angekündigt.
8. *Latenz Profil S ehrlich:* Standard `base` (nicht `small`), Zielwert höchstens 6 Sekunden bis
   zum ersten Ton auf Profil S, auf Profil M/L deutlich weniger; `jarvis voice bench` misst es,
   der Bericht nennt die Zahlen und sagt offen, wenn der Zielwert verfehlt wird.
9. *Fremd-Audio:* Der Kanal `voice` kommt in `NO_CLOUD_CHANNELS` (keine Cloud-Anfrage per
   Sprache). Mit eingeschalteter Sprecherprüfung zählt die Folgefragen-Zeit nur für Masters Stimme.
10. *Barge-in:* Mit Lautsprechern ist Unterbrechen standardmäßig nur über "Stopp" oder das
    Tastenkürzel möglich; Unterbrechen per Stimme (`barge_in = "voice"`) ist für Kopfhörer
    gedacht und einstellbar. Doppelklatschen weckt nicht im Schlafmodus (Mikrofon ist dann zu);
    nur das Tastenkürzel weckt dort. Beides steht in der Anleitung.
11. *Stimmen/Lizenzen:* `northern_english_male` ist ein britischer Sprecher, nicht zwingend ein
    "Butler"; M2.3 bleibt `[~]`, Master hört die Probe und kann eine andere freie Stimme wählen.
    Lizenz des Sprecher-Modells für sherpa-onnx wird vor dem Bau geprüft und in ADR-023 genannt.
    Indirekte Pakete werden genannt: `scipy`, `scikit-learn`, `av`, `ctranslate2`.

**Bauumfang der heutigen Nacht (ohne die offene Pipecat-Entscheidung):** Block A ohne die
Engine von M2.4, danach Block B und C in den folgenden Nächten.

## 11. Prüfrunde 2 (Opus): Urteil "BAUEN" für Block A, mit vier Ergänzungen
Die elf Funde aus Runde 1 sind eingearbeitet; die Behandlung der Pipecat-Frage (Engine von M2.4
nicht bauen, Master vorlegen) ist mit Masterauftrag §4 vereinbar. Ergänzungen:
1. *Sprachbestätigung wird im Core durchgesetzt:* Das `confirm`-Ereignis trägt die Risikoklasse.
   Der Core nimmt eine Zusage aus dem Kanal `voice` nur bei `WRITE_REVERSIBLE` an und lehnt
   alles andere ab (Test); der Client verlässt sich nicht allein auf sich selbst.
2. *Diktat-Audit:* Ein kleiner Core-Endpunkt `POST /v1/voice/event` (nur Ereignisart
   "dictation_start"/"dictation_end" und Zeit, nie Inhalt) schreibt ins vorhandene Audit-Log. Kein
   zweites Audit-System im Client.
3. *Rückfrage erledigt:* `/v1/events` meldet auch "Rückfrage erledigt" (Antwort, Zeitablauf,
   Not-Aus), damit die Web-Oberfläche alte Fragen entfernt.
4. *Platzhalter für das Satzende:* Bis zur Pipecat-Entscheidung gibt es einen klar benannten
   `SilenceTurnDetector` (nur numpy, Satzende = Stille). Er zählt nicht als M2.4.

## 12. Prüfrunde 3 (Opus, Block A): Funde und Korrekturen
Behoben: Not-Aus von außen ließ die Sitzung auf DENKT hängen; in CONFIRM wurde jeder stille Frame zur
Äußerung; mehrfaches Auslösen des Unterbrechens; Rückfragen am Handy kamen nach einer Trennung nicht
mehr an (Web); fremde Stimmen öffneten ein neues Folgefragen-Fenster; Hotkey im Schlaf; Ausfälle der
Erkennung/Ausgabe; unbegrenzte Zeitmessungen.
**Geändert gegenüber Abschnitt 10.10:** "Unterbrechen" beendet nur das Sprechen und die eigene
Anfrage (der Strom wird geschlossen). Den Not-Aus des Cores, der alle Geräte trifft, löst nur die
Phrase "Not-Aus" aus. Mit Lautsprechern (`barge_in = "key"`) unterbricht das Tastenkürzel; die
Phrase "Stopp" wirkt, sobald das Mikrofon zuhört (nach dem Wake Word oder in der Folgefragen-Zeit).
Während JARVIS spricht, hört der Client bei `key` nichts mit (Echo-Schutz); wer per Stimme
unterbrechen will, nutzt Kopfhörer und `barge_in = "voice"`.

## 13. Entscheidungen von Master (19.11., Besprechung 7:15)
- **M2.4 schlank per ONNX, ohne Pipecat:** Silero-VAD und Smart Turn laufen direkt mit onnxruntime
  (kein PyTorch, kein Pipecat). Die Abweichung vom Wortlaut der Checkliste ist damit von Master
  entschieden und in ADR-023 festgehalten. Die Engine von M2.4 wird in Block B gebaut.
- **Hardware unbekannt:** Standard ist das CPU-Profil (Spracherkennung `base`, int8); `jarvis voice
  doctor` erkennt die Hardware am PC und schlägt bei NVIDIA-GPU das größere Modell vor.
- **Ausgabe je nach Situation:** `barge_in` bleibt umschaltbar, Standard Tastenkürzel; mit
  Kopfhörern empfohlen `voice`.
- **Test durch Master:** erst nach M2 (M0, M1 und M2 gemeinsam), sobald Block B steht.
