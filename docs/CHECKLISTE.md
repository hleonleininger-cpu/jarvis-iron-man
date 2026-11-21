# JARVIS – Gesamt-Checkliste (Stand 15.11.2026)

Markierungen:
[ ]  offen
[~]  gebaut und von Claude getestet, Master hat es noch nicht ausprobiert
[?]  unklar, im Code prüfen und dann auf [~] oder [ ] setzen
[x]  von Master ausprobiert und abgenommen (nur Master setzt das)

## M0 – Fundament
- [~] M0.1 Python-Core (FastAPI) startet, Web-Oberfläche, CLI und Werkzeug-Server laufen
- [~] M0.2 jarvis doctor erkennt die Hardware und wählt das Modellprofil S, M oder L (Beleg: core/src/jarvis_core/hardware/profile.py, core/tests/test_profile.py)
- [~] M0.3 Audit-Log ohne Inhalte
- [~] M0.4 Policy mit Bestätigungspflicht für Schreibendes, Bezahlen und Handeln gesperrt
- [~] M0.5 Not-Aus per Taste und per Befehl, stoppt alle laufenden Aktionen (roter Knopf und Strg+. in der Web-Oberfläche, `jarvis stop`, POST /v1/stop; beendet auch einen hängenden Server. Beleg: docs/adr/ADR-018-not-aus.md, core/tests/test_emergency_stop.py. Systemweite Taste: Strg+Alt+N in der Desktop-App (M3.2), einstellbar über JARVIS_HUD_STOP_KEY, wirkt auch bei hängender Oberfläche; belegtes Kürzel wird im HUD gemeldet)
- [~] M0.6 CLI-Chat mit lokalem Modell über Ollama
- [~] M0.7 Konfiguration und Secrets getrennt (OS-Schlüsselbund statt .env, nie im Repo; Entscheidung von Master am 15.11.; Beleg: core/src/jarvis_core/secrets/store.py, core/tests/test_secrets.py, .gitignore)
- [~] M0.8 Testgerüst: 644 Tests, ruff und pyright sauber, CI auf Linux und Windows
- [~] M0.9 Core startet automatisch mit Windows (`jarvis autostart an|aus|status`, Startdatei im Autostart-Ordner. Beleg: core/src/jarvis_core/cli/autostart.py, core/tests/test_autostart.py. Die Startdatei ist auf der Windows-CI ausgeführt worden, eine echte Anmeldung nicht)
- [~] M0.10 Ollama-Start mit OLLAMA_CONTEXT_LENGTH=8192 automatisch sicherstellen oder beim Start prüfen und warnen (gebaut: `jarvis serve` und `jarvis chat` prüfen beim Start und warnen. Beleg: core/tests/test_config.py. Ollama wird nicht von JARVIS gestartet)

