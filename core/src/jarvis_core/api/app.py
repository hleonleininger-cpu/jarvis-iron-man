"""FastAPI-Server: GET /health, POST /v1/chat (Server-Sent Events)."""

import json
from collections.abc import AsyncIterator

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from pydantic_ai.models import Model

from jarvis_core import __version__
from jarvis_core.agent import (
    ChatError,
    ChatService,
    ConfirmRequested,
    Notice,
    TextDelta,
    ToolUsed,
    build_service,
)
from jarvis_core.audit import AuditLog
from jarvis_core.config import Settings


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=20_000)


class ConfirmAnswer(BaseModel):
    approve: bool


def _sse(event: str, data: dict[str, object]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def create_app(
    settings: Settings, model: Model | None = None, model_name: str | None = None
) -> FastAPI:
    audit = AuditLog(settings.audit_db_path)
    service: ChatService = build_service(settings, audit, model=model, model_name=model_name)
    app = FastAPI(title="JARVIS Core", version=__version__)

    @app.get("/health")
    async def health() -> dict[str, str]:  # pyright: ignore[reportUnusedFunction]
        return {"status": "ok", "version": __version__, "model": service.model_name}

    @app.post("/v1/chat")
    async def chat(request: ChatRequest) -> StreamingResponse:  # pyright: ignore[reportUnusedFunction]
        async def events() -> AsyncIterator[str]:
            try:
                async for event in service.stream(request.message, channel="api"):
                    match event:
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
            yield _sse("done", {"model": service.model_name})

        return StreamingResponse(events(), media_type="text/event-stream")

    @app.post("/v1/confirm/{request_id}")
    async def confirm(request_id: str, answer: ConfirmAnswer) -> dict[str, str]:  # pyright: ignore[reportUnusedFunction]
        if not service.broker.resolve(request_id, answer.approve):
            raise HTTPException(status_code=404, detail="Unbekannte oder bereits beantwortete ID.")
        return {"status": "ok"}

    return app
