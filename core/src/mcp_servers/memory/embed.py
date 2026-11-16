"""Embeddings vom lokalen Ollama (`/v1/embeddings`). Nur Rechner im eigenen Netz (ADR-003)."""

import ipaddress
import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable

DEFAULT_URL = "http://127.0.0.1:11434/v1"
DEFAULT_MODEL = "nomic-embed-text"
TIMEOUT_SECONDS = 20.0
MAX_BATCH = 32

Embedder = Callable[[list[str]], list[list[float]]]


class EmbedError(Exception):
    """Das Embedding-Modell ist nicht erreichbar oder lieferte Unbrauchbares."""


def is_local_url(url: str) -> bool:
    """Nur http(s) zu Loopback, privaten oder Tailnet-Adressen und Rechnernamen ohne Domain."""
    parts = urllib.parse.urlsplit(url)
    host = (parts.hostname or "").lower()
    if parts.scheme not in ("http", "https") or not host:
        return False
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return host == "localhost" or "." not in host or host.endswith((".ts.net", ".local"))
    return ip.is_loopback or ip.is_private or ip in ipaddress.ip_network("100.64.0.0/10")


def make_embedder(base_url: str = DEFAULT_URL, model: str = DEFAULT_MODEL) -> Embedder:
    """Funktion, die Texte in Vektoren verwandelt. Wirft EmbedError statt anderer Fehler."""
    if not is_local_url(base_url):
        raise ValueError(f"Embeddings nur von einem Rechner im eigenen Netz, nicht von {base_url}.")
    # Ohne System-Proxy: Texte aus dem Gedächtnis gehen nie über einen Proxy.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def embed(texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), MAX_BATCH):
            batch = texts[start : start + MAX_BATCH]
            request = urllib.request.Request(  # noqa: S310
                f"{base_url.rstrip('/')}/embeddings",
                data=json.dumps({"model": model, "input": batch}).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                with opener.open(request, timeout=TIMEOUT_SECONDS) as response:
                    data = json.loads(response.read())["data"]
                got = [list(map(float, item["embedding"])) for item in data]
            except urllib.error.HTTPError as exc:
                raise EmbedError(
                    f"Das Embedding-Modell '{model}' antwortet mit {exc.code}."
                ) from None
            except (OSError, TimeoutError, ValueError, KeyError, TypeError) as exc:
                raise EmbedError("Das Embedding-Modell ist nicht erreichbar.") from exc
            if len(got) != len(batch) or any(not v for v in got):
                raise EmbedError("Das Embedding-Modell lieferte eine unerwartete Antwort.")
            vectors += got
        return vectors

    return embed
