"""iCalendar (RFC 5545): nur die benötigten Felder lesen; VEVENT für neue Termine erzeugen."""

import re
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from mcp_servers.calendar.timeutil import TZ


@dataclass(frozen=True)
class Event:
    summary: str
    start: datetime | date
    end: datetime | date | None
    location: str | None = None
    calendar: str | None = None  # Anzeigename des Kalenders, aus dem der Termin stammt
    # True, wenn der Server eine Serie nicht in Einzeltermine aufgelöst hat (RRULE im Termin).
    unexpanded: bool = False
    # Kennung fürs Ändern/Löschen ("<Kalenderordner>/<Datei>.ics") und ob der Termin zu einer
    # Serie gehört (RRULE oder RECURRENCE-ID): Serien werden nicht geändert (ADR-014).
    event_id: str | None = None
    recurring: bool = False

    @property
    def all_day(self) -> bool:
        return not isinstance(self.start, datetime)


def _unfold(ics: str) -> list[str]:
    lines: list[str] = []
    for line in ics.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if line[:1] in (" ", "\t") and lines:
            lines[-1] += line[1:]
        else:
            lines.append(line)
    return lines


_BACKSLASH = "\\"


def _unescape(text: str) -> str:
    out: list[str] = []
    chars = iter(text)
    for char in chars:
        if char != _BACKSLASH:
            out.append(char)
            continue
        nxt = next(chars, "")
        out.append("\n" if nxt in ("n", "N") else nxt)
    return "".join(out)


def _escape(text: str) -> str:
    text = text.replace(_BACKSLASH, _BACKSLASH * 2)
    for char in (";", ","):
        text = text.replace(char, _BACKSLASH + char)
    return text.replace("\r\n", "\n").replace("\n", _BACKSLASH + "n")


def _to_value(value: str, params: dict[str, str]) -> datetime | date | None:
    """ICS-Wert -> date (ganztägig) oder datetime in Europe/Berlin. Unlesbares -> None."""
    try:
        if len(value) == 8 and value.isdigit():
            return date(int(value[:4]), int(value[4:6]), int(value[6:8]))
        if len(value) in (15, 16) and value[8] == "T":
            naive = datetime(
                int(value[:4]), int(value[4:6]), int(value[6:8]),
                int(value[9:11]), int(value[11:13]), int(value[13:15]),
            )  # fmt: skip
            if value.endswith("Z"):
                return naive.replace(tzinfo=UTC).astimezone(TZ)
            return naive.replace(tzinfo=_zone(params.get("TZID"))).astimezone(TZ)
    except (ValueError, KeyError, OSError, OverflowError):  # Overflow: z. B. Jahr 9999 + Zeitzone
        return None
    return None


def _zone(tzid: str | None) -> ZoneInfo:
    """Zeitzone eines TZID. Unbekannte Namen (z. B. Outlooks 'W. Europe Standard Time') gelten als
    Europe/Berlin, statt den ganzen Termin zu verwerfen."""
    if not tzid:
        return TZ
    try:
        return ZoneInfo(tzid)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        return TZ


_DURATION = re.compile(
    r"^(?P<sign>[+-])?P(?:(?P<weeks>\d+)W)?(?:(?P<days>\d+)D)?"
    r"(?:T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+)S)?)?$"
)


def _duration(value: str) -> timedelta | None:
    """ICS-DURATION (RFC 5545 §3.3.6), z. B. PT1H30M oder P1D; Unlesbares -> None."""
    match = _DURATION.match(value.strip().upper())
    if match is None or value.strip().upper() in ("P", "PT", "+P", "-P"):
        return None
    parts = {k: int(v) for k, v in match.groupdict().items() if v and k != "sign"}
    try:
        delta = timedelta(**parts)
    except OverflowError:
        return None
    return -delta if match.group("sign") == "-" else delta


def _split_key(raw_key: str) -> tuple[str, dict[str, str]]:
    name, *param_parts = raw_key.split(";")
    params: dict[str, str] = {}
    for part in param_parts:
        key, sep, val = part.partition("=")
        if sep:
            params[key.upper()] = val.strip('"')
    return name.upper(), params


