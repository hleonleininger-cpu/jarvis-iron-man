"""FastAPI-Server: Web-Client (/), GET /health, POST /v1/chat (Server-Sent Events), Sitzungen,
Bestätigungen. /v1/* verlangt ein Token, sobald der Server nicht nur lokal lauscht (ADR-011)."""

import asyncio
import hmac
import ipaddress
import json
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import Response, StreamingResponse
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
    Reminder,
    ReminderWatcher,
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


WEB_DIR = Path(__file__).parent / "web"
# Pfad -> (Datei, Medientyp). Der Client lädt nichts von außen (kein CDN, keine Schriften).
WEB_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/app.css": ("app.css", "text/css; charset=utf-8"),
}
WEB_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; "
        "img-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-cache",
}


def token_required(settings: Settings) -> bool:
    """Ein Token ist Pflicht, sobald der Server nicht nur lokal lauscht (Tailnet) oder es so
    konfiguriert ist."""
    if settings.require_api_token is not None:
        return settings.require_api_token
    return not is_loopback(settings.host)


def allowed_hosts(host: str) -> list[str]:
    """Host-Header, unter denen der Core antwortet. An einer Tailnet-Adresse gebunden, kommt das
    iPhone meist über den MagicDNS-Namen (pc.tailXXXX.ts.net); diese Namen vergibt nur Tailscale."""
    names = {*LOCAL_HOSTS, host.strip("[]")}
    if not is_loopback(host):
        names.add("*.ts.net")
    return sorted(names)


def is_loopback(host: str) -> bool:
    try:
        return ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        return host == "localhost"


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
    api_token: str | None = None,
    reminder_poll_seconds: float | None = None,
) -> FastAPI:
    """`api_token`: erwartetes Token für /v1/*; None = ohne Token (nur lokal zulässig)."""
    if token_required(settings) and not api_token:
        raise ValueError("Für diesen Server ist ein Zugangstoken nötig (ADR-011).")
    audit = AuditLog(settings.audit_db_path)
    service: ChatService = build_service(settings, audit, model=model, model_name=model_name)
    watcher = ReminderWatcher(
        settings.mcp_servers, audit, log_dir=settings.data_dir / "logs",
        **({"poll_seconds": reminder_poll_seconds} if reminder_poll_seconds else {}),
    )  # fmt: skip

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncGenerator[None]:
        task = asyncio.create_task(watcher.run()) if watcher.enabled else None
        try:
            yield
        finally:
            if task is not None:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task

    app = FastAPI(title="JARVIS Core", version=__version__, lifespan=lifespan)
    # Nur Anfragen an die eigene Adresse annehmen: sonst könnte eine fremde Webseite per
    # DNS-Rebinding im Browser des Nutzers mit dem Core sprechen (ADR-003).
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts(settings.host))
    expected = api_token.encode() if api_token else None

    async def authorized(authorization: str | None = Header(default=None)) -> None:
        if expected is None:
            return
        scheme, _, value = (authorization or "").strip().partition(" ")
        given = value.strip().encode() if scheme.lower() == "bearer" else b"\0"
        if not hmac.compare_digest(given, expected):
            raise HTTPException(
                status_code=401,
                detail="Zugangstoken fehlt oder ist falsch.",
                headers={"WWW-Authenticate": "Bearer"},
            )

    guarded = [Depends(authorized)]

    for route, (name, media_type) in WEB_FILES.items():
        content = (WEB_DIR / name).read_bytes()

        async def web_file(content: bytes = content, media_type: str = media_type) -> Response:
            return Response(content, media_type=media_type, headers=WEB_HEADERS)

        app.add_api_route(route, web_file, methods=["GET"], include_in_schema=False)

    @app.get("/health")
    async def health() -> dict[str, str]:  # pyright: ignore[reportUnusedFunction]
        return {"status": "ok", "version": __version__, "model": service.model_name}

    @app.post("/v1/chat", dependencies=guarded)
    async def chat(request: ChatRequest) -> StreamingResponse:  # pyright: ignore[reportUnusedFunction]
        session_id = request.session_id or service.sessions.new_id()
        # Gab es zu dieser ID keinen Verlauf (neu, abgelaufen, Server neu gestartet)? Der Client
        # erfährt es im `done`-Ereignis und kann sagen, dass JARVIS das Gespräch nicht mehr kennt.
        fresh = not service.sessions.history(session_id)

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
            yield _sse(
                "done", {"model": service.model_name, "session_id": session_id, "neu": fresh}
            )

        return StreamingResponse(events(), media_type="text/event-stream", headers=SSE_HEADERS)

    @app.post("/v1/confirm/{request_id}", dependencies=guarded)
    async def confirm(request_id: str, answer: ConfirmAnswer) -> dict[str, str]:  # pyright: ignore[reportUnusedFunction]
        if not service.broker.resolve(request_id, answer.approve):
            raise HTTPException(status_code=404, detail="Unbekannte oder bereits beantwortete ID.")
        return {"status": "ok"}

    @app.get("/v1/events", dependencies=guarded)
    async def events_stream() -> StreamingResponse:  # pyright: ignore[reportUnusedFunction]
        """Dauerhafter Stream für Meldungen ohne Anfrage (Erinnerungen, ADR-013)."""

        async def reminders(queue: asyncio.Queue[Reminder]) -> AsyncGenerator[Reminder]:
            while True:
                yield await queue.get()

        async def events() -> AsyncIterator[str]:
            yield _sse("ready", {"erinnerungen": watcher.enabled})
            with watcher.listen() as queue:
                async for item in with_keepalive(reminders(queue), keepalive_seconds):
                    if item is None:
                        yield KEEPALIVE
                    else:
                        yield _sse(
                            "reminder", {"id": item.id, "text": item.text, "zeitpunkt": item.when}
                        )

        return StreamingResponse(events(), media_type="text/event-stream", headers=SSE_HEADERS)

    @app.delete("/v1/sessions/{session_id}", dependencies=guarded)
    async def forget_session(session_id: str) -> dict[str, str]:  # pyright: ignore[reportUnusedFunction]
        if not is_valid_session_id(session_id) or not service.sessions.clear(session_id):
            raise HTTPException(status_code=404, detail="Unbekannte Sitzung.")
        return {"status": "ok"}

    return app
