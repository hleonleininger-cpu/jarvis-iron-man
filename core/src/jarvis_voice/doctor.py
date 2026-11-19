# pyright: basic, reportMissingImports=false, reportMissingModuleSource=false
"""`jarvis voice doctor` und `jarvis voice bench`: prüft Pakete, Modelle, Geräte und misst die
Antwortzeit je Stufe. Gemessen werden nur Zeiten, nie Inhalte."""

import asyncio
import importlib.util
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from jarvis_core.config import Settings
from jarvis_voice.models import load_registry, verify
from jarvis_voice.protocols import FRAME_SAMPLES, SAMPLE_RATE

SAMPLE_DE = "Guten Tag, Master. Wie kann ich helfen?"
SAMPLE_EN = "Good evening, sir. How may I help?"
TARGET_FIRST_SOUND_SECONDS = 6.0  # Ziel für Profil S (nur CPU), siehe docs/PLAN-M2.md


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str = ""


def resample_16k(pcm: bytes, rate: int) -> bytes:
    """Mono-16-Bit-PCM auf 16 kHz (lineare Interpolation reicht für Sprache)."""
    import numpy as np

    if rate == SAMPLE_RATE:
        return pcm
    samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
    count = int(len(samples) * SAMPLE_RATE / rate)
    out = np.interp(np.linspace(0, len(samples) - 1, count), np.arange(len(samples)), samples)
    return out.astype(np.int16).tobytes()


def package_checks() -> list[Check]:
    from jarvis_voice.cli import PACKAGES

    checks: list[Check] = []
    for name, why in PACKAGES.items():
        present = importlib.util.find_spec(name) is not None
        windows_only = name == "openwakeword" and sys.platform != "win32"
        detail = why + (" (auf diesem System nicht nötig)" if windows_only and not present else "")
        checks.append(Check(f"Paket {name}", present or windows_only, detail))
    return checks


def model_checks(models_dir: Path, deep: bool = False) -> list[Check]:
    checks: list[Check] = []
    for key, entry in load_registry().items():
        problems = verify(models_dir, entry, deep=deep)
        checks.append(Check(f"Modell {key}", not problems, "; ".join(problems) or entry.zweck))
    return checks


def hardware_hint() -> Check:
    from jarvis_core.hardware import detect_hardware, recommend

    info = detect_hardware()
    profile = recommend(info).profile.value
    gpu = f"{info.gpu}, {info.vram_gb:.0f} GB VRAM" if info.gpu and info.vram_gb else "keine"
    advice = (
        'NVIDIA-GPU erkannt: stt_device = "cuda" ist möglich (CUDA 12 und cuDNN 9 nötig).'
        if info.vram_gb and info.vram_gb >= 6
        else "CPU-Betrieb: Standard (base, int8) passt."
    )
    return Check("Hardware", True, f"Profil {profile}, GPU: {gpu}. {advice}")


def device_checks(settings: Settings) -> list[Check]:
    try:
        import sounddevice as sd  # pyright: ignore[reportMissingImports]
    except (ImportError, OSError) as exc:
        return [Check("Audiogeräte", False, f"sounddevice nicht nutzbar ({type(exc).__name__})")]
    from jarvis_voice.engines.audio_sounddevice import find_device, list_devices

    devices = list_devices(sd)
    out = [Check("Mikrofone", any(d[2] for d in devices), ", ".join(d[1] for d in devices if d[2]))]
    out.append(
        Check("Lautsprecher", any(d[3] for d in devices), ", ".join(d[1] for d in devices if d[3]))
    )
    for label, name, kind in (
        ("Eingabegerät laut Einstellung", settings.voice.mic_device, "input"),
        ("Ausgabegerät laut Einstellung", settings.voice.speaker_device, "output"),
    ):
        if name:
            try:
                find_device(sd, name, kind)
                out.append(Check(label, True, name))
            except ValueError as exc:
                out.append(Check(label, False, str(exc)))
    return out


