"""Gesprächsverlauf je Sitzung, ausschließlich im Arbeitsspeicher (ADR-009).

Eine Sitzung ist eine Folge abgeschlossener Runden (Frage, Werkzeugaufrufe, Antwort). Es wird
nichts auf die Platte geschrieben; mit dem Prozess endet der Verlauf. Begrenzt wird dreifach:
Runden je Sitzung, Zeichen je Sitzung (lokale Modelle haben ein kleines Kontextfenster) und
Anzahl der Sitzungen (die am längsten ungenutzte fällt weg).
"""

import json
import re
import time
import uuid
from collections import OrderedDict
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from pydantic_ai.messages import ModelMessage

DEFAULT_MAX_TURNS = 10
DEFAULT_MAX_CHARS = 16_000
MAX_SESSIONS = 32
SESSION_TTL_SECONDS = 6 * 60 * 60.0
# Auch von der API geprüft: Sitzungs-IDs dürfen vom Client gewählt werden.
SESSION_ID_PATTERN = r"^[A-Za-z0-9_-]{1,64}$"
_SESSION_ID_RE = re.compile(SESSION_ID_PATTERN)


def is_valid_session_id(value: str) -> bool:
    return _SESSION_ID_RE.fullmatch(value) is not None


def _chars(messages: Sequence[ModelMessage]) -> int:
    """Grobe Größe einer Runde: Texte, Werkzeugargumente und -ergebnisse (ohne Metadaten)."""
    total = 0
    for message in messages:
        for part in message.parts:
            for value in (getattr(part, "content", None), getattr(part, "args", None)):
                if isinstance(value, str):
                    total += len(value)
                elif value is not None:
                    total += len(json.dumps(value, ensure_ascii=False, default=str))
    return total


@dataclass
class _Turn:
    messages: list[ModelMessage]
    chars: int


@dataclass
class _Session:
    last_used: float
    turns: list[_Turn] = field(default_factory=lambda: [])


class SessionStore:
    def __init__(
        self,
        max_turns: int = DEFAULT_MAX_TURNS,
        max_chars: int = DEFAULT_MAX_CHARS,
        max_sessions: int = MAX_SESSIONS,
        ttl_seconds: float = SESSION_TTL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._max_turns = max_turns
        self._max_chars = max_chars
        self._max_sessions = max_sessions
        self._ttl = ttl_seconds
        self._clock = clock
        self._sessions: OrderedDict[str, _Session] = OrderedDict()

    @staticmethod
    def new_id() -> str:
        return uuid.uuid4().hex

    def __len__(self) -> int:
        self._expire()
        return len(self._sessions)

    def history(self, session_id: str, max_chars: int | None = None) -> list[ModelMessage]:
        """Bisherige Nachrichten der Sitzung (leer, wenn unbekannt oder abgelaufen).

        Mit `max_chars` nur die jüngsten ganzen Runden, die zusammen hineinpassen (das
        Kontextfenster des Modells ist knapp; ältere Runden bleiben gespeichert).
        """
        self._expire()
        session = self._sessions.get(session_id)
        if session is None:
            return []
        self._touch(session_id, session)
        turns = session.turns
        if max_chars is not None:
            kept: list[_Turn] = []
            used = 0
            for turn in reversed(turns):
                if used + turn.chars > max_chars:
                    break
                kept.append(turn)
                used += turn.chars
            turns = kept[::-1]
        return [message for turn in turns for message in turn.messages]

    def append(self, session_id: str, messages: Sequence[ModelMessage]) -> None:
        """Hängt eine abgeschlossene Runde an und kürzt den Verlauf von vorn."""
        if self._max_turns <= 0 or not messages:
            return
        self._expire()
        session = self._sessions.get(session_id)
        if session is None:
            session = self._sessions[session_id] = _Session(self._clock())
        self._touch(session_id, session)
        session.turns.append(_Turn(list(messages), _chars(messages)))
        # Immer ganze Runden entfernen, damit Werkzeugaufruf und -ergebnis zusammenbleiben.
        while len(session.turns) > self._max_turns or (
            len(session.turns) > 1 and sum(t.chars for t in session.turns) > self._max_chars
        ):
            session.turns.pop(0)
        if session.turns[0].chars > self._max_chars:
            session.turns.clear()  # eine einzelne übergroße Runde nützt dem Modell nicht
        while len(self._sessions) > self._max_sessions:
            self._sessions.popitem(last=False)

    def clear(self, session_id: str) -> bool:
        """Vergisst die Sitzung. False, wenn es sie nicht (mehr) gab."""
        self._expire()
        return self._sessions.pop(session_id, None) is not None

    def _touch(self, session_id: str, session: _Session) -> None:
        session.last_used = self._clock()
        self._sessions.move_to_end(session_id)

    def _expire(self) -> None:
        deadline = self._clock() - self._ttl
        for session_id in [k for k, s in self._sessions.items() if s.last_used < deadline]:
            del self._sessions[session_id]
