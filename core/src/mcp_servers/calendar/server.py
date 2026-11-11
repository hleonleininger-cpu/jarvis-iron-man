"""MCP-Server `calendar`: Termine lesen (list_events) und anlegen (create_event).

Läuft als eigener Prozess (stdio) und importiert nichts aus jarvis_core. Zugangsdaten liest er
selbst aus dem OS-Schlüsselbund (Dienst "jarvis", Eintrag "apple_caldav", JSON).
create_event schreibt sofort; die Bestätigung durch den Nutzer erzwingt der Core (Policy).
"""

import argparse
import json
import uuid
from datetime import date, datetime, timedelta
from typing import Any

import keyring
from keyring.errors import KeyringError
from mcp.server.mcpserver import MCPServer

from mcp_servers.calendar.caldav import CalDavClient, CalDavError, Credentials
from mcp_servers.calendar.ics import Event, default_end
from mcp_servers.calendar.timeutil import (
    TZ,
    format_clock,
    format_day,
    parse_when,
    resolve_window,
)

KEYRING_SERVICE = "jarvis"
KEYRING_ENTRY = "apple_caldav"
MAX_EVENTS = 25
MAX_TITLE_LENGTH = 200
MAX_NOTE_LENGTH = 2000
PAST_WARNING = "ACHTUNG: liegt in der Vergangenheit"

mcp = MCPServer("calendar")
_calendar_name: str | None = None


def load_credentials() -> Credentials:
    try:
        raw = keyring.get_password(KEYRING_SERVICE, KEYRING_ENTRY)
    except KeyringError:
        raise CalDavError("keyring_unavailable") from None
    if not raw:
        raise CalDavError("not_configured")
    try:
        data: dict[str, Any] = json.loads(raw)
        return Credentials(str(data["username"]), str(data["password"]))
    except (ValueError, KeyError, TypeError):
        raise CalDavError("not_configured") from None


def make_client() -> CalDavClient:
    """Wird in Tests ersetzt (CalDAV-Attrappe statt echtem iCloud)."""
    return CalDavClient(load_credentials(), calendar_name=_calendar_name)


def _fail(error: CalDavError | ValueError) -> dict[str, Any]:
    if isinstance(error, CalDavError):
        return {"ok": False, "fehler": error.text, "grund": error.reason}
    return {"ok": False, "fehler": str(error), "grund": "invalid_input"}


def _iso(value: datetime | date) -> str:
    return f"{value:%Y-%m-%d}" if not isinstance(value, datetime) else f"{value:%Y-%m-%dT%H:%M}"


def _last_day(event: Event) -> date | None:
    """Letzter Tag eines ganztägigen Termins. In ICS ist DTEND exklusiv: ein Geburtstag am 11.
    endet dort am 12.; dem Modell so gesagt, würde daraus 'vom 11. bis 12.'."""
    if event.end is None or isinstance(event.end, datetime):
        return None
    return max(event.end - timedelta(days=1), _as_date(event.start))


def _as_date(value: datetime | date) -> date:
    return value.astimezone(TZ).date() if isinstance(value, datetime) else value


def _event_dict(event: Event) -> dict[str, Any]:
    last_day = _last_day(event)
    end = last_day if event.all_day and last_day is not None else event.end
    return {
        "titel": event.summary,
        "start": _iso(event.start),
        "ende": _iso(end) if end is not None else None,
        "ganztaegig": event.all_day,
        "ort": event.location,
        "kalender": event.calendar,
    }


def list_events_data(zeitraum: str, now: datetime | None = None) -> dict[str, Any]:
    try:
        time_min, time_max, label = resolve_window(zeitraum, now or datetime.now(TZ))
        client = make_client()
        events = client.list_events(time_min, time_max)
    except (CalDavError, ValueError) as exc:
        return _fail(exc)
    result: dict[str, Any] = {
        "ok": True,
        "zeitraum": label,
        "zeitzone": "Europe/Berlin",
        "anzahl": len(events),
        "termine": [_event_dict(e) for e in events[:MAX_EVENTS]],
    }
    hints: list[str] = []
    if len(events) > MAX_EVENTS:
        hints.append(f"Nur die ersten {MAX_EVENTS} von {len(events)} Terminen gezeigt.")
    series = sorted({e.summary for e in events if e.unexpanded})
    if series:
        hints.append(
            "Wiederkehrende Termine ohne aufgelöste Einzeltermine (Datum unsicher, ggf. im "
            f"Kalender prüfen): {', '.join(series)}."
        )
    if client.unreadable:
        hints.append(f"Nicht lesbare Kalender: {', '.join(client.unreadable)}.")
    if hints:
        result["hinweis"] = " ".join(hints)
    return result


def _parse_new_event(
    titel: str, start: str, ende: str | None
) -> tuple[str, datetime | date, datetime | date]:
    title = titel.strip()
    if not title:
        raise ValueError("Der Termin braucht einen Titel.")
    if len(title) > MAX_TITLE_LENGTH:
        raise ValueError(f"Der Titel darf höchstens {MAX_TITLE_LENGTH} Zeichen lang sein.")
    begin = parse_when(start)
    finish = parse_when(ende) if ende and ende.strip() else default_end(begin)
    if isinstance(begin, datetime) != isinstance(finish, datetime):
        raise ValueError("Start und Ende müssen beide ganztägig oder beide mit Uhrzeit sein.")
    if finish <= begin:
        raise ValueError("Das Ende muss nach dem Start liegen.")
    return title, begin, finish


