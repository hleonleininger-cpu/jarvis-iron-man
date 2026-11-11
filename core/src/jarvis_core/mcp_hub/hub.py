"""MCP-Client-Hub: startet die in jarvis.toml deklarierten MCP-Server (stdio), reicht deren
Werkzeuge an den Agent und erzwingt vor JEDEM Aufruf die Policy. Jeder Aufruf geht ins Audit-Log
(nur Werkzeugname, Risikoklasse, Entscheidung, Ergebnis; nie Argumente)."""

import asyncio
import json
import os
import re
import sys
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Any, Self, TextIO

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
# Obergrenze je Werkzeugaufruf: ein hängender Server darf die Anfrage (und alle, die dahinter
# warten) nicht ewig blockieren.
TOOL_TIMEOUT_SECONDS = 90.0
_MAX_NAME_LENGTH = 80
_MAX_PREVIEW_LENGTH = 300
# Ein vom Modell erfundener Werkzeugname ist Modellausgabe und könnte Gesprächsinhalt tragen.
_PLAIN_NAME = re.compile(r"[A-Za-z0-9_.-]{1,80}")
INVALID_TOOL_NAME = "<ungültiger Name>"
TOOL_TIMEOUT_MESSAGE = "Das Werkzeug hat nicht rechtzeitig geantwortet. Es liegt kein Ergebnis vor."


def safe_tool_name(name: str) -> str:
    """Werkzeugname für Audit und Hinweise; alles außer schlichten Bezeichnern wird ersetzt."""
    return name if _PLAIN_NAME.fullmatch(name) else INVALID_TOOL_NAME


NO_CONFIRM_CHANNEL_MESSAGE = (
    "Bestätigung nötig, aber hier ist keine Rückfrage möglich. Das Werkzeug wurde nicht ausgeführt."
)
USER_DECLINED_MESSAGE = (
    "Der Nutzer hat abgelehnt. Es wurde NICHTS ausgeführt oder angelegt. "
    "Sage das dem Nutzer kurz und frage nicht erneut nach."
)
CONFIRM_TIMEOUT_MESSAGE = USER_DECLINED_MESSAGE
DENY_MESSAGE = "Die Policy lehnt dieses Werkzeug ab. Es wurde nicht ausgeführt."

# Das MCP-SDK gibt Server-Prozessen nur eine Minimal-Umgebung (PATH, HOME, ...). Ohne diese
# Variablen erreichen die Server hinter einem Proxy oder mit eigener Zertifikatsstelle das Netz
# nicht bzw. finden unter Linux den Schlüsselbund nicht. JARVIS_* wird bewusst nie vererbt.
PASS_THROUGH_ENV = (
    "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "ALL_PROXY",
    "http_proxy", "https_proxy", "no_proxy", "all_proxy",
    "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE",
    "DBUS_SESSION_BUS_ADDRESS", "XDG_RUNTIME_DIR", "PYTHON_KEYRING_BACKEND",
    "TZ", "LANG", "LC_ALL", "PYTHONUTF8", "PYTHONIOENCODING",
)  # fmt: skip


def server_env(declared: dict[str, str]) -> dict[str, str] | None:
    """Durchgereichte Variablen plus die in jarvis.toml deklarierten (diese haben Vorrang).

    Unter Windows sind Namen unabhängig von Groß-/Kleinschreibung: HTTPS_PROXY und https_proxy
    sind dieselbe Variable und dürfen nur einmal übergeben werden, sonst ist offen, welche gilt.
    """
    fold = str.upper if os.name == "nt" else str
    result: dict[str, str] = {}
    seen: set[str] = set()
    for key in PASS_THROUGH_ENV:
        if key in os.environ and fold(key) not in seen:
            result[key] = os.environ[key]
            seen.add(fold(key))
    for key, value in declared.items():
        for existing in [k for k in result if fold(k) == fold(key)]:
            del result[existing]
        result[key] = value
    return result or None


CallTool = Callable[[str, dict[str, Any]], Awaitable[Any]]
# (Werkzeug, Vorschau, Frage) -> Nutzer bestätigt? Gesetzt vom Agent je Anfrage.
Confirmer = Callable[[str, str, str], Awaitable[bool]]
current_confirmer: ContextVar[Confirmer | None] = ContextVar("jarvis_confirmer", default=None)


