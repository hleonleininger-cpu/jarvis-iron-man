"""MCP-Server `calendar`: Termine lesen, anlegen, ändern und löschen (ADR-005, ADR-014).

Läuft als eigener Prozess (stdio) und importiert nichts aus jarvis_core. Zugangsdaten liest er
selbst aus dem OS-Schlüsselbund (Dienst "jarvis", Eintrag "apple_caldav", JSON).
Die Schreibwerkzeuge schreiben sofort; die Bestätigung durch den Nutzer erzwingt der Core
(Policy) und zeigt dabei die Vorschau aus den preview_*-Werkzeugen.
"""

import argparse
import json
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

import keyring
from keyring.errors import KeyringError
from mcp.server.mcpserver import MCPServer

from mcp_servers.calendar.caldav import CalDavClient, CalDavError, Credentials
from mcp_servers.calendar.ics import Event, default_end, modify_vevent
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
MAX_NOTE_PREVIEW = 300  # was der Nutzer vor dem Bestätigen sieht; längeres wird markiert
PAST_WARNING = "ACHTUNG: liegt in der Vergangenheit"

mcp = MCPServer("calendar", log_level="WARNING")
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
        **({"id": event.event_id} if event.event_id else {}),
        **({"serie": True} if event.recurring else {}),
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


FIRST_YEAR, LAST_YEAR = 1970, 2200


def _plausible(value: datetime | date) -> datetime | date:
    """Schützt vor Überläufen (Jahr 9999) und offensichtlich geratenen Jahren."""
    if not FIRST_YEAR <= value.year <= LAST_YEAR:
        raise ValueError(
            f"Das Datum {value:%Y-%m-%d} ist nicht plausibel (erlaubt {FIRST_YEAR}–{LAST_YEAR})."
        )
    return value


def _parse_new_event(
    titel: str, start: str, ende: str | None
) -> tuple[str, datetime | date, datetime | date]:
    title = titel.strip()
    if not title:
        raise ValueError("Der Termin braucht einen Titel.")
    if len(title) > MAX_TITLE_LENGTH:
        raise ValueError(f"Der Titel darf höchstens {MAX_TITLE_LENGTH} Zeichen lang sein.")
    begin = _plausible(parse_when(start))
    finish = _parse_end(ende) if ende and ende.strip() else default_end(begin)
    return title, begin, _checked_end(begin, finish)


def _parse_end(ende: str) -> datetime | date:
    """Ende eines Termins. Ganztägig nennt das Modell den LETZTEN Tag (so liest es ihn auch aus
    list_events); in ICS ist DTEND exklusiv, also einen Tag später."""
    value = _plausible(parse_when(ende))
    return value if isinstance(value, datetime) else value + timedelta(days=1)


def _checked_end(begin: datetime | date, finish: datetime | date) -> datetime | date:
    if isinstance(begin, datetime) and isinstance(finish, datetime):
        if finish <= begin:
            raise ValueError("Das Ende muss nach dem Start liegen.")
    elif isinstance(begin, datetime) or isinstance(finish, datetime):
        raise ValueError("Start und Ende müssen beide ganztägig oder beide mit Uhrzeit sein.")
    elif finish <= begin:
        raise ValueError("Der letzte Tag darf nicht vor dem ersten liegen.")
    return _plausible(finish)


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
        text += f", Notiz: {_note_preview(notiz)}"
    return text


def _note_preview(notiz: str) -> str:
    note = notiz.strip()
    if len(note) <= MAX_NOTE_PREVIEW:
        return note
    return f"{note[:MAX_NOTE_PREVIEW]}… (gekürzt, {len(note)} Zeichen)"


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
        target = make_client().create_event(
            f"{uuid.uuid4()}@jarvis", title, begin, finish, _clean(ort, MAX_TITLE_LENGTH),
            _clean(notiz, MAX_NOTE_LENGTH),
        )  # fmt: skip
    except (CalDavError, ValueError) as exc:
        return _fail(exc)
    created = describe_event(title, begin, finish, ort, notiz)
    return {"ok": True, "angelegt": f"{created}, Kalender: {target}"}


def _summary(title: str, begin: datetime | date, finish: datetime | date, ort: str | None) -> str:
    return describe_event(title, begin, finish, ort, None).removeprefix("Neuer Termin: ")


