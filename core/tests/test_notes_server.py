"""Notiz-Server (ADR-012): Ablage, Werkzeuge, Vorschau, Weg über Agent + Bestätigung."""

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic_ai.models.test import TestModel

from jarvis_core.agent import ConfirmRequested, build_service
from jarvis_core.audit import AuditLog
from jarvis_core.config import Settings
from jarvis_core.evals.runner import eval_servers
from mcp_servers.notes import server
from mcp_servers.notes.store import NoteStore

NOW = datetime(2026, 11, 12, 9, 0, tzinfo=UTC)


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> NoteStore:
    notes = NoteStore(tmp_path / "notizen.sqlite3")
    monkeypatch.setattr(server, "store", lambda: notes)
    return notes


def test_add_list_complete(store: NoteStore) -> None:
    first = server.add_data("Milch kaufen", None)
    second = server.add_data("Steuer abgeben", "2026-11-10")
    third = server.add_data("Zahnarzt anrufen", "2026-11-20")
    assert first["ok"] and first["gespeichert"] == "Merken: Milch kaufen"
    assert second["gespeichert"] == "Merken: Steuer abgeben (fällig 10.11.2026)"

    listed = server.list_data(now=NOW)
    assert [e["text"] for e in listed["eintraege"]] == [
        "Steuer abgeben", "Zahnarzt anrufen", "Milch kaufen",
    ]  # fällige zuerst nach Datum, dann die übrigen  # fmt: skip
    assert listed["eintraege"][0]["ueberfaellig"] is True
    assert "ueberfaellig" not in listed["eintraege"][1]

    done = server.complete_data(third["id"])
    assert done == {"ok": True, "erledigt": "Zahnarzt anrufen", "id": third["id"]}
    assert server.list_data(now=NOW)["anzahl"] == 2
    everything = server.list_data(erledigte_einbeziehen=True, now=NOW)
    assert everything["anzahl"] == 3
    assert any("erledigt_am" in e for e in everything["eintraege"])


def test_filter_is_case_insensitive(store: NoteStore) -> None:
    server.add_data("Milch kaufen", None)
    server.add_data("Brot holen", None)
    assert [e["text"] for e in server.list_data("MILCH")["eintraege"]] == ["Milch kaufen"]


@pytest.mark.parametrize(
    ("text", "faellig", "message"),
    [
        ("   ", None, "braucht einen Text"),
        ("x" * 1001, None, "höchstens 1000"),
        ("Milch", "morgen", "Ungültiges Datum"),
        ("Milch", "9999-12-31", "nicht plausibel"),
    ],
)
def test_invalid_input_writes_nothing(
    store: NoteStore, text: str, faellig: str | None, message: str
) -> None:
    for result in (server.preview_add_data(text, faellig), server.add_data(text, faellig)):
        assert result["ok"] is False and message in result["fehler"]
    assert store.count() == 0


def test_previews_write_nothing(store: NoteStore) -> None:
    preview = server.preview_add_data("Milch kaufen", "2026-11-13")
    assert preview == {
        "ok": True, "vorschau": "Merken: Milch kaufen (fällig 13.11.2026)", "frage": "Speichern?",
    }  # fmt: skip
    assert store.count() == 0
    note_id = server.add_data("Milch kaufen", None)["id"]
    assert (
        server.preview_complete_data(note_id)["vorschau"] == "Als erledigt markieren: Milch kaufen"
    )
    assert server.list_data()["anzahl"] == 1  # Vorschau hakt nichts ab


def test_complete_unknown_or_twice(store: NoteStore) -> None:
    assert server.complete_data(99)["grund"] == "not_found"
    note_id = server.add_data("Milch", None)["id"]
    server.complete_data(note_id)
    assert server.complete_data(note_id)["grund"] == "already_done"
    assert server.preview_complete_data(note_id)["grund"] == "already_done"


def test_list_says_when_it_truncates(store: NoteStore) -> None:
    for i in range(server.MAX_LISTED + 5):
        store.add(f"Eintrag {i}", None)
    result = server.list_data()
    assert result["anzahl"] == 55 and len(result["eintraege"]) == 50
    assert "50 von 55" in result["hinweis"]


# ------------------------------------------------------------------- über Agent und Policy
@pytest.mark.anyio
@pytest.mark.parametrize("approve", [True, False])
async def test_add_note_asks_first_and_writes_only_on_yes(tmp_path: Path, approve: bool) -> None:
    notes_file = tmp_path / "notizen.sqlite3"
    servers = {"notes": eval_servers(tmp_path / "w.jsonl", notes_file)["notes"]}
    settings = Settings(data_dir=tmp_path / "data", mcp_servers=servers)
    service = build_service(
        settings, AuditLog(settings.audit_db_path), TestModel(call_tools=["add_note"]), "test"
    )
    previews: list[str] = []
    async for event in service.stream("Merk dir: Milch kaufen", channel="cli"):
        if isinstance(event, ConfirmRequested):
            assert not notes_file.exists() or NoteStore(notes_file).count() == 0
            previews.append(event.preview)
            service.broker.resolve(event.id, approve)
    assert len(previews) == 1 and previews[0].startswith("Merken: ")
    assert NoteStore(notes_file).count() == (1 if approve else 0)
    with sqlite3.connect(settings.audit_db_path) as conn:
        rows = conn.execute("SELECT tool, decision FROM audit WHERE tool IS NOT NULL").fetchall()
    assert rows[0] == ("add_note", "CONFIRM")
    assert b"Milch" not in settings.audit_db_path.read_bytes()  # Inhalte nie im Audit
