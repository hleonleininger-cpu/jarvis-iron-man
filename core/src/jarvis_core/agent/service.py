"""Chat-Dienst: Pydantic AI + Ollama (OpenAI-kompatibel), mit Audit pro Anfrage."""

import asyncio
import os
from collections.abc import AsyncGenerator, AsyncIterable
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

import structlog
from pydantic_ai import Agent
from pydantic_ai.exceptions import ModelAPIError, ModelHTTPError
from pydantic_ai.messages import AgentStreamEvent, FunctionToolCallEvent
from pydantic_ai.models import Model
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.tools import RunContext

from jarvis_core.agent.confirmation import ConfirmationBroker, ConfirmRequest
from jarvis_core.audit import AuditLog
from jarvis_core.config import Settings
from jarvis_core.hardware import detect_hardware, recommend
from jarvis_core.mcp_hub import McpHub, current_channel, current_confirmer

# Kein Werbebanner von Pydantic AI auf stdout/stderr.
os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")

SYSTEM_PROMPT = (
    "Du bist JARVIS, der persönliche KI-Assistent deines Nutzers. "
    "Antworte immer auf Deutsch, knapp und sachlich. "
    "Nutze ein Werkzeug nur, wenn die Frage es wirklich braucht, und gib ausschließlich Werte "
    "wieder, die ein Werkzeug geliefert hat. Meldet ein Werkzeug einen Fehler, sage das ehrlich "
    "und erfinde nichts. Bei Datumsangaben für Werkzeuge nenne immer das konkrete Datum "
    "(JJJJ-MM-TT), auch wenn der Nutzer nur einen Wochentag sagt. Für Werkzeuge, die etwas "
    "verändern, holt das System die Bestätigung selbst beim Nutzer ein: rufe sie direkt auf und "
    "frage vorher nicht nach."
)
WEEKDAYS_DE = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]


def system_prompt(now: datetime | None = None) -> str:
    """Systemprompt mit heutigem Datum, damit relative Angaben ("Montag") auflösbar sind."""
    today = (now or datetime.now(ZoneInfo("Europe/Berlin"))).astimezone(ZoneInfo("Europe/Berlin"))
    return (
        f"{SYSTEM_PROMPT} Heute ist {WEEKDAYS_DE[today.weekday()]}, der {today:%Y-%m-%d} "
        f"(Uhrzeit {today:%H:%M}, Zeitzone Europe/Berlin)."
    )


log = structlog.get_logger("jarvis.chat")


@dataclass(frozen=True)
class TextDelta:
    text: str


@dataclass(frozen=True)
class ToolUsed:
    name: str


@dataclass(frozen=True)
class Notice:
    """Hinweis an den Nutzer (z. B. MCP-Server nicht gestartet, unbekanntes Werkzeug abgelehnt)."""

    message: str


@dataclass(frozen=True)
class ConfirmRequested:
    """Ein CONFIRM-Werkzeug wartet auf Ja/Nein; Antwort über `ChatService.broker.resolve`."""

    id: str
    tool: str
    preview: str
    question: str


StreamEvent = TextDelta | ToolUsed | Notice | ConfirmRequested


class ChatError(Exception):
    """Fehler mit nutzerlesbarer, deutscher Meldung (kein Stacktrace nötig)."""


def resolve_model_name(settings: Settings) -> str:
    """Konfiguriertes Modell, sonst die Empfehlung von jarvis doctor."""
    if settings.model:
        return settings.model
    return recommend(detect_hardware()).model


def _translate(exc: Exception, model_name: str, base_url: str) -> ChatError:
    if isinstance(exc, ModelHTTPError) and exc.status_code == 404:
        return ChatError(
            f"Das Modell '{model_name}' ist nicht installiert. "
            f"Installiere es mit: ollama pull {model_name}"
        )
    if isinstance(exc, ModelHTTPError):
        return ChatError(f"Ollama meldete einen Fehler (HTTP {exc.status_code}).")
    if isinstance(exc, ModelAPIError | OSError):
        return ChatError(
            f"Ollama ist unter {base_url} nicht erreichbar. "
            "Starte Ollama (z. B. 'ollama serve') und versuche es erneut."
        )
    return ChatError("Unerwarteter Fehler bei der Modellabfrage.")


