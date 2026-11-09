"""Agent + MCP-Hub + echter stdio-Notion-Server (Attrappe): notion_query -> Antwort + Audit."""

import sqlite3
import sys
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from pydantic_ai.messages import ModelMessage, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel

from jarvis_core.agent import TextDelta, ToolUsed, build_service
from jarvis_core.audit import AuditLog
from jarvis_core.config import Settings
from jarvis_core.config.settings import McpServerConfig
from jarvis_core.policy import RiskClass

pytestmark = pytest.mark.anyio


def _settings(tmp_path: Path, *, with_db: bool = True) -> Settings:
    env = {"NOTION_DB_MISSION_CONTROL": "00000000-0000-0000-0000-0000000000a1"} if with_db else {}
    cfg = McpServerConfig(
        command=sys.executable,
        args=["-m", "mcp_servers.doubles.notion"],
        env=env,
        tools={
            "notion_search": RiskClass.READ,
            "notion_query": RiskClass.READ,
            "notion_get": RiskClass.READ,
        },
    )
    return Settings(data_dir=tmp_path / "data", mcp_servers={"notion": cfg})


def _model() -> FunctionModel:
    async def stream(
        messages: list[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[str | dict[int, DeltaToolCall]]:
        returned = [p for m in messages for p in m.parts if isinstance(p, ToolReturnPart)]
        if not returned:
            yield {
                0: DeltaToolCall(name="notion_query", json_args='{"datenbank": "mission_control"}')
            }
        else:
            yield f"Antwort: {returned[-1].content}"

    return FunctionModel(stream_function=stream)


async def test_agent_reads_mission_control_from_notion(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    service = build_service(settings, AuditLog(settings.audit_db_path), _model(), "test")
    events = [e async for e in service.stream("Was steht in Mission Control an?", channel="cli")]

    assert ToolUsed("notion_query") in events
    answer = "".join(e.text for e in events if isinstance(e, TextDelta))
    assert "JARVIS OS fertigstellen" in answer and "In Arbeit" in answer
    assert "Steuerunterlagen" not in answer  # erledigt
    with sqlite3.connect(settings.audit_db_path) as conn:
        rows = conn.execute(
            "SELECT tool, risk, decision, status FROM audit WHERE tool IS NOT NULL"
        ).fetchall()
        raw = settings.audit_db_path.read_bytes()
    assert rows == [("notion_query", "READ", "ALLOW", "ok")]
    assert b"JARVIS OS" not in raw  # keine Inhalte im Audit-Log


async def test_missing_database_config_is_reported_not_invented(tmp_path: Path) -> None:
    settings = _settings(tmp_path, with_db=False)
    service = build_service(settings, AuditLog(settings.audit_db_path), _model(), "test")
    events = [e async for e in service.stream("Was steht in Mission Control an?", channel="cli")]
    answer = "".join(e.text for e in events if isinstance(e, TextDelta))
    assert "NOTION_DB_MISSION_CONTROL" in answer and "jarvis.toml" in answer
    with sqlite3.connect(settings.audit_db_path) as conn:
        assert conn.execute("SELECT status FROM audit WHERE tool = 'notion_query'").fetchone() == (
            "error",
        )
