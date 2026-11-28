import pytest

from jarvis_core.policy import Decision, PolicyContext, RiskClass, decide


@pytest.mark.parametrize(
    ("risk", "expected"),
    [
        (RiskClass.READ, Decision.ALLOW),
        (RiskClass.WRITE_LOCAL, Decision.ALLOW),
        (RiskClass.WRITE_REVERSIBLE, Decision.CONFIRM),
        (RiskClass.IRREVERSIBLE, Decision.CONFIRM),
        (RiskClass.EXTERNAL_COMMUNICATION, Decision.CONFIRM),
        (RiskClass.FINANCIAL, Decision.DENY),
    ],
)
def test_default_decisions(risk: RiskClass, expected: Decision) -> None:
    assert decide(risk, PolicyContext()) is expected
    assert decide(risk) is expected


def test_every_risk_class_has_a_decision() -> None:
    assert {decide(r) for r in RiskClass} <= set(Decision)
