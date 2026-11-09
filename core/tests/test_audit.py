import sqlite3
from pathlib import Path

from jarvis_core.audit import AuditLog


def _log(tmp_path: Path, entries: int = 3) -> tuple[AuditLog, Path]:
    path = tmp_path / "audit.sqlite3"
    log = AuditLog(path)
    for i in range(entries):
        log.record(channel="cli", model="m", tokens=i, status="ok")
    return log, path


def test_empty_chain_is_valid(tmp_path: Path) -> None:
    result = AuditLog(tmp_path / "a.sqlite3").verify_chain()
    assert result.ok and result.checked == 0


def test_valid_chain(tmp_path: Path) -> None:
    log, _ = _log(tmp_path)
    result = log.verify_chain()
    assert result.ok and result.checked == 3 and result.first_invalid_id is None


def test_modified_row_reports_first_invalid_line(tmp_path: Path) -> None:
    log, path = _log(tmp_path, 4)
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE audit SET status = 'ok-manipuliert', tokens = 99 WHERE id = 2")
    result = log.verify_chain()
    assert not result.ok
    assert result.first_invalid_id == 2


def test_deleted_middle_row_is_detected(tmp_path: Path) -> None:
    log, path = _log(tmp_path, 4)
    with sqlite3.connect(path) as conn:
        conn.execute("DELETE FROM audit WHERE id = 2")
    result = log.verify_chain()
    assert not result.ok
    assert result.first_invalid_id == 3


def test_rehashed_row_breaks_following_link(tmp_path: Path) -> None:
    log, path = _log(tmp_path, 3)
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE audit SET hash = ? WHERE id = 1", ("f" * 64,))
    result = log.verify_chain()
    assert not result.ok
    assert result.first_invalid_id == 1


def test_no_message_content_columns(tmp_path: Path) -> None:
    _, path = _log(tmp_path, 1)
    with sqlite3.connect(path) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(audit)")}
    assert columns == {
        "id", "ts", "channel", "model", "tokens", "status", "prev_hash", "hash",
        "tool", "risk", "decision",
    }  # fmt: skip


def test_m0_database_is_migrated_and_stays_valid(tmp_path: Path) -> None:
    """Eine M0-Datenbank (ohne Werkzeug-Spalten) bleibt gültig und nimmt Werkzeugzeilen auf."""
    import hashlib
    import json

    path = tmp_path / "old.sqlite3"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE audit (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, "
        "channel TEXT NOT NULL, model TEXT NOT NULL, tokens INTEGER, status TEXT NOT NULL, "
        "prev_hash TEXT NOT NULL, hash TEXT NOT NULL)"
    )
    fields = ["0" * 64, "2026-01-01T00:00:00.000+00:00", "cli", "m", 5, "ok"]
    digest = hashlib.sha256(json.dumps(fields, separators=(",", ":")).encode()).hexdigest()
    conn.execute(
        "INSERT INTO audit (ts, channel, model, tokens, status, prev_hash, hash) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (fields[1], "cli", "m", 5, "ok", fields[0], digest),
    )
    conn.commit()
    conn.close()

    log = AuditLog(path)
    assert log.verify_chain().ok
    log.record_tool_call(
        channel="cli", model="m", tool="get_weather", risk="READ", decision="ALLOW", status="ok"
    )
    result = log.verify_chain()
    assert result.ok and result.checked == 2


def test_tampered_tool_row_is_detected(tmp_path: Path) -> None:
    log, path = _log(tmp_path, 1)
    log.record_tool_call(
        channel="cli", model="m", tool="get_weather", risk="READ", decision="ALLOW", status="ok"
    )
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE audit SET decision = 'DENY' WHERE id = 2")
    assert log.verify_chain().first_invalid_id == 2
