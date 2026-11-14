# ADR-008: Notion-Anbindung, nur lesend

**Status:** teilweise ersetzt durch ADR-017 (Anlegen neuer Einträge); Ändern und Löschen bleiben ausgeschlossen

## Kontext
Das JARVIS OS (Mission Control, Academy, Forge, Olympus, Vault) liegt in Notion. JARVIS soll
Fragen dazu aus echten Daten beantworten. Diese Daten sind geschützt und dürfen durch den Core
weder verändert noch gelöscht werden.

## Entscheidung
Ein eigener MCP-Server `mcp_servers/notion/` (eigener Prozess, kennt den Core nicht) mit genau
drei Werkzeugen, alle Risikoklasse READ: `notion_search`, `notion_query`, `notion_get`.

- **Schreiben ist nicht vorhanden, nicht nur deaktiviert.** Der Client kennt keine Schreib-
  Methode. Eine Allowlist in `client.py` lässt als einzige Requests zu: Datenbank abfragen
  (`POST /data_sources/{id}/query`, eine Leseabfrage), `GET /pages/{id}` und
  `GET /blocks/{id}/children`. Alles andere wird vor dem Netz verworfen. Tests belegen das
  (kein PATCH/DELETE/PUT im Quelltext, Verweigerung jedes anderen Requests, genau drei Werkzeuge).
- **Token** liegt im OS-Schlüsselbund (`jarvis secrets set notion`, JSON `{"token": ...}`), wird
  nur im Server-Prozess gelesen und nie geloggt, auditiert oder ausgegeben. Diagnosezeilen
  enthalten nur Schritt, HTTP-Status und Notion-Fehlercode.
- **Datenbank-IDs** stehen in `jarvis.toml` unter `[mcp_servers.notion] env` als
  `NOTION_DB_<NAME> = "<ID>"` (generisches `env` je Server, keine IDs im Code). Es sind
  Data-Source-IDs, wie im alten Projekt per Schema-Audit festgestellt.
- **Normalisierung** (`normalize.py`, Schema-Wissen aus dem alten `notionAdapter.js`): Titel und
  Typen werden über den Property-Typ erkannt, bekannte Namen (Status, Priorität, Fällig, Bereich,
  Eintragstyp, Fach, Uhrzeit, Kapitel, Fortschritt …) wählen die wichtigsten Felder. Rohantworten
  gehen nie ans Modell; fehlende Felder werden übersprungen. Antworten sind auf ca. 3500 Zeichen
  begrenzt; erledigte Einträge erscheinen nur auf Wunsch.
- **Audit:** je Aufruf eine Zeile (Werkzeug, READ, ALLOW, Ergebnis), keine Inhalte.

## Konsequenzen
- Ein späteres Schreiben bräuchte bewusst neuen Code, einen neuen ADR und Risikoklasse CONFIRM.
- Die Datenbank muss in Notion mit der Integration geteilt sein; sonst meldet das Werkzeug
  „nicht gefunden oder nicht geteilt“ und das Modell erfindet nichts.
- Die Suche lädt die konfigurierten Datenbanken (max. 300 Einträge je Datenbank) und filtert
  lokal; für sehr große Datenbanken wäre Notions serverseitige Suche der nächste Schritt.
- Evals nutzen eine Notion-Attrappe (`mcp_servers/doubles/notion.py`).