## M1 – Gehirn und Gedächtnis
- [~] M1.1 Modell-Router: kleines Modell für Schnelles, großes für Schweres (gebaut: `model_fast`/`model_strong` in jarvis.toml, Regel in agent/router.py, Wahl als Stream-Ereignis und im Audit (nur Modellname). Ohne `model_fast` keine Änderung. Beleg: core/tests/test_router.py, ADR-020. Schwelle nur mit echtem Embedding-Modell prüfbar)
- [~] M1.2 Orchestrator mit Sub-Agenten für parallele Aufgaben (gebaut: `/team <Auftrag>`, Zerlegung, je Sub-Agent eigener Kontext und Werkzeug-Teilset, Kurzbericht, Zusammenfassung; mit einem Modell nacheinander, Parallelität über `subagent_parallel` vorbereitet und getestet. Beleg: agent/subagents.py, core/tests/test_subagents.py, ADR-022)
- [~] M1.3 Aufgabenplaner: zerlegt mehrstufige Aufträge, arbeitet sie ab, prüft das Ergebnis selbst (gebaut: `/plan <Auftrag>` in Chat, Web und CLI; Plan, Schritte und Selbstprüfung als Fortschritt im Stream; Not-Aus, Anfrage-Limit und Rückfragen je Schritt. Beleg: agent/planner.py, core/tests/test_planner.py, ADR-020. Wie gut kleine Modelle Pläne schreiben, nur mit echtem Modell prüfbar)
- [~] M1.4 Werkzeugauswahl per Embedding, damit viele Werkzeuge kleine Modelle nicht überfordern (gebaut: `tool_select_limit` in jarvis.toml, mcp_hub/select.py; Policy und Rückfrage unverändert, Ausweichweg auf alle Werkzeuge. Beleg: core/tests/test_tool_select.py, ADR-020)
- [~] M1.5 Kern läuft vollständig lokal
- [~] M1.6 Cloud-Stufe nur nach Freigabe pro Aufgabe und nie mit privaten Daten (bisher gibt es keine Cloud-Modelle) (gebaut: nur das Gerüst, abgeschaltet und ohne Anbieter; `/cloud <Frage>` nur nach Freigabe des genauen Textes, harte Sperre für private Daten durch den Aufbau (keine Werkzeuge, kein Verlauf, keine persönlichen Angaben), Audit ohne Inhalt. Beleg: agent/cloud.py, core/tests/test_cloud.py, ADR-021. Kein Anbieter und kein Schlüssel eingerichtet, kein echter Cloud-Aufruf getestet)
- [~] M1.7 Langzeitgedächtnis (SQLite + sqlite-vec + Markdown). Heute liegt der Gesprächsverlauf nur im Arbeitsspeicher (gebaut: SQLite-Speicher mit remember/recall/list_memories/forget, Suche, ab M1.8 semantisch mit sqlite-vec. Beleg: docs/adr/ADR-019-langzeitgedaechtnis.md, core/tests/test_memory_server.py)
- [~] M1.8 Semantische Suche über Gespräche, Notizen und Dateien (gebaut: Gedächtnis, Vault-Dateien und offene Notizen; Embeddings über Ollama, sqlite-vec mit Python-Ausweichweg, Fallback auf Stichwörter. Beleg: core/tests/test_memory_server.py, core/tests/test_memory_vault.py. Schwelle 0,5 nur mit echtem Modell prüfbar)
- [~] M1.9 Tagesjournal (journal_add/journal_read, eine Markdown-Datei je Tag. Beleg: core/tests/test_memory_server.py)
- [~] M1.10 Vault für Dokumente und Ressourcen (gebaut: `vault_index` für ausdrücklich erlaubte Ordner und offene Notizen, Änderungserkennung, kein Link nach draußen. Beleg: core/tests/test_memory_vault.py. PDF folgt mit M5.4)
- [~] M1.11 "Vergiss das" löscht zuverlässig und nachweisbar (forget nach Rückfrage, secure_delete: der Text steht danach nicht mehr in der Datei. Beleg: core/tests/test_memory_server.py)
- [~] M1.12 Gedächtnispflege: Veraltetes aufräumen, Dateien indexieren (`jarvis memory-pflege` und beim Start von `jarvis serve`: Dubletten, fehlende Embeddings, Vault/Notizen. Beleg: core/tests/test_memory_server.py, test_config.py)
- [~] M1.13 Persona: Anrede "Master", trockener Humor, höflicher Widerspruch, Warnung bei schlechten Ideen (gebaut: Systemprompt PERSONA in agent/service.py, Anrede einstellbar über `anrede` in jarvis.toml; Test tests/test_agent_tools.py; Wirkung mit echtem Modell nur von Master prüfbar; Eval persona-address) (gebaut: Systemprompt PERSONA in agent/service.py, Anrede einstellbar über `anrede` in jarvis.toml; Test tests/test_agent_tools.py; Wirkung mit echtem Modell nur von Master prüfbar; Eval persona-address)
- [~] M1.14 Optionen mit Einschätzung: bei Entscheidungen zwei bis drei Wege samt Risiko (Systemprompt, agent/service.py; Wirkung mit echtem Modell nur von Master prüfbar; Eval persona-options)
- [~] M1.15 Simulationen: "Was passiert, wenn …" für Lern- und Wochenpläne (gebaut: `/simulation <Frage>`, nur lesende Werkzeuge, Ergebnis als Einschätzung gekennzeichnet. Beleg: core/tests/test_planner.py, ADR-020)
- [~] M1.16 Ehrlichkeit: unterscheidet Live-Daten, Gespeichertes und eigene Schlüsse, erfindet nichts (gebaut: Systemprompt HONESTY trennt aktuelle Daten, 'gespeichert' und 'meine Einschätzung'; Wirkung mit echtem Modell nur von Master prüfbar; Eval honesty-unknown)

