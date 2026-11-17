# Stand von JARVIS (15.11.2026)
(Geschrieben vom bisherigen Claude-Code-Lauf. Wo der Code etwas anderes zeigt, gilt der Code, und du meldest die Abweichung.)

Was JARVIS ist: ein lokaler persönlicher Assistent. Ein Python-Server (FastAPI) steht zwischen Master und einem Ollama-Modell, dazu kommen Web-Oberfläche, Kommandozeile und Werkzeug-Server. Alles läuft auf Masters Rechner. Nur Wetter, Websuche, iCloud und Notion gehen ins Netz, und das erst, wenn Master sie einschaltet (dazu das Cloud-Gerüst, aus und ohne Anbieter).

Was er kann:
- Chat: Web-Client (auch fürs Handy), CLI-Dialog, Gesprächsverlauf nur im Arbeitsspeicher, Zugriffstoken bei Nicht-Localhost.
- Wetter: aktuell und die nächsten drei Tage, mit Wohnort-Voreinstellung.
- Kalender (iCloud): Termine lesen (alle Kalender), anlegen, ändern und löschen, immer mit Vorschau und Rückfrage. Serientermine bleiben unangetastet.
- Notizen und Erinnerungen: lokal gespeichert, ohne Rückfrage. Erinnerungen melden sich zur Uhrzeit, solange JARVIS läuft.
- Websuche: DuckDuckGo, ohne Konto. Nur der Suchbegriff geht raus.
- Ideen, Aufträge, Entwürfe: lokal, für Nebeneinkünfte. Aufträge mit Frist, Betrag und Summen, Entwürfe als Textdateien.
- Gedächtnis (M1, in Arbeit): JARVIS merkt sich Ausdrückliches über Neustarts hinweg (Stichwortsuche), vergisst nach Rückfrage und führt ein Tagesjournal. Anrede "Master", Optionen mit Risiko, Kennzeichnung der Herkunft von Aussagen.
- Router und Planer (M1, Block C): Werkzeugauswahl per Embedding (`tool_select_limit`), zwei Modelle (`model_fast`/`model_strong`), `/plan <Auftrag>` mit Schrittliste und Selbstprüfung, `/simulation <Frage>` mit nur lesenden Werkzeugen. Alles aus, bis es in jarvis.toml eingeschaltet wird.
- Sub-Agenten und Cloud-Stufe (M1, Block D): `/team <Auftrag>` zerlegt in unabhängige Teilaufgaben mit eigenem Kontext und Werkzeug-Teilset. Die Cloud-Stufe ist nur ein abgeschaltetes Gerüst (`/cloud`, Freigabe je Frage, nie mit privaten Daten); es gibt keinen Anbieter und keinen Schlüssel.
- Notion: lesen, und neue Einträge anlegen mit Rückfrage. Ändern und Löschen gibt es im Code nicht.
- Not-Aus: roter Knopf und Strg+. in der Web-Oberfläche, `jarvis stop` in der Eingabeaufforderung. Bricht alle laufenden und wartenden Anfragen, offene Rückfragen und Werkzeuge ab; ein hängender Server wird beendet.
- Autostart: `jarvis autostart an` startet JARVIS bei jeder Windows-Anmeldung.
- Start-Prüfung: `jarvis serve` und `jarvis chat` warnen, wenn Ollamas Kontextfenster kleiner ist als in jarvis.toml angenommen.
- Sicherheit: Bezahlen und Handeln sind gesperrt, Schreibendes braucht Bestätigung, das Audit-Log enthält nie Inhalte, und die Cloud-Stufe ist aus und ohne Anbieter (ADR-021).

Qualität:
- 644 Tests grün, ruff und pyright sauber. Mehr als 100 Mängel wurden in 15 Prüfrunden behoben.
- Im Browser mit einer Ollama-Attrappe wurden alle Wege durchgespielt.
- Die CI (Linux und Windows) war für alle Commits bis 7f93d13 grün. Für den letzten Commit 0fab3d2 (sechs Korrekturen) ist die CI ebenfalls grün (geprüft am 15.11.).
- Alles liegt in PR #1 auf claude/gallant-carson-tp966a, der offen bleibt.

Nicht geprüft (geht nur auf Masters PC):
- Ein echtes Modell: ob kleine Modelle bei so vielen Werkzeugen die richtigen wählen.
- Ändern und Löschen im Kalender gegen echtes iCloud.
- Notion-Anlegen gegen echtes Notion, am besten mit einem Testeintrag.
- Ollama muss mit OLLAMA_CONTEXT_LENGTH=8192 starten, sonst passen die Werkzeuge nicht ins Kontextfenster.

Geld verdienen: JARVIS verdient nichts selbstständig. Er hilft bei Ideen, Recherche, Entwürfen und Fristen. Die Recherche zur Nische Programmieren/Tools liegt vor: Vorlagen, kleine Tools und Anleitungen für Einsteiger.

Arbeitsweise:
- Nachtschicht täglich ab 22:02 Uhr, Besprechung morgens um 7:15 bzw. 9:15 Uhr.
- Gebaut wird nach docs/MASTERAUFTRAG.md: ein Meilenstein pro Auftrag, nur Nummern aus docs/CHECKLISTE.md. Beauftragt ist M0.
- Abgrenzbare Teilaufgaben gehen an Untersessions, damit der Kontext klein bleibt.

Abweichungen aus dem Bestandsabgleich (15.11.): Secrets liegen im OS-Schlüsselbund, nicht in .env. Persona, HUD-Stil und PWA fehlen noch (Details in der Checkliste).

Meilenstein M0 (16.11.): alle Nummern stehen auf [~]. Nicht geprüft, geht nur auf Masters PC: Autostart bei einer echten Windows-Anmeldung, `jarvis stop` gegen einen hängenden Server unter Windows.

Nächster Schritt: Master probiert M0 aus und setzt [x]. Beauftragt ist danach M1 (Gehirn und Gedächtnis); dafür wird zuerst ein Plan vorgelegt. Master testet weiterhin Kalender, Notion und das Modell und meldet, was nicht passt.
