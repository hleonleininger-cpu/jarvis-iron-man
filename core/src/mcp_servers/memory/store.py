"""Lokale Ablage für das Langzeitgedächtnis (SQLite). Nur dieser Server liest und schreibt sie.

Gespeichert wird nur, was der Nutzer ausdrücklich sagen lässt ("Merk dir ..."). Die Einträge
tragen Platz für ein Embedding (M1.8); ohne Embedding-Modell funktioniert die Stichwortsuche.
"""

import re
import sqlite3
from collections.abc import Generator
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

KINDS = ("fakt", "vorliebe", "person", "projekt", "sonstiges")
_SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    text TEXT NOT NULL,
    kind TEXT NOT NULL,
    created TEXT NOT NULL,
    embedding BLOB,
    embed_model TEXT
)
"""
_FIELDS = "id, text, kind, created"
_WORD = re.compile(r"[a-zäöüß0-9]{3,}")
# Füllwörter, die in jeder Frage vorkommen und nichts über das Thema sagen.
_STOPWORDS = frozenset(
    w.casefold()  # casefold macht aus 'ß' ein 'ss': die Füllwörter müssen genauso behandelt werden
    for w in (
        "und",
        "oder",
        "aber",
        "der",
        "die",
        "das",
        "den",
        "dem",
        "des",
        "ein",
        "eine",
        "einen",
        "einem",
        "einer",
        "ist",
        "sind",
        "war",
        "was",
        "wer",
        "wie",
        "wo",
        "wann",
        "warum",
        "wieso",
        "welche",
        "welcher",
        "welches",
        "ich",
        "du",
        "er",
        "sie",
        "es",
        "wir",
        "ihr",
        "mein",
        "meine",
        "meinen",
        "meinem",
        "dein",
        "deine",
        "hat",
        "habe",
        "haben",
        "hatte",
        "nicht",
        "noch",
        "auch",
        "mit",
        "von",
        "für",
        "auf",
        "bei",
        "nach",
        "über",
        "zum",
        "zur",
        "weißt",
        "weiss",
        "kennst",
        "gibt",
        "gibts",
        "mir",
        "mich",
        "dir",
        "dich",
        "uns",
        "sag",
        "sage",
        "sagst",
    )
)


@dataclass(frozen=True)
class Memory:
    id: int
    text: str
    kind: str
    created: datetime


def words(text: str) -> set[str]:
    """Suchbegriffe eines Textes: kleingeschrieben, ohne Füllwörter und sehr kurze Wörter."""
    return {w for w in _WORD.findall(text.casefold()) if w not in _STOPWORDS}


def _row(row: tuple[object, ...]) -> Memory:
    memory_id, text, kind, created = row
    return Memory(int(str(memory_id)), str(text), str(kind), datetime.fromisoformat(str(created)))


class MemoryStore:
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

    def add(self, text: str, kind: str, now: datetime | None = None) -> tuple[Memory, bool]:
        """Legt einen Eintrag an. Gibt (Eintrag, neu) zurück; ein gleicher Text (ohne Rücksicht
        auf Groß-/Kleinschreibung) wird nicht doppelt gespeichert."""
        wanted = text.casefold()
        for known in self.entries():
            if known.text.casefold() == wanted:
                return known, False
        stamp = (now or datetime.now(UTC)).isoformat(timespec="seconds")
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO memories (text, kind, created) VALUES (?, ?, ?)", (text, kind, stamp)
            )
            memory_id = int(cur.lastrowid or 0)
        found = self.get(memory_id)
        assert found is not None
        return found, True

    def get(self, memory_id: int) -> Memory | None:
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT {_FIELDS} FROM memories WHERE id = ?", (memory_id,)
            ).fetchone()
        return _row(row) if row else None

    def entries(self, kind: str | None = None) -> list[Memory]:
        query = f"SELECT {_FIELDS} FROM memories"
        args: tuple[str, ...] = ()
        if kind:
            query += " WHERE kind = ?"
            args = (kind,)
        with self._connect() as conn:
            return [_row(r) for r in conn.execute(query + " ORDER BY id", args).fetchall()]

    def search(self, question: str, limit: int = 5) -> list[tuple[Memory, int]]:
        """Stichwortsuche: Einträge, die Wörter der Frage enthalten, die meisten Treffer zuerst
        (bei Gleichstand die neueren)."""
        wanted = words(question)
        scored = [(m, len(wanted & words(m.text))) for m in self.entries()] if wanted else []
        hits = [(m, score) for m, score in scored if score > 0]
        hits.sort(key=lambda pair: (-pair[1], -pair[0].id))
        return hits[:limit]

    def delete(self, memory_id: int) -> bool:
        """Löscht einen Eintrag samt Embedding. False, wenn es ihn nicht gab."""
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
            return cur.rowcount > 0

    def count(self) -> int:
        with self._connect() as conn:
            return int(conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0])
