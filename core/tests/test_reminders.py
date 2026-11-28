"""Erinnerungen (ADR-013): Ablage, Werkzeug, Wächter, Stream /v1/events."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from pydantic_ai.models.test import TestModel

from jarvis_core.agent import ReminderWatcher
from jarvis_core.agent.reminders import parse_reminders
from jarvis_core.api import create_app
from jarvis_core.audit import AuditLog
from jarvis_core.config import Settings
from jarvis_core.evals.runner import eval_servers
from mcp_servers.notes import server
from mcp_servers.notes.store import NoteStore

TZ = ZoneInfo("Europe/Berlin")
NOW = datetime(2026, 11, 12, 17, 0, tzinfo=TZ)


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> NoteStore:
    notes = NoteStore(tmp_path / "notizen.sqlite3")
    monkeypatch.setattr(server, "store", lambda: notes)
    return notes


# ------------------------------------------------------------------------------- Ablage/Werkzeug
def test_reminder_is_stored_and_shown(store: NoteStore) -> None:
    result = server.add_data("Müll rausbringen", None, "2026-11-12T18:00", now=NOW)
    assert result["gespeichert"] == "Merken: Müll rausbringen (Erinnerung 12.11.2026 18:00)"
    entry = server.list_data(now=NOW)["eintraege"][0]
    assert entry["erinnern_um"] == "2026-11-12T18:00" and "erinnert" not in entry


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ("2026-11-12T16:59", "Vergangenheit"),
        ("2026-11-12", "mit Uhrzeit"),
        ("heute abend", "Ungültiger Erinnerungszeitpunkt"),
        ("9999-12-31T10:00", "nicht plausibel"),
    ],
)
def test_bad_reminder_times_are_rejected(store: NoteStore, value: str, message: str) -> None:
    result = server.add_data("X", None, value, now=NOW)
    assert result["ok"] is False and message in result["fehler"]
    assert store.count() == 0


def test_due_reminders_are_handed_out_exactly_once(store: NoteStore) -> None:
    store.add("früh", None, remind_at=NOW + timedelta(minutes=5))
    store.add("spät", None, remind_at=NOW + timedelta(hours=2))
    done = store.add("erledigt", None, remind_at=NOW + timedelta(minutes=1))
    store.complete(done.id)
    later = NOW + timedelta(minutes=10)
    assert [r["text"] for r in server.take_due_data(later)["erinnerungen"]] == ["früh"]
    assert server.take_due_data(later)["erinnerungen"] == []  # nicht doppelt
    shown = server.list_data(now=later)["eintraege"]
    assert next(e for e in shown if e["text"] == "früh")["erinnert"] is True


def test_old_note_files_get_the_new_columns(tmp_path: Path) -> None:
    import sqlite3
    from contextlib import closing

    path = tmp_path / "alt.sqlite3"
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute(
            "CREATE TABLE notes (id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT NOT NULL, "
            "due TEXT, created TEXT NOT NULL, done_at TEXT)"
        )
        conn.execute("INSERT INTO notes (text, created) VALUES ('alt', '2026-11-01T10:00:00')")
    store = NoteStore(path)
    assert [n.text for n in store.entries()] == ["alt"]
    store.add("neu", None, remind_at=datetime.now(UTC) + timedelta(hours=1))
    assert store.count() == 2


def test_parse_reminders_skips_garbage() -> None:
    data = {"erinnerungen": [{"id": 1, "text": "ok", "zeitpunkt": "2026-11-12T18:00"},
                             {"id": "x"}, "kaputt"]}  # fmt: skip
    assert [r.text for r in parse_reminders(data)] == ["ok"]
    assert parse_reminders(None) == [] and parse_reminders({"erinnerungen": "nein"}) == []


# ------------------------------------------------------------------------------- Wächter
def _settings(tmp_path: Path) -> tuple[Settings, Path]:
    notes_file = tmp_path / "notizen.sqlite3"
    servers = {"notes": eval_servers(tmp_path / "w.jsonl", notes_file)["notes"]}
    return Settings(data_dir=tmp_path / "data", mcp_servers=servers), notes_file


@pytest.mark.anyio
async def test_watcher_delivers_due_reminders_to_listeners(tmp_path: Path) -> None:
    settings, notes_file = _settings(tmp_path)
    NoteStore(notes_file).add("Müll", None, remind_at=datetime.now(UTC) - timedelta(seconds=1))
    audit = AuditLog(settings.audit_db_path)
    watcher = ReminderWatcher(settings.mcp_servers, audit, poll_seconds=0.2)
    task = asyncio.create_task(watcher.run())
    try:
        with watcher.listen() as queue:
            reminder = await asyncio.wait_for(queue.get(), 15)
        assert reminder.text == "Müll"
    finally:
        task.cancel()
    import sqlite3
    from contextlib import closing

    with closing(sqlite3.connect(settings.audit_db_path)) as conn:
        rows = conn.execute("SELECT channel, tool, risk, status FROM audit").fetchall()
    assert rows == [("system", "take_due_reminders", "WRITE_LOCAL", "ok")]  # nur bei Zustellung
    assert "Müll".encode() not in settings.audit_db_path.read_bytes()


@pytest.mark.anyio
async def test_watcher_takes_nothing_while_nobody_listens(tmp_path: Path) -> None:
    settings, notes_file = _settings(tmp_path)
    store = NoteStore(notes_file)
    store.add("wartet", None, remind_at=datetime.now(UTC) - timedelta(seconds=1))
    watcher = ReminderWatcher(
        settings.mcp_servers, AuditLog(settings.audit_db_path), poll_seconds=0.1
    )
    task = asyncio.create_task(watcher.run())
    await asyncio.sleep(2.5)
    task.cancel()
    assert store.entries()[0].reminded_at is None  # nicht verloren, kommt beim nächsten Zuhörer


def test_watcher_is_off_without_reminder_servers(tmp_path: Path) -> None:
    assert not ReminderWatcher({}, AuditLog(tmp_path / "a.sqlite3")).enabled


# ------------------------------------------------------------------------------- Stream
def test_events_stream_delivers_a_reminder(tmp_path: Path) -> None:
    settings, notes_file = _settings(tmp_path)
    NoteStore(notes_file).add("Zahnarzt anrufen", None,
                              remind_at=datetime.now(UTC) - timedelta(seconds=1))  # fmt: skip
    app = create_app(
        settings, TestModel(), "test", keepalive_seconds=0.5, reminder_poll_seconds=0.2
    )
    with (
        TestClient(app, base_url="http://127.0.0.1") as client,
        client.stream("GET", "/v1/events") as response,
    ):
        events: list[tuple[str, dict[str, object]]] = []
        name = ""
        for line in response.iter_lines():
            if line.startswith("event: "):
                name = line[7:]
            elif line.startswith("data: "):
                events.append((name, json.loads(line[6:])))
                if name == "reminder":
                    break
    assert events[0] == ("ready", {"erinnerungen": True})
    assert events[-1][0] == "reminder" and events[-1][1]["text"] == "Zahnarzt anrufen"


def test_events_stream_is_guarded_by_the_token(tmp_path: Path) -> None:
    settings, _ = _settings(tmp_path)
    guarded = settings.model_copy(update={"require_api_token": True})
    app = create_app(guarded, TestModel(), "test", api_token="geheim")
    client = TestClient(app, base_url="http://127.0.0.1")
    assert client.get("/v1/events").status_code == 401


# ------------------------------------------------------------------------------- CLI-Dialog
def test_cli_dialog_prints_due_reminders(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    import time

    from jarvis_core.agent import ChatService, build_service
    from jarvis_core.cli import main as cli_main

    settings, notes_file = _settings(tmp_path)
    NoteStore(notes_file).add("Tee aufgießen", None,
                              remind_at=datetime.now(UTC) - timedelta(seconds=1))  # fmt: skip
    lines = iter(["/ende"])

    def slow_input(prompt: str = "") -> str:
        time.sleep(4)  # Zeit für den Wächter, die Erinnerung zu bringen
        return next(lines)

    def fake_build(s: Settings, audit: AuditLog) -> ChatService:
        return build_service(s, audit, TestModel(), "test")

    monkeypatch.setattr(cli_main, "REMINDER_POLL_SECONDS", 0.2)
    monkeypatch.setattr(cli_main, "load_settings", lambda: settings)
    monkeypatch.setattr(cli_main, "build_service", fake_build)
    monkeypatch.setattr("builtins.input", slow_input)
    cli_main.chat(None)
    out = capfd.readouterr().out
    assert "⏰ Erinnerung" in out and "Tee aufgießen" in out
