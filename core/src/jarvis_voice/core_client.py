"""Der Sprachclient spricht ausschließlich über die HTTP-Schnittstelle mit dem Core: /v1/chat als
SSE-Strom, /v1/confirm/{id} und /v1/stop. Nie direkt mit Ollama oder dem Netz."""

import contextlib
import json
from collections.abc import AsyncIterator

import httpx2

from jarvis_voice.protocols import (
    CoreConfirm,
    CoreDelta,
    CoreDone,
    CoreError,
    CoreEvent,
    CoreNotice,
)

TIMEOUT_SECONDS = 300.0


def parse_sse(lines: list[str]) -> tuple[str, dict[str, object]] | None:
    """Ein SSE-Block (Zeilen "event: ..." und "data: ...") -> (Name, Daten); Lebenszeichen None."""
    name = ""
    data = ""
    for line in lines:
        if line.startswith("event: "):
            name = line[7:]
        elif line.startswith("data: "):
            data = line[6:]
    if not name or not data:
        return None
    try:
        loaded = json.loads(data)
    except ValueError:
        return None
    return (name, loaded) if isinstance(loaded, dict) else None  # pyright: ignore[reportUnknownVariableType]


def to_event(name: str, data: dict[str, object]) -> CoreEvent | None:
    """Übersetzt ein SSE-Ereignis. Werkzeug- und Fortschrittsmeldungen werden nicht vorgelesen."""
    if name == "delta":
        return CoreDelta(str(data.get("text", "")))
    if name == "confirm":
        return CoreConfirm(
            str(data.get("id", "")), str(data.get("tool", "")), str(data.get("preview", "")),
            str(data.get("question", "")), str(data.get("risk", "")),
        )  # fmt: skip
    if name == "notice":
        return CoreNotice(str(data.get("message", "")))
    if name == "done":
        return CoreDone(str(data.get("session_id", "")))
    if name == "error":
        return CoreError(str(data.get("message", "Fehler beim Core.")))
    return None


class HttpCoreClient:
    def __init__(self, base_url: str, token: str | None = None) -> None:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        # trust_env=False: Gespräche gehen nie über einen System-Proxy.
        self._http = httpx2.AsyncClient(
            base_url=base_url, headers=headers, trust_env=False,
            timeout=httpx2.Timeout(TIMEOUT_SECONDS, connect=5.0),
        )  # fmt: skip

    async def ask(self, text: str, session_id: str | None) -> AsyncIterator[CoreEvent]:
        body: dict[str, object] = {"message": text, "voice": True}
        if session_id:
            body["session_id"] = session_id
        try:
            async with self._http.stream("POST", "/v1/chat", json=body) as response:
                if response.status_code != 200:
                    yield CoreError(f"Der Core antwortete mit HTTP {response.status_code}.")
                    return
                block: list[str] = []
                async for line in response.aiter_lines():
                    if line:
                        block.append(line)
                        continue
                    parsed = parse_sse(block)
                    block = []
                    event = to_event(*parsed) if parsed else None
                    if event is not None:
                        yield event
        except httpx2.HTTPError:
            yield CoreError("Der Core ist nicht erreichbar.")

    async def confirm(self, request_id: str, approve: bool, by_voice: bool) -> bool:
        body: dict[str, object] = {"approve": approve}
        if by_voice:
            body["via"] = "voice"
        try:
            response = await self._http.post(f"/v1/confirm/{request_id}", json=body)
        except httpx2.HTTPError:
            return False
        return response.status_code == 200

    async def status(self, event: str, **fields: str | bool | int) -> None:
        """Meldet dem Core Zustand, Mikrofon oder Lebenszeichen fürs HUD (M3, ohne Inhalte).
        Kurze Frist: ein hängender Core darf das Zuhören nicht aufhalten."""
        with contextlib.suppress(httpx2.HTTPError):
            await self._http.post("/v1/voice/event", json={"event": event, **fields}, timeout=2.0)

    async def voice_event(self, event: str) -> None:
        """Meldet dem Core ein Ereignis ohne Inhalt fürs Audit (dictation_start/dictation_end)."""
        with contextlib.suppress(httpx2.HTTPError):
            await self._http.post("/v1/voice/event", json={"event": event})

    async def stop(self) -> None:
        with contextlib.suppress(httpx2.HTTPError):  # Core hängt oder ist weg: `jarvis stop` hilft
            await self._http.post("/v1/stop", timeout=5.0)

    async def aclose(self) -> None:
        await self._http.aclose()
