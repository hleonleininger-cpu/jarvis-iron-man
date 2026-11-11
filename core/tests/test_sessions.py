"""Gesprächsverlauf: SessionStore, Agent mit Verlauf, API-Sitzungen, CLI-Dialog."""

import json
import sqlite3
from collections.abc import AsyncIterator

import pytest
from fastapi.testclient import TestClient
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel

from jarvis_core.agent import ChatService, SessionStore, build_service
from jarvis_core.api import create_app
from jarvis_core.audit import AuditLog
from jarvis_core.cli import main as cli_main
from jarvis_core.config import Settings


def _turn(question: str, answer: str = "ok") -> list[ModelMessage]:
    return [
        ModelRequest(parts=[UserPromptPart(content=question)]),
        ModelResponse(parts=[TextPart(content=answer)]),
    ]


def _questions(messages: list[ModelMessage]) -> list[str]:
    return [
        str(part.content)
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, UserPromptPart)
    ]


# ------------------------------------------------------------------------------- SessionStore
def test_store_keeps_turns_in_order_and_separates_sessions() -> None:
    store = SessionStore()
    store.append("a", _turn("eins"))
    store.append("a", _turn("zwei"))
    store.append("b", _turn("anders"))
    assert _questions(store.history("a")) == ["eins", "zwei"]
    assert _questions(store.history("b")) == ["anders"]
    assert store.history("unbekannt") == []


def test_store_drops_oldest_turns_beyond_max_turns() -> None:
    store = SessionStore(max_turns=2)
    for name in ("eins", "zwei", "drei"):
        store.append("a", _turn(name))
    assert _questions(store.history("a")) == ["zwei", "drei"]


def test_store_drops_whole_turns_beyond_char_budget() -> None:
    store = SessionStore(max_chars=1_500)
    store.append("a", _turn("alt", "x" * 900))
    store.append("a", _turn("neu", "y" * 900))
    history = store.history("a")
    assert _questions(history) == ["neu"]
    assert len(history) == 2  # Frage und Antwort bleiben zusammen


def test_store_forgets_a_single_oversized_turn() -> None:
    store = SessionStore(max_chars=1_000)
    store.append("a", _turn("riesig", "x" * 5_000))
    assert store.history("a") == []


def test_store_with_zero_turns_keeps_nothing() -> None:
    store = SessionStore(max_turns=0)
    store.append("a", _turn("eins"))
    assert store.history("a") == [] and len(store) == 0


def test_store_evicts_least_recently_used_session() -> None:
    store = SessionStore(max_sessions=2)
    store.append("a", _turn("a"))
    store.append("b", _turn("b"))
    store.history("a")  # a ist jetzt die zuletzt genutzte
    store.append("c", _turn("c"))
    assert store.history("b") == []
    assert _questions(store.history("a")) == ["a"] and _questions(store.history("c")) == ["c"]


def test_store_expires_idle_sessions() -> None:
    now = [0.0]
    store = SessionStore(ttl_seconds=60, clock=lambda: now[0])
    store.append("a", _turn("eins"))
    now[0] = 59.0
    assert _questions(store.history("a")) == ["eins"]  # Nutzung verlängert die Frist
    now[0] = 118.0
    assert len(store) == 1
    now[0] = 200.0
    assert store.history("a") == [] and len(store) == 0


def test_store_clear() -> None:
    store = SessionStore()
    store.append("a", _turn("eins"))
    assert store.clear("a") is True
    assert store.clear("a") is False
    assert store.history("a") == []


