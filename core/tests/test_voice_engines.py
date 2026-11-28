"""Echte Engines des Sprachclients. Ohne die Pakete der Gruppe `voice` werden diese Tests
übersprungen; die Modelltests laufen nur, wenn JARVIS_VOICE_MODELS auf einen Ordner mit den
Modellen zeigt (nach `jarvis voice setup`). Mikrofon und Lautsprecher sind Attrappen."""

import asyncio
import os
import sys
import types
from pathlib import Path
from typing import Any

import pytest

np = pytest.importorskip("numpy")

from jarvis_voice import engines  # noqa: E402
from jarvis_voice.audio import silence, tone  # noqa: E402
from jarvis_voice.engines import MissingPackage, audio_sounddevice  # noqa: E402
from jarvis_voice.engines.audio_sounddevice import (  # noqa: E402
    SoundDeviceSink,
    SoundDeviceSource,
    find_device,
    list_devices,
)
from jarvis_voice.protocols import FRAME_SAMPLES, FRAME_SECONDS  # noqa: E402

pytestmark = pytest.mark.anyio

LOUD = tone(300, FRAME_SECONDS, level=0.5)
QUIET = silence(FRAME_SECONDS)


# ------------------------------------------------------------------------ fehlende Pakete
def test_missing_package_gives_install_hint() -> None:
    with pytest.raises(MissingPackage, match="uv sync --extra voice"):
        engines.need("gibt_es_nicht_xyz", "Test")


# -------------------------------------------------------------------- Audio (Attrappen)
class FakeStream:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.active = False
        self.closed = False
        self.aborted = 0
        self.written: list[int] = []
        self.on_write: Any = None

    def start(self) -> None:
        self.active = True

    def stop(self) -> None:
        self.active = False

    def close(self) -> None:
        self.closed = True

    def abort(self) -> None:
        self.aborted += 1
        self.active = False

    def write(self, samples: Any) -> None:
        self.written.append(len(samples))
        if self.on_write:
            self.on_write()


class FakeSd(types.SimpleNamespace):
    def __init__(self) -> None:
        super().__init__()
        self.inputs: list[FakeStream] = []
        self.outputs: list[FakeStream] = []

    def query_devices(self) -> list[dict[str, Any]]:
        return [
            {"name": "Mikrofon (USB Headset)", "max_input_channels": 1, "max_output_channels": 0},
            {
                "name": "Lautsprecher (USB Headset)",
                "max_input_channels": 0,
                "max_output_channels": 2,
            },
        ]

    def RawInputStream(self, **kwargs: Any) -> FakeStream:  # noqa: N802
        stream = FakeStream(**kwargs)
        self.inputs.append(stream)
        return stream

    def OutputStream(self, **kwargs: Any) -> FakeStream:  # noqa: N802
        stream = FakeStream(**kwargs)
        self.outputs.append(stream)
        return stream


@pytest.fixture
def fake_sd(monkeypatch: pytest.MonkeyPatch) -> FakeSd:
    sd = FakeSd()
    real_need = engines.need

    def need(module: str, why: str) -> Any:
        return sd if module == "sounddevice" else real_need(module, why)

    monkeypatch.setattr(audio_sounddevice, "need", need)
    return sd


def test_device_lookup_by_name_and_helpful_error(fake_sd: FakeSd) -> None:
    assert find_device(fake_sd, None, "input") is None
    assert find_device(fake_sd, "usb headset", "input") == 0
    assert find_device(fake_sd, "usb headset", "output") == 1
    with pytest.raises(ValueError, match="Vorhanden: Mikrofon"):
        find_device(fake_sd, "Bluetooth", "input")
    assert list_devices(fake_sd)[0] == (0, "Mikrofon (USB Headset)", 1, 0)


async def test_source_streams_frames_and_close_really_closes(fake_sd: FakeSd) -> None:
    source = SoundDeviceSource("Mikrofon")
    await source.open()
    stream = fake_sd.inputs[0]
    assert stream.active and stream.kwargs["samplerate"] == 16_000
    assert stream.kwargs["dtype"] == "int16" and stream.kwargs["blocksize"] == FRAME_SAMPLES
    assert stream.kwargs["device"] == 0
    stream.kwargs["callback"](LOUD, FRAME_SAMPLES, None, None)
    stream.kwargs["callback"](QUIET, FRAME_SAMPLES, None, None)
    frames = source.frames()
    assert await asyncio.wait_for(anext(frames), 2) == LOUD
    assert await asyncio.wait_for(anext(frames), 2) == QUIET
    source.close()
    assert stream.closed and not stream.active and not source.is_open  # Mikrofon wirklich zu
    with pytest.raises(StopAsyncIteration):
        await asyncio.wait_for(anext(frames), 2)


