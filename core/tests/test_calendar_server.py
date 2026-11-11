"""Kalender-MCP-Server: Zeitfenster, ICS, CalDAV gegen Attrappe, Tool-Funktionen."""

import json
from datetime import UTC, date, datetime, timedelta
from typing import Any

import keyring
import pytest
from keyring.backend import KeyringBackend

from mcp_servers.calendar import server
from mcp_servers.calendar.caldav import (
    CalDavClient,
    CalDavError,
    Calendar,
    Credentials,
    Response,
    same_site,
    select_calendar,
)
from mcp_servers.calendar.ics import Event, build_vevent, parse_events
from mcp_servers.calendar.timeutil import TZ, resolve_window

PASSWORD = "app-pass-wort-geheim"
NOW = datetime(2026, 11, 9, 10, 0, tzinfo=TZ)  # Montag


# --------------------------------------------------------------------------- Zeitfenster
def test_windows() -> None:
    lo, hi, _ = resolve_window("heute", NOW)
    assert (lo, hi) == (datetime(2026, 11, 9, tzinfo=TZ), datetime(2026, 11, 10, tzinfo=TZ))
    assert resolve_window("morgen", NOW)[0] == datetime(2026, 11, 10, tzinfo=TZ)
    lo, hi, _ = resolve_window("woche", NOW)  # Montag -> bis kommenden Montag 00:00
    assert hi == datetime(2026, 11, 16, tzinfo=TZ)


def test_week_on_sunday_ends_tomorrow() -> None:
    sunday = datetime(2026, 11, 15, 20, 0, tzinfo=TZ)
    assert resolve_window("woche", sunday)[1] == datetime(2026, 11, 16, tzinfo=TZ)


def test_date_and_range_are_inclusive_and_dst_safe() -> None:
    lo, hi, _ = resolve_window("2026-10-25/2026-10-25", NOW)  # Ende der Sommerzeit: 25 h
    assert hi.astimezone(UTC) - lo.astimezone(UTC) == timedelta(hours=25)
    lo, hi, _ = resolve_window("2026-11-10/2026-11-12", NOW)
    assert hi == datetime(2026, 11, 13, tzinfo=TZ)


@pytest.mark.parametrize("bad", ["gestern-ish", "2026-13-40", "2026-11-12/2026-11-10", ""])
def test_invalid_windows(bad: str) -> None:
    with pytest.raises(ValueError):
        resolve_window(bad, NOW)


# --------------------------------------------------------------------------- ICS
ICS = (
    "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nSUMMARY:Handball\\, Training\r\n"
    "DTSTART;TZID=Europe/Berlin:20261110T180000\r\nDTEND;TZID=Europe/Berlin:20261110T200000\r\n"
    "LOCATION:Sporthalle\r\nEND:VEVENT\r\n"
    "BEGIN:VEVENT\r\nSUMMARY:Geburtstag\r\nDTSTART;VALUE=DATE:20261111\r\n"
    "DTEND;VALUE=DATE:20261112\r\nEND:VEVENT\r\n"
    "BEGIN:VEVENT\r\nSUMMARY:UTC-Termin\r\nDTSTART:20261110T080000Z\r\n"
    "DTEND:20261110T090000Z\r\nEND:VEVENT\r\n"
    "BEGIN:VEVENT\r\nSUMMARY:Lan\r\n ger Titel\r\nDTSTART:20261110T080000Z\r\nEND:VEVENT\r\n"
    "BEGIN:VEVENT\r\nSUMMARY:Ohne Start\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
)


def test_parse_events() -> None:
    events = parse_events([ICS])
    assert [e.summary for e in events] == [
        "Handball, Training",
        "Geburtstag",
        "UTC-Termin",
        "Langer Titel",
    ]
    handball, birthday, utc, _ = events
    assert (
        handball.start == datetime(2026, 11, 10, 18, 0, tzinfo=TZ)
        and handball.location == "Sporthalle"
    )
    assert birthday.all_day and birthday.start == date(2026, 11, 11)
    assert utc.start == datetime(2026, 11, 10, 9, 0, tzinfo=TZ)  # 08:00 UTC = 09:00 Berlin


