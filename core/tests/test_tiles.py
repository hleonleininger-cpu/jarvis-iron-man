"""Live-Kacheln fürs HUD (M3.6): Umformung, Abruf über den Hub, Zwischenspeicher, Sperre, Not-Aus."""

import asyncio
import sqlite3
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic_ai.models.test import TestModel

from jarvis_core.agent import build_service
from jarvis_core.agent.tiles import (
    MAX_EVENTS,
    TOOL_SECONDS,
    TZ,
    TileService,
    events_tile,
    system_tile,
    tasks_tile,
)
from jarvis_core.api import create_app
from jarvis_core.audit import AuditLog
from jarvis_core.config import Settings
from jarvis_core.evals.runner import eval_servers
from mcp_servers.notes.store import NoteStore

NOW = datetime(2026, 11, 21, 15, 0, tzinfo=TZ)


# ---------------------------------------------------------------------------- Umformung
def test_events_tile_keeps_running_and_upcoming_events_in_order() -> None:
    data = {
        "ok": True,
        "termine": [
            {
                "titel": "Später",
                "start": "2026-11-22T09:00:00+01:00",
                "ende": "2026-11-22T10:00:00+01:00",
            },
            {
                "titel": "Vorbei",
                "start": "2026-11-21T08:00:00+01:00",
                "ende": "2026-11-21T09:00:00+01:00",
            },
            {
                "titel": "Läuft gerade",
                "start": "2026-11-21T14:30:00+01:00",
                "ende": "2026-11-21T16:00:00+01:00",
            },
            {
                "titel": "Heute ganztägig",
                "start": "2026-11-21",
                "ende": "2026-11-21",
                "ganztaegig": True,
            },
            {"titel": "x" * 200, "start": "2026-11-23T09:00:00+01:00"},
            {"titel": "kaputt", "start": "gestern"},
        ],
    }
    tile = events_tile(data, NOW)
    titles = [entry["titel"] for entry in tile["eintraege"]]
    assert titles == ["Heute ganztägig", "Läuft gerade", "Später"]
    assert len(tile["eintraege"]) == MAX_EVENTS
    assert events_tile({"ok": False, "fehler": "iCloud weg"}, NOW)["ok"] is False
    assert events_tile("Unsinn", NOW)["ok"] is False


def test_tasks_tile_shortens_and_limits() -> None:
    data = {
        "ok": True,
        "anzahl": 7,
        "eintraege": [
            {"id": i, "text": "A" * 100, "faellig": "2026-11-20", "ueberfaellig": True}
            for i in range(7)
        ],
    }
    tile = tasks_tile(data)
    assert tile["anzahl"] == 7 and len(tile["eintraege"]) == 5
    assert len(tile["eintraege"][0]["text"]) == 80 and tile["eintraege"][0]["ueberfaellig"] is True
    assert "id" not in tile["eintraege"][0]
    assert tasks_tile(None)["ok"] is False


def test_system_tile_has_only_numbers() -> None:
    tile = system_tile()
    assert tile["ok"] is True and 0 <= tile["cpu"] <= 100 and 0 <= tile["ram"] <= 100
    assert tile["akku"] is None or set(tile["akku"]) == {"prozent", "laedt"}


# ------------------------------------------------------------------------- über den Hub
def _settings(tmp_path: Path) -> Settings:
    notes = tmp_path / "notizen.sqlite3"
    NoteStore(notes).add("Steuererklärung abgeben", None)
    servers = eval_servers(tmp_path / "writes.log", notes)
    keep = {name: servers[name] for name in ("calendar", "notes")}
    return Settings(data_dir=tmp_path / "data", mcp_servers=keep)


def _audit_rows(settings: Settings) -> list[tuple[str, str, str]]:
    with sqlite3.connect(settings.audit_db_path) as conn:
        return conn.execute("SELECT channel, tool, status FROM audit ORDER BY id").fetchall()


def test_tiles_endpoint_reads_calendar_and_tasks_once_per_interval(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    client = TestClient(create_app(settings, TestModel(), "t"), base_url="http://127.0.0.1")
    first = client.get("/v1/tiles").json()
    assert first["aktuell"] is True and first["system"]["ok"] is True
    assert first["termine"]["ok"] is True and isinstance(first["termine"]["eintraege"], list)
    assert first["missionen"]["eintraege"][0]["text"] == "Steuererklärung abgeben"
    assert first["fokus"] == {"ok": False, "grund": "Noch kein aktives Projekt festgelegt."}
    rows = _audit_rows(settings)
    assert rows == [("hud", "list_events", "ok"), ("hud", "list_notes", "ok")]  # ohne Inhalte
    second = client.get("/v1/tiles").json()  # innerhalb von fünf Minuten: aus dem Speicher
    assert second["missionen"] == first["missionen"] and _audit_rows(settings) == rows


def test_tiles_without_sources_say_so(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data", mcp_servers={})
    tiles = (
        TestClient(create_app(settings, TestModel(), "t"), base_url="http://127.0.0.1")
        .get("/v1/tiles")
        .json()
    )
    assert tiles["termine"] == {"ok": False, "grund": "Kein Kalender eingerichtet."}
    assert tiles["missionen"] == {"ok": False, "grund": "Keine Aufgabenliste eingerichtet."}


def test_tiles_need_the_token(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data", mcp_servers={}, require_api_token=True)
    client = TestClient(
        create_app(settings, TestModel(), "t", api_token="geheim"), base_url="http://127.0.0.1"
    )
    assert client.get("/v1/tiles").status_code == 401


# ------------------------------------------------------------ Sperre, Not-Aus, Zeitraster
@pytest.mark.anyio
async def test_busy_core_returns_the_old_state_without_waiting(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    service = build_service(settings, AuditLog(settings.audit_db_path), TestModel(), "t")
    clock = [0.0]
    tiles = TileService(service, clock=lambda: clock[0], now=lambda: NOW)
    loaded = await tiles.tiles()
    assert loaded["missionen"]["ok"] is True
    clock[0] += TOOL_SECONDS + 1  # fällig, aber eine Anfrage läuft
    async with service._run_lock:  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
        stale = await asyncio.wait_for(tiles.tiles(), 2)
    assert stale["aktuell"] is False and stale["missionen"] == loaded["missionen"]
    assert stale["stand"] == loaded["stand"]


@pytest.mark.anyio
async def test_emergency_stop_cancels_a_tile_refresh(tmp_path: Path) -> None:
    service = build_service(
        Settings(data_dir=tmp_path / "d", mcp_servers={}),
        AuditLog(tmp_path / "a.sqlite3"),
        TestModel(),
        "t",
    )
    entered = asyncio.Event()

    async def hold() -> None:
        async with service.idle_turn() as free:
            assert free
            entered.set()
            await asyncio.sleep(30)

    task = asyncio.create_task(hold())
    await entered.wait()
    assert service.emergency_stop("hud") == 1
    with pytest.raises(asyncio.CancelledError):
        await task
    async with service.idle_turn() as free:  # die Sperre ist wieder frei
        assert free
