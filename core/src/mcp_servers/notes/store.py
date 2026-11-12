"""Lokale Ablage für Notizen und Aufgaben (SQLite). Nur dieser Server liest und schreibt sie."""

import sqlite3
from collections.abc import Generator
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    text TEXT NOT NULL,
    due TEXT,
    created TEXT NOT NULL,
    done_at TEXT
)
"""
# Spalten für Erinnerungen (später dazugekommen; ältere Dateien werden ergänzt).
_REMINDER_COLUMNS = ("remind_at", "reminded_at")
_FIELDS = "id, text, due, created, done_at, remind_at, reminded_at"


@dataclass(frozen=True)
class Note:
    id: int
    text: str
    due: date | None
    created: datetime
    done_at: datetime | None
    remind_at: datetime | None = None  # Zeitpunkt der Erinnerung (UTC)
    reminded_at: datetime | None = None  # wann sie zugestellt wurde

    @property
    def done(self) -> bool:
        return self.done_at is not None


def _when(value: object) -> datetime | None:
    return datetime.fromisoformat(str(value)) if value else None


def _row(row: tuple[object, ...]) -> Note:
    note_id, text, due, created, done_at, remind_at, reminded_at = row
    return Note(
        id=int(str(note_id)),
        text=str(text),
        due=date.fromisoformat(str(due)) if due else None,
        created=datetime.fromisoformat(str(created)),
        done_at=_when(done_at),
        remind_at=_when(remind_at),
        reminded_at=_when(reminded_at),
    )


class NoteStore:
    def __init__(self, path: Path) -> None:
        self._path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(_SCHEMA)
            existing = {row[1] for row in conn.execute("PRAGMA table_info(notes)")}
            for column in _REMINDER_COLUMNS:
                if column not in existing:
                    conn.execute(f"ALTER TABLE notes ADD COLUMN {column} TEXT")

    @contextmanager
    def _connect(self) -> Generator[sqlite3.Connection]:
        """Verbindung mit Commit am Ende, die IMMER geschlossen wird. (`with sqlite3.connect()`
        allein schließt nicht; unter Windows bliebe die Datei gesperrt.)"""
        with closing(sqlite3.connect(self._path, timeout=10.0)) as conn, conn:
            yield conn

    def add(
        self,
        text: str,
        due: date | None,
        now: datetime | None = None,
        remind_at: datetime | None = None,
    ) -> Note:
        created = (now or datetime.now(UTC)).isoformat(timespec="seconds")
        remind = remind_at.astimezone(UTC).isoformat(timespec="seconds") if remind_at else None
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO notes (text, due, created, remind_at) VALUES (?, ?, ?, ?)",
                (text, due.isoformat() if due else None, created, remind),
            )
            note_id = int(cur.lastrowid or 0)
        found = self.get(note_id)
        assert found is not None
        return found

    def get(self, note_id: int) -> Note | None:
        with self._connect() as conn:
            row = conn.execute(f"SELECT {_FIELDS} FROM notes WHERE id = ?", (note_id,)).fetchone()
        return _row(row) if row else None

    def entries(self, include_done: bool = False) -> list[Note]:
        """Offene (bzw. alle) Einträge: fällige zuerst nach Datum, dann die übrigen nach Alter."""
        query = f"SELECT {_FIELDS} FROM notes"
        if not include_done:
            query += " WHERE done_at IS NULL"
        query += " ORDER BY due IS NULL, due, id"
        with self._connect() as conn:
            return [_row(row) for row in conn.execute(query).fetchall()]

    def complete(self, note_id: int, now: datetime | None = None) -> Note | None:
        """Markiert als erledigt. None, wenn es den Eintrag nicht gibt."""
        stamp = (now or datetime.now(UTC)).isoformat(timespec="seconds")
        with self._connect() as conn:
            conn.execute(
                "UPDATE notes SET done_at = ? WHERE id = ? AND done_at IS NULL", (stamp, note_id)
            )
        return self.get(note_id)

    def take_due_reminders(self, now: datetime | None = None) -> list[Note]:
        """Fällige, noch nicht zugestellte Erinnerungen offener Einträge; in derselben Transaktion
        als zugestellt markiert, damit jede genau einmal kommt (auch bei zwei Abholern)."""
        stamp = (now or datetime.now(UTC)).astimezone(UTC).isoformat(timespec="seconds")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                f"SELECT {_FIELDS} FROM notes WHERE remind_at IS NOT NULL AND remind_at <= ? "
                "AND reminded_at IS NULL AND done_at IS NULL ORDER BY remind_at, id",
                (stamp,),
            ).fetchall()
            conn.executemany(
                "UPDATE notes SET reminded_at = ? WHERE id = ?", [(stamp, row[0]) for row in rows]
            )
        return [_row(row) for row in rows]

    def count(self) -> int:
        with self._connect() as conn:
            return int(conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0])
