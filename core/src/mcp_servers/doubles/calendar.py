"""Kalender-Attrappe: Termine zum angefragten Zeitfenster; Schreibaufrufe (PUT) werden nur
protokolliert (JSON-Zeilen in die per Argument übergebene Datei), nie irgendwohin geschrieben."""

import json
import re
import sys
from datetime import UTC, datetime, timedelta

from mcp_servers.calendar import server
from mcp_servers.calendar.caldav import CalDavClient, Credentials, Response, Transport

BASE = "https://p01-caldav.icloud.com/1/calendars/"
NS = 'xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav"'


def _multistatus(*parts: str) -> str:
    return f'<?xml version="1.0"?><D:multistatus {NS}>{"".join(parts)}</D:multistatus>'


def _vevent(title: str, start: datetime, hours: int) -> str:
    fmt = "%Y%m%dT%H%M%SZ"
    end = start + timedelta(hours=hours)
    return (
        f"BEGIN:VEVENT\r\nSUMMARY:{title}\r\nDTSTART:{start:{fmt}}\r\nDTEND:{end:{fmt}}\r\n"
        "END:VEVENT\r\n"
    )


def _events_for(body: str) -> str:
    match = re.search(r'time-range start="(\d{8}T\d{6}Z)"', body)
    first = (
        datetime.strptime(match.group(1), "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
        if match
        else (datetime.now(UTC))
    )
    ics = "BEGIN:VCALENDAR\r\n" + _vevent("Besprechung", first + timedelta(hours=9), 1)
    ics += _vevent("Handballtraining", first + timedelta(hours=17), 2) + "END:VCALENDAR\r\n"
    return _multistatus(
        f"<D:response><D:href>{BASE}privat/a.ics</D:href><D:propstat><D:prop>"
        f"<C:calendar-data><![CDATA[{ics}]]></C:calendar-data></D:prop></D:propstat></D:response>"
    )


def make_transport(log_path: str) -> Transport:
    def transport(method: str, url: str, headers: dict[str, str], body: bytes | None) -> Response:
        text = body.decode() if body else ""
        if method == "PUT":
            with open(log_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps({"method": "PUT", "url": url, "ics": text}) + "\n")
            return Response(201, "", url)
        if "current-user-principal" in text:
            href = (
                "<D:current-user-principal><D:href>/1/principal/</D:href>"
                "</D:current-user-principal>"
            )
        elif "calendar-home-set" in text:
            href = f"<C:calendar-home-set><D:href>{BASE}</D:href></C:calendar-home-set>"
        elif "supported-calendar-component-set" in text:
            return Response(207, _multistatus(
                f"<D:response><D:href>{BASE}privat/</D:href><D:propstat><D:prop><D:resourcetype>"
                "<D:collection/><C:calendar/></D:resourcetype><D:displayname>Privat</D:displayname>"
                '<C:supported-calendar-component-set><C:comp name="VEVENT"/>'
                "</C:supported-calendar-component-set></D:prop></D:propstat></D:response>"
            ), url)  # fmt: skip
        elif "calendar-query" in text:
            return Response(207, _events_for(text), url)
        else:
            return Response(500, "", url)
        return Response(
            207,
            _multistatus(
                f"<D:response><D:href>/</D:href><D:propstat><D:prop>{href}</D:prop></D:propstat></D:response>"
            ),
            url,
        )

    return transport


def main() -> None:
    log_path = sys.argv[1]
    sys.argv = sys.argv[:1]
    server.make_client = lambda: CalDavClient(
        Credentials("eval", "eval"), transport=make_transport(log_path)
    )
    server.main()


if __name__ == "__main__":
    main()
