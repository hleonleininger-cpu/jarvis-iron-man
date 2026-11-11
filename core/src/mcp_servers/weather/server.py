"""MCP-Server `weather`: get_weather(ort) über Open-Meteo (Geocoding + Forecast, ohne Schlüssel).

Liefert das aktuelle Wetter und eine Tagesvorhersage (heute und die nächsten Tage), damit auch
Fragen nach "morgen" mit echten Werten beantwortet werden.

Läuft als eigener Prozess (stdio) und importiert nichts aus jarvis_core.
"""

import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from mcp.server.mcpserver import MCPServer
from pydantic import BaseModel, ValidationError

GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
TIMEOUT_SECONDS = 10.0
FORECAST_DAYS = 4  # heute + 3 Tage

# WMO-Wettercodes (Open-Meteo `weather_code`) -> deutscher Text.
WMO_CODES: dict[int, str] = {
    0: "klarer Himmel",
    1: "überwiegend klar",
    2: "teilweise bewölkt",
    3: "bedeckt",
    45: "Nebel",
    48: "Reifnebel",
    51: "leichter Nieselregen",
    53: "mäßiger Nieselregen",
    55: "starker Nieselregen",
    56: "leichter gefrierender Nieselregen",
    57: "starker gefrierender Nieselregen",
    61: "leichter Regen",
    63: "mäßiger Regen",
    65: "starker Regen",
    66: "leichter gefrierender Regen",
    67: "starker gefrierender Regen",
    71: "leichter Schneefall",
    73: "mäßiger Schneefall",
    75: "starker Schneefall",
    77: "Schneegriesel",
    80: "leichte Regenschauer",
    81: "mäßige Regenschauer",
    82: "heftige Regenschauer",
    85: "leichte Schneeschauer",
    86: "starke Schneeschauer",
    95: "Gewitter",
    96: "Gewitter mit leichtem Hagel",
    99: "Gewitter mit starkem Hagel",
}


class WeatherError(Exception):
    """Erwarteter Fehler mit nutzerlesbarer Meldung (kein Netz, Ort unbekannt, ...)."""


class PlaceNotFound(WeatherError):
    """Der Geocoding-Dienst kennt den Ort nicht."""


def fetch(url: str, params: dict[str, str]) -> bytes:
    """HTTP-GET, liefert den Antwortkörper. Wird in Tests ersetzt (keine Netzzugriffe)."""
    full = f"{url}?{urllib.parse.urlencode(params)}"
    try:
        with urllib.request.urlopen(full, timeout=TIMEOUT_SECONDS) as resp:  # noqa: S310
            return resp.read()
    except urllib.error.HTTPError as exc:  # erreichbar, aber die Anfrage wurde abgelehnt
        if exc.code == 429:
            raise WeatherError(
                "Open-Meteo begrenzt gerade die Anfragen. Bitte später erneut."
            ) from exc
        raise WeatherError(
            f"Der Wetterdienst Open-Meteo meldet einen Fehler (HTTP {exc.code})."
        ) from exc
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        raise WeatherError("Der Wetterdienst Open-Meteo ist gerade nicht erreichbar.") from exc


class Place(BaseModel):
    name: str
    country: str = ""
    admin1: str = ""  # Bundesland/Region: unterscheidet z. B. Frankfurt am Main und (Oder)
    latitude: float
    longitude: float


class _Geocoding(BaseModel):
    results: list[Place] = []


class _Current(BaseModel):
    time: str = ""
    temperature_2m: float
    apparent_temperature: float
    weather_code: int
    wind_speed_10m: float


class _Daily(BaseModel):
    time: list[str] = []
    weather_code: list[int | None] = []
    temperature_2m_min: list[float | None] = []
    temperature_2m_max: list[float | None] = []
    precipitation_probability_max: list[float | None] = []


class _Forecast(BaseModel):
    current: _Current
    daily: _Daily | None = None


def _condition(code: int | None) -> str | None:
    return None if code is None else WMO_CODES.get(code, f"Wettercode {code}")