async def test_source_drops_old_frames_instead_of_lagging(fake_sd: FakeSd) -> None:
    source = SoundDeviceSource()
    await source.open()
    callback = fake_sd.inputs[0].kwargs["callback"]
    for i in range(audio_sounddevice.QUEUE_FRAMES + 20):
        callback(bytes([i % 256]) * 4, 2, None, None)
    await asyncio.sleep(0.05)
    assert source._queue.qsize() == audio_sounddevice.QUEUE_FRAMES  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
    source.close()


async def test_sink_plays_in_blocks_and_stop_cuts_off_immediately(fake_sd: FakeSd) -> None:
    sink = SoundDeviceSink("Lautsprecher")
    pcm = np.zeros(audio_sounddevice.BLOCK_FRAMES * 10, dtype=np.int16).tobytes()
    await sink.play(pcm, 22_050)
    stream = fake_sd.outputs[0]
    assert stream.kwargs["samplerate"] == 22_050 and stream.kwargs["device"] == 1
    assert stream.written == [audio_sounddevice.BLOCK_FRAMES] * 10
    stream.written.clear()
    stream.on_write = sink.stop  # während des ersten Blocks kommt der Stopp
    await sink.play(pcm, 22_050)
    assert len(stream.written) == 1 and stream.aborted == 1  # sofort verstummt
    stream.on_write = None
    await sink.play(pcm, 22_050)  # danach geht Ausgabe wieder
    assert len(stream.written) > 5


async def test_beep_plays_a_short_tone(fake_sd: FakeSd) -> None:
    sink = SoundDeviceSink()
    sink.beep("wake")
    await asyncio.sleep(0.1)
    assert fake_sd.outputs and fake_sd.outputs[0].written


# ------------------------------------------------------------------------ Wake Word
def test_wake_detector_buffers_frames_into_80ms_chunks(monkeypatch: pytest.MonkeyPatch) -> None:
    from jarvis_voice.engines import wake_openwakeword as module

    calls: list[int] = []
    scores = iter([0.1, 0.2, 0.9, 0.1, 0.1])

    class FakeModel:
        def __init__(self, **kwargs: Any) -> None:
            assert kwargs["wakeword_model_paths"] == ["w.onnx"]
            assert kwargs["melspec_onnx_model_path"] == "m.onnx"
            assert kwargs["embedding_onnx_model_path"] == "e.onnx"

        def predict(self, chunk: Any) -> dict[str, float]:
            calls.append(len(chunk))
            return {"hey_jarvis": next(scores)}

        def reset(self) -> None:
            calls.append(-1)

    real_need = engines.need
    fake_module = types.SimpleNamespace(Model=FakeModel)

    def need(name: str, why: str) -> Any:
        return fake_module if name == "openwakeword.model" else real_need(name, why)

    monkeypatch.setattr(module, "need", need)
    detector = module.OpenWakeWordDetector(Path("w.onnx"), Path("m.onnx"), Path("e.onnx"), 0.5)
    hits = [detector.process(LOUD) for _ in range(10)]  # 10 x 512 = 5120 Samples = 4 Stücke
    assert calls[:4] == [1280, 1280, 1280, -1]  # drei Stücke, dann Treffer und Zurücksetzen
    assert any(hits) and detector.last_score >= 0.5
    assert hits.index(True) == 7  # 3 x 1280 Samples sind nach 7,5 Frames à 512 Samples beisammen


# ------------------------------------------------------------------- Satzende-Logik
def _turn(monkeypatch: pytest.MonkeyPatch, probability: float) -> Any:
    from jarvis_voice.engines.smart_turn import SmartTurnDetector

    detector = object.__new__(SmartTurnDetector)
    detector._np = np  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
    detector.configure(0.25, 0.25, 1.0, 0.5)
    detector.reset()
    detector.complete_probability = lambda: probability  # type: ignore[method-assign]
    return detector


