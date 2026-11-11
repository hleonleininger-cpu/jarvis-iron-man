"""Notion-Server: Normalisierung, Fehlerklassen, NUR-LESEN-Nachweis, Tool-Funktionen."""

import asyncio
import json
import re
from pathlib import Path
from typing import Any

import keyring
import pytest

import mcp_servers.notion as notion_pkg
from mcp_servers.doubles import notion as double
from mcp_servers.notion import client as notion_client
from mcp_servers.notion import server
from mcp_servers.notion.client import (
    ALLOWED_REQUESTS,
    NotionClient,
    NotionError,
    Response,
    normalize_token,
)
from mcp_servers.notion.normalize import (
    blocks_to_text,
    extract_page_id,
    fit_budget,
    is_done,
    normalize_page,
    property_value,
    sort_entries,
)
from tests.test_calendar_server import MemoryKeyring

TOKEN = "ntn_supergeheim123"
MISSION_ID = "00000000-0000-0000-0000-0000000000a1"
ACADEMY_ID = "00000000-0000-0000-0000-0000000000a2"


@pytest.fixture(autouse=True)
def notion_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NOTION_DB_MISSION_CONTROL", MISSION_ID)
    monkeypatch.setenv("NOTION_DB_ACADEMY", ACADEMY_ID)


@pytest.fixture
def fake_notion(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = double.make_transport()
    monkeypatch.setattr(server, "make_client", lambda: NotionClient(TOKEN, transport=transport))


# --------------------------------------------------------------------------- Normalisierung
def _prop(kind: str, value: Any) -> dict[str, Any]:
    return {"type": kind, kind: value}


def test_mission_control_entry_is_normalized() -> None:
    entry = normalize_page(double.MISSION_PAGES[0])
    assert entry == {
        "titel": "JARVIS OS fertigstellen",
        "id": "11111111-1111-1111-1111-111111111101",
        "status": "In Arbeit",
        "datum": "2026-11-20",
        "felder": {"Priorität": "Hoch", "Bereich": "Projekt"},
    }


def test_reading_vault_entry_numbers_are_clean() -> None:
    entry = normalize_page(double.ACADEMY_PAGES[0])
    assert entry["felder"] == {"Kapitel": 7, "Fortschritt": 45}
    assert "datum" not in entry


def test_schulmissionen_schema_with_fach_fallback_and_time() -> None:
    page = {
        "id": "x",
        "properties": {
            "Aufgabe": _prop("title", [{"plain_text": "Klausur Mathe"}]),
            "Fach": _prop("select", {"name": "MA"}),
            "Eintragstyp": _prop("select", {"name": "Klausur"}),
            "Fällig": _prop("date", {"start": "2026-12-01"}),
            "Uhrzeit": _prop("rich_text", [{"plain_text": "08:00"}]),
            "Status": _prop("select", {"name": "Offen"}),
        },
    }
    entry = normalize_page(page)
    assert entry["titel"] == "Klausur Mathe" and entry["datum"] == "2026-12-01"
    assert list(entry["felder"])[:3] == ["Eintragstyp", "Fach", "Uhrzeit"]


def test_missing_and_odd_fields_are_skipped_not_fatal() -> None:
    assert normalize_page({}) == {"titel": "Unbenannter Eintrag"}
    assert normalize_page({"properties": "kaputt"}) == {"titel": "Unbenannter Eintrag"}
    page = {
        "properties": {
            "Name": _prop("title", []),
            "Status": _prop("status", None),
            "Unbekannt": {"type": "rollup", "rollup": {"x": 1}},
            "Kaputt": "kein dict",
            "Tags": _prop("multi_select", [{"name": "a"}, {"name": "b"}]),
            "Erledigt?": _prop("checkbox", True),
            "Berechnet": _prop("formula", {"type": "number", "number": 2.5}),
            "Link": _prop("url", "https://example.org"),
        }
    }
    entry = normalize_page(page)
    assert entry["titel"] == "Unbenannter Eintrag" and "status" not in entry
    assert entry["felder"]["Tags"] == ["a", "b"] and entry["felder"]["Berechnet"] == 2.5
    assert "Unbekannt" not in entry["felder"] and "Kaputt" not in entry["felder"]


def test_long_text_is_clipped_and_field_count_limited() -> None:
    props: dict[str, Any] = {"T": _prop("title", [{"plain_text": "x" * 1000}])}
    for i in range(12):
        props[f"F{i}"] = _prop("number", i)
    entry = normalize_page({"properties": props})
    assert len(entry["titel"]) <= 301 and len(entry["felder"]) == 6


def test_done_detection_priority_sort_and_budget() -> None:
    entries = [normalize_page(p) for p in double.MISSION_PAGES]
    assert [is_done(e) for e in entries] == [False, False, False, True]
    ordered = [e["titel"] for e in sort_entries(entries[:3])]
    assert ordered == ["JARVIS OS fertigstellen", "Mathe LK wiederholen", "Handballtraining planen"]
    many = [{"titel": "x" * 200} for _ in range(40)]
    kept, truncated = fit_budget(many)
    assert truncated and 1 <= len(kept) < 40 and len(json.dumps(kept)) <= 3500


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("11111111111111111111111111111101", "11111111-1111-1111-1111-111111111101"),
        ("11111111-1111-1111-1111-111111111101", "11111111-1111-1111-1111-111111111101"),
        (
            "https://www.notion.so/ws/Titel-11111111111111111111111111111101?pvs=4",
            "11111111-1111-1111-1111-111111111101",
        ),
        ("kein-id", None),
        ("../../users", None),
    ],
)
def test_extract_page_id(text: str, expected: str | None) -> None:
    assert extract_page_id(text) == expected


