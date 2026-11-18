"""Sprachclient gegen den echten Core (ASGI, ohne Netz): Strom, Kanal "voice", Stopp, CLI."""

import sqlite3
from pathlib import Path

import httpx2
import pytest
from pydantic_ai.models.test import TestModel
from typer.testing import CliRunner

from jarvis_core.api import create_app
from jarvis_core.cli import main as cli_main
from jarvis_core.config import Settings
from jarvis_voice.core_client import HttpCoreClient
from jarvis_voice.protocols import CoreDelta, CoreDone, CoreEvent

pytestmark = pytest.mark.anyio


def _client(settings: Settings, text: str = "Guten Tag, Master.") -> HttpCoreClient:
    app = create_app(settings, TestModel(call_tools=[], custom_output_text=text), "t")
    client = HttpCoreClient("http://127.0.0.1")
    client._http = httpx2.AsyncClient(  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
        transport=httpx2.ASGITransport(app=app), base_url="http://127.0.0.1"
    )
    return client


async def test_ask_streams_text_and_marks_the_voice_channel(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data")
    client = _client(settings)
    events: list[CoreEvent] = [e async for e in client.ask("Hallo", None)]
    assert "".join(e.text for e in events if isinstance(e, CoreDelta)) == "Guten Tag, Master."
    done = events[-1]
    assert isinstance(done, CoreDone) and done.session_id
    with sqlite3.connect(settings.audit_db_path) as conn:
        assert conn.execute("SELECT channel FROM audit").fetchall() == [("voice",)]
    await client.aclose()


async def test_stop_and_unknown_confirmation(tmp_path: Path) -> None:
    client = _client(Settings(data_dir=tmp_path / "data"))
    await client.stop()  # läuft nichts: darf nicht scheitern
    assert await client.confirm("gibt-es-nicht", True, by_voice=True) is False
    await client.aclose()


async def test_unreachable_core_is_reported_not_raised() -> None:
    client = HttpCoreClient("http://127.0.0.1:9")  # nichts hört zu
    events = [e async for e in client.ask("Hallo", None)]
    assert events and type(events[0]).__name__ == "CoreError"
    await client.stop()
    assert await client.confirm("x", True, by_voice=False) is False
    await client.aclose()


def test_cli_status_and_help(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(cli_main.app, ["voice", "status"])
    assert result.exit_code == 0
    assert "Sprachclient: aus" in result.output and "Core: nicht erreichbar" in result.output
    assert "faster_whisper" in result.output
    assert "text" in runner.invoke(cli_main.app, ["voice", "--help"]).output
