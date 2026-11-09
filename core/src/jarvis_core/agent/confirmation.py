"""Allgemeine Bestätigung für CONFIRM-Werkzeuge: Anfrage -> Nutzer sagt Ja/Nein -> Entscheidung.

Nicht kalenderspezifisch: jedes Werkzeug, für das die Policy CONFIRM liefert, nutzt diesen Fluss.
CLI und API beantworten Anfragen über `resolve()`; ohne Antwort gilt nach dem Timeout "Nein".
"""

import asyncio
import uuid
from collections.abc import Callable
from dataclasses import dataclass

DEFAULT_TIMEOUT_SECONDS = 120.0


@dataclass(frozen=True)
class ConfirmRequest:
    id: str
    tool: str
    preview: str
    question: str


class ConfirmationBroker:
    def __init__(self, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> None:
        self._timeout = timeout
        self._pending: dict[str, asyncio.Future[bool]] = {}

    async def ask(
        self, tool: str, preview: str, question: str, emit: Callable[[ConfirmRequest], None]
    ) -> bool:
        request = ConfirmRequest(uuid.uuid4().hex, tool, preview, question)
        future: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
        self._pending[request.id] = future
        try:
            emit(request)
            return await asyncio.wait_for(future, self._timeout)
        except TimeoutError:
            return False
        finally:
            self._pending.pop(request.id, None)

    def resolve(self, request_id: str, approve: bool) -> bool:
        """Beantwortet eine Anfrage (threadsicher). False, wenn unbekannt oder schon beantwortet."""
        future = self._pending.pop(request_id, None)
        if future is None or future.done():
            return False
        future.get_loop().call_soon_threadsafe(_set_result, future, approve)
        return True


def _set_result(future: "asyncio.Future[bool]", value: bool) -> None:
    if not future.done():
        future.set_result(value)
