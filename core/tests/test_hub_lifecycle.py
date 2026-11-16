"""Lebenszyklus des MCP-Hubs: wiederholte und gleichzeitige Anfragen, Server-Grenzen, Aufräumen."""

import asyncio
import sqlite3
import sys
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from pydantic import ValidationError
from pydantic_ai.messages import ModelMessage, ModelRequest, ToolReturnPart, UserPromptPart
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel
from pydantic_ai.models.test import TestModel

from jarvis_core.agent import Notice, StreamEvent, ToolUsed, build_service
from jarvis_core.audit import AuditLog
from jarvis_core.config import Settings
from jarvis_core.config.settings import McpServerConfig
from jarvis_core.mcp_hub import McpHub
from jarvis_core.policy import RiskClass

pytestmark = pytest.mark.anyio

FAKE_SERVER = str(Path(__file__).parent / "fake_mcp_server.py")


def _weather(tools: dict[str, RiskClass] | None = None) -> McpServerConfig:
    return McpServerConfig(
        command="python", args=[FAKE_SERVER], tools=tools or {"get_weather": RiskClass.READ}
    )


def _broken() -> McpServerConfig:
    return McpServerConfig(command="gibt-es-nicht-xyz", tools={"nie_da": RiskClass.READ})


def _children() -> set[int]:
    """PIDs der Kindprozesse dieses Prozesses (nur Linux; sonst leer)."""
    import os

    found: set[int] = set()
    for entry in Path("/proc").iterdir() if sys.platform == "linux" else []:
        if not entry.name.isdigit():
            continue
        try:
            fields = (entry / "stat").read_text().rsplit(")", 1)[1].split()
        except OSError:
            continue
        if int(fields[1]) == os.getpid() and fields[0] != "Z":
            found.add(int(entry.name))
    return found


