"""Zugangsdaten im OS-Schlüsselbund (keyring). Werte werden nie ausgegeben oder geloggt.

Die MCP-Server lesen denselben Eintrag selbst (Dienst "jarvis", Name = Secret-Name); das Format
ist JSON mit den Feldern des Secrets, z. B. {"username", "password"} oder {"token"} (ADR-006).
"""

import json
from dataclasses import dataclass

import keyring
from keyring.errors import KeyringError, PasswordDeleteError

KEYRING_SERVICE = "jarvis"


@dataclass(frozen=True)
class SecretField:
    key: str
    label: str
    hidden: bool = False


@dataclass(frozen=True)
class SecretSpec:
    name: str
    fields: tuple[SecretField, ...]


SECRETS: dict[str, SecretSpec] = {
    "apple_caldav": SecretSpec(
        "apple_caldav",
        (
            SecretField("username", "Apple-ID"),
            SecretField("password", "App-spezifisches Passwort", hidden=True),
        ),
    ),
    "notion": SecretSpec(
        "notion", (SecretField("token", "Notion-Integration-Token", hidden=True),)
    ),
}


class SecretsError(Exception):
    """Schlüsselbund nicht verfügbar oder Zugriff fehlgeschlagen (deutsche Meldung)."""


def store_secret(name: str, **values: str) -> None:
    spec = SECRETS.get(name)
    if spec is None:
        raise SecretsError(f"Unbekanntes Secret '{name}'. Bekannt: {', '.join(SECRETS)}.")
    keys = {f.key for f in spec.fields}
    if set(values) != keys or not all(v.strip() for v in values.values()):
        raise SecretsError("Alle Felder müssen ausgefüllt sein.")
    try:
        keyring.set_password(KEYRING_SERVICE, name, json.dumps(values))
    except KeyringError as exc:
        raise SecretsError(
            f"Der OS-Schlüsselbund ist nicht verfügbar ({type(exc).__name__})."
        ) from None


def delete_secret(name: str) -> bool:
    """Entfernt den Eintrag. False, wenn keiner gesetzt war."""
    if name not in SECRETS:
        raise SecretsError(f"Unbekanntes Secret '{name}'. Bekannt: {', '.join(SECRETS)}.")
    try:
        if keyring.get_password(KEYRING_SERVICE, name) is None:
            return False
        keyring.delete_password(KEYRING_SERVICE, name)
    except PasswordDeleteError:
        return False
    except KeyringError as exc:
        raise SecretsError(
            f"Der OS-Schlüsselbund ist nicht verfügbar ({type(exc).__name__})."
        ) from None
    return True


def is_set(name: str) -> bool:
    """Nur ob ein Wert gesetzt ist, nie der Wert selbst."""
    return secret_state(name) == "gesetzt"


def secret_state(name: str) -> str:
    """'gesetzt', 'nicht gesetzt' oder, wenn der OS-Schlüsselbund fehlt, ein klarer Hinweis."""
    try:
        present = keyring.get_password(KEYRING_SERVICE, name) is not None
    except KeyringError:
        return "Schlüsselbund nicht verfügbar"
    return "gesetzt" if present else "nicht gesetzt"
