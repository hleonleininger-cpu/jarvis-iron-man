"""Modellverzeichnis und `jarvis voice setup`: Prüfsummen, Download nur auf ausdrücklichen Befehl."""

import hashlib
import http.server
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest
from typer.testing import CliRunner

from jarvis_core.cli import main as cli_main
from jarvis_voice import setup as voice_setup
from jarvis_voice.models import (
    ModelError,
    fetch,
    load_registry,
    model_path,
    sha256_of,
    verify,
)

PAYLOAD = b"Modell-Inhalt " * 1000


class Handler(http.server.BaseHTTPRequestHandler):
    payload = PAYLOAD

    def do_GET(self) -> None:  # noqa: N802
        self.send_response(200)
        self.send_header("Content-Length", str(len(self.payload)))
        self.end_headers()
        self.wfile.write(self.payload)

    def log_message(self, format: str, *args: object) -> None:
        pass


@pytest.fixture
def server() -> Iterator[str]:
    Handler.payload = PAYLOAD
    httpd = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def registry_text(url: str, sha: str | None = None, size: int | None = None) -> str:
    sha = sha or hashlib.sha256(PAYLOAD).hexdigest()
    size = size if size is not None else len(PAYLOAD)
    return f"""
[demo]
zweck = "Testmodell"
lizenz = "MIT"
ordner = "demo"
dateien = [
  {{ name = "m.bin", url = "{url}/m.bin", sha256 = "{sha}", bytes = {size} }},
]
"""


def test_packaged_registry_is_complete_and_well_formed() -> None:
    registry = load_registry()
    assert {"silero_vad", "smart_turn", "wake_word", "whisper_base", "piper_en", "piper_de"} <= set(
        registry
    )
    for entry in registry.values():
        assert entry.lizenz and entry.zweck and entry.files
        for file in entry.files:
            assert len(file.sha256) == 64 and int(file.sha256, 16) >= 0
            assert file.url.startswith("https://") and file.bytes > 0
    assert registry["whisper_base"].folder == "whisper-base"
    assert registry["piper_de"].total_bytes > 60_000_000


def test_missing_models_are_reported_with_the_setup_hint(tmp_path: Path) -> None:
    entry = load_registry()["silero_vad"]
    assert verify(tmp_path, entry) == ["silero_vad.onnx: fehlt"]
    with pytest.raises(ModelError, match="jarvis voice setup"):
        model_path(tmp_path, entry, "silero_vad.onnx")


def test_fetch_downloads_verifies_and_skips_what_is_there(tmp_path: Path, server: str) -> None:
    entry = load_registry(registry_text(server))["demo"]
    seen: list[tuple[str, int, int]] = []
    assert fetch(tmp_path, entry, lambda n, d, t: seen.append((n, d, t))) == 1
    assert (tmp_path / "demo" / "m.bin").read_bytes() == PAYLOAD
    assert seen and seen[-1][1] == seen[-1][2] == len(PAYLOAD)
    assert verify(tmp_path, entry, deep=True) == []
    assert model_path(tmp_path, entry, "m.bin") == tmp_path / "demo" / "m.bin"
    assert fetch(tmp_path, entry) == 0  # nichts erneut geladen


def test_wrong_checksum_is_rejected_and_nothing_is_kept(tmp_path: Path, server: str) -> None:
    entry = load_registry(registry_text(server, sha="0" * 64))["demo"]
    with pytest.raises(ModelError, match="Prüfsumme"):
        fetch(tmp_path, entry)
    assert not list((tmp_path / "demo").glob("*"))  # weder Datei noch .part


def test_wrong_size_is_rejected(tmp_path: Path, server: str) -> None:
    entry = load_registry(registry_text(server, size=5))["demo"]
    with pytest.raises(ModelError):
        fetch(tmp_path, entry)


def test_unreachable_server_gives_a_german_message(tmp_path: Path) -> None:
    entry = load_registry(registry_text("http://127.0.0.1:9"))["demo"]
    with pytest.raises(ModelError, match="Download"):
        fetch(tmp_path, entry)


def test_damaged_file_is_detected_and_replaced(tmp_path: Path, server: str) -> None:
    entry = load_registry(registry_text(server))["demo"]
    fetch(tmp_path, entry)
    path = tmp_path / "demo" / "m.bin"
    path.write_bytes(b"X" * len(PAYLOAD))  # gleiche Größe, anderer Inhalt
    assert verify(tmp_path, entry, deep=True) == ["m.bin: Prüfsumme stimmt nicht"]
    assert fetch(tmp_path, entry) == 1
    assert sha256_of(path) == hashlib.sha256(PAYLOAD).hexdigest()


def test_setup_asks_before_downloading(
    tmp_path: Path, server: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = load_registry(registry_text(server))
    monkeypatch.setattr(voice_setup, "load_registry", lambda: registry)
    lines: list[str] = []
    declined = voice_setup.run_setup(tmp_path, ("demo",), lines.append, lambda _q: False)
    assert declined is False and not (tmp_path / "demo").exists()  # ohne Ja nichts geladen
    assert any("Testmodell" in line for line in lines) and any(
        "kostenlos" in line for line in lines
    )
    ok = voice_setup.run_setup(tmp_path, ("demo",), lines.append, lambda _q: True)
    assert ok is True and (tmp_path / "demo" / "m.bin").exists()
    assert voice_setup.run_setup(tmp_path, ("demo",), lines.append, lambda _q: False) is True


def test_setup_rejects_unknown_group(tmp_path: Path) -> None:
    with pytest.raises(ModelError, match="Unbekanntes Modell"):
        voice_setup.plan(tmp_path, ("gibt-es-nicht",))


def test_setup_cli_reports_failure_with_exit_code(
    tmp_path: Path, server: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    bad = load_registry(registry_text(server, sha="1" * 64))
    monkeypatch.setattr(voice_setup, "load_registry", lambda: bad)
    from jarvis_core.config import Settings

    def fixed_dir(_settings: Settings) -> Path:
        return tmp_path

    monkeypatch.setattr("jarvis_voice.run.models_dir", fixed_dir)
    result = CliRunner().invoke(cli_main.app, ["voice", "setup", "--nur", "demo", "--ja"])
    assert result.exit_code == 1 and "FEHLER" in result.output
