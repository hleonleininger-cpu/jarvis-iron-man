"""Sub-Agenten (M1.2): Zerlegung, eigener Kontext und Werkzeug-Teilset, Reihenfolge/Parallelität,
Not-Aus, Rückfragen. Modell: Attrappe, die je nach Rolle antwortet."""

import asyncio
import sqlite3
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel

from jarvis_core.agent import (
    ChatService,
    ConfirmRequested,
    Notice,
    Progress,
    StreamEvent,
    TextDelta,
    ToolUsed,
    build_service,
)
from jarvis_core.agent.service import EMERGENCY_STOP_MESSAGE
from jarvis_core.agent.subagents import (
    MAX_SUBAGENTS,
    SubTask,
    parse_subtasks,
    parse_team_request,
    run_all,
)
from jarvis_core.audit import AuditLog
from jarvis_core.config import Settings
from jarvis_core.config.settings import McpServerConfig
from jarvis_core.mcp_hub.select import ToolSelector
from jarvis_core.policy import RiskClass

from .test_tool_select import fake_embed

pytestmark = pytest.mark.anyio

FAKE_SERVER = str(Path(__file__).parent / "fake_mcp_server.py")
SPLIT = "1. Wetter: Wetter in Dortmund prüfen\n2. Notiz: Die Notiz prüfen"


def test_parse_team_request() -> None:
    assert parse_team_request("/team Woche planen") == "Woche planen"
    assert parse_team_request("/TEAM") == ""
    assert parse_team_request("/teams x") is None
    assert parse_team_request("Wetter") is None


def test_parse_subtasks() -> None:
    subs = parse_subtasks("Plan:\n1. Wetter: Wetter prüfen\n2) Nur eine Aufgabe ohne Titel")
    assert subs[0] == SubTask("Wetter", "Wetter prüfen")
    assert subs[1].task == "Nur eine Aufgabe ohne Titel" and subs[1].title
    many = parse_subtasks("\n".join(f"{i}. T{i}: tue {i}" for i in range(1, 10)))
    assert len(many) == MAX_SUBAGENTS
    assert parse_subtasks("keine Liste") == []


async def test_run_all_keeps_order_and_respects_parallelism() -> None:
    running = peak = 0

    async def runner(index: int, sub: SubTask) -> str:
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0.02)
        running -= 1
        return f"{index}:{sub.title}"

    subs = [SubTask(f"t{i}", "x") for i in range(4)]
    assert await run_all(subs, runner, 1) == ["0:t0", "1:t1", "2:t2", "3:t3"]
    assert peak == 1
    peak = 0
    assert await run_all(subs, runner, 3) == ["0:t0", "1:t1", "2:t2", "3:t3"]
    assert peak == 3


async def test_run_all_failure_cancels_the_rest() -> None:
    async def runner(index: int, _sub: SubTask) -> str:
        if index == 0:
            raise RuntimeError("kaputt")
        await asyncio.sleep(5)
        return "nie"

    with pytest.raises(ExceptionGroup):
        await run_all([SubTask("a", "a"), SubTask("b", "b")], runner, 2)


