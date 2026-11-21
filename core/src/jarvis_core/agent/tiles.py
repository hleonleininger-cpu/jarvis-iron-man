"""Live-Kacheln fürs HUD (M3.6): System, Termine, Missionen (offene Aufgaben), Fokus.

Systemwerte rechnet der Core selbst (psutil, ohne Werkzeug). Termine und Aufgaben kommen über die
vorhandenen READ-Werkzeuge auf demselben Weg wie Anfragen (Anfrage-Sperre, Hub, Policy, Audit
ohne Inhalte, Not-Aus). Sie werden höchstens alle fünf Minuten neu geholt: so bleibt das Audit
lesbar und iCloud bekommt nicht jede Minute eine Anfrage. Läuft gerade eine Anfrage, liefert der
Dienst den letzten Stand mit Zeitstempel und versucht es beim nächsten Abruf wieder.

Jede Kachel zeigt ehrlich "keine Daten", wenn die Quelle fehlt; nie einen erfundenen Wert.
"""

import asyncio
import time
from collections.abc import Callable
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import psutil

from jarvis_core.agent.service import ChatService
from jarvis_core.mcp_hub import current_channel

TZ = ZoneInfo("Europe/Berlin")
TOOL_SECONDS = 300.0  # Termine und Aufgaben höchstens alle fünf Minuten
MAX_EVENTS = 3
MAX_TASKS = 5
MAX_TEXT = 80
NO_FOCUS = "Noch kein aktives Projekt festgelegt."

type Tile = dict[str, Any]


def _none(reason: str) -> Tile:
    return {"ok": False, "grund": reason}


def _short(text: object) -> str:
    value = text if isinstance(text, str) else ""
    return value if len(value) <= MAX_TEXT else value[: MAX_TEXT - 1] + "…"


def _as_dict(value: object) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    mapping: dict[str, Any] = value  # pyright: ignore[reportUnknownVariableType]
    return mapping


def _as_list(value: object) -> list[dict[str, Any]]:
    items: list[object] = value if isinstance(value, list) else []  # pyright: ignore[reportUnknownVariableType]
    return [entry for item in items if (entry := _as_dict(item)) is not None]


def system_tile() -> Tile:
    """CPU- und Speicherlast in Prozent, Akku falls vorhanden. Nur Zahlen."""
    memory = psutil.virtual_memory()
    battery = psutil.sensors_battery() if hasattr(psutil, "sensors_battery") else None
    return {
        "ok": True,
        "cpu": round(psutil.cpu_percent(interval=None)),
        "ram": round(memory.percent),
        "akku": None
        if battery is None
        else {"prozent": round(battery.percent), "laedt": bool(battery.power_plugged)},
    }


def _start_of(entry: dict[str, Any]) -> datetime | None:
    raw = entry.get("start")
    if not isinstance(raw, str):
        return None
    try:
        if len(raw) == 10:  # ganztägig: JJJJ-MM-TT
            return datetime.combine(date.fromisoformat(raw), datetime.min.time(), TZ)
        value = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return value if value.tzinfo else value.replace(tzinfo=TZ)


def _end_of(entry: dict[str, Any], start: datetime) -> datetime:
    raw = entry.get("ende")
    if isinstance(raw, str):
        try:
            if len(raw) == 10:  # ganztägig: letzter Tag inklusive
                return datetime.combine(date.fromisoformat(raw), datetime.min.time(), TZ) + (
                    timedelta(days=1)
                )
            value = datetime.fromisoformat(raw)
            return value if value.tzinfo else value.replace(tzinfo=TZ)
        except ValueError:
            pass
    return start


