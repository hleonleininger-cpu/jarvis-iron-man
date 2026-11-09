"""FastAPI-Server: GET /health, POST /v1/chat (Server-Sent Events)."""

import json
from collections.abc import AsyncIterator

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from pydantic_ai.models import Model

from jarvis_core import __version__
from jarvis_core.agent import ChatError, ChatService, build_service
from jarvis_core.audit import AuditLog
from jarvis_core.config import Settings


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=20_000)


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
                async for delta in service.stream(request.message, channel="api"):
                    yield _sse("delta", {"text": delta})
            except ChatError as exc:
                yield _sse("error", {"message": str(exc)})
                return
            yield _sse("done", {"model": service.model_name})

        return StreamingResponse(events(), media_type="text/event-stream")

    return app
