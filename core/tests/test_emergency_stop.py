"""Not-Aus (M0.5, ADR-018): stoppt laufende und wartende Anfragen, offene Rückfragen, Werkzeuge."""

import asyncio
import os
import sqlite3
import subprocess
import sys
import time
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel
from typer.testing import CliRunner

from jarvis_core.agent import (
    ChatService,
    ConfirmationBroker,
    ConfirmRequested,
    Notice,
    StreamEvent,
    TextDelta,
    build_service,
)
from jarvis_core.agent.service import EMERGENCY_STOP_MESSAGE
from jarvis_core.api import create_app
from jarvis_core.audit import AuditLog
from jarvis_core.cli import main as cli_main
from jarvis_core.config import Settings
from jarvis_core.config.settings import McpServerConfig
from jarvis_core.policy import RiskClass

pytestmark = pytest.mark.anyio

FAKE_SERVER = str(Path(__file__).parent / "fake_mcp_server.py")


def _hanging_model(started: asyncio.Event) -> FunctionModel:
    """Ein Modell, das nie antwortet (wie ein hängendes Ollama)."""

    async def stream(_messages: list[ModelMessage], _info: AgentInfo) -> AsyncIterator[str]:
        started.set()
        await asyncio.sleep(3600)
        yield "nie"

    return FunctionModel(stream_function=stream)


def _audit(settings: Settings) -> list[tuple[str, str | None, str]]:
    with sqlite3.connect(settings.audit_db_path) as conn:
        return conn.execute("SELECT channel, tool, status FROM audit ORDER BY id").fetchall()


async def _collect(service: ChatService, text: str = "Frage") -> list[StreamEvent]:
    return [event async for event in service.stream(text, channel="api")]


async def test_stop_ends_a_hanging_request_and_the_service_works_afterwards(
    settings: Settings,
) -> None:
    started = asyncio.Event()
    service = build_service(
        settings, AuditLog(settings.audit_db_path), _hanging_model(started), "t"
    )
    running = asyncio.create_task(_collect(service))
    await started.wait()
    began = time.monotonic()
    assert service.emergency_stop("api") == 1
    events = await asyncio.wait_for(running, 5)
    assert time.monotonic() - began < 3  # sofort, nicht erst nach einem Zeitlimit
    assert events == [Notice(EMERGENCY_STOP_MESSAGE)]
    assert _audit(settings) == [("api", "emergency_stop", "ok"), ("api", None, "aborted")]
    # Danach arbeitet JARVIS normal weiter (Sperre ist frei, nichts hängt nach).
    ok = build_service(settings, AuditLog(settings.audit_db_path), TestModel(), "t")
    service._model = ok._model  # pyright: ignore[reportPrivateUsage]
    answer = await asyncio.wait_for(_collect(service), 10)
    assert any(isinstance(event, TextDelta) for event in answer)
    assert service.emergency_stop() == 0  # nichts läuft mehr
    assert _audit(settings)[-1] == ("system", "emergency_stop", "idle")


async def test_stop_also_ends_requests_that_are_still_waiting_in_line(settings: Settings) -> None:
    started = asyncio.Event()
    service = build_service(
        settings, AuditLog(settings.audit_db_path), _hanging_model(started), "t"
    )
    first = asyncio.create_task(_collect(service, "eins"))
    await started.wait()
    second = asyncio.create_task(_collect(service, "zwei"))
    third = asyncio.create_task(_collect(service, "drei"))
    await asyncio.sleep(0.1)  # beide warten hinter der ersten
    assert service.emergency_stop() == 3
    results = await asyncio.wait_for(asyncio.gather(first, second, third), 5)
    assert all(events[-1] == Notice(EMERGENCY_STOP_MESSAGE) for events in results)
    assert [row[2] for row in _audit(settings) if row[1] is None] == ["aborted"] * 3


