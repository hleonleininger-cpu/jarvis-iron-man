"""CalDAV-Client für iCloud (Port von calendarTool.js aus dem alten Projekt jarvis-os).

Ablauf (RFC 4791/6764): PRINCIPAL -> HOME_SET -> CALENDAR_LIST (PROPFIND), danach EVENTS
(REPORT calendar-query) bzw. PUT eines VEVENT. Diagnose-Meldungen wie im Original: nur
Schrittname, HTTP-Status und Grund, nie Zugangsdaten, Header oder vollständige Antworten.
"""

import base64
import dataclasses
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from urllib.parse import urljoin, urlsplit

from mcp_servers.calendar.ics import Event, build_vevent, parse_events
from mcp_servers.calendar.timeutil import TZ, start_of_day

CALDAV_ENTRY_URL = "https://caldav.icloud.com/"
TIMEOUT_SECONDS = 15.0
MAX_REDIRECTS = 5

ERROR_TEXTS: dict[str, str] = {
    "not_configured": (
        "Keine Kalender-Zugangsdaten gesetzt. Bitte 'jarvis secrets set apple_caldav' ausführen."
    ),
    "keyring_unavailable": (
        "Der OS-Schlüsselbund ist nicht verfügbar; die Kalender-Zugangsdaten sind nicht lesbar."
    ),
    "unauthorized": (
        "iCloud hat die Zugangsdaten abgelehnt (Apple-ID oder App-Passwort falsch). "
        "Bitte 'jarvis secrets set apple_caldav' erneut ausführen."
    ),
    "rate_limited": "iCloud begrenzt gerade die Anfragen. Bitte später erneut versuchen.",
    "unavailable": "iCloud ist gerade nicht erreichbar.",
    "invalid_response": "iCloud lieferte eine unerwartete Antwort.",
    "calendar_not_found": "Es wurde kein passender Kalender gefunden.",
    "write_failed": "iCloud hat den Termin nicht angenommen.",
}