def test_smart_turn_waits_for_the_model_then_ends(monkeypatch: pytest.MonkeyPatch) -> None:
    done = _turn(monkeypatch, 0.9)
    assert not done.feed(LOUD, True)
    ends = [done.feed(QUIET, False) for _ in range(20)]
    assert ends.index(True) == 7  # 0,25 s Stille = 8 Frames, beim 8. wird geprüft
    assert done.last_probability == 0.9


def test_smart_turn_keeps_waiting_when_the_sentence_is_unfinished(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unfinished = _turn(monkeypatch, 0.1)
    unfinished.feed(LOUD, True)
    ends = [unfinished.feed(QUIET, False) for _ in range(40)]
    assert ends.index(True) == 30  # erst nach 1,0 s Stille (max_silence), nicht früher
    unfinished.reset()
    assert not unfinished.feed(QUIET, False)  # Stille ohne Sprache beendet nichts


def test_smart_turn_ignores_silence_before_speech(monkeypatch: pytest.MonkeyPatch) -> None:
    detector = _turn(monkeypatch, 0.9)
    assert not any(detector.feed(QUIET, False) for _ in range(100))
    assert len(detector._audio) == 0  # noqa: SLF001  # Stille vorab wird nicht aufgehoben


# --------------------------------------------------------------------- echte Modelle
MODELS = os.environ.get("JARVIS_VOICE_MODELS")
needs_models = pytest.mark.skipif(not MODELS, reason="JARVIS_VOICE_MODELS nicht gesetzt")


@pytest.fixture(scope="module")
def parts() -> Any:
    pytest.importorskip("faster_whisper")
    pytest.importorskip("piper")
    pytest.importorskip("onnxruntime")
    if sys.platform != "win32":
        pytest.importorskip("openwakeword")
    from jarvis_core.config import Settings
    from jarvis_core.config.settings import VoiceConfig
    from jarvis_voice.run import build_parts

    settings = Settings(voice=VoiceConfig(models_dir=Path(MODELS or ".")))
    return build_parts(settings, with_audio=False)


async def _speech(parts: Any, text: str, language: str) -> bytes:
    from jarvis_voice.doctor import resample_16k

    audio = b""
    async for chunk, rate in parts.tts.synthesize(text, language, False):
        audio += resample_16k(chunk, rate)
    return audio


@needs_models
async def test_tts_speaks_both_languages_and_whisper_is_quieter(parts: Any) -> None:
    from jarvis_voice.audio import rms

    loud = await _speech(parts, "Guten Abend, Master.", "de")
    assert len(loud) > 16_000
    quiet = b""
    async for chunk, _rate in parts.tts.synthesize("Guten Abend, Master.", "de", True):
        quiet += chunk
    assert rms(quiet) < rms(loud)
    assert len(await _speech(parts, "Good evening, sir.", "en")) > 16_000


@needs_models
async def test_stt_recognizes_own_voice_and_the_language(parts: Any) -> None:
    german = await parts.stt.transcribe(
        await _speech(parts, "Wie wird das Wetter morgen in Dortmund?", "de")
    )
    english = await parts.stt.transcribe(
        await _speech(parts, "What is the weather like tomorrow in London?", "en")
    )
    assert "morgen" in german.text.casefold() and german.language == "de"
    assert "tomorrow" in english.text.casefold() and english.language == "en"


@needs_models
async def test_vad_separates_speech_from_silence(parts: Any) -> None:
    audio = await _speech(parts, "Wie wird das Wetter morgen in Dortmund?", "de")
    step = FRAME_SAMPLES * 2
    frames = [audio[i : i + step] for i in range(0, len(audio) - step, step)]
    parts.vad.reset()
    speech = sum(parts.vad.is_speech(f) for f in frames)
    parts.vad.reset()
    assert speech > len(frames) // 3
    assert not any(parts.vad.is_speech(QUIET) for _ in range(20))


@needs_models
async def test_smart_turn_runs_and_ends_by_silence_at_the_latest(parts: Any) -> None:
    audio = await _speech(parts, "Wie wird das Wetter morgen in Dortmund?", "de")
    step = FRAME_SAMPLES * 2
    parts.turn.reset()
    for i in range(0, len(audio) - step, step):
        assert not parts.turn.feed(audio[i : i + step], True)
    probability = parts.turn.complete_probability()
    assert 0.0 <= probability <= 1.0  # ob das Urteil stimmt, zeigt erst echte Sprache am PC
    ended = False
    for _ in range(200):
        if parts.turn.feed(QUIET, False):
            ended = True
            break
    assert ended


@needs_models
async def test_wake_word_does_not_fire_on_ordinary_speech(parts: Any) -> None:
    audio = await _speech(parts, "Bitte schalte das Licht in der Küche ein.", "de")
    step = FRAME_SAMPLES * 2
    hits = [parts.wake.process(audio[i : i + step]) for i in range(0, len(audio) - step, step)]
    assert not any(hits)
    assert 0.0 <= parts.wake.last_score <= 1.0


@needs_models
def test_doctor_function_probe_passes_with_models() -> None:
    from jarvis_core.config import Settings
    from jarvis_core.config.settings import VoiceConfig
    from jarvis_voice.doctor import function_checks

    checks = function_checks(Settings(voice=VoiceConfig(models_dir=Path(MODELS or "."))))
    assert all(c.ok for c in checks), [c for c in checks if not c.ok]
    assert {"Sprachausgabe", "Spracherkennung (eigene Stimme)"} <= {c.name for c in checks}


@needs_models
def test_bench_reports_stages_and_estimate() -> None:
    from jarvis_core.config import Settings
    from jarvis_core.config.settings import VoiceConfig
    from jarvis_voice.doctor import estimate_first_sound, measure

    stages = measure(Settings(voice=VoiceConfig(models_dir=Path(MODELS or "."))), runs=1)
    assert {"tts_erstes_stueck", "stt_je_aufnahme", "vad_je_frame", "satzende_pruefung"} <= set(
        stages
    )
    assert 0 < estimate_first_sound(stages) < 60


# ------------------------------------------------ Prüfrunde-Funde (Block B)
async def test_stop_during_write_swallows_the_portaudio_error(fake_sd: FakeSd) -> None:
    sink = SoundDeviceSink()
    pcm = np.zeros(audio_sounddevice.BLOCK_FRAMES * 4, dtype=np.int16).tobytes()
    await sink.play(pcm, 16_000)
    stream = fake_sd.outputs[0]

    def stop_then_fail() -> None:
        sink.stop()
        raise OSError("PortAudioError: Stream is stopped")

    stream.on_write = stop_then_fail
    await sink.play(pcm, 16_000)  # darf nicht werfen: der Stopp ist der Grund
    stream.on_write = None

    def fail_for_real() -> None:
        raise OSError("Gerät weg")

    stream.on_write = fail_for_real
    with pytest.raises(OSError, match="Gerät weg"):
        await sink.play(pcm, 16_000)  # ein echter Fehler bleibt sichtbar


async def test_failed_beep_does_not_leave_an_unretrieved_task(fake_sd: FakeSd) -> None:
    sink = SoundDeviceSink()
    sink.beep("wake")
    await asyncio.sleep(0.05)
    fake_sd.outputs[0].on_write = lambda: (_ for _ in ()).throw(OSError("weg"))
    sink.beep("wake")
    await asyncio.sleep(0.05)
    assert not sink._beeps  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]


