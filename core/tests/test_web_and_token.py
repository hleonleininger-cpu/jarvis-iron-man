"""Web-Client (ADR-010) und Zugangstoken für die API (ADR-011)."""

import json
import re

import keyring
import pytest
from fastapi.testclient import TestClient
from pydantic_ai.models.test import TestModel
from typer.testing import CliRunner

from jarvis_core.api import create_app
from jarvis_core.api.app import WEB_FILES, token_required
from jarvis_core.cli.main import app as cli
from jarvis_core.config import Settings
from jarvis_core.secrets import is_set, read_secret_field
from tests.test_calendar_server import MemoryKeyring

TOKEN = "geheimes-token-123"


def _client(settings: Settings, token: str | None = None) -> TestClient:
    return TestClient(
        create_app(settings, TestModel(custom_output_text="Hallo"), "test", api_token=token),
        base_url=f"http://{settings.host}",
    )


# ------------------------------------------------------------------------------- Web-Client
@pytest.mark.parametrize("path", list(WEB_FILES))
def test_web_files_are_served_with_strict_headers(settings: Settings, path: str) -> None:
    response = _client(settings).get(path)
    assert response.status_code == 200
    csp = response.headers["content-security-policy"]
    assert "default-src 'none'" in csp and "script-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["content-type"].startswith(WEB_FILES[path][1].split(";")[0])


def test_web_client_loads_nothing_from_outside(settings: Settings) -> None:
    client = _client(settings)
    for path in WEB_FILES:
        text = client.get(path).text
        assert not re.search(r"https?://", text), path  # kein CDN, keine Schriften, kein Tracking


def test_web_client_never_injects_html() -> None:
    from jarvis_core.api.app import WEB_DIR

    script = (WEB_DIR / "app.js").read_text(encoding="utf-8")
    assert "innerHTML" not in script and "insertAdjacentHTML" not in script
    assert "eval(" not in script and "new Function" not in script


def test_web_pages_are_open_but_api_is_guarded(settings: Settings) -> None:
    tailnet = settings.model_copy(update={"host": "100.101.102.103"})
    client = _client(tailnet, TOKEN)
    assert client.get("/").status_code == 200
    assert client.get("/health").status_code == 200


# ------------------------------------------------------------------------------- Token
def test_token_is_required_off_localhost(settings: Settings) -> None:
    assert token_required(settings) is False
    assert token_required(settings.model_copy(update={"host": "100.101.102.103"})) is True
    assert token_required(settings.model_copy(update={"require_api_token": True})) is True
    with pytest.raises(ValueError, match="Zugangstoken"):
        create_app(settings.model_copy(update={"host": "100.101.102.103"}), TestModel(), "t")


@pytest.mark.parametrize(
    "header", [None, "Bearer falsch", "geheimes-token-12", "Bearer geheimes-token-1234", ""]
)
def test_wrong_or_missing_token_is_rejected(settings: Settings, header: str | None) -> None:
    client = _client(settings.model_copy(update={"require_api_token": True}), TOKEN)
    headers = {"Authorization": header} if header is not None else {}
    for method, path, body in (
        ("POST", "/v1/chat", {"message": "Hallo"}),
        ("POST", "/v1/confirm/abc", {"approve": True}),
        ("DELETE", "/v1/sessions/abc", None),
    ):
        response = client.request(method, path, json=body, headers=headers)
        assert response.status_code == 401, (method, path)
        assert response.headers["www-authenticate"] == "Bearer"


def test_right_token_is_accepted(settings: Settings) -> None:
    client = _client(settings.model_copy(update={"require_api_token": True}), TOKEN)
    with client.stream(
        "POST", "/v1/chat", json={"message": "Hallo"}, headers={"Authorization": f"Bearer {TOKEN}"}
    ) as response:
        body = "".join(response.iter_text())
    assert response.status_code == 200 and "event: done" in body
    texts = [
        json.loads(line[6:]).get("text", "")
        for line in body.splitlines()
        if line.startswith("data: ")
    ]
    assert "".join(texts) == "Hallo"


def test_generate_stores_a_strong_token_and_shows_it_once() -> None:
    keyring.set_keyring(MemoryKeyring())
    result = CliRunner().invoke(cli, ["secrets", "generate", "api_token"])
    assert result.exit_code == 0
    token = result.stdout.strip()
    assert len(token) >= 43 and read_secret_field("api_token", "token") == token
    again = CliRunner().invoke(cli, ["secrets", "generate", "api_token"]).stdout.strip()
    assert again != token and is_set("api_token")
    assert CliRunner().invoke(cli, ["secrets", "generate", "notion"]).exit_code == 1


def test_serve_off_localhost_refuses_without_token(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import typer
    import uvicorn

    from jarvis_core.cli import main as cli_main

    keyring.set_keyring(MemoryKeyring())
    monkeypatch.setattr(
        cli_main, "load_settings",
        lambda: settings.model_copy(update={"require_api_token": True, "model": "m"}),
    )  # fmt: skip
    started: list[object] = []

    def fake_run(app: object, **kwargs: object) -> None:
        started.append(app)

    monkeypatch.setattr(uvicorn, "run", fake_run)
    with pytest.raises(typer.Exit):
        cli_main.serve()
    assert started == [] and "jarvis secrets generate api_token" in capsys.readouterr().err

    CliRunner().invoke(cli, ["secrets", "generate", "api_token"])
    cli_main.serve()
    assert len(started) == 1 and "Zugang nur mit Token" in capsys.readouterr().err
