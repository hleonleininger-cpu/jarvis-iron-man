# ADR-003: Nur localhost und Tailnet

**Status:** akzeptiert

## Kontext
Der Core läuft auf dem Windows-PC, kein Gerät im Dauerbetrieb. Das iPhone soll später zugreifen
können, der Core darf aber nie offen im Internet oder im Heimnetz stehen.

## Entscheidung
- Standardbindung ist `127.0.0.1`.
- Zugriff von außen nur über ein privates Tailnet (Tailscale), indem der Server an die konkrete
  Tailnet-Adresse gebunden wird.
- Die Konfigurationsprüfung lehnt Wildcard-Adressen (`0.0.0.0`, `::`, `*`, leer) ab.
- Der Server beantwortet nur Anfragen, deren Host-Header die eigene Adresse nennt (127.0.0.1,
  localhost oder die konfigurierte Tailnet-Adresse). Das verhindert, dass eine fremde Webseite
  per DNS-Rebinding im Browser des Nutzers mit dem Core spricht.
- Keine Cloud-Modelle, keine API-Schlüssel; Ollama läuft lokal. Die Konfiguration lehnt eine
  `ollama_base_url` ab, die nicht auf diesen Rechner, das Heimnetz (private Adressen, `.local`,
  Rechnernamen ohne Domain) oder das Tailnet (`100.64.0.0/10`, `.ts.net`) zeigt.

## Konsequenzen
- Kein Port-Forwarding, keine öffentliche Angriffsfläche.
- Authentifizierung am Tailnet-Zugang kommt mit dem ersten Client (eigener ADR).
- Der Core ist nur erreichbar, solange der PC läuft.
