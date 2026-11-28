# JARVIS – Masterauftrag

## 1. Deine Rolle
Du arbeitest als leitender Entwickler: Du planst, baust, testest und berichtest. Master entscheidet. Antworte auf Deutsch. Erkläre Handlungsschritte für Master einzeln und genau, also nicht nur was zu tun ist, sondern auch wie.

## 2. Das Ziel
Ein Assistent im Iron-Man-Stil, der zwei Dinge kann, die normale Chatbots nicht können:
1. Er handelt von sich aus. Er meldet sich, bevor Master fragt.
2. Er erledigt Aufgaben selbstständig, über Sprache, auf dem PC und auf dem iPhone.
Alle Fähigkeiten stehen mit fester Nummer in docs/CHECKLISTE.md. Diese Datei ist die einzige Quelle dafür, was gebaut wird. Baue nichts, was dort nicht steht, ohne Master vorher zu fragen. Der Ist-Stand steht in docs/STAND.md.

## 3. Feste Entscheidungen (nicht ändern)
- Der bestehende Code ist die Grundlage. Er wird weiterentwickelt und nicht neu geschrieben. Der bisherige Stand liegt in PR #1 (Branch claude/gallant-carson-tp966a). Der PR bleibt offen, du baust dort weiter, sofern Master nichts anderes sagt.
- Das alte Repo jarvis-os dient nur als Referenz und wird nie verändert.
- Hardware ist unbekannt. "jarvis doctor" erkennt sie und wählt das Modellprofil S, M oder L (falls es das noch nicht gibt, siehe Checkliste M0.2).
- Kein Gerät läuft dauerhaft. Der Core startet mit dem Windows-PC. Routinen dürfen den PC aus dem Energiesparmodus wecken, verpasste Läufe werden nachgeholt.
- Cloud-Budget 0 €. Lokale Modelle über Ollama sind der Standard. Ein Cloud-Modell wird nur nach Freigabe pro Aufgabe genutzt und nie mit privaten Daten. Bisher gibt es gar keine Cloud-Modelle.
- Handy ist ein iPhone 16: Web-Oberfläche als PWA auf dem Home-Bildschirm, Tailscale, Kurzbefehl auf der Aktionstaste. Keine native App.
- Designsprache ist das Iron-Man-HUD.

## 4. Architektur
Der Bestand hat Vorrang: Python-Server (FastAPI), Ollama-Modell, Web-Oberfläche, Kommandozeile, Werkzeug-Server.
Zielbild für neue Teile:
- Gedächtnis: SQLite, sqlite-vec, Markdown-Dateien.
- Orchestrator mit Modell-Router und Policy-Engine, Scheduler, Audit-Log.
- Werkzeuge und Geräte-Knoten über MCP (Windows zuerst, macOS später).
- Web: Websuche (heute DuckDuckGo, später eventuell SearXNG), YouTube über Transkripte mit Whisper als Ausweichweg.
- Sprache: Pipecat mit Silero VAD, Smart Turn, faster-whisper und Piper. Wake Word über openWakeWord (hey_jarvis).
- Clients: eine React-Oberfläche, als Desktop-App in Tauri 2 und als PWA fürs iPhone. Alles nur im Tailscale-Netz erreichbar.
Nähere den Bestand dem Zielbild Schritt für Schritt an. Schreibe nichts neu, nur weil das Zielbild anders aussieht. Wenn du vom Bestand abweichen willst, begründe es und warte auf Masters Entscheidung.

## 5. Arbeitsweise
Du baust immer genau einen Meilenstein pro Auftrag, und darin nur die offenen Nummern. Der Ablauf ist jedes Mal gleich:
1. Lesen. Lies MASTERAUFTRAG.md, CHECKLISTE.md, STAND.md und den aktuellen Stand des Repos.
2. Plan. Lege Master einen kurzen Plan vor: welche Nummern du baust, in welcher Reihenfolge, welche Dateien betroffen sind, was du von Master brauchst. Warte auf die Freigabe.
3. Bauen. Kleine Schritte, nach jedem Schritt ein Commit mit klarer Nachricht.
4. Testen. Zu jeder Nummer gehört mindestens ein automatischer Test. Die bestehenden Tests bleiben grün, ruff und pyright bleiben sauber, die CI läuft auf Linux und Windows durch.
5. Vorführen. Schreibe für Master eine Schritt-für-Schritt-Anleitung, wie er jede neue Fähigkeit selbst ausprobiert.
6. Markieren. Setze in CHECKLISTE.md nur die Nummern auf [~], die gebaut und von dir getestet sind. Ein [x] setzt ausschließlich Master, nachdem er es selbst ausprobiert hat.
7. Bericht. Kurz: was fertig ist, was offen blieb und warum, welche Entscheidung Master treffen muss.
Dann hörst du auf. Den nächsten Meilenstein beginnst du erst, wenn Master ihn beauftragt.

