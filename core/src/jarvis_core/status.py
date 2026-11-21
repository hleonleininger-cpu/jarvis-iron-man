"""Status-Ereignisse für das HUD (M3, ADR-024): was JARVIS gerade tut, nie womit.

Ein Ereignis trägt nur Art, Werkzeugname, Risikoklasse, Dauer und Ergebnis, nie Nachrichtentext
oder Werkzeugergebnisse (dieselbe Regel wie beim Audit). Das ist hier fest eingebaut: jede Art
hat eine feste Liste erlaubter Felder, Texte sind kurz und Zahlen nur Zahlen. Wer mehr schicken
will, bekommt einen Fehler, statt dass Inhalte unbemerkt durchrutschen.

Der Bus hält nur den letzten Stand je Zustand im Arbeitsspeicher (für den `snapshot` beim
Verbinden), sonst nichts. Er hängt an nichts aus den oberen Schichten, damit `mcp_hub` und
`agent` ihn nutzen dürfen.
"""

import asyncio
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Final

type Value = str | int | float | bool | None

# Erlaubte Felder je Ereignisart. Neue Arten brauchen hier einen Eintrag (und einen Test).
FIELDS: Final[dict[str, frozenset[str]]] = {
    "state": frozenset({"state"}),  # Zustand des Sprachclients
    "mic": frozenset({"on"}),  # Mikrofon offen oder zu
    "level": frozenset({"level", "source"}),  # Pegel 0..7, Quelle "mic" oder "speaker"
    "heartbeat": frozenset(),  # Lebenszeichen des Sprachclients
    "thinking": frozenset({"active", "run"}),  # Agent arbeitet an Anfrage `run`
    "tool_start": frozenset({"call", "tool", "risk"}),
    "tool_end": frozenset({"call", "tool", "risk", "status", "ms"}),
    "stop": frozenset({"count"}),  # Not-Aus ausgelöst
    "screen_read": frozenset({"on"}),  # Bildschirm wird gelesen (Vertrag für M4.6/M4.8)
    "profile": frozenset({"name"}),  # Hardware-Profil S/M/L (z. B. 3D-Ansicht nur ab M)
}
# Arten, deren letzter Stand im Snapshot steht.
SNAPSHOT_KINDS: Final = ("state", "mic", "thinking", "screen_read", "profile")
# Bei vollem Puffer werden zuerst diese Arten verworfen (häufig und ohne bleibende Wirkung).
DROPPABLE: Final = frozenset({"level", "heartbeat"})
MAX_TEXT = 64  # Zustands- und Werkzeugnamen sind kurz; längerer Text wäre ein Inhalt
MAX_QUEUE = 256  # langsamer Zuhörer: alte Ereignisse fallen weg, der Core wartet nie


class StatusError(ValueError):
    """Ereignis mit unbekannter Art, unerlaubtem Feld oder zu langem Text."""


@dataclass(frozen=True)
class StatusEvent:
    kind: str
    data: dict[str, Value] = field(default_factory=dict[str, Value])


def _check(kind: str, data: dict[str, object]) -> None:
    """Prüft zur Laufzeit; Aufrufer können sich nicht auf Typprüfung allein verlassen."""
    allowed = FIELDS.get(kind)
    if allowed is None:
        raise StatusError(f"Unbekannte Status-Art: {kind!r}")
    extra = set(data) - allowed
    if extra:
        raise StatusError(f"Unerlaubte Felder für {kind!r}: {sorted(extra)}")
    for key, value in data.items():
        if isinstance(value, str) and len(value) > MAX_TEXT:
            raise StatusError(f"Feld {key!r} ist zu lang für ein Status-Ereignis.")
        if not isinstance(value, str | int | float | bool | None):
            raise StatusError(f"Feld {key!r} hat keinen einfachen Wert.")


class StatusBus:
    def __init__(self) -> None:
        self._listeners: list[asyncio.Queue[StatusEvent]] = []
        self._last: dict[str, StatusEvent] = {}

    def publish(self, kind: str, **data: Value) -> None:
        """Verteilt ein Ereignis an alle Zuhörer. Wartet nie und wirft nie wegen Zuhörern."""
        _check(kind, dict(data))
        event = StatusEvent(kind, data)
        if kind in SNAPSHOT_KINDS:
            self._last[kind] = event
        for queue in self._listeners:
            if queue.full():  # ein hängender Client bremst den Core nicht aus
                if kind in DROPPABLE:
                    continue  # Pegel und Lebenszeichen sind entbehrlich, Zustände nicht
                queue.get_nowait()
            queue.put_nowait(event)

    @contextmanager
    def listen(self) -> Generator[asyncio.Queue[StatusEvent]]:
        queue: asyncio.Queue[StatusEvent] = asyncio.Queue(MAX_QUEUE)
        self._listeners.append(queue)
        try:
            yield queue
        finally:
            self._listeners.remove(queue)

    def snapshot(self) -> dict[str, dict[str, Value]]:
        """Letzter Stand je Zustand, z. B. {"state": {"state": "WARTET"}}."""
        return {kind: dict(event.data) for kind, event in self._last.items()}
