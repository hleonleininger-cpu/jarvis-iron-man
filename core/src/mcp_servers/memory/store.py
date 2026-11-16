"""Lokale Ablage für das Langzeitgedächtnis (SQLite). Nur dieser Server liest und schreibt sie.

Gespeichert wird nur, was der Nutzer ausdrücklich sagen lässt ("Merk dir ..."). Die Einträge
tragen Platz für ein Embedding (M1.8); ohne Embedding-Modell funktioniert die Stichwortsuche.
"""

import math
import re
import sqlite3
import struct
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
_DOCUMENTS = """
CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    path TEXT NOT NULL,
    chunk INTEGER NOT NULL,
    text TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    embedding BLOB,
    embed_model TEXT
)
"""
_DOC_FIELDS = "id, source, path, chunk, text"
_FIELDS = "id, text, kind, created"
# Das Embedding steckt als float32-Folge im BLOB (Format von sqlite-vec).
MIN_SIMILARITY = 0.5  # darunter gilt ein Treffer der semantischen Suche als nicht passend
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


def pack(vector: list[float]) -> bytes:
    return struct.pack(f"<{len(vector)}f", *vector)


def unpack(blob: bytes) -> list[float]:
    return list(struct.unpack(f"<{len(blob) // 4}f", blob))


def cosine(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        return 0.0
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return sum(x * y for x, y in zip(a, b, strict=True)) / norm if norm else 0.0


@dataclass(frozen=True)
class Document:
    """Ein Textstück aus dem Vault (Datei) oder aus den Notizen. Nur ein Index, kein Original."""

    id: int
    source: str  # "vault" | "notiz"
    path: str  # "<Ordnername>/<Datei>" bzw. "notiz:<Nummer>"
    chunk: int
    text: str


@dataclass(frozen=True)
class Memory:
    id: int
    text: str
    kind: str
    created: datetime


def words(text: str) -> set[str]:
    """Suchbegriffe eines Textes: kleingeschrieben, ohne Füllwörter und sehr kurze Wörter."""
    return {w for w in _WORD.findall(text.casefold()) if w not in _STOPWORDS}


def _load_vec(conn: sqlite3.Connection) -> bool:
    """Lädt sqlite-vec in die Verbindung. False, wenn das auf diesem Rechner nicht geht."""
    try:
        import sqlite_vec  # pyright: ignore[reportMissingTypeStubs]

        conn.enable_load_extension(True)
        sqlite_vec.load(conn)  # pyright: ignore[reportUnknownMemberType]
        conn.enable_load_extension(False)
    except (ImportError, AttributeError, sqlite3.Error):
        return False
    return True


def _similarity(conn: sqlite3.Connection, use_vec: bool, vector: list[float], blob: bytes) -> float:
    """Kosinus-Ähnlichkeit (1 = gleiche Richtung): sqlite-vec, sonst Python."""
    if use_vec:
        distance = conn.execute(
            "SELECT vec_distance_cosine(?, ?)", (pack(vector), blob)
        ).fetchone()[0]
        return 1.0 - float(distance) if distance is not None else 0.0
    return cosine(vector, unpack(blob))


def _row(row: tuple[object, ...]) -> Memory:
    memory_id, text, kind, created = row
    return Memory(int(str(memory_id)), str(text), str(kind), datetime.fromisoformat(str(created)))


class MemoryStore:
    def __init__(self, path: Path) -> None:
        self._path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(_SCHEMA)
            conn.execute(_DOCUMENTS)

    @contextmanager
    def _connect(self) -> Generator[sqlite3.Connection]:
        """Verbindung mit Commit am Ende, die IMMER geschlossen wird (Windows hält sonst die
        Datei fest)."""
        with closing(sqlite3.connect(self._path, timeout=10.0)) as conn, conn:
            # Gelöschtes wird überschrieben statt nur freigegeben: sonst stünde ein "vergessener"
            # Text weiter in der Datei (M1.11, "Vergiss das" muss nachweisbar sein).
            conn.execute("PRAGMA secure_delete = ON")
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

    def set_embedding(self, memory_id: int, vector: list[float], model: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE memories SET embedding = ?, embed_model = ? WHERE id = ?",
                (pack(vector), model, memory_id),
            )

    def needing_embedding(self, model: str, limit: int) -> list[Memory]:
        """Einträge ohne Embedding oder mit dem eines anderen (früheren) Modells."""
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT {_FIELDS} FROM memories WHERE embedding IS NULL OR embed_model IS NOT ? "
                "ORDER BY id LIMIT ?",
                (model, limit),
            ).fetchall()
        return [_row(r) for r in rows]

    def without_embedding(self) -> list[Memory]:
        """Einträge, die (noch) kein Embedding haben, z. B. weil das Modell fehlte."""
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT {_FIELDS} FROM memories WHERE embedding IS NULL ORDER BY id"
            ).fetchall()
        return [_row(r) for r in rows]

    def semantic_search(
        self, vector: list[float], model: str, limit: int = 5
    ) -> tuple[list[tuple[Memory, float]], int]:
        """(Treffer mit Ähnlichkeit, Zahl der verglichenen Einträge). Verglichen werden nur
        Einträge, die mit demselben Modell eingebettet wurden (sonst sind Vektoren unvergleichbar).
        sqlite-vec rechnet, wenn es sich laden lässt; sonst rechnet Python (gleiches Ergebnis)."""
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT {_FIELDS}, embedding FROM memories "
                "WHERE embedding IS NOT NULL AND embed_model = ?",
                (model,),
            ).fetchall()
            compared = len(rows)
            scored: list[tuple[Memory, float]] = []
            use_vec = _load_vec(conn)
            for row in rows:
                scored.append((_row(row[:4]), _similarity(conn, use_vec, vector, bytes(row[4]))))
        scored = [pair for pair in scored if pair[1] >= MIN_SIMILARITY]
        scored.sort(key=lambda pair: (-pair[1], -pair[0].id))
        return scored[:limit], compared

    # ----------------------------------------------------------------------- Dokumente (M1.10)
    def doc_fingerprints(self) -> dict[str, str]:
        """Pfad -> Fingerabdruck der zuletzt indexierten Fassung (um Änderungen zu erkennen)."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT path, MIN(fingerprint) FROM documents GROUP BY path"
            ).fetchall()
        return {str(path): str(fingerprint) for path, fingerprint in rows}

    def replace_document(self, source: str, path: str, fingerprint: str, chunks: list[str]) -> None:
        """Ersetzt alle Stücke eines Pfads durch die neuen (ohne Embedding, das kommt danach)."""
        with self._connect() as conn:
            conn.execute("DELETE FROM documents WHERE path = ?", (path,))
            conn.executemany(
                "INSERT INTO documents (source, path, chunk, text, fingerprint) "
                "VALUES (?, ?, ?, ?, ?)",
                [(source, path, number, text, fingerprint) for number, text in enumerate(chunks)],
            )

    def delete_documents(self, paths: list[str]) -> int:
        with self._connect() as conn:
            return sum(
                conn.execute("DELETE FROM documents WHERE path = ?", (path,)).rowcount
                for path in paths
            )

    def documents_without_embedding(self, limit: int) -> list[Document]:
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT {_DOC_FIELDS} FROM documents WHERE embedding IS NULL ORDER BY id LIMIT ?",
                (limit,),
            ).fetchall()
        return [Document(*row) for row in rows]

    def set_document_embedding(self, doc_id: int, vector: list[float], model: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE documents SET embedding = ?, embed_model = ? WHERE id = ?",
                (pack(vector), model, doc_id),
            )

    def search_documents(self, question: str, limit: int = 5) -> list[Document]:
        """Stichwortsuche über die Dokumentstücke, die meisten Treffer zuerst."""
        wanted = words(question)
        if not wanted:
            return []
        with self._connect() as conn:
            rows = conn.execute(f"SELECT {_DOC_FIELDS} FROM documents").fetchall()
        scored = [(Document(*row), len(wanted & words(str(row[4])))) for row in rows]
        hits = [(doc, score) for doc, score in scored if score > 0]
        hits.sort(key=lambda pair: (-pair[1], pair[0].id))
        return [doc for doc, _score in hits[:limit]]

    def semantic_documents(
        self, vector: list[float], model: str, limit: int = 5
    ) -> list[tuple[Document, float]]:
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT {_DOC_FIELDS}, embedding FROM documents "
                "WHERE embedding IS NOT NULL AND embed_model = ?",
                (model,),
            ).fetchall()
            use_vec = _load_vec(conn)
            scored = [
                (Document(*row[:5]), _similarity(conn, use_vec, vector, bytes(row[5])))
                for row in rows
            ]
        scored = [pair for pair in scored if pair[1] >= MIN_SIMILARITY]
        scored.sort(key=lambda pair: (-pair[1], pair[0].id))
        return scored[:limit]

    def document_counts(self) -> tuple[int, int]:
        """(Stücke, davon ohne Embedding)."""
        with self._connect() as conn:
            total = int(conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0])
            missing = int(
                conn.execute("SELECT COUNT(*) FROM documents WHERE embedding IS NULL").fetchone()[0]
            )
        return total, missing

    def delete(self, memory_id: int) -> bool:
        """Löscht einen Eintrag samt Embedding. False, wenn es ihn nicht gab."""
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
            return cur.rowcount > 0

    def count(self) -> int:
        with self._connect() as conn:
            return int(conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0])