def test_vevent_roundtrip_and_format() -> None:
    start = datetime(2026, 11, 10, 15, 0, tzinfo=TZ)
    ics = build_vevent(
        "abc@jarvis", "Zahnarzt; Dr. Müller, Praxis", start, start + timedelta(hours=1),
        "Praxis\nHauptstr. 1", "x" * 200, datetime(2026, 11, 9, 9, 0, tzinfo=UTC),
    )  # fmt: skip
    assert ics.startswith("BEGIN:VCALENDAR\r\n") and ics.endswith("END:VCALENDAR\r\n")
    assert "DTSTART:20261110T140000Z" in ics  # 15:00 Berlin = 14:00 UTC
    assert all(len(line.encode()) <= 75 for line in ics.split("\r\n"))
    (event,) = parse_events([ics])
    assert event.summary == "Zahnarzt; Dr. Müller, Praxis"
    assert event.start == start and event.location == "Praxis\nHauptstr. 1"


def test_vevent_all_day() -> None:
    ics = build_vevent("u@j", "Urlaub", date(2026, 12, 24), date(2026, 12, 27), None, None, NOW)
    assert "DTSTART;VALUE=DATE:20261224" in ics and "DTEND;VALUE=DATE:20261227" in ics
    assert parse_events([ics])[0].all_day


# --------------------------------------------------------------------------- CalDAV-Attrappe
def _multistatus(*responses: str) -> str:
    return (
        '<?xml version="1.0"?><D:multistatus xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">'
        + "".join(responses)
        + "</D:multistatus>"
    )


PRINCIPAL = _multistatus(
    "<D:response><D:href>/</D:href><D:propstat><D:prop><D:current-user-principal>"
    "<D:href>/123/principal/</D:href></D:current-user-principal></D:prop></D:propstat></D:response>"
)
HOME = _multistatus(
    "<D:response><D:href>/123/principal/</D:href><D:propstat><D:prop><C:calendar-home-set>"
    "<D:href>https://p01-caldav.icloud.com/123/calendars/</D:href></C:calendar-home-set>"
    "</D:prop></D:propstat></D:response>"
)


def _calendar_entry(href: str, name: str, comp: str) -> str:
    return (
        f"<D:response><D:href>{href}</D:href><D:propstat><D:prop><D:resourcetype><D:collection/>"
        f"<C:calendar/></D:resourcetype><D:displayname>{name}</D:displayname>"
        f'<C:supported-calendar-component-set><C:comp name="{comp}"/></C:supported-calendar-component-set>'
        "</D:prop></D:propstat></D:response>"
    )


LIST = _multistatus(
    "<D:response><D:href>/123/calendars/</D:href><D:propstat><D:prop><D:resourcetype><D:collection/>"
    "</D:resourcetype></D:prop></D:propstat></D:response>",
    _calendar_entry("/123/calendars/erinnerungen/", "Erinnerungen", "VTODO"),
    _calendar_entry("/123/calendars/privat/", "Privat", "VEVENT"),
    _calendar_entry("/123/calendars/arbeit/", "Arbeit", "VEVENT"),
)


def _report(ics: str) -> str:
    return _multistatus(
        "<D:response><D:href>/123/calendars/privat/a.ics</D:href><D:propstat><D:prop>"
        f"<C:calendar-data><![CDATA[{ics}]]></C:calendar-data></D:prop></D:propstat></D:response>"
    )


WORK_ICS = (
    "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nSUMMARY:Teammeeting\r\n"
    "DTSTART:20261110T100000Z\r\nDTEND:20261110T110000Z\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
)


class FakeICloud:
    def __init__(self, statuses: dict[str, int] | None = None) -> None:
        self.requests: list[tuple[str, str, dict[str, str], str]] = []
        self.statuses = statuses or {}
        self.put_status = 201

    def __call__(
        self, method: str, url: str, headers: dict[str, str], body: bytes | None
    ) -> Response:
        text = body.decode() if body else ""
        self.requests.append((method, url, headers, text))
        if method == "PUT":
            return Response(self.put_status, "", url)
        if "calendar-query" in text and "/arbeit/" in url:
            status = self.statuses.get("arbeit", 207)
            return Response(status, _report(WORK_ICS) if status == 207 else "", url)
        for marker, content in (
            ("current-user-principal", PRINCIPAL),
            ("calendar-home-set", HOME),
            ("supported-calendar-component-set", LIST),
            ("calendar-query", _report(ICS)),
        ):
            if marker in text:
                return Response(self.statuses.get(marker, 207), content, url)
        return Response(500, "", url)


