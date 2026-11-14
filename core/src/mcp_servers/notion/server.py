"""MCP-Server `notion`: lesen (notion_search, notion_query, notion_get) und neue Einträge
anlegen (notion_add_entry, ADR-017). Ändern oder Löschen gibt es nicht.

Läuft als eigener Prozess (stdio) und importiert nichts aus jarvis_core. Der Integration-Token
kommt aus dem OS-Schlüsselbund (Dienst "jarvis", Eintrag "notion", JSON {"token": ...}); die
Zuordnung Name -> Datenbank-ID kommt aus der Umgebung des Servers (in jarvis.toml unter
[mcp_servers.notion] env: NOTION_DB_<NAME> = "<ID>"). notion_add_entry schreibt sofort; die
Bestätigung durch den Nutzer erzwingt der Core (Policy) anhand der Vorschau.
"""

import json
import os
from typing import Any

import keyring
from keyring.errors import KeyringError
from mcp.server.mcpserver import MCPServer

from mcp_servers.notion.client import MAX_QUERY_PAGES, NotionClient, NotionError, is_notion_id
from mcp_servers.notion.normalize import (
    blocks_to_text,
    extract_page_id,
    fit_budget,
    flatten_page,
    is_done,
    matches,
    normalize_page,
    sort_entries,
)

KEYRING_SERVICE = "jarvis"
KEYRING_ENTRY = "notion"
ENV_PREFIX = "NOTION_DB_"
KNOWN_DATABASES = ("mission_control", "academy", "forge", "olympus", "vault")
MAX_SEARCH_RESULTS = 10
MAX_TITLE_LENGTH = 200
MAX_NOTE_LENGTH = 2000
MAX_NOTE_PREVIEW = 300


def configured_databases() -> dict[str, str]:
    """Name -> Datenbank-ID aus NOTION_DB_<NAME> (Name kleingeschrieben)."""
    return {
        key[len(ENV_PREFIX) :].lower(): value.strip()
        for key, value in os.environ.items()
        if key.startswith(ENV_PREFIX) and value.strip()
    }


def load_token() -> str:
    try:
        raw = keyring.get_password(KEYRING_SERVICE, KEYRING_ENTRY)
    except KeyringError:
        raise NotionError("keyring_unavailable") from None
    if not raw:
        raise NotionError("not_configured")
    try:
        data: Any = json.loads(raw)
        return str(data["token"])
    except (ValueError, KeyError, TypeError):
        raise NotionError("not_configured") from None


def make_client() -> NotionClient:
    """Wird in Tests und Evals ersetzt (Notion-Attrappe statt echter API)."""
    return NotionClient(load_token())


def _fail(message: str, reason: str) -> dict[str, Any]:
    return {"ok": False, "fehler": message, "grund": reason}


def _fail_error(error: NotionError) -> dict[str, Any]:
    return _fail(error.text, error.reason)


def _no_database_message(requested: str | None) -> str:
    configured = configured_databases()
    missing = [f"{ENV_PREFIX}{name.upper()}" for name in KNOWN_DATABASES if name not in configured]
    have = ", ".join(sorted(configured)) or "keine"
    head = f"Die Datenbank '{requested}' ist nicht konfiguriert. " if requested else ""
    return (
        f"{head}Konfiguriert: {have}. In jarvis.toml fehlen unter [mcp_servers.notion] env "
        f"(Name -> Datenbank-ID): {', '.join(missing) or 'nichts'}."
    )


def _resolve(name: str | None) -> dict[str, str] | dict[str, Any]:
    """Gewählte Datenbanken oder ein Fehler-Dict."""
    configured = configured_databases()
    if not configured:
        return _fail(_no_database_message(None), "not_configured_database")
    if name is None or not name.strip():
        # Ohne Namen: alle brauchbaren Datenbanken; nur wenn keine brauchbar ist, ein Fehler.
        usable = {k: v for k, v in configured.items() if is_notion_id(v)}
        return usable or _fail(_invalid_id_message(sorted(configured)), "invalid_database_id")
    key = name.strip().lower().replace(" ", "_").replace("-", "_")
    if key not in configured:
        return _fail(_no_database_message(name.strip()), "unknown_database")
    if not is_notion_id(configured[key]):
        return _fail(_invalid_id_message([key]), "invalid_database_id")
    return {key: configured[key]}


def _invalid_id_message(names: list[str]) -> str:
    variables = ", ".join(f"{ENV_PREFIX}{name.upper()}" for name in names)
    return (
        f"In jarvis.toml steht unter [mcp_servers.notion] env keine gültige Notion-ID für: "
        f"{variables} (erwartet 32 Hex-Zeichen, mit oder ohne Bindestriche)."
    )


