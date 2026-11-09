"""MCP-Client-Hub: startet die in jarvis.toml deklarierten MCP-Server (stdio), reicht deren
Werkzeuge an den Agent und erzwingt vor JEDEM Aufruf die Policy. Jeder Aufruf geht ins Audit-Log
(nur Werkzeugname, Risikoklasse, Entscheidung, Ergebnis; nie Argumente)."""

import sys
from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack
from contextvars import ContextVar
from dataclasses import dataclass
from types import TracebackType
from typing import Any, Self

import structlog
from fastmcp.client.transports import StdioTransport
from pydantic_ai.mcp import MCPToolset
from pydantic_ai.toolsets import AbstractToolset

from jarvis_core.audit import AuditLog
from jarvis_core.config.settings import McpServerConfig
from jarvis_core.policy import Decision, PolicyContext, RiskClass, decide

log = structlog.get_logger("jarvis.mcp")

# Kanal der laufenden Anfrage ("cli", "api"); wird vom Agent gesetzt.
current_channel: ContextVar[str] = ContextVar("jarvis_channel", default="cli")

INIT_TIMEOUT_SECONDS = 20.0
_MAX_NAME_LENGTH = 80

CONFIRM_MESSAGE = "Bestätigung nötig, kommt in M1b. Das Werkzeug wurde nicht ausgeführt."
DENY_MESSAGE = "Die Policy lehnt dieses Werkzeug ab. Es wurde nicht ausgeführt."

CallTool = Callable[[str, dict[str, Any]], Awaitable[Any]]


@dataclass(frozen=True)
class ToolInfo:
    server: str
    name: str
    description: str
    risk: RiskClass


def _is_failure(result: Any) -> bool:
    """Die Werkzeuge melden erwartete Fehler als {"ok": False, "fehler": ...}."""
    if not isinstance(result, dict):
        return False
    mapping: dict[str, Any] = result  # pyright: ignore[reportUnknownVariableType]
    return mapping.get("ok") is False


class McpHub:
    def __init__(
        self, servers: dict[str, McpServerConfig], audit: AuditLog, model_name: str
    ) -> None:
        self._servers = servers
        self._audit = audit
        self._model_name = model_name
        # Einzige Quelle der Werkzeug-Metadaten: die Deklaration in jarvis.toml.
        self._risks: dict[str, RiskClass] = {
            tool: risk for cfg in servers.values() for tool, risk in cfg.tools.items()
        }
        self._stack: AsyncExitStack | None = None
        self._connected: dict[str, MCPToolset[Any]] = {}
        self._infos: list[ToolInfo] = []
        self.failures: list[str] = []

    @property
    def known_tools(self) -> frozenset[str]:
        return frozenset(self._risks)

    @property
    def offered_tools(self) -> frozenset[str]:
        """Werkzeuge, die dem Modell tatsächlich angeboten werden (deklariert und verbunden)."""
        return frozenset(info.name for info in self._infos)

    @property
    def toolsets(self) -> list[AbstractToolset[Any]]:
        """Verbundene Server; angeboten werden nur in jarvis.toml deklarierte Werkzeuge."""
        declared = self.known_tools
        return [
            ts.filtered(lambda _ctx, td: td.name in declared) for ts in self._connected.values()
        ]

    async def __aenter__(self) -> Self:
        self._stack = AsyncExitStack()
        await self._stack.__aenter__()
        for name, cfg in self._servers.items():
            toolset = self._build_toolset(name, cfg)
            try:
                await self._stack.enter_async_context(toolset)
            except Exception as exc:
                self.failures.append(
                    f"MCP-Server '{name}' konnte nicht gestartet werden ({type(exc).__name__}); "
                    "Antwort erfolgt ohne dessen Werkzeuge."
                )
                log.info("mcp_server_failed", server=name, error=type(exc).__name__)
                continue
            self._connected[name] = toolset
        self._infos = await self._fetch_tool_infos()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        stack, self._stack = self._stack, None
        self._connected = {}
        self._infos = []
        if stack is not None:
            await stack.__aexit__(exc_type, exc, tb)

    def _build_toolset(self, server: str, cfg: McpServerConfig) -> MCPToolset[Any]:
        command = sys.executable if cfg.command == "python" else cfg.command

        async def process(
            _ctx: object, call_tool: CallTool, name: str, args: dict[str, Any]
        ) -> Any:
            return await self.gated_call(server, call_tool, name, args)

        return MCPToolset(
            StdioTransport(command=command, args=cfg.args),
            process_tool_call=process,  # pyright: ignore[reportArgumentType]
            tool_error_behavior="failed",
            init_timeout=INIT_TIMEOUT_SECONDS,
        )

    def list_tools(self) -> list[ToolInfo]:
        """Verfügbare Werkzeuge (nur nach `async with hub`)."""
        return list(self._infos)

    async def _fetch_tool_infos(self) -> list[ToolInfo]:
        infos: list[ToolInfo] = []
        for server, toolset in self._connected.items():
            for tool in await toolset.client.list_tools():
                risk = self._risks.get(tool.name)
                if risk is not None:
                    infos.append(ToolInfo(server, tool.name, tool.description or "", risk))
        return infos

    async def gated_call(
        self, server: str, call_tool: CallTool, name: str, args: dict[str, Any]
    ) -> Any:
        risk = self._risks.get(name)
        channel = current_channel.get()
        if risk is None:  # nicht deklariert -> nie ausführen
            self._record(channel, name, "UNKNOWN", Decision.DENY, "blocked")
            return DENY_MESSAGE
        decision = decide(risk, PolicyContext(channel=channel))
        if decision is Decision.CONFIRM:
            self._record(channel, name, risk, decision, "blocked")
            return CONFIRM_MESSAGE
        if decision is Decision.DENY:
            self._record(channel, name, risk, decision, "blocked")
            return DENY_MESSAGE
        try:
            result: Any = await call_tool(name, args)
        except Exception:
            self._record(channel, name, risk, decision, "error")
            raise
        self._record(channel, name, risk, decision, "error" if _is_failure(result) else "ok")
        log.info("tool_call", server=server, tool=name, risk=str(risk), status="ok")
        return result

    def record_unknown_tool(self, name: str) -> None:
        """Modell wollte ein unbekanntes Werkzeug aufrufen: abgelehnt und protokolliert."""
        self._record(current_channel.get(), name, "UNKNOWN", Decision.DENY, "blocked")

    def _record(self, channel: str, tool: str, risk: str, decision: Decision, status: str) -> None:
        self._audit.record_tool_call(
            channel=channel,
            model=self._model_name,
            tool=tool[:_MAX_NAME_LENGTH],
            risk=str(risk),
            decision=str(decision),
            status=status,
        )
