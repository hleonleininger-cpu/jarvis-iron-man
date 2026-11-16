"""Gedächtnis-Server (M1.7, M1.9): merken, finden, vergessen, Tagesjournal. Nur tmp_path."""

import sqlite3
from datetime import datetime
from pathlib import Path

import pytest

from mcp_servers.memory import server
from mcp_servers.memory.store import MemoryStore, words

NOW = datetime(2026, 11, 16, 14, 30, tzinfo=server.TZ)


@pytest.fixture(autouse=True)
def _files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(server, "_store", MemoryStore(tmp_path / "g.sqlite3"))
    monkeypatch.setattr(server, "_journal_dir", tmp_path / "journal")


def test_remember_and_recall_by_keywords() -> None:
    assert server.remember_data("  Mein  Lieblingsverein   ist der BVB ", "Vorliebe") == {
        "ok": True, "gespeichert": "Gemerkt.", "id": 1, "text": "Mein Lieblingsverein ist der BVB",
    }  # fmt: skip
    server.remember_data("Die Mathe-Klausur ist am 12. Dezember", "projekt")
    found = server.recall_data("Welcher Verein ist mein Lieblingsverein?")
    assert found["ok"] and found["suche"] == "Stichwortsuche" and found["anzahl"] == 1
    assert found["treffer"][0]["text"] == "Mein Lieblingsverein ist der BVB"
    assert found["treffer"][0]["art"] == "vorliebe"
    nothing = server.recall_data("Wie heißt mein Hund?")
    assert nothing["anzahl"] == 0 and "nichts gespeichert" in nothing["hinweis"]


def test_the_same_text_is_not_stored_twice() -> None:
    first = server.remember_data("Ich wohne in Dortmund")
    again = server.remember_data("ich WOHNE in dortmund")
    assert again["gespeichert"] == "Das wusste ich schon." and again["id"] == first["id"]
    assert server.store().count() == 1


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (("",), "Es fehlt der Text"),
        (("x" * 1001,), "höchstens 1000"),
        (("a", "kaputt"), "Unbekannte Art"),
    ],
)
def test_invalid_memories_are_rejected_without_saving(args: tuple[str, ...], message: str) -> None:
    result = server.remember_data(*args)
    assert result["ok"] is False and message in result["fehler"]
    assert server.store().count() == 0


def test_best_match_first_and_at_most_five() -> None:
    for number in range(8):
        server.remember_data(f"Handball Training Nummer {number}")
    server.remember_data("Handball Spiel Samstag Sporthalle")
    result = server.recall_data("Handball Spiel Sporthalle")
    assert (
        result["anzahl"] == 5
        and result["treffer"][0]["text"] == "Handball Spiel Samstag Sporthalle"
    )


def test_stopwords_alone_find_nothing() -> None:
    server.remember_data("Der Schlüssel liegt im Keller")
    assert server.recall_data("Was ist das?")["anzahl"] == 0
    assert words("Was weißt du über den BVB?") == {"bvb"}


def test_list_filters_by_kind_and_limits() -> None:
    server.remember_data("Eins", "fakt")
    server.remember_data("Zwei", "person")
    assert [e["text"] for e in server.list_data("person")["eintraege"]] == ["Zwei"]
    assert server.list_data("kaputt")["ok"] is False
    for number in range(server.MAX_LISTED + 3):
        server.remember_data(f"Eintrag {number}")
    result = server.list_data()
    assert len(result["eintraege"]) == server.MAX_LISTED and "Nur die neuesten" in result["hinweis"]


def test_forget_previews_then_deletes_for_good() -> None:
    server.remember_data("Geheimzahl vom Fahrradschloss ist 4711")
    preview = server.preview_forget_data(1)
    assert preview["vorschau"].startswith("Aus dem Gedächtnis löschen (endgültig): Geheimzahl")
    assert preview["frage"] == "Vergessen?" and server.store().count() == 1  # Vorschau löscht nie
    done = server.forget_data(1)
    assert done["ok"] and done["eintraege_vorher"] == 1 and done["eintraege_nachher"] == 0
    assert server.recall_data("Fahrradschloss Geheimzahl")["anzahl"] == 0  # auch nicht mehr findbar
    assert server.forget_data(1)["grund"] == "not_found"
    assert server.preview_forget_data(1)["grund"] == "not_found"


def test_forgotten_text_is_physically_gone_from_the_database_file(tmp_path: Path) -> None:
    server.remember_data("Streng vertraulicher Satz XYZZY")
    server.forget_data(1)
    with sqlite3.connect(tmp_path / "g.sqlite3") as conn:
        conn.execute("VACUUM")
    assert b"XYZZY" not in (tmp_path / "g.sqlite3").read_bytes()


