"""CONFIRM end-to-end: Agent + Testmodell erzwingt create_event -> Vorschau -> Ja/Nein."""

import asyncio
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic_ai.models.test import TestModel

from jarvis_core.agent import (
    ChatService,
    ConfirmationBroker,
    ConfirmRequested,
    Notice,
    StreamEvent,
    TextDelta,
    ToolUsed,
    build_service,
)
from jarvis_core.api import create_app
from jarvis_core.audit import AuditLog
from jarvis_core.cli import main as cli_main
from jarvis_core.config import Settings
from jarvis_core.config.settings import McpServerConfig
from jarvis_core.policy import RiskClass

pytestmark = pytest.mark.anyio

FAKE_SERVER = str(Path(__file__).parent / "fake_mcp_server.py")


def _settings(tmp_path: Path) -> Settings:
    cfg = McpServerConfig(
        command="python",
        args=[FAKE_SERVER, str(tmp_path / "writes.log")],
        tools={"get_weather": RiskClass.READ, "create_event": RiskClass.WRITE_REVERSIBLE},
        previews={"create_event": "preview_create_event"},
    )
    return Settings(data_dir=tmp_path / "data", mcp_servers={"cal": cfg})


def _service(settings: Settings, broker: ConfirmationBroker | None = None) -> ChatService:
    model = TestModel(call_tools=["create_event"])
    service = build_service(settings, AuditLog(settings.audit_db_path), model, "test")
    if broker is not None:
        service.broker = broker
    return service


def _writes(tmp_path: Path) -> list[str]:
    path = tmp_path / "writes.log"
    return path.read_text(encoding="utf-8").splitlines() if path.exists() else []


def _tool_rows(settings: Settings) -> list[tuple[str, str, str]]:
    with sqlite3.connect(settings.audit_db_path) as conn:
        return conn.execute(
            "SELECT tool, decision, status FROM audit WHERE tool IS NOT NULL ORDER BY id"
        ).fetchall()


async def _run(service: ChatService, approve: bool) -> list[StreamEvent]:
    events: list[StreamEvent] = []
    async for event in service.stream("Trag am Montag 15 Uhr Zahnarzt ein", channel="cli"):
        events.append(event)
        if isinstance(event, ConfirmRequested):
            service.broker.resolve(event.id, approve)
    return events