INCOMPLETE_HINT = (
    f"Die Datenbank hat mehr als {MAX_QUERY_PAGES * 100} Einträge; nur die ersten wurden gelesen."
)


def _is_error(value: dict[str, str] | dict[str, Any]) -> bool:
    return value.get("ok") is False


def query_data(
    datenbank: str, filter: str | None = None, erledigte_einbeziehen: bool = False
) -> dict[str, Any]:
    if not datenbank.strip():
        # Ohne Namen lieferte _resolve alle Datenbanken; hier ist genau eine gemeint.
        return _fail(f"Bitte eine Datenbank nennen. {_database_hint()}", "invalid_input")
    chosen = _resolve(datenbank)
    if _is_error(chosen):
        return chosen
    ((name, source_id),) = chosen.items()
    try:
        client = make_client()
        pages = client.query_database(source_id, name.upper())
    except NotionError as exc:
        return _fail_error(exc)
    entries: list[dict[str, Any]] = []
    for page in pages:
        entry = normalize_page(page)
        _, values = flatten_page(page)
        if filter and filter.strip() and not matches(entry, values, filter):
            continue
        if not erledigte_einbeziehen and is_done(entry):
            continue
        entries.append(entry)
    total = len(entries)
    shown, truncated = fit_budget(sort_entries(entries))
    result: dict[str, Any] = {"ok": True, "datenbank": name, "anzahl": total, "eintraege": shown}
    hints = [f"Nur die ersten {len(shown)} von {total} Einträgen gezeigt."] if truncated else []
    if client.incomplete:
        hints.append(INCOMPLETE_HINT)
    if hints:
        result["hinweis"] = " ".join(hints)
    return result


def search_data(begriff: str, datenbank: str | None = None) -> dict[str, Any]:
    if not begriff.strip():
        return _fail("Es wurde kein Suchbegriff angegeben.", "invalid_input")
    chosen = _resolve(datenbank)
    if _is_error(chosen):
        return chosen
    try:
        client = make_client()
        hits: list[dict[str, Any]] = []
        incomplete = False
        for name, source_id in chosen.items():
            pages = client.query_database(source_id, name.upper())
            incomplete = incomplete or client.incomplete
            for page in pages:
                entry = normalize_page(page)
                _, values = flatten_page(page)
                if matches(entry, values, begriff):
                    hits.append({"datenbank": name, **entry})
    except NotionError as exc:
        return _fail_error(exc)
    total = len(hits)
    shown, truncated = fit_budget(hits[:MAX_SEARCH_RESULTS])
    result: dict[str, Any] = {
        "ok": True,
        "begriff": begriff.strip(),
        "anzahl": total,
        "treffer": shown,
    }
    hints = (
        [f"Nur die ersten {len(shown)} von {total} Treffern gezeigt."]
        if truncated or total > len(shown)
        else []
    )
    if incomplete:
        hints.append(INCOMPLETE_HINT)
    if hints:
        result["hinweis"] = " ".join(hints)
    return result


def get_data(seite: str) -> dict[str, Any]:
    page_id = extract_page_id(seite)
    if page_id is None:
        return _fail("Das ist keine gültige Notion-Seiten-ID oder -URL.", "invalid_input")
    try:
        client = make_client()
        page = client.get_page(page_id)
        blocks = client.get_blocks(page_id)
    except NotionError as exc:
        return _fail_error(exc)
    result = {"ok": True, **normalize_page(page), "inhalt": blocks_to_text(blocks)}
    if client.incomplete:
        result["hinweis"] = "Die Seite ist länger; nur der Anfang wurde gelesen."
    return result


def _entry_input(datenbank: str, titel: str, notiz: str | None) -> tuple[str, str, str, list[str]]:
    """(Name, Datenbank-ID, Titel, Absätze) oder ValueError mit lesbarer Meldung."""
    title = " ".join(titel.split())
    if not title:
        raise ValueError("Der Eintrag braucht einen Titel.")
    if len(title) > MAX_TITLE_LENGTH:
        raise ValueError(f"Der Titel darf höchstens {MAX_TITLE_LENGTH} Zeichen lang sein.")
    note = (notiz or "").strip()
    if len(note) > MAX_NOTE_LENGTH:
        raise ValueError(f"Die Notiz darf höchstens {MAX_NOTE_LENGTH} Zeichen lang sein.")
    if not datenbank.strip():
        raise ValueError(f"Bitte eine Datenbank nennen. {_database_hint()}")
    chosen = _resolve(datenbank)
    if _is_error(chosen):
        raise ValueError(str(chosen["fehler"]))
    ((name, source_id),) = chosen.items()
    paragraphs = [p.strip() for p in note.replace("\r\n", "\n").split("\n\n") if p.strip()]
    return name, source_id, title, paragraphs


