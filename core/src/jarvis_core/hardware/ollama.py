"""Prüft, ob Ollama läuft und welche Modelle installiert sind (nativer /api/tags)."""

import urllib.error
import urllib.request
from dataclasses import dataclass, field

from pydantic import BaseModel, ValidationError


class _Tag(BaseModel):
    name: str


class _Tags(BaseModel):
    models: list[_Tag] = []


@dataclass(frozen=True)
class OllamaStatus:
    running: bool
    models: list[str] = field(default_factory=lambda: [])
    error: str | None = None


# Ollama ist lokal: nie über einen System-Proxy ansprechen.
_NO_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _native_root(base_url: str) -> str:
    root = base_url.rstrip("/")
    return root.removesuffix("/v1")


def check_ollama(base_url: str, timeout: float = 2.0) -> OllamaStatus:
    url = f"{_native_root(base_url)}/api/tags"
    try:
        with _NO_PROXY.open(url, timeout=timeout) as resp:  # noqa: S310
            body = resp.read()
        tags = _Tags.model_validate_json(body)
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        return OllamaStatus(running=False, error=str(exc))
    except ValidationError:
        return OllamaStatus(running=False, error="Unerwartete Antwort von Ollama")
    return OllamaStatus(running=True, models=[t.name for t in tags.models])


def is_installed(model: str, installed: list[str]) -> bool:
    candidates = {model}
    if ":" not in model:
        candidates.add(f"{model}:latest")
    return any(name in candidates for name in installed)
