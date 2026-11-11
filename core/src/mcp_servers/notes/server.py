"""MCP-Server `notes`: Notizen und Aufgaben, lokal in einer SQLite-Datei (ADR-012).

Läuft als eigener Prozess (stdio) und importiert nichts aus jarvis_core. Kein Netz. Inhalte
bleiben in der Datei; ins Audit-Log kommt wie immer nur der Werkzeugname.
add_note und complete_note schreiben sofort; die Bestätigung erzwingt der Core (Policy).
"""

import argparse
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from mcp.server.mcpserver import MCPServer

from mcp_servers.notes.store import Note, NoteStore

TZ = ZoneInfo("Europe/Berlin")
DEFAULT_FILE = Path.home() / ".jarvis" / "notizen.sqlite3"
MAX_TEXT_LENGTH = 1000
MAX_LISTED = 50
FIRST_YEAR, LAST_YEAR = 1970, 2200

mcp = MCPServer("notes", log_level="WARNING")
_store: NoteStore | None = None


def store() -> NoteStore:
    """Wird in Tests ersetzt; im Betrieb von main() gesetzt."""
    global _store
    if _store is None:
        _store = NoteStore(DEFAULT_FILE)
    return _store


def _fail(message: str, reason: str = "invalid_input") -> dict[str, Any]:
    return {"ok": False, "fehler": message, "grund": reason}


def _parse_text(text: str) -> str:
    cleaned = text.strip()
    if not cleaned:
        raise ValueError("Der Eintrag braucht einen Text.")
    if len(cleaned) > MAX_TEXT_LENGTH:
        raise ValueError(f"Der Text darf höchstens {MAX_TEXT_LENGTH} Zeichen lang sein.")
    return cleaned


def _parse_due(faellig: str | None) -> date | None:
    if faellig is None or not faellig.strip():
        return None
    try:
        due = date.fromisoformat(faellig.strip()[:10])
    except ValueError:
        raise ValueError(f"Ungültiges Datum '{faellig}'. Erwartet JJJJ-MM-TT.") from None
    if not FIRST_YEAR <= due.year <= LAST_YEAR:
        raise ValueError(f"Das Datum {due:%Y-%m-%d} ist nicht plausibel.")
    return due


def _note_dict(note: Note, today: date) -> dict[str, Any]:
    entry: dict[str, Any] = {"id": note.id, "text": note.text}
    if note.due is not None:
        entry["faellig"] = note.due.isoformat()
        if not note.done and note.due < today:
            entry["ueberfaellig"] = True
    if note.done_at is not None:
        entry["erledigt_am"] = note.done_at.astimezone(TZ).date().isoformat()
    return entry


def _describe(text: str, due: date | None) -> str:
    shown = text if len(text) <= 300 else f"{text[:300]}… (gekürzt, {len(text)} Zeichen)"
    return f"Merken: {shown}" + (f" (fällig {due:%d.%m.%Y})" if due else "")


def preview_add_data(text: str, faellig: str | None) -> dict[str, Any]:
    try:
        cleaned, due = _parse_text(text), _parse_due(faellig)
    except ValueError as exc:
        return _fail(str(exc))
    return {"ok": True, "vorschau": _describe(cleaned, due), "frage": "Speichern?"}


def add_data(text: str, faellig: str | None) -> dict[str, Any]:
    try:
        cleaned, due = _parse_text(text), _parse_due(faellig)
    except ValueError as exc:
        return _fail(str(exc))
    note = store().add(cleaned, due)
    return {"ok": True, "gespeichert": _describe(note.text, note.due), "id": note.id}


def list_data(
    filter: str | None = None, erledigte_einbeziehen: bool = False, now: datetime | None = None
) -> dict[str, Any]:
    today = (now or datetime.now(TZ)).astimezone(TZ).date()
    notes = store().list(include_done=erledigte_einbeziehen)
    if filter and filter.strip():
        needle = filter.strip().casefold()
        notes = [n for n in notes if needle in n.text.casefold()]
    result: dict[str, Any] = {
        "ok": True,
        "anzahl": len(notes),
        "eintraege": [_note_dict(n, today) for n in notes[:MAX_LISTED]],
    }
    if len(notes) > MAX_LISTED:
        result["hinweis"] = f"Nur die ersten {MAX_LISTED} von {len(notes)} Einträgen gezeigt."
    return result


def _find(note_id: int) -> Note | dict[str, Any]:
    note = store().get(note_id)
    if note is None:
        return _fail(f"Es gibt keinen Eintrag mit der Nummer {note_id}.", "not_found")
    if note.done:
        return _fail(f"Eintrag {note_id} ist schon erledigt.", "already_done")
    return note


def preview_complete_data(id: int) -> dict[str, Any]:
    found = _find(id)
    if isinstance(found, dict):
        return found
    return {"ok": True, "vorschau": f"Als erledigt markieren: {found.text}", "frage": "Abhaken?"}


def complete_data(id: int) -> dict[str, Any]:
    found = _find(id)
    if isinstance(found, dict):
        return found
    store().complete(id)
    return {"ok": True, "erledigt": found.text, "id": id}


@mcp.tool()
def add_note(text: str, faellig: str | None = None) -> dict[str, Any]:
    """Merkt sich eine Notiz oder Aufgabe des Nutzers (lokal gespeichert). faellig optional als
    Datum 'JJJJ-MM-TT' (relative Angaben vorher umrechnen). Direkt aufrufen: die Bestätigung holt
    das System selbst beim Nutzer ein."""
    return add_data(text, faellig)


@mcp.tool()
def list_notes(filter: str | None = None, erledigte_einbeziehen: bool = False) -> dict[str, Any]:
    """Liest die gemerkten Notizen und Aufgaben (offene zuerst nach Fälligkeit). filter engt per
    Text ein; erledigte nur mit erledigte_einbeziehen=true. Jeder Eintrag hat eine id."""
    return list_data(filter, erledigte_einbeziehen)


@mcp.tool()
def complete_note(id: int) -> dict[str, Any]:
    """Hakt einen Eintrag als erledigt ab (id aus list_notes). Direkt aufrufen: die Bestätigung
    holt das System selbst beim Nutzer ein."""
    return complete_data(id)


@mcp.tool()
def preview_add_note(text: str, faellig: str | None = None) -> dict[str, Any]:
    """Interne Vorschau für add_note (schreibt nichts)."""
    return preview_add_data(text, faellig)


@mcp.tool()
def preview_complete_note(id: int) -> dict[str, Any]:
    """Interne Vorschau für complete_note (schreibt nichts)."""
    return preview_complete_data(id)


def main() -> None:
    global _store
    parser = argparse.ArgumentParser(prog="mcp_servers.notes")
    parser.add_argument("--datei", type=Path, default=DEFAULT_FILE, help="SQLite-Datei")
    _store = NoteStore(parser.parse_args().datei.expanduser())
    mcp.run(transport="stdio")
