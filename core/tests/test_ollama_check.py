from collections.abc import Callable

import pytest

from jarvis_core.hardware import check_ollama, is_installed

Starter = Callable[[Callable[[str], tuple[int, dict[str, object]]]], str]


def test_running_with_models(fake_server: Starter) -> None:
    url = fake_server(lambda _p: (200, {"models": [{"name": "qwen3.5:4b"}]}))
    status = check_ollama(f"{url}/v1")
    assert status.running
    assert status.models == ["qwen3.5:4b"]


def test_not_reachable() -> None:
    status = check_ollama("http://127.0.0.1:1/v1", timeout=0.5)
    assert not status.running
    assert status.models == []


def test_is_installed() -> None:
    assert is_installed("qwen3.5:4b", ["qwen3.5:4b"])
    assert is_installed("llama", ["llama:latest"])
    assert not is_installed("qwen3.5:9b", ["qwen3.5:4b"])


def test_check_ignores_system_proxy(fake_server: Starter, monkeypatch: pytest.MonkeyPatch) -> None:
    url = fake_server(lambda _p: (200, {"models": [{"name": "qwen3.5:4b"}]}))
    monkeypatch.setenv("HTTP_PROXY", "http://10.255.255.1:9")
    monkeypatch.setenv("NO_PROXY", "")
    monkeypatch.setenv("no_proxy", "")
    assert check_ollama(f"{url}/v1").models == ["qwen3.5:4b"]
