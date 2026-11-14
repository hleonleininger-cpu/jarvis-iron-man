"""Projekte-Server: Ideen, Aufträge, Stand, Entwürfe. Nur lokale Dateien (tmp_path)."""

from datetime import datetime
from pathlib import Path

import pytest

from mcp_servers.projekte import server
from mcp_servers.projekte.store import ProjectStore

NOW = datetime(2026, 11, 14, 12, 0, tzinfo=server.TZ)


@pytest.fixture(autouse=True)
def _files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(server, "_store", ProjectStore(tmp_path / "projekte.sqlite3"))
    monkeypatch.setattr(server, "_drafts_dir", tmp_path / "entwuerfe")


def test_idea_roundtrip_and_status() -> None:
    added = server.add_idea_data("  Lernplan-Vorlage ", "Produkt", "Für Abiturienten")
    assert added == {"ok": True, "gespeichert": "Idee: Lernplan-Vorlage", "id": 1}
    (entry,) = server.list_ideas_data(now=NOW)["eintraege"]
    assert entry == {
        "id": 1, "titel": "Lernplan-Vorlage", "status": "neu",
        "kategorie": "produkt", "notiz": "Für Abiturienten",
    }  # fmt: skip
    assert server.set_status_data(1, " In_Arbeit ")["status"] == "in_arbeit"
    assert server.list_ideas_data("neu", NOW)["anzahl"] == 0
    assert server.list_ideas_data("in_arbeit", NOW)["anzahl"] == 1


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (("",), "Titel fehlt"),
        (("x" * 201,), "höchstens 200"),
        (("A", "spiel"), "Unbekannte Kategorie"),
        (("A", None, "n" * 2001), "höchstens 2000"),
    ],
)
def test_invalid_ideas_are_rejected_without_saving(args: tuple[str, ...], message: str) -> None:
    result = server.add_idea_data(*args)
    assert result["ok"] is False and message in result["fehler"]
    assert server.store().count() == 0


def test_leads_with_deadline_amount_and_sums() -> None:
    server.add_lead_data("Müller GmbH", "Flyer", "2026-11-10", "250")
    server.add_lead_data("Schmidt", None, "2026-12-01", "249,90")
    server.add_lead_data("Kein Betrag")
    server.set_status_data(1, "gewonnen")
    server.set_status_data(2, "gewonnen")
    server.set_status_data(2, "bezahlt")
    result = server.list_leads_data(now=NOW)
    assert (
        result["summe_bezahlt"] == "249,90 €" and result["summe_gewonnen_unbezahlt"] == "250,00 €"
    )
    first, second, third = result["eintraege"]  # Frist zuerst, ohne Frist zuletzt
    assert (first["titel"], first["betrag"], first["frist"]) == (
        "Müller GmbH",
        "250,00 €",
        "2026-11-10",
    )
    assert "ueberfaellig" not in first  # gewonnen: nichts mehr offen
    assert (
        second["status"] == "bezahlt" and third["titel"] == "Kein Betrag" and "betrag" not in third
    )


def test_open_lead_past_its_deadline_is_flagged() -> None:
    server.add_lead_data("Spät", None, "2026-11-01")
    server.add_lead_data("Rechtzeitig", None, "2026-11-30")
    late, fine = server.list_leads_data("offen", NOW)["eintraege"]
    assert late["ueberfaellig"] is True and "ueberfaellig" not in fine


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"kunde": " "}, "Kunde fehlt"),
        ({"kunde": "A", "frist": "morgen"}, "Ungültiges Datum"),
        ({"kunde": "A", "frist": "9999-01-01"}, "nicht plausibel"),
        ({"kunde": "A", "betrag_eur": "viel"}, "Ungültiger Betrag"),
        ({"kunde": "A", "betrag_eur": "-5"}, "zwischen 0 und"),
        ({"kunde": "A", "betrag_eur": "NaN"}, "zwischen 0 und"),
        ({"kunde": "A", "betrag_eur": "1e9"}, "zwischen 0 und"),
    ],
)
def test_invalid_leads_are_rejected_without_saving(kwargs: dict[str, str], message: str) -> None:
    result = server.add_lead_data(**kwargs)
    assert result["ok"] is False and message in result["fehler"]
    assert server.store().count() == 0


