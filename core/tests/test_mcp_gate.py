"""Policy-Gate des MCP-Hubs: READ ausführen, CONFIRM nur nach Ja, DENY/undeklariert ablehnen."""

import asyncio
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from jarvis_core.audit import AuditLog
from jarvis_core.config.settings import McpServerConfig
from jarvis_core.mcp_hub import McpHub, current_confirmer
from jarvis_core.mcp_hub.hub import (
    DENY_MESSAGE,
    NO_CONFIRM_CHANNEL_MESSAGE,
    USER_DECLINED_MESSAGE,
)
from jarvis_core.policy import RiskClass

pytestmark = pytest.mark.anyio

SECRET_ARG = "streng-geheimes-argument"


class FakeTool:
    """Attrappe für call_tool; kennt zusätzlich das Vorschau-Werkzeug."""

    def __init__(self, result: Any = None, error: Exception | None = None) -> None:
        self.calls: list[str] = []
        self._result = result
        self._error = error

    async def __call__(self, name: str, args: dict[str, Any]) -> Any:
        self.calls.append(name)
        if name == "vorschau_schreiben":
            if args.get("kaputt"):
                return {"ok": False, "fehler": "Ungültige Zeit"}
            return {"ok": True, "vorschau": "Neuer Termin: Zahnarzt", "frage": "Anlegen?"}
        if self._error:
            raise self._error
        return self._result

    @property
    def real_calls(self) -> int:
        return len([c for c in self.calls if c != "vorschau_schreiben"])


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
        previews={"schreiben": "vorschau_schreiben"},
    )
    return McpHub({"fake": cfg}, audit, "testmodell")


def _rows(audit: AuditLog) -> list[tuple[str, str, str, str]]:
    with sqlite3.connect(audit._path) as conn:  # pyright: ignore[reportPrivateUsage]
        return conn.execute("SELECT tool, risk, decision, status FROM audit ORDER BY id").fetchall()


class Answer:
    """Bestätigungskanal-Attrappe: antwortet fest und merkt sich, was gefragt wurde."""

    def __init__(self, approve: bool) -> None:
        self.approve = approve
        self.asked: list[tuple[str, str, str]] = []

    async def __call__(self, tool: str, preview: str, question: str) -> bool:
        self.asked.append((tool, preview, question))
        return self.approve


async def test_read_is_called_and_audited(hub: McpHub, audit: AuditLog) -> None:
    tool = FakeTool(result={"ok": True})
    result = await hub.gated_call("fake", tool, "lesen", {"x": SECRET_ARG})
    assert result == {"ok": True}
    assert tool.real_calls == 1
    assert _rows(audit) == [("lesen", "READ", "ALLOW", "ok")]


async def test_tool_reported_failure_is_audited_as_error(hub: McpHub, audit: AuditLog) -> None:
    tool = FakeTool(result={"ok": False, "fehler": "Ort unbekannt"})
    result = await hub.gated_call("fake", tool, "lesen", {})
    assert result["fehler"] == "Ort unbekannt"
    assert _rows(audit)[0][3] == "error"


async def test_exception_is_audited_and_reraised(hub: McpHub, audit: AuditLog) -> None:
    tool = FakeTool(error=RuntimeError("kaputt"))
    with pytest.raises(RuntimeError):
        await hub.gated_call("fake", tool, "lesen", {})
    assert _rows(audit)[0][1:] == ("READ", "ALLOW", "error")


async def test_financial_is_denied(hub: McpHub, audit: AuditLog) -> None:
    tool = FakeTool(result="darf nicht passieren")
    assert await hub.gated_call("fake", tool, "zahlen", {}) == DENY_MESSAGE
    assert tool.calls == []
    assert _rows(audit) == [("zahlen", "FINANCIAL", "DENY", "blocked")]


async def test_confirm_without_confirmation_channel_does_nothing(
    hub: McpHub, audit: AuditLog
) -> None:
    tool = FakeTool(result="x")
    assert await hub.gated_call("fake", tool, "senden", {}) == NO_CONFIRM_CHANNEL_MESSAGE
    assert tool.real_calls == 0
    assert _rows(audit) == [("senden", "EXTERNAL_COMMUNICATION", "CONFIRM", "blocked")]