async def test_sink_falls_back_to_the_device_rate_when_the_rate_is_refused(
    fake_sd: FakeSd,
) -> None:
    created: list[int] = []
    original = fake_sd.OutputStream

    def picky(**kwargs: Any) -> FakeStream:
        created.append(kwargs["samplerate"])
        if kwargs["samplerate"] == 22_050:
            raise OSError("Invalid sample rate")
        return original(**kwargs)

    fake_sd.OutputStream = picky  # type: ignore[method-assign]
    fake_sd.query_devices = lambda *a, **k: (  # type: ignore[method-assign]
        {"default_samplerate": 48_000.0} if a else FakeSd.query_devices(fake_sd)
    )
    sink = SoundDeviceSink()
    pcm = np.zeros(audio_sounddevice.BLOCK_FRAMES, dtype=np.int16).tobytes()
    await sink.play(pcm, 22_050)
    assert created == [22_050, 48_000]
    written = fake_sd.outputs[-1].written
    assert written and written[0] == int(audio_sounddevice.BLOCK_FRAMES * 48_000 / 22_050)


def test_device_lookup_prefers_mme(fake_sd: FakeSd) -> None:
    devices = [
        {"name": "Headset (WASAPI)", "max_input_channels": 1, "hostapi": 1},
        {"name": "Headset (MME)", "max_input_channels": 1, "hostapi": 0},
    ]
    fake_sd.query_devices = lambda *a, **k: devices  # type: ignore[method-assign]
    fake_sd.query_hostapis = lambda i: {"name": ["MME", "Windows WASAPI"][i]}  # type: ignore[attr-defined]
    assert find_device(fake_sd, "Headset", "input") == 1


