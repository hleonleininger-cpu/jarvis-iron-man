"""Minimaler stdio-MCP-Server für Tests (kein Netz): get_weather mit festen Werten."""

import sys
from typing import Any

from mcp.server.mcpserver import MCPServer

mcp = MCPServer("fake")


@mcp.tool()
def get_weather(ort: str) -> dict[str, Any]:
    """Wetter (Attrappe)."""
    return {"ok": True, "ort": ort, "temperatur_c": 12.5, "zustand": "Nebel", "wind_kmh": 7.0}


@mcp.tool()
def create_event(titel: str, start: str = "") -> dict[str, Any]:
    """Schreibt (Attrappe): hängt eine Zeile an die in argv[1] genannte Datei an."""
    if len(sys.argv) > 1:
        with open(sys.argv[1], "a", encoding="utf-8") as fh:
            fh.write(f"{titel}|{start}\n")
    return {"ok": True, "angelegt": f"Neuer Termin: {titel}"}


@mcp.tool()
def preview_create_event(titel: str, start: str = "") -> dict[str, Any]:
    """Vorschau (Attrappe), schreibt nichts."""
    return {"ok": True, "vorschau": f"Neuer Termin: {titel} ({start})", "frage": "Anlegen?"}


@mcp.tool()
def secret_admin() -> str:
    """Nicht in jarvis.toml deklariert -> darf nie angeboten werden."""
    return "geheim"


if __name__ == "__main__":
    mcp.run(transport="stdio")
