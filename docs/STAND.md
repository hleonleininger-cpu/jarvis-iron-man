# Stand von JARVIS (15.11.2026)
(Geschrieben vom bisherigen Claude-Code-Lauf. Wo der Code etwas anderes zeigt, gilt der Code, und du meldest die Abweichung.)

Was JARVIS ist: ein lokaler persönlicher Assistent. Ein Python-Server (FastAPI) steht zwischen Master und einem Ollama-Modell, dazu kommen Web-Oberfläche, Kommandozeile und Werkzeug-Server. Alles läuft auf Masters Rechner. Nur Wetter, Websuche, iCloud und Notion gehen ins Netz, und das erst, wenn Master sie einschaltet.

Was er kann:
- Chat: Web-Client (auch fürs Handy), CLI-Dialog, Gesprächsverlauf nur im Arbeitsspeicher, Zugriffstoken bei Nicht-Localhost.
- Wetter: aktuell und die nächsten drei Tage, mit Wohnort-Voreinstellung.
- Kalender (iCloud): Termine lesen (alle Kalender), anlegen, ändern und löschen, immer mit Vorschau und Rückfrage. Serientermine bleiben unangetastet.
- Notizen und Erinnerungen: lokal gespeichert, ohne Rückfrage. Erinnerungen melden sich zur Uhrzeit, solange JARVIS läuft.
- Websuche: DuckDuckGo, ohne Konto. Nur der Suchbegriff geht raus.
- Ideen, Aufträge, Entwürfe: lokal, für Nebeneinkünfte. Aufträge mit Frist, Betrag und Summen, Entwürfe als Textdateien.
- Notion: lesen, und neue Einträge anlegen mit Rückfrage. Ändern und Löschen gibt es im Code nicht.
- Sicherheit: Bezahlen und Handeln sind gesperrt, Schreibendes braucht Bestätigung, das Audit-Log enthält nie Inhalte, und es gibt keine Cloud-Modelle.

Qualität:
- 577 Tests grün, ruff und pyright sauber. Mehr als 100 Mängel wurden in 14 Prüfrunden behoben.
- Im Browser mit einer Ollama-Attrappe wurden alle Wege durchgespielt.
- Die CI (Linux und Windows) war für alle Commits bis 7f93d13 grün. Für den letzten Commit 0fab3d2 (sechs Korrekturen) ist das Ergebnis noch nicht geprüft.
- Alles liegt in PR #1 auf claude/gallant-carson-tp966a, der offen bleibt.

Nicht geprüft (geht nur auf Masters PC):
- Ein echtes Modell: ob kleine Modelle bei so vielen Werkzeugen die richtigen wählen.
- Ändern und Löschen im Kalender gegen echtes iCloud.
- Notion-Anlegen gegen echtes Notion, am besten mit einem Testeintrag.
- Ollama muss mit OLLAMA_CONTEXT_LENGTH=8192 starten, sonst passen die Werkzeuge nicht ins Kontextfenster.

Geld verdienen: JARVIS verdient nichts selbstständig. Er hilft bei Ideen, Recherche, Entwürfen und Fristen. Die Recherche zur Nische Programmieren/Tools liegt vor: Vorlagen, kleine Tools und Anleitungen für Einsteiger.

Arbeitsweise:
- Nachtschicht täglich ab 22:02 Uhr, Besprechung morgens um 7:15 bzw. 9:15 Uhr.
- Neue Funktionen werden erst nach Masters Rückmeldung zu den Tests gebaut.
- Abgrenzbare Teilaufgaben gehen an Untersessions, damit der Kontext klein bleibt.

Nächster Schritt: Master testet Kalender, Notion und das Modell und meldet, was nicht passt. Danach wird gebaut und korrigiert.