def _at[T](values: list[T], index: int) -> T | None:
    return values[index] if index < len(values) else None


def _daily_forecast(daily: _Daily | None) -> list[dict[str, Any]]:
    """Je Tag Datum, Tiefst-/Höchstwert, Zustand, Regenwahrscheinlichkeit; fehlende Werte None."""
    if daily is None:
        return []
    return [
        {
            "datum": day,
            "min_c": _at(daily.temperature_2m_min, index),
            "max_c": _at(daily.temperature_2m_max, index),
            "zustand": _condition(_at(daily.weather_code, index)),
            "regen_prozent": _at(daily.precipitation_probability_max, index),
        }
        for index, day in enumerate(daily.time[:FORECAST_DAYS])
    ]


def parse_geocoding(body: bytes, ort: str) -> Place:
    try:
        results = _Geocoding.model_validate_json(body).results
    except ValidationError as exc:
        raise WeatherError("Unerwartete Antwort vom Geocoding-Dienst.") from exc
    if not results:
        raise PlaceNotFound(f"Der Ort '{ort}' wurde nicht gefunden.")
    return results[0]


def parse_forecast(body: bytes, place: Place) -> dict[str, Any]:
    try:
        forecast = _Forecast.model_validate_json(body)
    except ValidationError as exc:
        raise WeatherError("Unerwartete Antwort vom Wetterdienst.") from exc
    current = forecast.current
    result: dict[str, Any] = {
        "ok": True,
        "ort": place.name,
        "land": place.country,
        "region": place.admin1,
        "temperatur_c": current.temperature_2m,
        "gefuehlt_c": current.apparent_temperature,
        "zustand": _condition(current.weather_code),
        "wind_kmh": current.wind_speed_10m,
        "zeitpunkt": current.time,
    }
    days = _daily_forecast(forecast.daily)
    if days:
        result["vorhersage"] = days
    return result


def _find_place(name: str) -> Place:
    """Ort per Geocoding. Open-Meteo kennt nur den Ortsnamen: "Berlin, Deutschland" oder
    "Frankfurt am Main, Hessen" liefern nichts, also wird dann der Teil vor dem Komma gesucht."""
    params = {"count": "1", "language": "de", "format": "json"}
    try:
        return parse_geocoding(fetch(GEOCODING_URL, {"name": name, **params}), name)
    except PlaceNotFound:  # bei Netzfehlern nicht ein zweites Mal warten
        short = name.split(",", 1)[0].strip()
        if not short or short == name:
            raise
        return parse_geocoding(fetch(GEOCODING_URL, {"name": short, **params}), name)


def get_weather_data(ort: str) -> dict[str, Any]:
    """Aktuelles Wetter für einen Ort. Fehler kommen als {"ok": False, "fehler": ...} zurück."""
    name = ort.strip()
    if not name:
        return {"ok": False, "fehler": "Es wurde kein Ort angegeben."}
    try:
        place = _find_place(name)
        forecast = fetch(
            FORECAST_URL,
            {
                "latitude": str(place.latitude),
                "longitude": str(place.longitude),
                "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m",
                "daily": (
                    "weather_code,temperature_2m_min,temperature_2m_max,"
                    "precipitation_probability_max"
                ),
                "forecast_days": str(FORECAST_DAYS),
                "wind_speed_unit": "kmh",
                "timezone": "auto",
            },
        )
        return parse_forecast(forecast, place)
    except WeatherError as exc:
        return {"ok": False, "fehler": str(exc)}


mcp = MCPServer("weather", log_level="WARNING")


@mcp.tool()
def get_weather(ort: str) -> dict[str, Any]:
    """Wetter für einen Ort, z. B. "Dortmund": aktuelle Werte (Temperatur, Zustand, Wind) und
    unter `vorhersage` je Tag Tiefst-/Höchstwert, Zustand und Regenwahrscheinlichkeit für heute
    und die nächsten 3 Tage."""
    return get_weather_data(ort)


def main() -> None:
    mcp.run(transport="stdio")
