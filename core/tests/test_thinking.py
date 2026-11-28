"""Nachdenken (Thinking) der lokalen Modelle: Standard aus, per Einstellung einschaltbar."""

import json
from pathlib import Path

import httpx2
import pytest
from openai import AsyncOpenAI
from pydantic_ai import Agent

from jarvis_core.agent import service
from jarvis_core.config import Settings

pytestmark = pytest.mark.anyio


def _client_recording(bodies: list[dict[str, object]]) -> AsyncOpenAI:
    def handler(request: httpx2.Request) -> httpx2.Response:
        bodies.append(json.loads(request.content))
        return httpx2.Response(
            200,
            json={
                "id": "x",
                "object": "chat.completion",
                "created": 0,
                "model": "m",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": "ok"},
                    }
                ],
            },
        )

    http = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    return AsyncOpenAI(base_url="http://127.0.0.1:11434/v1", api_key="ollama", http_client=http)


@pytest.mark.parametrize(("thinking", "expected"), [(False, "none"), (True, None)])
async def test_reasoning_effort_follows_setting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, thinking: bool, expected: str | None
) -> None:
    bodies: list[dict[str, object]] = []

    def fake_client(_settings: Settings) -> AsyncOpenAI:
        return _client_recording(bodies)

    monkeypatch.setattr(service, "_ollama_client", fake_client)
    settings = Settings(data_dir=tmp_path, model_thinking=thinking)
    model = service._ollama_model("qwen3.5:4b", settings)  # pyright: ignore[reportPrivateUsage]
    result = await Agent(model).run("hallo")
    assert result.output == "ok"
    assert bodies[0].get("reasoning_effort") == expected


def test_thinking_is_off_by_default(tmp_path: Path) -> None:
    assert Settings(data_dir=tmp_path).model_thinking is False