async def test_stop_rejects_an_open_confirmation_and_nothing_is_written(tmp_path: Path) -> None:
    cfg = McpServerConfig(
        command="python",
        args=[FAKE_SERVER, str(tmp_path / "writes.log")],
        tools={"create_event": RiskClass.WRITE_REVERSIBLE},
        previews={"create_event": "preview_create_event"},
    )
    settings = Settings(data_dir=tmp_path / "data", mcp_servers={"cal": cfg})
    service = build_service(
        settings, AuditLog(settings.audit_db_path), TestModel(call_tools=["create_event"]), "t"
    )
    service.broker = ConfirmationBroker(timeout=None)  # ohne Not-Aus wartete sie ewig
    events: list[StreamEvent] = []

    async def run() -> None:
        async for event in service.stream("Trag etwas ein", channel="api"):
            events.append(event)
            if isinstance(event, ConfirmRequested):
                service.emergency_stop("api")

    await asyncio.wait_for(run(), 20)
    assert events[-1] == Notice(EMERGENCY_STOP_MESSAGE)
    assert not (tmp_path / "writes.log").exists()  # das Werkzeug lief nie
    with sqlite3.connect(settings.audit_db_path) as conn:
        tools = conn.execute(
            "SELECT tool, decision FROM audit WHERE tool = 'create_event' ORDER BY id"
        ).fetchall()
    assert tools and all(decision != "CONFIRMED" for _, decision in tools)


async def test_stop_ends_the_tool_server_processes(tmp_path: Path) -> None:
    """Ein Werkzeug, das hängt: nach dem Not-Aus läuft sein Server-Prozess nicht weiter."""
    marker = tmp_path / "pid.txt"
    script = tmp_path / "slow_server.py"
    script.write_text(
        "import os, sys, time\n"
        "from mcp.server.mcpserver import MCPServer\n"
        "mcp = MCPServer('slow', log_level='WARNING')\n"
        "@mcp.tool()\n"
        "def slow() -> str:\n"
        "    open(sys.argv[1], 'w').write(str(os.getpid()))\n"
        "    time.sleep(600)\n"
        "    return 'nie'\n"
        "mcp.run(transport='stdio')\n",
        encoding="utf-8",
    )
    cfg = McpServerConfig(
        command="python", args=[str(script), str(marker)], tools={"slow": RiskClass.READ}
    )
    settings = Settings(data_dir=tmp_path / "data", mcp_servers={"slow": cfg})
    service = build_service(
        settings, AuditLog(settings.audit_db_path), TestModel(call_tools=["slow"]), "t"
    )
    running = asyncio.create_task(_collect(service))
    for _ in range(300):  # bis das Werkzeug wirklich läuft
        if marker.exists() and marker.read_text():
            break
        await asyncio.sleep(0.1)
    pid = int(marker.read_text())
    assert service.emergency_stop() == 1
    events = await asyncio.wait_for(running, 30)
    assert events[-1] == Notice(EMERGENCY_STOP_MESSAGE)
    for _ in range(100):
        if not _alive(pid):
            break
        await asyncio.sleep(0.1)
    assert not _alive(pid)


def _alive(pid: int) -> bool:
    if os.name == "nt":
        out = subprocess.run(  # noqa: S603
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],  # noqa: S607
            capture_output=True, text=True, check=False,
        ).stdout  # fmt: skip
        return str(pid) in out
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    # Ein beendeter, noch nicht abgeholter Kindprozess (Zombie) zählt nicht als lebendig.
    try:
        return Path(f"/proc/{pid}/stat").read_text().split(") ")[1][0] != "Z"
    except (OSError, IndexError):
        return True


def test_broker_reject_all_answers_every_open_question_with_no() -> None:
    async def scenario() -> list[bool | None]:
        broker = ConfirmationBroker(timeout=None)
        asks = [
            asyncio.create_task(broker.ask("t", "v", "f", lambda _request: None)) for _ in range(3)
        ]
        await asyncio.sleep(0.05)
        assert broker.reject_all() == 3 and broker.reject_all() == 0
        return list(await asyncio.gather(*asks))

    assert asyncio.run(scenario()) == [False, False, False]


# --------------------------------------------------------------------------- API
def test_api_stop_without_running_request(settings: Settings) -> None:
    client = TestClient(create_app(settings, TestModel(), "t"), base_url="http://127.0.0.1")
    assert client.post("/v1/stop").json() == {"status": "ok", "abgebrochen": 0}
    assert _audit(settings) == [("api", "emergency_stop", "idle")]


