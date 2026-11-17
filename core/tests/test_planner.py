"""Aufgabenplaner (M1.3) und Simulationen (M1.15): Plan, Schritte, Selbstprüfung, Grenzen."""

import asyncio
import sqlite3
from collections.abc import AsyncIterator, Sequence
from pathlib import Path

import pytest
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel

from jarvis_core.agent import (
    ChatError,
    ChatService,
    Notice,
    Progress,
    StreamEvent,
    TextDelta,
    build_service,
)
from jarvis_core.agent.planner import (
    MAX_STEPS,
    SIMULATION_NOTE,
    PlanRequest,
    parse_request,
    parse_steps,
    step_message,
)
from jarvis_core.agent.service import EMERGENCY_STOP_MESSAGE, MAX_MODEL_REQUESTS
from jarvis_core.audit import AuditLog
from jarvis_core.config import Settings
from jarvis_core.config.settings import McpServerConfig
from jarvis_core.policy import RiskClass

pytestmark = pytest.mark.anyio

FAKE_SERVER = str(Path(__file__).parent / "fake_mcp_server.py")


def test_parse_request() -> None:
    assert parse_request("/plan Reise planen") == PlanRequest("Reise planen", simulation=False)
    assert parse_request("/PLAN x") == PlanRequest("x", simulation=False)
    assert parse_request("/plan") is None
    assert parse_request("/planen nein") is None
    assert parse_request("Was passiert, wenn ich früher aufstehe?") == PlanRequest(
        "Was passiert, wenn ich früher aufstehe?", simulation=True
    )
    assert parse_request("/simulation Lernplan 2 Wochen") == PlanRequest(
        "Lernplan 2 Wochen", simulation=True
    )
    assert parse_request("Wie ist das Wetter?") is None


def test_parse_steps_caps_and_ignores_prose() -> None:
    text = "Hier der Plan:\n1. Eins\n2) Zwei\n- Drei\n" + "\n".join(f"{i}. x" for i in range(4, 12))
    steps = parse_steps(text)
    assert steps[:3] == ["Eins", "Zwei", "Drei"]
    assert len(steps) == MAX_STEPS
    assert parse_steps("kein Plan") == []


def test_step_message_truncates_old_results() -> None:
    text = step_message("A", ["s1", "s2"], ["r" * 5000], 1)
    assert len(text) < 1000 and "Schritt 2" in text