def test_token_normalization() -> None:
    assert normalize_token('  Bearer "ntn_abc"  ') == "ntn_abc"
    assert normalize_token("'ntn_abc'") == "ntn_abc"
    with pytest.raises(NotionError) as err:
        NotionClient("   ")
    assert err.value.reason == "not_configured"


# --------------------------------------------------------------------------- Fehlerklassen
def _failing(status: int, body: str = "{}") -> NotionClient:
    return NotionClient(TOKEN, transport=lambda m, u, h, b: Response(status, body))


@pytest.mark.parametrize(
    ("status", "reason"),
    [
        (401, "unauthorized"),
        (403, "not_found"),
        (404, "not_found"),
        (429, "rate_limited"),
        (500, "unavailable"),
        (502, "unavailable"),
    ],
)
def test_http_errors_are_classified_and_logged_without_token(
    status: int, reason: str, capsys: pytest.CaptureFixture[str]
) -> None:
    client = _failing(status, json.dumps({"code": "object_not_found", "message": "geheimer Text"}))
    with pytest.raises(NotionError) as err:
        client.query_database(MISSION_ID, "MISSION_CONTROL")
    assert err.value.reason == reason
    logged = capsys.readouterr().err
    assert f"[notion:MISSION_CONTROL] status={status} code=object_not_found" in logged
    assert TOKEN not in logged and "geheimer Text" not in logged and MISSION_ID not in logged


def test_network_error_and_garbage() -> None:
    def down(m: str, u: str, h: dict[str, str], b: bytes | None) -> Response:
        raise ConnectionError("kein Netz")

    with pytest.raises(NotionError) as err:
        NotionClient(TOKEN, transport=down).query_database(MISSION_ID, "X")
    assert err.value.reason == "unavailable"
    for body in ("kein json", "[]", "{}", '{"results": "nope"}'):
        with pytest.raises(NotionError) as bad:
            _failing(200, body).query_database(MISSION_ID, "X")
        assert bad.value.reason == "invalid_response"


def test_pagination_follows_cursor_and_is_capped() -> None:
    calls: list[dict[str, Any]] = []

    def paged(method: str, url: str, headers: dict[str, str], body: bytes | None) -> Response:
        calls.append(json.loads(body or b"{}"))
        return Response(
            200,
            json.dumps(
                {"results": [{"id": "a"}], "has_more": True, "next_cursor": f"c{len(calls)}"}
            ),
        )

    results = NotionClient(TOKEN, transport=paged).query_database(MISSION_ID, "X")
    assert len(calls) == notion_client.MAX_QUERY_PAGES == len(results)
    assert "start_cursor" not in calls[0] and calls[1]["start_cursor"] == "c1"


