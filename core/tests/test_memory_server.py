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
