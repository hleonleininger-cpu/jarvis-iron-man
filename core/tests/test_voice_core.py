"""Core-Seite der Sprache (M2, ADR-023): Risikoklasse in Rückfragen, Sprachbestätigung nur bei
WRITE_REVERSIBLE, Rückfragen über /v1/events, Kanal "voice", Audit des Diktiermodus."""

import asyncio
import json
import sqlite3
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic_ai.models.test import TestModel

from jarvis_core.agent import ChatService, ConfirmationBroker, ConfirmRequested, build_service
from jarvis_core.agent.confirmation import ConfirmDone, ConfirmRequest
from jarvis_core.agent.service import VOICE_STYLE
from jarvis_core.api import create_app
from jarvis_core.audit import AuditLog
from jarvis_core.config import Settings
from jarvis_core.config.settings import McpServerConfig
from jarvis_core.policy import RiskClass

FAKE_SERVER = str(Path(__file__).parent / "fake_mcp_server.py")


def _settings(tmp_path: Path, risk: RiskClass = RiskClass.WRITE_REVERSIBLE) -> Settings:
    cfg = McpServerConfig(
        command="python",
        args=[FAKE_SERVER, str(tmp_path / "writes.log")],
        tools={"get_weather": RiskClass.READ, "create_event": risk},
        previews={"create_event": "preview_create_event"},
    )
    return Settings(data_dir=tmp_path / "data", mcp_servers={"cal": cfg})


# ---------------------------------------------------------------------------- Broker
@pytest.mark.anyio
async def test_broker_publishes_open_and_done_requests() -> None:
    broker = ConfirmationBroker(timeout=2)
    seen: list[ConfirmRequest] = []
    with broker.listen() as queue:
        task = asyncio.create_task(
            broker.ask("t", "Vorschau", "Frage?", seen.append, "WRITE_LOCAL")
        )
        opened = await asyncio.wait_for(queue.get(), 2)
        assert isinstance(opened, ConfirmRequest) and opened.risk == "WRITE_LOCAL"
        assert broker.open_requests() == [opened] and broker.risk_of(opened.id) == "WRITE_LOCAL"
        broker.resolve(opened.id, True)
        done = await asyncio.wait_for(queue.get(), 2)
        assert done == ConfirmDone(opened.id, True)
        assert await task is True
    assert broker.open_requests() == [] and broker.risk_of(opened.id) is None


@pytest.mark.anyio
async def test_broker_reports_timeout_as_done_without_answer() -> None:
    broker = ConfirmationBroker(timeout=0.05)
    with broker.listen() as queue:
        assert await broker.ask("t", "p", "q", lambda _r: None) is None
        await queue.get()  # die Anfrage selbst
        done = await queue.get()
    assert isinstance(done, ConfirmDone) and done.approved is None


