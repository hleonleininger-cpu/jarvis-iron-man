import json
import sqlite3
from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.models.test import TestModel
from pydantic_ai.providers.openai import OpenAIProvider

from jarvis_core.agent import ChatError, build_service
from jarvis_core.api import create_app
from jarvis_core.audit import AuditLog
from jarvis_core.config import Settings

Starter = Callable[[Callable[[str], tuple[int, dict[str, object]]]], str]


def _events(client: TestClient, message: str) -> list[tuple[str, dict[str, str]]]:
    events: list[tuple[str, dict[str, str]]] = []
    with client.stream("POST", "/v1/chat", json={"message": message}) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        for block in "".join(resp.iter_text()).strip().split("\n\n"):
            event_line, data_line = block.split("\n")
            events.append((event_line.removeprefix("event: "), json.loads(data_line[6:])))
    return events


def _audit_rows(settings: Settings) -> list[tuple[str, str, int | None, str]]:
    with sqlite3.connect(settings.audit_db_path) as conn:
        return conn.execute(
            "SELECT channel, model, tokens, status FROM audit ORDER BY id"
        ).fetchall()


def test_health(settings: Settings) -> None:
    client = TestClient(create_app(settings, TestModel(), "test"), base_url="http://127.0.0.1")
    assert client.get("/health").json()["status"] == "ok"


def test_chat_streams_deltas_then_done(settings: Settings) -> None:
    model = TestModel(custom_output_text="Ich bin JARVIS, Ihr Assistent.")
    client = TestClient(create_app(settings, model, "test"), base_url="http://127.0.0.1")
    events = _events(client, "Wer bist du?")
    kinds = [kind for kind, _ in events]
    assert kinds[-1] == "done"
    assert set(kinds[:-1]) == {"delta"}
    assert "".join(d["text"] for k, d in events if k == "delta") == "Ich bin JARVIS, Ihr Assistent."


def test_chat_writes_audit_without_content(settings: Settings) -> None:
    client = TestClient(
        create_app(settings, TestModel(custom_output_text="Hallo"), "test"),
        base_url="http://127.0.0.1",
    )
    _events(client, "streng geheimer Inhalt")
    rows = _audit_rows(settings)
    assert len(rows) == 1
    channel, model, tokens, status = rows[0]
    assert (channel, model, status) == ("api", "test", "ok")
    assert tokens is not None and tokens > 0
    assert b"streng geheimer Inhalt" not in settings.audit_db_path.read_bytes()
    assert AuditLog(settings.audit_db_path).verify_chain().ok


def test_empty_message_rejected(settings: Settings) -> None:
    client = TestClient(create_app(settings, TestModel(), "test"), base_url="http://127.0.0.1")
    assert client.post("/v1/chat", json={"message": ""}).status_code == 422


def test_unreachable_ollama_gives_error_event(settings: Settings) -> None:
    unreachable = settings.model_copy(update={"ollama_base_url": "http://127.0.0.1:1/v1"})
    client = TestClient(
        create_app(unreachable, model_name="qwen3.5:4b"), base_url="http://127.0.0.1"
    )
    events = _events(client, "Hallo")
    assert events[-1][0] == "error"
    assert "nicht erreichbar" in events[-1][1]["message"]
    assert _audit_rows(settings)[0][3] == "error"


@pytest.mark.anyio
async def test_missing_model_names_pull_command(settings: Settings, fake_server: Starter) -> None:
    url = fake_server(lambda _p: (404, {"error": {"message": "model not found"}}))
    model = OpenAIChatModel("fehlt:1b", provider=OpenAIProvider(base_url=f"{url}/v1", api_key="x"))
    service = build_service(settings, AuditLog(settings.audit_db_path), model, "fehlt:1b")
    with pytest.raises(ChatError, match="ollama pull fehlt:1b"):
        async for _ in service.stream("Hallo", channel="cli"):
            pass
    assert _audit_rows(settings)[0][3] == "error"