def test_amounts_are_exact_in_cents() -> None:
    assert (
        server.to_cents("19,99") == 1999
        and server.to_cents(0.1) == 10
        and server.to_cents(" ") is None
    )
    assert server.to_cents("0.005") == 1 and server.to_cents("0.004") == 0  # kaufmännisch gerundet


def test_status_must_fit_the_kind_and_the_entry_must_exist() -> None:
    server.add_idea_data("Idee")
    server.add_lead_data("Kunde")
    assert server.set_status_data(1, "bezahlt")["ok"] is False  # Auftragsstatus bei einer Idee
    assert server.set_status_data(2, "verworfen")["ok"] is False  # Ideenstatus bei einem Auftrag
    assert server.set_status_data(99, "neu")["grund"] == "not_found"
    assert server.list_ideas_data("bezahlt", NOW)["ok"] is False
    assert server.store().get(1) is not None and server.store().get(1).status == "neu"  # type: ignore[union-attr]


def test_ideas_and_leads_are_listed_separately() -> None:
    server.add_idea_data("Idee")
    server.add_lead_data("Kunde")
    assert [e["titel"] for e in server.list_ideas_data(now=NOW)["eintraege"]] == ["Idee"]
    assert [e["titel"] for e in server.list_leads_data(now=NOW)["eintraege"]] == ["Kunde"]


def test_list_is_limited() -> None:
    for number in range(server.MAX_LISTED + 3):
        server.add_idea_data(f"Idee {number}")
    result = server.list_ideas_data(now=NOW)
    assert len(result["eintraege"]) == server.MAX_LISTED and "Nur die ersten" in result["hinweis"]


def test_draft_is_saved_and_never_overwrites(tmp_path: Path) -> None:
    first = server.save_draft_data("Mein Entwurf: Größe & Zähne", "Text eins")
    second = server.save_draft_data("Mein Entwurf: Größe & Zähne", "Text zwei")
    assert first["ok"] and second["ok"]
    one, two = Path(first["datei"]), Path(second["datei"])
    assert (
        one.name == "mein-entwurf-grosse-zahne.md" and two.name == "mein-entwurf-grosse-zahne-2.md"
    )
    assert one.parent == tmp_path / "entwuerfe"
    assert one.read_text(encoding="utf-8") == "# Mein Entwurf: Größe & Zähne\n\nText eins\n"
    assert "Text zwei" in two.read_text(encoding="utf-8")


@pytest.mark.parametrize("title", ["../../etc/passwd", "..\\..\\x", "/absolut/pfad", "CON", "***"])
def test_draft_names_cannot_leave_the_folder(tmp_path: Path, title: str) -> None:
    result = server.save_draft_data(title, "x")
    assert result["ok"]
    saved = Path(result["datei"]).resolve()
    assert saved.parent == (tmp_path / "entwuerfe").resolve() and saved.suffix == ".md"
    assert not any(c in saved.name for c in "/\\:*")
    assert saved.stem.split("-")[0] not in server._RESERVED or saved.stem.startswith("entwurf")  # pyright: ignore[reportPrivateUsage]


def test_invalid_drafts_write_nothing(tmp_path: Path) -> None:
    assert server.save_draft_data("", "x")["ok"] is False
    assert server.save_draft_data("T", "  ")["ok"] is False
    too_long = server.save_draft_data("T", "x" * (server.MAX_DRAFT_LENGTH + 1))
    assert too_long["ok"] is False and "höchstens" in too_long["fehler"]
    assert not (tmp_path / "entwuerfe").exists() or not list((tmp_path / "entwuerfe").iterdir())


def test_windows_device_names_get_a_prefix(tmp_path: Path) -> None:
    result = server.save_draft_data("CON", "x")
    assert Path(result["datei"]).name == "entwurf-con.md"