class Team:
    """Modell-Attrappe: Zerlegung, Sub-Agent (ruft das Wetterwerkzeug) und Zusammenfassung."""

    def __init__(self, split: str = SPLIT, delay: float = 0.0, fail_sub: bool = False) -> None:
        self.fail_sub = fail_sub
        self.split = split
        self.delay = delay
        self.sub_seen: list[str] = []  # Nutzertext + Verlauf je Sub-Agent-Lauf
        self.sub_tools: list[set[str]] = []
        self.running = self.peak = 0
        self.both_running = asyncio.Event()

    async def _decide(self, messages: list[ModelMessage], info: AgentInfo) -> str | None:
        text = info.instructions or ""
        if "UNABHÄNGIGE" in text:
            return self.split
        if "Fasse die Berichte" in text:
            return "Zusammenfassung"
        if self.fail_sub:
            raise OSError("weg")
        returned = any(
            isinstance(p, ToolReturnPart) for m in messages for p in getattr(m, "parts", [])
        )
        if not any(isinstance(p, ToolReturnPart) for p in getattr(messages[-1], "parts", [])) and (
            not returned
        ):
            self.sub_seen.append(repr(messages))
            self.sub_tools.append({t.name for t in info.function_tools})
        self.running += 1
        self.peak = max(self.peak, self.running)
        if self.peak >= 2:
            self.both_running.set()
        await asyncio.sleep(self.delay)
        self.running -= 1
        return "Bericht" if returned else None

    def model(self) -> FunctionModel:
        async def function(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            text = await self._decide(messages, info)
            if text is None:
                return ModelResponse(parts=[ToolCallPart("get_weather", {"ort": "Dortmund"})])
            return ModelResponse(parts=[TextPart(text)])

        async def stream(
            messages: list[ModelMessage], info: AgentInfo
        ) -> AsyncIterator[str | dict[int, DeltaToolCall]]:
            text = await self._decide(messages, info)
            if text is None:
                yield {0: DeltaToolCall(name="get_weather", json_args='{"ort": "Dortmund"}')}
            else:
                yield text

        return FunctionModel(function, stream_function=stream)


def _service(
    tmp_path: Path,
    team: Team,
    *,
    parallel: int = 1,
    selector: ToolSelector | None = None,
    risk: RiskClass = RiskClass.READ,
) -> tuple[Settings, ChatService]:
    cfg = McpServerConfig(
        command="python",
        args=[FAKE_SERVER],
        tools={"get_weather": risk, "create_event": RiskClass.WRITE_REVERSIBLE},
        previews={"create_event": "preview_create_event"},
    )
    settings = Settings(
        data_dir=tmp_path / "data", mcp_servers={"weather": cfg}, subagent_parallel=parallel
    )
    audit = AuditLog(settings.audit_db_path)
    return settings, build_service(settings, audit, team.model(), "test", selector=selector)


async def _collect(service: ChatService, text: str, **kw: object) -> list[StreamEvent]:
    out: list[StreamEvent] = []
    async for event in service.stream(text, channel="cli", **kw):  # type: ignore[arg-type]
        out.append(event)
        if isinstance(event, ConfirmRequested):
            service.broker.resolve(event.id, True)
    return out


async def test_team_runs_subagents_then_summarizes(tmp_path: Path) -> None:
    team = Team()
    settings, service = _service(tmp_path, team)
    events = await _collect(service, "/team Wetter und Notiz")
    texts = [e.text for e in events if isinstance(e, TextDelta)]
    assert "".join(texts) == "Zusammenfassung"  # Berichte der Sub-Agenten werden nicht gestreamt
    agent_lines = [e.text for e in events if isinstance(e, Progress) and e.kind == "agent"]
    assert agent_lines == [
        "Teilaufgaben: Wetter; Notiz",
        "1/2 startet: Wetter",
        "1/2 fertig: Wetter",
        "2/2 startet: Notiz",
        "2/2 fertig: Notiz",
    ]
    assert [e for e in events if isinstance(e, ToolUsed)] == [ToolUsed("get_weather")] * 2
    with sqlite3.connect(settings.audit_db_path) as conn:
        rows = conn.execute("SELECT tool, status FROM audit").fetchall()
    assert rows == [("get_weather", "ok"), ("get_weather", "ok"), (None, "ok")]


async def test_subagent_has_own_context(tmp_path: Path) -> None:
    team = Team()
    _, service = _service(tmp_path, team)
    await _collect(service, "Merk dir: Passwort geheim123", session_id="s")
    team.sub_seen.clear()
    await _collect(service, "/team Wetter und Notiz", session_id="s")
    assert len(team.sub_seen) == 2
    assert "Wetter in Dortmund prüfen" in team.sub_seen[0]
    assert "Die Notiz prüfen" not in team.sub_seen[0]  # kennt die andere Teilaufgabe nicht
    assert all("geheim123" not in seen for seen in team.sub_seen)  # kein Gesprächsverlauf


async def test_subagents_get_their_own_tool_subset(tmp_path: Path) -> None:
    team = Team(split="1. Wetter: Wetter prüfen\n2. Termine: Termin anlegen")
    selector = ToolSelector(fake_embed, limit=1)
    _, service = _service(tmp_path, team, selector=selector)
    await _collect(service, "/team Wetter und Termin")
    assert team.sub_tools[0] == {"get_weather"}
    assert team.sub_tools[1] == {"create_event"}


async def test_subagents_run_one_after_another_by_default(tmp_path: Path) -> None:
    team = Team(delay=0.02)
    _, service = _service(tmp_path, team)
    await _collect(service, "/team zwei Teile")
    assert team.peak == 1


async def test_subagents_can_run_in_parallel(tmp_path: Path) -> None:
    team = Team(delay=0.05)
    _, service = _service(tmp_path, team, parallel=2)
    await _collect(service, "/team zwei Teile")
    assert team.peak == 2


async def test_unusable_split_runs_the_task_as_one_subagent(tmp_path: Path) -> None:
    team = Team(split="keine Liste")
    _, service = _service(tmp_path, team)
    events = await _collect(service, "/team Wetter")
    assert [e.text for e in events if isinstance(e, Progress)][0].startswith("Teilaufgaben: Wetter")


async def test_missing_task(tmp_path: Path) -> None:
    team = Team()
    _, service = _service(tmp_path, team)
    events = await _collect(service, "/team")
    assert any(isinstance(e, Notice) and "Auftrag" in e.message for e in events)
    assert not team.sub_seen


async def test_subagent_writes_still_ask(tmp_path: Path) -> None:
    team = Team()
    _, service = _service(tmp_path, team, risk=RiskClass.WRITE_LOCAL)
    events = await _collect(service, "/team Wetter und Notiz")
    assert sum(isinstance(e, ConfirmRequested) for e in events) == 2


async def test_emergency_stop_cancels_running_subagents(tmp_path: Path) -> None:
    team = Team(delay=3600)
    settings, service = _service(tmp_path, team, parallel=2)

    async def consume() -> list[StreamEvent]:
        return await _collect(service, "/team zwei Teile")

    task = asyncio.create_task(consume())
    # Wartet, bis die Sub-Agenten laufen (die Zerlegung selbst hängt nicht).
    await asyncio.wait_for(team.both_running.wait(), 10)
    service.emergency_stop("api")
    events = await asyncio.wait_for(task, 10)
    assert any(isinstance(e, Notice) and e.message == EMERGENCY_STOP_MESSAGE for e in events)
    with sqlite3.connect(settings.audit_db_path) as conn:
        assert ("cli", "aborted") in conn.execute("SELECT channel, status FROM audit")


async def test_failing_subagent_gives_a_readable_message(tmp_path: Path) -> None:
    from jarvis_core.agent import ChatError

    _, service = _service(tmp_path, Team(fail_sub=True))
    with pytest.raises(ChatError, match="Ollama"):
        await _collect(service, "/team zwei Teile")


async def test_empty_team_starts_no_servers(tmp_path: Path) -> None:
    team = Team()
    _, service = _service(tmp_path, team)
    events = await _collect(service, "/team")
    assert any(isinstance(e, Notice) and "Auftrag" in e.message for e in events)


async def test_local_write_confirmation_is_reset_after_team(tmp_path: Path) -> None:
    team = Team()
    _, service = _service(tmp_path, team)
    await _collect(service, "/team Wetter und Notiz")
    assert service.hub.confirm_local_writes is False
