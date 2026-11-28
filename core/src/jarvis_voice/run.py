"""`jarvis voice run`: baut die Sitzung aus den echten Engines und lässt sie laufen."""

import asyncio
import contextlib
import sys
from pathlib import Path

from jarvis_core.config import Settings
from jarvis_voice.clap import ClapDetector
from jarvis_voice.cli_parts import ConsoleIndicator, NeverWake, NoSource, NullSink, core_client
from jarvis_voice.engines import MissingPackage
from jarvis_voice.hotkeys import HotkeyListener
from jarvis_voice.models import ModelEntry, ModelError, load_registry, model_path, verify
from jarvis_voice.protocols import (
    AudioSink,
    AudioSource,
    SpeakerGate,
    TextTyper,
    WakeWordDetector,
    WindowWatcher,
)
from jarvis_voice.session import Parts, VoiceSession
from jarvis_voice.speaker import ProfileError, load_profile, profile_path


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


def build_speaker(
    settings: Settings, directory: Path, registry: dict[str, ModelEntry]
) -> SpeakerGate:
    """Sprecherprüfung (M2.9): Modell und angelerntes Profil müssen da sein."""
    from jarvis_voice.engines.speaker_sherpa import SherpaEmbedder, SherpaSpeakerGate

    model = model_path(directory, registry["speaker_titanet"], "nemo_en_titanet_small.onnx")
    try:
        profile, name = load_profile(profile_path(settings.data_dir))
    except ProfileError as exc:
        raise ModelError(str(exc)) from None
    if (
        name != model.name
    ):  # das Profil stammt von einem anderen Modell: Vektoren sind nicht vergleichbar
        raise ModelError(
            f"Das Sprecherprofil gehört zum Modell {name}, geladen ist {model.name}. "
            "Neu anlernen mit: jarvis voice anlernen"
        )
    return SherpaSpeakerGate(SherpaEmbedder(model), profile, settings.voice.speaker_threshold)


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
        needed = ("model.bin", "config.json", "tokenizer.json", "vocabulary.txt")
        if not all((whisper_dir / name).is_file() for name in needed):
            raise ModelError(f"Das Modell '{config.stt_model}' liegt nicht in {whisper_dir}.")
    wake = registry["wake_word"]
    if with_audio:
        from jarvis_voice.engines.audio_sounddevice import SoundDeviceSink, SoundDeviceSource

        source: AudioSource = SoundDeviceSource(config.mic_device)
        sink: AudioSink = SoundDeviceSink(config.speaker_device)
    else:  # Funktionsprobe und Messung brauchen keine Audiogeräte
        source, sink = NoSource(), NullSink()
    try:
        detector: WakeWordDetector = OpenWakeWordDetector(
            model_path(directory, wake, "hey_jarvis_v0.1.onnx"),
            model_path(directory, wake, "melspectrogram.onnx"),
            model_path(directory, wake, "embedding_model.onnx"),
            config.wake_threshold,
        )
    except MissingPackage:
        if with_audio:
            raise
        detector = NeverWake()  # nur Funktionsprobe/Messung auf Systemen ohne das Wake-Word-Paket
    # Englische Stimme optional: fehlt sie, bleibt die deutsche (Fehler erst bei Englisch).
    voices = {"de": model_path(directory, registry["piper_de"], f"{config.tts_voice_de}.onnx")}
    if not verify(directory, registry["piper_en"]):
        voices["en"] = model_path(directory, registry["piper_en"], f"{config.tts_voice_en}.onnx")
    speaker = build_speaker(settings, directory, registry) if config.speaker_check else None
    typer: TextTyper | None = None
    watcher: WindowWatcher | None = None
    if with_audio:  # Tippen und Fensterwechsel nur im echten Betrieb
        try:
            from jarvis_voice.engines.typer_pynput import PynputTyper
            from jarvis_voice.engines.window_win import ForegroundWatcher

            typer, watcher = PynputTyper(), ForegroundWatcher()
        except MissingPackage:
            typer = None  # ohne pynput kein Diktat (und keine Tastenkürzel)
    return Parts(
        source=source,
        sink=sink,
        wake=detector,
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
        tts=PiperTts(voices),
        core=core_client(settings),
        indicator=AudioIndicator(sink),
        speaker=speaker,
        typer=typer,
        window=watcher,
        clap=ClapDetector(config.clap_threshold) if config.clap_wake and with_audio else None,
    )


def start_hotkeys(settings: Settings, session: VoiceSession) -> "HotkeyListener | None":
    """Globale Tastenkürzel (M2.13). Ohne pynput läuft der Client ohne sie weiter."""
    from jarvis_voice.hotkeys import HotkeyListener

    config = settings.voice
    actions = {
        config.hotkey_listen: session.trigger_listen,
        config.hotkey_mute: session.toggle_mute,
        config.hotkey_interrupt: session.request_interrupt,
        config.hotkey_dictation: session.toggle_dictation,
    }
    bindings = {combo: action for combo, action in actions.items() if combo}
    if not bindings:
        return None
    listener = HotkeyListener(bindings, asyncio.get_running_loop())
    try:
        listener.start()
    except MissingPackage as exc:
        print(f"Hinweis: Tastenkürzel sind aus ({exc})", file=sys.stderr)
        return None
    except Exception as exc:  # z. B. kein Zugriff auf die Tastatur (Berechtigung)
        print(f"Hinweis: Tastenkürzel sind aus ({type(exc).__name__}).", file=sys.stderr)
        return None
    return listener


async def run(settings: Settings, echo: object) -> None:
    del echo
    if not settings.voice.enabled:
        raise ValueError(
            "Der Sprachclient ist aus. In jarvis.toml unter [voice] enabled = true setzen."
        )
    parts = build_parts(settings)
    session = VoiceSession(parts, settings.voice)
    # Modelle vorab laden, damit die erste Antwort nicht auf das Laden warten muss.
    await asyncio.to_thread(parts.stt.warm_up)  # type: ignore[attr-defined]
    await asyncio.to_thread(parts.tts.warm_up)  # type: ignore[attr-defined]
    hotkeys = start_hotkeys(settings, session)
    print(
        'Bereit. Sage "Hey Jarvis". Beenden mit Strg+C, schlafen mit "Gute Nacht".', file=sys.stderr
    )
    try:
        await session.run()
    finally:
        if hotkeys is not None:
            hotkeys.stop()
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
