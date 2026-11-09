"""CalDAV-Client für iCloud (Port von calendarTool.js aus dem alten Projekt jarvis-os).

Ablauf (RFC 4791/6764): PRINCIPAL -> HOME_SET -> CALENDAR_LIST (PROPFIND), danach EVENTS
(REPORT calendar-query) bzw. PUT eines VEVENT. Diagnose-Meldungen wie im Original: nur
Schrittname, HTTP-Status und Grund, nie Zugangsdaten, Header oder vollständige Antworten.
"""

import base64
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from urllib.parse import urljoin

from mcp_servers.calendar.ics import Event, build_vevent, parse_events
from mcp_servers.calendar.timeutil import TZ, start_of_day

CALDAV_ENTRY_URL = "https://caldav.icloud.com/"
TIMEOUT_SECONDS = 15.0
MAX_REDIRECTS = 5
MAX_EVENTS = 25

ERROR_TEXTS: dict[str, str] = {
    "not_configured": (
        "Keine Kalender-Zugangsdaten gesetzt. Bitte 'jarvis secrets set apple_caldav' ausführen."
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
            if err.code in (301, 302, 307, 308) and location:
                current = urljoin(current, location)
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
        if cal.display_name == wanted:
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

    def _calendar(self) -> Calendar:
        principal = self._principal()
        home = self._home_set(principal)
        return select_calendar(self._calendars(home), self._calendar_name)

    def list_events(self, time_min: datetime, time_max: datetime) -> list[Event]:
        calendar = self._calendar()
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
        events = [e for e in parse_events(blocks) if _overlaps(e, time_min, time_max)]
        events.sort(key=lambda e: _sort_key(e))
        return events[:MAX_EVENTS]

    def create_event(
        self,
        uid: str,
        title: str,
        start: datetime | date,
        end: datetime | date,
        location: str | None,
        note: str | None,
    ) -> None:
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


def _instant(value: datetime | date) -> datetime:
    return value if isinstance(value, datetime) else start_of_day(value)


def _sort_key(event: Event) -> datetime:
    return _instant(event.start)


def _overlaps(event: Event, time_min: datetime, time_max: datetime) -> bool:
    start = _instant(event.start)
    end = _instant(event.end) if event.end is not None else start
    return start < time_max and (end > time_min or start >= time_min)


__all__ = ["TZ", "CalDavClient", "CalDavError", "Credentials", "Response", "Transport"]
