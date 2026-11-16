"""Vault-Indexer (M1.10): Textdateien aus ausdrücklich erlaubten Ordnern und offene Notizen
werden in Stücke geteilt und durchsuchbar gemacht. Es wird nur gelesen und ein Index geführt;
die Originale bleiben unverändert, und außerhalb der erlaubten Ordner wird nichts gelesen.
"""

import hashlib
import os
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from mcp_servers.memory.embed import Embedder, EmbedError
from mcp_servers.memory.store import MemoryStore

SUFFIXES = (".md", ".markdown", ".txt")
MAX_FILES = 2000
MAX_FILE_BYTES = 500_000
CHUNK_TARGET = 600  # Zeichen: ab hier wird ein Stück abgeschlossen
CHUNK_MAX = 1200
MAX_EMBEDS_PER_RUN = 300  # darüber hinaus beim nächsten Lauf (Embedding auf CPU ist langsam)


@dataclass(frozen=True)
class Report:
    files: int
    changed: int
    removed: int
    chunks: int
    embedded: int
    without_embedding: int
    skipped: int  # zu groß, nicht lesbar, Binärdatei, zu viele Dateien

    def text(self) -> str:
        return (
            f"{self.files} Dateien/Notizen geprüft, {self.changed} neu oder geändert, "
            f"{self.removed} entfernt, {self.chunks} Textstücke, {self.embedded} neu eingebettet"
        )


def chunk_text(text: str) -> list[str]:
    """Teilt an Absätzen in Stücke von etwa CHUNK_TARGET Zeichen (nie über CHUNK_MAX)."""
    chunks: list[str] = []
    current = ""
    for paragraph in (p.strip() for p in text.replace("\r\n", "\n").split("\n\n")):
        if not paragraph:
            continue
        while len(paragraph) > CHUNK_MAX:  # ein einzelner riesiger Absatz
            cut = paragraph.rfind(" ", 0, CHUNK_MAX)
            cut = cut if cut > CHUNK_MAX // 2 else CHUNK_MAX
            chunks.append(paragraph[:cut].strip())
            paragraph = paragraph[cut:].strip()
        if current and len(current) + len(paragraph) + 2 > CHUNK_MAX:
            chunks.append(current)
            current = ""
        current = f"{current}\n\n{paragraph}" if current else paragraph
        if len(current) >= CHUNK_TARGET:
            chunks.append(current)
            current = ""
    if current:
        chunks.append(current)
    return [c for c in chunks if c]


def display_key(root: Path, file: Path) -> str:
    return f"{root.name}/{file.relative_to(root).as_posix()}"


def _vault_files(roots: list[Path]) -> tuple[dict[str, Path], int]:
    """Alle indexierbaren Dateien (Anzeigename -> Pfad) und die Zahl der übersprungenen.

    Nur reguläre Dateien, deren aufgelöster Pfad wirklich unter dem erlaubten Ordner liegt (ein
    Link nach draußen wird nicht gefolgt); versteckte Ordner und Dateien bleiben außen vor.
    """
    found: dict[str, Path] = {}
    skipped = 0
    for root in roots:
        real_root = root.resolve()
        for folder, dirs, names in os.walk(real_root, followlinks=False):
            dirs[:] = sorted(d for d in dirs if not d.startswith("."))
            for name in sorted(names):
                if name.startswith(".") or not name.lower().endswith(SUFFIXES):
                    continue
                path = Path(folder) / name
                try:
                    real = path.resolve(strict=True)
                    real.relative_to(real_root)  # ValueError: Link zeigt aus dem Ordner hinaus
                    if not real.is_file() or real.stat().st_size > MAX_FILE_BYTES:
                        raise ValueError
                except (OSError, ValueError):
                    skipped += 1
                    continue
                if len(found) >= MAX_FILES:
                    skipped += 1
                    continue
                found[display_key(real_root, real)] = real
    return found, skipped


def _fingerprint(path: Path) -> str:
    info = path.stat()
    return f"{info.st_mtime_ns}:{info.st_size}"


def _open_notes(notes_file: Path) -> dict[str, str]:
    """Offene Notizen (Schlüssel 'notiz:<Nr>' -> Text), nur lesend. Fehlt die Datei: leer."""
    if not notes_file.is_file():
        return {}
    try:
        with closing(
            sqlite3.connect(f"{notes_file.as_uri()}?mode=ro", uri=True, timeout=5)
        ) as conn:
            rows = conn.execute("SELECT id, text FROM notes WHERE done_at IS NULL").fetchall()
    except sqlite3.Error:
        return {}
    return {f"notiz:{row[0]}": str(row[1]) for row in rows}


def index_all(
    store: MemoryStore,
    roots: list[Path],
    notes_file: Path | None,
    embedder: Embedder | None,
    model: str,
) -> Report:
    """Gleicht den Index mit den Ordnern und Notizen ab: Neues und Geändertes einlesen,
    Verschwundenes entfernen, fehlende Embeddings (soweit das Budget reicht) nachholen."""
    files, skipped = _vault_files(roots)
    notes = _open_notes(notes_file) if notes_file else {}
    known = store.doc_fingerprints()
    changed = 0
    for key, path in files.items():
        try:
            fingerprint = _fingerprint(path)
            if known.get(key) == fingerprint:
                continue
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            skipped += 1
            continue
        if "\x00" in text:  # Binärdatei mit Textendung
            skipped += 1
            continue
        store.replace_document("vault", key, fingerprint, chunk_text(text))
        changed += 1
    for key, text in notes.items():
        fingerprint = hashlib.sha1(text.encode()).hexdigest()  # noqa: S324 (kein Sicherheitszweck)
        if known.get(key) != fingerprint:
            store.replace_document("notiz", key, fingerprint, chunk_text(text))
            changed += 1
    gone = [path for path in known if path not in files and path not in notes]
    removed = len(gone)
    store.delete_documents(gone)
    embedded = 0
    if embedder is not None:
        pending = store.documents_without_embedding(MAX_EMBEDS_PER_RUN)
        try:
            vectors = embedder([doc.text for doc in pending]) if pending else []
        except EmbedError:
            vectors = []
        for doc, vector in zip(pending, vectors, strict=False):
            store.set_document_embedding(doc.id, vector, model)
            embedded += 1
    total, missing = store.document_counts()
    return Report(len(files) + len(notes), changed, removed, total, embedded, missing, skipped)
