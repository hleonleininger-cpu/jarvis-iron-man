"""Vault-Indexer (M1.10): nur erlaubte Ordner, Änderungen erkennen, Notizen mitnehmen."""

import os
import sqlite3
from pathlib import Path

import pytest

from mcp_servers.memory import server, vault
from mcp_servers.memory.store import MemoryStore
from mcp_servers.memory.vault import chunk_text, index_all


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> MemoryStore:
    memory = MemoryStore(tmp_path / "g.sqlite3")
    monkeypatch.setattr(server, "_store", memory)
    monkeypatch.setattr(server, "_embedder", None)
    return memory


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    root = tmp_path / "Vault"
    (root / "Schule").mkdir(parents=True)
    (root / "Schule" / "mathe.md").write_text(
        "# Mathe\n\nDie Ableitung von x² ist 2x.\n", encoding="utf-8"
    )
    (root / "Schule" / "plan.txt").write_text(
        "Klausur am 12. Dezember in Raum 204.", encoding="utf-8"
    )
    (root / "bild.png").write_bytes(b"\x89PNG")
    (root / ".versteckt").mkdir()
    (root / ".versteckt" / "geheim.md").write_text("Nicht indexieren", encoding="utf-8")
    return root


def test_chunks_follow_paragraphs_and_never_exceed_the_limit() -> None:
    assert chunk_text("") == [] and chunk_text("  \n\n ") == []
    assert chunk_text("Eins\n\nZwei") == ["Eins\n\nZwei"]  # kleine Absätze bleiben zusammen
    long = " ".join(["wort"] * 600)  # ein einziger riesiger Absatz
    chunks = chunk_text(long)
    assert len(chunks) > 1 and all(len(c) <= vault.CHUNK_MAX for c in chunks)
    assert " ".join(chunks).split() == long.split()  # nichts geht verloren
    many = chunk_text("\n\n".join(f"Absatz {n} " + "x" * 300 for n in range(10)))
    assert all(len(c) <= vault.CHUNK_MAX for c in many) and len(many) > 2


def test_index_reads_only_allowed_text_files(store: MemoryStore, folder: Path) -> None:
    report = index_all(store, [folder], None, None, "m")
    assert report.files == 2 and report.changed == 2 and report.removed == 0
    assert set(store.doc_fingerprints()) == {"Vault/Schule/mathe.md", "Vault/Schule/plan.txt"}
    assert not any("geheim" in p or "png" in p for p in store.doc_fingerprints())


def test_unchanged_files_are_not_read_again_but_changes_and_deletions_are_seen(
    store: MemoryStore, folder: Path
) -> None:
    index_all(store, [folder], None, None, "m")
    again = index_all(store, [folder], None, None, "m")
    assert again.changed == 0 and again.removed == 0
    plan = folder / "Schule" / "plan.txt"
    plan.write_text("Klausur verschoben auf den 19. Dezember.", encoding="utf-8")
    os.utime(plan, ns=(1, 1))  # sicher anderer Zeitstempel
    (folder / "Schule" / "mathe.md").unlink()
    third = index_all(store, [folder], None, None, "m")
    assert third.changed == 1 and third.removed == 1
    assert [d.text for d in store.search_documents("Klausur verschoben")] == [
        "Klausur verschoben auf den 19. Dezember."
    ]
    assert store.search_documents("Ableitung") == []  # gelöschte Datei ist aus dem Index


def test_symlinks_out_of_the_folder_are_never_followed(
    store: MemoryStore, folder: Path, tmp_path: Path
) -> None:
    outside = tmp_path / "ausserhalb"
    outside.mkdir()
    (outside / "passwoerter.md").write_text("Das Passwort ist hunter2", encoding="utf-8")
    try:
        (folder / "link.md").symlink_to(outside / "passwoerter.md")
        (folder / "ordnerlink").symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("Symlinks sind hier nicht erlaubt (Windows ohne Entwicklermodus)")
    report = index_all(store, [folder], None, None, "m")
    assert report.skipped >= 1
    assert store.search_documents("hunter2 Passwort") == []
    assert not any("link" in p for p in store.doc_fingerprints())


def test_oversized_binary_and_unreadable_files_are_skipped(
    store: MemoryStore, folder: Path
) -> None:
    (folder / "gross.md").write_text("x" * (vault.MAX_FILE_BYTES + 1), encoding="utf-8")
    (folder / "binaer.txt").write_bytes(b"abc\x00def")
    (folder / "latin1.md").write_bytes("Größe".encode("latin-1"))
    report = index_all(store, [folder], None, None, "m")
    assert report.skipped == 3 and report.files == 4  # gefunden: 2 Texte, Binär- und Latin-1-Datei
    assert set(store.doc_fingerprints()) == {"Vault/Schule/mathe.md", "Vault/Schule/plan.txt"}