# --------------------------------------------------------------------------- Journal
def test_journal_appends_entries_with_time(tmp_path: Path) -> None:
    first = server.journal_add_data("Mathe gelernt", NOW)
    server.journal_add_data("Handballtraining", NOW.replace(hour=18, minute=5))
    assert first["eingetragen"] == "16.11.2026 14:30"
    path = tmp_path / "journal" / "2026-11-16.md"
    assert path.read_text(encoding="utf-8") == (
        "# Journal 2026-11-16\n\n- 14:30 Mathe gelernt\n- 18:05 Handballtraining\n"
    )
    read = server.journal_read_data("heute", NOW)
    assert read["tag"] == "2026-11-16" and "Mathe gelernt" in read["inhalt"]
    assert server.journal_read_data("2026-11-16", NOW)["inhalt"] == read["inhalt"]


def test_journal_read_other_days_and_errors() -> None:
    assert "keinen Eintrag" in server.journal_read_data("gestern", NOW)["hinweis"]
    for bad in ("../../etc/passwd", "morgen", "2026-13-40", "9999-01-01", "0001-01-01"):
        result = server.journal_read_data(bad, NOW)
        assert (
            result["ok"] is False and "Tag" in result["fehler"] or "plausibel" in result["fehler"]
        )
    assert server.journal_add_data("  ", NOW)["ok"] is False


def test_long_journal_is_shortened_when_read(tmp_path: Path) -> None:
    for number in range(200):
        server.journal_add_data(f"Eintrag {number} " + "x" * 40, NOW)
    read = server.journal_read_data("heute", NOW)
    assert len(read["inhalt"]) == server.MAX_JOURNAL_SHOWN and "Nur die letzten" in read["hinweis"]
    assert "Eintrag 199" in read["inhalt"]


# --------------------------------------------------------------------------- Embeddings (M1.8)
VECTORS = {
    "mein lieblingsverein ist der bvb": [1.0, 0.1, 0.0],
    "die mathe-klausur ist am 12. dezember": [0.0, 1.0, 0.1],
    "welcher fussballclub gefällt mir?": [
        0.9,
        0.2,
        0.0,
    ],  # casefold macht aus ß ss  # ähnlich zum Verein, ohne gleiches Wort
    "was kostet ein flug?": [0.0, 0.0, 1.0],
}


def fake_embedder(texts: list[str]) -> list[list[float]]:
    return [VECTORS.get(text.casefold(), [0.3, 0.3, 0.3]) for text in texts]


@pytest.fixture
def embeddings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(server, "_embedder", fake_embedder)
    monkeypatch.setattr(server, "_embed_model", "test-modell")


def test_semantic_recall_finds_synonyms_without_a_shared_word(embeddings: None) -> None:
    server.remember_data("Mein Lieblingsverein ist der BVB")
    server.remember_data("Die Mathe-Klausur ist am 12. Dezember")
    found = server.recall_data("Welcher Fußballclub gefällt mir?")
    assert found["suche"] == "semantisch"
    assert [t["text"] for t in found["treffer"]] == ["Mein Lieblingsverein ist der BVB"]
    # Ohne Embeddings fände die Stichwortsuche hier nichts:
    assert server.store().search("Welcher Fußballclub gefällt mir?") == []


def test_unrelated_question_finds_nothing_semantically(embeddings: None) -> None:
    server.remember_data("Mein Lieblingsverein ist der BVB")
    found = server.recall_data("Was kostet ein Flug?")
    assert found["anzahl"] == 0 and "nichts gespeichert" in found["hinweis"]


def test_keyword_hits_are_kept_when_the_embedding_model_is_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mcp_servers.memory.embed import EmbedError

    def down(_texts: list[str]) -> list[list[float]]:
        raise EmbedError("aus")

    monkeypatch.setattr(server, "_embedder", down)
    result = server.remember_data("Mein Lieblingsverein ist der BVB")  # wird trotzdem gespeichert
    assert result["ok"] and server.store().without_embedding()[0].id == result["id"]
    found = server.recall_data("Lieblingsverein")
    assert found["suche"] == "Stichwortsuche" and found["anzahl"] == 1
    assert "ollama pull" in found["hinweis"]


def test_entries_of_another_model_are_not_compared(embeddings: None) -> None:
    server.remember_data("Mein Lieblingsverein ist der BVB")
    server.store().set_embedding(1, [1.0, 0.1], "altes-modell")  # andere Dimension und Modell
    assert server.recall_data("Welcher Fußballclub gefällt mir?")["suche"] == "Stichwortsuche"


def test_forget_removes_the_embedding_too(embeddings: None, tmp_path: Path) -> None:
    server.remember_data("Mein Lieblingsverein ist der BVB")
    server.forget_data(1)
    with sqlite3.connect(tmp_path / "g.sqlite3") as conn:
        assert conn.execute("SELECT COUNT(*) FROM memories").fetchone() == (0,)
    assert server.recall_data("Welcher Fußballclub gefällt mir?")["anzahl"] == 0


