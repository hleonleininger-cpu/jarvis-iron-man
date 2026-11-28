"""Werkzeugauswahl per Embedding (M1.4): Auswahl, Folgefragen, Ausweichweg, Policy unverändert."""

import sqlite3
from pathlib import Path

import pytest
from pydantic_ai.models.test import TestModel

from jarvis_core.agent import ChatService, ToolUsed, build_service
from jarvis_core.audit import AuditLog
from jarvis_core.config import Settings
from jarvis_core.config.settings import McpServerConfig
from jarvis_core.mcp_hub import ToolInfo
from jarvis_core.mcp_hub.select import COOLDOWN_SECONDS, ToolSelector, cosine
from jarvis_core.policy import RiskClass

pytestmark = pytest.mark.anyio

FAKE_SERVER = str(Path(__file__).parent / "fake_mcp_server.py")

INFOS = [
    ToolInfo("a", "get_weather", "Wetter und Temperatur", RiskClass.READ),
    ToolInfo("a", "list_events", "Termine im Kalender lesen", RiskClass.READ),
    ToolInfo("a", "add_note", "Notiz speichern", RiskClass.WRITE_LOCAL),
]
VECTORS = {"wetter": [1.0, 0.0, 0.0], "termin": [0.0, 1.0, 0.0], "notiz": [0.0, 0.0, 1.0]}


def fake_embed(texts: list[str]) -> list[list[float]]:
    """Attrappe: Vektor nach Stichwort, so ist die Rangfolge im Test festgelegt."""
    out: list[list[float]] = []
    for text in texts:
        low = text.lower()
        vec = next((v for key, v in VECTORS.items() if key in low), [0.1, 0.1, 0.1])
        out.append(vec)
    return out


def test_cosine() -> None:
    assert cosine([1, 0], [1, 0]) == pytest.approx(1.0)
    assert cosine([1, 0], [0, 1]) == pytest.approx(0.0)
    assert cosine([0, 0], [1, 1]) == 0.0


async def test_selector_keeps_best_matches() -> None:
    selector = ToolSelector(fake_embed, limit=1)
    selection = await selector.choose(INFOS, "Wie wird das Wetter?")
    assert selection is not None
    assert selection.names == {"get_weather"}
    assert selection.best == pytest.approx(1.0)


async def test_limit_zero_keeps_all_but_still_scores() -> None:
    selection = await ToolSelector(fake_embed, limit=0).choose(INFOS, "Termine heute")
    assert selection is not None
    assert selection.names == {"get_weather", "list_events", "add_note"}


async def test_failure_falls_back_to_all_and_pauses() -> None:
    now = [0.0]
    calls = [0]

    def broken(_texts: list[str]) -> list[list[float]]:
        calls[0] += 1
        raise OSError("weg")

    selector = ToolSelector(broken, limit=1, clock=lambda: now[0])
    assert await selector.choose(INFOS, "Wetter") is None
    assert await selector.choose(INFOS, "Wetter") is None  # Pause: kein zweiter Versuch
    assert calls[0] == 1
    now[0] += COOLDOWN_SECONDS + 1
    assert await selector.choose(INFOS, "Wetter") is None
    assert calls[0] == 2


async def test_tool_vectors_are_cached() -> None:
    seen: list[int] = []

    def counting(texts: list[str]) -> list[list[float]]:
        seen.append(len(texts))
        return fake_embed(texts)

    selector = ToolSelector(counting, limit=1)
    await selector.choose(INFOS, "Wetter")
    await selector.choose(INFOS, "Termin")
    assert seen == [3, 1, 1]  # Werkzeuge nur einmal, danach nur die Frage


def _service(
    tmp_path: Path,
    limit: int,
    selector: ToolSelector | None,
    model: TestModel | None = None,
) -> tuple[Settings, ChatService]:
    cfg = McpServerConfig(
        command="python",
        args=[FAKE_SERVER],
        tools={"get_weather": RiskClass.READ, "create_event": RiskClass.WRITE_REVERSIBLE},
        previews={"create_event": "preview_create_event"},
    )
    settings = Settings(
        data_dir=tmp_path / "data", mcp_servers={"weather": cfg}, tool_select_limit=limit
    )
    audit = AuditLog(settings.audit_db_path)
    return settings, build_service(settings, audit, model or TestModel(), "test", selector=selector)


async def test_hub_offers_only_selected_tools(tmp_path: Path) -> None:
    _, service = _service(tmp_path, 0, None)
    async with service.hub:
        assert service.hub.offered_tools == {"get_weather", "create_event"}
        service.hub.select_tools(frozenset({"get_weather"}))
        assert service.hub.offered_tools == {"get_weather"}
        assert service.hub.prompt_chars < sum(i.prompt_chars for i in service.hub.list_tools())
    async with service.hub:  # nächste Anfrage: Auswahl ist zurückgesetzt
        assert service.hub.offered_tools == {"get_weather", "create_event"}


async def test_chat_with_selection_hides_other_tools_from_model(tmp_path: Path) -> None:
    selector = ToolSelector(fake_embed, limit=1)
    model = TestModel(call_tools=["get_weather"])
    settings, service = _service(tmp_path, 1, selector, model)
    events = [e async for e in service.stream("Wie ist das Wetter?", channel="cli")]
    assert ToolUsed("get_weather") in events
    assert model.last_model_request_parameters is not None
    offered = {t.name for t in model.last_model_request_parameters.function_tools}
    assert offered == {"get_weather"}
    with sqlite3.connect(settings.audit_db_path) as conn:
        rows = conn.execute("SELECT tool, decision FROM audit WHERE tool IS NOT NULL").fetchall()
    assert rows == [("get_weather", "ALLOW")]


async def test_follow_up_uses_previous_question(tmp_path: Path) -> None:
    queries: list[str] = []

    def spying(texts: list[str]) -> list[list[float]]:
        if len(texts) == 1:
            queries.append(texts[0])
        return fake_embed(texts)

    selector = ToolSelector(spying, limit=1)
    _, service = _service(tmp_path, 1, selector, TestModel(call_tools=[]))
    async for _ in service.stream("Wie ist das Wetter?", channel="cli", session_id="s1"):
        pass
    async for _ in service.stream("und morgen?", channel="cli", session_id="s1"):
        pass
    assert queries[0] == "Wie ist das Wetter?"
    assert queries[1].startswith("Wie ist das Wetter?") and queries[1].endswith("und morgen?")