async def test_confirm_yes_executes_after_preview(hub: McpHub, audit: AuditLog) -> None:
    answer = Answer(approve=True)
    token = current_confirmer.set(answer)
    try:
        tool = FakeTool(result={"ok": True, "angelegt": "Neuer Termin: Zahnarzt"})
        result = await hub.gated_call("fake", tool, "schreiben", {"titel": SECRET_ARG})
    finally:
        current_confirmer.reset(token)
    assert result["ok"] is True
    assert tool.real_calls == 1
    assert answer.asked == [("schreiben", "Neuer Termin: Zahnarzt", "Anlegen?")]
    assert _rows(audit) == [
        ("schreiben", "WRITE_REVERSIBLE", "CONFIRM", "pending"),
        ("schreiben", "WRITE_REVERSIBLE", "CONFIRMED", "ok"),
    ]


async def test_confirm_no_does_not_execute(hub: McpHub, audit: AuditLog) -> None:
    token = current_confirmer.set(Answer(approve=False))
    try:
        tool = FakeTool(result={"ok": True})
        result = await hub.gated_call("fake", tool, "schreiben", {})
    finally:
        current_confirmer.reset(token)
    assert result == USER_DECLINED_MESSAGE
    assert tool.real_calls == 0
    assert _rows(audit) == [
        ("schreiben", "WRITE_REVERSIBLE", "CONFIRM", "pending"),
        ("schreiben", "WRITE_REVERSIBLE", "DENIED", "denied"),
    ]


async def test_confirmed_but_failing_write_is_audited_as_error(
    hub: McpHub, audit: AuditLog
) -> None:
    token = current_confirmer.set(Answer(approve=True))
    try:
        tool = FakeTool(result={"ok": False, "fehler": "iCloud nicht erreichbar"})
        result = await hub.gated_call("fake", tool, "schreiben", {})
    finally:
        current_confirmer.reset(token)
    assert result["ok"] is False
    assert _rows(audit)[-1] == ("schreiben", "WRITE_REVERSIBLE", "CONFIRMED", "error")


async def test_cancelled_confirmation_is_denied_and_audited(hub: McpHub, audit: AuditLog) -> None:
    async def never(tool: str, preview: str, question: str) -> bool:
        await asyncio.sleep(60)
        return True

    token = current_confirmer.set(never)
    try:
        tool = FakeTool(result={"ok": True})
        task = asyncio.create_task(hub.gated_call("fake", tool, "schreiben", {}))
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        current_confirmer.reset(token)
    assert tool.real_calls == 0
    assert _rows(audit)[-1] == ("schreiben", "WRITE_REVERSIBLE", "DENIED", "aborted")


async def test_invalid_input_is_reported_to_model_without_asking(
    hub: McpHub, audit: AuditLog
) -> None:
    answer = Answer(approve=True)
    token = current_confirmer.set(answer)
    try:
        tool = FakeTool(result={"ok": True})
        result = await hub.gated_call("fake", tool, "schreiben", {"kaputt": True})
    finally:
        current_confirmer.reset(token)
    assert result == {"ok": False, "fehler": "Ungültige Zeit"}
    assert answer.asked == []
    assert tool.real_calls == 0
    assert _rows(audit) == [("schreiben", "WRITE_REVERSIBLE", "CONFIRM", "invalid")]


async def test_confirm_without_preview_tool_shows_generic_preview(
    hub: McpHub, audit: AuditLog
) -> None:
    answer = Answer(approve=False)
    token = current_confirmer.set(answer)
    try:
        await hub.gated_call("fake", FakeTool(), "senden", {"an": "x@example.org"})
    finally:
        current_confirmer.reset(token)
    assert answer.asked[0][1].startswith("senden(an=")
    assert answer.asked[0][2] == "Ausführen?"


async def test_undeclared_tool_is_never_called(hub: McpHub, audit: AuditLog) -> None:
    tool = FakeTool(result="x")
    assert await hub.gated_call("fake", tool, "nicht_deklariert", {}) == DENY_MESSAGE
    assert tool.calls == []
    assert _rows(audit) == [("nicht_deklariert", "UNKNOWN", "DENY", "blocked")]


async def test_arguments_and_previews_never_reach_the_audit_log(
    hub: McpHub, audit: AuditLog
) -> None:
    token = current_confirmer.set(Answer(approve=True))
    try:
        await hub.gated_call("fake", FakeTool(result={"ok": True}), "lesen", {"x": SECRET_ARG})
        await hub.gated_call("fake", FakeTool(result={"ok": True}), "schreiben", {"x": SECRET_ARG})
    finally:
        current_confirmer.reset(token)
    data = audit._path.read_bytes()  # pyright: ignore[reportPrivateUsage]
    assert SECRET_ARG.encode() not in data
    assert b"Zahnarzt" not in data
    assert audit.verify_chain().ok