class CalDavError(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason

    @property
    def text(self) -> str:
        return ERROR_TEXTS.get(self.reason, ERROR_TEXTS["unavailable"])


@dataclass(frozen=True)
class Credentials:
    username: str
    password: str


@dataclass(frozen=True)
class Response:
    status: int
    body: str
    url: str


# (method, url, headers, body) -> Response. In Tests ersetzt.
Transport = Callable[[str, str, dict[str, str], bytes | None], Response]


def same_site(origin: str, target: str) -> bool:
    """True, wenn `target` per https auf derselben Domain wie `origin` liegt (z. B. icloud.com).

    Nur dorthin gehen Zugangsdaten: iCloud verteilt auf pNN-caldav.icloud.com, aber eine
    Weiterleitung oder ein href auf eine fremde Domain bekäme sonst das App-Passwort.
    """
    source, dest = urlsplit(origin), urlsplit(target)
    if dest.scheme != "https" or not source.hostname or not dest.hostname:
        return False
    domain = ".".join(source.hostname.lower().split(".")[-2:])
    host = dest.hostname.lower()
    return host == domain or host.endswith("." + domain)


def urllib_transport(
    method: str, url: str, headers: dict[str, str], body: bytes | None
) -> Response:
    """HTTP mit benutzerdefinierter Methode; Weiterleitungen werden mit Methode/Body gefolgt."""
    current = url
    for _ in range(MAX_REDIRECTS + 1):
        request = urllib.request.Request(current, data=body, headers=headers, method=method)  # noqa: S310
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as resp:  # noqa: S310
                return Response(resp.status, resp.read().decode("utf-8", "replace"), current)
        except urllib.error.HTTPError as err:
            location = err.headers.get("Location")
            target = urljoin(current, location) if location else None
            if err.code in (301, 302, 307, 308) and target and same_site(url, target):
                current = target
                continue
            return Response(err.code, err.read().decode("utf-8", "replace"), current)
    return Response(310, "", current)


def log_error(step: str, status: int | None, reason: str) -> None:
    print(f"[caldav:{step}] status={status if status is not None else 'n/a'} reason={reason}",
          file=sys.stderr)  # fmt: skip


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _find_all(root: ET.Element, name: str) -> list[ET.Element]:
    return [el for el in root.iter() if _local(el.tag) == name]


def _first_href_in(root: ET.Element, container: str) -> str | None:
    # Gezielt der href INNERHALB des Containers, nicht der äußere href der Response.
    for block in _find_all(root, container):
        for href in _find_all(block, "href"):
            if href.text and href.text.strip():
                return href.text.strip()
    return None


def _parse_xml(text: str, step: str, status: int) -> ET.Element:
    try:
        return ET.fromstring(text)
    except ET.ParseError:
        log_error(step, status, "invalid_response:xml_parse_failed")
        raise CalDavError("invalid_response") from None


@dataclass(frozen=True)
class Calendar:
    url: str
    display_name: str | None


def select_calendar(calendars: list[Calendar], wanted: str | None) -> Calendar:
    if not calendars:
        log_error("SELECT_CALENDAR", 200, "calendar_not_found:no_calendars_in_home_set")
        raise CalDavError("calendar_not_found")
    if not wanted:
        return calendars[0]
    for cal in calendars:
        if (cal.display_name or "").strip().casefold() == wanted.strip().casefold():
            return cal
    log_error("SELECT_CALENDAR", 200, f"calendar_not_found:name_mismatch(count={len(calendars)})")
    raise CalDavError("calendar_not_found")


def _caldav_utc(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


class CalDavClient:
    def __init__(
        self,
        credentials: Credentials,
        transport: Transport = urllib_transport,
        calendar_name: str | None = None,
        entry_url: str = CALDAV_ENTRY_URL,
    ) -> None:
        token = base64.b64encode(f"{credentials.username}:{credentials.password}".encode()).decode()
        self._auth = f"Basic {token}"
        self._transport = transport
        self._calendar_name = calendar_name
        self._entry_url = entry_url
        # Kalender, die beim letzten list_events nicht lesbar waren (z. B. abonnierte).
        self.unreadable: list[str] = []

    def _request(
        self,
        step: str,
        method: str,
        url: str,
        *,
        body: str | None = None,
        depth: int | None = None,
        content_type: str = "application/xml; charset=utf-8",
        extra: dict[str, str] | None = None,
        ok_statuses: tuple[int, ...] = (200, 207),
    ) -> Response:
        headers = {"Authorization": self._auth, "Content-Type": content_type}
        if depth is not None:
            headers["Depth"] = str(depth)
        headers.update(extra or {})
        if not same_site(self._entry_url, url):
            log_error(step, None, "invalid_response:foreign_host")
            raise CalDavError("invalid_response")
        try:
            response = self._transport(method, url, headers, body.encode() if body else None)
        except (OSError, TimeoutError) as exc:
            log_error(step, None, f"network_error:{type(exc).__name__}")
            raise CalDavError("unavailable") from None
        if response.status in (401, 403):
            log_error(step, response.status, "unauthorized")
            raise CalDavError("unauthorized")
        if response.status == 429:
            log_error(step, response.status, "rate_limited")
            raise CalDavError("rate_limited")
        if response.status not in ok_statuses:
            reason = "write_failed" if method == "PUT" else "unavailable"
            log_error(step, response.status, reason)
            raise CalDavError(reason)
        return response

    def _principal(self) -> str:
        body = (
            '<?xml version="1.0" encoding="UTF-8"?><D:propfind xmlns:D="DAV:">'
            "<D:prop><D:current-user-principal/></D:prop></D:propfind>"
        )
        resp = self._request("PRINCIPAL", "PROPFIND", self._entry_url, body=body, depth=0)
        href = _first_href_in(
            _parse_xml(resp.body, "PRINCIPAL", resp.status), "current-user-principal"
        )
        if not href:
            log_error("PRINCIPAL", 200, "invalid_response:no_principal_href")
            raise CalDavError("invalid_response")
        return urljoin(resp.url, href)

    def _home_set(self, principal_url: str) -> str:
        body = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<D:propfind xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">'
            "<D:prop><C:calendar-home-set/></D:prop></D:propfind>"
        )
        resp = self._request("HOME_SET", "PROPFIND", principal_url, body=body, depth=0)
        href = _first_href_in(_parse_xml(resp.body, "HOME_SET", resp.status), "calendar-home-set")
        if not href:
            log_error("HOME_SET", 200, "invalid_response:no_home_set_href")
            raise CalDavError("invalid_response")
        return urljoin(resp.url, href)

    def _calendars(self, home_url: str) -> list[Calendar]:
        body = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<D:propfind xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav"><D:prop>'
            "<D:resourcetype/><D:displayname/><C:supported-calendar-component-set/>"
            "</D:prop></D:propfind>"
        )
        resp = self._request("CALENDAR_LIST", "PROPFIND", home_url, body=body, depth=1)
        root = _parse_xml(resp.body, "CALENDAR_LIST", resp.status)
        calendars: list[Calendar] = []
        for response in _find_all(root, "response"):
            if not _find_all(response, "calendar"):
                continue  # keine Kalender-Collection (z. B. der Home-Set-Eintrag selbst)
            components = [c.get("name", "").upper() for c in _find_all(response, "comp")]
            if components and "VEVENT" not in components:
                continue  # z. B. Erinnerungslisten (nur VTODO)
            href = _first_href_in(response, "response") or next(
                (h.text.strip() for h in _find_all(response, "href") if h.text), None
            )
            if not href:
                continue
            names = _find_all(response, "displayname")
            name = names[0].text.strip() if names and names[0].text else None
            calendars.append(Calendar(urljoin(resp.url, href), name))
        return calendars

    def _all_calendars(self) -> list[Calendar]:
        return self._calendars(self._home_set(self._principal()))

    def _calendar(self) -> Calendar:
        """Der Kalender für neue Termine: der benannte, sonst der erste."""
        return select_calendar(self._all_calendars(), self._calendar_name)

    def target_calendar_name(self) -> str:
        """Anzeigename des Kalenders, in den create_event schreiben würde (nur Lesen)."""
        return self._calendar().display_name or "ohne Namen"

    def list_events(self, time_min: datetime, time_max: datetime) -> list[Event]:
        """Alle Termine im Fenster, nach Start sortiert (Kürzen ist Sache des Aufrufers).

        Ohne benannten Kalender werden ALLE Terminkalender gelesen. Ist ein einzelner nicht
        lesbar, steht sein Name in `unreadable`; scheitern alle, gilt der erste Fehler.
        """
        calendars = self._all_calendars()
        if self._calendar_name or not calendars:
            calendars = [select_calendar(calendars, self._calendar_name)]
        self.unreadable = []
        events: list[Event] = []
        first_error: CalDavError | None = None
        for calendar in calendars:
            try:
                events += self._events_of(calendar, time_min, time_max)
            except CalDavError as exc:
                first_error = first_error or exc
                self.unreadable.append(calendar.display_name or "ohne Namen")
        if first_error is not None and len(self.unreadable) == len(calendars):
            raise first_error
        events.sort(key=lambda e: _sort_key(e))
        return events

    def _events_of(self, calendar: Calendar, time_min: datetime, time_max: datetime) -> list[Event]:
        start, end = _caldav_utc(time_min), _caldav_utc(time_max)
        body = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<C:calendar-query xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">'
            f'<D:prop><D:getetag/><C:calendar-data><C:expand start="{start}" end="{end}"/>'
            "</C:calendar-data></D:prop>"
            '<C:filter><C:comp-filter name="VCALENDAR"><C:comp-filter name="VEVENT">'
            f'<C:time-range start="{start}" end="{end}"/>'
            "</C:comp-filter></C:comp-filter></C:filter></C:calendar-query>"
        )
        resp = self._request("EVENTS", "REPORT", calendar.url, body=body, depth=1)
        root = _parse_xml(resp.body, "EVENTS", resp.status)
        blocks = [el.text for el in _find_all(root, "calendar-data") if el.text]
        return [
            dataclasses.replace(event, calendar=calendar.display_name)
            for event in parse_events(blocks)
            if _overlaps(event, time_min, time_max)
        ]

    def create_event(
        self,
        uid: str,
        title: str,
        start: datetime | date,
        end: datetime | date,
        location: str | None,
        note: str | None,
    ) -> str:
        """Legt den Termin an und gibt den Anzeigenamen des Kalenders zurück."""
        calendar = self._calendar()
        ics = build_vevent(uid, title, start, end, location, note, datetime.now(UTC))
        url = urljoin(
            calendar.url if calendar.url.endswith("/") else calendar.url + "/", f"{uid}.ics"
        )
        self._request(
            "CREATE",
            "PUT",
            url,
            body=ics,
            content_type="text/calendar; charset=utf-8",
            extra={"If-None-Match": "*"},
            ok_statuses=(200, 201, 204),
        )
        return calendar.display_name or "ohne Namen"


def _instant(value: datetime | date) -> datetime:
    return value if isinstance(value, datetime) else start_of_day(value)


def _sort_key(event: Event) -> datetime:
    return _instant(event.start)


def _overlaps(event: Event, time_min: datetime, time_max: datetime) -> bool:
    if event.unexpanded:
        # Serie nicht aufgelöst: ob eine Wiederholung ins Fenster fällt, ist unbekannt. Behalten
        # (der Aufrufer weist darauf hin), statt den Termin still zu verlieren.
        return _instant(event.start) < time_max
    start = _instant(event.start)
    end = _instant(event.end) if event.end is not None else start
    return start < time_max and (end > time_min or start >= time_min)


__all__ = ["TZ", "CalDavClient", "CalDavError", "Credentials", "Response", "Transport"]
