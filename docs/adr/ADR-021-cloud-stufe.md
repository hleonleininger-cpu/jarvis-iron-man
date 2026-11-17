# ADR-021: Cloud-Stufe als abgeschaltetes Gerüst

**Status:** akzeptiert. Ändert ADR-003 an genau einer Stelle: "Keine Cloud-Modelle".

## Kontext
Der Masterauftrag erlaubt ein Cloud-Modell "nur nach Freigabe pro Aufgabe und nie mit privaten
Daten" (Checkliste M1.6). Bisher gibt es keines, und ADR-003 verbietet es. Das Cloud-Budget ist
0 €. Gebaut wird nur das Gerüst, damit die Sperren stehen, bevor je ein Anbieter eingerichtet wird.

## Entscheidung
- **Aus und ohne Anbieter.** `[cloud]` in jarvis.toml: `enabled = false`, kein `provider`, kein
  `model`, keine `base_url`. Nutzbar ist die Stufe nur, wenn alles vier gesetzt ist. `base_url`
  muss `https://` sein. Die Prüfung der Ollama-Adresse (ADR-003) bleibt unverändert: Gespräche
  laufen weiter ausschließlich über das lokale Modell.
- **Nur ausdrücklich:** `/cloud <Frage>`. Nie automatisch, nie als Ausweichweg, wenn das lokale
  Modell nicht antwortet, nie im Plan oder bei Sub-Agenten.
- **Freigabe pro Aufgabe:** Der Nutzer sieht den genauen Text («Frage»), Anbieter und Modell und
  sagt Ja oder Nein (die vorhandene Rückfrage; ohne Antwort gilt Nein). Kanäle ohne Rückfrage
  senden nichts.
- **Harte Sperre für private Daten durch den Aufbau, nicht durch eine Inhaltsprüfung:** Die
  Cloud-Anfrage hat keine Werkzeuge, keinen Gesprächsverlauf und eine feste Anweisung ohne
  Wohnort, Anrede, Datum oder Gedächtnis. Aus Kalender, Notion, Notizen oder Gedächtnis kann
  deshalb nichts in sie gelangen. Was Master selbst in die Frage tippt, sieht er vor dem Senden.
- **Schlüssel im OS-Schlüsselbund** (`jarvis secrets set cloud_api_key`), nie in Dateien oder Logs.
- **Audit:** ein Eintrag je Schritt (angefragt, freigegeben oder abgelehnt, Ergebnis) mit
  Anbieter/Modell, Risikoklasse EXTERNAL_COMMUNICATION, ohne Inhalt. Fehler der Gegenseite
  werden ohne Details gemeldet.
- **Kennzeichnung:** Die Antwort trägt den Hinweis "aus der Cloud, nicht lokal".

## Folgen
- Ohne Konfiguration ändert sich nichts. Kein Test macht einen Cloud-Aufruf (Attrappe).
- Ob ein Anbieter wirklich eingerichtet wird, entscheidet Master später; Kosten und Alternativen
  (z. B. ein größeres lokales Modell) kommen dann mit Preis in den Bericht.
