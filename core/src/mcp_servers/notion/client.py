"""Notion-API-Client: AUSSCHLIESSLICH LESEN.

Es gibt hier keinen Schreib-Endpunkt (kein Anlegen, Ändern, Archivieren, Kommentieren). Die
Allowlist unten ist die einzige Stelle, die Requests zulässt; alles andere wird vor dem Netz
verworfen. Rohantworten verlassen dieses Paket nicht (siehe normalize.py).
"""

import json
import re
import sys
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from mcp_servers.notion.jsonutil import as_dict, as_list

NOTION_API_URL = "https://api.notion.com/v1"
# Die konfigurierten IDs sind Data-Source-IDs (Schema-Audit im alten Projekt jarvis-os).
NOTION_VERSION = "2026-03-11"
TIMEOUT_SECONDS = 15.0
MAX_QUERY_PAGES = 3  # je 100 Einträge

_UUID = r"[0-9a-fA-F]{8}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{12}"
# Einzige erlaubte Zugriffe: Datenbank abfragen (POST ist hier eine LESE-Abfrage), Seite und
# deren Blöcke lesen.
ALLOWED_REQUESTS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("POST", re.compile(rf"^/data_sources/{_UUID}/query$")),
    ("GET", re.compile(rf"^/pages/{_UUID}$")),
    ("GET", re.compile(rf"^/blocks/{_UUID}/children$")),
)

ERROR_TEXTS: dict[str, str] = {
    "not_configured": "Kein Notion-Token gesetzt. Bitte 'jarvis secrets set notion' ausführen.",
    "unauthorized": (
        "Notion hat den Token abgelehnt (ungültig oder widerrufen). "
        "Bitte 'jarvis secrets set notion' erneut ausführen."
    ),
    "not_found": (
        "Die Datenbank bzw. Seite wurde nicht gefunden oder ist nicht mit der Integration "
        "geteilt (in Notion: Seite > Verbindungen > Integration hinzufügen)."
    ),
    "rate_limited": "Notion begrenzt gerade die Anfragen. Bitte später erneut versuchen.",
    "unavailable": "Notion ist gerade nicht erreichbar.",
    "invalid_response": "Notion lieferte eine unerwartete Antwort.",
    "forbidden_request": "Dieser Notion-Zugriff ist nicht erlaubt (nur Lesen).",
}


class NotionError(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason

    @property
    def text(self) -> str:
        return ERROR_TEXTS.get(self.reason, ERROR_TEXTS["unavailable"])


@dataclass(frozen=True)
class Response:
    status: int
    body: str


# (method, url, headers, body) -> Response. In Tests ersetzt.
Transport = Callable[[str, str, dict[str, str], bytes | None], Response]


def urllib_transport(
    method: str, url: str, headers: dict[str, str], body: bytes | None
) -> Response:
    request = urllib.request.Request(url, data=body, headers=headers, method=method)  # noqa: S310
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as resp:  # noqa: S310
            return Response(resp.status, resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as err:
        return Response(err.code, err.read().decode("utf-8", "replace"))


def normalize_token(raw: str) -> str:
    """Entfernt Whitespace, ein mitkopiertes 'Bearer ' und umschließende Anführungszeichen."""
    value = raw.strip()
    value = re.sub(r"^Bearer\s+", "", value, flags=re.IGNORECASE)
    return value.strip("\"'").strip()


def log_error(step: str, status: int | None, code: str) -> None:
    """Diagnose: nur Schritt, HTTP-Status und Notion-Fehlercode; nie Token oder Antworttexte."""
    print(f"[notion:{step}] status={status if status is not None else 'n/a'} code={code}",
          file=sys.stderr)  # fmt: skip


def _error_code(body: str) -> str:
    try:
        code = as_dict(json.loads(body)).get("code")
    except ValueError:
        return "n/a"
    return code if isinstance(code, str) else "n/a"


class NotionClient:
    def __init__(self, token: str, transport: Transport = urllib_transport) -> None:
        self._token = normalize_token(token)
        if not self._token:
            raise NotionError("not_configured")
        self._transport = transport

    def _request(
        self, step: str, method: str, path: str, body: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        if not any(m == method and rx.match(path) for m, rx in ALLOWED_REQUESTS):
            log_error(step, None, "forbidden_request")
            raise NotionError("forbidden_request")
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json",
        }
        payload = json.dumps(body).encode() if body is not None else None
        try:
            response = self._transport(method, f"{NOTION_API_URL}{path}", headers, payload)
        except (OSError, TimeoutError) as exc:
            log_error(step, None, f"network_error:{type(exc).__name__}")
            raise NotionError("unavailable") from None
        if response.status == 401:
            log_error(step, 401, _error_code(response.body))
            raise NotionError("unauthorized")
        if response.status in (403, 404):
            log_error(step, response.status, _error_code(response.body))
            raise NotionError("not_found")
        if response.status == 429:
            log_error(step, 429, _error_code(response.body))
            raise NotionError("rate_limited")
        if not 200 <= response.status < 300:
            log_error(step, response.status, _error_code(response.body))
            raise NotionError("unavailable")
        try:
            data: Any = json.loads(response.body)
        except ValueError:
            log_error(step, response.status, "invalid_response:not_json")
            raise NotionError("invalid_response") from None
        result = as_dict(data)
        if not result:
            log_error(step, response.status, "invalid_response:not_object")
            raise NotionError("invalid_response")
        return result

    def query_database(self, source_id: str, step: str) -> list[dict[str, Any]]:
        """Alle Einträge einer Datenbank (bis zu MAX_QUERY_PAGES Seiten). Nur Lesen."""
        results: list[dict[str, Any]] = []
        cursor: str | None = None
        for _ in range(MAX_QUERY_PAGES):
            body: dict[str, Any] = {"page_size": 100}
            if cursor:
                body["start_cursor"] = cursor
            data = self._request(step, "POST", f"/data_sources/{source_id}/query", body)
            if not isinstance(data.get("results"), list):
                log_error(step, 200, "invalid_response:no_results")
                raise NotionError("invalid_response")
            results += [d for d in map(as_dict, as_list(data["results"])) if d]
            next_cursor = data.get("next_cursor")
            if not data.get("has_more") or not isinstance(next_cursor, str):
                break
            cursor = next_cursor
        return results

    def get_page(self, page_id: str) -> dict[str, Any]:
        return self._request("PAGE", "GET", f"/pages/{page_id}")

    def get_blocks(self, page_id: str) -> list[dict[str, Any]]:
        data = self._request("BLOCKS", "GET", f"/blocks/{page_id}/children")
        if not isinstance(data.get("results"), list):
            raise NotionError("invalid_response")
        return [d for d in map(as_dict, as_list(data["results"])) if d]