def _client(fake: FakeICloud, name: str | None = None) -> CalDavClient:
    return CalDavClient(Credentials("me@icloud.com", PASSWORD), transport=fake, calendar_name=name)


def test_list_events_follows_discovery_and_skips_reminder_lists() -> None:
    fake = FakeICloud()
    events = _client(fake).list_events(
        datetime(2026, 11, 10, tzinfo=TZ), datetime(2026, 11, 11, tzinfo=TZ)
    )
    methods = [(m, u) for m, u, _, _ in fake.requests]
    assert methods[0] == ("PROPFIND", "https://caldav.icloud.com/")
    assert methods[1][1] == "https://caldav.icloud.com/123/principal/"
    assert methods[2][1] == "https://p01-caldav.icloud.com/123/calendars/"
    assert methods[3] == ("REPORT", "https://p01-caldav.icloud.com/123/calendars/privat/")
    assert methods[4] == ("REPORT", "https://p01-caldav.icloud.com/123/calendars/arbeit/")
    assert len(methods) == 5  # die Erinnerungsliste (nur VTODO) wird nicht abgefragt
    assert "<C:expand" in fake.requests[3][3] and "time-range" in fake.requests[3][3]
    assert fake.requests[0][2]["Authorization"].startswith("Basic ")
    assert [(e.summary, e.calendar) for e in events] == [
        ("UTC-Termin", "Privat"),
        ("Langer Titel", "Privat"),
        ("Teammeeting", "Arbeit"),
        ("Handball, Training", "Privat"),
    ]  # alle Kalender, nach Start sortiert