def _notes_db(path: Path) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE notes (id INTEGER PRIMARY KEY, text TEXT, done_at TEXT)")
        conn.executemany(
            "INSERT INTO notes (id, text, done_at) VALUES (?, ?, ?)",
            [
                (1, "Milch kaufen", None),
                (2, "Steuern abgeben", "2026-11-01T00:00:00"),
                (3, "Zahnarzt anrufen", None),
            ],
        )
    conn.close()


def test_open_notes_are_indexed_read_only(store: MemoryStore, tmp_path: Path) -> None:
    notes = tmp_path / "notizen.sqlite3"
    _notes_db(notes)
    before = notes.read_bytes()
    report = index_all(store, [], notes, None, "m")
    assert report.files == 2 and set(store.doc_fingerprints()) == {"notiz:1", "notiz:3"}  # offene
    assert notes.read_bytes() == before  # die Notizdatei bleibt unverändert
    with sqlite3.connect(notes) as conn:
        conn.execute("UPDATE notes SET done_at = '2026-11-16' WHERE id = 1")
    conn.close()
    assert index_all(store, [], notes, None, "m").removed == 1  # erledigt: aus dem Index
    assert index_all(store, [], tmp_path / "gibt-es-nicht.sqlite3", None, "m").removed == 1


def test_embeddings_are_added_within_the_budget(
    store: MemoryStore, folder: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def embed(texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0] for _ in texts]

    monkeypatch.setattr(vault, "MAX_EMBEDS_PER_RUN", 1)
    first = index_all(store, [folder], None, embed, "m")
    assert first.embedded == 1 and first.without_embedding == 1
    second = index_all(store, [folder], None, embed, "m")
    assert second.embedded == 1 and second.without_embedding == 0


def test_embedding_failure_does_not_break_indexing(store: MemoryStore, folder: Path) -> None:
    from mcp_servers.memory.embed import EmbedError

    def down(_texts: list[str]) -> list[list[float]]:
        raise EmbedError("aus")

    report = index_all(store, [folder], None, down, "m")
    assert report.changed == 2 and report.embedded == 0 and report.without_embedding == 2


# --------------------------------------------------------------------------- Werkzeuge
def test_vault_index_tool_needs_configured_folders(store: MemoryStore) -> None:
    result = server.vault_index_data()
    assert (
        result["ok"] is False
        and result["grund"] == "not_configured"
        and "--vault" in result["fehler"]
    )


def test_recall_finds_vault_text_and_names_the_source(
    store: MemoryStore, folder: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(server, "_vault_roots", [folder])
    indexed = server.vault_index_data()
    assert indexed["ok"] and "2 Dateien/Notizen geprüft" in indexed["ergebnis"]
    server.remember_data("Die Klausur-Aufsicht ist Frau Meier")
    found = server.recall_data("Wann ist die Klausur?")
    sources = [t["quelle"] for t in found["treffer"]]
    assert "Datei Vault/Schule/plan.txt" in sources and "Gedächtnis" in sources
    plan = next(t for t in found["treffer"] if t["quelle"].endswith("plan.txt"))
    assert "12. Dezember" in plan["text"]


def test_semantic_recall_over_the_vault(
    store: MemoryStore, folder: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vectors = {
        "Die Ableitung von x² ist 2x.": [1.0, 0.0],
        "Klausur am 12. Dezember in Raum 204.": [0.0, 1.0],
    }

    def embed(texts: list[str]) -> list[list[float]]:
        return [
            vectors.get(t.split("\n\n")[-1], [0.9, 0.1] if "Analysis" in t else [0.5, 0.5])
            for t in texts
        ]

    monkeypatch.setattr(server, "_embedder", embed)
    monkeypatch.setattr(server, "_embed_model", "m")
    monkeypatch.setattr(server, "_vault_roots", [folder])
    server.vault_index_data()
    found = server.recall_data("Analysis Hausaufgabe")
    assert found["suche"] == "semantisch"
    assert found["treffer"][0]["quelle"] == "Datei Vault/Schule/mathe.md"


def test_the_model_cannot_choose_a_folder() -> None:
    import inspect

    assert list(inspect.signature(server.vault_index_data).parameters) == []
