"""`jarvis voice run`: baut die Sitzung aus den echten Engines und lässt sie laufen."""

import asyncio
import contextlib
import sys
from pathlib import Path

from jarvis_core.config import Settings
from jarvis_voice.cli_parts import ConsoleIndicator, NoSource, NullSink, core_client
from jarvis_voice.engines import MissingPackage
from jarvis_voice.models import ModelError, load_registry, model_path
from jarvis_voice.protocols import AudioSink, AudioSource
from jarvis_voice.session import Parts, VoiceSession


def models_dir(settings: Settings) -> Path:
    path = settings.voice.models_dir
    return path if path.is_absolute() else settings.data_dir / path


class AudioIndicator(ConsoleIndicator):
    """Konsolenanzeige plus kurze Hinweistöne über den Lautsprecher."""

    def __init__(self, sink: object) -> None:
        self._sink = sink

    def cue(self, name: str) -> None:
        beep = getattr(self._sink, "beep", None)
        if callable(beep):
            beep(name)


def build_parts(settings: Settings, with_audio: bool = True) -> Parts:
    """Setzt alle Engines zusammen. Wirft ModelError oder MissingPackage mit deutscher Meldung."""
    from jarvis_voice.engines.piper_tts import PiperTts
    from jarvis_voice.engines.silero import SileroVad
    from jarvis_voice.engines.smart_turn import SmartTurnDetector
    from jarvis_voice.engines.wake_openwakeword import OpenWakeWordDetector
    from jarvis_voice.engines.whisper import FasterWhisperStt

    config = settings.voice
    directory = models_dir(settings)
    registry = load_registry()
    whisper_key = f"whisper_{config.stt_model}"
    if whisper_key in registry:
        whisper_dir = model_path(directory, registry[whisper_key])
    else:  # eigenes Modell: Ordner models_dir/whisper-<name> (CTranslate2-Format)
        whisper_dir = directory / f"whisper-{config.stt_model}"
        if not (whisper_dir / "model.bin").is_file():
            raise ModelError(f"Das Modell '{config.stt_model}' liegt nicht in {whisper_dir}.")
    wake = registry["wake_word"]
    if with_audio:
        from jarvis_voice.engines.audio_sounddevice import SoundDeviceSink, SoundDeviceSource

        source: AudioSource = SoundDeviceSource(config.mic_device)
        sink: AudioSink = SoundDeviceSink(config.speaker_device)
    else:  # Funktionsprobe und Messung brauchen keine Audiogeräte
        source, sink = NoSource(), NullSink()
    return Parts(
        source=source,
        sink=sink,
        wake=OpenWakeWordDetector(
            model_path(directory, wake, "hey_jarvis_v0.1.onnx"),
            model_path(directory, wake, "melspectrogram.onnx"),
            model_path(directory, wake, "embedding_model.onnx"),
            config.wake_threshold,
        ),
        vad=SileroVad(model_path(directory, registry["silero_vad"], "silero_vad.onnx")),
        turn=SmartTurnDetector(
            model_path(directory, registry["smart_turn"], "smart-turn-v3.2-cpu.onnx"),
            max_silence=max(config.end_silence_seconds, 0.5) + 1.2,
        ),
        stt=FasterWhisperStt(
            whisper_dir,
            config.stt_device,
            "float16" if config.stt_device == "cuda" else "int8",
            None if config.language == "auto" else config.language,
        ),  # fmt: skip
        tts=PiperTts(
            {
                "de": model_path(directory, registry["piper_de"], f"{config.tts_voice_de}.onnx"),
                "en": model_path(directory, registry["piper_en"], f"{config.tts_voice_en}.onnx"),
            }
        ),
        core=core_client(settings),
        indicator=AudioIndicator(sink),
    )


async def run(settings: Settings, echo: object) -> None:
    del echo
    parts = build_parts(settings)
    session = VoiceSession(parts, settings.voice)
    # Modelle vorab laden, damit die erste Antwort nicht auf das Laden warten muss.
    await asyncio.to_thread(parts.stt.warm_up)  # type: ignore[attr-defined]
    await asyncio.to_thread(parts.tts.warm_up)  # type: ignore[attr-defined]
    print(
        'Bereit. Sage "Hey Jarvis". Beenden mit Strg+C, schlafen mit "Gute Nacht".', file=sys.stderr
    )
    try:
        await session.run()
    finally:
        with contextlib.suppress(Exception):
            await parts.core.aclose()  # type: ignore[attr-defined]


def main(settings: Settings) -> None:
    try:
        asyncio.run(run(settings, None))
    except (ModelError, MissingPackage, ValueError) as exc:
        print(f"Fehler: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
    except KeyboardInterrupt:
        print("Beendet.", file=sys.stderr)
