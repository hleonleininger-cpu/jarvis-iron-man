"""Werkzeugauswahl per Embedding (M1.4): je Anfrage nur die passendsten Werkzeuge anbieten.

Viele Werkzeuge füllen das kleine Kontextfenster lokaler Modelle und verwirren sie. Die Auswahl
betrifft nur, was dem Modell angeboten wird; Policy, Bestätigung und Audit je Aufruf bleiben im
Hub unverändert. Jede Störung (kein Embedding-Modell, Fehler) führt zum Ausweichweg: alle
Werkzeuge, so wie ohne Auswahl.
"""

import asyncio
import math
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass

import structlog

from jarvis_core.mcp_hub.hub import ToolInfo

log = structlog.get_logger("jarvis.select")

# Text -> Vektor (blockierend, wie `mcp_servers.memory.embed.make_embedder`).
Embedder = Callable[[list[str]], list[list[float]]]

# Nach einem Fehler des Embedding-Modells so lange nicht erneut versuchen, damit eine
# ausgefallene Ollama-Verbindung nicht jede Anfrage um die Zeitüberschreitung verzögert.
COOLDOWN_SECONDS = 60.0


@dataclass(frozen=True)
class Selection:
    names: frozenset[str]  # die angebotenen Werkzeuge
    best: float  # größte Ähnlichkeit zwischen Anfrage und einem Werkzeug (für den Router)


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return sum(x * y for x, y in zip(a, b, strict=False)) / norm if norm else 0.0


class ToolSelector:
    """Bewertet Werkzeuge gegen die Anfrage. `limit` 0 = alle behalten (nur bewerten)."""

    def __init__(
        self,
        embedder: Embedder,
        limit: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._embedder = embedder
        self._limit = limit
        self._clock = clock
        self._cache: dict[tuple[str, str], list[float]] = {}  # (Name, Beschreibung) -> Vektor
        self._blocked_until = 0.0

    async def choose(self, infos: Sequence[ToolInfo], query: str) -> Selection | None:
        """Die passendsten Werkzeuge, oder None (alles anbieten), wenn nichts bewertbar ist."""
        if not infos or not query.strip() or self._clock() < self._blocked_until:
            return None
        try:
            return await asyncio.to_thread(self._rank, infos, query)
        except Exception as exc:  # Embedding-Modell weg oder unbrauchbar: nicht die Anfrage kippen
            self._blocked_until = self._clock() + COOLDOWN_SECONDS
            log.info("tool_select_failed", error=type(exc).__name__)
            return None

    def _rank(self, infos: Sequence[ToolInfo], query: str) -> Selection:
        missing = [i for i in infos if (i.name, i.description) not in self._cache]
        if missing:
            vectors = self._embedder([f"{i.name}: {i.description}" for i in missing])
            for info, vector in zip(missing, vectors, strict=True):
                self._cache[(info.name, info.description)] = vector
        (query_vector,) = self._embedder([query])
        scored = sorted(
            ((cosine(query_vector, self._cache[(i.name, i.description)]), i.name) for i in infos),
            key=lambda pair: (-pair[0], pair[1]),
        )
        keep = scored if self._limit <= 0 else scored[: self._limit]
        return Selection(frozenset(name for _, name in keep), scored[0][0])
