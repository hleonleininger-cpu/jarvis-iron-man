"""Allgemeine Bestätigung für CONFIRM-Werkzeuge: Anfrage -> Nutzer sagt Ja/Nein -> Entscheidung.

Nicht kalenderspezifisch: jedes Werkzeug, für das die Policy CONFIRM liefert, nutzt diesen Fluss.
CLI und API beantworten Anfragen über `resolve()`; ohne Antwort gilt nach dem Timeout "Nein".
Die CLI wartet ohne Timeout: dort sitzt der Nutzer vor der Rückfrage und `input()` blockiert
ohnehin, eine späte Antwort darf nicht stillschweigend verworfen werden.
"""

import asyncio
import uuid
from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass

DEFAULT_TIMEOUT_SECONDS = 120.0


@dataclass(frozen=True)
class ConfirmRequest:
    id: str
    tool: str
    preview: str
    question: str
    risk: str = ""  # Risikoklasse des Werkzeugs; leer = unbekannt (dann nie per Stimme bestätigen)


@dataclass(frozen=True)
class ConfirmDone:
    """Rückfrage erledigt (Antwort, Zeitablauf, Not-Aus); `approved` None = Zeitablauf."""

    id: str
    approved: bool | None


class ConfirmationBroker:
    def __init__(self, timeout: float | None = DEFAULT_TIMEOUT_SECONDS) -> None:
        self.timeout = timeout
        self._pending: dict[str, asyncio.Future[bool]] = {}
        self._requests: dict[str, ConfirmRequest] = {}
        # Zuhörer (Stream /v1/events): neue und erledigte Rückfragen, auch von anderen Geräten.
        self._listeners: list[asyncio.Queue[ConfirmRequest | ConfirmDone]] = []

    @contextmanager
    def listen(self) -> Generator[asyncio.Queue[ConfirmRequest | ConfirmDone]]:
        queue: asyncio.Queue[ConfirmRequest | ConfirmDone] = asyncio.Queue()
        self._listeners.append(queue)
        try:
            yield queue
        finally:
            self._listeners.remove(queue)

    def open_requests(self) -> list[ConfirmRequest]:
        """Gerade offene Rückfragen (für Clients, die sich erst jetzt verbinden)."""
        return list(self._requests.values())

    def risk_of(self, request_id: str) -> str | None:
        """Risikoklasse einer offenen Rückfrage; None, wenn es sie nicht (mehr) gibt."""
        request = self._requests.get(request_id)
        return None if request is None else request.risk

    def _publish(self, item: ConfirmRequest | ConfirmDone) -> None:
        for queue in self._listeners:
            queue.put_nowait(item)

    async def ask(
        self,
        tool: str,
        preview: str,
        question: str,
        emit: Callable[[ConfirmRequest], None],
        risk: str = "",
    ) -> bool | None:
        """Ja/Nein des Nutzers; None, wenn bis zum Timeout keine Antwort kam."""
        request = ConfirmRequest(uuid.uuid4().hex, tool, preview, question, risk)
        future: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
        self._pending[request.id] = future
        self._requests[request.id] = request
        answer: bool | None = None
        try:
            emit(request)
            self._publish(request)
            answer = await asyncio.wait_for(future, self.timeout)
            return answer
        except TimeoutError:
            return None
        finally:
            self._pending.pop(request.id, None)
            self._requests.pop(request.id, None)
            self._publish(ConfirmDone(request.id, answer))

    def resolve(self, request_id: str, approve: bool) -> bool:
        """Beantwortet eine Anfrage (threadsicher). False, wenn unbekannt oder schon beantwortet."""
        future = self._pending.pop(request_id, None)
        if future is None or future.done():
            return False
        future.get_loop().call_soon_threadsafe(_set_result, future, approve)
        return True

    def reject_all(self) -> int:
        """Not-Aus: beantwortet alle offenen Rückfragen mit Nein. Gibt deren Anzahl zurück."""
        pending = list(self._pending)
        return sum(self.resolve(request_id, False) for request_id in pending)


def _set_result(future: "asyncio.Future[bool]", value: bool) -> None:
    if not future.done():
        future.set_result(value)