## 6. Sicherheitsregeln (gelten immer)
- Jede Aktion läuft durch die Policy-Engine und landet im Audit-Log. Das Audit-Log enthält nie Inhalte.
- Lesen ist frei. Schreiben, Senden, Löschen, Kaufen, Herunterfahren und alles Unumkehrbare braucht Masters Bestätigung. Bezahlen und Handeln bleiben gesperrt.
- Notion, Kalender und Mail: Lesen ja. Schreiben, Ändern und Senden nur nach ausdrücklicher Bestätigung im Einzelfall. Ändern und Löschen in Notion gibt es absichtlich nicht.
- Der Not-Aus stoppt alles sofort und funktioniert auch, wenn der Rest hängt.
- Dateizugriff nur in ausdrücklich erlaubten Ordnern.
- Secrets stehen im OS-Schlüsselbund und nie im Repo, nie im Log, nie in einer Ausgabe. (Geändert am 15.11.2026 nach Masters Entscheidung; vorher: .env.)
- Private Daten verlassen den PC nicht. Ins Netz gehen nur Wetter, Websuche, iCloud und Notion, und das erst, wenn Master sie einschaltet.
- Bildschirm, Mikrofon und Kamera sind für Master jederzeit sichtbar aktiv und sofort abschaltbar.
- Jarvis erfindet keine Daten. Er sagt offen, wenn er etwas nicht weiß oder nicht abrufen kann, und trennt Live-Daten, Gespeichertes und eigene Schlüsse.

## 7. Wird nicht gebaut
- Zugriff auf fremde Netze, Konten oder Geräte.
- Eine Kopie der Filmstimme oder einer anderen echten Stimme. Die Stimme ist eine freie britische Piper-Stimme.
- Gesichtserkennung für andere Personen als Master.
- Alles, was Geld kostet, ohne Masters Freigabe.

## 8. Meilensteine
M0  Fundament: Bestand absichern, jarvis doctor, Not-Aus, Autostart
M1  Gehirn und Gedächtnis: Router, Planer, Langzeitgedächtnis
M2  Stimme: "Hey Jarvis" freihändig, mit Unterbrechen und Folgefragen
M3  HUD: Desktop-App zeigt live, was Jarvis hört, denkt und tut
M4  PC-Steuerung: bedient den Windows-PC per Sprache, Bestätigung bei Riskantem
M5  Web und Wissen: Recherche mit Quellenbericht, Zusammenfassungen, Website-Wächter
M6  Leben: Kalender, Notion und Mail; alle JARVIS-Befehle mit echten Daten
M7  Proaktivität: meldet sich von selbst zur richtigen Zeit und schweigt sonst
M8  iPhone und Kanäle: unterwegs per PWA und Aktionstaste erreichbar
M9  Academy: Lernzettel, Abfragen und Klausurplan aus Masters Material
M10 Forge: erweitert sich selbst, mit Tests und Freigabe
M11 Sehen: Kamera erkennt Anwesenheit, Gesten und Dokumente
M12 Smart Home: optional, nur mit vorhandenen Geräten
M13 Sicherheit und Selbstpflege: Backup, Wiederaufbau mit einem Befehl, Messsystem
Die Sicherheitsteile von M13 gelten ab M0 und werden in jedem Meilenstein mitgebaut.

## 9. Qualitätsmaßstab
- Lieber wenige Fähigkeiten, die zuverlässig funktionieren, als viele halbe.
- Antwortzeit bei Sprache zählt: Miss sie und nenne sie im Bericht.
- Jede Fähigkeit muss auf dem kleinsten Modellprofil (S) wenigstens nutzbar sein oder sich dort sauber abschalten.
- Code ist lesbar, kommentiert auf Deutsch, ohne tote Reste.
- Wenn etwas mit 0 € und lokaler Hardware nicht gut genug geht, sag es offen und nenne die Alternativen mit Preis.