def _entry_text(name: str, title: str, paragraphs: list[str]) -> str:
    text = f"Notion: neuer Eintrag in '{name}': {title}"
    if paragraphs:
        note = "\n\n".join(paragraphs)
        shown = (
            note
            if len(note) <= MAX_NOTE_PREVIEW
            else f"{note[:MAX_NOTE_PREVIEW]}… (gekürzt, {len(note)} Zeichen)"
        )
        text += f", Notiz: {shown}"
    return text


def preview_add_entry_data(datenbank: str, titel: str, notiz: str | None = None) -> dict[str, Any]:
    try:
        name, source_id, title, paragraphs = _entry_input(datenbank, titel, notiz)
        # Zugriff und Titel-Spalte prüfen, BEVOR der Nutzer gefragt wird (liest nur das Schema).
        make_client().title_property(source_id, name.upper())
    except ValueError as exc:
        return _fail(str(exc), "invalid_input")
    except NotionError as exc:
        return _fail_error(exc)
    return {"ok": True, "vorschau": _entry_text(name, title, paragraphs), "frage": "Anlegen?"}


def add_entry_data(datenbank: str, titel: str, notiz: str | None = None) -> dict[str, Any]:
    try:
        name, source_id, title, paragraphs = _entry_input(datenbank, titel, notiz)
        client = make_client()
        column = client.title_property(source_id, name.upper())
        page = client.add_entry(source_id, column, title, paragraphs, name.upper())
    except ValueError as exc:
        return _fail(str(exc), "invalid_input")
    except NotionError as exc:
        return _fail_error(exc)
    result: dict[str, Any] = {"ok": True, "angelegt": _entry_text(name, title, paragraphs)}
    page_id, url = page.get("id"), page.get("url")
    if isinstance(page_id, str):
        result["id"] = page_id
    if isinstance(url, str) and url.startswith(("https://www.notion.so/", "https://notion.so/")):
        result["url"] = url
    return result


def _database_hint() -> str:
    names = sorted(configured_databases())
    return (
        f"Konfigurierte Datenbanken: {', '.join(names)}."
        if names
        else "Keine Datenbank konfiguriert."
    )


mcp = MCPServer("notion", log_level="WARNING")


@mcp.tool(
    description=(
        "Durchsucht die Notion-Datenbanken des Nutzers (JARVIS OS: Missionen, Academy, Notizen) "
        f"nach einem Begriff und liefert passende Einträge. Nur lesend. {_database_hint()}"
    )
)
def notion_search(begriff: str, datenbank: str | None = None) -> dict[str, Any]:
    return search_data(begriff, datenbank)


@mcp.tool(
    description=(
        "Liest die Einträge einer Notion-Datenbank mit Titel, Status, Datum und wichtigen "
        "Feldern, z. B. datenbank='mission_control' für offene Missionen. filter (optional) "
        "engt per Text ein; "
        "erledigte Einträge werden nur mit erledigte_einbeziehen=true gezeigt. Nur lesend. "
        f"{_database_hint()}"
    )
)
def notion_query(
    datenbank: str, filter: str | None = None, erledigte_einbeziehen: bool = False
) -> dict[str, Any]:
    return query_data(datenbank, filter, erledigte_einbeziehen)


@mcp.tool(
    description=(
        "Liest eine einzelne Notion-Seite (Eigenschaften und Textinhalt) anhand ihrer ID oder URL, "
        "z. B. nach einem Treffer aus notion_search. Nur lesend."
    )
)
def notion_get(seite: str) -> dict[str, Any]:
    return get_data(seite)


@mcp.tool(
    description=(
        "Legt einen NEUEN Eintrag (Seite mit Titel und optionalem Text) in einer Notion-Datenbank "
        "an, z. B. datenbank='mission_control'. Ändern oder Löschen ist nicht möglich. Direkt "
        "aufrufen: die Bestätigung holt das System selbst beim Nutzer ein. "
        f"{_database_hint()}"
    )
)
def notion_add_entry(datenbank: str, titel: str, notiz: str | None = None) -> dict[str, Any]:
    return add_entry_data(datenbank, titel, notiz)


@mcp.tool(description="Interne Vorschau für notion_add_entry (schreibt nichts).")
def preview_notion_add_entry(
    datenbank: str, titel: str, notiz: str | None = None
) -> dict[str, Any]:
    return preview_add_entry_data(datenbank, titel, notiz)


def main() -> None:
    mcp.run(transport="stdio")