def test_requests_carry_token_and_version_only_in_headers() -> None:
    seen: list[tuple[str, str, dict[str, str]]] = []

    def spy(method: str, url: str, headers: dict[str, str], body: bytes | None) -> Response:
        seen.append((method, url, headers))
        return Response(200, json.dumps({"results": []}))

    NotionClient(f"Bearer {TOKEN}", transport=spy).query_database(MISSION_ID, "X")
    method, url, headers = seen[0]
    assert (method, TOKEN in url) == ("POST", False)
    assert headers["Authorization"] == f"Bearer {TOKEN}" and headers["Notion-Version"]


# --------------------------------------------------------------------------- NUR LESEN
def _spy_client() -> tuple[NotionClient, list[str]]:
    sent: list[str] = []

    def spy(method: str, url: str, headers: dict[str, str], body: bytes | None) -> Response:
        sent.append(f"{method} {url}")
        return Response(200, json.dumps({"results": []}))

    return NotionClient(TOKEN, transport=spy), sent


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("PATCH", f"/pages/{MISSION_ID}"),
        ("DELETE", f"/blocks/{MISSION_ID}"),
        ("PUT", f"/pages/{MISSION_ID}"),
        ("POST", "/pages"),
        ("POST", f"/pages/{MISSION_ID}"),
        ("POST", "/comments"),
        ("POST", f"/blocks/{MISSION_ID}/children"),
        ("PATCH", f"/blocks/{MISSION_ID}/children"),
        ("POST", f"/data_sources/{MISSION_ID}"),
        ("POST", "/search"),
        ("GET", "/users"),
        ("GET", f"/pages/{MISSION_ID}/../../users"),
        ("POST", f"/data_sources/{MISSION_ID}/query/../../pages"),
    ],
)
def test_client_refuses_everything_that_is_not_a_read(method: str, path: str) -> None:
    client, sent = _spy_client()
    with pytest.raises(NotionError) as err:
        client._request("T", method, path, {})  # pyright: ignore[reportPrivateUsage]
    assert err.value.reason == "forbidden_request"
    assert sent == []  # nichts hat das Netz erreicht


def test_allowlist_contains_only_the_three_read_endpoints() -> None:
    assert [m for m, _ in ALLOWED_REQUESTS] == ["POST", "GET", "GET"]
    patterns = [rx.pattern for _, rx in ALLOWED_REQUESTS]
    assert any("data_sources" in p and p.endswith("query$") for p in patterns)
    assert any(p.startswith("^/pages/") for p in patterns) and any(
        "children" in p for p in patterns
    )


def test_server_source_contains_no_write_code() -> None:
    package = Path(notion_pkg.__file__).parent
    source = "\n".join(p.read_text(encoding="utf-8") for p in package.glob("*.py"))
    assert not re.search(r'["\'](PATCH|DELETE|PUT)["\']', source)
    assert set(re.findall(r'["\'](GET|POST)["\']', source)) == {"GET", "POST"}
    assert not re.search(r"/comments|/search|/users|archive|trash", source, re.IGNORECASE)
    names = {n.lower() for module in (server, notion_client) for n in dir(module)}
    forbidden = re.compile(r"create|update|delete|archive|append|patch|write|put_|insert")
    assert [n for n in names if forbidden.search(n) and not n.startswith("__")] == []


def test_server_offers_exactly_three_read_tools() -> None:
    tools = asyncio.run(server.mcp.list_tools())
    assert sorted(t.name for t in tools) == ["notion_get", "notion_query", "notion_search"]


def test_write_and_search_endpoints_do_not_appear_as_request_paths() -> None:
    package = Path(notion_pkg.__file__).parent
    paths = re.findall(r'f?"(/[a-z_]+/[^"]*)"', (package / "client.py").read_text(encoding="utf-8"))
    assert all(p.startswith(("/data_sources/", "/pages/", "/blocks/")) for p in paths)


# --------------------------------------------------------------------------- Tool-Funktionen
def test_query_mission_control(fake_notion: None) -> None:
    result = server.query_data("mission_control")
    assert result["ok"] and result["anzahl"] == 3
    assert [e["titel"] for e in result["eintraege"]][0] == "JARVIS OS fertigstellen"
    assert all(e["titel"] != "Steuerunterlagen sortieren" for e in result["eintraege"])  # erledigt


