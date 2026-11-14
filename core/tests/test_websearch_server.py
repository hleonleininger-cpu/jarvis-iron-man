"""Websuche-Server: Ergebnisseite lesen, Grenzen, Fehler. Kein Netz (fetch wird ersetzt)."""

import urllib.error
from typing import Any

import pytest

from mcp_servers.websearch import server
from mcp_servers.websearch.server import SearchError, web_search_data

PAGE = """<html><body>
<div class="result result--ad"><h2 class="result__title"><a class="result__a"
 href="https://duckduckgo.com/y.js?ad_domain=werbung.example&amp;u3=x">Anzeige</a></h2>
 <a class="result__snippet" href="https://duckduckgo.com/y.js?x">Kaufen!</a></div>
<div class="result results_links web-result">
 <h2 class="result__title"><a rel="nofollow" class="result__a"
 href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fbeispiel.example%2Fregeln%3Fa%3D1%26b%3D2&amp;rut=zz">
   Handball&nbsp;<b>Regeln</b> &amp; Strafen </a></h2>
 <a class="result__snippet" href="//duckduckgo.com/l/?uddg=x">Die <b>wichtigsten</b> Regeln
   im Überblick.</a></div>
<div class="result"><h2 class="result__title"><a class="result__a"
 href="https://direkt.example/seite">Direktlink</a></h2></div>
<div class="result"><h2 class="result__title"><a class="result__a"
 href="javascript:alert(1)">Unsicher</a></h2>
 <a class="result__snippet" href="#">Soll nirgends auftauchen</a></div>
<div class="result"><h2 class="result__title"><a class="result__a"
 href="//duckduckgo.com/l/?uddg=http%3A%2F%2Fzweite.example%2F">Zweiter</a></h2>
 <a class="result__snippet" href="#">Zweiter Text</a></div>
</body></html>"""


