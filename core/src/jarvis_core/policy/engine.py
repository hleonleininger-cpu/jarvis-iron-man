"""Policy: Risikoklasse -> ALLOW | CONFIRM | DENY. Der MCP-Hub fragt sie vor jedem Aufruf."""

from dataclasses import dataclass
from enum import StrEnum


class RiskClass(StrEnum):
    READ = "READ"
    # Schreibt nur lokal (eigene Datei auf diesem Rechner), umkehrbar, ohne Außenwirkung,
    # z. B. Notizen. Ohne Rückfrage erlaubt, aber wie alles im Audit-Log (ADR-012).
    WRITE_LOCAL = "WRITE_LOCAL"
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
    RiskClass.WRITE_LOCAL: Decision.ALLOW,
    RiskClass.WRITE_REVERSIBLE: Decision.CONFIRM,
    RiskClass.IRREVERSIBLE: Decision.CONFIRM,
    RiskClass.EXTERNAL_COMMUNICATION: Decision.CONFIRM,
    RiskClass.FINANCIAL: Decision.DENY,
}


def decide(risk: RiskClass, context: PolicyContext | None = None) -> Decision:
    """Standardentscheidung je Risikoklasse; unbekannte Klassen werden abgelehnt."""
    del context  # noch ohne Wirkung; Signatur ist Teil des Vertrags
    return _DEFAULTS.get(risk, Decision.DENY)