def test_query_includes_done_on_request_and_filters(fake_notion: None) -> None:
    assert server.query_data("Mission Control", erledigte_einbeziehen=True)["anzahl"] == 4
    filtered = server.query_data("mission_control", filter="schule")
    assert [e["titel"] for e in filtered["eintraege"]] == ["Mathe LK wiederholen"]


def test_search(fake_notion: None) -> None:
    result = server.search_data("handball")
    assert result["ok"] and result["treffer"][0]["datenbank"] == "mission_control"
    assert server.search_data("gibt-es-nicht-xyz")["anzahl"] == 0
    assert server.search_data("Atomic", "academy")["treffer"][0]["titel"] == "Atomic Habits"
    assert server.search_data("  ")["grund"] == "invalid_input"


def test_get_page(fake_notion: None) -> None:
    result = server.get_data("11111111111111111111111111111101")
    assert result["ok"] and "[x] Notion lesen" in result["inhalt"]
    assert server.get_data("nonsens")["grund"] == "invalid_input"


def test_no_database_configured_names_whats_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NOTION_DB_MISSION_CONTROL")
    monkeypatch.delenv("NOTION_DB_ACADEMY")
    result = server.query_data("mission_control")
    assert result["grund"] == "not_configured_database"
    for name in (
        "NOTION_DB_MISSION_CONTROL",
        "NOTION_DB_ACADEMY",
        "NOTION_DB_FORGE",
        "NOTION_DB_OLYMPUS",
        "NOTION_DB_VAULT",
    ):
        assert name in result["fehler"]
    assert "jarvis.toml" in result["fehler"]


def test_unknown_database_lists_configured_and_missing(fake_notion: None) -> None:
    result = server.query_data("forge")
    assert result["grund"] == "unknown_database"
    assert "mission_control" in result["fehler"] and "NOTION_DB_FORGE" in result["fehler"]


def test_missing_token_points_to_secrets_command() -> None:
    keyring.set_keyring(MemoryKeyring())
    result = server.query_data("mission_control")
    assert result["grund"] == "not_configured"
    assert "jarvis secrets set notion" in result["fehler"]


def test_token_is_read_from_keyring() -> None:
    backend = MemoryKeyring()
    keyring.set_keyring(backend)
    backend.store[("jarvis", "notion")] = json.dumps({"token": "ntn_x"})
    assert server.load_token() == "ntn_x"
    backend.store[("jarvis", "notion")] = "kein json"
    with pytest.raises(NotionError):
        server.load_token()


def test_notion_errors_become_clean_results_without_invented_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(server, "make_client", lambda: _failing(401))
    result = server.query_data("mission_control")
    assert result["ok"] is False and result["grund"] == "unauthorized"
    assert "eintraege" not in result
    monkeypatch.setattr(server, "make_client", lambda: _failing(404))
    assert (
        "nicht gefunden oder ist nicht mit der Integration geteilt"
        in server.search_data("x")["fehler"]
    )


# --------------------------------------------------------------------------- Runde 2
@pytest.mark.parametrize("bad", ["<ID>", "keine-id", "1234"])
def test_malformed_database_id_gets_a_clear_message(
    fake_notion: None, monkeypatch: pytest.MonkeyPatch, bad: str
) -> None:
    """Früher: 'Dieser Notion-Zugriff ist nicht erlaubt (nur Lesen)', obwohl nur die ID falsch war."""
    monkeypatch.setenv("NOTION_DB_MISSION_CONTROL", bad)
    result = server.query_data("mission_control")
    assert result["grund"] == "invalid_database_id"
    assert "NOTION_DB_MISSION_CONTROL" in result["fehler"] and "jarvis.toml" in result["fehler"]


