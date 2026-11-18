"""Befehle `jarvis voice ...`. Die schweren Sprachpakete werden erst hier und nur bei Bedarf
geladen; der Core läuft ohne sie. Block A: `status` und `text` (Gesprächslogik ohne Mikrofon)."""

import asyncio
import contextlib
import importlib.util
import sys
from collections.abc import AsyncIterator

import httpx2
import typer

from jarvis_core.config import Settings
from jarvis_core.secrets import SecretsError, read_secret_field
from jarvis_voice.core_client import HttpCoreClient
from jarvis_voice.protocols import State
from jarvis_voice.session import Parts, VoiceSession
from jarvis_voice.turn import EnergyVad, SilenceTurnDetector

# Pakete der Gruppe `voice` (Import-Name -> Zweck); fehlt eines, sagt `status` es.
PACKAGES = {
    "numpy": "Signalverarbeitung",
    "sounddevice": "Mikrofon und Lautsprecher",
    "onnxruntime": "Modelle ausführen",
    "openwakeword": "Wake Word (nur Windows)",
    "faster_whisper": "Spracherkennung",
    "piper": "Sprachausgabe",
    "sherpa_onnx": "Sprecherkennung",
    "pynput": "Tastenkürzel und Diktat",
}


class ConsoleIndicator:
    """Zeigt jeden Zustandswechsel in der Konsole (Mikrofon-Anzeige, Masterauftrag §6)."""

    def show(self, state: State) -> None:
        typer.echo(f"● {state.value}", err=True)

    def cue(self, name: str) -> None:
        del name  # Hinweistöne kommen mit der Audio-Ausgabe (Block B)


class PrintTts:
    """Textmodus: statt zu sprechen wird der Satz ausgegeben."""

    async def synthesize(
        self, text: str, language: str, whisper: bool
    ) -> AsyncIterator[tuple[bytes, int]]:
        suffix = " (flüsternd)" if whisper else ""
        typer.echo(f"JARVIS [{language}]{suffix}: {text}")
        return
        yield b"", 0  # macht daraus einen Generator


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


class TextStt:
    async def transcribe(self, pcm: bytes) -> object:  # im Textmodus nie aufgerufen
        raise RuntimeError("Der Textmodus hat keine Spracherkennung.")


def installed_packages() -> dict[str, bool]:
    return {name: importlib.util.find_spec(name) is not None for name in PACKAGES}


def core_client(settings: Settings) -> HttpCoreClient:
    token: str | None = None
    with contextlib.suppress(SecretsError):
        token = read_secret_field("api_token", "token")
    return HttpCoreClient(settings.voice.core_url, token)


def status(settings: Settings) -> None:
    config = settings.voice
    typer.echo(
        f"Sprachclient: {'eingeschaltet' if config.enabled else 'aus (jarvis.toml [voice])'}"
    )
    typer.echo(f"Core-Adresse: {config.core_url}")
    typer.echo(
        f"Sprache: {config.language}, Unterbrechen: {config.barge_in}, "
        f"Folgefrage: {config.follow_up_seconds:g} s, Bestätigen per Stimme: "
        f"{'ja' if config.confirm_by_voice else 'nein'}"
    )
    try:
        reply = httpx2.get(f"{config.core_url}/health", timeout=3.0, trust_env=False)
        typer.echo(f"Core: erreichbar ({reply.status_code})")
    except httpx2.HTTPError:
        typer.echo("Core: nicht erreichbar (läuft `jarvis serve`?)")
    typer.echo("Pakete (uv sync --extra voice):")
    for name, present in installed_packages().items():
        typer.echo(f"  {'ja  ' if present else 'fehlt'} {name}: {PACKAGES[name]}")


def text_mode(settings: Settings) -> None:
    """Gesprächslogik ohne Mikrofon: jede eingetippte Zeile gilt als gesprochene Äußerung. So
    lässt sich der Weg zum Core (Kanal "voice", kurze Antworten, Folgefragen) ausprobieren."""
    config = settings.voice
    client = core_client(settings)

    async def loop() -> None:
        session = VoiceSession(
            Parts(
                NoSource(), NullSink(), NeverWake(), EnergyVad(),
                SilenceTurnDetector(config.end_silence_seconds), TextStt(),  # pyright: ignore[reportArgumentType]
                PrintTts(), client, ConsoleIndicator(),
            ),
            config,
        )  # fmt: skip
        typer.echo(
            "Textmodus: Zeile eingeben (Strg+D beendet). Befehle: stopp, gute nacht, flüstere."
        )
        try:
            while line := await asyncio.to_thread(sys.stdin.readline):
                if line.strip():
                    await session.handle_text(line.strip())
                    if session.state is State.SLEEP:
                        typer.echo("(Schlafmodus: im Textmodus ohne Wirkung, weiter mit Eingabe)")
                        session.state = State.WAITING
        finally:
            await client.aclose()
            await session.shutdown()

    asyncio.run(loop())
