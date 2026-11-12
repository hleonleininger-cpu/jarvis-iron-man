"""Erinnerungen zustellen (ADR-013): fragt die MCP-Server mit `reminders` regelmäßig nach fälligen
Erinnerungen und verteilt sie an alle, die gerade zuhören (Web-Oberfläche, CLI-Dialog).

Abgeholt wird nur, wenn mindestens jemand zuhört: das Abholen markiert eine Erinnerung als
zugestellt, ohne Zuhörer ginge sie verloren. Die Verbindung zu den Servern bleibt offen, solange
der Wächter läuft (eigener Hub, unabhängig von den Chat-Anfragen).
"""

import asyncio
import contextlib
from collections.abc import Generator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog

from jarvis_core.audit import AuditLog
from jarvis_core.config.settings import McpServerConfig
from jarvis_core.mcp_hub import McpHub
from jarvis_core.policy import RiskClass

log = structlog.get_logger("jarvis.reminders")

POLL_SECONDS = 20.0
RETRY_SECONDS = 60.0  # nach einem Fehler (Server abgestürzt, Datei gesperrt)
MAX_TEXT_LENGTH = 300


@dataclass(frozen=True)
class Reminder:
    id: int
    text: str
    when: str  # JJJJ-MM-TTTHH:MM, Europe/Berlin


def _as_dict(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    mapping: dict[str, Any] = value  # pyright: ignore[reportUnknownVariableType]
    return mapping


def parse_reminders(data: object) -> list[Reminder]:
    """Antwort des Abhol-Werkzeugs -> Erinnerungen; Unlesbares wird übersprungen."""
    raw_entries = _as_dict(data).get("erinnerungen")
    entries: list[object] = (
        list(raw_entries)  # pyright: ignore[reportUnknownArgumentType]
        if isinstance(raw_entries, list)
        else []
    )
    result: list[Reminder] = []
    for raw in entries:
        entry = _as_dict(raw)
        note_id, text, when = entry.get("id"), entry.get("text"), entry.get("zeitpunkt")
        if isinstance(note_id, int) and isinstance(text, str) and isinstance(when, str):
            result.append(Reminder(note_id, text[:MAX_TEXT_LENGTH], when))
    return result


def _delivered(data: object) -> bool:
    return bool(parse_reminders(data))


class ReminderWatcher:
    def __init__(
        self,
        servers: dict[str, McpServerConfig],
        audit: AuditLog,
        log_dir: Path | None = None,
        poll_seconds: float = POLL_SECONDS,
    ) -> None:
        self._servers = {name: cfg for name, cfg in servers.items() if cfg.reminders}
        self._audit = audit
        self._log_dir = log_dir
        self._poll = poll_seconds
        self._listeners: set[asyncio.Queue[Reminder]] = set()

    @property
    def enabled(self) -> bool:
        return bool(self._servers)

    @contextlib.contextmanager
    def listen(self) -> Generator[asyncio.Queue[Reminder]]:
        """Zuhören, solange der with-Block läuft; liefert eine Queue mit Erinnerungen."""
        queue: asyncio.Queue[Reminder] = asyncio.Queue()
        self._listeners.add(queue)
        try:
            yield queue
        finally:
            self._listeners.discard(queue)

    async def run(self) -> None:
        """Läuft bis zum Abbruch; Fehler führen zu einem neuen Versuch, nie zum Ende."""
        while self.enabled:
            try:
                async with McpHub(self._servers, self._audit, "-", log_dir=self._log_dir) as hub:
                    while True:
                        if self._listeners:
                            await self._poll_once(hub)
                        await asyncio.sleep(self._poll)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("reminders_failed", error=type(exc).__name__)
                await asyncio.sleep(RETRY_SECONDS)

    async def _poll_once(self, hub: McpHub) -> None:
        for server, cfg in self._servers.items():
            if cfg.reminders is None:
                continue
            data = await hub.call_internal(
                server, cfg.reminders, RiskClass.WRITE_LOCAL, worth_recording=_delivered
            )
            for reminder in parse_reminders(data):
                log.info("reminder_delivered", server=server, listeners=len(self._listeners))
                for queue in list(self._listeners):
                    queue.put_nowait(reminder)