def test_search_skips_databases_with_malformed_id(
    fake_notion: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NOTION_DB_MISSION_CONTROL", "<ID>")
    assert server.search_data("Atomic")["treffer"][0]["titel"] == "Atomic Habits"
    monkeypatch.setenv("NOTION_DB_ACADEMY", "<ID>")
    assert server.search_data("Atomic")["grund"] == "invalid_database_id"


def test_query_says_when_the_database_was_not_read_completely(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def endless(method: str, url: str, headers: dict[str, str], body: bytes | None) -> Response:
        page = {
            "id": "a",
            "properties": {"Name": {"type": "title", "title": [{"plain_text": "x"}]}},
        }
        return Response(200, json.dumps({"results": [page], "has_more": True, "next_cursor": "c"}))

    monkeypatch.setattr(server, "make_client", lambda: NotionClient(TOKEN, transport=endless))
    assert "mehr als 300 Einträge" in server.query_data("mission_control")["hinweis"]
    assert "mehr als 300 Einträge" in server.search_data("x")["hinweis"]


def test_complete_query_has_no_incomplete_hint(fake_notion: None) -> None:
    assert "hinweis" not in server.query_data("mission_control")


# --------------------------------------------------------------------------- Runde 4
def test_long_page_says_that_only_the_beginning_was_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def transport(method: str, url: str, headers: dict[str, str], body: bytes | None) -> Response:
        if url.endswith("/children"):
            block = {"type": "paragraph", "paragraph": {"rich_text": [{"plain_text": "Anfang"}]}}
            return Response(200, json.dumps({"results": [block], "has_more": True}))
        return Response(200, json.dumps({"id": MISSION_ID, "properties": {}}))

    monkeypatch.setattr(server, "make_client", lambda: NotionClient(TOKEN, transport=transport))
    result = server.get_data(MISSION_ID)
    assert result["inhalt"] == "Anfang" and "nur der Anfang" in result["hinweis"]


def test_short_page_has_no_hint(fake_notion: None) -> None:
    assert "hinweis" not in server.get_data("11111111111111111111111111111101")


# --------------------------------------------------------------------------- Runde 5
def test_peek_link_from_a_database_view_names_the_page() -> None:
    link = (
        "https://www.notion.so/ws/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        "?v=bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb&p=11111111111111111111111111111101&pm=s"
    )
    assert extract_page_id(link) == "11111111-1111-1111-1111-111111111101"
    plain = extract_page_id("https://notion.so/Titel-11111111111111111111111111111102")
    assert plain == "11111111-1111-1111-1111-111111111102"


# --------------------------------------------------------------------------- Runde 6
def test_search_does_not_match_property_names(fake_notion: None) -> None:
    """Früher traf 'Priorität' jede Mission, weil der Feldname mitdurchsucht wurde."""
    assert server.search_data("Priorität")["anzahl"] == 0
    assert server.search_data("Schule")["anzahl"] == 1  # Werte werden weiter gefunden


@pytest.mark.parametrize("status", ["✅ Erledigt", "Done!", "abgeschlossen", "ERLEDIGT"])
def test_done_status_with_symbols_is_recognized(status: str) -> None:
    assert is_done({"status": status})


@pytest.mark.parametrize("status", ["Offen", "In Arbeit", "Nicht erledigt", "not done", ""])
def test_open_status_is_not_done(status: str) -> None:
    assert not is_done({"status": status})


def test_priority_sorting_ignores_case_of_the_field_name() -> None:
    entries = [
        {"titel": "b", "felder": {"priorität": "Niedrig"}},
        {"titel": "a", "felder": {"PRIORITÄT": "hoch"}},
    ]
    assert [e["titel"] for e in sort_entries(entries)] == ["a", "b"]


# --------------------------------------------------------------------------- Runde 7
def test_more_property_types_are_read() -> None:
    assert property_value(_prop("unique_id", {"prefix": "MIS", "number": 42})) == "MIS-42"
    assert property_value(_prop("unique_id", {"prefix": None, "number": 7})) == "7"
    created = "2026-11-01T10:00:00.000Z"
    assert property_value(_prop("created_time", created)) == created
    assert property_value(_prop("relation", [{"id": "a"}, {"id": "b"}])) == "2 verknüpft"
    assert property_value(_prop("relation", [])) is None


def test_code_blocks_and_subpages_are_kept() -> None:
    blocks = [
        {"type": "code", "code": {"rich_text": [{"plain_text": "print(1)"}]}},
        {"type": "child_page", "child_page": {"title": "Notizen"}},
    ]
    assert blocks_to_text(blocks) == "```\nprint(1)\n```\n[Unterseite: Notizen]"
