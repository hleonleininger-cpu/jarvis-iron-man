import json

import keyring
import pytest
from keyring.errors import KeyringError
from typer.testing import CliRunner

from jarvis_core.cli.main import app
from jarvis_core.secrets import SecretsError, is_set, store_secret
from tests.test_calendar_server import MemoryKeyring

runner = CliRunner()
PASSWORD = "xxxx-yyyy-zzzz-wwww"


@pytest.fixture
def backend() -> MemoryKeyring:
    backend = MemoryKeyring()
    keyring.set_keyring(backend)
    return backend


def test_set_stores_json_in_keyring_and_prints_only_gespeichert(backend: MemoryKeyring) -> None:
    result = runner.invoke(
        app, ["secrets", "set", "apple_caldav"], input=f"me@icloud.com\n{PASSWORD}\n"
    )
    assert result.exit_code == 0
    assert result.stdout.strip().endswith("gespeichert")
    assert (
        PASSWORD not in result.stdout
        and "me@icloud.com" not in result.stdout.split("gespeichert")[-1]
    )
    assert json.loads(backend.store[("jarvis", "apple_caldav")]) == {
        "username": "me@icloud.com",
        "password": PASSWORD,
    }


def test_status_never_shows_values(backend: MemoryKeyring) -> None:
    before = runner.invoke(app, ["secrets", "status"])
    assert "apple_caldav: nicht gesetzt" in before.stdout
    store_secret("apple_caldav", username="me@icloud.com", password=PASSWORD)
    after = runner.invoke(app, ["secrets", "status"])
    assert "apple_caldav: gesetzt" in after.stdout
    assert PASSWORD not in after.stdout and "me@icloud.com" not in after.stdout


def test_unknown_secret_name() -> None:
    result = runner.invoke(app, ["secrets", "set", "gibt_es_nicht"])
    assert result.exit_code == 2


def test_empty_values_rejected(backend: MemoryKeyring) -> None:
    with pytest.raises(SecretsError):
        store_secret("apple_caldav", username="", password="pw")
    assert not is_set("apple_caldav")


def test_keyring_failure_gives_german_error(
    backend: MemoryKeyring, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*args: object) -> None:
        raise KeyringError("kaputt")

    monkeypatch.setattr(backend, "set_password", boom)
    result = runner.invoke(app, ["secrets", "set", "apple_caldav"], input="a\nb\n")
    assert result.exit_code == 1
    assert "Schlüsselbund" in result.output and "Traceback" not in result.output


def test_notion_token_is_asked_hidden_and_stored_as_token_json(backend: MemoryKeyring) -> None:
    result = runner.invoke(app, ["secrets", "set", "notion"], input="ntn_geheimer_token\n")
    assert result.exit_code == 0
    assert result.stdout.strip().endswith("gespeichert")
    assert "ntn_geheimer_token" not in result.stdout
    assert json.loads(backend.store[("jarvis", "notion")]) == {"token": "ntn_geheimer_token"}
    status = runner.invoke(app, ["secrets", "status"])
    assert "notion: gesetzt" in status.stdout and "ntn_geheimer_token" not in status.stdout
