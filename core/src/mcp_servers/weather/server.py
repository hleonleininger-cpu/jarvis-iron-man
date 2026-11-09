"""MCP-Server `weather`: get_weather(ort) über Open-Meteo (Geocoding + Forecast, ohne Schlüssel).

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


def fetch(url: str, params: dict[str, str]) -> bytes:
    """HTTP-GET, liefert den Antwortkörper. Wird in Tests ersetzt (keine Netzzugriffe)."""
    full = f"{url}?{urllib.parse.urlencode(params)}"
    try:
        with urllib.request.urlopen(full, timeout=TIMEOUT_SECONDS) as resp:  # noqa: S310
            return resp.read()
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        raise WeatherError("Der Wetterdienst Open-Meteo ist gerade nicht erreichbar.") from exc


class Place(BaseModel):
    name: str
    country: str = ""
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


class _Forecast(BaseModel):
    current: _Current


def parse_geocoding(body: bytes, ort: str) -> Place:
    try:
        results = _Geocoding.model_validate_json(body).results
    except ValidationError as exc:
        raise WeatherError("Unerwartete Antwort vom Geocoding-Dienst.") from exc
    if not results:
        raise WeatherError(f"Der Ort '{ort}' wurde nicht gefunden.")
    return results[0]


def parse_forecast(body: bytes, place: Place) -> dict[str, Any]:
    try:
        current = _Forecast.model_validate_json(body).current
    except ValidationError as exc:
        raise WeatherError("Unerwartete Antwort vom Wetterdienst.") from exc
    return {
        "ok": True,
        "ort": place.name,
        "land": place.country,
        "temperatur_c": current.temperature_2m,
        "gefuehlt_c": current.apparent_temperature,
        "zustand": WMO_CODES.get(current.weather_code, f"Wettercode {current.weather_code}"),
        "wind_kmh": current.wind_speed_10m,
        "zeitpunkt": current.time,
    }


def get_weather_data(ort: str) -> dict[str, Any]:
    """Aktuelles Wetter für einen Ort. Fehler kommen als {"ok": False, "fehler": ...} zurück."""
    name = ort.strip()
    if not name:
        return {"ok": False, "fehler": "Es wurde kein Ort angegeben."}
    try:
        geo = fetch(GEOCODING_URL, {"name": name, "count": "1", "language": "de", "format": "json"})
        place = parse_geocoding(geo, name)
        forecast = fetch(
            FORECAST_URL,
            {
                "latitude": str(place.latitude),
                "longitude": str(place.longitude),
                "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m",
                "wind_speed_unit": "kmh",
                "timezone": "auto",
            },
        )
        return parse_forecast(forecast, place)
    except WeatherError as exc:
        return {"ok": False, "fehler": str(exc)}


mcp = MCPServer("weather")


@mcp.tool()
def get_weather(ort: str) -> dict[str, Any]:
    """Aktuelles Wetter (Temperatur, Zustand, Wind) für einen Ort, z. B. "Dortmund"."""
    return get_weather_data(ort)


def main() -> None:
    mcp.run(transport="stdio")
