"""Echo-Schutz, Stufe 1 (M2.8): halbduplex. Während JARVIS spricht, gilt eigene Stimme im
Mikrofon nicht als Master: Sprache zählt erst, wenn sie deutlich lauter ist als das, was gerade
aus dem Lautsprecher kommt. Echte Echounterdrückung (AEC) ist Folgearbeit (docs/PLAN-M2.md)."""

from jarvis_voice.audio import rms

MIN_LEVEL = 0.02  # darunter ist es Rauschen
ECHO_FACTOR = 1.6  # Masters Stimme muss so viel lauter sein als die Wiedergabe (im Mikrofon)
LEAKAGE = 0.6  # grobe Annahme: so viel der Wiedergabe kommt im Mikrofon an
HOLD_SECONDS = 0.25  # die Wiedergabe klingt kurz nach (Raum, Puffer)


class EchoGuard:
    def __init__(self) -> None:
        self._level = 0.0
        self._until = 0.0

    def played(self, pcm: bytes, now: float, duration: float) -> None:
        """Merkt sich den Pegel eines Wiedergabe-Stücks, das jetzt `duration` Sekunden läuft."""
        self._level = max(self._level * 0.5, rms(pcm))
        self._until = now + duration + HOLD_SECONDS

    def is_user_speech(self, frame: bytes, now: float) -> bool:
        level = rms(frame)
        if level < MIN_LEVEL:
            return False
        if now > self._until:
            return True  # nichts läuft: jede Sprache zählt
        return level > max(MIN_LEVEL, self._level * LEAKAGE * ECHO_FACTOR)
