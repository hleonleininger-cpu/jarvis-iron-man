"""Live-Tests: echtes Wetter (Netz) bzw. Agent mit Ollama; ohne Netz/Ollama übersprungen."""

import socket
import sqlite3
from pathlib import Path

import pytest

from jarvis_core.agent import TextDelta, ToolUsed, build_service
from jarvis_core.audit import AuditLog
from jarvis_core.config import Settings
from jarvis_core.config.settings import McpServerConfig
from jarvis_core.hardware import check_ollama
from jarvis_core.policy import RiskClass
from mcp_servers.weather.server import get_weather_data

pytestmark = [pytest.mark.live, pytest.mark.anyio]


def _online() -> bool:
    try:
        socket.create_connection(("api.open-meteo.com", 443), timeout=3).close()
    except OSError:
        return False
    return True


async def test_live_weather_dortmund() -> None:
    if not _online():
        pytest.skip("Kein Netz zu Open-Meteo")
    result = get_weather_data("Dortmund")
    assert result["ok"] is True
    assert result["ort"] == "Dortmund"
    assert -40 < result["temperatur_c"] < 50
    assert result["wind_kmh"] >= 0


async def test_live_agent_calls_weather_tool(tmp_path: Path) -> None:
    if not _online():
        pytest.skip("Kein Netz zu Open-Meteo")
    base = Settings(data_dir=tmp_path / "data")
    status = check_ollama(base.ollama_base_url)
    if not status.running or not status.models:
        pytest.skip("Ollama nicht erreichbar oder ohne Modell")
    cfg = McpServerConfig(
        command="python", args=["-m", "mcp_servers.weather"], tools={"get_weather": RiskClass.READ}
    )
    settings = base.model_copy(update={"mcp_servers": {"weather": cfg}})
    service = build_service(
        settings, AuditLog(settings.audit_db_path), model_name=base.model or status.models[0]
    )
    events = [e async for e in service.stream("Wie ist das Wetter in Dortmund?", channel="cli")]
    assert ToolUsed("get_weather") in events
    assert "".join(e.text for e in events if isinstance(e, TextDelta)).strip()
    with sqlite3.connect(settings.audit_db_path) as conn:
        assert (
            conn.execute("SELECT COUNT(*) FROM audit WHERE tool = 'get_weather'").fetchone()[0] >= 1
        )