def function_checks(settings: Settings) -> list[Check]:
    """Funktionsprobe mit den Modellen: laden, sprechen, die eigene Stimme erkennen, Sprache/Stille.
    Wake Word und Satzende werden hier nur geladen; ihre Güte zeigt erst echte Sprache am PC."""
    from jarvis_voice.run import build_parts

    try:
        parts = build_parts(settings, with_audio=False)
    except Exception as exc:  # fehlende Pakete oder Modelle: verständlich melden
        return [Check("Engines laden", False, str(exc))]
    checks = [Check("Engines laden", True, "Wake Word, VAD, Satzende, Erkennung, Sprachausgabe")]

    async def probe() -> list[Check]:
        out: list[Check] = []
        t0 = time.monotonic()
        audio = b""
        async for chunk, rate in parts.tts.synthesize(SAMPLE_DE, "de", False):
            audio += resample_16k(chunk, rate)
        out.append(Check("Sprachausgabe", len(audio) > 16_000, f"{time.monotonic() - t0:.2f} s"))
        t0 = time.monotonic()
        heard = await parts.stt.transcribe(audio)
        out.append(
            Check(
                "Spracherkennung (eigene Stimme)",
                "master" in heard.text.casefold(),
                f"{time.monotonic() - t0:.2f} s, Sprache {heard.language}",
            )
        )
        frames = [
            audio[i : i + FRAME_SAMPLES * 2]
            for i in range(0, len(audio) - FRAME_SAMPLES * 2, FRAME_SAMPLES * 2)
        ]
        speech = sum(parts.vad.is_speech(f) for f in frames)
        out.append(
            Check(
                "Sprache/Stille-Erkennung",
                0 < speech < len(frames),
                f"{speech} von {len(frames)} Frames",
            )
        )
        return out

    return checks + asyncio.run(probe())


def run_doctor(settings: Settings, deep: bool = False) -> list[Check]:
    from jarvis_voice.run import models_dir

    checks = [hardware_hint(), *package_checks(), *device_checks(settings)]
    checks += model_checks(models_dir(settings), deep)
    if all(c.ok for c in checks if c.name.startswith(("Modell", "Paket"))):
        checks += function_checks(settings)
    else:
        checks.append(
            Check("Funktionsprobe", False, "übersprungen: erst Pakete/Modelle (jarvis voice setup)")
        )
    return checks


# ------------------------------------------------------------------------------------- bench
def measure(settings: Settings, runs: int = 3) -> dict[str, float]:
    """Mittelwerte der Stufen in Sekunden (nur Zeiten). Fehlt etwas, wirft es ModelError."""
    from jarvis_voice.run import build_parts

    parts = build_parts(settings, with_audio=False)
    stt, tts, vad = parts.stt, parts.tts, parts.vad
    stages: dict[str, list[float]] = {}

    def note(name: str, value: float) -> None:
        stages.setdefault(name, []).append(value)

    async def go() -> None:
        stt.warm_up()  # type: ignore[attr-defined]
        tts.warm_up()  # type: ignore[attr-defined]
        for _ in range(runs):
            start = time.monotonic()
            audio = b""
            first: float | None = None
            async for chunk, rate in tts.synthesize(SAMPLE_DE, "de", False):
                first = first if first is not None else time.monotonic() - start
                audio += resample_16k(chunk, rate)
            note("tts_erstes_stueck", first or 0.0)
            seconds = len(audio) / 2 / SAMPLE_RATE
            start = time.monotonic()
            await stt.transcribe(audio)
            note("stt_je_aufnahme", time.monotonic() - start)
            note("stt_aufnahme_sekunden", seconds)
            frame = audio[: FRAME_SAMPLES * 2]
            start = time.monotonic()
            for _ in range(50):
                vad.is_speech(frame)
            note("vad_je_frame", (time.monotonic() - start) / 50)
            start = time.monotonic()
            for _ in range(10):
                parts.wake.process(frame * 3)
            note("wake_je_frame", (time.monotonic() - start) / 10 / 3)
            turn = parts.turn
            for f in [
                audio[i : i + FRAME_SAMPLES * 2] for i in range(0, len(audio), FRAME_SAMPLES * 2)
            ][:40]:
                turn.feed(f.ljust(FRAME_SAMPLES * 2, b"\0"), True)
            start = time.monotonic()
            turn.complete_probability()  # type: ignore[attr-defined]
            note("satzende_pruefung", time.monotonic() - start)
            turn.reset()
        # Core bis zum ersten Satz (nur wenn erreichbar)
        from jarvis_voice.protocols import CoreDelta

        start = time.monotonic()
        async for event in parts.core.ask("Sag kurz Hallo.", None):
            if isinstance(event, CoreDelta):
                note("core_erster_text", time.monotonic() - start)
                break
        await parts.core.aclose()  # type: ignore[attr-defined]

    asyncio.run(go())
    return {name: sum(v) / len(v) for name, v in stages.items()}


def estimate_first_sound(stages: dict[str, float], end_silence: float = 0.25) -> float:
    """Grobe Zeit vom Ende des Satzes bis zum ersten Ton: Pause, Erkennung, Core, Ausgabe."""
    return (
        end_silence
        + stages.get("satzende_pruefung", 0.0)
        + stages.get("stt_je_aufnahme", 0.0)
        + stages.get("core_erster_text", 0.0)
        + stages.get("tts_erstes_stueck", 0.0)
    )