async def test_start_failures_are_not_repeated_on_later_requests(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data", mcp_servers={"kaputt": _broken()})
    model = TestModel(call_tools=[], custom_output_text="ok")
    service = build_service(settings, AuditLog(settings.audit_db_path), model, "test")
    for _ in range(3):
        events = [e async for e in service.stream("Hallo", channel="cli")]
        assert len([e for e in events if isinstance(e, Notice)]) == 1


async def test_overlapping_requests_do_not_break_each_other(tmp_path: Path) -> None:
    """Früher: die schnelle Anfrage räumte den Hub ab, die langsame sah 'unbekanntes Werkzeug'."""
    settings = Settings(data_dir=tmp_path / "data", mcp_servers={"weather": _weather()})
    gate = asyncio.Event()

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
            yield "fertig"
            return
        first = messages[0].parts[0]
        if isinstance(first, UserPromptPart) and "langsam" in str(first.content):
            await gate.wait()
        yield {0: DeltaToolCall(name="get_weather", json_args='{"ort": "Dortmund"}')}

    service = build_service(
        settings, AuditLog(settings.audit_db_path), FunctionModel(stream_function=stream_fn), "test"
    )

    async def ask(text: str) -> list[StreamEvent]:
        return [e async for e in service.stream(text, channel="api")]

    before = _children()
    slow = asyncio.create_task(ask("langsam"))
    await asyncio.sleep(1.0)
    fast = asyncio.create_task(ask("schnell"))
    await asyncio.sleep(0.5)
    gate.set()
    for events in await asyncio.gather(slow, fast):
        assert ToolUsed("get_weather") in events
        assert not any(isinstance(e, Notice) and "Unbekannt" in e.message for e in events)
    with sqlite3.connect(settings.audit_db_path) as conn:
        rows = conn.execute("SELECT tool, decision FROM audit WHERE tool IS NOT NULL").fetchall()
    assert rows == [("get_weather", "ALLOW")] * 2
    await asyncio.sleep(0.3)
    assert _children() <= before


async def test_hub_refuses_to_be_entered_twice(tmp_path: Path) -> None:
    hub = McpHub({}, AuditLog(tmp_path / "audit.sqlite3"), "test")
    async with hub:
        with pytest.raises(RuntimeError, match="bereits aktiv"):
            await hub.__aenter__()
    async with hub:  # nach dem Verlassen wieder nutzbar
        pass


async def test_tool_of_one_server_is_not_offered_by_another(tmp_path: Path) -> None:
    """`create_event` ist nur für Server b deklariert; a bietet es zwar an, darf es aber nicht."""
    servers = {
        "a": McpServerConfig(
            command="python",
            args=[FAKE_SERVER, str(tmp_path / "writes.log")],
            tools={"get_weather": RiskClass.READ},
        ),
        "b": McpServerConfig(command="gibt-es-nicht-xyz", tools={"create_event": RiskClass.READ}),
    }
    settings = Settings(data_dir=tmp_path / "data", mcp_servers=servers)
    async with McpHub(servers, AuditLog(tmp_path / "audit.sqlite3"), "test") as hub:
        assert hub.offered_tools == {"get_weather"}
    # TestModel ruft jedes angebotene Werkzeug auf: create_event darf nicht dabei sein.
    service = build_service(settings, AuditLog(settings.audit_db_path), TestModel(), "test")
    events = [e async for e in service.stream("Hallo", channel="cli")]
    assert [e.name for e in events if isinstance(e, ToolUsed)] == ["get_weather"]
    assert not (tmp_path / "writes.log").exists()


@pytest.mark.skipif(sys.platform != "linux", reason="Prozessliste über /proc")
async def test_server_process_ends_with_the_request(tmp_path: Path) -> None:
    before = _children()
    async with McpHub({"a": _weather()}, AuditLog(tmp_path / "audit.sqlite3"), "test"):
        assert len(_children() - before) == 1
    await asyncio.sleep(0.3)
    assert _children() <= before


async def test_gate_uses_the_risk_declared_for_that_server(tmp_path: Path) -> None:
    servers = {"a": _weather({"get_weather": RiskClass.READ})}
    hub = McpHub(servers, AuditLog(tmp_path / "audit.sqlite3"), "test")
    called: list[str] = []

    async def call_tool(name: str, args: dict[str, object]) -> str:
        called.append(name)
        return "ok"

    assert await hub.gated_call("a", call_tool, "get_weather", {}) == "ok"
    await hub.gated_call("unbekannter-server", call_tool, "get_weather", {})
    assert called == ["get_weather"]  # für einen anderen Server gilt die Deklaration nicht


def test_same_tool_name_on_two_servers_is_rejected() -> None:
    with pytest.raises(ValidationError, match="get_weather"):
        Settings(mcp_servers={"a": _weather(), "b": _weather()})


def test_preview_name_must_not_collide_with_a_tool_of_another_server() -> None:
    a = McpServerConfig(
        command="python",
        tools={"create_event": RiskClass.WRITE_REVERSIBLE},
        previews={"create_event": "get_weather"},
    )
    with pytest.raises(ValidationError, match="get_weather"):
        Settings(mcp_servers={"a": a, "b": _weather()})


async def test_missing_declared_tool_is_reported(tmp_path: Path) -> None:
    servers = {"a": _weather({"get_weather": RiskClass.READ, "tippfehler": RiskClass.READ})}
    async with McpHub(servers, AuditLog(tmp_path / "audit.sqlite3"), "test") as hub:
        assert hub.offered_tools == {"get_weather"}
        assert any("tippfehler" in failure for failure in hub.failures)


@pytest.mark.skipif(sys.platform != "linux", reason="Prozessliste über /proc")
async def test_cancel_during_startup_leaves_no_server_process(tmp_path: Path) -> None:
    before = _children()
    hub = McpHub({"a": _weather()}, AuditLog(tmp_path / "audit.sqlite3"), "test")
    original = hub._fetch_tool_infos  # pyright: ignore[reportPrivateUsage]

    async def boom() -> list[object]:
        raise RuntimeError("Liste nicht lesbar")

    hub._fetch_tool_infos = boom  # pyright: ignore[reportPrivateUsage, reportAttributeAccessIssue]
    with pytest.raises(RuntimeError):
        await hub.__aenter__()
    hub._fetch_tool_infos = original  # pyright: ignore[reportPrivateUsage]
    await asyncio.sleep(0.5)
    assert _children() <= before


async def test_request_waiting_for_another_gets_a_notice(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data")
    gate = asyncio.Event()

    async def stream_fn(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        await gate.wait()
        yield "fertig"

    service = build_service(
        settings, AuditLog(settings.audit_db_path), FunctionModel(stream_function=stream_fn), "test"
    )

    async def ask() -> list[StreamEvent]:
        return [e async for e in service.stream("Hallo", channel="api")]

    first = asyncio.create_task(ask())
    await asyncio.sleep(0.2)
    second = asyncio.create_task(ask())
    await asyncio.sleep(0.2)
    gate.set()
    events_first, events_second = await asyncio.gather(first, second)
    assert not any(isinstance(e, Notice) for e in events_first)
    assert any(isinstance(e, Notice) and "wartet" in e.message for e in events_second)


# ------------------------------------------------------------------------------------- Runde 5
def test_server_env_passes_proxy_but_never_jarvis_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    from jarvis_core.mcp_hub.hub import PASS_THROUGH_ENV, server_env

    for key in PASS_THROUGH_ENV:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy:3128")
    monkeypatch.setenv("SSL_CERT_FILE", "/etc/ca.pem")
    monkeypatch.setenv("JARVIS_MODEL", "geheimer-wert")
    monkeypatch.setenv("IRGENDWAS", "x")
    env = server_env({"NOTION_DB_X": "1", "HTTPS_PROXY": "http://anders:1"}) or {}
    assert env == {
        "HTTPS_PROXY": "http://anders:1",
        "SSL_CERT_FILE": "/etc/ca.pem",
        "NOTION_DB_X": "1",
    }


async def test_too_many_waiting_requests_are_turned_away(tmp_path: Path) -> None:
    from jarvis_core.agent import ChatError
    from jarvis_core.agent.service import MAX_WAITING

    settings = Settings(data_dir=tmp_path / "data")
    gate = asyncio.Event()

    async def stream_fn(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        await gate.wait()
        yield "fertig"

    service = build_service(
        settings, AuditLog(settings.audit_db_path), FunctionModel(stream_function=stream_fn), "test"
    )

    async def ask() -> str:
        try:
            async for _ in service.stream("Hallo", channel="api"):
                pass
        except ChatError as exc:
            return str(exc)
        return "ok"

    tasks = [asyncio.create_task(ask()) for _ in range(MAX_WAITING + 2)]
    await asyncio.sleep(0.3)
    gate.set()
    results = await asyncio.gather(*tasks)
    assert results.count("ok") == MAX_WAITING + 1
    assert results.count("JARVIS ist gerade ausgelastet. Bitte gleich noch einmal versuchen.") == 1
    assert await ask() == "ok"  # danach wieder frei


# ------------------------------------------------------------------------------------- Runde 9
async def test_tool_servers_do_not_log_into_the_terminal(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    cfg = McpServerConfig(
        command="python", args=["-m", "mcp_servers.doubles.weather"],
        tools={"get_weather": RiskClass.READ},
    )  # fmt: skip
    async with McpHub({"w": cfg}, AuditLog(tmp_path / "audit.sqlite3"), "test") as hub:
        toolset = hub._connected["w"]  # pyright: ignore[reportPrivateUsage]
        with pytest.raises(Exception):  # noqa: B017 - falsche Argumente
            await toolset.client.call_tool("get_weather", {"falsch": 1})
    await asyncio.sleep(0.2)
    assert "rejected" not in capfd.readouterr().err


# ------------------------------------------------------------------------------------- Runde 11
async def test_servers_start_in_parallel(tmp_path: Path) -> None:
    """Früher nacheinander: vier Server kosteten je Anfrage die Summe ihrer Startzeiten."""
    import time

    slow = tmp_path / "slow_server.py"
    slow.write_text(
        f"import time\ntime.sleep(1.0)\nexec(open({FAKE_SERVER!r}, encoding='utf-8').read())\n",
        encoding="utf-8",
    )
    servers = {
        f"s{i}": McpServerConfig(
            command="python", args=[str(slow)], tools={f"tool_{i}": RiskClass.READ}
        )
        for i in range(3)
    }
    audit = AuditLog(tmp_path / "audit.sqlite3")
    started = time.monotonic()
    async with McpHub({"s0": servers["s0"]}, audit, "test"):
        single = time.monotonic() - started
    started = time.monotonic()
    async with McpHub(servers, audit, "test"):
        three = time.monotonic() - started
    # Nacheinander wären es ~3 x single; parallel kaum mehr als einer (auf einem überlasteten
    # CI-Rechner wurden 2,1 x gemessen, daher 2,6 statt 2). Python startet unter
    # Windows deutlich langsamer, daher relativ statt einer festen Sekundengrenze).
    assert three < 2.6 * single, f"{three:.1f} s für drei, {single:.1f} s für einen Server"


# ------------------------------------------------------------------------------------- Runde 13
async def test_server_stderr_goes_to_a_per_run_log_file(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    noisy = tmp_path / "noisy_server.py"
    noisy.write_text(
        "import sys\nprint('Diagnose: Lauf', file=sys.stderr, flush=True)\n"
        f"exec(open({FAKE_SERVER!r}, encoding='utf-8').read())\n",
        encoding="utf-8",
    )
    servers = {"laut": McpServerConfig(command="python", args=[str(noisy)],
                                       tools={"get_weather": RiskClass.READ})}  # fmt: skip
    logs = tmp_path / "logs"
    for _ in range(2):
        async with McpHub(servers, AuditLog(tmp_path / "audit.sqlite3"), "test", log_dir=logs):
            pass
    assert "Diagnose" not in capfd.readouterr().err  # Terminal bleibt sauber
    text = (logs / "laut.log").read_text(encoding="utf-8")
    assert text.count("Diagnose: Lauf") == 1  # je Lauf neu, nicht angehängt


def test_server_env_has_each_name_once_on_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    """Windows: HTTPS_PROXY und https_proxy sind dieselbe Variable; doppelt übergeben wäre offen,
    welche gilt, und eine Überschreibung aus jarvis.toml könnte verloren gehen."""
    from jarvis_core.mcp_hub import hub as hub_module
    from jarvis_core.mcp_hub.hub import PASS_THROUGH_ENV, server_env

    for key in PASS_THROUGH_ENV:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy:3128")
    monkeypatch.setenv("https_proxy", "http://proxy:3128")
    monkeypatch.setattr(hub_module.os, "name", "nt")
    env = server_env({"https_proxy": "http://aus-der-toml:1"}) or {}
    assert [k for k in env if k.upper() == "HTTPS_PROXY"] == ["https_proxy"]
    assert env["https_proxy"] == "http://aus-der-toml:1"