def test_sqlite_vec_and_the_python_fallback_agree(
    embeddings: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mcp_servers.memory import store as store_module

    server.remember_data("Mein Lieblingsverein ist der BVB")
    server.remember_data("Die Mathe-Klausur ist am 12. Dezember")
    vector = [0.9, 0.2, 0.0]
    with_vec = server.store().semantic_search(vector, "test-modell")

    def no_vec(_conn: object) -> bool:
        return False

    monkeypatch.setattr(store_module, "_load_vec", no_vec)
    without = server.store().semantic_search(vector, "test-modell")
    assert [m.id for m, _ in with_vec[0]] == [m.id for m, _ in without[0]] == [1]
    assert with_vec[0][0][1] == pytest.approx(without[0][0][1], abs=1e-5)


def test_embedder_only_talks_to_the_own_network() -> None:
    from mcp_servers.memory.embed import is_local_url, make_embedder

    for url in ("http://127.0.0.1:11434/v1", "http://localhost:11434/v1", "http://192.168.1.5:11434",
                "http://100.101.102.103:11434", "http://mein-pc:11434", "http://pc.tail1234.ts.net:11434"):  # fmt: skip
        assert is_local_url(url), url
    for url in ("https://api.openai.com/v1", "http://8.8.8.8/v1", "ftp://127.0.0.1", "", "http://"):
        assert not is_local_url(url), url
    with pytest.raises(ValueError, match="eigenen Netz"):
        make_embedder("https://api.openai.com/v1")


def test_embedder_talks_openai_protocol_and_reports_problems(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import json
    import urllib.request

    from mcp_servers.memory.embed import EmbedError, make_embedder

    seen: list[tuple[str, dict[str, object]]] = []

    class Reply:
        def __init__(self, body: bytes) -> None:
            self._body = body

        def __enter__(self) -> "Reply":
            return self

        def __exit__(self, *_exc: object) -> None:
            return None

        def read(self) -> bytes:
            return self._body

    replies = [
        b'{"data": [{"embedding": [0.1, 0.2]}, {"embedding": [0.3, 0.4]}]}',
        b"{}",
        b"kaputt",
    ]

    def fake_open(self: object, request: urllib.request.Request, timeout: float) -> Reply:
        seen.append((request.full_url, json.loads(request.data or b"{}")))  # type: ignore[arg-type]
        return Reply(replies.pop(0))

    monkeypatch.setattr(urllib.request.OpenerDirector, "open", fake_open)
    embed = make_embedder("http://127.0.0.1:11434/v1", "m")
    assert embed(["a", "b"]) == [[0.1, 0.2], [0.3, 0.4]]
    assert seen == [("http://127.0.0.1:11434/v1/embeddings", {"model": "m", "input": ["a", "b"]})]
    for _ in range(2):
        with pytest.raises(EmbedError):
            embed(["a"])


# --------------------------------------------------------------------------- M1.11 und M1.12
def test_a_forgotten_text_is_gone_from_the_file_without_any_vacuum(tmp_path: Path) -> None:
    """secure_delete: gelöschter Inhalt wird überschrieben, nicht nur freigegeben."""
    server.remember_data("Streng vertraulicher Satz PLUGH " + "x" * 200)
    assert b"PLUGH" in (tmp_path / "g.sqlite3").read_bytes()
    server.forget_data(1)
    assert b"PLUGH" not in (tmp_path / "g.sqlite3").read_bytes()


def test_maintenance_removes_duplicates_and_fills_missing_embeddings(
    embeddings: None,
) -> None:
    from mcp_servers.memory.maintain import maintain

    # Dubletten, wie sie durch ältere Fassungen oder Handarbeit in der Datei entstehen könnten
    store = server.store()
    with store._connect() as conn:  # pyright: ignore[reportPrivateUsage]
        for text in (
            "Ich wohne in Dortmund",
            "ICH WOHNE IN DORTMUND",
            "Mein Lieblingsverein ist der BVB",
        ):
            conn.execute(
                "INSERT INTO memories (text, kind, created) VALUES (?, 'fakt', '2026-11-16T00:00:00+00:00')",
                (text,),
            )
    first = maintain(store, [], None, fake_embedder, "test-modell")
    assert first.duplicates_removed == 1 and first.memories_embedded == 2
    assert [m.text for m in store.entries()] == [
        "Ich wohne in Dortmund",
        "Mein Lieblingsverein ist der BVB",
    ]
    second = maintain(store, [], None, fake_embedder, "test-modell")
    assert second.duplicates_removed == 0 and second.memories_embedded == 0
    assert second.memories_without_embedding == 0


def test_maintenance_re_embeds_entries_of_an_older_model(embeddings: None) -> None:
    from mcp_servers.memory.maintain import maintain

    server.remember_data("Mein Lieblingsverein ist der BVB")
    server.store().set_embedding(1, [1.0], "altes-modell")
    done = maintain(server.store(), [], None, fake_embedder, "test-modell")
    assert done.memories_embedded == 1
    assert server.recall_data("Welcher Fußballclub gefällt mir?")["suche"] == "semantisch"


def test_maintenance_tool_reports_in_plain_words(embeddings: None) -> None:
    server.remember_data("Ich wohne in Dortmund")
    assert "0 Dubletten entfernt" in server.maintain_data()["ergebnis"]
