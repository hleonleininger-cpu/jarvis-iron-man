"""Notion-Attrappe: feste Seiten statt der echten API (nur Lesen, wie der echte Server).

Die Datenbank-IDs kommen wie im Betrieb aus NOTION_DB_<NAME>; die Attrappe liefert je Name
passende Beispieldaten (mission_control -> Missionen, academy -> Reading Vault).
"""

import json
import os
import re
import sys
from typing import Any

from mcp_servers.notion import server
from mcp_servers.notion.client import NotionClient, Response, Transport


def _title(name: str, text: str) -> dict[str, Any]:
    return {name: {"type": "title", "title": [{"plain_text": text}]}}


def _select(name: str, value: str) -> dict[str, Any]:
    return {name: {"type": "select", "select": {"name": value}}}


def _date(name: str, value: str) -> dict[str, Any]:
    return {name: {"type": "date", "date": {"start": value}}}


def _number(name: str, value: float) -> dict[str, Any]:
    return {name: {"type": "number", "number": value}}


def _page(page_id: str, *props: dict[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for prop in props:
        merged.update(prop)
    return {"object": "page", "id": page_id, "properties": merged}


MISSION_PAGES = [
    _page(
        "11111111-1111-1111-1111-111111111101",
        _title("Mission", "JARVIS OS fertigstellen"), _select("Status", "In Arbeit"),
        _select("Priorität", "Hoch"), _date("Fällig", "2026-11-20"), _select("Bereich", "Projekt"),
    ),
    _page(
        "11111111-1111-1111-1111-111111111102",
        _title("Mission", "Mathe LK wiederholen"), _select("Status", "Offen"),
        _select("Priorität", "Mittel"), _date("Fällig", "2026-11-15"), _select("Bereich", "Schule"),
    ),
    _page(
        "11111111-1111-1111-1111-111111111103",
        _title("Mission", "Handballtraining planen"), _select("Status", "Offen"),
        _select("Priorität", "Niedrig"), _date("Fällig", "2026-11-12"), _select("Bereich", "Sport"),
    ),
    _page(
        "11111111-1111-1111-1111-111111111104",
        _title("Mission", "Steuerunterlagen sortieren"), _select("Status", "Erledigt"),
        _select("Priorität", "Mittel"), _date("Fällig", "2026-11-01"),
    ),
]  # fmt: skip
ACADEMY_PAGES = [
    _page(
        "22222222-2222-2222-2222-222222222201",
        _title("Buch", "Atomic Habits"), _select("Status", "Lese Ich"),
        _number("Kapitel", 7), _number("Fortschritt", 45),
    ),
    _page(
        "22222222-2222-2222-2222-222222222202",
        _title("Buch", "Deep Work"), _select("Status", "Geplant"),
    ),
]  # fmt: skip
PAGES_BY_NAME = {"mission_control": MISSION_PAGES, "academy": ACADEMY_PAGES}
BLOCKS = [
    {"type": "heading_2", "heading_2": {"rich_text": [{"plain_text": "Ziele"}]}},
    {
        "type": "paragraph",
        "paragraph": {"rich_text": [{"plain_text": "Alle Module laufen lokal."}]},
    },
    {"type": "to_do", "to_do": {"rich_text": [{"plain_text": "Notion lesen"}], "checked": True}},
    {
        "type": "to_do",
        "to_do": {"rich_text": [{"plain_text": "Evals erweitern"}], "checked": False},
    },
]


def make_transport() -> Transport:
    ids = {
        key[len(server.ENV_PREFIX) :].lower(): value
        for key, value in os.environ.items()
        if key.startswith(server.ENV_PREFIX)
    }
    by_id = {value: PAGES_BY_NAME.get(name, []) for name, value in ids.items()}

    def transport(method: str, url: str, headers: dict[str, str], body: bytes | None) -> Response:
        path = url.split("/v1", 1)[1]
        query = re.match(r"^/data_sources/([^/]+)/query$", path)
        if method == "POST" and query:
            pages = by_id.get(query.group(1))
            if pages is None:
                return Response(404, json.dumps({"code": "object_not_found"}))
            return Response(200, json.dumps({"results": pages, "has_more": False}))
        if method == "GET" and path.startswith("/pages/"):
            return Response(200, json.dumps(MISSION_PAGES[0]))
        if method == "GET" and path.endswith("/children"):
            return Response(200, json.dumps({"results": BLOCKS}))
        return Response(400, json.dumps({"code": "validation_error"}))

    return transport


def main() -> None:
    transport = make_transport()
    server.make_client = lambda: NotionClient("eval", transport=transport)
    server.main()


if __name__ == "__main__":
    sys.exit(main())