def test_one_unreadable_calendar_does_not_hide_the_others(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeICloud({"arbeit": 500})
    monkeypatch.setattr(server, "make_client", lambda: _client(fake))
    result = server.list_events_data("2026-11-10", now=NOW)
    assert result["ok"] and result["anzahl"] == 3
    assert result["hinweis"] == "Nicht lesbare Kalender: Arbeit."


def test_all_calendars_unreadable_is_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeICloud({"arbeit": 500, "calendar-query": 500})
    monkeypatch.setattr(server, "make_client", lambda: _client(fake))
    assert server.list_events_data("2026-11-10", now=NOW)["grund"] == "unavailable"


@pytest.mark.parametrize("name", ["Arbeit", "arbeit", " ARBEIT "])
def test_named_calendar_is_the_only_one_read(name: str) -> None:
    fake = FakeICloud()
    events = _client(fake, name).list_events(
        datetime(2026, 11, 10, tzinfo=TZ), datetime(2026, 11, 11, tzinfo=TZ)
    )
    assert [m for m, _, _, _ in fake.requests].count("REPORT") == 1
    assert fake.requests[3][1].endswith("/arbeit/")
    assert [e.summary for e in events] == ["Teammeeting"]


def test_named_calendar_missing(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(CalDavError) as err:
        _client(FakeICloud(), "Gibt es nicht").list_events(NOW, NOW + timedelta(days=1))
    assert err.value.reason == "calendar_not_found"
    assert (
        "[caldav:SELECT_CALENDAR] status=200 reason=calendar_not_found:name_mismatch"
        in capsys.readouterr().err
    )


def test_select_calendar_empty() -> None:
    with pytest.raises(CalDavError):
        select_calendar([], None)
    assert select_calendar([Calendar("u1", "A"), Calendar("u2", "B")], None).url == "u1"


@pytest.mark.parametrize(
    ("status", "reason"),
    [(401, "unauthorized"), (403, "unauthorized"), (429, "rate_limited"), (500, "unavailable")],
)
def test_http_errors_are_classified_and_logged_without_secrets(
    status: int, reason: str, capsys: pytest.CaptureFixture[str]
) -> None:
    fake = FakeICloud({"current-user-principal": status})
    with pytest.raises(CalDavError) as err:
        _client(fake).list_events(NOW, NOW + timedelta(days=1))
    assert err.value.reason == reason
    logged = capsys.readouterr().err
    assert f"[caldav:PRINCIPAL] status={status} reason={reason}" in logged
    assert PASSWORD not in logged and "me@icloud.com" not in logged


def test_network_error_is_unavailable(capsys: pytest.CaptureFixture[str]) -> None:
    def down(method: str, url: str, headers: dict[str, str], body: bytes | None) -> Response:
        raise ConnectionError("kein Netz")

    client = CalDavClient(Credentials("u", PASSWORD), transport=down)
    with pytest.raises(CalDavError) as err:
        client.list_events(NOW, NOW + timedelta(days=1))
    assert err.value.reason == "unavailable"
    assert "network_error:ConnectionError" in capsys.readouterr().err


def test_garbage_xml_is_invalid_response() -> None:
    def garbage(method: str, url: str, headers: dict[str, str], body: bytes | None) -> Response:
        return Response(207, "<<<kein xml", url)

    with pytest.raises(CalDavError) as err:
        CalDavClient(Credentials("u", "p"), transport=garbage).list_events(NOW, NOW)
    assert err.value.reason == "invalid_response"


def test_create_event_puts_ics_without_overwriting() -> None:
    fake = FakeICloud()
    start = datetime(2026, 11, 10, 15, 0, tzinfo=TZ)
    _client(fake).create_event(
        "uid-1@jarvis", "Zahnarzt", start, start + timedelta(hours=1), None, None
    )
    method, url, headers, body = fake.requests[-1]
    assert (method, url) == (
        "PUT",
        "https://p01-caldav.icloud.com/123/calendars/privat/uid-1@jarvis.ics",
    )
    assert headers["If-None-Match"] == "*" and headers["Content-Type"].startswith("text/calendar")
    assert "SUMMARY:Zahnarzt" in body and PASSWORD not in body


def test_create_event_rejected_by_icloud() -> None:
    fake = FakeICloud()
    fake.put_status = 412
    start = datetime(2026, 11, 10, 15, 0, tzinfo=TZ)
    with pytest.raises(CalDavError) as err:
        _client(fake).create_event("u@j", "X", start, start + timedelta(hours=1), None, None)
    assert err.value.reason == "write_failed"


# --------------------------------------------------------------------------- Tool-Funktionen
@pytest.fixture
def fake_icloud(monkeypatch: pytest.MonkeyPatch) -> FakeICloud:
    fake = FakeICloud()
    monkeypatch.setattr(server, "make_client", lambda: _client(fake))
    return fake


def test_list_events_tool(fake_icloud: FakeICloud) -> None:
    result = server.list_events_data("2026-11-10", now=NOW)
    assert result["ok"] and result["anzahl"] == 4
    handball = next(t for t in result["termine"] if t["titel"] == "Handball, Training")
    assert handball == {
        "titel": "Handball, Training", "start": "2026-11-10T18:00", "ende": "2026-11-10T20:00",
        "ganztaegig": False, "ort": "Sporthalle", "kalender": "Privat",
    }  # fmt: skip


def test_list_events_tool_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    assert server.list_events_data("irgendwann")["grund"] == "invalid_input"
    fake = FakeICloud({"current-user-principal": 401})
    monkeypatch.setattr(server, "make_client", lambda: _client(fake))
    result = server.list_events_data("heute")
    assert result["ok"] is False and "jarvis secrets set apple_caldav" in result["fehler"]


def test_preview_names_concrete_date_with_year(fake_icloud: FakeICloud) -> None:
    result = server.preview_event_data("Zahnarzt", "2026-11-09T15:00", None, None, None, now=NOW)
    assert (
        result["vorschau"] == "Neuer Termin: Zahnarzt, Mo 09.11.2026 15:00-16:00, Kalender: Privat"
    )
    assert all(method != "PUT" for method, _, _, _ in fake_icloud.requests)  # Vorschau schreibt nie
    assert result["frage"] == "Anlegen?"


@pytest.mark.parametrize(
    ("start", "past"),
    [
        ("2025-11-10T15:00", True),  # falsches Jahr geraten
        ("2026-11-09T08:00", True),  # heute, aber schon vorbei
        ("2026-11-08", True),  # ganztägig gestern
        ("2026-11-09", False),  # ganztägig heute läuft noch
        ("2026-11-09T09:30", False),  # läuft gerade (endet 10:30)
        ("2027-01-05T15:00", False),
    ],
)
def test_preview_warns_about_events_in_the_past(
    fake_icloud: FakeICloud, start: str, past: bool
) -> None:
    preview = server.preview_event_data("Zahnarzt", start, None, None, None, now=NOW)["vorschau"]
    assert (server.PAST_WARNING in preview) is past


def test_list_events_tool_says_when_it_truncates(monkeypatch: pytest.MonkeyPatch) -> None:
    class Many:
        unreadable: list[str] = []

        def list_events(self, time_min: datetime, time_max: datetime) -> list[Event]:
            return [Event(f"Termin {i}", time_min + timedelta(minutes=i), None) for i in range(40)]

    monkeypatch.setattr(server, "make_client", lambda: Many())
    result = server.list_events_data("heute", now=NOW)
    assert result["anzahl"] == 40 and len(result["termine"]) == server.MAX_EVENTS
    assert result["hinweis"] == "Nur die ersten 25 von 40 Terminen gezeigt."
    assert "hinweis" not in server.list_events_data("irgendwann")


# --------------------------------------------------------------------------- Zugangsdaten-Grenze
@pytest.mark.parametrize(
    ("target", "allowed"),
    [
        ("https://p42-caldav.icloud.com/1/", True),
        ("https://icloud.com/", True),
        ("http://p42-caldav.icloud.com/1/", False),  # kein Klartext
        ("https://icloud.com.evil.example/1/", False),
        ("https://evilicloud.com/1/", False),
        ("https://example.org/", False),
        ("/nur/pfad", False),
    ],
)
def test_same_site(target: str, allowed: bool) -> None:
    assert same_site("https://caldav.icloud.com/", target) is allowed


def test_credentials_are_never_sent_to_a_foreign_host(capsys: pytest.CaptureFixture[str]) -> None:
    """Ein href auf eine fremde Domain darf das App-Passwort nicht bekommen."""
    evil_home = HOME.replace("https://p01-caldav.icloud.com", "https://angreifer.example")

    class Evil(FakeICloud):
        def __call__(
            self, method: str, url: str, headers: dict[str, str], body: bytes | None
        ) -> Response:
            response = super().__call__(method, url, headers, body)
            if body and b"calendar-home-set" in body:
                return Response(207, evil_home, url)
            return response

    fake = Evil()
    with pytest.raises(CalDavError) as err:
        _client(fake).list_events(NOW, NOW + timedelta(days=1))
    assert err.value.reason == "invalid_response"
    assert all("angreifer.example" not in url for _, url, _, _ in fake.requests)
    assert "foreign_host" in capsys.readouterr().err


def test_redirect_to_foreign_host_is_not_followed(monkeypatch: pytest.MonkeyPatch) -> None:
    import email.message
    import urllib.error
    import urllib.request

    from mcp_servers.calendar import caldav

    opened: list[tuple[str, str | None]] = []

    def fake_urlopen(request: urllib.request.Request, timeout: float) -> object:
        opened.append((request.full_url, request.get_header("Authorization")))
        headers = email.message.Message()
        headers["Location"] = "https://angreifer.example/abgriff"
        raise urllib.error.HTTPError(request.full_url, 302, "Found", headers, None)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    response = caldav.urllib_transport(
        "PROPFIND", "https://caldav.icloud.com/", {"Authorization": "Basic geheim"}, b"<x/>"
    )
    assert response.status == 302
    assert opened == [("https://caldav.icloud.com/", "Basic geheim")]


@pytest.mark.parametrize(
    ("start", "ende"),
    [
        ("morgen", None),
        ("2026-11-09T15:00", "2026-11-09T14:00"),
        ("2026-11-09", "2026-11-09T10:00"),
        ("2026-11-09T15:00", "kaputt"),
    ],
)
def test_invalid_event_input(start: str, ende: str | None) -> None:
    assert server.preview_event_data("X", start, ende, None, None)["ok"] is False


def test_create_event_tool_writes(fake_icloud: FakeICloud) -> None:
    result = server.create_event_data("Zahnarzt", "2026-11-09T15:00", None, "Praxis", None)
    assert result["ok"] and "Zahnarzt" in result["angelegt"]
    assert fake_icloud.requests[-1][0] == "PUT"


def test_create_event_tool_error_writes_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeICloud()
    fake.put_status = 500
    monkeypatch.setattr(server, "make_client", lambda: _client(fake))
    result = server.create_event_data("Zahnarzt", "2026-11-09T15:00", None, None, None)
    assert result["ok"] is False and result["grund"] == "write_failed"


class MemoryKeyring(KeyringBackend):
    priority = 1  # type: ignore[assignment]

    def __init__(self) -> None:
        super().__init__()
        self.store: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self.store.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.store[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        self.store.pop((service, username), None)


def test_credentials_come_from_keyring_and_missing_is_not_configured() -> None:
    backend = MemoryKeyring()
    keyring.set_keyring(backend)
    with pytest.raises(CalDavError) as err:
        server.load_credentials()
    assert err.value.reason == "not_configured"
    backend.store[("jarvis", "apple_caldav")] = json.dumps({"username": "a@b.de", "password": "pw"})
    assert server.load_credentials() == Credentials("a@b.de", "pw")
    backend.store[("jarvis", "apple_caldav")] = "kein json"
    with pytest.raises(CalDavError):
        server.load_credentials()


def test_not_configured_message_points_to_secrets_command() -> None:
    keyring.set_keyring(MemoryKeyring())
    result: dict[str, Any] = server.list_events_data("heute")
    assert result["grund"] == "not_configured"
    assert "jarvis secrets set apple_caldav" in result["fehler"]


# --------------------------------------------------------------------------- Runde 2
def test_cancelled_events_are_not_listed() -> None:
    ics = (
        "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nSUMMARY:Abgesagt\r\nSTATUS:CANCELLED\r\n"
        "DTSTART:20261110T080000Z\r\nEND:VEVENT\r\n"
        "BEGIN:VEVENT\r\nSUMMARY:Findet statt\r\nSTATUS:CONFIRMED\r\n"
        "DTSTART:20261110T090000Z\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
    )
    assert [e.summary for e in parse_events([ics])] == ["Findet statt"]


def test_window_is_limited_to_a_quarter() -> None:
    assert resolve_window("2026-01-01/2026-04-02", NOW)[2] == "2026-01-01/2026-04-02"
    with pytest.raises(ValueError, match="höchstens 92 Tage"):
        resolve_window("2026-01-01/2026-04-03", NOW)


# --------------------------------------------------------------------------- Runde 4
def test_overlong_title_is_rejected_before_asking() -> None:
    result = server.preview_event_data("x" * 201, "2026-11-10T15:00", None, None, None, now=NOW)
    assert result["ok"] is False and "höchstens 200 Zeichen" in result["fehler"]


def test_blank_location_and_note_are_not_written(fake_icloud: FakeICloud) -> None:
    server.create_event_data("Zahnarzt", "2026-11-10T15:00", None, "   ", " \n ")
    body = fake_icloud.requests[-1][3]
    assert "LOCATION" not in body and "DESCRIPTION" not in body


def test_overlong_note_is_shortened(fake_icloud: FakeICloud) -> None:
    server.create_event_data("Zahnarzt", "2026-11-10T15:00", None, None, "n" * 5000)
    body = fake_icloud.requests[-1][3].replace("\r\n ", "")
    assert body.count("n" * 2000) == 1 and "n" * 2001 not in body


# --------------------------------------------------------------------------- Runde 5
def test_unknown_tzid_keeps_the_event_in_berlin_time() -> None:
    ics = (
        "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nSUMMARY:Outlook-Einladung\r\n"
        'DTSTART;TZID="W. Europe Standard Time":20261110T150000\r\n'
        'DTEND;TZID="W. Europe Standard Time":20261110T160000\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n'
    )
    (event,) = parse_events([ics])
    assert event.start == datetime(2026, 11, 10, 15, 0, tzinfo=TZ)


@pytest.mark.parametrize(
    ("duration", "end"),
    [
        ("PT1H30M", datetime(2026, 11, 10, 16, 30, tzinfo=TZ)),
        ("P1DT2H", datetime(2026, 11, 11, 17, 0, tzinfo=TZ)),
        ("PT45M", datetime(2026, 11, 10, 15, 45, tzinfo=TZ)),
        ("unsinn", None),
    ],
)
def test_duration_gives_the_end(duration: str, end: datetime | None) -> None:
    ics = (
        "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nSUMMARY:Mit Dauer\r\n"
        f"DTSTART;TZID=Europe/Berlin:20261110T150000\r\nDURATION:{duration}\r\n"
        "END:VEVENT\r\nEND:VCALENDAR\r\n"
    )
    assert parse_events([ics])[0].end == end


def test_all_day_end_is_the_last_day(fake_icloud: FakeICloud) -> None:
    """ICS-DTEND ist exklusiv; dem Modell wird der letzte Tag genannt."""
    birthday = next(
        t
        for t in server.list_events_data("2026-11-11", now=NOW)["termine"]
        if t["titel"] == "Geburtstag"
    )
    assert (birthday["start"], birthday["ende"]) == ("2026-11-11", "2026-11-11")


@pytest.mark.parametrize(
    ("zeitraum", "first", "last_exclusive"),
    [
        ("übermorgen", datetime(2026, 11, 11, tzinfo=TZ), datetime(2026, 11, 12, tzinfo=TZ)),
        ("Nächste Woche", datetime(2026, 11, 16, tzinfo=TZ), datetime(2026, 11, 23, tzinfo=TZ)),
    ],
)
def test_more_named_windows(zeitraum: str, first: datetime, last_exclusive: datetime) -> None:
    lo, hi, _ = resolve_window(zeitraum, NOW)
    assert (lo, hi) == (first, last_exclusive)


def test_unavailable_keyring_is_not_reported_as_missing_credentials() -> None:
    from keyring.errors import KeyringError

    class Broken(MemoryKeyring):
        def get_password(self, service: str, username: str) -> str | None:
            raise KeyringError("kein Backend")

    keyring.set_keyring(Broken())
    result = server.list_events_data("heute", now=NOW)
    assert result["grund"] == "keyring_unavailable" and "Schlüsselbund" in result["fehler"]


# --------------------------------------------------------------------------- Runde 6
def test_preview_without_credentials_fails_before_asking() -> None:
    keyring.set_keyring(MemoryKeyring())
    result = server.preview_event_data("Zahnarzt", "2026-11-10T15:00", None, None, None, now=NOW)
    assert result["ok"] is False and result["grund"] == "not_configured"


def test_preview_names_the_chosen_calendar(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeICloud()
    monkeypatch.setattr(server, "make_client", lambda: _client(fake, "arbeit"))
    result = server.preview_event_data("Zahnarzt", "2026-11-10T15:00", None, None, None, now=NOW)
    assert result["vorschau"].endswith("Kalender: Arbeit")


# --------------------------------------------------------------------------- Runde 7
def test_alarm_inside_an_event_does_not_overwrite_its_title() -> None:
    ics = (
        "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nSUMMARY:Zahnarzt\r\n"
        "DTSTART:20261110T140000Z\r\nBEGIN:VALARM\r\nACTION:EMAIL\r\n"
        "SUMMARY:Erinnerung per Mail\r\nTRIGGER:-PT15M\r\nEND:VALARM\r\n"
        "LOCATION:Praxis\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
    )
    (event,) = parse_events([ics])
    assert (event.summary, event.location) == ("Zahnarzt", "Praxis")


def test_unexpanded_series_is_kept_and_flagged(monkeypatch: pytest.MonkeyPatch) -> None:
    series = (
        "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nSUMMARY:Handball\r\nRRULE:FREQ=WEEKLY\r\n"
        "DTSTART:20260105T170000Z\r\nDTEND:20260105T190000Z\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
    )

    class NoExpand(FakeICloud):
        def __call__(
            self, method: str, url: str, headers: dict[str, str], body: bytes | None
        ) -> Response:
            if body and b"calendar-query" in body:
                return Response(207, _report(series), url)
            return super().__call__(method, url, headers, body)

    monkeypatch.setattr(server, "make_client", lambda: _client(NoExpand()))
    result = server.list_events_data("2026-11-10", now=NOW)
    assert [t["titel"] for t in result["termine"]] == ["Handball", "Handball"]  # je Kalender
    assert "Wiederkehrende Termine" in result["hinweis"] and "Handball" in result["hinweis"]
