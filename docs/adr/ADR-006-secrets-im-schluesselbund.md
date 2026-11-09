# ADR-006: Secrets im OS-Schlüsselbund

**Status:** akzeptiert

## Kontext
Der Kalender braucht Apple-ID und App-spezifisches Passwort. Sie dürfen nie in Repo, Logs,
Audit-Log oder Ausgaben landen.

## Entscheidung
- Ablage im OS-Schlüsselbund über die Bibliothek `keyring` (macOS Keychain, Windows
  Credential Manager). Dienst `jarvis`, Eintrag je Secret (z. B. `apple_caldav`), Wert als JSON
  `{"username": ..., "password": ...}`.
- `jarvis secrets set <name>` fragt lokal (Passwort verdeckt) und gibt nur „gespeichert“ aus;
  `jarvis secrets status` zeigt nur gesetzt/nicht gesetzt.
- Der MCP-Server liest den Eintrag selbst (Prozessgrenze: der Core reicht keine Secrets durch,
  `jarvis_core` und `mcp_servers` teilen nur diese Konvention).
- Ohne Eintrag meldet das Werkzeug „bitte `jarvis secrets set apple_caldav` ausführen“.

## Konsequenzen
- Neue Abhängigkeit `keyring` (plus `tzdata` für Zeitzonen unter Windows).
- Auf Systemen ohne Schlüsselbund (Headless-Linux) schlägt `secrets set` mit klarer Meldung fehl.
- Diagnosemeldungen der CalDAV-Schicht enthalten nur Schrittname, Status und Grund.
- Beim ersten Zugriff kann macOS nach Erlaubnis fragen.