# ------------------------------------------------------------------------------- ChatService
class _Recorder:
    """Modell-Attrappe: merkt sich je Aufruf die erhaltenen Fragen und die Anweisungen."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.instructions: list[str | None] = []

    async def stream(self, messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        self.calls.append(_questions(messages))
        self.instructions.append(info.instructions)
        yield f"Antwort {len(self.calls)}"


def _service(settings: Settings, recorder: _Recorder) -> ChatService:
    model = FunctionModel(stream_function=recorder.stream)
    return build_service(settings, AuditLog(settings.audit_db_path), model, "test")


async def _ask(service: ChatService, text: str, session_id: str | None) -> None:
    async for _ in service.stream(text, channel="cli", session_id=session_id):
        pass


@pytest.mark.anyio
async def test_session_gives_model_the_previous_turns(settings: Settings) -> None:
    recorder = _Recorder()
    service = _service(settings, recorder)
    await _ask(service, "Ich heiße Leon.", "s1")
    await _ask(service, "Wie heiße ich?", "s1")
    await _ask(service, "Und in einer anderen Sitzung?", "s2")
    assert recorder.calls == [
        ["Ich heiße Leon."],
        ["Ich heiße Leon.", "Wie heiße ich?"],
        ["Und in einer anderen Sitzung?"],
    ]


@pytest.mark.anyio
async def test_without_session_every_request_stands_alone(settings: Settings) -> None:
    recorder = _Recorder()
    service = _service(settings, recorder)
    await _ask(service, "eins", None)
    await _ask(service, "zwei", None)
    assert recorder.calls == [["eins"], ["zwei"]]
    assert len(service.sessions) == 0


@pytest.mark.anyio
async def test_instructions_are_sent_on_every_turn(settings: Settings) -> None:
    recorder = _Recorder()
    service = _service(settings, recorder)
    await _ask(service, "eins", "s1")
    await _ask(service, "zwei", "s1")
    assert len(recorder.instructions) == 2
    assert all(
        text and "Du bist JARVIS" in text and "Heute ist" in text for text in recorder.instructions
    )


@pytest.mark.anyio
async def test_failed_turn_is_not_remembered(settings: Settings) -> None:
    calls: list[list[str]] = []

    async def stream_fn(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        calls.append(_questions(messages))
        if len(calls) == 1:
            raise RuntimeError("Modell weg")
        yield "ok"

    service = build_service(
        settings, AuditLog(settings.audit_db_path), FunctionModel(stream_function=stream_fn), "test"
    )
    with pytest.raises(Exception, match="Unerwarteter Fehler"):
        await _ask(service, "geht schief", "s1")
    await _ask(service, "zweiter Versuch", "s1")
    assert calls[1] == ["zweiter Versuch"]


@pytest.mark.anyio
async def test_history_never_reaches_audit_or_disk(settings: Settings) -> None:
    service = _service(settings, _Recorder())
    await _ask(service, "streng geheime Frage", "s1")
    await _ask(service, "noch eine geheime Frage", "s1")
    for path in settings.data_dir.rglob("*"):
        if path.is_file():
            assert b"geheime Frage" not in path.read_bytes()
    with sqlite3.connect(settings.audit_db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM audit").fetchone() == (2,)


# ------------------------------------------------------------------------------------- API
def _post(client: TestClient, body: dict[str, str]) -> list[tuple[str, dict[str, str]]]:
    events: list[tuple[str, dict[str, str]]] = []
    with client.stream("POST", "/v1/chat", json=body) as resp:
        assert resp.status_code == 200
        for block in "".join(resp.iter_text()).strip().split("\n\n"):
            event_line, data_line = block.split("\n")
            events.append((event_line.removeprefix("event: "), json.loads(data_line[6:])))
    return events


def test_api_returns_session_id_and_continues_it(settings: Settings) -> None:
    recorder = _Recorder()
    client = TestClient(
        create_app(settings, FunctionModel(stream_function=recorder.stream), "test"),
        base_url="http://127.0.0.1",
    )
    first = _post(client, {"message": "Ich heiße Leon."})
    session_id = first[-1][1]["session_id"]
    assert first[-1][0] == "done" and len(session_id) == 32

    second = _post(client, {"message": "Wie heiße ich?", "session_id": session_id})
    assert second[-1][1]["session_id"] == session_id
    assert recorder.calls[1] == ["Ich heiße Leon.", "Wie heiße ich?"]

    _post(client, {"message": "Neu"})
    assert recorder.calls[2] == ["Neu"]


def test_api_accepts_client_chosen_session_id(settings: Settings) -> None:
    recorder = _Recorder()
    client = TestClient(
        create_app(settings, FunctionModel(stream_function=recorder.stream), "test"),
        base_url="http://127.0.0.1",
    )
    _post(client, {"message": "eins", "session_id": "iphone-leon"})
    events = _post(client, {"message": "zwei", "session_id": "iphone-leon"})
    assert events[-1][1]["session_id"] == "iphone-leon"
    assert recorder.calls[1] == ["eins", "zwei"]


@pytest.mark.parametrize("bad", ["", "a b", "ä", "x" * 65, "../etc"])
def test_api_rejects_malformed_session_id(settings: Settings, bad: str) -> None:
    client = TestClient(create_app(settings, TestModel(), "test"), base_url="http://127.0.0.1")
    response = client.post("/v1/chat", json={"message": "Hallo", "session_id": bad})
    assert response.status_code == 422


def test_api_delete_session_forgets_history(settings: Settings) -> None:
    recorder = _Recorder()
    client = TestClient(
        create_app(settings, FunctionModel(stream_function=recorder.stream), "test"),
        base_url="http://127.0.0.1",
    )
    _post(client, {"message": "eins", "session_id": "s1"})
    assert client.delete("/v1/sessions/s1").json() == {"status": "ok"}
    assert client.delete("/v1/sessions/s1").status_code == 404
    _post(client, {"message": "zwei", "session_id": "s1"})
    assert recorder.calls[1] == ["zwei"]


# ------------------------------------------------------------------------------------- CLI
def _patch_cli(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, recorder: _Recorder, typed: list[str]
) -> list[str]:
    prompts: list[str] = []
    lines = iter(typed)

    def fake_input(prompt: str = "") -> str:
        prompts.append(prompt)
        try:
            return next(lines)
        except StopIteration:
            raise EOFError from None

    def fake_build(s: Settings, audit: AuditLog) -> ChatService:
        return build_service(s, audit, FunctionModel(stream_function=recorder.stream), "test")

    monkeypatch.setattr(cli_main, "load_settings", lambda: settings)
    monkeypatch.setattr(cli_main, "build_service", fake_build)
    monkeypatch.setattr("builtins.input", fake_input)
    return prompts


def test_cli_dialog_keeps_history_until_reset(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    recorder = _Recorder()
    prompts = _patch_cli(
        monkeypatch, settings, recorder, ["Ich heiße Leon.", "", "Wie heiße ich?", "/neu", "Hallo"]
    )
    cli_main.chat(None)
    out = capfd.readouterr().out
    assert recorder.calls == [
        ["Ich heiße Leon."],
        ["Ich heiße Leon.", "Wie heiße ich?"],
        ["Hallo"],
    ]
    assert "Antwort 1" in out and "Antwort 3" in out and "Neues Gespräch." in out
    assert set(prompts) == {"du> "}


def test_cli_dialog_ends_on_command(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    recorder = _Recorder()
    _patch_cli(monkeypatch, settings, recorder, ["Hallo", "/ende", "wird nie gesendet"])
    cli_main.chat(None)
    capfd.readouterr()
    assert recorder.calls == [["Hallo"]]


def test_cli_single_message_stays_stateless(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    recorder = _Recorder()
    prompts = _patch_cli(monkeypatch, settings, recorder, [])
    cli_main.chat("Wer bist du?")
    assert "Antwort 1" in capfd.readouterr().out
    assert prompts == [] and recorder.calls == [["Wer bist du?"]]


def test_cli_dialog_does_not_send_mistyped_commands_to_the_model(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    recorder = _Recorder()
    _patch_cli(monkeypatch, settings, recorder, ["/hilfe", "/NEU", "Was ist 3/4 als Dezimalzahl?"])
    cli_main.chat(None)
    out = capfd.readouterr().out
    assert "Unbekannter Befehl '/hilfe'" in out and "Neues Gespräch." in out
    assert recorder.calls == [["Was ist 3/4 als Dezimalzahl?"]]


def test_cli_strips_terminal_control_sequences(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    async def stream_fn(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        yield "Hallo \x1b]52;c;Ym9zZQ==\x07Welt\x1b[2J\n"

    def fake_build(s: Settings, audit: AuditLog) -> ChatService:
        return build_service(s, audit, FunctionModel(stream_function=stream_fn), "test")

    monkeypatch.setattr(cli_main, "load_settings", lambda: settings)
    monkeypatch.setattr(cli_main, "build_service", fake_build)
    cli_main.chat("Hallo")
    out = capfd.readouterr().out
    assert "\x1b" not in out and "\x07" not in out
    assert "Hallo ]52;c;Ym9zZQ==Welt[2J" in out  # Text bleibt lesbar, wirkt aber nicht mehr
