"""Wetter-Attrappe: feste Antworten statt Open-Meteo. Unbekannte Orte liefern keine Treffer."""

import json
import sys

from mcp_servers.weather import server

PLACES = {
    "dortmund": ("Dortmund", 51.51, 7.47),
    "berlin": ("Berlin", 52.52, 13.41),
    "münchen": ("München", 48.14, 11.58),
    "muenchen": ("München", 48.14, 11.58),
    "hamburg": ("Hamburg", 53.55, 9.99),
    "köln": ("Köln", 50.94, 6.96),
}
CURRENT = {
    "time": "2026-11-09T14:15",
    "temperature_2m": 11.5,
    "apparent_temperature": 9.0,
    "weather_code": 3,
    "wind_speed_10m": 14.0,
}


def fake_fetch(url: str, params: dict[str, str]) -> bytes:
    if url == server.GEOCODING_URL:
        place = PLACES.get(params["name"].strip().lower())
        if place is None:
            return json.dumps({}).encode()
        name, lat, lon = place
        result = {"name": name, "country": "Deutschland", "latitude": lat, "longitude": lon}
        return json.dumps({"results": [result]}).encode()
    return json.dumps({"current": CURRENT}).encode()


def main() -> None:
    server.fetch = fake_fetch
    server.main()


if __name__ == "__main__":
    sys.exit(main())
