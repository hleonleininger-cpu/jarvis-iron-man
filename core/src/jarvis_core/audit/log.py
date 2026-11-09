"""Audit-Log: SQLite, nur anhängen, jede Zeile trägt den SHA-256 der vorherigen.

Gespeichert werden ausschließlich Metadaten (Zeit, Kanal, Modell, Tokenzahl, Status),
niemals Nachrichteninhalte.
"""

import hashlib
import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

GENESIS_HASH = "0" * 64

_SCHEMA = """
CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    channel TEXT NOT NULL,
    model TEXT NOT NULL,
    tokens INTEGER,
    status TEXT NOT NULL,
    prev_hash TEXT NOT NULL,
    hash TEXT NOT NULL
)
"""


@dataclass(frozen=True)
class VerifyResult:
    ok: bool
    checked: int
    first_invalid_id: int | None = None
    reason: str | None = None


def _compute_hash(
    prev_hash: str, ts: str, channel: str, model: str, tokens: int | None, status: str
) -> str:
    payload = json.dumps(
        [prev_hash, ts, channel, model, tokens, status], ensure_ascii=True, separators=(",", ":")
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class AuditLog:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._path, isolation_level=None, timeout=10.0)

    def record(self, *, channel: str, model: str, tokens: int | None, status: str) -> int:
        """Hängt einen Eintrag an und gibt dessen Zeilen-ID zurück."""
        ts = datetime.now(UTC).isoformat(timespec="milliseconds")
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute("SELECT hash FROM audit ORDER BY id DESC LIMIT 1").fetchone()
                prev_hash = str(row[0]) if row else GENESIS_HASH
                new_hash = _compute_hash(prev_hash, ts, channel, model, tokens, status)
                cur = conn.execute(
                    "INSERT INTO audit (ts, channel, model, tokens, status, prev_hash, hash) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (ts, channel, model, tokens, status, prev_hash, new_hash),
                )
                conn.execute("COMMIT")
                return int(cur.lastrowid or 0)
            except BaseException:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise
            finally:
                conn.close()

    def verify_chain(self) -> VerifyResult:
        """Prüft die Kette von vorn; meldet die erste ungültige Zeile."""
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT id, ts, channel, model, tokens, status, prev_hash, hash "
                "FROM audit ORDER BY id"
            ).fetchall()
        finally:
            conn.close()

        expected_prev = GENESIS_HASH
        expected_id = rows[0][0] if rows else 1
        for count, (row_id, ts, channel, model, tokens, status, prev_hash, row_hash) in enumerate(
            rows
        ):
            if count == 0 and row_id != 1:
                return VerifyResult(False, count, row_id, "Vorherige Einträge fehlen")
            if row_id != expected_id:
                return VerifyResult(False, count, row_id, "Lücke in der Zeilenfolge")
            if prev_hash != expected_prev:
                return VerifyResult(False, count, row_id, "Verweis auf Vorgänger stimmt nicht")
            if _compute_hash(prev_hash, ts, channel, model, tokens, status) != row_hash:
                return VerifyResult(False, count, row_id, "Inhalt passt nicht zum Hash")
            expected_prev = row_hash
            expected_id = row_id + 1
        return VerifyResult(True, len(rows))
