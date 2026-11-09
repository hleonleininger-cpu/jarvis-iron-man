"""Policy-Gate des MCP-Hubs: READ -> aufrufen, CONFIRM/DENY/undeklariert -> abgelehnt."""

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from jarvis_core.audit import AuditLog
from jarvis_core.config.settings import McpServerConfig
from jarvis_core.mcp_hub import McpHub
from jarvis_core.mcp_hub.hub import CONFIRM_MESSAGE, DENY_MESSAGE
from jarvis_core.policy import RiskClass

pytestmark = pytest.mark.anyio

SECRET_ARG = "streng-geheimes-argument"


class FakeTool:
    def __init__(self, result: Any = None, error: Exception | None = None) -> None:
        self.calls = 0
        self._result = result
        self._error = error

    async def __call__(self, name: str, args: dict[str, Any]) -> Any:
        self.calls += 1
        if self._error:
            raise self._error
        return self._result


@pytest.fixture
def audit(tmp_path: Path) -> AuditLog:
    return AuditLog(tmp_path / "audit.sqlite3")


@pytest.fixture
def hub(audit: AuditLog) -> McpHub:
    cfg = McpServerConfig(
        command="python",
        tools={
            "lesen": RiskClass.READ,
            "schreiben": RiskClass.WRITE_REVERSIBLE,
            "senden": RiskClass.EXTERNAL_COMMUNICATION,
            "zahlen": RiskClass.FINANCIAL,
        },
    )
    return McpHub({"fake": cfg}, audit, "testmodell")


def _rows(audit: AuditLog) -> list[tuple[str, str, str, str, str]]:
    with sqlite3.connect(audit._path) as conn:  # pyright: ignore[reportPrivateUsage]
        return conn.execute(
            "SELECT tool, risk, decision, status, model FROM audit ORDER BY id"
        ).fetchall()


async def test_read_is_called_and_audited(hub: McpHub, audit: AuditLog) -> None:
    tool = FakeTool(result={"ok": True})
    result = await hub.gated_call("fake", tool, "lesen", {"x": SECRET_ARG})
    assert result == {"ok": True}
    assert tool.calls == 1
    assert _rows(audit) == [("lesen", "READ", "ALLOW", "ok", "testmodell")]


async def test_tool_reported_failure_is_audited_as_error(hub: McpHub, audit: AuditLog) -> None:
    tool = FakeTool(result={"ok": False, "fehler": "Ort unbekannt"})
    result = await hub.gated_call("fake", tool, "lesen", {})
    assert result["fehler"] == "Ort unbekannt"
    assert _rows(audit)[0][3] == "error"


async def test_exception_is_audited_and_reraised(hub: McpHub, audit: AuditLog) -> None:
    tool = FakeTool(error=RuntimeError("kaputt"))
    with pytest.raises(RuntimeError):
        await hub.gated_call("fake", tool, "lesen", {})
    assert _rows(audit)[0][1:4] == ("READ", "ALLOW", "error")


@pytest.mark.parametrize(
    ("name", "risk", "decision", "message"),
    [
        ("schreiben", "WRITE_REVERSIBLE", "CONFIRM", CONFIRM_MESSAGE),
        ("senden", "EXTERNAL_COMMUNICATION", "CONFIRM", CONFIRM_MESSAGE),
        ("zahlen", "FINANCIAL", "DENY", DENY_MESSAGE),
    ],
)
async def test_confirm_and_deny_are_blocked(
    hub: McpHub, audit: AuditLog, name: str, risk: str, decision: str, message: str
) -> None:
    tool = FakeTool(result="darf nicht passieren")
    result = await hub.gated_call("fake", tool, name, {})
    assert result == message
    assert tool.calls == 0
    assert _rows(audit) == [(name, risk, decision, "blocked", "testmodell")]


async def test_confirm_message_mentions_m1b() -> None:
    assert "M1b" in CONFIRM_MESSAGE


async def test_undeclared_tool_is_never_called(hub: McpHub, audit: AuditLog) -> None:
    tool = FakeTool(result="x")
    result = await hub.gated_call("fake", tool, "nicht_deklariert", {})
    assert result == DENY_MESSAGE
    assert tool.calls == 0
    assert _rows(audit) == [("nicht_deklariert", "UNKNOWN", "DENY", "blocked", "testmodell")]


async def test_arguments_never_reach_the_audit_log(hub: McpHub, audit: AuditLog) -> None:
    await hub.gated_call("fake", FakeTool(result={"ok": True}), "lesen", {"x": SECRET_ARG})
    await hub.gated_call("fake", FakeTool(), "zahlen", {"x": SECRET_ARG})
    assert SECRET_ARG.encode() not in audit._path.read_bytes()  # pyright: ignore[reportPrivateUsage]
    assert audit.verify_chain().ok
