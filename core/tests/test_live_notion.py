"""Live-Test gegen echtes Notion, NUR LESEND. Ohne Token oder konfigurierte Datenbank übersprungen."""

import pytest

from jarvis_core.config import Settings
from jarvis_core.secrets import is_set
from mcp_servers.notion import server

pytestmark = pytest.mark.live


def test_live_read_first_configured_database(monkeypatch: pytest.MonkeyPatch) -> None:
    if not is_set("notion"):
        pytest.skip("Kein Token (jarvis secrets set notion)")
    cfg = Settings().mcp_servers.get("notion")
    if cfg is None or not cfg.env:
        pytest.skip("Keine Notion-Datenbank in jarvis.toml konfiguriert")
    for key, value in cfg.env.items():
        monkeypatch.setenv(key, value)
    name = sorted(server.configured_databases())[0]
    result = server.query_data(name)
    assert result["ok"] is True, result
    assert isinstance(result["eintraege"], list)
