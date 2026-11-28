"""Modell-Router (M1.1): Auswahlregel, Stream-Ereignis, Audit ohne Inhalte, ohne zweites Modell
keine Änderung."""

import sqlite3
from pathlib import Path

import pytest
from pydantic_ai.models.test import TestModel

from jarvis_core.agent import Progress, TextDelta, ToolUsed, build_service
from jarvis_core.agent.router import FAST, SHORT_CHARS, STRONG, choose_tier
from jarvis_core.audit import AuditLog
from jarvis_core.config import Settings
from jarvis_core.config.settings import McpServerConfig
from jarvis_core.mcp_hub.select import ToolSelector
from jarvis_core.policy import RiskClass

from .test_tool_select import FAKE_SERVER, fake_embed

pytestmark = pytest.mark.anyio


def test_short_question_without_tool_goes_fast() -> None:
    assert choose_tier("Wer bist du?", 0.1, 0.6) == FAST


def test_tool_question_goes_strong() -> None:
    assert choose_tier("Wie ist das Wetter?", 0.9, 0.6) == STRONG


def test_long_question_goes_strong() -> None:
    assert choose_tier("x" * (SHORT_CHARS + 1), 0.0, 0.6) == STRONG


def test_unknown_match_goes_strong() -> None:
    assert choose_tier("Hallo", None, 0.6) == STRONG


def _service(tmp_path: Path, fast: bool):  # noqa: ANN202
    cfg = McpServerConfig(
        command="python", args=[FAKE_SERVER], tools={"get_weather": RiskClass.READ}
    )
    settings = Settings(
        data_dir=tmp_path / "data", mcp_servers={"weather": cfg},
        model_fast="klein" if fast else None, model_strong="gross",
    )  # fmt: skip
    audit = AuditLog(settings.audit_db_path)
    selector = ToolSelector(fake_embed, limit=0)
    service = build_service(
        settings, audit, TestModel(call_tools=["get_weather"]), "gross", selector=selector
    )
    return settings, service


def _models(settings: Settings) -> list[str]:
    with sqlite3.connect(settings.audit_db_path) as conn:
        return [r[0] for r in conn.execute("SELECT model FROM audit WHERE tool IS NULL")]


async def test_without_fast_model_nothing_changes(tmp_path: Path) -> None:
    settings, service = _service(tmp_path, fast=False)
    events = [e async for e in service.stream("Wie ist das Wetter?", channel="cli")]
    assert not any(isinstance(e, Progress) for e in events)
    assert ToolUsed("get_weather") in events
    assert _models(settings) == ["gross"]


async def test_tool_question_is_routed_strong(tmp_path: Path) -> None:
    settings, service = _service(tmp_path, fast=True)
    events = [e async for e in service.stream("Wie ist das Wetter?", channel="cli")]
    assert Progress("route", "stark: gross") in events
    assert ToolUsed("get_weather") in events
    assert _models(settings) == ["gross"]


async def test_small_talk_is_routed_fast_without_tools(tmp_path: Path) -> None:
    settings, service = _service(tmp_path, fast=True)
    # Die Attrappe kennt nur "wetter"; "Hallo" liegt unter der Schwelle -> schnelles Modell.
    # Das schnelle Modell ist hier ein eigenes TestModel, damit der Test ohne Ollama läuft.
    service._fast = (TestModel(call_tools=[], custom_output_text="Hallo, Master."), "klein")  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
    events = [e async for e in service.stream("Hallo", channel="cli")]
    assert Progress("route", "schnell: klein") in events
    assert not any(isinstance(e, ToolUsed) for e in events)
    assert "".join(e.text for e in events if isinstance(e, TextDelta)) == "Hallo, Master."
    assert _models(settings) == ["klein"]


async def test_fast_model_still_gets_the_tools(tmp_path: Path) -> None:
    """Eine falsch als "kein Werkzeug" eingestufte Frage darf nicht werkzeuglos beantwortet werden."""
    _, service = _service(tmp_path, fast=True)
    fast = TestModel(call_tools=[], custom_output_text="ok")
    service._fast = (fast, "klein")  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
    [e async for e in service.stream("Hallo", channel="cli")]
    assert fast.last_model_request_parameters is not None
    assert {t.name for t in fast.last_model_request_parameters.function_tools} == {"get_weather"}
