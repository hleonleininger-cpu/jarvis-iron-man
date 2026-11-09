"""Chat-Dienst: Pydantic AI + Ollama (OpenAI-kompatibel), mit Audit pro Anfrage."""

import asyncio
import os
from collections.abc import AsyncGenerator, AsyncIterable
from dataclasses import dataclass

import structlog
from pydantic_ai import Agent
from pydantic_ai.exceptions import ModelAPIError, ModelHTTPError
from pydantic_ai.messages import AgentStreamEvent, FunctionToolCallEvent
from pydantic_ai.models import Model
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.tools import RunContext

from jarvis_core.audit import AuditLog
from jarvis_core.config import Settings
from jarvis_core.hardware import detect_hardware, recommend
from jarvis_core.mcp_hub import McpHub, current_channel

# Kein Werbebanner von Pydantic AI auf stdout/stderr.
os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")

SYSTEM_PROMPT = (
    "Du bist JARVIS, der persönliche KI-Assistent deines Nutzers. "
    "Antworte immer auf Deutsch, knapp und sachlich. "
    "Nutze ein Werkzeug nur, wenn die Frage es wirklich braucht, und gib ausschließlich Werte "
    "wieder, die ein Werkzeug geliefert hat. Meldet ein Werkzeug einen Fehler, sage das ehrlich "
    "und erfinde nichts."
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


StreamEvent = TextDelta | ToolUsed | Notice


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
        self, model: Model, model_name: str, base_url: str, audit: AuditLog, hub: McpHub
    ) -> None:
        self.model_name = model_name
        self.hub = hub
        self._model = model
        self._base_url = base_url
        self._audit = audit

    async def stream(self, message: str, *, channel: str) -> AsyncGenerator[StreamEvent]:
        """Liefert Ereignisse. Schreibt genau einen Chat-Audit-Eintrag (ohne Inhalte)."""
        status = "error"
        tokens: int | None = None
        pending: list[StreamEvent] = []
        channel_token = current_channel.set(channel)

        async def on_events(
            _ctx: RunContext[object], events: AsyncIterable[AgentStreamEvent]
        ) -> None:
            async for event in events:
                if not isinstance(event, FunctionToolCallEvent):
                    continue
                name = event.part.tool_name
                if name in self.hub.offered_tools:
                    pending.append(ToolUsed(name))
                else:
                    self.hub.record_unknown_tool(name)
                    pending.append(Notice(f"Unbekanntes Werkzeug '{name[:60]}' wurde abgelehnt."))

        def drain() -> list[StreamEvent]:
            items = list(pending)
            pending.clear()
            return items

        try:
            async with self.hub:
                for failure in self.hub.failures:
                    yield Notice(failure)
                agent: Agent[None, str] = Agent(
                    self._model, system_prompt=SYSTEM_PROMPT, toolsets=self.hub.toolsets
                )
                async with agent.run_stream(message, event_stream_handler=on_events) as result:
                    for item in drain():
                        yield item
                    async for delta in result.stream_text(delta=True):
                        for item in drain():
                            yield item
                        yield TextDelta(delta)
                    for item in drain():
                        yield item
                    tokens = result.usage.total_tokens or None
            status = "ok"
        except (asyncio.CancelledError, GeneratorExit):
            status = "aborted"
            raise
        except Exception as exc:
            log.warning(
                "chat_failed", channel=channel, model=self.model_name, error=type(exc).__name__
            )
            raise _translate(exc, self.model_name, self._base_url) from None
        finally:
            current_channel.reset(channel_token)
            self._audit.record(channel=channel, model=self.model_name, tokens=tokens, status=status)
            log.info("chat_done", channel=channel, model=self.model_name, status=status)


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
