"""Austauschbare Teile des Sprachclients. Die Gesprächslogik (`session.py`) kennt nur diese
Protokolle; echte Engines (Windows) und Attrappen (Tests) stecken dahinter.

Audio überall: 16 kHz, mono, 16 Bit (little endian) als `bytes`. Ein Frame sind 512 Samples
(32 ms). Aufnahmen bleiben im Arbeitsspeicher und werden nie auf die Platte geschrieben.
"""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

SAMPLE_RATE = 16_000
FRAME_SAMPLES = 512
FRAME_SECONDS = FRAME_SAMPLES / SAMPLE_RATE


class State(StrEnum):
    SLEEP = "SCHLAF"  # Mikrofon ganz geschlossen, nur das Tastenkürzel weckt
    WAITING = "WARTET"  # horcht nur auf das Wake Word
    LISTENING = "HÖRT ZU"
    THINKING = "DENKT"
    SPEAKING = "SPRICHT"
    CONFIRM = "WARTET AUF JA/NEIN"  # Sprachbestätigung (nur mit Sprecherprüfung)


@dataclass(frozen=True)
class Transcript:
    text: str
    language: str | None = None  # "de", "en" oder None, wenn nicht erkannt


# ---- Ereignisse des Cores (aus dem /v1/chat-Strom) --------------------------------------------
@dataclass(frozen=True)
class CoreDelta:
    text: str


@dataclass(frozen=True)
class CoreConfirm:
    id: str
    tool: str
    preview: str
    question: str
    risk: str


@dataclass(frozen=True)
class CoreNotice:
    message: str


@dataclass(frozen=True)
class CoreDone:
    session_id: str


@dataclass(frozen=True)
class CoreError:
    message: str


CoreEvent = CoreDelta | CoreConfirm | CoreNotice | CoreDone | CoreError


class CoreClient(Protocol):
    def ask(self, text: str, session_id: str | None) -> AsyncIterator[CoreEvent]: ...
    async def confirm(self, request_id: str, approve: bool, by_voice: bool) -> bool: ...
    async def stop(self) -> None: ...


class AudioSource(Protocol):
    async def open(self) -> None: ...
    def close(self) -> None: ...
    def frames(self) -> AsyncIterator[bytes]: ...


class AudioSink(Protocol):
    async def play(self, pcm: bytes, sample_rate: int) -> None: ...
    def stop(self) -> None: ...  # sofort verstummen, Warteschlange leeren


class WakeWordDetector(Protocol):
    def process(self, frame: bytes) -> bool: ...
    def reset(self) -> None: ...


class VoiceActivity(Protocol):
    def is_speech(self, frame: bytes) -> bool: ...


class TurnDetector(Protocol):
    """Erkennt das Ende einer Äußerung (M2.4). Bekommt jeden Frame samt VAD-Urteil."""

    def feed(self, frame: bytes, speech: bool) -> bool: ...
    def reset(self) -> None: ...


class SpeechToText(Protocol):
    async def transcribe(self, pcm: bytes) -> Transcript: ...


class TextToSpeech(Protocol):
    def synthesize(
        self, text: str, language: str, whisper: bool
    ) -> AsyncIterator[tuple[bytes, int]]: ...  # (PCM, Abtastrate)


class SpeakerGate(Protocol):
    """Hört nur auf Master (M2.9). Ohne Gate gilt jede Stimme."""

    def verify(self, pcm: bytes) -> bool: ...


class Indicator(Protocol):
    """Zeigt Master jederzeit, was das Mikrofon tut (Masterauftrag §6)."""

    def show(self, state: State) -> None: ...
    def cue(self, name: str) -> None: ...  # kurzer Ton: "wake", "listen", "done"