## M2 – Stimme
- [~] M2.1 Wake Word "Hey Jarvis", lokal (openWakeWord) (gebaut: Adapter für openWakeWord mit dem Modell hey_jarvis (ONNX), `wake_threshold` einstellbar; getestet mit echtem Modell auf Linux (lädt, rechnet, löst bei normaler Sprache nicht aus) und mit Attrappen; Trefferquote bei echter Stimme nur am PC prüfbar. Beleg: engines/wake_openwakeword.py, tests/test_voice_engines.py, docs/STIMMEN.md)
- [~] M2.2 Spracheingabe lokal (faster-whisper) (gebaut: faster-whisper base/int8, nur lokale Modellordner, Deutsch/Englisch automatisch; getestet mit echtem Modell: erkennt eigene Stimme in beiden Sprachen, ca. 0,4 x Echtzeit auf CPU; Mikrofon-Qualität nur am PC prüfbar. Beleg: engines/whisper.py, tests/test_voice_engines.py)
- [~] M2.3 Sprachausgabe lokal (Piper), britischer Butler-Ton, keine Kopie einer echten Stimme (gebaut: Piper mit en_GB-northern_english_male und de_DE-thorsten, Flüsterstufe, erstes Stück nach ca. 0,1 s; ob der Ton für dich passt, hörst nur du. Beleg: engines/piper_tts.py, docs/STIMMEN.md)
- [~] M2.4 Sprechpausen-Erkennung (Silero VAD, Smart Turn) über Pipecat (gebaut nach Masters Entscheidung vom 19.11. ohne Pipecat: Silero-VAD und Smart Turn v3.2 direkt als ONNX, ohne PyTorch; getestet mit echten Modellen (Sprache/Stille sauber getrennt, Satzende endet spätestens durch Stille); ob das Smart-Turn-Urteil bei echter Sprache stimmt, nur am PC prüfbar. Beleg: engines/silero.py, engines/smart_turn.py, ADR-023)
- [~] M2.5 Unterbrechen mitten im Satz (gebaut: Gesprächslogik im Paket jarvis_voice, getestet mit Attrappen in core/tests/test_voice_session.py; am echten Mikrofon erst mit Block B prüfbar. ADR-023)
- [~] M2.6 Folgefragen ohne erneutes Wake Word (gebaut: Gesprächslogik im Paket jarvis_voice, getestet mit Attrappen in core/tests/test_voice_session.py; am echten Mikrofon erst mit Block B prüfbar. ADR-023)
- [~] M2.7 Deutsch und Englisch automatisch (gebaut: Whisper erkennt die Sprache je Äußerung (nur de/en), die Antwort folgt in der erkannten Sprache mit passender Stimme; mit echten Modellen getestet. Beleg: engines/whisper.py, tests/test_voice_engines.py, test_voice_session.py)
- [~] M2.8 Echo-Schutz: reagiert nicht auf die eigene Stimme (gebaut: Stufe 1, halbduplex mit Pegelvergleich; echte Echounterdrückung ist Folgearbeit, bei Lautsprechern kann Unterbrechen per Stimme unzuverlässig sein; Beleg: test_voice_parts.py, test_voice_session.py; ADR-023)
- [~] M2.9 Stimmerkennung: hört nur auf Master (gebaut: TitaNet small über sherpa-onnx, `jarvis voice anlernen|vergessen`, nur der Vektor wird lokal gespeichert, Schwelle einstellbar; mit echtem Modell geprüft: trennt zwei synthetische Stimmen; echte Menschen und Mikrofon nur am PC prüfbar, keine Sicherheitsgarantie gegen Aufnahmen. Beleg: speaker.py, engines/speaker_sherpa.py, tests/test_voice_speaker.py, ADR-023)
- [~] M2.10 Wake Word mitten im Satz, versteht das kurze Gespräch davor (nur lokal, nur wenige Sekunden) (gebaut: Ringpuffer `pre_roll_seconds` nur im Arbeitsspeicher, standardmäßig aus; getestet mit Attrappen, am Mikrofon nur am PC prüfbar. Beleg: session.py, tests/test_voice_session.py)
- [~] M2.11 Schlafmodus ("Gute Nacht, Jarvis") und Flüstermodus (gebaut: Gesprächslogik im Paket jarvis_voice, getestet mit Attrappen in core/tests/test_voice_session.py; am echten Mikrofon erst mit Block B prüfbar. ADR-023)
- [~] M2.12 Diktiermodus in jedes Textfeld (gebaut: Diktat per Sprache oder Kürzel, nur Tastenanschläge ohne Zwischenablage und ohne Enter/Tab/Steuerzeichen, endet bei Fensterwechsel/Ruhe/Schlaf, Audit nur Beginn und Ende; getestet mit Attrappen, echtes Tippen in fremde Fenster nur am PC prüfbar. Beleg: session.py, engines/typer_pynput.py, tests/test_voice_session.py)
- [~] M2.13 Start per Tastenkürzel oder Doppelklatschen (gebaut: globale Kürzel (Strg+Alt+J/M/X/D) über pynput und Doppelklatschen als reine Signalverarbeitung; getestet mit Attrappen und künstlichen Signalen, echte Tastatur und echtes Klatschen nur am PC prüfbar. Beleg: hotkeys.py, clap.py, tests/test_voice_block_c.py)

