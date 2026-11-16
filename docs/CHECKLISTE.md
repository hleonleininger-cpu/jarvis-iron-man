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
- [~] M0.5 Not-Aus per Taste und per Befehl, stoppt alle laufenden Aktionen (roter Knopf und Strg+. in der Web-Oberfläche, `jarvis stop`, POST /v1/stop; beendet auch einen hängenden Server. Beleg: docs/adr/ADR-018-not-aus.md, core/tests/test_emergency_stop.py. Eine systemweite Taste außerhalb des Browsers kommt mit der Desktop-App, M3)
- [~] M0.6 CLI-Chat mit lokalem Modell über Ollama
- [~] M0.7 Konfiguration und Secrets getrennt (OS-Schlüsselbund statt .env, nie im Repo; Entscheidung von Master am 15.11.; Beleg: core/src/jarvis_core/secrets/store.py, core/tests/test_secrets.py, .gitignore)
- [~] M0.8 Testgerüst: 644 Tests, ruff und pyright sauber, CI auf Linux und Windows
- [~] M0.9 Core startet automatisch mit Windows (`jarvis autostart an|aus|status`, Startdatei im Autostart-Ordner. Beleg: core/src/jarvis_core/cli/autostart.py, core/tests/test_autostart.py. Die Startdatei ist auf der Windows-CI ausgeführt worden, eine echte Anmeldung nicht)
- [~] M0.10 Ollama-Start mit OLLAMA_CONTEXT_LENGTH=8192 automatisch sicherstellen oder beim Start prüfen und warnen (gebaut: `jarvis serve` und `jarvis chat` prüfen beim Start und warnen. Beleg: core/tests/test_config.py. Ollama wird nicht von JARVIS gestartet)

## M1 – Gehirn und Gedächtnis
- [ ] M1.1 Modell-Router: kleines Modell für Schnelles, großes für Schweres
- [ ] M1.2 Orchestrator mit Sub-Agenten für parallele Aufgaben
- [ ] M1.3 Aufgabenplaner: zerlegt mehrstufige Aufträge, arbeitet sie ab, prüft das Ergebnis selbst
- [ ] M1.4 Werkzeugauswahl per Embedding, damit viele Werkzeuge kleine Modelle nicht überfordern
- [~] M1.5 Kern läuft vollständig lokal
- [ ] M1.6 Cloud-Stufe nur nach Freigabe pro Aufgabe und nie mit privaten Daten (bisher gibt es keine Cloud-Modelle)
- [~] M1.7 Langzeitgedächtnis (SQLite + sqlite-vec + Markdown). Heute liegt der Gesprächsverlauf nur im Arbeitsspeicher (gebaut: SQLite-Speicher mit remember/recall/list_memories/forget, Suche, ab M1.8 semantisch mit sqlite-vec. Beleg: docs/adr/ADR-019-langzeitgedaechtnis.md, core/tests/test_memory_server.py)
- [~] M1.8 Semantische Suche über Gespräche, Notizen und Dateien (gebaut: Gedächtnis, Vault-Dateien und offene Notizen; Embeddings über Ollama, sqlite-vec mit Python-Ausweichweg, Fallback auf Stichwörter. Beleg: core/tests/test_memory_server.py, core/tests/test_memory_vault.py. Schwelle 0,5 nur mit echtem Modell prüfbar)
- [~] M1.9 Tagesjournal (journal_add/journal_read, eine Markdown-Datei je Tag. Beleg: core/tests/test_memory_server.py)
- [~] M1.10 Vault für Dokumente und Ressourcen (gebaut: `vault_index` für ausdrücklich erlaubte Ordner und offene Notizen, Änderungserkennung, kein Link nach draußen. Beleg: core/tests/test_memory_vault.py. PDF folgt mit M5.4)
- [ ] M1.11 "Vergiss das" löscht zuverlässig und nachweisbar
- [ ] M1.12 Gedächtnispflege: Veraltetes aufräumen, Dateien indexieren
- [~] M1.13 Persona: Anrede "Master", trockener Humor, höflicher Widerspruch, Warnung bei schlechten Ideen (gebaut: Systemprompt PERSONA in agent/service.py, Anrede einstellbar über `anrede` in jarvis.toml; Test tests/test_agent_tools.py; Wirkung mit echtem Modell nur von Master prüfbar; Eval persona-address) (gebaut: Systemprompt PERSONA in agent/service.py, Anrede einstellbar über `anrede` in jarvis.toml; Test tests/test_agent_tools.py; Wirkung mit echtem Modell nur von Master prüfbar; Eval persona-address)
- [~] M1.14 Optionen mit Einschätzung: bei Entscheidungen zwei bis drei Wege samt Risiko (Systemprompt, agent/service.py; Wirkung mit echtem Modell nur von Master prüfbar; Eval persona-options)
- [ ] M1.15 Simulationen: "Was passiert, wenn …" für Lern- und Wochenpläne
- [~] M1.16 Ehrlichkeit: unterscheidet Live-Daten, Gespeichertes und eigene Schlüsse, erfindet nichts (gebaut: Systemprompt HONESTY trennt aktuelle Daten, 'gespeichert' und 'meine Einschätzung'; Wirkung mit echtem Modell nur von Master prüfbar; Eval honesty-unknown)

## M2 – Stimme
- [ ] M2.1 Wake Word "Hey Jarvis", lokal (openWakeWord)
- [ ] M2.2 Spracheingabe lokal (faster-whisper)
- [ ] M2.3 Sprachausgabe lokal (Piper), britischer Butler-Ton, keine Kopie einer echten Stimme
- [ ] M2.4 Sprechpausen-Erkennung (Silero VAD, Smart Turn) über Pipecat
- [ ] M2.5 Unterbrechen mitten im Satz
- [ ] M2.6 Folgefragen ohne erneutes Wake Word
- [ ] M2.7 Deutsch und Englisch automatisch
- [ ] M2.8 Echo-Schutz: reagiert nicht auf die eigene Stimme
- [ ] M2.9 Stimmerkennung: hört nur auf Master
- [ ] M2.10 Wake Word mitten im Satz, versteht das kurze Gespräch davor (nur lokal, nur wenige Sekunden)
- [ ] M2.11 Schlafmodus ("Gute Nacht, Jarvis") und Flüstermodus
- [ ] M2.12 Diktiermodus in jedes Textfeld
- [ ] M2.13 Start per Tastenkürzel oder Doppelklatschen

## M3 – HUD
- [ ] M3.1 Web-Oberfläche im Iron-Man-Stil (Bestand: schlichte Chat-Oberfläche hell/dunkel mit Markdown, Stopp, Rückfragen und Erinnerungen, api/web/; kein HUD-Stil)
- [ ] M3.2 Desktop-App mit Tauri 2
- [ ] M3.3 Boot-Sequenz und Sounds
- [ ] M3.4 Audio-Visualizer beim Zuhören und Sprechen
- [ ] M3.5 Transparentes Overlay über dem Desktop
- [ ] M3.6 Live-Kacheln: System, Termine, Fokus, Missionen
- [ ] M3.7 Situatives HUD: Anzeigen erscheinen nur, wenn sie gebraucht werden
- [ ] M3.8 Anzeige, was Jarvis gerade tut, mit Abbrechen-Knopf
- [ ] M3.9 Augensymbol, solange Jarvis den Bildschirm liest
- [ ] M3.10 Drehbare 3D-Ansichten als Hologramm-Ersatz

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