@pytest.mark.anyio
async def test_aborted_stream_is_audited(settings: Settings) -> None:
    service = build_service(
        settings,
        AuditLog(settings.audit_db_path),
        TestModel(custom_output_text="eins zwei drei vier"),
        "test",
    )
    gen = service.stream("Hallo", channel="cli")
    await anext(gen)
    await gen.aclose()
    assert _audit_rows(settings)[0][3] == "aborted"


@pytest.mark.parametrize("blank", [" ", "\n\t  "])
def test_blank_message_rejected(settings: Settings, blank: str) -> None:
    client = TestClient(create_app(settings, TestModel(), "test"), base_url="http://127.0.0.1")
    assert client.post("/v1/chat", json={"message": blank}).status_code == 422


def test_stream_is_not_cached_or_buffered(settings: Settings) -> None:
    client = TestClient(
        create_app(settings, TestModel(custom_output_text="Hallo"), "test"),
        base_url="http://127.0.0.1",
    )
    with client.stream("POST", "/v1/chat", json={"message": "Hallo"}) as resp:
        assert resp.headers["cache-control"] == "no-store"
        assert resp.headers["x-accel-buffering"] == "no"


@pytest.mark.parametrize("host", ["evil.example", "127.0.0.1.evil.example", "testserver"])
def test_foreign_host_header_is_rejected(settings: Settings, host: str) -> None:
    """Schutz vor DNS-Rebinding: der Core antwortet nur unter seiner eigenen Adresse."""
    client = TestClient(create_app(settings, TestModel(), "test"), base_url="http://127.0.0.1")
    response = client.post("/v1/chat", json={"message": "Hallo"}, headers={"Host": host})
    assert response.status_code == 400
    assert client.get("/health", headers={"Host": host}).status_code == 400


@pytest.mark.parametrize("host", ["127.0.0.1", "127.0.0.1:8765", "localhost:8765"])
def test_own_host_header_is_accepted(settings: Settings, host: str) -> None:
    client = TestClient(create_app(settings, TestModel(), "test"), base_url="http://127.0.0.1")
    assert client.get("/health", headers={"Host": host}).status_code == 200


def test_configured_tailnet_address_is_accepted(settings: Settings) -> None:
    tailnet = settings.model_copy(update={"host": "100.101.102.103"})
    client = TestClient(create_app(tailnet, TestModel(), "test"), base_url="http://100.101.102.103")
    assert client.get("/health").status_code == 200


# ------------------------------------------------------------------------------------- Runde 7
def test_keepalive_pings_while_nothing_happens(settings: Settings) -> None:
    import asyncio
    from collections.abc import AsyncIterator

    from pydantic_ai.messages import ModelMessage
    from pydantic_ai.models.function import AgentInfo, FunctionModel

    async def slow(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        await asyncio.sleep(0.5)
        yield "fertig"

    app = create_app(settings, FunctionModel(stream_function=slow), "test", keepalive_seconds=0.1)
    client = TestClient(app, base_url="http://127.0.0.1")
    with client.stream("POST", "/v1/chat", json={"message": "Hallo"}) as resp:
        body = "".join(resp.iter_text())
    assert body.count(": ping\n\n") >= 2
    assert "event: delta" in body and body.rstrip().splitlines()[-2] == "event: done"
    assert _audit_rows(settings)[0][3] == "ok"


@pytest.mark.anyio
async def test_keepalive_closes_the_source_when_the_client_leaves() -> None:
    import asyncio
    from collections.abc import AsyncGenerator

    from jarvis_core.api.app import with_keepalive

    closed: list[bool] = []

    async def source() -> AsyncGenerator[str]:
        try:
            yield "eins"
            await asyncio.sleep(10)
            yield "nie"
        finally:
            closed.append(True)

    stream = with_keepalive(source(), 0.05)
    assert await anext(stream) == "eins"
    assert await anext(stream) is None  # Lebenszeichen, Quelle wartet noch
    await stream.aclose()
    assert closed == [True]