## M3 – HUD
- [~] M3.1 Web-Oberfläche im Iron-Man-Stil (gebaut: hud/ mit Vite, React, TypeScript; Zustandsring, Aktivität, Gespräch, Rückfrage-Karte, Not-Aus; läuft im Browser über `npm run dev`, Desktop-App folgt mit M3.2. Status-Ereignisse inhaltsfrei in /v1/events. Beleg: hud/, core/src/jarvis_core/status.py, core/tests/test_status.py, hud/test/, docs/ANLEITUNG-HUD.md, ADR-024. Optik nur von Master beurteilbar)
- [~] M3.2 Desktop-App mit Tauri 2 (gebaut: hud/src-tauri; der Rust-Teil spricht nur mit localhost, die Webview hat keinen Netzzugang (CSP); Tray mit HUD zeigen, Overlay, Not-Aus, Autostart-Schalter (Standard aus), Beenden; systemweites Not-Aus Strg+Alt+N. Unter Linux gebaut und gestartet (Xvfb, Bildschirmfoto, Kürzel ausgelöst); unter Windows nur am PC prüfbar, solange die Windows-CI gesperrt ist. Beleg: hud/src-tauri/src/lib.rs, core.rs, sse.rs, cargo test, hud/test/tauri.test.ts, docs/ANLEITUNG-HUD.md)
- [~] M3.3 Boot-Sequenz und Sounds (gebaut: Startanimation bis zur Verbindung, höchstens 4 s, Klick überspringt; Töne per Web Audio ohne Dateien bei Verbindung, Zuhören, Rückfrage, Not-Aus, Fehler; Einstellungen: Startanimation, Töne, Lautstärke. Klang und Wirkung nur von Master beurteilbar. Beleg: hud/src/components/BootSequence.tsx, hud/src/sound.ts, hud/test/block-c.test.ts, hud/test/app.test.tsx)
- [~] M3.4 Audio-Visualizer beim Zuhören und Sprechen (gebaut: Balkenkranz um den Ring; der Sprachclient meldet nur eine Pegelstufe 0..7, höchstens 8-mal je Sekunde und je Quelle, nicht ins Audit; das HUD öffnet nie ein Mikrofon. Mit echtem Mikrofon nur am PC prüfbar. Beleg: core/src/jarvis_voice/audio.py level, session.py _report_level, core/tests/test_voice_session.py, hud/src/components/Visualizer.tsx)
- [~] M3.5 Transparentes Overlay über dem Desktop (gebaut: Strg+Alt+O oder Tray; rahmenlos, immer vorn, Klicks gehen durch, ohne Gesprächsfeld; Standard aus. Durchsichtigkeit nur am PC prüfbar, hier ohne Compositor nicht sichtbar. Beleg: hud/src-tauri/src/lib.rs set_overlay, hud/src/App.tsx, hud/src/theme/hud.css)
- [~] M3.6 Live-Kacheln: System, Termine, Fokus, Missionen (gebaut: GET /v1/tiles; System aus psutil (nur Zahlen), Termine und offene Aufgaben (nur mit Fälligkeit oder Erinnerung, keine Merkzettel) über die READ-Werkzeuge auf demselben Weg wie Anfragen (Anfrage-Sperre, Policy, Audit ohne Inhalte, Not-Aus; jede echte Anfrage hat Vorrang und bricht den Abruf ab), höchstens alle 5 Minuten, je Werkzeug höchstens 10 s; Fokus zeigt ehrlich "keine Daten", weil es noch kein aktives Projekt gibt. Beleg: core/src/jarvis_core/agent/tiles.py, hub.read_tool, core/tests/test_tiles.py, hud/src/components/TileGrid.tsx)
- [~] M3.7 Situatives HUD: Anzeigen erscheinen nur, wenn sie gebraucht werden (gebaut: Regeln als Liste in hud/src/situation.ts: Rückfrage vor laufender Arbeit vor Termin in 15 Minuten vor Ruhe; im Fenster wird das Wichtige hervorgehoben, im Overlay erscheint nur das Gebrauchte (laufende Werkzeuge oder die Termin-Kachel). Beleg: hud/test/block-d.test.ts, hud/test/app.test.tsx)
- [~] M3.8 Anzeige, was Jarvis gerade tut, mit Abbrechen-Knopf (gebaut: Aktivitätsliste aus tool_start/tool_end mit Risiko, Ergebnis, Dauer; Not-Aus-Knopf und Strg+.; Hinweis auf `jarvis stop`, wenn der Core nicht antwortet. Beleg: hud/src/components/ActivityList.tsx, hud/test/app.test.tsx, core/tests/test_status.py)
- [~] M3.9 Augensymbol, solange Jarvis den Bildschirm liest (gebaut: Augensymbol im Kopf bei Ereignis screen_read; vorbereitet, denn Bildschirmlesen selbst kommt erst mit M4.6/M4.8 und speist dann dieses Ereignis. Beleg: hud/src/App.tsx, core/src/jarvis_core/status.py, hud/test/app.test.tsx)
- [~] M3.10 Drehbare 3D-Ansichten als Hologramm-Ersatz (gebaut: 3D-Ring mit three.js, per Maus drehbar, Farbe und Tempo je Zustand; nur nachgeladen, wenn gebraucht; Einstellung automatisch/an/aus, automatisch erst ab Profil M (der Core meldet das Profil im Snapshot); ohne WebGL zurück zum flachen Ring. In Chromium mit Software-WebGL gezeigt; Wirkung auf Masters Grafik nur am PC prüfbar. Beleg: hud/src/components/Hologram.tsx, hud/src/hologram.ts, hud/test/block-e.test.tsx, core/tests/test_status.py)

