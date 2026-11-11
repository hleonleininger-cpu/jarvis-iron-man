"""FastAPI-Server: GET /health, POST /v1/chat (Server-Sent Events), Sitzungen, Bestätigungen."""

import asyncio
import json
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import suppress

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator
from pydantic_ai.models import Model
from starlette.middleware.trustedhost import TrustedHostMiddleware

from jarvis_core import __version__
from jarvis_core.agent import (
    SESSION_ID_PATTERN,
    ChatError,
    ChatService,
    ConfirmRequested,
    Notice,
    TextDelta,
    ToolUsed,
    build_service,
    is_valid_session_id,
)
from jarvis_core.audit import AuditLog
from jarvis_core.config import Settings


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=20_000)
    # Ohne Angabe beginnt ein neues Gespräch; die ID steht im `done`-Ereignis.
    session_id: str | None = Field(default=None, pattern=SESSION_ID_PATTERN)

    @field_validator("message")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Die Nachricht ist leer.")
        return value


class ConfirmAnswer(BaseModel):
    approve: bool


# Lebenszeichen während Funkstille (z. B. Rückfrage offen): Clients und Proxys trennen sonst
# leerlaufende Verbindungen (iOS nach 60 s), und die Antwort ginge verloren.
KEEPALIVE_SECONDS = 15.0
KEEPALIVE = ": ping\n\n"

LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1")

# Ereignisse sofort und unverändert ausliefern: kein Zwischenspeichern, kein Proxy-Puffer.
SSE_HEADERS = {"Cache-Control": "no-store", "X-Accel-Buffering": "no"}


async def with_keepalive[T](source: AsyncGenerator[T], interval: float) -> AsyncGenerator[T | None]:
    """Reicht Ereignisse durch; nach `interval` Sekunden ohne Ereignis kommt None (Lebenszeichen).

    Das Warten auf das nächste Ereignis wird dabei nie abgebrochen, nur nicht länger blockiert.
    """
    pending: asyncio.Task[T] | None = None
    try:
        while True:
            if pending is None:
                pending = asyncio.ensure_future(anext(source))
            done, _ = await asyncio.wait({pending}, timeout=interval)
            if not done:
                yield None
                continue
            finished, pending = pending, None
            try:
                item = finished.result()
            except StopAsyncIteration:
                return
            yield item
    finally:
        if pending is not None and not pending.done():
            pending.cancel()
            with suppress(asyncio.CancelledError, StopAsyncIteration, Exception):
                await pending
        await source.aclose()


def _sse(event: str, data: dict[str, object]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def create_app(
    settings: Settings,
    model: Model | None = None,
    model_name: str | None = None,
    keepalive_seconds: float = KEEPALIVE_SECONDS,
) -> FastAPI:
    audit = AuditLog(settings.audit_db_path)
    service: ChatService = build_service(settings, audit, model=model, model_name=model_name)
    app = FastAPI(title="JARVIS Core", version=__version__)
    # Nur Anfragen an die eigene Adresse annehmen: sonst könnte eine fremde Webseite per
    # DNS-Rebinding im Browser des Nutzers mit dem Core sprechen (ADR-003).
    app.add_middleware(
        TrustedHostMiddleware, allowed_hosts=sorted({*LOCAL_HOSTS, settings.host.strip("[]")})
    )

    @app.get("/health")
    async def health() -> dict[str, str]:  # pyright: ignore[reportUnusedFunction]
        return {"status": "ok", "version": __version__, "model": service.model_name}

    @app.post("/v1/chat")
    async def chat(request: ChatRequest) -> StreamingResponse:  # pyright: ignore[reportUnusedFunction]
        session_id = request.session_id or service.sessions.new_id()

        async def events() -> AsyncIterator[str]:
            stream = service.stream(request.message, channel="api", session_id=session_id)
            try:
                async for event in with_keepalive(stream, keepalive_seconds):
                    match event:
                        case None:
                            yield KEEPALIVE
                        case TextDelta(text):
                            yield _sse("delta", {"text": text})
                        case ToolUsed(name):
                            yield _sse("tool", {"name": name})
                        case Notice(message):
                            yield _sse("notice", {"message": message})
                        case ConfirmRequested(request_id, tool, preview, question):
                            yield _sse(
                                "confirm",
                                {"id": request_id, "tool": tool, "preview": preview,
                                 "question": question},
                            )  # fmt: skip
            except ChatError as exc:
                yield _sse("error", {"message": str(exc)})
                return
            yield _sse("done", {"model": service.model_name, "session_id": session_id})

        return StreamingResponse(events(), media_type="text/event-stream", headers=SSE_HEADERS)

    @app.post("/v1/confirm/{request_id}")
    async def confirm(request_id: str, answer: ConfirmAnswer) -> dict[str, str]:  # pyright: ignore[reportUnusedFunction]
        if not service.broker.resolve(request_id, answer.approve):
            raise HTTPException(status_code=404, detail="Unbekannte oder bereits beantwortete ID.")
        return {"status": "ok"}

    @app.delete("/v1/sessions/{session_id}")
    async def forget_session(session_id: str) -> dict[str, str]:  # pyright: ignore[reportUnusedFunction]
        if not is_valid_session_id(session_id) or not service.sessions.clear(session_id):
            raise HTTPException(status_code=404, detail="Unbekannte Sitzung.")
        return {"status": "ok"}

    return app
