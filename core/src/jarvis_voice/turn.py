"""Platzhalter für die Sprechpausen-Erkennung (NICHT M2.4): ein einfacher Pegel-Erkenner und ein
Erkenner, der das Satzende an anhaltender Stille festmacht. Die richtige Erkennung (Silero-VAD,
Smart Turn) kommt als Adapter, sobald Master die Pipecat-Frage entschieden hat (docs/PLAN-M2.md)."""

from jarvis_voice.audio import rms
from jarvis_voice.protocols import FRAME_SECONDS


class EnergyVad:
    """Sprache, wenn der Pegel über der Schwelle liegt."""

    def __init__(self, threshold: float = 0.02) -> None:
        self.threshold = threshold

    def is_speech(self, frame: bytes) -> bool:
        return rms(frame) >= self.threshold


class SilenceTurnDetector:
    """Satzende = Sprache war da, danach `end_silence` Sekunden Stille."""

    def __init__(self, end_silence: float = 0.8) -> None:
        self._needed = max(1, round(end_silence / FRAME_SECONDS))
        self._heard = False
        self._quiet = 0

    def feed(self, frame: bytes, speech: bool) -> bool:
        del frame
        if speech:
            self._heard = True
            self._quiet = 0
            return False
        if not self._heard:
            return False
        self._quiet += 1
        return self._quiet >= self._needed

    def reset(self) -> None:
        self._heard = False
        self._quiet = 0
