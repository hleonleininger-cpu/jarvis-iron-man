"""Policy-Gerüst: Risikoklasse -> ALLOW | CONFIRM | DENY. Noch keine Werkzeuge."""

from dataclasses import dataclass
from enum import StrEnum


class RiskClass(StrEnum):
    READ = "READ"
    WRITE_REVERSIBLE = "WRITE_REVERSIBLE"
    IRREVERSIBLE = "IRREVERSIBLE"
    EXTERNAL_COMMUNICATION = "EXTERNAL_COMMUNICATION"
    FINANCIAL = "FINANCIAL"


class Decision(StrEnum):
    ALLOW = "ALLOW"
    CONFIRM = "CONFIRM"
    DENY = "DENY"


@dataclass(frozen=True)
class PolicyContext:
    """Kontext einer Entscheidung (Platzhalter für spätere Regeln je Kanal)."""

    channel: str = "cli"


_DEFAULTS: dict[RiskClass, Decision] = {
    RiskClass.READ: Decision.ALLOW,
    RiskClass.WRITE_REVERSIBLE: Decision.CONFIRM,
    RiskClass.IRREVERSIBLE: Decision.CONFIRM,
    RiskClass.EXTERNAL_COMMUNICATION: Decision.CONFIRM,
    RiskClass.FINANCIAL: Decision.DENY,
}


def decide(risk: RiskClass, context: PolicyContext | None = None) -> Decision:
    """Standardentscheidung je Risikoklasse; unbekannte Klassen werden abgelehnt."""
    del context  # noch ohne Wirkung; Signatur ist Teil des Vertrags
    return _DEFAULTS.get(risk, Decision.DENY)