class ChatService:
    def __init__(
        self,
        model: Model,
        model_name: str,
        base_url: str,
        audit: AuditLog,
        hub: McpHub,
        broker: ConfirmationBroker | None = None,
    ) -> None:
        self.model_name = model_name
        self.hub = hub
        self.broker = broker or ConfirmationBroker()
        self._model = model
        self._base_url = base_url
        self._audit = audit

    async def stream(self, message: str, *, channel: str) -> AsyncGenerator[StreamEvent]:
        """Liefert Ereignisse. Schreibt genau einen Chat-Audit-Eintrag (ohne Inhalte).

        Der Agent läuft in einem eigenen Task, damit Ereignisse (z. B. eine Bestätigungsanfrage)
        auch dann ausgeliefert werden, während ein Werkzeugaufruf auf die Antwort wartet.
        """
        queue: asyncio.Queue[StreamEvent | BaseException | None] = asyncio.Queue()
        usage: list[int | None] = [None]

        def emit(request: ConfirmRequest) -> None:
            queue.put_nowait(
                ConfirmRequested(request.id, request.tool, request.preview, request.question)
            )

        async def confirmer(tool: str, preview: str, question: str) -> bool:
            approved = await self.broker.ask(tool, preview, question, emit)
            if not approved:  # feste Meldung, unabhängig davon, was das Modell danach sagt
                queue.put_nowait(Notice(f"Abgelehnt: '{tool}' wurde nicht ausgeführt."))
            return approved

        channel_token = current_channel.set(channel)
        confirmer_token = current_confirmer.set(confirmer)
        try:
            task = asyncio.create_task(self._run(message, queue, usage))
        finally:
            current_channel.reset(channel_token)
            current_confirmer.reset(confirmer_token)

        status = "error"
        try:
            while (item := await queue.get()) is not None:
                if isinstance(item, BaseException):
                    raise item
                yield item
            status = "ok"
        except (asyncio.CancelledError, GeneratorExit, KeyboardInterrupt):
            status = "aborted"
            raise
        except Exception as exc:
            log.warning(
                "chat_failed", channel=channel, model=self.model_name, error=type(exc).__name__
            )
            raise _translate(exc, self.model_name, self._base_url) from None
        finally:
            if not task.done():
                task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await task
            self._audit.record(
                channel=channel, model=self.model_name, tokens=usage[0], status=status
            )
            log.info("chat_done", channel=channel, model=self.model_name, status=status)

    async def _run(
        self,
        message: str,
        queue: asyncio.Queue[StreamEvent | BaseException | None],
        usage: list[int | None],
    ) -> None:
        async def on_events(
            _ctx: RunContext[object], events: AsyncIterable[AgentStreamEvent]
        ) -> None:
            async for event in events:
                if not isinstance(event, FunctionToolCallEvent):
                    continue
                name = event.part.tool_name
                if name in self.hub.offered_tools:
                    queue.put_nowait(ToolUsed(name))
                else:
                    self.hub.record_unknown_tool(name)
                    queue.put_nowait(Notice(f"Unbekanntes Werkzeug '{name[:60]}' wurde abgelehnt."))

        try:
            async with self.hub:
                for failure in self.hub.failures:
                    queue.put_nowait(Notice(failure))
                agent: Agent[None, str] = Agent(
                    self._model, system_prompt=system_prompt(), toolsets=self.hub.toolsets
                )
                async with agent.run_stream(message, event_stream_handler=on_events) as result:
                    async for delta in result.stream_text(delta=True):
                        queue.put_nowait(TextDelta(delta))
                    usage[0] = result.usage.total_tokens or None
            queue.put_nowait(None)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            queue.put_nowait(exc)


def build_service(
    settings: Settings,
    audit: AuditLog,
    model: Model | None = None,
    model_name: str | None = None,
    hub: McpHub | None = None,
) -> ChatService:
    name = model_name or resolve_model_name(settings)
    if model is None:
        provider = OpenAIProvider(base_url=settings.ollama_base_url, api_key="ollama")
        model = OpenAIChatModel(name, provider=provider)
    hub = hub or McpHub(settings.mcp_servers, audit, name)
    return ChatService(model, name, settings.ollama_base_url, audit, hub)