async def test_yes_writes_after_preview(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    events = await _run(_service(settings), approve=True)

    confirms = [e for e in events if isinstance(e, ConfirmRequested)]
    assert len(confirms) == 1
    assert _tool_rows(settings)[0] == ("create_event", "CONFIRM", "pending")  # erst fragen
    assert confirms[0].preview.startswith("Neuer Termin:") and confirms[0].question == "Anlegen?"
    assert ToolUsed("create_event") in events
    assert events.index(ToolUsed("create_event")) < events.index(confirms[0])
    assert len(_writes(tmp_path)) == 1
    assert _tool_rows(settings) == [
        ("create_event", "CONFIRM", "pending"),
        ("create_event", "CONFIRMED", "ok"),
    ]
    assert any(isinstance(e, TextDelta) for e in events)
    assert AuditLog(settings.audit_db_path).verify_chain().ok


async def test_no_writes_nothing(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    events = await _run(_service(settings), approve=False)
    assert any(isinstance(e, ConfirmRequested) for e in events)
    assert _writes(tmp_path) == []
    assert _tool_rows(settings) == [
        ("create_event", "CONFIRM", "pending"),
        ("create_event", "DENIED", "denied"),
    ]


async def test_no_answer_times_out_as_no(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    service = _service(settings, ConfirmationBroker(timeout=0.3))
    events = [e async for e in service.stream("Trag etwas ein", channel="cli")]
    assert any(isinstance(e, ConfirmRequested) for e in events)
    assert _writes(tmp_path) == []
    assert _tool_rows(settings)[-1] == ("create_event", "DENIED", "denied")


async def test_abort_while_waiting_is_denied_and_never_writes(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    service = _service(settings)
    gen = service.stream("Trag etwas ein", channel="cli")
    async for event in gen:
        if isinstance(event, ConfirmRequested):
            break
    await gen.aclose()
    await asyncio.sleep(0.1)
    assert _writes(tmp_path) == []
    assert _tool_rows(settings)[-1] == ("create_event", "DENIED", "aborted")
    with sqlite3.connect(settings.audit_db_path) as conn:
        assert conn.execute("SELECT status FROM audit WHERE tool IS NULL").fetchone() == (
            "aborted",
        )


def test_broker_resolve_unknown_or_twice() -> None:
    broker = ConfirmationBroker()
    assert broker.resolve("gibt-es-nicht", True) is False


# ------------------------------------------------------------------------------------- API
def test_api_confirm_endpoint_unknown_id(tmp_path: Path) -> None:
    client = TestClient(
        create_app(_settings(tmp_path), TestModel(), "test"), base_url="http://127.0.0.1"
    )
    response = client.post("/v1/confirm/gibt-es-nicht", json={"approve": True})
    assert response.status_code == 404


@pytest.mark.parametrize("approve", [True, False])
def test_api_confirm_event_then_second_call_decides(tmp_path: Path, approve: bool) -> None:
    import json
    import threading

    settings = _settings(tmp_path)
    model = TestModel(call_tools=["create_event"])
    client = TestClient(create_app(settings, model, "test"), base_url="http://127.0.0.1")
    seen: list[tuple[str, dict[str, str]]] = []
    done = threading.Event()

    def consume() -> None:
        with client.stream("POST", "/v1/chat", json={"message": "Trag etwas ein"}) as resp:
            buffer = ""
            for chunk in resp.iter_text():
                buffer += chunk
                while "\n\n" in buffer:
                    block, buffer = buffer.split("\n\n", 1)
                    event_line, data_line = block.split("\n")
                    seen.append((event_line[7:], json.loads(data_line[6:])))
        done.set()

    thread = threading.Thread(target=consume, daemon=True)
    thread.start()
    confirm: dict[str, str] | None = None
    for _ in range(100):
        confirm = next((d for k, d in seen if k == "confirm"), None)
        if confirm:
            break
        done.wait(0.1)
    assert confirm is not None and confirm["preview"].startswith("Neuer Termin:")
    assert _writes(tmp_path) == []  # vor der Antwort wurde nichts geschrieben
    answer = client.post(f"/v1/confirm/{confirm['id']}", json={"approve": approve})
    assert answer.status_code == 200
    finished = done.wait(25)
    assert finished, seen
    assert seen[-1][0] == "done"
    assert len(_writes(tmp_path)) == (1 if approve else 0)
    assert client.post(f"/v1/confirm/{confirm['id']}", json={"approve": True}).status_code == 404


# ------------------------------------------------------------------------------------- CLI
@pytest.mark.parametrize(("typed", "writes"), [("j", 1), ("n", 0), ("", 0), ("ja", 1), ("nein", 0)])
def test_cli_asks_and_respects_answer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
    typed: str,
    writes: int,
) -> None:
    settings = _settings(tmp_path)
    prompts: list[str] = []

    def fake_input(prompt: str = "") -> str:
        prompts.append(prompt)
        return typed

    monkeypatch.setattr(cli_main, "load_settings", lambda: settings)

    def fake_build(s: Settings, audit: AuditLog) -> ChatService:
        return build_service(s, audit, TestModel(call_tools=["create_event"]), "test")

    monkeypatch.setattr(cli_main, "build_service", fake_build)
    monkeypatch.setattr("builtins.input", fake_input)
    cli_main.chat("Trag am Montag 15 Uhr Zahnarzt ein")
    out = capfd.readouterr().out
    assert "[Werkzeug: create_event]" in out and "Neuer Termin:" in out
    assert prompts == ["Anlegen? [j/N] "]
    assert len(_writes(tmp_path)) == writes


# ------------------------------------------------------------------------------------- Runde 2
async def test_timeout_notice_differs_from_a_refusal(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    timed_out = [
        e
        async for e in _service(settings, ConfirmationBroker(timeout=0.3)).stream(
            "Trag etwas ein", channel="api"
        )
    ]
    refused = await _run(_service(settings), approve=False)
    assert any(isinstance(e, Notice) and "Keine Antwort innerhalb" in e.message for e in timed_out)
    assert any(isinstance(e, Notice) and e.message.startswith("Abgelehnt") for e in refused)
    assert _writes(tmp_path) == []


def test_cli_accepts_a_late_yes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    """Früher: Antwort nach dem Timeout wurde angenommen, der Termin aber nicht angelegt."""
    import time

    settings = _settings(tmp_path)

    def slow_input(prompt: str = "") -> str:
        time.sleep(0.6)
        return "j"

    monkeypatch.setattr(cli_main, "load_settings", lambda: settings)

    def fake_build(s: Settings, audit: AuditLog) -> ChatService:
        service = build_service(s, audit, TestModel(call_tools=["create_event"]), "test")
        service.broker = ConfirmationBroker(timeout=0.2)  # die CLI muss das ersetzen
        return service

    monkeypatch.setattr(cli_main, "build_service", fake_build)
    monkeypatch.setattr("builtins.input", slow_input)
    cli_main.chat("Trag am Montag 15 Uhr Zahnarzt ein")
    capfd.readouterr()
    assert len(_writes(tmp_path)) == 1


def test_cli_rejects_blank_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    import typer

    monkeypatch.setattr(cli_main, "load_settings", lambda: _settings(tmp_path))
    with pytest.raises(typer.Exit) as err:
        cli_main.chat("   ")
    assert err.value.exit_code == 2 and "leer" in capfd.readouterr().err
