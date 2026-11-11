import json
import sqlite3
from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.models.test import TestModel
from pydantic_ai.providers.openai import OpenAIProvider

from jarvis_core.agent import ChatError, TextDelta, build_service
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


# ------------------------------------------------------------------------------------- Runde 8
def test_tailnet_name_is_accepted_only_when_bound_to_the_tailnet(settings: Settings) -> None:
    from jarvis_core.api.app import allowed_hosts

    assert "*.ts.net" in allowed_hosts("100.101.102.103")
    assert "*.ts.net" not in allowed_hosts("127.0.0.1")
    tailnet = settings.model_copy(update={"host": "100.101.102.103"})
    client = TestClient(create_app(tailnet, TestModel(), "test"), base_url="http://100.101.102.103")
    assert client.get("/health", headers={"Host": "pc.tail1234.ts.net"}).status_code == 200
    assert client.get("/health", headers={"Host": "evil.example"}).status_code == 400


def test_model_requests_ignore_system_proxy_and_time_out(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, fake_server: Starter
) -> None:
    """Gesprächsinhalte gehen nie über HTTP_PROXY; ein hängendes Modell blockiert nicht ewig."""
    import asyncio

    from jarvis_core.agent import service as service_module

    hits: list[str] = []

    def slow(path: str) -> tuple[int, dict[str, object]]:
        hits.append(path)
        import time

        time.sleep(1.5)
        return 200, {}

    url = fake_server(slow)
    monkeypatch.setenv("HTTP_PROXY", "http://10.255.255.1:9")
    monkeypatch.setenv("NO_PROXY", "")
    monkeypatch.setenv("no_proxy", "")
    monkeypatch.setattr(service_module, "MODEL_TIMEOUT_SECONDS", 0.5)
    local = settings.model_copy(update={"ollama_base_url": f"{url}/v1"})
    service = build_service(local, AuditLog(local.audit_db_path), model_name="langsam:1b")

    async def ask() -> None:
        async for _ in service.stream("Hallo", channel="cli"):
            pass

    with pytest.raises(ChatError, match="nicht geantwortet"):
        asyncio.run(ask())
    assert hits == ["/v1/chat/completions"]  # direkt beim lokalen Server, genau ein Versuch


# ------------------------------------------------------------------------------------- Runde 10
def test_dialog_turns_survive_keep_alive_connections(settings: Settings) -> None:
    """Früher scheiterte jede zweite Dialogrunde: die offene Verbindung gehörte zur Event-Loop
    der Vorrunde ("Event loop is closed"). Echtes Ollama hält Verbindungen offen (HTTP/1.1)."""
    import asyncio
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    def chunk(delta: dict[str, object], finish: str | None = None) -> str:
        choice = {"index": 0, "delta": delta, "finish_reason": finish}
        payload = {"id": "x", "object": "chat.completion.chunk", "created": 1, "model": "m",
                   "choices": [choice]}  # fmt: skip
        return f"data: {json.dumps(payload)}\n\n"

    class KeepAlive(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self) -> None:  # noqa: N802
            self.rfile.read(int(self.headers["Content-Length"]))
            body = (
                chunk({"role": "assistant", "content": "Hallo"}) + chunk({}, "stop")
                + "data: [DONE]\n\n"
            ).encode()  # fmt: skip
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), KeepAlive)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        local = settings.model_copy(
            update={"ollama_base_url": f"http://127.0.0.1:{server.server_port}/v1"}
        )
        service = build_service(local, AuditLog(local.audit_db_path), model_name="m")

        async def turn() -> str:
            return "".join(
                [e.text async for e in service.stream("Hi", channel="cli", session_id="s")
                 if isinstance(e, TextDelta)]
            )  # fmt: skip

        assert [asyncio.run(turn()) for _ in range(3)] == ["Hallo"] * 3  # je Runde neue Loop
    finally:
        server.shutdown()
        server.server_close()