## M4 – PC-Steuerung
- [ ] M4.1 Windows-Knoten über MCP
- [ ] M4.2 Apps öffnen und schließen
- [ ] M4.3 Dateien suchen, öffnen, sortieren (nur in erlaubten Ordnern)
- [ ] M4.4 Fenster, Lautstärke, Helligkeit, Zwischenablage
- [ ] M4.5 Screenshots
- [ ] M4.6 Bildschirm lesen und verstehen
- [ ] M4.7 Maus und Tastatur bedienen
- [ ] M4.8 Privatsphäre-Schalter: Bildschirmwahrnehmung sofort aus
- [ ] M4.9 Systemmonitor: CPU, RAM, Akku, Speicher, Netzwerk
- [ ] M4.10 Warnung bei vollem Speicher, heißer CPU, leerem Akku
- [ ] M4.11 Sperren, Energiesparen, Herunterfahren, Wecken
- [ ] M4.12 Volldiagnose auf Zuruf: alle Dienste, Modelle, Verbindungen
- [ ] M4.13 Timer, Wecker, Umrechnungen
- [ ] M4.14 Vormachen statt erklären: Ablauf aufzeichnen, Jarvis wiederholt ihn

## M5 – Web und Wissen
- [~] M5.1 Websuche (heute DuckDuckGo ohne Konto, nur der Suchbegriff geht raus). Umstellung auf SearXNG nur nach Masters Entscheidung
- [ ] M5.2 Tiefenrecherche mit Bericht und Quellen
- [ ] M5.3 YouTube-Videos zusammenfassen (Transkript, sonst Whisper)
- [ ] M5.4 PDFs und Dokumente lesen und zusammenfassen
- [ ] M5.5 Browser-Automation mit sichtbarem Browser (Playwright)
- [ ] M5.6 SSRF-Schutz für alle Webzugriffe (Bestand: kein allgemeiner Schutz; heutige Zugriffe gehen nur an feste Adressen bzw. sind im Kalender per same_site begrenzt. Nötig ab M5.2)
- [ ] M5.7 Tägliches News-Briefing nach Masters Themen
- [~] M5.8 Wetter: aktuell und nächste drei Tage, mit Wohnort-Voreinstellung
- [ ] M5.9 Website-Wächter: meldet Änderungen (Vertretungsplan, Preise, neue Uploads)

