"""Chat-Dienst: Pydantic AI + Ollama (OpenAI-kompatibel), mit Audit pro Anfrage."""

import asyncio
import os
from collections.abc import AsyncGenerator

import structlog
from pydantic_ai import Agent
from pydantic_ai.exceptions import ModelAPIError, ModelHTTPError
from pydantic_ai.models import Model
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from jarvis_core.audit import AuditLog
from jarvis_core.config import Settings
from jarvis_core.hardware import detect_hardware, recommend

# Kein Werbebanner von Pydantic AI auf stdout/stderr.
os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")

SYSTEM_PROMPT = (
    "Du bist JARVIS, der persönliche KI-Assistent deines Nutzers. "
    "Antworte immer auf Deutsch, knapp und sachlich."
)

log = structlog.get_logger("jarvis.chat")


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
    def __init__(self, model: Model, model_name: str, base_url: str, audit: AuditLog) -> None:
        self.model_name = model_name
        self._base_url = base_url
        self._audit = audit
        self._agent: Agent[None, str] = Agent(model, system_prompt=SYSTEM_PROMPT)

    async def stream(self, message: str, *, channel: str) -> AsyncGenerator[str]:
        """Liefert Textteile. Schreibt genau einen Audit-Eintrag (ohne Inhalte)."""
        status = "error"
        tokens: int | None = None
        try:
            async with self._agent.run_stream(message) as result:
                async for delta in result.stream_text(delta=True):
                    yield delta
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
            self._audit.record(channel=channel, model=self.model_name, tokens=tokens, status=status)
            log.info("chat_done", channel=channel, model=self.model_name, status=status)


def build_service(
    settings: Settings, audit: AuditLog, model: Model | None = None, model_name: str | None = None
) -> ChatService:
    name = model_name or resolve_model_name(settings)
    if model is None:
        provider = OpenAIProvider(base_url=settings.ollama_base_url, api_key="ollama")
        model = OpenAIChatModel(name, provider=provider)
    return ChatService(model, name, settings.ollama_base_url, audit)
