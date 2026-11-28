"""Doppelklatschen als Start (M2.13): reine Signalverarbeitung, kein Modell, kein Paket.

Ein Klatschen ist ein kurzer, lauter Stoß (höchstens ca. 130 ms) mit leiser Umgebung davor und
danach; Sprache ist länger laut. Zwei Stöße im Abstand von 0,15 bis 0,6 Sekunden gelten als
Doppelklatschen. Nach einem Treffer ruht der Erkenner eine Sekunde. Der Erkenner arbeitet nur, wenn
das Mikrofon ohnehin offen ist (nicht im Schlafmodus)."""

from jarvis_voice.audio import rms
from jarvis_voice.protocols import FRAME_SECONDS

MAX_BURST_FRAMES = 4  # länger laut = Sprache oder Musik, kein Klatschen
MIN_GAP = 0.15
MAX_GAP = 0.6
REFRACTORY = 1.0
QUIET_FACTOR = 0.5  # unter threshold * diesem Faktor gilt wieder als leise


class ClapDetector:
    def __init__(self, threshold: float = 0.3) -> None:
        self.threshold = threshold
        self.reset()

    def reset(self) -> None:
        self._loud = 0  # wie viele Frames in Folge laut
        self._onset = 0.0
        self._first: float | None = None
        self._blocked_until = 0.0

    def process(self, frame: bytes, now: float) -> bool:
        """True, wenn mit diesem Frame ein Doppelklatschen abgeschlossen ist."""
        level = rms(frame)
        if level >= self.threshold:
            if self._loud == 0:
                self._onset = now
            self._loud += 1
            return False
        if level >= self.threshold * QUIET_FACTOR:
            return False  # Ausklingen: weder laut noch leise
        burst, self._loud = self._loud, 0
        if burst == 0 or now < self._blocked_until:
            return False
        if burst > MAX_BURST_FRAMES:  # zu lang: Sprache
            self._first = None
            return False
        first = self._first
        if first is not None and MIN_GAP <= self._onset - first <= MAX_GAP:
            self._first = None
            self._blocked_until = now + REFRACTORY
            return True
        if first is not None and self._onset - first < MIN_GAP:
            return False  # Nachhall desselben Klatschens
        self._first = self._onset
        return False

    @staticmethod
    def frames_for(seconds: float) -> int:
        return max(1, round(seconds / FRAME_SECONDS))