## M6 – Leben
- [~] M6.1 iCloud-Kalender: lesen (alle Kalender), anlegen, ändern, löschen, immer mit Vorschau und Rückfrage; Serientermine bleiben unangetastet. Ändern und Löschen noch nicht gegen echtes iCloud geprüft
- [~] M6.2 Notion lesen (bekannte Namen: Mission Control, Academy, Forge, Olympus, Vault; Titan Bible geht über NOTION_DB_<NAME>, ist aber ungetestet; mit Attrappe getestet sind Mission Control und Academy)
- [~] M6.3 Notion: neue Einträge anlegen mit Rückfrage. Noch nicht gegen echtes Notion geprüft, am besten mit einem Testeintrag
- [ ] M6.4 Mails lesen, sortieren, Entwürfe schreiben; Senden nur nach Bestätigung
- [ ] M6.5 Aufgabenverwaltung
- [ ] M6.6 Gewohnheiten und Routinen tracken
- [ ] M6.7 Befehle: Status, Now, Focus, Suit Up, Arc Reactor
- [ ] M6.8 Match Mode, Gym Mode, School Mode
- [ ] M6.9 Zielverfolgung: langfristige Ziele aus Olympus selbstständig vorantreiben
- [ ] M6.10 Nachrichten diktieren und als Entwurf vorbereiten
- [ ] M6.11 Musiksteuerung

## M7 – Proaktivität
- [ ] M7.1 Scheduler für geplante Routinen
- [ ] M7.2 Verpasste Läufe werden nachgeholt
- [ ] M7.3 Routinen dürfen den PC aus dem Energiesparmodus wecken
- [ ] M7.4 Heartbeat: prüft regelmäßig, ob etwas ansteht, und schweigt sonst; mit Ruhezeiten
- [ ] M7.5 Morning Briefing
- [ ] M7.6 Evening Review
- [ ] M7.7 Weekly, Monthly, Yearly
- [~] M7.8 Erinnerungen (melden sich zur Uhrzeit, solange JARVIS läuft). Countdown zu Klausuren und Spielen fehlt noch
- [ ] M7.9 Warnungen von sich aus: Termin-Kollision, Deadline, Wetter
- [ ] M7.10 Mustererkennung: schlägt Routinen aus Masters Verhalten vor
- [ ] M7.11 Anstupser bei Untätigkeit
- [ ] M7.12 Nachtschicht: Aufträge über Nacht, Bericht am Morgen