def parse_events(ics_blocks: list[str]) -> list[Event]:
    events: list[Event] = []
    for ics in ics_blocks:
        current: dict[str, object] | None = None
        nested = 0  # Tiefe innerhalb von VALARM & Co.: deren SUMMARY/DTSTART gehören nicht dazu
        for line in _unfold(ics):
            if line == "BEGIN:VEVENT":
                current, nested = {}, 0
                continue
            if current is not None and line.startswith("BEGIN:"):
                nested += 1
                continue
            if current is not None and nested and line.startswith("END:"):
                nested -= 1
                continue
            if nested:
                continue
            if line == "END:VEVENT":
                cancelled = (
                    str(current.get("status", "")).upper() == "CANCELLED" if current else False
                )
                start = current.get("start") if current else None
                if current and not cancelled and isinstance(start, datetime | date):
                    summary = str(current.get("summary") or "").strip() or "Ohne Titel"
                    raw_end = current.get("end")
                    end = raw_end if isinstance(raw_end, datetime | date) else None
                    duration = current.get("duration")
                    if end is None and isinstance(duration, timedelta) and duration >= timedelta():
                        with suppress(OverflowError):  # Dauer über das Jahr 9999 hinaus
                            end = start + duration
                    location = current.get("location")
                    events.append(
                        Event(
                            summary,
                            start,
                            end,
                            str(location) if location else None,
                            unexpanded="rrule" in current,
                            recurring="rrule" in current or "recurrence_id" in current,
                        )
                    )
                current = None
                continue
            if current is None or ":" not in line:
                continue
            raw_key, _, value = line.partition(":")
            name, params = _split_key(raw_key)
            if name == "SUMMARY":
                current["summary"] = _unescape(value)
            elif name == "LOCATION":
                current["location"] = _unescape(value).strip()
            elif name == "STATUS":
                current["status"] = value.strip()
            elif name == "DTSTART":
                current["start"] = _to_value(value, params)
            elif name == "DTEND":
                current["end"] = _to_value(value, params)
            elif name == "RRULE":
                current["rrule"] = value
            elif name == "RECURRENCE-ID":
                current["recurrence_id"] = value
            elif name == "DURATION":
                current["duration"] = _duration(value)
    return events


def _utc(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def _fold(line: str) -> list[str]:
    """Zeilen auf höchstens 75 Oktette falten (RFC 5545 §3.1)."""
    raw = line.encode("utf-8")
    if len(raw) <= 75:
        return [line]
    parts: list[str] = []
    current = ""
    limit = 75
    for char in line:
        if len((current + char).encode("utf-8")) > limit:
            parts.append(current)
            current, limit = char, 74
        else:
            current += char
    parts.append(current)
    return [parts[0], *(" " + p for p in parts[1:])]


def build_vevent(
    uid: str,
    title: str,
    start: datetime | date,
    end: datetime | date,
    location: str | None,
    note: str | None,
    now: datetime,
) -> str:
    """Vollständige .ics-Datei mit einem VEVENT. Zeiten in UTC, damit kein VTIMEZONE nötig ist."""
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//JARVIS//Calendar MCP//DE",
        "BEGIN:VEVENT",
        f"UID:{uid}",
        f"DTSTAMP:{_utc(now)}",
    ]
    if isinstance(start, datetime) and isinstance(end, datetime):
        lines += [f"DTSTART:{_utc(start)}", f"DTEND:{_utc(end)}"]
    else:
        lines += [
            f"DTSTART;VALUE=DATE:{start:%Y%m%d}",
            f"DTEND;VALUE=DATE:{end:%Y%m%d}",
        ]
    lines.append(f"SUMMARY:{_escape(title)}")
    if location:
        lines.append(f"LOCATION:{_escape(location)}")
    if note:
        lines.append(f"DESCRIPTION:{_escape(note)}")
    lines += ["END:VEVENT", "END:VCALENDAR"]
    folded = [part for line in lines for part in _fold(line)]
    return "\r\n".join(folded) + "\r\n"


