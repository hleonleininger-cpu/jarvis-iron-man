"""Cloud-Stufe (M1.6, ADR-021): aus und ohne Anbieter, Freigabe je Aufgabe, harte Sperre für
private Daten, Audit ohne Inhalt. Kein echter Cloud-Aufruf: das Modell ist eine Attrappe."""

import sqlite3
from pathlib import Path

import pytest
from pydantic import ValidationError
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models import Model
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel

from jarvis_core.agent import (
    ChatError,
    ChatService,
    ConfirmRequested,
    Notice,
    StreamEvent,
    TextDelta,
    build_service,
)
from jarvis_core.agent.cloud import (
    CLOUD_INSTRUCTIONS,
    CLOUD_TOOL,
    DECLINED_MESSAGE,
    OFF_MESSAGE,
    parse_cloud_request,
)
from jarvis_core.audit import AuditLog
from jarvis_core.config import Settings
from jarvis_core.config.settings import CloudConfig, McpServerConfig
from jarvis_core.policy import RiskClass

pytestmark = pytest.mark.anyio

FAKE_SERVER = str(Path(__file__).parent / "fake_mcp_server.py")
ON = CloudConfig(
    enabled=True, provider="Anbieter", model="gross", base_url="https://api.example.com/v1"
)


class Spy:
    """Cloud-Attrappe: merkt sich, was sie zu sehen bekam."""

    def __init__(self, fail: bool = False) -> None:
        self.calls = 0
        self.seen: list[str] = []
        self.tools: list[str] = []
        self.instructions = ""
        self.fail = fail

    def build(self) -> Model:
        def function(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            self.calls += 1
            if self.fail:
                raise OSError("Anbieter nicht erreichbar mit geheimem Detail")
            self.seen = [repr(m) for m in messages]
            self.tools = [t.name for t in info.function_tools]
            self.instructions = info.instructions or ""
            return ModelResponse(parts=[TextPart("Cloud-Antwort")])

        return FunctionModel(function)


def _settings(tmp_path: Path, cloud: CloudConfig | None = None) -> Settings:
    cfg = McpServerConfig(
        command="python", args=[FAKE_SERVER], tools={"get_weather": RiskClass.READ}
    )
    return Settings(
        data_dir=tmp_path / "data", mcp_servers={"weather": cfg}, home_location="Dortmund",
        anrede="Chef", cloud=cloud or CloudConfig(),
    )  # fmt: skip


def _service(settings: Settings, model: Model | None) -> ChatService:
    service = build_service(settings, AuditLog(settings.audit_db_path), TestModel(), "lokal")
    service._cloud_model = lambda: model  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
    return service


async def _ask(
    service: ChatService, text: str, approve: bool = True, session_id: str | None = None
) -> list[StreamEvent]:
    events: list[StreamEvent] = []
    async for event in service.stream(text, channel="api", session_id=session_id):
        events.append(event)
        if isinstance(event, ConfirmRequested):
            service.broker.resolve(event.id, approve)
    return events


def _cloud_rows(settings: Settings) -> list[tuple[str, str, str]]:
    with sqlite3.connect(settings.audit_db_path) as conn:
        return conn.execute(
            "SELECT risk, decision, status FROM audit WHERE tool = ? ORDER BY id", (CLOUD_TOOL,)
        ).fetchall()


def test_off_and_without_provider_by_default() -> None:
    assert Settings().cloud.enabled is False
    assert CloudConfig().usable is False
    assert CloudConfig(enabled=True).usable is False  # eingeschaltet, aber kein Anbieter
    assert ON.usable


def test_base_url_must_be_https() -> None:
    with pytest.raises(ValidationError):
        CloudConfig(base_url="http://api.example.com")


def test_parse_cloud_request() -> None:
    assert parse_cloud_request("/cloud Was ist Python?") == "Was ist Python?"
    assert parse_cloud_request("/CLOUD x") == "x"
    assert parse_cloud_request("/cloud") == ""
    assert parse_cloud_request("/cloudy x") is None
    assert parse_cloud_request("Wetter?") is None


async def test_off_sends_nothing(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    spy = Spy()
    events = await _ask(_service(settings, spy.build()), "/cloud Frage")
    assert any(isinstance(e, Notice) and e.message == OFF_MESSAGE for e in events)
    assert spy.calls == 0
    assert _cloud_rows(settings) == [("EXTERNAL_COMMUNICATION", "DENY", "blocked")]


async def test_declined_sends_nothing(tmp_path: Path) -> None:
    settings = _settings(tmp_path, ON)
    spy = Spy()
    events = await _ask(_service(settings, spy.build()), "/cloud Frage", approve=False)
    assert any(isinstance(e, Notice) and e.message == DECLINED_MESSAGE for e in events)
    assert spy.calls == 0
    assert [r[1:] for r in _cloud_rows(settings)] == [("CONFIRM", "pending"), ("DENIED", "denied")]


async def test_without_key_sends_nothing(tmp_path: Path) -> None:
    settings = _settings(tmp_path, ON)
    events = await _ask(_service(settings, None), "/cloud Frage")
    assert any(isinstance(e, Notice) and "Schlüssel" in e.message for e in events)
    assert _cloud_rows(settings)[-1][1:] == ("CONFIRMED", "error")


async def test_approval_shows_exact_text_and_request_carries_nothing_private(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path, ON)
    service = _service(settings, (spy := Spy()).build())
    # Vorher läuft eine normale Runde mit "privaten" Angaben im Verlauf dieser Sitzung.
    await _ask(service, "Merk: mein Passwort ist geheim123", session_id="s")
    events = await _ask(service, "/cloud Was ist Python?", session_id="s")
    confirm = next(e for e in events if isinstance(e, ConfirmRequested))
    assert "«Was ist Python?»" in confirm.preview and "Anbieter" in confirm.preview
    assert "".join(e.text for e in events if isinstance(e, TextDelta)) == "Cloud-Antwort"
    assert any(isinstance(e, Notice) and "Cloud" in e.message for e in events)
    # Harte Sperre: nur Frage und feste Anweisung, keine Werkzeuge, kein Verlauf, nichts Privates.
    assert spy.tools == []
    assert spy.instructions == CLOUD_INSTRUCTIONS
    joined = " ".join(spy.seen) + spy.instructions
    for private in ("Dortmund", "Chef", "geheim123", "get_weather"):
        assert private not in joined
    assert "Was ist Python?" in joined


async def test_audit_has_no_content(tmp_path: Path) -> None:
    settings = _settings(tmp_path, ON)
    await _ask(_service(settings, Spy().build()), "/cloud EinzigartigerFragetext")
    assert [r[1:] for r in _cloud_rows(settings)] == [("CONFIRM", "pending"), ("CONFIRMED", "ok")]
    assert b"EinzigartigerFragetext" not in settings.audit_db_path.read_bytes()


async def test_provider_failure_is_reported_without_details(tmp_path: Path) -> None:
    settings = _settings(tmp_path, ON)
    with pytest.raises(ChatError) as info:
        await _ask(_service(settings, Spy(fail=True).build()), "/cloud Frage")
    assert "geheim" not in str(info.value)
    assert _cloud_rows(settings)[-1][1:] == ("CONFIRMED", "error")


async def test_missing_question(tmp_path: Path) -> None:
    spy = Spy()
    events = await _ask(_service(_settings(tmp_path, ON), spy.build()), "/cloud")
    assert any(isinstance(e, Notice) and "Frage" in e.message for e in events)
    assert spy.calls == 0


async def test_normal_chat_never_reaches_the_cloud(tmp_path: Path) -> None:
    spy = Spy()
    await _ask(_service(_settings(tmp_path, ON), spy.build()), "Wie ist das Wetter?")
    assert spy.calls == 0