# ------------------------------------------------------------ Risikoklasse im Strom
@pytest.mark.anyio
async def test_confirm_event_carries_the_risk_class(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    service = build_service(
        settings, AuditLog(settings.audit_db_path), TestModel(call_tools=["create_event"]), "t"
    )
    risks: list[str] = []
    async for event in service.stream("Trag etwas ein", channel="cli"):
        if isinstance(event, ConfirmRequested):
            risks.append(event.risk)
            service.broker.resolve(event.id, False)
    assert risks == ["WRITE_REVERSIBLE"]


# ------------------------------------------------------------------------------- API
def _client(tmp_path: Path, risk: RiskClass) -> tuple[TestClient, Settings, ConfirmationBroker]:
    settings = _settings(tmp_path, risk)
    app = create_app(settings, TestModel(call_tools=["create_event"]), "t")
    service: ChatService = app.state.service
    return TestClient(app, base_url="http://127.0.0.1"), settings, service.broker


def _chat_in_thread(client: TestClient, voice: bool) -> threading.Thread:
    def run() -> None:
        with client.stream(
            "POST", "/v1/chat", json={"message": "Trag etwas ein", "voice": voice}
        ) as r:
            for _ in r.iter_lines():
                pass

    thread = threading.Thread(target=run)
    thread.start()
    return thread


def _wait_for_open(broker: ConfirmationBroker) -> ConfirmRequest:
    import time

    for _ in range(100):
        if broker.open_requests():
            return broker.open_requests()[0]
        time.sleep(0.05)
    raise AssertionError("keine Rückfrage")


@pytest.mark.parametrize(
    ("risk", "allowed"),
    [(RiskClass.WRITE_REVERSIBLE, True), (RiskClass.IRREVERSIBLE, False)],
)
def test_voice_approval_only_for_write_reversible(
    tmp_path: Path, risk: RiskClass, allowed: bool
) -> None:
    client, _settings_, broker = _client(tmp_path, risk)
    thread = _chat_in_thread(client, voice=True)
    request = _wait_for_open(broker)
    answer = client.post(f"/v1/confirm/{request.id}", json={"approve": True, "via": "voice"})
    if allowed:
        assert answer.status_code == 200
    else:
        assert answer.status_code == 403
        assert broker.open_requests()  # bleibt offen: nur der Bildschirm darf bestätigen
        assert client.post(f"/v1/confirm/{request.id}", json={"approve": True}).status_code == 200
    thread.join(10)
    assert (tmp_path / "writes.log").exists()


def test_voice_decline_is_always_allowed(tmp_path: Path) -> None:
    client, _s, broker = _client(tmp_path, RiskClass.IRREVERSIBLE)
    thread = _chat_in_thread(client, voice=True)
    request = _wait_for_open(broker)
    answer = client.post(f"/v1/confirm/{request.id}", json={"approve": False, "via": "voice"})
    assert answer.status_code == 200
    thread.join(10)
    assert not (tmp_path / "writes.log").exists()


def test_voice_chat_uses_voice_channel_and_speaking_rules(tmp_path: Path) -> None:
    from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
    from pydantic_ai.models.function import AgentInfo, FunctionModel

    seen: list[str] = []

    def function(_messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        seen.append(info.instructions or "")
        return ModelResponse(parts=[TextPart("Guten Morgen.")])

    async def stream(_m: list[ModelMessage], info: AgentInfo):  # noqa: ANN202
        seen.append(info.instructions or "")
        yield "Guten Morgen."

    settings = _settings(tmp_path)
    model = FunctionModel(function, stream_function=stream)
    client = TestClient(create_app(settings, model, "t"), base_url="http://127.0.0.1")
    assert client.post("/v1/chat", json={"message": "Hallo", "voice": True}).status_code == 200
    assert client.post("/v1/chat", json={"message": "Hallo"}).status_code == 200
    assert VOICE_STYLE in seen[0] and VOICE_STYLE not in seen[1]
    with sqlite3.connect(settings.audit_db_path) as conn:
        assert conn.execute("SELECT channel FROM audit").fetchall() == [("voice",), ("api",)]


def test_normal_chat_keeps_api_channel(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    client = TestClient(
        create_app(settings, TestModel(call_tools=[]), "t"), base_url="http://127.0.0.1"
    )
    client.post("/v1/chat", json={"message": "Hallo"})
    with sqlite3.connect(settings.audit_db_path) as conn:
        assert conn.execute("SELECT channel FROM audit").fetchall() == [("api",)]


def test_dictation_events_are_audited_without_content(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    client = TestClient(
        create_app(settings, TestModel(call_tools=[]), "t"), base_url="http://127.0.0.1"
    )
    assert client.post("/v1/voice/event", json={"event": "dictation_start"}).status_code == 200
    assert client.post("/v1/voice/event", json={"event": "dictation_end"}).status_code == 200
    assert client.post("/v1/voice/event", json={"event": "etwas anderes"}).status_code == 422
    with sqlite3.connect(settings.audit_db_path) as conn:
        rows = conn.execute("SELECT channel, tool, status FROM audit ORDER BY id").fetchall()
    assert rows == [
        ("voice", "voice_dictation_start", "ok"),
        ("voice", "voice_dictation_end", "ok"),
    ]


def test_events_stream_shows_open_confirmations_to_other_clients(tmp_path: Path) -> None:
    client, _s, broker = _client(tmp_path, RiskClass.WRITE_REVERSIBLE)
    chat = _chat_in_thread(client, voice=False)
    request = _wait_for_open(broker)
    seen: list[tuple[str, dict[str, object]]] = []
    name = ""
    with client.stream("GET", "/v1/events") as response:
        for line in response.iter_lines():
            if line.startswith("event: "):
                name = line[7:]
            elif line.startswith("data: "):
                seen.append((name, json.loads(line[6:])))
                if name == "confirm":
                    client.post(f"/v1/confirm/{request.id}", json={"approve": True})
                if name == "confirm_done":
                    break
    chat.join(10)
    kinds = [kind for kind, _ in seen]
    assert "confirm" in kinds and kinds[-1] == "confirm_done"
    confirm = next(data for kind, data in seen if kind == "confirm")
    assert confirm["id"] == request.id and confirm["risk"] == "WRITE_REVERSIBLE"