## M8 – iPhone und Kanäle
- [ ] M8.1 Web-Oberfläche als PWA auf dem Home-Bildschirm (Bestand: handytaugliches Layout, aber kein Manifest und kein Home-Bildschirm-Icon)
- [~] M8.2 Zugriff nur im Tailscale-Netz (Server bindet nur an localhost/private/Tailnet-Adressen, Token-Pflicht außerhalb von localhost, Host-Prüfung; Beleg: config/settings.py is_private_host, tests/test_web_and_token.py. Nicht mit echtem Tailscale geprüft)
- [ ] M8.3 Kurzbefehl auf der Aktionstaste als Sprechtaste
- [ ] M8.4 Push-Nachrichten
- [ ] M8.5 Orts- und Zeit-Auslöser über Kurzbefehle
- [ ] M8.6 Homescreen-Widget
- [ ] M8.7 Gleicher Gesprächskontext auf PC und iPhone
- [ ] M8.8 Messenger-Kanal, z. B. Telegram (optional, braucht Masters Freigabe)

## M9 – Academy
- [ ] M9.1 Lernzettel aus Masters Material
- [ ] M9.2 Abfragen per Stimme und Karteikarten
- [ ] M9.3 Klausurplan mit Lernphasen
- [ ] M9.4 Erklärmodus Schritt für Schritt
- [ ] M9.5 Tafelbilder und Arbeitsblätter abfotografieren und auswerten

## M10 – Forge
- [ ] M10.1 Git, Tests, Code-Review, Build-Status per Sprache
- [ ] M10.2 Jarvis schreibt sich neue Fähigkeiten selbst (mit Tests und Masters Freigabe)
- [ ] M10.3 Iron Legion: mehrere Agenten parallel, mehrere Geräte-Knoten (Windows, macOS)
- [ ] M10.4 Claude-Code-Sitzungen per Sprache starten (braucht ein Claude-Abo)

## M11 – Sehen
- [ ] M11.1 Webcam: Anwesenheit erkennen
- [ ] M11.2 Gestensteuerung
- [ ] M11.3 Masters Gesicht als Entsperrung (nur lokal, nur das eigene)
- [ ] M11.4 Dokumente über die Kamera scannen

## M12 – Smart Home (optional, braucht Geräte)
- [ ] M12.1 Home Assistant über MCP
- [ ] M12.2 Licht, Steckdosen, Heizung
- [ ] M12.3 Szenen wie "Lernmodus" oder "Gute Nacht"
- [ ] M12.4 Wächter fürs eigene Heimnetz: meldet unbekannte Geräte

## M13 – Sicherheit und Selbstpflege
- [~] M13.1 Policy-Engine vollständig: Riskantes nur mit Bestätigung (Beleg: policy/engine.py, tests/test_policy.py, tests/test_confirm_flow.py; jedes Werkzeug braucht eine deklarierte Risikoklasse)
- [~] M13.2 Private Daten bleiben lokal, Audit-Log ohne Inhalte
- [ ] M13.3 Automatische Backups
- [ ] M13.4 Wiederaufbau auf einem neuen PC mit einem Befehl
- [ ] M13.5 Messsystem: feste Testfragen zeigen, ob Jarvis mit jeder Version besser wird
- [ ] M13.6 Eigene Fehlerlogs auswerten und Verbesserungen vorschlagen
- [ ] M13.7 Lernen aus Masters Korrekturen
- [ ] M13.8 Updates für Modelle und Abhängigkeiten
- [ ] M13.9 Wächter für den PC: meldet Auffälliges

## Zusätzlich vorhanden (nicht in der ursprünglichen Liste)
- [~] N1 Notizen, lokal gespeichert, ohne Rückfrage
- [~] N2 Ideen, Aufträge (mit Frist, Betrag und Summen) und Entwürfe als Textdateien, für Nebeneinkünfte, alles lokal

## Offene Entscheidungen von Master
- [ ] Dauerläufer-Gerät (alter Laptop oder Mini-PC) ja oder nein
- [ ] Messenger-Kanal ja oder nein
- [ ] Smart-Home-Geräte anschaffen ja oder nein
- [ ] Websuche bei DuckDuckGo lassen oder auf SearXNG umstellen
- [ ] Cloud-Budget später neu entscheiden
- [ ] Welches Jarvis-Gerät: Windows-PC oder MacBook
