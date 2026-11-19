"""Bausteine, die `cli.py` (Textmodus) und `run.py` (echtes Mikrofon) gemeinsam nutzen."""

import contextlib
from collections.abc import AsyncIterator

import typer

from jarvis_core.config import Settings
from jarvis_core.secrets import SecretsError, read_secret_field
from jarvis_voice.core_client import HttpCoreClient
from jarvis_voice.protocols import State


class ConsoleIndicator:
    """Zeigt jeden Zustandswechsel in der Konsole (Mikrofon-Anzeige, Masterauftrag §6)."""

    def show(self, state: State) -> None:
        typer.echo(f"● {state.value}", err=True)

    def cue(self, name: str) -> None:
        del name  # Hinweistöne kommen mit der Audio-Ausgabe (Block B)


def core_client(settings: Settings) -> HttpCoreClient:
    token: str | None = None
    with contextlib.suppress(SecretsError):
        token = read_secret_field("api_token", "token")
    return HttpCoreClient(settings.voice.core_url, token)


class NullSink:
    async def play(self, pcm: bytes, sample_rate: int) -> None:
        del pcm, sample_rate

    def stop(self) -> None:
        pass


class NoSource:
    async def open(self) -> None:
        pass

    def close(self) -> None:
        pass

    async def frames(self) -> AsyncIterator[bytes]:
        return
        yield b""


class NeverWake:
    def process(self, frame: bytes) -> bool:
        del frame
        return False

    def reset(self) -> None:
        pass
