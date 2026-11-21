"""Status-Ereignisse fürs HUD (M3, ADR-024): Bus, Hub, ChatService, /v1/events, /v1/voice/event.

Kernregel: kein Ereignis trägt Nachrichtentext oder Werkzeugargumente/-ergebnisse."""

import asyncio
import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic_ai.models.test import TestModel

from jarvis_core.agent import ChatService, build_service
from jarvis_core.api import create_app
from jarvis_core.api.app import merge_queues
from jarvis_core.audit import AuditLog
from jarvis_core.config import Settings
from jarvis_core.config.settings import McpServerConfig
from jarvis_core.mcp_hub import McpHub, current_confirmer
from jarvis_core.policy import RiskClass
from jarvis_core.status import MAX_QUEUE, StatusBus, StatusError, StatusEvent

FAKE_SERVER = str(Path(__file__).parent / "fake_mcp_server.py")
SECRET = "streng-geheimer-inhalt"


# ------------------------------------------------------------------------------------- Bus
def test_bus_rejects_unknown_kinds_fields_and_long_text() -> None:
    bus = StatusBus()
    with pytest.raises(StatusError):
        bus.publish("gedanke", text="x")
    with pytest.raises(StatusError):
        bus.publish("tool_end", call=1, tool="lesen", risk="READ", status="ok", ms=1, result="x")
    with pytest.raises(StatusError):
        bus.publish("state", state="x" * 65)
    with pytest.raises(StatusError):
        bus.publish("state", state=["Liste"])  # pyright: ignore[reportArgumentType]


def test_bus_snapshot_keeps_only_the_last_state_and_never_tool_events() -> None:
    bus = StatusBus()
    bus.publish("state", state="WARTET")
    bus.publish("state", state="HÖRT ZU")
    bus.publish("mic", on=True)
    bus.publish("tool_start", call=1, tool="lesen", risk="READ")
    assert bus.snapshot() == {"state": {"state": "HÖRT ZU"}, "mic": {"on": True}}


def test_slow_listener_loses_old_events_but_never_blocks() -> None:
    bus = StatusBus()
    with bus.listen() as queue:
        for _ in range(MAX_QUEUE + 5):
            bus.publish("heartbeat")
        assert queue.qsize() == MAX_QUEUE
    bus.publish("heartbeat")  # ohne Zuhörer: nichts passiert


@pytest.mark.anyio
async def test_merge_queues_delivers_from_all_sources() -> None:
    first: asyncio.Queue[Any] = asyncio.Queue()
    second: asyncio.Queue[Any] = asyncio.Queue()
    merged = merge_queues(first, second)
    second.put_nowait("b")
    assert await anext(merged) == "b"
    first.put_nowait("a")
    assert await anext(merged) == "a"
    await merged.aclose()


# ------------------------------------------------------------------------------------- Hub
class FakeTool:
    def __init__(self, result: Any = None, error: Exception | None = None, delay: float = 0):
        self.result, self.error, self.delay = result, error, delay

    async def __call__(self, name: str, args: dict[str, Any]) -> Any:
        if name == "vorschau":
            return {"ok": True, "vorschau": SECRET, "frage": "Anlegen?"}
        await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        return self.result


@pytest.fixture
def hub(tmp_path: Path) -> McpHub:
    cfg = McpServerConfig(
        command="python",
        tools={"lesen": RiskClass.READ, "schreiben": RiskClass.WRITE_REVERSIBLE},
        previews={"schreiben": "vorschau"},
    )
    return McpHub({"fake": cfg}, AuditLog(tmp_path / "a.sqlite3"), "m", status=StatusBus())


def _drain(queue: asyncio.Queue[StatusEvent]) -> list[StatusEvent]:
    items: list[StatusEvent] = []
    while not queue.empty():
        items.append(queue.get_nowait())
    return items


@pytest.mark.anyio
async def test_hub_reports_start_and_end_without_arguments_or_results(hub: McpHub) -> None:
    assert hub.status is not None
    with hub.status.listen() as queue:
        await hub.gated_call("fake", FakeTool({"inhalt": SECRET}), "lesen", {"q": SECRET})
        events = _drain(queue)
    assert [e.kind for e in events] == ["tool_start", "tool_end"]
    assert events[0].data == {"call": 1, "tool": "lesen", "risk": "READ"}
    end = events[1].data
    assert end["status"] == "ok" and end["call"] == 1 and isinstance(end["ms"], int)
    assert SECRET not in repr(events)


