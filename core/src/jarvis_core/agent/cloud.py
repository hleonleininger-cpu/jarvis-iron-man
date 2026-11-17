"""Cloud-Stufe (M1.6): nur ein abgeschaltetes Gerüst (ADR-021, ändert ADR-003).

Standard ist AUS und ohne Anbieter. Selbst eingeschaltet gilt:
- Nur ausdrücklich: `/cloud <Frage>`; nie automatisch, nie als Ausweichweg bei einem Fehler.
- Freigabe pro Aufgabe: Der Nutzer sieht den genauen Text, der das Gerät verlässt, und sagt Ja.
- Harte Sperre für private Daten: Die Cloud-Anfrage besteht aus der Nutzerfrage und einer festen
  allgemeinen Anweisung. Sie hat KEINE Werkzeuge, KEINEN Gesprächsverlauf, keinen Wohnort,
  keine Anrede und nichts aus Kalender, Notion, Notizen oder Gedächtnis. Das ist keine Prüfung
  von Inhalten (die ließe sich umgehen), sondern der Aufbau: Es gibt keinen Weg, private Daten
  in diese Anfrage zu legen.
- Der Schlüssel liegt im OS-Schlüsselbund (`jarvis secrets set cloud_api_key`), nie in der Datei.
- Audit: Ein Eintrag mit Anbieter, Modell und Ergebnis, ohne Inhalt.
"""

from jarvis_core.config.settings import CloudConfig

CLOUD_TOOL = "cloud_model"  # Name des Audit-Eintrags (kein Werkzeug des Modells)
CLOUD_PREFIX = "/cloud"
# Kanäle, die Rückfragen selbst beantworten (Evals): dort nie ein Cloud-Aufruf.
NO_CLOUD_CHANNELS = frozenset({"eval"})
CLOUD_SECRET = "cloud_api_key"  # Name im Schlüsselbund; Feld "token"

# Die einzige Anweisung, die eine Cloud-Anfrage trägt. Absichtlich ohne Datum, Ort, Anrede.
CLOUD_INSTRUCTIONS = "Beantworte die Frage sachlich und knapp auf Deutsch."

OFF_MESSAGE = (
    "Die Cloud-Stufe ist ausgeschaltet. Sie ist nur ein Gerüst und standardmäßig aus "
    "(jarvis.toml, Abschnitt [cloud])."
)
DECLINED_MESSAGE = "Abgelehnt: Die Frage wurde nicht an die Cloud geschickt."


def parse_cloud_request(message: str) -> str | None:
    """Die Frage hinter `/cloud` (leer, wenn sie fehlt), sonst None (normale Anfrage)."""
    text = message.strip()
    low = text.casefold()
    if low == CLOUD_PREFIX or low.startswith(CLOUD_PREFIX + " "):
        return text[len(CLOUD_PREFIX) :].strip()
    return None


def approval_preview(config: CloudConfig, question: str) -> tuple[str, str]:
    """Vorschau und Frage für die Freigabe: zeigt genau, was das Gerät verlässt."""
    where = f"{config.provider} ({config.model})"
    preview = f"Diese Frage geht ins Internet an {where}:\n«{question}»"
    return preview, "Wirklich senden? Der Anbieter sieht diesen Text."
