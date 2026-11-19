"""Befehle `jarvis voice ...`. Die schweren Sprachpakete werden erst hier und nur bei Bedarf
geladen; der Core läuft ohne sie. Block A: `status` und `text` (Gesprächslogik ohne Mikrofon)."""

import asyncio
import importlib.util
import sys
from collections.abc import AsyncIterator

import httpx2
import typer

from jarvis_core.config import Settings
from jarvis_voice.cli_parts import ConsoleIndicator, NeverWake, NoSource, NullSink, core_client
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


class PrintTts:
    """Textmodus: statt zu sprechen wird der Satz ausgegeben."""

    async def synthesize(
        self, text: str, language: str, whisper: bool
    ) -> AsyncIterator[tuple[bytes, int]]:
        suffix = " (flüsternd)" if whisper else ""
        typer.echo(f"JARVIS [{language}]{suffix}: {text}")
        return
        yield b"", 0  # macht daraus einen Generator


class TextStt:
    async def transcribe(self, pcm: bytes) -> object:  # im Textmodus nie aufgerufen
        raise RuntimeError("Der Textmodus hat keine Spracherkennung.")


def installed_packages() -> dict[str, bool]:
    return {name: importlib.util.find_spec(name) is not None for name in PACKAGES}


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


def setup_command(settings: Settings, only: list[str], yes: bool) -> None:
    from jarvis_voice.run import models_dir
    from jarvis_voice.setup import DEFAULT_KEYS, run_setup

    keys = tuple(only) if only else DEFAULT_KEYS
    ok = run_setup(
        models_dir(settings), keys, typer.echo,
        (lambda _q: True) if yes else (lambda q: typer.confirm(q, default=False)),
    )  # fmt: skip
    if not ok:
        raise typer.Exit(1)


def doctor_command(settings: Settings, deep: bool) -> None:
    from jarvis_voice.doctor import run_doctor

    checks = run_doctor(settings, deep)
    for check in checks:
        typer.echo(f"{'OK    ' if check.ok else 'FEHLT '} {check.name}: {check.detail}")
    if not all(c.ok for c in checks):
        raise typer.Exit(1)


def bench_command(settings: Settings, runs: int) -> None:
    from jarvis_voice.doctor import TARGET_FIRST_SOUND_SECONDS, estimate_first_sound, measure
    from jarvis_voice.engines import MissingPackage
    from jarvis_voice.models import ModelError

    try:
        stages = measure(settings, runs)
    except (ModelError, MissingPackage, ValueError) as exc:
        typer.echo(f"Fehler: {exc}", err=True)
        raise typer.Exit(1) from None
    for name, value in stages.items():
        typer.echo(f"{name:24} {value * 1000:9.1f} ms")
    total = estimate_first_sound(stages)
    seconds = stages.get("stt_aufnahme_sekunden", 0.0)
    if seconds:
        typer.echo(f"Erkennung: {stages.get('stt_je_aufnahme', 0.0) / seconds:.2f} x Echtzeit")
    if "core_erster_text" not in stages:
        typer.echo(
            "Hinweis: Der Core war nicht erreichbar (jarvis serve?). Die Zeit bis zum ersten Satz "
            "des Modells fehlt in der Schätzung; mit einem lokalen Modell kommen typisch 2 bis "
            "8 Sekunden dazu."
        )
    verdict = "erreicht" if total <= TARGET_FIRST_SOUND_SECONDS else "NICHT erreicht"
    typer.echo(
        f"Geschätzt vom Satzende bis zum ersten Ton: {total:.1f} s "
        f"(Ziel auf CPU: höchstens {TARGET_FIRST_SOUND_SECONDS:.0f} s) - {verdict}"
    )
