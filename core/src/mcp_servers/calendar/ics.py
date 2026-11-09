"""iCalendar (RFC 5545): nur die benötigten Felder lesen; VEVENT für neue Termine erzeugen."""

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from mcp_servers.calendar.timeutil import TZ


@dataclass(frozen=True)
class Event:
    summary: str
    start: datetime | date
    end: datetime | date | None
    location: str | None = None

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
            tzid = params.get("TZID")
            zone = ZoneInfo(tzid) if tzid else TZ
            return naive.replace(tzinfo=zone).astimezone(TZ)
    except (ValueError, KeyError, OSError):
        return None
    return None


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
        for line in _unfold(ics):
            if line == "BEGIN:VEVENT":
                current = {}
                continue
            if line == "END:VEVENT":
                if current and isinstance(current.get("start"), datetime | date):
                    summary = str(current.get("summary") or "").strip() or "Ohne Titel"
                    start = current["start"]
                    end = current.get("end")
                    location = current.get("location")
                    events.append(
                        Event(
                            summary,
                            start,  # pyright: ignore[reportArgumentType]
                            end if isinstance(end, datetime | date) else None,
                            str(location) if location else None,
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
            elif name == "DTSTART":
                current["start"] = _to_value(value, params)
            elif name == "DTEND":
                current["end"] = _to_value(value, params)
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