@dataclass(frozen=True)
class ToolInfo:
    server: str
    name: str
    description: str
    risk: RiskClass
    # Ungefähre Größe der Werkzeugdefinition im Prompt (Name, Beschreibung, Parameter-Schema).
    prompt_chars: int = 0


def _is_failure(result: Any) -> bool:
    """Die Werkzeuge melden erwartete Fehler als {"ok": False, "fehler": ...}."""
    if not isinstance(result, dict):
        return False
    mapping: dict[str, Any] = result  # pyright: ignore[reportUnknownVariableType]
    return mapping.get("ok") is False


class McpHub:
    def __init__(
        self,
        servers: dict[str, McpServerConfig],
        audit: AuditLog,
        model_name: str,
        log_dir: Path | None = None,
    ) -> None:
        self._servers = servers
        # Fehlerausgabe der Server je Lauf in <log_dir>/<server>.log (überschrieben, nicht
        # angehängt); ohne log_dir landet sie auf stderr.
        self._log_dir = log_dir
        self._audit = audit
        self._model_name = model_name
        self._running: list[asyncio.Task[None]] | None = None
        self._stop = asyncio.Event()
        self._connected: dict[str, MCPToolset[Any]] = {}
        self._infos: list[ToolInfo] = []
        self.failures: list[str] = []
        self.tool_timeout = TOOL_TIMEOUT_SECONDS

    def _risk(self, server: str, tool: str) -> RiskClass | None:
        """Einzige Quelle der Werkzeug-Metadaten: die Deklaration DIESES Servers in jarvis.toml."""
        cfg = self._servers.get(server)
        return cfg.tools.get(tool) if cfg is not None else None

    @property
    def offered_tools(self) -> frozenset[str]:
        """Werkzeuge, die dem Modell tatsächlich angeboten werden (deklariert und verbunden)."""
        return frozenset(info.name for info in self._infos)

    @property
    def toolsets(self) -> list[AbstractToolset[Any]]:
        """Verbundene Server; je Server nur die für ihn in jarvis.toml deklarierten Werkzeuge."""

        def only_declared(server: str, toolset: MCPToolset[Any]) -> AbstractToolset[Any]:
            declared = frozenset(self._servers[server].tools)
            return toolset.filtered(lambda _ctx, td: td.name in declared)

        return [only_declared(server, ts) for server, ts in self._connected.items()]

    async def __aenter__(self) -> Self:
        if self._running is not None:
            # Ein Hub trägt genau eine laufende Anfrage; der Agent reiht Anfragen deshalb ein.
            raise RuntimeError("McpHub ist bereits aktiv.")
        self._running = []
        self._stop = asyncio.Event()
        self.failures = []
        try:
            # Alle Server gleichzeitig starten (sonst summieren sich die Startzeiten je Anfrage).
            # Jeder Server lebt in einer eigenen Task, die ihn auch wieder beendet: MCP-Verbindungen
            # müssen in derselben Task geschlossen werden, in der sie geöffnet wurden.
            ready: dict[str, asyncio.Future[MCPToolset[Any]]] = {}
            loop = asyncio.get_running_loop()
            for name, cfg in self._servers.items():
                ready[name] = loop.create_future()
                self._running.append(
                    asyncio.create_task(self._serve(name, cfg, ready[name], self._stop))
                )
            results = await asyncio.gather(*ready.values(), return_exceptions=True)
            for name, result in zip(ready, results, strict=True):
                if isinstance(result, Exception):
                    self._server_failed(name, result)
                elif isinstance(result, BaseException):
                    raise result
                else:
                    self._connected[name] = result
            self._infos = await self._fetch_tool_infos()
        except BaseException:
            # Auch bei Abbruch mitten im Start: bereits gestartete Server-Prozesse beenden.
            await self.__aexit__(None, None, None)
            raise
        return self

    async def _serve(
        self,
        name: str,
        cfg: McpServerConfig,
        ready: asyncio.Future[MCPToolset[Any]],
        stop: asyncio.Event,
    ) -> None:
        """Öffnet die Verbindung zu einem Server, hält sie bis `stop` und schließt sie wieder."""
        log_file = self._open_log(name)
        toolset = self._build_toolset(name, cfg, log_file)
        try:
            async with toolset:
                if ready.done():  # Start wurde inzwischen abgebrochen: gleich wieder beenden
                    return
                ready.set_result(toolset)
                await stop.wait()
        except Exception as exc:
            if not ready.done():
                ready.set_exception(exc)
            else:  # Fehler beim Beenden: nicht die Anfrage scheitern lassen
                log.info("mcp_server_stop_failed", server=name, error=type(exc).__name__)
        finally:
            if not ready.done():
                ready.cancel()
            if log_file is not None:
                log_file.close()

    def _open_log(self, server: str) -> TextIO | None:
        if self._log_dir is None:
            return None
        try:
            self._log_dir.mkdir(parents=True, exist_ok=True)
            return (self._log_dir / f"{server}.log").open("w", encoding="utf-8")
        except OSError:  # Log nicht schreibbar: dann eben auf stderr, die Anfrage läuft weiter
            return None

    def _server_failed(self, server: str, exc: Exception) -> None:
        self.failures.append(
            f"MCP-Server '{server}' konnte nicht gestartet werden ({type(exc).__name__}); "
            "Antwort erfolgt ohne dessen Werkzeuge."
        )
        log.info("mcp_server_failed", server=server, error=type(exc).__name__)

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        running, self._running = self._running, None
        self._connected = {}
        self._infos = []
        if running is None:
            return
        self._stop.set()
        # Auch bei Abbruch (Strg+C, Client weg) zu Ende warten, damit keine Prozesse übrig bleiben.
        stopping = asyncio.gather(*running, return_exceptions=True)
        try:
            await asyncio.shield(stopping)
        except asyncio.CancelledError:
            await stopping
            raise

    def _build_toolset(
        self, server: str, cfg: McpServerConfig, log_file: TextIO | None = None
    ) -> MCPToolset[Any]:
        command = sys.executable if cfg.command == "python" else cfg.command

        async def process(
            _ctx: object, call_tool: CallTool, name: str, args: dict[str, Any]
        ) -> Any:
            return await self.gated_call(server, call_tool, name, args)

        return MCPToolset(
            # keep_alive=False: sonst lebt der Server-Prozess nach jeder Anfrage weiter (Leck).
            StdioTransport(
                command=command,
                args=cfg.args,
                env=server_env(cfg.env),
                keep_alive=False,
                log_file=log_file,
            ),
            process_tool_call=process,  # pyright: ignore[reportArgumentType]
            tool_error_behavior="failed",
            init_timeout=INIT_TIMEOUT_SECONDS,
        )

    @property
    def prompt_chars(self) -> int:
        """Platz, den die angebotenen Werkzeugdefinitionen im Prompt belegen (Zeichen)."""
        return sum(info.prompt_chars for info in self._infos)

    def list_tools(self) -> list[ToolInfo]:
        """Verfügbare Werkzeuge (nur nach `async with hub`)."""
        return list(self._infos)

    async def _fetch_tool_infos(self) -> list[ToolInfo]:
        infos: list[ToolInfo] = []
        for server, toolset in list(self._connected.items()):
            try:
                tools = await toolset.client.list_tools()
            except Exception as exc:  # verbunden, aber ohne lesbare Werkzeugliste: wie Ausfall
                del self._connected[server]
                self._server_failed(server, exc)
                continue
            provided = {tool.name for tool in tools}
            for tool in tools:
                risk = self._risk(server, tool.name)
                if risk is not None:
                    size = len(tool.name) + len(tool.description or "")
                    size += len(json.dumps(tool.input_schema, ensure_ascii=False))
                    infos.append(ToolInfo(server, tool.name, tool.description or "", risk, size))
            cfg = self._servers[server]
            missing = sorted((set(cfg.tools) | set(cfg.previews.values())) - provided)
            if missing:
                self.failures.append(
                    f"MCP-Server '{server}' bietet nicht an, was jarvis.toml nennt: "
                    f"{', '.join(missing)}."
                )
        return infos

    async def gated_call(
        self, server: str, call_tool: CallTool, name: str, args: dict[str, Any]
    ) -> Any:
        risk = self._risk(server, name)
        channel = current_channel.get()
        if risk is None:  # nicht deklariert -> nie ausführen
            self._record(channel, name, "UNKNOWN", Decision.DENY, "blocked")
            return DENY_MESSAGE
        decision = decide(risk, PolicyContext(channel=channel))
        if decision is Decision.DENY:
            self._record(channel, name, risk, decision, "blocked")
            return DENY_MESSAGE
        if decision is Decision.CONFIRM:
            return await self._confirmed_call(server, call_tool, name, args, risk, channel)
        return await self._execute(server, call_tool, name, args, risk, decision, channel)

    async def _execute(
        self,
        server: str,
        call_tool: CallTool,
        name: str,
        args: dict[str, Any],
        risk: RiskClass,
        decision: Decision | str,
        channel: str,
    ) -> Any:
        try:
            result: Any = await asyncio.wait_for(call_tool(name, args), self.tool_timeout)
        except TimeoutError:
            self._record(channel, name, risk, decision, "timeout")
            log.warning("tool_timeout", server=server, tool=name)
            return TOOL_TIMEOUT_MESSAGE
        except Exception:
            self._record(channel, name, risk, decision, "error")
            raise
        self._record(channel, name, risk, decision, "error" if _is_failure(result) else "ok")
        log.info("tool_call", server=server, tool=name, risk=str(risk))
        return result

    async def _preview(
        self, server: str, call_tool: CallTool, name: str, args: dict[str, Any]
    ) -> tuple[str, str] | dict[str, Any]:
        """(Vorschau, Frage) oder ein Fehler-Dict des Vorschau-Werkzeugs (ungültige Eingabe)."""
        preview_tool = self._servers[server].previews.get(name)
        if preview_tool is not None:
            result: Any = await asyncio.wait_for(call_tool(preview_tool, args), self.tool_timeout)
            if _is_failure(result):
                return result  # pyright: ignore[reportUnknownVariableType]
            if isinstance(result, dict):
                data: dict[str, Any] = result  # pyright: ignore[reportUnknownVariableType]
                if isinstance(data.get("vorschau"), str):
                    return data["vorschau"], str(data.get("frage") or "Ausführen?")
        shown = f"{name}({', '.join(f'{k}={v!r}' for k, v in args.items())})"
        if len(shown) > _MAX_PREVIEW_LENGTH:  # sichtbar machen, dass nicht alles gezeigt wird
            shown = shown[: _MAX_PREVIEW_LENGTH - 12] + "… (gekürzt)"
        return shown, "Ausführen?"

    async def _confirmed_call(
        self,
        server: str,
        call_tool: CallTool,
        name: str,
        args: dict[str, Any],
        risk: RiskClass,
        channel: str,
    ) -> Any:
        """CONFIRM: Vorschau, Ja/Nein abwarten, nur nach Ja ausführen (zwei Audit-Zeilen)."""
        try:
            preview = await self._preview(server, call_tool, name, args)
        except TimeoutError:
            self._record(channel, name, risk, Decision.CONFIRM, "timeout")
            return TOOL_TIMEOUT_MESSAGE
        except Exception:
            self._record(channel, name, risk, Decision.CONFIRM, "error")
            raise
        if isinstance(preview, dict):  # ungültige Eingabe: dem Modell melden, nichts fragen
            self._record(channel, name, risk, Decision.CONFIRM, "invalid")
            return preview
        confirmer = current_confirmer.get()
        if confirmer is None:
            self._record(channel, name, risk, Decision.CONFIRM, "blocked")
            return NO_CONFIRM_CHANNEL_MESSAGE
        self._record(channel, name, risk, Decision.CONFIRM, "pending")
        try:
            approved = await confirmer(name, *preview)
        except asyncio.CancelledError:
            self._record(channel, name, risk, "DENIED", "aborted")
            raise
        if not approved:
            self._record(channel, name, risk, "DENIED", "denied")
            return USER_DECLINED_MESSAGE
        return await self._execute(server, call_tool, name, args, risk, "CONFIRMED", channel)

    def record_unknown_tool(self, name: str) -> None:
        """Modell wollte ein unbekanntes Werkzeug aufrufen: abgelehnt und protokolliert."""
        self._record(
            current_channel.get(), safe_tool_name(name), "UNKNOWN", Decision.DENY, "blocked"
        )

    def _record(self, channel: str, tool: str, risk: str, decision: str, status: str) -> None:
        self._audit.record_tool_call(
            channel=channel,
            model=self._model_name,
            tool=tool[:_MAX_NAME_LENGTH],
            risk=str(risk),
            decision=str(decision),
            status=status,
        )