def _existing(event_id: str) -> tuple[CalDavClient, Event, str, str | None]:
    client = make_client()
    event, ics, etag = client.get_event(event_id)
    if event.recurring:
        raise CalDavError("recurring")
    return client, event, ics, etag


def _event_end(event: Event) -> datetime | date:
    return event.end if event.end is not None else default_end(event.start)


REMOVE = "-"  # ort/notiz: dieser Wert entfernt die Angabe; leer oder ungenannt = unverändert


@dataclass(frozen=True)
class _Change:
    title: str
    begin: datetime | date
    finish: datetime | date
    retimed: bool  # False: Zeiten (samt Zeitzone) bleiben in der Termindatei unangetastet
    location: str | None  # None = unverändert, "" = entfernen, sonst neuer Text
    note: str | None


def _field_change(value: str | None, limit: int) -> str | None:
    """Kleine Modelle füllen ungenutzte Parameter gern mit "": das heißt 'unverändert', nicht
    'löschen'. Entfernt wird nur mit dem ausdrücklichen Wert '-'."""
    text = (value or "").strip()
    if not text:
        return None
    return "" if text == REMOVE else text[:limit]


def _plan_change(
    event: Event,
    titel: str | None,
    start: str | None,
    ende: str | None,
    ort: str | None,
    notiz: str | None,
) -> _Change:
    """Was sich ändert; Ungenanntes bleibt. Ein neuer Start ohne Ende behält die Dauer."""
    title = (titel or "").strip()
    new_start, new_end = (start or "").strip(), (ende or "").strip()
    location = _field_change(ort, MAX_TITLE_LENGTH)
    note = _field_change(notiz, MAX_NOTE_LENGTH)
    if not (title or new_start or new_end) and location is None and note is None:
        raise ValueError("Es wurde nichts zum Ändern angegeben.")
    if len(title) > MAX_TITLE_LENGTH:
        raise ValueError(f"Der Titel darf höchstens {MAX_TITLE_LENGTH} Zeichen lang sein.")
    title = title or event.summary
    old_end = _event_end(event)
    if not (new_start or new_end):  # Zeiten bleiben, wie sie sind (auch ein Termin mit Dauer 0)
        return _Change(title, event.start, old_end, False, location, note)
    begin = _plausible(parse_when(new_start)) if new_start else event.start
    finish = _parse_end(new_end) if new_end else _keep_duration(begin, event.start, old_end)
    return _Change(title, begin, _checked_end(begin, finish), True, location, note)


def _keep_duration(
    begin: datetime | date, old_start: datetime | date, old_end: datetime | date
) -> datetime | date:
    """Neues Ende bei verschobenem Start: gleiche Dauer wie bisher. Wechselt der Termin zwischen
    ganztägig und Uhrzeit, gilt die Standarddauer (1 Stunde bzw. 1 Tag)."""
    if (
        isinstance(begin, datetime)
        and isinstance(old_start, datetime)
        and isinstance(old_end, datetime)
    ):
        return begin + (old_end - old_start)
    if not (
        isinstance(begin, datetime)
        or isinstance(old_start, datetime)
        or isinstance(old_end, datetime)
    ):
        return begin + (old_end - old_start)
    return default_end(begin)


def _change_text(event: Event, change: _Change) -> str:
    before = _summary(event.summary, event.start, _event_end(event), event.location)
    location = event.location if change.location is None else change.location
    after = _summary(change.title, change.begin, change.finish, location)
    text = f"Termin ändern: {before} → {after}, Kalender: {event.calendar or 'ohne Namen'}"
    if change.location == "" and event.location:
        text += ", Ort entfernen"
    # Der Nutzer bestätigt, was geschrieben wird: den neuen Notiztext zeigen, nicht nur "neu".
    if change.note:
        text += f", neue Notiz: {_note_preview(change.note)}"
    elif change.note == "":
        text += ", Notiz entfernen"
    return text


