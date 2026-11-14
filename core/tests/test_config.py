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


def test_logging_survives_a_closed_stderr(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ein Logeintrag darf nicht scheitern, wenn das beim Konfigurieren aktive stderr zu ist."""
    import io
    import sys

    import structlog

    from jarvis_core.config import configure_logging

    stale = io.StringIO()
    monkeypatch.setattr(sys, "stderr", stale)
    configure_logging("INFO")
    stale.close()
    structlog.get_logger("test").warning("nach_dem_schliessen")  # darf nicht werfen

    fresh = io.StringIO()
    monkeypatch.setattr(sys, "stderr", fresh)
    structlog.get_logger("test").warning("auf_neuem_stderr")
    assert "auf_neuem_stderr" in fresh.getvalue()


def test_log_level_is_validated_and_normalized() -> None:
    assert Settings(log_level=" debug ").log_level == "DEBUG"
    with pytest.raises(ValidationError, match="log_level"):
        Settings(log_level="laut")


# --------------------------------------------------------------------------- Runde 4
def test_config_file_can_be_named_by_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from jarvis_core.config import load_settings

    elsewhere = tmp_path / "anderswo" / "meine.toml"
    elsewhere.parent.mkdir()
    elsewhere.write_text('port = 9999\nmodel = "aus-der-datei"\n', encoding="utf-8")
    monkeypatch.setenv("JARVIS_CONFIG", str(elsewhere))
    settings = load_settings()
    assert settings.port == 9999 and settings.model == "aus-der-datei"


def test_named_config_file_that_is_missing_is_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from jarvis_core.config import ConfigFileMissing, load_settings

    monkeypatch.setenv("JARVIS_CONFIG", str(tmp_path / "gibt-es-nicht.toml"))
    with pytest.raises(ConfigFileMissing, match="gibt-es-nicht.toml"):
        load_settings()


def test_doctor_shows_config_servers_and_secrets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import keyring
    from typer.testing import CliRunner

    from jarvis_core.cli.main import app
    from jarvis_core.secrets import store_secret
    from tests.test_calendar_server import MemoryKeyring

    keyring.set_keyring(MemoryKeyring())
    store_secret("notion", token="ntn_geheim")
    (tmp_path / "jarvis.toml").write_text(
        'ollama_base_url = "http://127.0.0.1:1/v1"\n'
        '[mcp_servers.weather]\ncommand = "python"\n[mcp_servers.weather.tools]\n'
        'get_weather = "READ"\n',
        encoding="utf-8",
    )
    out = CliRunner().invoke(app, ["doctor"]).stdout
    assert "jarvis.toml (gefunden)" in out
    assert "MCP-Server" in out and "weather" in out
    assert "notion: gesetzt" in out and "apple_caldav: nicht gesetzt" in out
    assert "ntn_geheim" not in out

    (tmp_path / "jarvis.toml").unlink()
    out = CliRunner().invoke(app, ["doctor"]).stdout
    assert "nicht gefunden, es gelten Standardwerte" in out and "keine (ohne Werkzeuge)" in out


def test_missing_named_config_is_reported_by_the_cli(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from typer.testing import CliRunner

    from jarvis_core.cli.main import app

    monkeypatch.setenv("JARVIS_CONFIG", str(tmp_path / "fehlt.toml"))
    result = CliRunner().invoke(app, ["doctor"])
    assert result.exit_code == 2 and "fehlt.toml" in result.output


def test_serve_announces_its_address(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import uvicorn

    from jarvis_core.cli import main as cli_main

    started: list[tuple[str, int]] = []

    def fake_run(app: object, host: str, port: int, log_config: object) -> None:
        started.append((host, port))

    monkeypatch.setattr(uvicorn, "run", fake_run)
    monkeypatch.setattr(
        cli_main, "load_settings", lambda: Settings(data_dir=tmp_path, model="testmodell")
    )
    cli_main.serve()
    assert started == [("127.0.0.1", 8765)]
    assert "http://127.0.0.1:8765 (Modell testmodell)" in capsys.readouterr().err


def test_relative_data_dir_lives_next_to_the_config_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from jarvis_core.config import load_settings

    home = tmp_path / "jarvis"
    home.mkdir()
    (home / "jarvis.toml").write_text('data_dir = "daten"\n', encoding="utf-8")
    monkeypatch.setenv("JARVIS_CONFIG", str(home / "jarvis.toml"))
    monkeypatch.chdir(tmp_path)  # anderer Startordner
    assert load_settings().data_dir == home.resolve() / "daten"


def test_secrets_status_says_when_keyring_is_missing() -> None:
    import keyring
    from keyring.errors import KeyringError
    from typer.testing import CliRunner

    from jarvis_core.cli.main import app
    from tests.test_calendar_server import MemoryKeyring

    class Broken(MemoryKeyring):
        def get_password(self, service: str, username: str) -> str | None:
            raise KeyringError("kein Backend")

    keyring.set_keyring(Broken())
    out = CliRunner().invoke(app, ["secrets", "status"]).stdout
    assert "notion: Schlüsselbund nicht verfügbar" in out


# --------------------------------------------------------------------------- Runde 7
@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:11434/v1",
        "http://localhost:11434/v1",
        "http://[::1]:11434/v1",
        "http://192.168.1.20:11434/v1",
        "http://100.101.102.103:11434/v1",
        "http://gaming-pc:11434/v1",
        "http://gaming-pc.tail1234.ts.net:11434/v1",
        "http://ollama.local:11434/v1",
    ],
)
def test_local_model_endpoints_are_allowed(url: str) -> None:
    assert Settings(ollama_base_url=url).ollama_base_url == url.rstrip("/")


@pytest.mark.parametrize(
    "url",
    [
        "https://api.openai.com/v1",
        "https://openrouter.ai/api/v1",
        "http://8.8.8.8:11434/v1",
        "http://127.0.0.1.nip.io/v1",
    ],
)
def test_cloud_model_endpoints_are_rejected(url: str) -> None:
    with pytest.raises(ValidationError, match="keine Cloud-Modelle"):
        Settings(ollama_base_url=url)


def test_serve_reports_a_busy_port(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import socket

    import typer
    import uvicorn

    from jarvis_core.cli import main as cli_main

    with socket.socket() as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen()
        port = busy.getsockname()[1]
        monkeypatch.setattr(
            cli_main, "load_settings", lambda: Settings(data_dir=tmp_path, port=port, model="m")
        )

        def must_not_start(*args: object, **kwargs: object) -> None:
            pytest.fail("darf nicht starten")

        monkeypatch.setattr(uvicorn, "run", must_not_start)
        with pytest.raises(typer.Exit) as err:
            cli_main.serve()
    assert err.value.exit_code == 1
    assert "Läuft JARVIS schon?" in capsys.readouterr().err


def test_audit_verify_does_not_create_a_log_and_names_the_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from typer.testing import CliRunner

    from jarvis_core.audit import AuditLog
    from jarvis_core.cli.main import app

    result = CliRunner().invoke(app, ["audit", "verify"])
    assert result.exit_code == 1 and "Kein Audit-Log" in result.output
    assert not (tmp_path / "data").exists()
    AuditLog(tmp_path / "data" / "audit.sqlite3").record(
        channel="cli", model="m", tokens=1, status="ok"
    )
    result = CliRunner().invoke(app, ["audit", "verify"])
    assert result.exit_code == 0 and "audit.sqlite3" in result.output


def test_doctor_does_not_call_the_model_missing_when_ollama_is_down(tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from jarvis_core.cli.main import app

    (tmp_path / "jarvis.toml").write_text(
        'ollama_base_url = "http://127.0.0.1:1/v1"\nmodel = "qwen3.5:4b"\n', encoding="utf-8"
    )
    out = CliRunner().invoke(app, ["doctor"]).stdout
    assert "qwen3.5:4b (unbekannt, Ollama nicht erreichbar)" in out and "FEHLT" not in out


def test_serve_explains_an_unavailable_address(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import typer

    from jarvis_core.cli import main as cli_main

    monkeypatch.setattr(
        cli_main, "load_settings",
        lambda: Settings(data_dir=tmp_path, host="100.64.255.254", model="m"),
    )  # fmt: skip
    with pytest.raises(typer.Exit):
        cli_main.serve()
    err = capsys.readouterr().err
    assert "Tailscale verbunden" in err and "Läuft JARVIS schon" not in err


def test_cli_chat_error_shows_no_json_log_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    import typer

    from jarvis_core.cli import main as cli_main

    monkeypatch.setattr(
        cli_main, "load_settings",
        lambda: Settings(data_dir=tmp_path, ollama_base_url="http://127.0.0.1:1/v1", model="m"),
    )  # fmt: skip
    with pytest.raises(typer.Exit):
        cli_main.chat("Hallo")
    err = capfd.readouterr().err
    assert "nicht erreichbar" in err and '"event": "chat_failed"' not in err


def test_port_check_accepts_a_port_in_time_wait(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nach einem Neustart hängen alte Verbindungen in TIME_WAIT; das ist kein belegter Port."""
    import socket
    import threading

    from jarvis_core.cli.main import _ensure_port_free  # pyright: ignore[reportPrivateUsage]

    server = socket.socket()
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", 0))
    port = server.getsockname()[1]
    server.listen()
    client = socket.create_connection(("127.0.0.1", port))
    accepted, _ = server.accept()
    accepted.close()  # Server schließt zuerst -> seine Seite geht in TIME_WAIT
    threading.Event().wait(0.1)
    client.close()
    server.close()
    _ensure_port_free("127.0.0.1", port)  # darf nicht mit "belegt" abbrechen


# --------------------------------------------------------------------------- jarvis init
def test_init_creates_a_valid_config_and_never_overwrites(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from typer.testing import CliRunner

    from jarvis_core.cli.main import app
    from jarvis_core.config import load_settings

    result = CliRunner().invoke(app, ["init"])
    assert (
        result.exit_code == 0
        and "ollama pull" in result.stdout
        and "OLLAMA_CONTEXT_LENGTH=8192" in result.stdout
    )
    created = tmp_path / "jarvis.toml"
    settings = load_settings()  # die Vorlage muss gültig sein
    assert set(settings.mcp_servers) == {
        "weather",
        "websearch",
        "calendar",
        "notes",
        "projekte",
    }  # Notion erst nach Einrichtung
    assert settings.host == "127.0.0.1"

    created.write_text("# eigene Änderung\n", encoding="utf-8")
    again = CliRunner().invoke(app, ["init"])
    assert again.exit_code == 1 and "nichts geändert" in again.output
    assert created.read_text(encoding="utf-8") == "# eigene Änderung\n"


def test_init_honours_jarvis_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from typer.testing import CliRunner

    from jarvis_core.cli.main import app

    target = tmp_path / "anderswo" / "jarvis.toml"
    monkeypatch.setenv("JARVIS_CONFIG", str(target))
    assert CliRunner().invoke(app, ["init"]).exit_code == 0
    assert target.is_file()


def test_serve_open_starts_the_browser(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import threading
    import webbrowser

    import uvicorn

    from jarvis_core.cli import main as cli_main

    opened: list[str] = []

    class Immediately:
        def __init__(self, delay: float, function: object, args: tuple[str]) -> None:
            self.args = args

        def start(self) -> None:
            opened.append(self.args[0])

    monkeypatch.setattr(threading, "Timer", Immediately)

    def wrong_open(url: str) -> None:
        opened.append("falsch")

    def no_server(*args: object, **kwargs: object) -> None:
        return None

    monkeypatch.setattr(webbrowser, "open", wrong_open)
    monkeypatch.setattr(uvicorn, "run", no_server)
    monkeypatch.setattr(cli_main, "load_settings", lambda: Settings(data_dir=tmp_path, model="m"))
    cli_main.serve(open_browser=True)
    assert opened == ["http://127.0.0.1:8765/"]
    opened.clear()
    cli_main.serve()  # ohne --open: kein Browser
    assert opened == []


# --------------------------------------------------------------------------- Runde 13
def test_toml_syntax_error_is_reported_without_traceback(tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from jarvis_core.cli.main import app

    (tmp_path / "jarvis.toml").write_text('port = 8765\nmodel = "qwen\n', encoding="utf-8")
    result = CliRunner().invoke(app, ["doctor"])
    assert result.exit_code == 2
    assert "Ungültige Konfiguration" in result.output and "line 2" in result.output
    assert "Traceback" not in result.output


def test_broken_audit_log_stops_with_a_clear_message(tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from jarvis_core.cli.main import app

    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "audit.sqlite3").write_bytes(b"das ist keine Datenbank" * 100)
    for command in (["audit", "verify"], ["chat", "Hallo"]):
        result = CliRunner().invoke(app, command)
        assert result.exit_code == 1, command
        assert "Audit-Log" in result.output and "nicht nutzbar" in result.output


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        (None, "OLLAMA_CONTEXT_LENGTH nicht gesetzt (Ollama-Standard 4096)"),
        ("2048", "WARNUNG: OLLAMA_CONTEXT_LENGTH=2048 ist kleiner"),
        ("16384", "OLLAMA_CONTEXT_LENGTH=16384"),
    ],
)
def test_doctor_explains_the_context_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, env: str | None, expected: str
) -> None:
    from typer.testing import CliRunner

    from jarvis_core.cli.main import app

    if env is None:
        monkeypatch.delenv("OLLAMA_CONTEXT_LENGTH", raising=False)
    else:
        monkeypatch.setenv("OLLAMA_CONTEXT_LENGTH", env)
    (tmp_path / "jarvis.toml").write_text(
        'ollama_base_url = "http://127.0.0.1:1/v1"\ncontext_tokens = 4096\n', encoding="utf-8"
    )
    out = CliRunner().invoke(app, ["doctor"]).stdout
    assert "Kontextfenster" in out and expected in " ".join(out.split())


def test_example_notion_section_is_valid_once_uncommented(tmp_path: Path) -> None:
    """Die auskommentierte Notion-Vorlage muss gültig sein, sobald man sie einschaltet."""
    import re

    from jarvis_core.cli.main import EXAMPLE_CONFIG
    from jarvis_core.config import load_settings

    text = EXAMPLE_CONFIG.read_text(encoding="utf-8")
    block = text[text.index("# [mcp_servers.notion]") :]
    enabled = re.sub(r"(?m)^# ?", "", block)
    (tmp_path / "jarvis.toml").write_text(enabled, encoding="utf-8")
    notion = load_settings().mcp_servers["notion"]
    assert set(notion.tools) == {
        "notion_search", "notion_query", "notion_get", "notion_add_entry",
    }  # fmt: skip
    assert notion.tools["notion_add_entry"] == "WRITE_REVERSIBLE"  # mit Rückfrage, nie still
    assert notion.previews == {"notion_add_entry": "preview_notion_add_entry"}
