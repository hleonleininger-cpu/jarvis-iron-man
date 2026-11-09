"""Minimaler stdio-MCP-Server für Tests (kein Netz): get_weather mit festen Werten."""

from typing import Any

from mcp.server.mcpserver import MCPServer

mcp = MCPServer("fake")


@mcp.tool()
def get_weather(ort: str) -> dict[str, Any]:
    """Wetter (Attrappe)."""
    return {"ok": True, "ort": ort, "temperatur_c": 12.5, "zustand": "Nebel", "wind_kmh": 7.0}


@mcp.tool()
def secret_admin() -> str:
    """Nicht in jarvis.toml deklariert -> darf nie angeboten werden."""
    return "geheim"


if __name__ == "__main__":
    mcp.run(transport="stdio")
