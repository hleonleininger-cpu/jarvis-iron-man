from pathlib import Path

import pytest
from pydantic import ValidationError

from jarvis_core.config import Settings


def test_defaults_bind_localhost_only() -> None:
    s = Settings()
    assert s.host == "127.0.0.1"
    assert s.ollama_base_url == "http://127.0.0.1:11434/v1"
    assert s.model is None


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "[::]", "*", ""])
def test_wildcard_host_rejected(host: str) -> None:
    with pytest.raises(ValidationError, match="0.0.0.0"):
        Settings(host=host)


def test_wildcard_host_rejected_via_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JARVIS_HOST", "0.0.0.0")
    with pytest.raises(ValidationError):
        Settings()


def test_env_overrides_toml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "jarvis.toml").write_text('model = "aus-toml"\nport = 9000\n', encoding="utf-8")
    assert Settings().model == "aus-toml"
    monkeypatch.setenv("JARVIS_MODEL", "aus-env")
    s = Settings()
    assert s.model == "aus-env"
    assert s.port == 9000


def test_unknown_toml_key_rejected(tmp_path: Path) -> None:
    (tmp_path / "jarvis.toml").write_text('api_key = "geheim"\n', encoding="utf-8")
    with pytest.raises(ValidationError):
        Settings()


def test_non_http_ollama_url_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(ollama_base_url="file:///etc/passwd")


def test_mcp_servers_from_toml(tmp_path: Path) -> None:
    (tmp_path / "jarvis.toml").write_text(
        '[mcp_servers.weather]\ncommand = "python"\nargs = ["-m", "mcp_servers.weather"]\n'
        '[mcp_servers.weather.tools]\nget_weather = "READ"\n',
        encoding="utf-8",
    )
    server = Settings().mcp_servers["weather"]
    assert server.command == "python"
    assert server.tools == {"get_weather": "READ"}


def test_no_mcp_servers_by_default() -> None:
    assert Settings().mcp_servers == {}


def test_unknown_risk_class_rejected(tmp_path: Path) -> None:
    (tmp_path / "jarvis.toml").write_text(
        '[mcp_servers.x]\ncommand = "python"\n[mcp_servers.x.tools]\nfoo = "HARMLOS"\n',
        encoding="utf-8",
    )
    with pytest.raises(ValidationError):
        Settings()
