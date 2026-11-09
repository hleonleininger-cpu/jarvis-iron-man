"""Zugangsdaten im OS-Schlüsselbund (keyring). Werte werden nie ausgegeben oder geloggt.

Die MCP-Server lesen denselben Eintrag selbst (Dienst "jarvis", Name = Secret-Name); das Format
ist JSON {"username": ..., "password": ...} (siehe ADR-006).
"""

import json
from dataclasses import dataclass

import keyring
from keyring.errors import KeyringError

KEYRING_SERVICE = "jarvis"


@dataclass(frozen=True)
class SecretSpec:
    name: str
    username_label: str
    password_label: str


SECRETS: dict[str, SecretSpec] = {
    "apple_caldav": SecretSpec("apple_caldav", "Apple-ID", "App-spezifisches Passwort"),
}


class SecretsError(Exception):
    """Schlüsselbund nicht verfügbar oder Zugriff fehlgeschlagen (deutsche Meldung)."""


def store_secret(name: str, username: str, password: str) -> None:
    if name not in SECRETS:
        raise SecretsError(f"Unbekanntes Secret '{name}'. Bekannt: {', '.join(SECRETS)}.")
    if not username or not password:
        raise SecretsError("Benutzername und Passwort dürfen nicht leer sein.")
    try:
        keyring.set_password(
            KEYRING_SERVICE, name, json.dumps({"username": username, "password": password})
        )
    except KeyringError as exc:
        raise SecretsError(
            f"Der OS-Schlüsselbund ist nicht verfügbar ({type(exc).__name__})."
        ) from None


def is_set(name: str) -> bool:
    """Nur ob ein Wert gesetzt ist, nie der Wert selbst."""
    try:
        return keyring.get_password(KEYRING_SERVICE, name) is not None
    except KeyringError:
        return False
