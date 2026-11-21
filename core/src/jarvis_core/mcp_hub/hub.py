"""MCP-Client-Hub: startet die in jarvis.toml deklarierten MCP-Server (stdio), reicht deren
Werkzeuge an den Agent und erzwingt vor JEDEM Aufruf die Policy. Jeder Aufruf geht ins Audit-Log
(nur Werkzeugname, Risikoklasse, Entscheidung, Ergebnis; nie Argumente)."""

import asyncio
import itertools
import json
import os
import re
import sys
import time
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
from jarvis_core.status import MAX_TEXT, StatusBus

log = structlog.get_logger("jarvis.mcp")

# Kanal der laufenden Anfrage ("cli", "api"); wird vom Agent gesetzt.
current_channel: ContextVar[str] = ContextVar("jarvis_channel", default="cli")
# Ergebnisse des laufenden Werkzeugaufrufs (für das Status-Ereignis `tool_end`, M3.8).
_call_outcomes: ContextVar[list[str] | None] = ContextVar("jarvis_call_outcomes", default=None)

INIT_TIMEOUT_SECONDS = 20.0
# Obergrenze je Werkzeugaufruf: ein hängender Server darf die Anfrage (und alle, die dahinter
# warten) nicht ewig blockieren.
TOOL_TIMEOUT_SECONDS = 90.0
_MAX_NAME_LENGTH = 80
_MAX_PREVIEW_LENGTH = 300
# Ein vom Modell erfundener Werkzeugname ist Modellausgabe und könnte Gesprächsinhalt tragen.
_PLAIN_NAME = re.compile(r"[A-Za-z0-9_.-]{1,80}")
INVALID_TOOL_NAME = "<ungültiger Name>"
# Werkzeuge, die in Simulationen nie angeboten werden: ihre Suchbegriffe gehen ins Netz.
NO_SIMULATION = frozenset({"web_search"})
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
        status: StatusBus | None = None,
    ) -> None:
        self._servers = servers
        # Meldet Beginn und Ende jedes Werkzeugaufrufs ans HUD (nur Name, Risiko, Ergebnis).
        self.status = status
        self._calls = itertools.count(1)
        # Fehlerausgabe der Server je Lauf in <log_dir>/<server>.log (überschrieben, nicht
        # angehängt); ohne log_dir landet sie auf stderr.
        self._log_dir = log_dir
        self._audit = audit
        self._model_name = model_name
        self._running: list[asyncio.Task[None]] | None = None
        self._stop = asyncio.Event()
        self._connected: dict[str, MCPToolset[Any]] = {}
        self._infos: list[ToolInfo] = []
        # Auswahl je Anfrage (M1.4): None = alle deklarierten und verbundenen Werkzeuge.
        self._selected: frozenset[str] | None = None
        # Planläufe (M1.3): auch lokales Schreiben (Notizen, Gedächtnis) braucht Rückfrage, weil
        # Werkzeugergebnisse früherer Schritte Fremdinhalt tragen können.
        self.confirm_local_writes = False
        self.failures: list[str] = []
        self.tool_timeout = TOOL_TIMEOUT_SECONDS

    def _risk(self, server: str, tool: str) -> RiskClass | None:
        """Einzige Quelle der Werkzeug-Metadaten: die Deklaration DIESES Servers in jarvis.toml."""
        cfg = self._servers.get(server)
        return cfg.tools.get(tool) if cfg is not None else None

    def risk_of(self, tool: str) -> str:
        """Risikoklasse eines deklarierten Werkzeugs aus jarvis.toml; leer, wenn unbekannt."""
        for cfg in self._servers.values():
            if tool in cfg.tools:
                return str(cfg.tools[tool])
        return ""

    @property
    def offered_tools(self) -> frozenset[str]:
        """Werkzeuge, die dem Modell angeboten werden (deklariert, verbunden, gewählt)."""
        return frozenset(info.name for info in self._offered_infos())

    def _offered_infos(self) -> list[ToolInfo]:
        selected = self._selected
        return [i for i in self._infos if selected is None or i.name in selected]

    def select_tools(self, names: frozenset[str] | None) -> None:
        """Beschränkt die angebotenen Werkzeuge für diese Anfrage (None = alle).

        Ändert nur das Angebot an das Modell; `gated_call` prüft weiterhin jeden Aufruf.
        """
        self._selected = names

    @property
    def toolsets(self) -> list[AbstractToolset[Any]]:
        """Verbundene Server; je Server nur die für ihn in jarvis.toml deklarierten und für die
        Anfrage gewählten Werkzeuge."""
        return self.toolsets_for(self._selected)

    def toolsets_for(self, names: frozenset[str] | None) -> list[AbstractToolset[Any]]:
        """Wie `toolsets`, aber mit einem eigenen Teilset (Sub-Agenten). Angeboten wird nur, was
        auch für die Anfrage gewählt ist (`select_tools`): ein Teilset erweitert nie das Angebot."""

        def only_declared(server: str, toolset: MCPToolset[Any]) -> AbstractToolset[Any]:
            declared = frozenset(self._servers[server].tools)
            selected = self._selected
            return toolset.filtered(
                lambda _ctx, td: (
                    td.name in declared
                    and (selected is None or td.name in selected)
                    and (names is None or td.name in names)
                )
            )

        return [only_declared(server, ts) for server, ts in self._connected.items()]

    def read_only_infos(self) -> list[ToolInfo]:
        """Lesende Werkzeuge ohne Netzzugriff nach außen mit privaten Suchbegriffen (Simulationen
        ändern nichts und schicken keine privaten Daten in eine Suche)."""
        return [i for i in self._infos if i.risk == RiskClass.READ and i.name not in NO_SIMULATION]

    async def __aenter__(self) -> Self:
        if self._running is not None:
            # Ein Hub trägt genau eine laufende Anfrage; der Agent reiht Anfragen deshalb ein.
            raise RuntimeError("McpHub ist bereits aktiv.")
        self._running = []
        self._stop = asyncio.Event()
        self._selected = None
        self.confirm_local_writes = False
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
        self._selected = None
        self.confirm_local_writes = False
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
        return sum(info.prompt_chars for info in self._offered_infos())

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
            missing = sorted((set(cfg.tools) | set(cfg.internal_tools)) - provided)
            if missing:
                self.failures.append(
                    f"MCP-Server '{server}' bietet nicht an, was jarvis.toml nennt: "
                    f"{', '.join(missing)}."
                )
        return infos

    async def gated_call(
        self, server: str, call_tool: CallTool, name: str, args: dict[str, Any]
    ) -> Any:
        """Jeder Werkzeugaufruf des Modells: Policy, Rückfrage, Ausführung, Audit. Mit Status-Bus
        gehen Beginn und Ende (Ergebnis wie im Audit, Dauer) als Ereignis ans HUD."""
        status = self.status
        if status is None:
            return await self._gate(server, call_tool, name, args)
        risk = self._risk(server, name)
        call = next(self._calls)
        tool = safe_tool_name(name)[:MAX_TEXT]
        risk_text = "UNKNOWN" if risk is None else str(risk)
        outcomes: list[str] = []
        token = _call_outcomes.set(outcomes)
        started = time.monotonic()
        status.publish("tool_start", call=call, tool=tool, risk=risk_text)
        try:
            return await self._gate(server, call_tool, name, args)
        finally:
            _call_outcomes.reset(token)
            status.publish(
                "tool_end", call=call, tool=tool, risk=risk_text,
                status=outcomes[-1] if outcomes else "aborted",
                ms=round((time.monotonic() - started) * 1000),
            )  # fmt: skip

    async def _gate(self, server: str, call_tool: CallTool, name: str, args: dict[str, Any]) -> Any:
        risk = self._risk(server, name)
        channel = current_channel.get()
        if risk is None or (self._selected is not None and name not in self._selected):
            # nicht deklariert oder für diese Anfrage nicht angeboten -> nie ausführen
            self._record(
                channel, name, "UNKNOWN" if risk is None else risk, Decision.DENY, "blocked"
            )
            return DENY_MESSAGE
        decision = decide(risk, PolicyContext(channel=channel))
        if (
            decision is Decision.ALLOW
            and risk is RiskClass.WRITE_LOCAL
            and self.confirm_local_writes
        ):
            decision = Decision.CONFIRM
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

    async def call_internal(
        self,
        server: str,
        tool: str,
        risk: RiskClass,
        worth_recording: Callable[[Any], bool] = lambda _data: True,
    ) -> Any:
        """Ruft ein internes Werkzeug (nicht fürs Modell) direkt auf, z. B. das Abholen fälliger
        Erinnerungen. Audit nur mit Name, Klasse und Ergebnis, Kanal "system"; Fehler immer,
        Erfolge nur, wenn `worth_recording` zustimmt (regelmäßiges Abfragen füllte sonst das
        Log)."""
        toolset = self._connected.get(server)
        if toolset is None:
            raise RuntimeError(f"MCP-Server '{server}' ist nicht verbunden.")
        try:
            result = await asyncio.wait_for(toolset.client.call_tool(tool, {}), self.tool_timeout)
        except Exception:
            self._record("system", tool, risk, Decision.ALLOW, "error")
            raise
        data: Any = result.data
        if _is_failure(data):
            self._record("system", tool, risk, Decision.ALLOW, "error")
        elif worth_recording(data):
            self._record("system", tool, risk, Decision.ALLOW, "ok")
        return data

    def has_read_tool(self, tool: str) -> bool:
        """Ist ein Werkzeug als READ deklariert (unabhängig davon, ob der Server gerade läuft)?"""
        return any(self._risk(server, tool) is RiskClass.READ for server in self._servers)

    async def read_tool(self, tool: str, args: dict[str, Any]) -> Any:
        """Ein lesendes Werkzeug ohne Modell aufrufen (HUD-Kacheln, M3.6). Nur ausdrücklich als
        READ deklarierte Werkzeuge; Policy und Audit wie immer (Kanal aus `current_channel`, nie
        Argumente oder Ergebnis). Gibt None zurück, wenn es nicht geht."""
        server = next((s for s in self._connected if self._risk(s, tool) is RiskClass.READ), None)
        if server is None:
            return None
        channel = current_channel.get()
        decision = decide(RiskClass.READ, PolicyContext(channel=channel))
        if decision is not Decision.ALLOW:
            self._record(channel, tool, RiskClass.READ, decision, "blocked")
            return None
        try:
            result = await asyncio.wait_for(
                self._connected[server].client.call_tool(tool, args), self.tool_timeout
            )
        except Exception:
            self._record(channel, tool, RiskClass.READ, decision, "error")
            return None
        data: Any = result.data
        self._record(
            channel, tool, RiskClass.READ, decision, "error" if _is_failure(data) else "ok"
        )
        return data

    def record_unknown_tool(self, name: str) -> None:
        """Modell wollte ein unbekanntes Werkzeug aufrufen: abgelehnt und protokolliert."""
        self._record(
            current_channel.get(), safe_tool_name(name), "UNKNOWN", Decision.DENY, "blocked"
        )

    def _record(self, channel: str, tool: str, risk: str, decision: str, status: str) -> None:
        outcomes = _call_outcomes.get()
        if outcomes is not None and status != "pending":  # "pending": Rückfrage läuft noch
            outcomes.append(status)
        self._audit.record_tool_call(
            channel=channel,
            model=self._model_name,
            tool=tool[:_MAX_NAME_LENGTH],
            risk=str(risk),
            decision=str(decision),
            status=status,
        )
