import json

import pytest

from mcp_servers.weather import server
from mcp_servers.weather.server import WeatherError, get_weather_data

GEO = {
    "results": [
        {"name": "Dortmund", "country": "Deutschland", "latitude": 51.51, "longitude": 7.47}
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