def test_api_stop_needs_the_token_when_the_server_does(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "d", require_api_token=True)
    client = TestClient(
        create_app(settings, TestModel(), "t", api_token="geheim-123"), base_url="http://127.0.0.1"
    )
    assert client.post("/v1/stop").status_code == 401
    ok = client.post("/v1/stop", headers={"Authorization": "Bearer geheim-123"})
    assert ok.status_code == 200


def test_web_page_offers_the_stop_button_and_shortcut() -> None:
    web = Path(cli_main.__file__).parent.parent / "api" / "web"
    assert 'id="panic"' in (web / "index.html").read_text(encoding="utf-8")
    script = (web / "app.js").read_text(encoding="utf-8")
    assert '"/v1/stop"' in script and 'keyEvent.key === "."' in script


# --------------------------------------------------------------------------- jarvis stop
@pytest.fixture
def cli_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "jarvis.toml").write_text('data_dir = "d"\nport = 8793\n', encoding="utf-8")
    (tmp_path / "d").mkdir()
    return tmp_path


@pytest.mark.parametrize(
    ("outcome", "text", "code"),
    [
        (("ok", 2), "2 Anfrage(n) abgebrochen", 0),
        (("ok", 0), "es lief nichts", 0),
        (("down", 0), "läuft nicht", 0),
        (("denied", 401), "abgelehnt (HTTP 401)", 1),
    ],
)
def test_cli_stop_reports_the_outcome(
    cli_env: Path, monkeypatch: pytest.MonkeyPatch, outcome: tuple[str, int], text: str, code: int
) -> None:
    def fake(_settings: Settings, _token: str | None) -> tuple[str, int]:
        return outcome

    monkeypatch.setattr(cli_main, "_post_stop", fake)
    result = CliRunner().invoke(cli_main.app, ["stop"])
    assert result.exit_code == code and text in result.output


def test_cli_stop_kills_a_server_that_no_longer_answers(
    cli_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def hung(_settings: Settings, _token: str | None) -> tuple[str, int]:
        return "hung", 0

    monkeypatch.setattr(cli_main, "_post_stop", hung)
    victim = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"])  # noqa: S603
    try:
        (cli_env / "d" / "jarvis.pid").write_text(str(victim.pid), encoding="utf-8")
        result = CliRunner().invoke(cli_main.app, ["stop"])
        assert result.exit_code == 0 and "wurde beendet" in result.output
        assert victim.wait(timeout=10) != 0
    finally:
        victim.kill()


@pytest.mark.parametrize("content", [None, "", "kein-pid", "0", "-5"])
def test_cli_stop_without_usable_pid_file_says_so(
    cli_env: Path, monkeypatch: pytest.MonkeyPatch, content: str | None
) -> None:
    def hung(_settings: Settings, _token: str | None) -> tuple[str, int]:
        return "hung", 0

    monkeypatch.setattr(cli_main, "_post_stop", hung)
    if content is not None:
        (cli_env / "d" / "jarvis.pid").write_text(content, encoding="utf-8")
    result = CliRunner().invoke(cli_main.app, ["stop"])
    assert result.exit_code == 1 and "ließ sich nicht beenden" in result.output


def test_post_stop_tells_down_from_ok(cli_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from jarvis_core.config import load_settings

    settings = load_settings()
    assert cli_main._post_stop(settings, None) == ("down", 0)  # pyright: ignore[reportPrivateUsage]


def test_serve_writes_and_removes_the_pid_file(
    cli_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import uvicorn

    seen: list[str] = []

    def fake_run(*_args: object, **_kwargs: object) -> None:
        seen.append((cli_env / "d" / "jarvis.pid").read_text(encoding="utf-8"))

    def nothing(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(uvicorn, "run", fake_run)
    monkeypatch.setattr(cli_main, "_ensure_port_free", nothing)
    assert CliRunner().invoke(cli_main.app, ["serve"]).exit_code == 0
    assert seen == [str(os.getpid())] and not (cli_env / "d" / "jarvis.pid").exists()