@pytest.mark.anyio
async def test_hub_reports_blocked_denied_error_and_aborted(hub: McpHub) -> None:
    assert hub.status is not None

    async def no(tool: str, preview: str, question: str) -> bool:
        return False

    with hub.status.listen() as queue:
        await hub.gated_call("fake", FakeTool(), "unbekannt", {})
        token = current_confirmer.set(no)
        try:
            await hub.gated_call("fake", FakeTool(), "schreiben", {"t": SECRET})
        finally:
            current_confirmer.reset(token)
        with pytest.raises(RuntimeError):
            await hub.gated_call("fake", FakeTool(error=RuntimeError("x")), "lesen", {})
        task = asyncio.create_task(hub.gated_call("fake", FakeTool(delay=5), "lesen", {}))
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        ends = [e.data["status"] for e in _drain(queue) if e.kind == "tool_end"]
    assert ends == ["blocked", "denied", "error", "aborted"]


# ------------------------------------------------------------------------------ ChatService
@pytest.mark.anyio
async def test_service_reports_thinking_and_stop(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data", mcp_servers={})
    service = build_service(settings, AuditLog(tmp_path / "a.sqlite3"), TestModel(), "t")
    assert service.hub.status is service.status
    with service.status.listen() as queue:
        async for _ in service.stream(SECRET, channel="api"):
            pass
        service.emergency_stop("api")
        events = _drain(queue)
    assert [(e.kind, e.data.get("active")) for e in events] == [
        ("thinking", True),
        ("thinking", False),
        ("stop", None),
    ]
    assert SECRET not in repr(events)


# --------------------------------------------------------------------------------- API
def _settings(tmp_path: Path) -> Settings:
    cfg = McpServerConfig(
        command="python",
        args=[FAKE_SERVER, str(tmp_path / "writes.log")],
        tools={"get_weather": RiskClass.READ},
    )
    return Settings(data_dir=tmp_path / "data", mcp_servers={"cal": cfg})


def test_voice_events_validate_fields_and_audit_only_state_and_mic(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    app = create_app(settings, TestModel(call_tools=[]), "t")
    service: ChatService = app.state.service
    client = TestClient(app, base_url="http://127.0.0.1")

    def post(body: dict[str, object]) -> int:
        return client.post("/v1/voice/event", json=body).status_code

    assert post({"event": "state", "state": "HÖRT ZU"}) == 200
    assert post({"event": "mic", "on": True}) == 200
    assert post({"event": "level", "level": 5, "source": "mic"}) == 200
    assert post({"event": "heartbeat"}) == 200
    assert post({"event": "state"}) == 422  # Zustand fehlt
    assert post({"event": "state", "state": SECRET}) == 422  # kein freier Text
    assert post({"event": "level", "level": 9, "source": "mic"}) == 422
    assert post({"event": "heartbeat", "on": True}) == 422  # Angabe passt nicht zur Art
    assert post({"event": "dictation_start", "state": "WARTET"}) == 422
    assert service.status.snapshot() == {"state": {"state": "HÖRT ZU"}, "mic": {"on": True}}
    with sqlite3.connect(settings.audit_db_path) as conn:
        tools = [row[0] for row in conn.execute("SELECT tool FROM audit ORDER BY id")]
    assert tools == ["voice_state", "voice_mic_on"]  # Pegel und Lebenszeichen nicht im Audit


def _read_events(lines: Any, until: str) -> list[tuple[str, dict[str, object]]]:
    """Liest SSE-Ereignisse vom Zeilen-Iterator, bis eines `until` heißt (Daten zählen nicht)."""
    events: list[tuple[str, dict[str, object]]] = []
    name = ""
    for line in lines:
        if line.startswith("event: "):
            name = line[7:]
        elif line.startswith("data: "):
            events.append((name, json.loads(line[6:])))
            if name == until and events[-1][1].get("active") is not True:
                break
    return events


def test_events_stream_sends_snapshot_then_live_status_without_content(tmp_path: Path) -> None:
    app = create_app(_settings(tmp_path), TestModel(call_tools=["get_weather"]), "t")
    client = TestClient(app, base_url="http://127.0.0.1")
    assert client.post("/v1/voice/event", json={"event": "state", "state": "WARTET"}).is_success

    def chat() -> None:
        time.sleep(0.3)  # erst zuhören, dann fragen
        with client.stream("POST", "/v1/chat", json={"message": SECRET}) as r:
            for _ in r.iter_lines():
                pass

    with client.stream("GET", "/v1/events") as response:
        thread = threading.Thread(target=chat)
        thread.start()
        events = _read_events(response.iter_lines(), until="thinking")
    thread.join(timeout=10)
    kinds = [name for name, _ in events]
    assert kinds[:2] == ["ready", "snapshot"]
    assert events[1][1] == {"state": {"state": "WARTET"}}
    assert kinds[2:] == ["thinking", "tool_start", "tool_end", "thinking"]
    assert events[3][1]["tool"] == "get_weather" and events[4][1]["status"] == "ok"
    assert SECRET not in json.dumps(events)