def describe_event(
    title: str,
    begin: datetime | date,
    finish: datetime | date,
    ort: str | None,
    notiz: str | None,
) -> str:
    if isinstance(begin, datetime) and isinstance(finish, datetime):
        same_day = begin.astimezone(TZ).date() == finish.astimezone(TZ).date()
        end_text = (
            format_clock(finish) if same_day else f"{format_day(finish)} {format_clock(finish)}"
        )
        when = f"{format_day(begin)} {format_clock(begin)}-{end_text}"
    else:
        last = finish - timedelta(days=1)
        when = (
            f"{format_day(begin)} ganztägig"
            if last == begin
            else f"{format_day(begin)} bis {format_day(last)} ganztägig"
        )
    text = f"Neuer Termin: {title}, {when}"
    if ort and ort.strip():
        text += f", Ort: {ort.strip()}"
    if notiz and notiz.strip():
        text += f", Notiz: {notiz.strip()[:80]}"
    return text


def _clean(value: str | None, limit: int) -> str | None:
    """Leere Angaben werden zu None; überlange werden gekürzt statt den Termin abzulehnen."""
    text = (value or "").strip()
    return text[:limit] or None


def _is_past(finish: datetime | date, now: datetime) -> bool:
    if isinstance(finish, datetime):
        return finish <= now
    return finish <= now.astimezone(TZ).date()  # ganztägig: Ende ist exklusiv


def preview_event_data(
    titel: str,
    start: str,
    ende: str | None,
    ort: str | None,
    notiz: str | None,
    now: datetime | None = None,
) -> dict[str, Any]:
    try:
        title, begin, finish = _parse_new_event(titel, start, ende)
    except ValueError as exc:
        return _fail(exc)
    try:
        # Zielkalender nennen und fehlende Zugangsdaten melden, BEVOR der Nutzer gefragt wird.
        target = make_client().target_calendar_name()
    except CalDavError as exc:
        return _fail(exc)
    preview = f"{describe_event(title, begin, finish, ort, notiz)}, Kalender: {target}"
    if _is_past(finish, now or datetime.now(TZ)):
        preview += f" ({PAST_WARNING})"
    return {"ok": True, "vorschau": preview, "frage": "Anlegen?"}


def create_event_data(
    titel: str, start: str, ende: str | None, ort: str | None, notiz: str | None
) -> dict[str, Any]:
    try:
        title, begin, finish = _parse_new_event(titel, start, ende)
        make_client().create_event(
            f"{uuid.uuid4()}@jarvis", title, begin, finish, _clean(ort, MAX_TITLE_LENGTH),
            _clean(notiz, MAX_NOTE_LENGTH),
        )  # fmt: skip
    except (CalDavError, ValueError) as exc:
        return _fail(exc)
    return {"ok": True, "angelegt": describe_event(title, begin, finish, ort, notiz)}


@mcp.tool()
def list_events(zeitraum: str) -> dict[str, Any]:
    """Liest Termine aus allen Apple-Kalendern (iCloud) des Nutzers. zeitraum: 'heute',
    'morgen', 'übermorgen', 'woche' (Rest der Woche bis Sonntag), 'nächste woche', ein Datum
    'JJJJ-MM-TT' oder 'JJJJ-MM-TT/JJJJ-MM-TT'. Bei ganztägigen Terminen ist 'ende' der letzte
    Tag."""
    return list_events_data(zeitraum)


@mcp.tool()
def create_event(
    titel: str,
    start: str,
    ende: str | None = None,
    ort: str | None = None,
    notiz: str | None = None,
) -> dict[str, Any]:
    """Legt einen Termin im Apple-Kalender an. Direkt aufrufen: die Bestätigung holt das System
    selbst beim Nutzer ein. start/ende als
    konkretes Datum 'JJJJ-MM-TTTHH:MM' (Europe/Berlin) oder 'JJJJ-MM-TT' für ganztägig; ohne
    ende dauert der Termin 1 Stunde. Relative Angaben wie 'Montag' vorher in ein Datum umrechnen."""
    return create_event_data(titel, start, ende, ort, notiz)


@mcp.tool()
def preview_create_event(
    titel: str,
    start: str,
    ende: str | None = None,
    ort: str | None = None,
    notiz: str | None = None,
) -> dict[str, Any]:
    """Interne Vorschau für create_event (schreibt nichts)."""
    return preview_event_data(titel, start, ende, ort, notiz)


def main() -> None:
    global _calendar_name
    parser = argparse.ArgumentParser(prog="mcp_servers.calendar")
    parser.add_argument(
        "--kalender",
        help="Nur diesen Kalender nutzen (Standard: alle lesen, neue Termine in den ersten)",
    )
    _calendar_name = parser.parse_args().kalender
    mcp.run(transport="stdio")
