"""Zeitfenster und Termin-Zeiten (Europe/Berlin, sommerzeitsicher über zoneinfo)."""

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Berlin")
WEEKDAYS = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]
MAX_RANGE_DAYS = 92  # ein Quartal; größere Abfragen sind für iCloud teuer und fürs Modell nutzlos


def start_of_day(day: date) -> datetime:
    return datetime.combine(day, time(), tzinfo=TZ)


def resolve_window(zeitraum: str, now: datetime) -> tuple[datetime, datetime, str]:
    """Löst heute/morgen/übermorgen/woche/nächste woche, ein Datum oder 'von/bis' (inklusive)
    in [timeMin, timeMax) auf.

    'woche' = ab heute 00:00 bis zum Ende der laufenden Kalenderwoche (Sonntag 24 Uhr);
    'nächste woche' = Montag bis Sonntag der folgenden Kalenderwoche.
    """
    today = now.astimezone(TZ).date()
    key = zeitraum.strip().lower()
    if key in ("heute", "today"):
        return start_of_day(today), start_of_day(today + timedelta(days=1)), "heute"
    if key in ("morgen", "tomorrow"):
        d = today + timedelta(days=1)
        return start_of_day(d), start_of_day(d + timedelta(days=1)), "morgen"
    if key in ("übermorgen", "uebermorgen"):
        d = today + timedelta(days=2)
        return start_of_day(d), start_of_day(d + timedelta(days=1)), "übermorgen"
    monday = today + timedelta(days=7 - today.weekday())
    if key in ("woche", "week", "diese woche"):
        return start_of_day(today), start_of_day(monday), "woche"
    if key in ("nächste woche", "naechste woche", "next week"):
        return start_of_day(monday), start_of_day(monday + timedelta(days=7)), "nächste woche"
    try:
        if "/" in key:
            first, last = (date.fromisoformat(part.strip()) for part in key.split("/", 1))
        else:
            first = last = date.fromisoformat(key)
    except ValueError:
        raise ValueError(
            "Unbekannter Zeitraum. Erlaubt: heute, morgen, übermorgen, woche, nächste woche, "
            "JJJJ-MM-TT oder JJJJ-MM-TT/JJJJ-MM-TT."
        ) from None
    if last < first:
        raise ValueError("Das Enddatum liegt vor dem Startdatum.")
    if (last - first).days >= MAX_RANGE_DAYS:
        raise ValueError(f"Der Zeitraum darf höchstens {MAX_RANGE_DAYS} Tage umfassen.")
    return start_of_day(first), start_of_day(last + timedelta(days=1)), key


def parse_when(value: str) -> datetime | date:
    """ISO-Datum (ganztägig) oder ISO-Zeit; ohne Zone gilt Europe/Berlin."""
    text = value.strip()
    try:
        if len(text) == 10:
            return date.fromisoformat(text)
        parsed = datetime.fromisoformat(text)
    except ValueError:
        raise ValueError(
            f"Ungültige Zeitangabe '{value}'. Erwartet JJJJ-MM-TT oder JJJJ-MM-TTTHH:MM."
        ) from None
    return parsed.replace(tzinfo=TZ) if parsed.tzinfo is None else parsed.astimezone(TZ)


def format_day(value: datetime | date) -> str:
    """Wochentag und Datum MIT Jahr: ein vom Modell falsch geratenes Jahr muss auffallen."""
    day = value.astimezone(TZ).date() if isinstance(value, datetime) else value
    return f"{WEEKDAYS[day.weekday()]} {day:%d.%m.%Y}"


def format_clock(value: datetime) -> str:
    return f"{value.astimezone(TZ):%H:%M}"
