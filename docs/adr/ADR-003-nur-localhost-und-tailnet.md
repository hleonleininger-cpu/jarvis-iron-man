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
- Keine Cloud-Modelle, keine API-Schlüssel; Ollama läuft lokal.

## Konsequenzen
- Kein Port-Forwarding, keine öffentliche Angriffsfläche.
- Authentifizierung am Tailnet-Zugang kommt mit dem ersten Client (eigener ADR).
- Der Core ist nur erreichbar, solange der PC läuft.