class Script:
    """Modell-Attrappe: antwortet je nach Rolle (Plan, Schritt, Prüfung) und merkt sich alles."""

    def __init__(self, plan: str, calls_per_step: int = 0) -> None:
        self.plan = plan
        self.calls_per_step = calls_per_step
        self.offered: list[set[str]] = []
        self.requests = 0

    def _decide(self, messages: list[ModelMessage], info: AgentInfo) -> str | None:
        """Text der Antwort oder None = Werkzeug aufrufen."""
        instructions = info.instructions or ""
        if "Zerlege" in instructions:
            return self.plan
        if "Prüfe anhand" in instructions:
            return "OK"
        returns = sum(
            isinstance(p, ToolReturnPart) for m in messages for p in getattr(m, "parts", [])
        )
        last = messages[-1]
        fresh = not any(isinstance(p, ToolReturnPart) for p in getattr(last, "parts", []))
        if fresh:
            self.offered.append({t.name for t in info.function_tools})
        return "Ergebnis" if returns >= self.calls_per_step else None

    def model(self) -> FunctionModel:
        def function(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            self.requests += 1
            text = self._decide(messages, info)
            if text is None:
                return ModelResponse(parts=[ToolCallPart("get_weather", {"ort": "Dortmund"})])
            return ModelResponse(parts=[TextPart(text)])

        async def stream(
            messages: list[ModelMessage], info: AgentInfo
        ) -> AsyncIterator[str | dict[int, DeltaToolCall]]:
            self.requests += 1
            text = self._decide(messages, info)
            if text is None:
                yield {0: DeltaToolCall(name="get_weather", json_args='{"ort": "Dortmund"}')}
            else:
                yield text

        return FunctionModel(function, stream_function=stream)


def _service(tmp_path: Path, model: FunctionModel) -> tuple[Settings, ChatService]:
    cfg = McpServerConfig(
        command="python",
        args=[FAKE_SERVER],
        tools={"get_weather": RiskClass.READ, "create_event": RiskClass.WRITE_REVERSIBLE},
        previews={"create_event": "preview_create_event"},
    )
    settings = Settings(data_dir=tmp_path / "data", mcp_servers={"weather": cfg})
    return settings, build_service(settings, AuditLog(settings.audit_db_path), model, "test")


def _kinds(events: Sequence[StreamEvent]) -> list[str]:
    return [e.kind for e in events if isinstance(e, Progress)]


async def test_plan_runs_steps_then_checks(tmp_path: Path) -> None:
    script = Script("1. Wetter prüfen\n2. Antwort geben", calls_per_step=1)
    settings, service = _service(tmp_path, script.model())
    events = [e async for e in service.stream("/plan Wetter beschreiben", channel="cli")]
    progress = [e for e in events if isinstance(e, Progress)]
    assert _kinds(events) == ["plan", "step", "step", "check"]
    assert progress[1].text.startswith("1/2:") and progress[2].text.startswith("2/2:")
    assert progress[3].text == "OK"
    assert any(isinstance(e, TextDelta) for e in events)
    with sqlite3.connect(settings.audit_db_path) as conn:
        rows = conn.execute("SELECT tool, status FROM audit").fetchall()
    assert rows == [("get_weather", "ok"), ("get_weather", "ok"), (None, "ok")]


async def test_unusable_plan_runs_as_single_step(tmp_path: Path) -> None:
    _, service = _service(tmp_path, Script("keine Liste").model())
    events = [e async for e in service.stream("/plan Aufgabe", channel="cli")]
    assert any(isinstance(e, Notice) and "Kein Plan" in e.message for e in events)
    assert _kinds(events) == ["plan", "step", "check"]


async def test_request_limit_applies_per_step(tmp_path: Path) -> None:
    # Jeder Schritt braucht 6 Modellanfragen (5 Werkzeugaufrufe + Antwort): unter dem Limit je
    # Schritt, über dem Limit für alle zusammen.
    script = Script("1. A\n2. B", calls_per_step=5)
    _, service = _service(tmp_path, script.model())
    events = [e async for e in service.stream("/plan zwei Schritte", channel="cli")]
    assert _kinds(events) == ["plan", "step", "step", "check"]
    assert script.requests > MAX_MODEL_REQUESTS


async def test_endless_step_hits_the_limit(tmp_path: Path) -> None:
    _, service = _service(tmp_path, Script("1. A", calls_per_step=10_000).model())
    with pytest.raises(ChatError, match="Schritten"):
        [e async for e in service.stream("/plan endlos", channel="cli")]


async def test_simulation_offers_only_read_tools_and_labels_result(tmp_path: Path) -> None:
    script = Script("1. Termine lesen", calls_per_step=1)
    settings, service = _service(tmp_path, script.model())
    events = [
        e async for e in service.stream("Was passiert, wenn ich Montag frei nehme?", channel="cli")
    ]
    assert script.offered and all(names == {"get_weather"} for names in script.offered)
    text = "".join(e.text for e in events if isinstance(e, TextDelta))
    assert SIMULATION_NOTE in text
    with sqlite3.connect(settings.audit_db_path) as conn:
        tools = {r[0] for r in conn.execute("SELECT tool FROM audit WHERE tool IS NOT NULL")}
    assert tools == {"get_weather"}


async def test_emergency_stop_cancels_a_plan(tmp_path: Path) -> None:
    started = asyncio.Event()

    async def hang(_m: list[ModelMessage], _i: AgentInfo) -> AsyncIterator[str]:
        started.set()
        await asyncio.sleep(3600)
        yield "nie"

    async def never(_m: list[ModelMessage], _i: AgentInfo) -> ModelResponse:
        started.set()  # der Plan-Aufruf läuft ohne Stream und hängt hier
        await asyncio.sleep(3600)
        raise AssertionError

    settings, service = _service(tmp_path, FunctionModel(never, stream_function=hang))

    async def consume() -> list[StreamEvent]:
        return [e async for e in service.stream("/plan hängt", channel="api")]

    task = asyncio.create_task(consume())
    await asyncio.wait_for(started.wait(), 5)
    service.emergency_stop("api")
    events = await asyncio.wait_for(task, 5)
    assert any(isinstance(e, Notice) and e.message == EMERGENCY_STOP_MESSAGE for e in events)
    with sqlite3.connect(settings.audit_db_path) as conn:
        assert ("api", "aborted") in conn.execute("SELECT channel, status FROM audit")
