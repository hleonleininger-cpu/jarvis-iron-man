"""Lokale Ablage für Ideen und Aufträge (SQLite). Nur dieser Server liest und schreibt sie."""

import sqlite3
from collections.abc import Generator
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

IDEA, LEAD = "idee", "auftrag"
IDEA_STATUS = ("neu", "in_arbeit", "veroeffentlicht", "verworfen")
LEAD_STATUS = ("offen", "angebot", "gewonnen", "bezahlt", "abgelehnt")
_SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    detail TEXT,
    category TEXT,
    status TEXT NOT NULL,
    due TEXT,
    amount_cents INTEGER,
    created TEXT NOT NULL,
    updated TEXT NOT NULL
)
"""
_FIELDS = "id, kind, title, detail, category, status, due, amount_cents, created, updated"


@dataclass(frozen=True)
class Item:
    id: int
    kind: str
    title: str
    detail: str | None
    category: str | None
    status: str
    due: date | None
    amount_cents: int | None
    created: datetime
    updated: datetime


def _row(row: tuple[object, ...]) -> Item:
    item_id, kind, title, detail, category, status, due, cents, created, updated = row
    return Item(
        id=int(str(item_id)),
        kind=str(kind),
        title=str(title),
        detail=str(detail) if detail else None,
        category=str(category) if category else None,
        status=str(status),
        due=date.fromisoformat(str(due)) if due else None,
        amount_cents=int(str(cents)) if cents is not None else None,
        created=datetime.fromisoformat(str(created)),
        updated=datetime.fromisoformat(str(updated)),
    )


class ProjectStore:
    def __init__(self, path: Path) -> None:
        self._path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(_SCHEMA)

    @contextmanager
    def _connect(self) -> Generator[sqlite3.Connection]:
        """Verbindung mit Commit am Ende, die IMMER geschlossen wird (Windows hält sonst die
        Datei fest)."""
        with closing(sqlite3.connect(self._path, timeout=10.0)) as conn, conn:
            yield conn

    def add(
        self,
        kind: str,
        title: str,
        detail: str | None,
        category: str | None,
        due: date | None,
        amount_cents: int | None,
        now: datetime | None = None,
    ) -> Item:
        stamp = (now or datetime.now(UTC)).isoformat(timespec="seconds")
        status = IDEA_STATUS[0] if kind == IDEA else LEAD_STATUS[0]
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO items (kind, title, detail, category, status, due, amount_cents, "
                "created, updated) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (kind, title, detail, category, status, due.isoformat() if due else None,
                 amount_cents, stamp, stamp),
            )  # fmt: skip
            item_id = int(cur.lastrowid or 0)
        found = self.get(item_id)
        assert found is not None
        return found

    def get(self, item_id: int) -> Item | None:
        with self._connect() as conn:
            row = conn.execute(f"SELECT {_FIELDS} FROM items WHERE id = ?", (item_id,)).fetchone()
        return _row(row) if row else None

    def entries(self, kind: str) -> list[Item]:
        """Einträge einer Art: Frist zuerst (soweit vorhanden), dann nach Anlage."""
        query = f"SELECT {_FIELDS} FROM items WHERE kind = ? ORDER BY due IS NULL, due, id"
        with self._connect() as conn:
            return [_row(row) for row in conn.execute(query, (kind,)).fetchall()]

    def set_status(self, item_id: int, status: str, now: datetime | None = None) -> Item | None:
        stamp = (now or datetime.now(UTC)).isoformat(timespec="seconds")
        with self._connect() as conn:
            conn.execute(
                "UPDATE items SET status = ?, updated = ? WHERE id = ?", (status, stamp, item_id)
            )
        return self.get(item_id)

    def count(self) -> int:
        with self._connect() as conn:
            return int(conn.execute("SELECT COUNT(*) FROM items").fetchone()[0])
