# ADR-023: Sprachclient

**Status:** akzeptiert (Block A; Engines und Zusätze folgen). Plan: `docs/PLAN-M2.md`.

## Kontext
M2 (Stimme) soll "Hey Jarvis" freihändig, mit Unterbrechen und Folgefragen bieten, lokal und
ohne Kosten. Der Core ist ein Server; Mikrofon und Lautsprecher gehören auf den PC des Nutzers.

## Entscheidung
- **Dünner Client:** Das Paket `jarvis_voice` (Befehl `jarvis voice ...`) spricht nur über die
  HTTP-Schnittstelle mit dem Core (`/v1/chat`, `/v1/confirm`, `/v1/stop`). Die Core-Adresse muss
  wie `ollama_base_url` auf diesen Rechner, das Heimnetz oder das Tailnet zeigen.
- **Austauschbare Teile:** Wake Word, Spracherkennung, Sprachausgabe, VAD, Satzende, Sprecher,
  Audio-Ein-/Ausgabe sind Protokolle (`protocols.py`). Die Gesprächslogik (`session.py`) kennt nur
  diese und ist mit Attrappen vollständig testbar; echte Engines kommen als Adapter.
- **Optionale Pakete:** Die schweren Sprachpakete stehen in der Gruppe `voice`
  (`uv sync --extra voice`), nicht im Kern. Die Windows-CI prüft, dass sie installierbar sind.
- **Kanal `voice`:** `/v1/chat` nimmt `voice: true`. Dann gilt der Audit-Kanal `voice`, und der
  Systemprompt verlangt kurze, vorlesbare Antworten. Der Kanal `voice` ist von der Cloud-Stufe
  ausgeschlossen (fremdes Audio kann Befehle tragen).
- **Rückfragen:** Das Ereignis `confirm` trägt die Risikoklasse. Eine Zusage mit `via: "voice"`
  nimmt der Core nur bei `WRITE_REVERSIBLE` an (sonst 403); ein Nein ist über jeden Weg erlaubt.
  Der Client bestätigt per Stimme nur mit eingeschaltetem `confirm_by_voice`, laufender
  Sprecherprüfung und der festen Phrase "Ja, bestätigt" im Zeitfenster. Alles andere bleibt auf dem
  Bildschirm oder am Handy offen: offene Rückfragen kommen über `/v1/events` an alle verbundenen
  Clients (neue Ereignisse `confirm` und `confirm_done`).
- **Mikrofon sichtbar und abschaltbar:** Der Client zeigt jeden Zustand (Konsole), im Schlafmodus
  ("Gute Nacht") wird der Mikrofon-Strom geschlossen, nicht nur ignoriert; nur das Tastenkürzel
  weckt. "Stopp" und der Not-Aus des Cores beenden das Sprechen.
- **Datenschutz:** Aufnahmen bleiben im Arbeitsspeicher, nichts davon wird auf die Platte
  geschrieben. Audit und Logs enthalten nie Transkripte, nur Zustände und Zeiten. Der Diktiermodus
  (Block C) meldet nur Start und Ende an `POST /v1/voice/event`.
- **Unterbrechen:** Es beendet nur das Sprechen und die eigene Anfrage (Strom schließen), nie den
  Not-Aus des Cores; den löst nur die Phrase "Not-Aus" aus. Standard ist nur das Tastenkürzel;
  "Stopp" wirkt, sobald das Mikrofon zuhört. Unterbrechen per Stimme
  (`barge_in = "voice"`) ist für Kopfhörer gedacht; der Echo-Schutz (Stufe 1, halbduplex) verlangt,
  dass Sprache deutlich lauter ist als die eigene Wiedergabe. Echte Echounterdrückung ist Folgearbeit.
- **Kein Pipecat (Entscheidung von Master, 19.11.):** Silero-VAD und Smart Turn (M2.4) laufen direkt
  mit onnxruntime, ohne Pipecat und ohne PyTorch; Pipecat ist ein Cloud-Pipeline-Framework mit
  vielen Pflichtpaketen. Bis die Engine gebaut ist, erkennt ein einfacher Pegel-Erkenner das
  Satzende (`SilenceTurnDetector`); er ersetzt M2.4 nicht.

## Folgen
- Der Core ändert sich nur im Rahmen dieser Datei (Kanal, Risikoklasse, `/v1/events`); ohne
  Sprachclient verhält er sich wie bisher.
- Lizenzen der Engines (Piper GPL-3, openWakeWord-Modelle CC BY-NC-SA, Stimmen) werden mit Block B
  in diesem ADR ergänzt; nur privater, lokaler Gebrauch.

## Ergänzung Block B (Engines)
- **Modelle:** `models.toml` nennt URL, Größe und SHA-256 je Datei (am 19.11.2026 aus den
  Originalquellen gemessen). `jarvis voice setup` ist der einzige Netzzugriff; die Engines bekommen nur
  lokale Pfade (`HF_HUB_OFFLINE=1`, `local_files_only=True`). Eine geänderte Datei am Ursprung wird
  verworfen, nicht still übernommen.
- **Engines:** openWakeWord (nur Windows, ONNX), faster-whisper, Piper, Silero-VAD, Smart Turn v3.2,
  alle über onnxruntime/CTranslate2 ohne PyTorch. Sie sitzen hinter den Protokollen; ein Austausch
  berührt die Gesprächslogik nicht.
- **Lizenzen:** Siehe `docs/STIMMEN.md`. Piper ist GPL-3 (nur lokal, der Code wird nicht
  weitergegeben); die Wake-Word-Modelle sind CC BY-NC-SA (privat).
- **Messung:** `jarvis voice bench` misst nur Zeiten. Ziel auf dem CPU-Profil: höchstens 6 Sekunden
  vom Satzende bis zum ersten Ton.

## Ergänzung Block C (Sprecher, Diktat, Kürzel, Gespräch davor)
- **Sprecherprüfung (M2.9):** TitaNet small über sherpa-onnx, Kosinus-Vergleich mit dem angelernten
  Profil. Gespeichert wird nur der normierte Vektor in `data/voice/sprecher.json` (Rechte nur für den
  Benutzer), nie Audio; `jarvis voice vergessen` löscht ihn. Mit eingeschalteter Prüfung ignoriert
  der Client andere Stimmen für Befehle, Antworten, Bestätigungen und das Diktat; zu kurze Äußerungen
  werden abgelehnt. Keine Sicherheitsgarantie gegen Aufnahmen oder Nachahmer.
- **Gespräch vor dem Wake Word (M2.10):** Ein Ringpuffer hält höchstens `pre_roll_seconds` Audio
  im Arbeitsspeicher, nur im Zustand WARTET. Er wird bei Treffer, im Schlaf und beim Beenden geleert und nie
  geschrieben. Standard aus.
- **Diktat (M2.12):** Nur Tastenanschläge (pynput), nur druckbare Zeichen, keine Zwischenablage, nie
  Enter/Tab/Steuerzeichen. Es endet bei "Diktat Ende", Kürzel, Fensterwechsel (Windows, ctypes), Schlaf,
  Unterbrechen oder Ruhe. Der Core bekommt nur `dictation_start` und `dictation_end` (`POST
  /v1/voice/event`) und schreibt sie ohne Text ins Audit. Der Text geht nie zum Core.
- **Kürzel und Klatschen (M2.13):** Globale Kürzel über pynput (Standard Strg+Alt+J/M/X/D, änderbar,
  leer = aus). Doppelklatschen ist reine Signalverarbeitung, standardmäßig aus und im Schlafmodus
  wirkungslos, weil das Mikrofon dort zu ist; das Kürzel weckt.
