# Stimmen, Modelle und Lizenzen der Sprachfunktion (M2)

Alles läuft lokal und kostenlos. Die Modelle lädt nur `jarvis voice setup` herunter (Größen und
SHA-256-Prüfsummen stehen in `core/src/jarvis_voice/models.toml`). Keine Nachbildung einer echten
bekannten Stimme (Masterauftrag §7).

| Teil | Modell / Paket | Lizenz | Hinweis |
|---|---|---|---|
| Wake Word | openWakeWord, Modell `hey_jarvis` v0.1 | Code Apache-2.0, **Modelle CC BY-NC-SA 4.0** | nur privater, nichtkommerzieller Gebrauch |
| Sprache/Stille | Silero VAD (ONNX) | MIT | |
| Satzende | Pipecat Smart Turn v3.2 (ONNX, CPU) | BSD-2-Clause | Merkmale über faster-whisper, ohne PyTorch |
| Spracherkennung | faster-whisper `base` (Systran) | MIT | Deutsch und Englisch automatisch |
| Sprachausgabe | Piper (`piper-tts`) | **GPL-3.0** | nur lokaler Gebrauch; der JARVIS-Code wird nicht mit Piper weitergegeben |
| Stimme Englisch | `en_GB-northern_english_male-medium` | CC BY-SA 4.0 | britischer Sprecher, kein "Butler" im engeren Sinn; Namensnennung hier |
| Stimme Deutsch | `de_DE-thorsten-medium` | CC0 | |
| Sprecherkennung (Block C) | sherpa-onnx | Apache-2.0 | Lizenz des Sprecher-Modells wird vor dem Einbau geprüft |

Nicht gewählt: die Stimmen `alan` und `jenny_dioco` (Lizenz ungeprüft). Eine andere freie Stimme
lässt sich in `jarvis.toml` (`tts_voice_en`, `tts_voice_de`) eintragen, wenn die Datei in `data/voice/`
liegt.
