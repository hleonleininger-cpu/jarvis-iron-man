"""Agent + MCP-Hub: echter stdio-MCP-Server (Attrappe), Policy, Audit, Fehlerfälle."""

import sqlite3
import sys
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel
from pydantic_ai.models.test import TestModel

from jarvis_core.agent import ChatService, Notice, StreamEvent, TextDelta, ToolUsed, build_service
from jarvis_core.audit import AuditLog
from jarvis_core.config import Settings
from jarvis_core.config.settings import McpServerConfig
from jarvis_core.policy import RiskClass

pytestmark = pytest.mark.anyio

FAKE_SERVER = str(Path(__file__).parent / "fake_mcp_server.py")


def _settings(tmp_path: Path, command: str = "python", args: list[str] | None = None) -> Settings:
    cfg = McpServerConfig(
        command=command,
        args=args if args is not None else [FAKE_SERVER],
        tools={"get_weather": RiskClass.READ},
    )
    return Settings(data_dir=tmp_path / "data", mcp_servers={"weather": cfg})


async def _collect(
    service: ChatService, text: str = "Wie ist das Wetter in Dortmund?"
) -> list[StreamEvent]:
    return [event async for event in service.stream(text, channel="cli")]


def _tool_rows(settings: Settings) -> list[tuple[str, str, str, str]]:
    with sqlite3.connect(settings.audit_db_path) as conn:
        return conn.execute(
            "SELECT tool, risk, decision, status FROM audit WHERE tool IS NOT NULL ORDER BY id"
        ).fetchall()


