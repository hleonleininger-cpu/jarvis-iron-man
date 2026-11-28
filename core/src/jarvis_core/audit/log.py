"""Audit-Log: SQLite, nur anhängen, jede Zeile trägt den SHA-256 der vorherigen.

Gespeichert werden ausschließlich Metadaten (Zeit, Kanal, Modell, Tokenzahl, Status; bei
Werkzeugaufrufen Werkzeugname, Risikoklasse, Entscheidung), niemals Nachrichten- oder
Argumentinhalte.
"""

import hashlib
import json
import sqlite3
import threading
from collections.abc import Iterable
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

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
    hash TEXT NOT NULL,
    tool TEXT,
    risk TEXT,
    decision TEXT
)
"""

# Spalten, die erst mit M1a dazukamen (Werkzeugaufrufe); ältere Datenbanken werden ergänzt.
_TOOL_COLUMNS = ("tool", "risk", "decision")


@dataclass(frozen=True)
class VerifyResult:
    ok: bool
    checked: int
    first_invalid_id: int | None = None
    reason: str | None = None


def _compute_hash(
    prev_hash: str,
    ts: str,
    channel: str,
    model: str,
    tokens: int | None,
    status: str,
    tool: str | None = None,
    risk: str | None = None,
    decision: str | None = None,
) -> str:
    fields: list[object] = [prev_hash, ts, channel, model, tokens, status]
    if tool is not None or risk is not None or decision is not None:
        # Chat-Zeilen behalten die M0-Hashform, damit bestehende Ketten gültig bleiben.
        fields += [tool, risk, decision]
    payload = json.dumps(fields, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class AuditError(Exception):
    """Audit-Log nicht nutzbar (beschädigt, gesperrt, nicht schreibbar). Ohne Audit kein Betrieb."""


class AuditLog:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            self._init_schema()
        except (sqlite3.DatabaseError, OSError) as exc:
            raise AuditError(
                f"Das Audit-Log {path} ist nicht nutzbar ({exc}). Ohne Audit-Log arbeitet JARVIS "
                "nicht; Datei prüfen oder umbenennen (dann wird ein neues angelegt)."
            ) from None

    def _init_schema(self) -> None:
        with closing(self._connect()) as conn:  # `with conn` allein schließt nicht
            conn.execute(_SCHEMA)
            existing = {row[1] for row in conn.execute("PRAGMA table_info(audit)")}
            for column in _TOOL_COLUMNS:
                if column not in existing:
                    conn.execute(f"ALTER TABLE audit ADD COLUMN {column} TEXT")

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._path, isolation_level=None, timeout=10.0)

    def record_tool_call(
        self, *, channel: str, model: str, tool: str, risk: str, decision: str, status: str
    ) -> int:
        """Werkzeugaufruf: nur Name, Risikoklasse, Entscheidung und Ergebnis, keine Argumente."""
        return self.record(
            channel=channel,
            model=model,
            tokens=None,
            status=status,
            tool=tool,
            risk=risk,
            decision=decision,
        )

    def record(
        self,
        *,
        channel: str,
        model: str,
        tokens: int | None,
        status: str,
        tool: str | None = None,
        risk: str | None = None,
        decision: str | None = None,
    ) -> int:
        """Hängt einen Eintrag an und gibt dessen Zeilen-ID zurück."""
        ts = datetime.now(UTC).isoformat(timespec="milliseconds")
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute("SELECT hash FROM audit ORDER BY id DESC LIMIT 1").fetchone()
                prev_hash = str(row[0]) if row else GENESIS_HASH
                new_hash = _compute_hash(
                    prev_hash, ts, channel, model, tokens, status, tool, risk, decision
                )
                cur = conn.execute(
                    "INSERT INTO audit (ts, channel, model, tokens, status, prev_hash, hash, "
                    "tool, risk, decision) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (ts, channel, model, tokens, status, prev_hash, new_hash, tool, risk, decision),
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
            return self._verify_rows(
                conn.execute(
                    "SELECT id, ts, channel, model, tokens, status, prev_hash, hash, "
                    "tool, risk, decision FROM audit ORDER BY id"
                )
            )
        finally:
            conn.close()

    @staticmethod
    def _verify_rows(rows: Iterable[tuple[Any, ...]]) -> VerifyResult:
        """Zeile für Zeile, ohne das ganze Log in den Speicher zu laden."""
        expected_prev = GENESIS_HASH
        expected_id = 1
        checked = 0
        for row in rows:
            (row_id, ts, channel, model, tokens, status, prev_hash, row_hash) = row[:8]
            tool, risk, decision = row[8:]
            if checked == 0 and row_id != 1:
                return VerifyResult(False, checked, row_id, "Vorherige Einträge fehlen")
            if row_id != expected_id:
                return VerifyResult(False, checked, row_id, "Lücke in der Zeilenfolge")
            if prev_hash != expected_prev:
                return VerifyResult(False, checked, row_id, "Verweis auf Vorgänger stimmt nicht")
            if (
                _compute_hash(prev_hash, ts, channel, model, tokens, status, tool, risk, decision)
                != row_hash
            ):
                return VerifyResult(False, checked, row_id, "Inhalt passt nicht zum Hash")
            expected_prev = row_hash
            expected_id = row_id + 1
            checked += 1
        return VerifyResult(True, checked)
