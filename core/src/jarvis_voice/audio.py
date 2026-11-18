"""Kleine Hilfen für 16-Bit-PCM ohne numpy (läuft auch dort, wo nur der Kern installiert ist)."""

import math
from array import array


def samples(pcm: bytes) -> array[int]:
    data = array("h")
    data.frombytes(pcm[: len(pcm) // 2 * 2])
    return data


def rms(pcm: bytes) -> float:
    """Effektivpegel von 0 bis 1 (Vollaussteuerung = 1)."""
    data = samples(pcm)
    if not data:
        return 0.0
    return math.sqrt(sum(x * x for x in data) / len(data)) / 32768.0


def tone(freq: float, seconds: float, sample_rate: int = 16_000, level: float = 0.3) -> bytes:
    """Ein Sinuston (für Hinweistöne und Tests)."""
    count = int(sample_rate * seconds)
    data = array(
        "h",
        (int(32767 * level * math.sin(2 * math.pi * freq * i / sample_rate)) for i in range(count)),
    )
    return data.tobytes()


def silence(seconds: float, sample_rate: int = 16_000) -> bytes:
    return bytes(int(sample_rate * seconds) * 2)