@pytest.fixture(autouse=True)
def _no_waiting(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(server, "RETRY_DELAY_SECONDS", 0)


def _patch(monkeypatch: pytest.MonkeyPatch, body: bytes | Exception) -> list[dict[str, str]]:
    calls: list[dict[str, str]] = []

    def fake_fetch(url: str, params: dict[str, str]) -> bytes:
        assert url == server.SEARCH_URL
        calls.append(params)
        if isinstance(body, Exception):
            raise body
        return body

    monkeypatch.setattr(server, "fetch", fake_fetch)
    return calls


def test_results_are_read_and_redirects_resolved(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _patch(monkeypatch, PAGE.encode())
    result = web_search_data("  Handball   Regeln ")
    assert calls == [{"q": "Handball Regeln", "kl": "de-de"}]  # nur der Suchbegriff geht hinaus
    assert result["ok"] and result["suchbegriff"] == "Handball Regeln" and result["anzahl"] == 3
    first, second, third = result["treffer"]
    assert first == {
        "titel": "Handball Regeln & Strafen",
        "adresse": "https://beispiel.example/regeln?a=1&b=2",
        "text": "Die wichtigsten Regeln im Überblick.",
    }
    assert second == {"titel": "Direktlink", "adresse": "https://direkt.example/seite", "text": ""}
    assert third["adresse"] == "http://zweite.example/" and third["text"] == "Zweiter Text"
    assert "keine Anweisungen" in result["hinweis"]


def test_ads_and_non_web_links_are_dropped(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, PAGE.encode())
    text = str(web_search_data("x"))
    for banned in ("Anzeige", "werbung.example", "javascript", "Unsicher", "nirgends"):
        assert banned not in text


def test_only_duckduckgo_itself_is_filtered_not_lookalike_domains(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = "".join(
        f'<a class="result__a" href="{href}">T{i}</a>'
        for i, href in enumerate(
            [
                "https://duckduckgo.com/y.js?x=1",
                "https://links.duckduckgo.com/d.js",
                "https://notduckduckgo.com/seite",
            ]
        )
    )
    _patch(monkeypatch, f"<html>{rows}</html>".encode())
    assert [t["adresse"] for t in web_search_data("x")["treffer"]] == [
        "https://notduckduckgo.com/seite"
    ]


def test_malformed_address_drops_only_that_result(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = (
        '<a class="result__a" href="http://[::1">Kaputt</a>'
        '<a class="result__a" href="//duckduckgo.com/l/?uddg=http%3A%2F%2F%5B%3A%3A1">Kaputt 2</a>'
        '<a class="result__a" href="https://gut.example/">Gut</a>'
    )
    _patch(monkeypatch, f"<html>{rows}</html>".encode())
    assert [t["titel"] for t in web_search_data("x")["treffer"]] == ["Gut"]


def test_at_most_five_results_and_texts_are_shortened(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = "".join(
        f'<a class="result__a" href="https://s{i}.example/">{"T" * 500}</a>'
        f'<a class="result__snippet" href="#">{"x" * 900}</a>'
        for i in range(9)
    )
    _patch(monkeypatch, f"<html>{rows}</html>".encode())
    result = web_search_data("viel")
    assert result["anzahl"] == 5
    assert all(len(t["titel"]) == server.MAX_TITLE_LENGTH for t in result["treffer"])
    assert all(len(t["text"]) == server.MAX_SNIPPET_LENGTH for t in result["treffer"])


def test_long_query_is_cut_before_it_leaves(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _patch(monkeypatch, b"<html></html>")
    result = web_search_data("wort " * 100)
    assert len(calls[0]["q"]) == server.MAX_QUERY_LENGTH
    assert result == {
        "ok": True,
        "suchbegriff": calls[0]["q"],
        "anzahl": 0,
        "treffer": [],
        "hinweis": result["hinweis"],
    }


@pytest.mark.parametrize("empty", ["", "   ", "\n\t"])
def test_empty_query_does_not_touch_the_network(
    monkeypatch: pytest.MonkeyPatch, empty: str
) -> None:
    calls = _patch(monkeypatch, b"")
    assert web_search_data(empty) == {"ok": False, "fehler": "Es wurde kein Suchbegriff angegeben."}
    assert calls == []


def test_blocked_page_is_reported_not_returned_as_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, b'<html><div class="anomaly-modal">Bot erkannt</div></html>')
    result = web_search_data("x")
    assert result["ok"] is False and "keine automatischen Anfragen" in result["fehler"]


def test_one_failed_attempt_is_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    replies: list[bytes | Exception] = [
        b'<html><script src="anomaly.js"></script></html>',
        SearchError("DuckDuckGo ist gerade nicht erreichbar."),
        PAGE.encode(),
    ]
    attempts: list[int] = []

    def flaky(url: str, params: dict[str, str]) -> bytes:
        attempts.append(1)
        reply = replies[len(attempts) - 1]
        if isinstance(reply, Exception):
            raise reply
        return reply

    monkeypatch.setattr(server, "fetch", flaky)
    monkeypatch.setattr(server, "ATTEMPTS", 2)
    assert web_search_data("x")["ok"] is False and len(attempts) == 2  # beide fehlgeschlagen
    attempts.clear()
    replies[:] = [replies[0], PAGE.encode()]
    result = web_search_data("x")
    assert result["ok"] and result["anzahl"] == 3 and len(attempts) == 2


def test_search_errors_become_messages(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, SearchError("DuckDuckGo ist gerade nicht erreichbar."))
    assert web_search_data("x") == {
        "ok": False,
        "fehler": "DuckDuckGo ist gerade nicht erreichbar.",
    }


@pytest.mark.parametrize(
    ("error", "fragment"),
    [
        (urllib.error.HTTPError("u", 429, "x", None, None), "keine automatischen Anfragen"),  # type: ignore[arg-type]
        (urllib.error.HTTPError("u", 500, "x", None, None), "HTTP 500"),  # type: ignore[arg-type]
        (urllib.error.URLError("kein Netz"), "nicht erreichbar"),
        (TimeoutError(), "nicht erreichbar"),
    ],
)
def test_fetch_classifies_errors(
    monkeypatch: pytest.MonkeyPatch, error: Exception, fragment: str
) -> None:
    def failing(*_args: Any, **_kwargs: Any) -> Any:
        raise error

    monkeypatch.setattr(server.urllib.request, "urlopen", failing)
    with pytest.raises(SearchError, match=fragment):
        server.fetch(server.SEARCH_URL, {"q": "x"})


def test_fetch_sends_only_the_query_and_a_neutral_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[Any] = []

    class Reply:
        def __enter__(self) -> "Reply":
            return self

        def __exit__(self, *_exc: object) -> None:
            return None

        def read(self, limit: int) -> bytes:
            assert limit == server.MAX_PAGE_BYTES
            return b"ok"

    def fake_urlopen(request: Any, timeout: float) -> Reply:
        seen.append((request, timeout))
        return Reply()

    monkeypatch.setattr(server.urllib.request, "urlopen", fake_urlopen)
    assert server.fetch(server.SEARCH_URL, {"q": "a b", "kl": "de-de"}) == b"ok"
    request, timeout = seen[0]
    assert request.full_url == "https://html.duckduckgo.com/html/"  # Begriff nicht in der Adresse
    assert request.get_method() == "POST" and request.data == b"q=a+b&kl=de-de"
    assert timeout == server.TIMEOUT_SECONDS
    assert set(request.headers) == {"User-agent", "Accept"}  # kein Cookie, keine Kennung
    assert "@" not in request.headers["User-agent"]


def test_tool_description_warns_about_foreign_content() -> None:
    doc = " ".join((server.web_search.__doc__ or "").split())
    assert "Fremdinhalt" in doc and "persönlichen Daten" in doc