def preview_update_data(
    id: str,
    titel: str | None = None,
    start: str | None = None,
    ende: str | None = None,
    ort: str | None = None,
    notiz: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    try:
        _client, event, _ics, _etag = _existing(id)
        change = _plan_change(event, titel, start, ende, ort, notiz)
    except (CalDavError, ValueError) as exc:
        return _fail(exc)
    text = _change_text(event, change)
    if _is_past(change.finish, now or datetime.now(TZ)):
        text += f" ({PAST_WARNING})"
    return {"ok": True, "vorschau": text, "frage": "Ändern?"}


def update_data(
    id: str,
    titel: str | None = None,
    start: str | None = None,
    ende: str | None = None,
    ort: str | None = None,
    notiz: str | None = None,
) -> dict[str, Any]:
    try:
        client, event, ics, etag = _existing(id)
        change = _plan_change(event, titel, start, ende, ort, notiz)
        times = (change.begin, change.finish) if change.retimed else None
        updated = modify_vevent(
            ics, change.title, times, change.location, change.note, datetime.now(TZ)
        )
        client.replace_event(id, updated, etag)
    except (CalDavError, ValueError) as exc:
        return _fail(exc)
    return {"ok": True, "geaendert": _change_text(event, change).removeprefix("Termin ändern: ")}


def _delete_text(event: Event) -> str:
    summary = _summary(event.summary, event.start, _event_end(event), event.location)
    return f"Termin löschen: {summary}, Kalender: {event.calendar or 'ohne Namen'}"


def preview_delete_data(id: str) -> dict[str, Any]:
    try:
        _client, event, _ics, _etag = _existing(id)
    except CalDavError as exc:
        return _fail(exc)
    return {"ok": True, "vorschau": _delete_text(event), "frage": "Endgültig löschen?"}


def delete_data(id: str) -> dict[str, Any]:
    try:
        client, event, _ics, etag = _existing(id)
        client.delete_event(id, etag)
    except CalDavError as exc:
        return _fail(exc)
    return {"ok": True, "geloescht": _delete_text(event).removeprefix("Termin löschen: ")}


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
    konkretes Datum 'JJJJ-MM-TTTHH:MM' (Europe/Berlin) oder 'JJJJ-MM-TT' für ganztägig (ende ist
    dann der letzte Tag); ohne ende dauert der Termin 1 Stunde bzw. 1 Tag. Relative Angaben wie
    'Montag' vorher in ein Datum umrechnen."""
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


@mcp.tool()
def update_event(
    id: str,
    titel: str | None = None,
    start: str | None = None,
    ende: str | None = None,
    ort: str | None = None,
    notiz: str | None = None,
) -> dict[str, Any]:
    """Ändert einen bestehenden Termin. id unverändert aus list_events übernehmen (vorher dort
    nachsehen). Nur Angaben nennen, die sich ändern; ein neuer start ohne ende behält die Dauer.
    Zeiten wie bei create_event. ort oder notiz '-' entfernt sie. Serientermine sind nicht
    änderbar. Direkt aufrufen: die Bestätigung holt das System selbst beim Nutzer ein."""
    return update_data(id, titel, start, ende, ort, notiz)


@mcp.tool()
def delete_event(id: str) -> dict[str, Any]:
    """Löscht einen Termin endgültig. id unverändert aus list_events übernehmen (vorher dort
    nachsehen). Serientermine sind nicht löschbar. Direkt aufrufen: die Bestätigung holt das
    System selbst beim Nutzer ein."""
    return delete_data(id)


@mcp.tool()
def preview_update_event(
    id: str,
    titel: str | None = None,
    start: str | None = None,
    ende: str | None = None,
    ort: str | None = None,
    notiz: str | None = None,
) -> dict[str, Any]:
    """Interne Vorschau für update_event (schreibt nichts)."""
    return preview_update_data(id, titel, start, ende, ort, notiz)


@mcp.tool()
def preview_delete_event(id: str) -> dict[str, Any]:
    """Interne Vorschau für delete_event (schreibt nichts)."""
    return preview_delete_data(id)


def main() -> None:
    global _calendar_name
    parser = argparse.ArgumentParser(prog="mcp_servers.calendar")
    parser.add_argument(
        "--kalender",
        help="Nur diesen Kalender nutzen (Standard: alle lesen, neue Termine in den ersten)",
    )
    _calendar_name = parser.parse_args().kalender
    mcp.run(transport="stdio")