def test_whisper_reruns_with_de_or_en_when_whisper_guesses_another_language(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from jarvis_voice.engines import whisper as module

    calls: list[str | None] = []

    class Info:
        def __init__(self, language: str, probs: list[tuple[str, float]]) -> None:
            self.language = language
            self.language_probability = 0.9
            self.all_language_probs = probs

    class Segment:
        def __init__(self, text: str) -> None:
            self.text = text

    class FakeModel:
        def __init__(self, *_a: Any, **_k: Any) -> None:
            pass

        def transcribe(self, _audio: Any, language: str | None = None, **_k: Any) -> Any:
            calls.append(language)
            if language is None:
                return [Segment("falscher Text")], Info(
                    "nl", [("nl", 0.6), ("de", 0.3), ("en", 0.1)]
                )
            return [Segment("Hallo Welt")], Info(language, [])

    fake = types.SimpleNamespace(WhisperModel=FakeModel)
    real_need = engines.need

    def need(name: str, why: str) -> Any:
        return fake if name == "faster_whisper" else real_need(name, why)

    monkeypatch.setattr(module, "need", need)
    stt = module.FasterWhisperStt(Path("x"))
    result = stt._run(bytes(3200))  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
    assert calls == [None, "de"] and result.text == "Hallo Welt" and result.language == "de"


def test_hf_offline_is_set_before_any_engine_import() -> None:
    assert os.environ.get("HF_HUB_OFFLINE") == "1"


def test_piper_reports_a_missing_voice_file_in_german(tmp_path: Path) -> None:
    pytest.importorskip("piper")
    from jarvis_voice.engines.piper_tts import PiperTts
    from jarvis_voice.models import ModelError

    tts = PiperTts({"de": tmp_path / "gibt-es-nicht.onnx"})
    with pytest.raises(ModelError, match="jarvis voice setup"):
        tts.warm_up("de")


async def test_source_falls_back_to_device_rate_and_resamples(
    fake_sd: FakeSd, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Gerät nimmt 16 kHz nicht an (Windows WDM-KS): Öffnen mit 48 kHz, Frames auf 16 kHz umrechnen."""

    class DeviceRejected(Exception):
        pass

    real_query = fake_sd.query_devices
    real_stream = fake_sd.RawInputStream
    monkeypatch.setattr(fake_sd, "PortAudioError", DeviceRejected, raising=False)

    def query(*args: Any, **kwargs: Any) -> Any:
        if args:  # query_devices(index, "input"): Infos eines Geräts
            return {"default_samplerate": 48_000.0}
        return real_query(*args, **kwargs)

    def stream(**kwargs: Any) -> FakeStream:
        if kwargs["samplerate"] == 16_000:
            raise DeviceRejected("Invalid device")
        return real_stream(**kwargs)

    monkeypatch.setattr(fake_sd, "query_devices", query)
    monkeypatch.setattr(fake_sd, "RawInputStream", stream)
    source = SoundDeviceSource("Mikrofon")
    await source.open()
    opened = fake_sd.inputs[0]
    assert opened.kwargs["samplerate"] == 48_000
    assert opened.kwargs["blocksize"] == FRAME_SAMPLES * 3
    # 1536 Samples bei 48 kHz werden zu 512 Samples bei 16 kHz
    opened.kwargs["callback"](
        np.zeros(FRAME_SAMPLES * 3, dtype=np.int16).tobytes(), 1536, None, None
    )
    await asyncio.sleep(0.05)
    frame = await source._queue.get()  # pyright: ignore[reportPrivateUsage]  # noqa: SLF001
    assert frame is not None and len(frame) == FRAME_SAMPLES * 2
    source.close()
