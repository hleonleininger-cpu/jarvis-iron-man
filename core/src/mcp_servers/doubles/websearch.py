"""Websuche-Attrappe: feste Treffer statt DuckDuckGo. Unbekannte Begriffe liefern nichts."""

import sys
import urllib.parse

from mcp_servers.websearch import server

PAGES: dict[str, list[tuple[str, str, str]]] = {
    "handball": [
        ("Handball-Regeln im Überblick", "https://beispiel.example/handball-regeln",
         "Die wichtigsten Regeln: Spielfeld, Spielzeit und Strafen."),
        ("Handball Bundesliga Tabelle", "https://beispiel.example/bundesliga",
         "Aktuelle Tabelle und Spielplan."),
    ],
    "python": [
        ("Python-Dokumentation", "https://docs.example/python", "Offizielle Dokumentation."),
    ],
}  # fmt: skip


def _result(title: str, url: str, text: str) -> str:
    link = "//duckduckgo.com/l/?uddg=" + urllib.parse.quote(url, safe="") + "&rut=abc"
    return (
        f'<div class="result"><h2 class="result__title"><a rel="nofollow" class="result__a" '
        f'href="{link}">{title}</a></h2><a class="result__snippet" href="{link}">{text}</a></div>'
    )


def fake_fetch(url: str, params: dict[str, str]) -> bytes:
    query = params["q"].lower()
    hits: list[tuple[str, str, str]] = []
    for key, page in PAGES.items():
        if key in query:
            hits = page
            break
    return f"<html><body>{''.join(_result(*hit) for hit in hits)}</body></html>".encode()


def main() -> None:
    server.fetch = fake_fetch
    server.main()


if __name__ == "__main__":
    sys.exit(main())
