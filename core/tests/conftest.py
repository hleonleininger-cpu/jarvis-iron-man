import json
import threading
from collections.abc import Callable, Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from jarvis_core.config import Settings


@pytest.fixture(autouse=True)
def isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keine jarvis.toml / JARVIS_*-Variablen des Entwicklers im Test."""
    import os

    for key in list(os.environ):
        if key.startswith("JARVIS_"):
            monkeypatch.delenv(key)
    monkeypatch.chdir(tmp_path)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(data_dir=tmp_path / "data")


Responder = Callable[[str], tuple[int, dict[str, object]]]


@pytest.fixture
def fake_server() -> Iterator[Callable[[Responder], str]]:
    """Startet einen Mini-HTTP-Server (Ollama-Attrappe); gibt die Basis-URL zurück."""
    servers: list[HTTPServer] = []

    def start(responder: Responder) -> str:
        class Handler(BaseHTTPRequestHandler):
            def _reply(self) -> None:
                status, body = responder(self.path)
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self) -> None:  # noqa: N802
                self._reply()

            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("Content-Length", 0))
                self.rfile.read(length)
                self._reply()

            def log_message(self, format: str, *args: object) -> None:
                pass

        server = HTTPServer(("127.0.0.1", 0), Handler)
        servers.append(server)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return f"http://127.0.0.1:{server.server_port}"

    yield start
    for server in servers:
        server.shutdown()
        server.server_close()


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