def events_tile(data: object, now: datetime) -> Tile:
    """Die nächsten Termine, die noch nicht vorbei sind (laufende zählen mit)."""
    result = _as_dict(data)
    if result is None or result.get("ok") is not True:
        return _none("Termine konnten nicht gelesen werden.")
    upcoming: list[tuple[datetime, dict[str, Any]]] = []
    for entry in _as_list(result.get("termine")):
        start = _start_of(entry)
        if start is not None and _end_of(entry, start) > now:
            upcoming.append((start, entry))
    upcoming.sort(key=lambda pair: pair[0])
    return {
        "ok": True,
        "eintraege": [
            {
                "titel": _short(entry.get("titel")),
                "start": start.isoformat(),
                "ganztaegig": entry.get("ganztaegig") is True,
                "ort": _short(entry.get("ort")) or None,
            }
            for start, entry in upcoming[:MAX_EVENTS]
        ],
    }


def tasks_tile(data: object) -> Tile:
    """Offene Aufgaben, überfällige und bald fällige zuerst (Reihenfolge des Notiz-Servers)."""
    result = _as_dict(data)
    if result is None or result.get("ok") is not True:
        return _none("Aufgaben konnten nicht gelesen werden.")
    entries = _as_list(result.get("eintraege"))
    count = result.get("anzahl")
    return {
        "ok": True,
        "anzahl": count if isinstance(count, int) else len(entries),
        "eintraege": [
            {
                "text": _short(entry.get("text")),
                "faellig": entry.get("faellig") if isinstance(entry.get("faellig"), str) else None,
                "ueberfaellig": entry.get("ueberfaellig") is True,
            }
            for entry in entries[:MAX_TASKS]
        ],
    }


class TileService:
    def __init__(
        self,
        service: ChatService,
        clock: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] = lambda: datetime.now(TZ),
    ) -> None:
        self._service = service
        self._clock = clock
        self._now = now
        self._tools: dict[str, Tile] | None = None
        self._tools_at: float | None = None
        self._stand: str | None = None
        self._loading: asyncio.Task[None] | None = None
        psutil.cpu_percent(interval=None)  # der erste Wert ist immer 0: einmal vorab messen

    async def tiles(self) -> dict[str, Any]:
        due = self._tools_at is None or self._clock() - self._tools_at >= TOOL_SECONDS
        if due and (self._loading is None or self._loading.done()):
            self._loading = asyncio.create_task(self._load())
        if self._loading is not None and not self._loading.done():
            # Abbrechen der HTTP-Anfrage bricht das Laden nicht ab (shield); der Not-Aus schon.
            await asyncio.wait({asyncio.shield(self._loading)})
        tools = self._tools or {
            "termine": _none("Noch nicht geladen."),
            "missionen": _none("Noch nicht geladen."),
        }
        return {
            "stand": self._stand,
            "aktuell": self._tools_at is not None and self._clock() - self._tools_at < TOOL_SECONDS,
            "system": await asyncio.to_thread(system_tile),
            **tools,
            "fokus": _none(NO_FOCUS),
        }

    async def _load(self) -> None:
        hub = self._service.hub
        token = current_channel.set("hud")
        try:
            async with self._service.idle_turn() as free:
                if not free:
                    return  # eine Anfrage läuft: alter Stand bleibt, nächster Abruf versucht es
                now = self._now()
                today = now.date()
                tiles: dict[str, Tile] = {}
                if hub.has_read_tool("list_events"):
                    window = f"{today.isoformat()}/{(today + timedelta(days=6)).isoformat()}"
                    data = await hub.read_tool("list_events", {"zeitraum": window})
                    tiles["termine"] = events_tile(data, now)
                else:
                    tiles["termine"] = _none("Kein Kalender eingerichtet.")
                if hub.has_read_tool("list_notes"):
                    tiles["missionen"] = tasks_tile(await hub.read_tool("list_notes", {}))
                else:
                    tiles["missionen"] = _none("Keine Aufgabenliste eingerichtet.")
                self._tools = tiles
                self._tools_at = self._clock()
                self._stand = now.isoformat(timespec="seconds")
        finally:
            current_channel.reset(token)
