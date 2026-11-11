import json

import pytest

from mcp_servers.weather import server
from mcp_servers.weather.server import WeatherError, get_weather_data

GEO = {
    "results": [
        {
            "name": "Dortmund",
            "country": "Deutschland",
            "admin1": "Nordrhein-Westfalen",
            "latitude": 51.51,
            "longitude": 7.47,
        }  # fmt: skip
    ]
}
FORECAST = {
    "current": {
        "time": "2026-11-09T14:15",
        "temperature_2m": 10.4,
        "apparent_temperature": 8.0,
        "weather_code": 2,
        "wind_speed_10m": 5.8,
    }
}


def _patch(monkeypatch: pytest.MonkeyPatch, geo: object, forecast: object) -> list[str]:
    urls: list[str] = []

    def fake_fetch(url: str, params: dict[str, str]) -> bytes:
        urls.append(url)
        return json.dumps(geo if url == server.GEOCODING_URL else forecast).encode()

    monkeypatch.setattr(server, "fetch", fake_fetch)
    return urls


def test_current_weather_is_parsed(monkeypatch: pytest.MonkeyPatch) -> None:
    urls = _patch(monkeypatch, GEO, FORECAST)
    assert get_weather_data("Dortmund") == {
        "ok": True,
        "ort": "Dortmund",
        "land": "Deutschland",
        "region": "Nordrhein-Westfalen",
        "temperatur_c": 10.4,
        "gefuehlt_c": 8.0,
        "zustand": "teilweise bewölkt",
        "wind_kmh": 5.8,
        "zeitpunkt": "2026-11-09T14:15",
    }
    assert urls == [server.GEOCODING_URL, server.FORECAST_URL]


def test_unknown_weather_code_is_not_guessed(monkeypatch: pytest.MonkeyPatch) -> None:
    forecast = {"current": {**FORECAST["current"], "weather_code": 1234}}
    _patch(monkeypatch, GEO, forecast)
    assert get_weather_data("Dortmund")["zustand"] == "Wettercode 1234"


def test_unknown_place(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, {}, FORECAST)
    result = get_weather_data("Nirgendwo")
    assert result["ok"] is False
    assert "Nirgendwo" in result["fehler"]


def test_service_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(url: str, params: dict[str, str]) -> bytes:
        raise WeatherError("Der Wetterdienst Open-Meteo ist gerade nicht erreichbar.")

    monkeypatch.setattr(server, "fetch", boom)
    result = get_weather_data("Dortmund")
    assert result == {
        "ok": False,
        "fehler": "Der Wetterdienst Open-Meteo ist gerade nicht erreichbar.",
    }


def test_garbage_response_gives_clean_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, GEO, {"unerwartet": True})
    result = get_weather_data("Dortmund")
    assert result["ok"] is False


def test_empty_place() -> None:
    assert get_weather_data("   ")["ok"] is False


# --------------------------------------------------------------------------- Runde 4
DAILY = {
    "time": ["2026-11-09", "2026-11-10", "2026-11-11", "2026-11-12", "2026-11-13"],
    "weather_code": [3, 61, None, 1234],
    "temperature_2m_min": [7.0, 6.0, 4.0, 2.0, 1.0],
    "temperature_2m_max": [12.0, 9.5, 10.0, 8.0, 7.0],
    "precipitation_probability_max": [20, 85, 10],
}


def test_forecast_days_are_returned(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, GEO, {**FORECAST, "daily": DAILY})
    days = get_weather_data("Dortmund")["vorhersage"]
    assert len(days) == server.FORECAST_DAYS  # ein fünfter gelieferter Tag wird nicht gezeigt
    assert days[1] == {
        "datum": "2026-11-10", "min_c": 6.0, "max_c": 9.5, "zustand": "leichter Regen",
        "regen_prozent": 85,
    }  # fmt: skip
    assert days[2]["zustand"] is None  # fehlender Wert wird nicht geraten
    assert days[3]["zustand"] == "Wettercode 1234" and days[3]["regen_prozent"] is None


def test_forecast_is_requested_from_the_service(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[dict[str, str]] = []

    def fake_fetch(url: str, params: dict[str, str]) -> bytes:
        seen.append(params)
        return json.dumps(GEO if url == server.GEOCODING_URL else FORECAST).encode()

    monkeypatch.setattr(server, "fetch", fake_fetch)
    assert "vorhersage" not in get_weather_data("Dortmund")  # Dienst lieferte keine Tageswerte
    assert "precipitation_probability_max" in seen[1]["daily"]
    assert seen[1]["forecast_days"] == "4"


# --------------------------------------------------------------------------- Runde 6
def test_place_with_country_falls_back_to_the_name(monkeypatch: pytest.MonkeyPatch) -> None:
    asked: list[str] = []

    def fake_fetch(url: str, params: dict[str, str]) -> bytes:
        if url == server.GEOCODING_URL:
            asked.append(params["name"])
            return json.dumps(GEO if params["name"] == "Dortmund" else {}).encode()
        return json.dumps(FORECAST).encode()

    monkeypatch.setattr(server, "fetch", fake_fetch)
    assert get_weather_data("Dortmund, Deutschland")["ort"] == "Dortmund"
    assert asked == ["Dortmund, Deutschland", "Dortmund"]
    result = get_weather_data("Nirgendwo, Land")
    assert result["ok"] is False and "Nirgendwo, Land" in result["fehler"]


@pytest.mark.parametrize(("code", "text"), [(429, "begrenzt"), (400, "HTTP 400")])
def test_http_errors_are_not_reported_as_unreachable(
    monkeypatch: pytest.MonkeyPatch, code: int, text: str
) -> None:
    import email.message
    import urllib.error
    import urllib.request

    def refuse(url: str, timeout: float) -> object:
        raise urllib.error.HTTPError(url, code, "x", email.message.Message(), None)

    monkeypatch.setattr(urllib.request, "urlopen", refuse)
    result = get_weather_data("Dortmund")
    assert result["ok"] is False and text in result["fehler"]
    assert "nicht erreichbar" not in result["fehler"]