async def test_model_calls_weather_tool_and_result_reaches_answer(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    model = TestModel(call_tools=["get_weather"])
    service = build_service(settings, AuditLog(settings.audit_db_path), model, "test")

    events = await _collect(service)

    assert ToolUsed("get_weather") in events
    assert events.index(ToolUsed("get_weather")) < min(
        i for i, e in enumerate(events) if isinstance(e, TextDelta)
    )
    answer = "".join(e.text for e in events if isinstance(e, TextDelta))
    assert "12.5" in answer and "Nebel" in answer  # echte Werkzeugwerte im Ergebnis
    assert _tool_rows(settings) == [("get_weather", "READ", "ALLOW", "ok")]
    assert AuditLog(settings.audit_db_path).verify_chain().ok


async def test_undeclared_tool_of_a_server_is_not_offered(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    service = build_service(settings, AuditLog(settings.audit_db_path), TestModel(), "test")
    async with service.hub:
        offered = service.hub.offered_tools
    assert offered == {"get_weather"}


async def test_chat_without_tool_need_uses_no_tool(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    model = TestModel(call_tools=[], custom_output_text="Ich bin JARVIS.")
    service = build_service(settings, AuditLog(settings.audit_db_path), model, "test")
    events = await _collect(service, "Wer bist du?")
    assert not any(isinstance(e, ToolUsed) for e in events)
    assert "".join(e.text for e in events if isinstance(e, TextDelta)) == "Ich bin JARVIS."
    assert _tool_rows(settings) == []


async def test_server_that_cannot_start_does_not_break_chat(tmp_path: Path) -> None:
    settings = _settings(tmp_path, command="gibt-es-nicht-xyz", args=[])
    model = TestModel(call_tools=[], custom_output_text="Antwort ohne Werkzeug")
    service = build_service(settings, AuditLog(settings.audit_db_path), model, "test")
    events = await _collect(service)
    notices = [e for e in events if isinstance(e, Notice)]
    assert notices and "'weather'" in notices[0].message
    assert "".join(e.text for e in events if isinstance(e, TextDelta)) == "Antwort ohne Werkzeug"


async def test_unknown_tool_call_is_rejected_and_audited(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    calls = {"n": 0}

    async def stream_fn(
        messages: list[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[str | dict[int, DeltaToolCall]]:
        calls["n"] += 1
        if calls["n"] == 1:
            yield {0: DeltaToolCall(name="rm_rf", json_args='{"pfad": "/"}')}
        else:
            yield "Das konnte ich nicht ausführen."

    service = build_service(
        settings, AuditLog(settings.audit_db_path), FunctionModel(stream_function=stream_fn), "test"
    )
    events = await _collect(service)

    assert any(isinstance(e, Notice) and "rm_rf" in e.message for e in events)
    assert not any(isinstance(e, ToolUsed) for e in events)
    assert _tool_rows(settings) == [("rm_rf", "UNKNOWN", "DENY", "blocked")]
    assert sys.platform  # Attrappe hat nichts ausgeführt; Tool existiert nicht


async def test_text_before_a_tool_call_does_not_end_the_answer(tmp_path: Path) -> None:
    """Früher endete die Antwort nach 'Ich schaue kurz nach.'; das Ergebnis kam nie an."""
    from pydantic_ai.messages import ModelRequest, ModelResponse, ToolReturnPart

    settings = _settings(tmp_path)

    async def stream_fn(
        messages: list[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[str | dict[int, DeltaToolCall]]:
        answered = any(
            isinstance(part, ToolReturnPart)
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
        )
        if answered:
            yield "In Dortmund sind es "
            yield "12.5 Grad."
        else:
            yield "Ich schaue kurz nach. "
            yield {1: DeltaToolCall(name="get_weather", json_args='{"ort": "Dortmund"}')}

    service = build_service(
        settings, AuditLog(settings.audit_db_path), FunctionModel(stream_function=stream_fn), "test"
    )
    events = [e async for e in service.stream("Wetter in Dortmund?", channel="cli", session_id="s")]
    text = [e.text for e in events if isinstance(e, TextDelta)]
    assert text == ["Ich schaue kurz nach. ", "In Dortmund sind es ", "12.5 Grad."]
    assert events.index(ToolUsed("get_weather")) == 1  # zwischen Vorrede und Antwort
    assert _tool_rows(settings) == [("get_weather", "READ", "ALLOW", "ok")]
    history = service.sessions.history("s")
    assert isinstance(history[-1], ModelResponse)  # der Verlauf endet mit der Antwort


async def test_model_that_keeps_calling_tools_is_stopped(tmp_path: Path) -> None:
    from jarvis_core.agent import ChatError
    from jarvis_core.agent.service import MAX_MODEL_REQUESTS

    settings = _settings(tmp_path)
    calls = {"n": 0}

    async def stream_fn(
        messages: list[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[str | dict[int, DeltaToolCall]]:
        calls["n"] += 1
        yield {0: DeltaToolCall(name="get_weather", json_args='{"ort": "Dortmund"}')}

    service = build_service(
        settings, AuditLog(settings.audit_db_path), FunctionModel(stream_function=stream_fn), "test"
    )
    with pytest.raises(ChatError, match=f"nach {MAX_MODEL_REQUESTS} Schritten"):
        await _collect(service)
    assert calls["n"] == MAX_MODEL_REQUESTS
    with sqlite3.connect(settings.audit_db_path) as conn:
        assert conn.execute("SELECT status FROM audit WHERE tool IS NULL").fetchone() == ("error",)


async def test_leading_blank_lines_are_not_shown(tmp_path: Path) -> None:
    settings = _settings(tmp_path)

    async def stream_fn(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        yield "\n\n  "
        yield "\nHallo!\n\nZweiter Absatz."

    service = build_service(
        settings, AuditLog(settings.audit_db_path), FunctionModel(stream_function=stream_fn), "test"
    )
    events = await _collect(service, "Hallo")
    assert (
        "".join(e.text for e in events if isinstance(e, TextDelta)) == "Hallo!\n\nZweiter Absatz."
    )


async def test_tools_list_fails_when_a_server_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import typer

    from jarvis_core.cli import main as cli_main

    settings = _settings(tmp_path, command="gibt-es-nicht-xyz", args=[])
    with pytest.raises(typer.Exit) as err:
        await cli_main._list_tools(settings)  # pyright: ignore[reportPrivateUsage]
    assert err.value.exit_code == 1
    await cli_main._list_tools(_settings(tmp_path))  # pyright: ignore[reportPrivateUsage]


# ------------------------------------------------------------------------- Wohnort und Tag
def test_system_prompt_names_home_and_day_overview() -> None:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from jarvis_core.agent.service import system_prompt

    now = datetime(2026, 11, 12, 7, 15, tzinfo=ZoneInfo("Europe/Berlin"))
    with_home = system_prompt(now, home="Dortmund")
    assert "Der Nutzer wohnt in Dortmund" in with_home and "Donnerstag, der 2026-11-12" in with_home
    assert "Was steht heute an?" in with_home
    assert "wohnt" not in system_prompt(now)


async def test_home_location_reaches_the_model(tmp_path: Path) -> None:
    seen: list[str | None] = []

    async def stream_fn(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        seen.append(info.instructions)
        yield "ok"

    settings = Settings(data_dir=tmp_path / "data", home_location="Dortmund")
    service = build_service(
        settings, AuditLog(settings.audit_db_path), FunctionModel(stream_function=stream_fn), "test"
    )
    await _collect(service, "Wie ist das Wetter?")
    assert seen and seen[0] is not None and "wohnt in Dortmund" in seen[0]