def default_end(start: datetime | date) -> datetime | date:
    return start + timedelta(hours=1) if isinstance(start, datetime) else start + timedelta(days=1)


# Felder, die modify_vevent ersetzt; alle anderen Zeilen (UID, VALARM, Teilnehmer ...) bleiben.
_REPLACED = {"SUMMARY", "DTSTART", "DTEND", "DURATION", "LOCATION", "DESCRIPTION", "DTSTAMP",
             "LAST-MODIFIED", "SEQUENCE"}  # fmt: skip
_TIME_FIELDS = {"DTSTART", "DTEND", "DURATION"}


def _date_lines(start: datetime | date, end: datetime | date) -> list[str]:
    if isinstance(start, datetime) and isinstance(end, datetime):
        return [f"DTSTART:{_utc(start)}", f"DTEND:{_utc(end)}"]
    return [f"DTSTART;VALUE=DATE:{start:%Y%m%d}", f"DTEND;VALUE=DATE:{end:%Y%m%d}"]


def modify_vevent(
    ics: str,
    title: str,
    times: tuple[datetime | date, datetime | date] | None,
    location: str | None,
    note: str | None,
    now: datetime,
) -> str:
    """Ändert genau einen (nicht wiederkehrenden) VEVENT; alles Übrige bleibt erhalten.

    Ersetzt den Titel, setzt DTSTAMP/LAST-MODIFIED neu und zählt SEQUENCE hoch. `times` (Start,
    Ende): None = Zeiten samt Zeitzone unangetastet lassen. Ort und Notiz: None = unverändert
    lassen, "" = entfernen, sonst ersetzen. Serien (RRULE, RECURRENCE-ID) oder mehrere VEVENTs
    lehnt sie ab.
    """
    replaced = set(_REPLACED)
    if times is None:
        replaced -= _TIME_FIELDS
    if location is None:
        replaced.discard("LOCATION")
    if note is None:
        replaced.discard("DESCRIPTION")
    lines = _unfold(ics)
    starts = [i for i, line in enumerate(lines) if line == "BEGIN:VEVENT"]
    if len(starts) != 1:
        raise ValueError("Die Termindatei enthält mehrere Termine (Serie); nicht änderbar.")
    begin = starts[0]
    finish = lines.index("END:VEVENT", begin)
    sequence = 0
    kept: list[str] = []
    nested = 0
    for line in lines[begin + 1 : finish]:
        if line.startswith("BEGIN:"):
            nested += 1
        elif line.startswith("END:") and nested:
            nested -= 1
        name = _split_key(line.partition(":")[0])[0] if ":" in line else ""
        if not nested and name in ("RRULE", "RECURRENCE-ID", "RDATE", "EXDATE"):
            raise ValueError("Serientermine können nicht geändert werden.")
        if not nested and name == "SEQUENCE":
            with suppress(ValueError):
                sequence = int(line.partition(":")[2].strip())
        if nested or name not in replaced:
            kept.append(line)
    stamp = _utc(now)
    fresh = [
        f"DTSTAMP:{stamp}",
        f"LAST-MODIFIED:{stamp}",
        f"SEQUENCE:{sequence + 1}",
        *(_date_lines(*times) if times else []),
        f"SUMMARY:{_escape(title)}",
        *([f"LOCATION:{_escape(location)}"] if location else []),
        *([f"DESCRIPTION:{_escape(note)}"] if note else []),
    ]
    # Neue Felder vor eingebetteten Komponenten (VALARM), damit die Datei übersichtlich bleibt.
    split = next((i for i, line in enumerate(kept) if line.startswith("BEGIN:")), len(kept))
    body = [*lines[: begin + 1], *kept[:split], *fresh, *kept[split:], *lines[finish:]]
    folded = [part for line in body if line for part in _fold(line)]
    return "\r\n".join(folded) + "\r\n"
