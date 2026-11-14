"""Live-Test: echte DuckDuckGo-Suche (Netz); ohne Netz oder bei Sperre übersprungen."""

import pytest

from mcp_servers.websearch.server import web_search_data

pytestmark = pytest.mark.live


def test_live_websearch_returns_readable_results() -> None:
    result = web_search_data("Handball Regeln")
    if result["ok"] is False:
        pytest.skip(f"DuckDuckGo von hier aus nicht nutzbar: {result['fehler']}")
    assert 1 <= result["anzahl"] <= 5
    for hit in result["treffer"]:
        assert hit["titel"] and hit["adresse"].startswith(("http://", "https://"))
        assert "duckduckgo.com" not in hit["adresse"]
