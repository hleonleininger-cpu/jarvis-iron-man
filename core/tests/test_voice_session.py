"""Gesprächslogik des Sprachclients mit Attrappen: Wake Word, Sprechpausen, Folgefragen,
Unterbrechen, Schlaf- und Flüstermodus, Rückfragen per Stimme, Datenschutz."""

import asyncio
import os
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from jarvis_core.config.settings import VoiceConfig
from jarvis_voice.audio import silence, tone
from jarvis_voice.protocols import (
    FRAME_SECONDS,
    CoreConfirm,
    CoreDelta,
    CoreDone,
    CoreEvent,
    State,
    Transcript,
)
from jarvis_voice.session import MESSAGES, Parts, VoiceSession
from jarvis_voice.turn import EnergyVad, SilenceTurnDetector

pytestmark = pytest.mark.anyio

WAKE = tone(300, FRAME_SECONDS, level=0.9)  # die Wake-Word-Attrappe erkennt diesen Pegel
SPEECH = tone(200, FRAME_SECONDS, level=0.3)
LOUD = tone(200, FRAME_SECONDS, level=0.95)
QUIET = silence(FRAME_SECONDS)


class Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


class FakeWake:
    def process(self, frame: bytes) -> bool:
        return frame == WAKE

    def reset(self) -> None:
        pass


class FakeSource:
    def __init__(self, frames: list[bytes] | None = None) -> None:
        self.script = frames or []
        self.opened = 0
        self.closed = 0

    async def open(self) -> None:
        self.opened += 1

    def close(self) -> None:
        self.closed += 1

    async def frames(self) -> AsyncIterator[bytes]:
        while self.script:
            await asyncio.sleep(0)
            yield self.script.pop(0)


class FakeSink:
    def __init__(self) -> None:
        self.played: list[bytes] = []
        self.stops = 0

    async def play(self, pcm: bytes, sample_rate: int) -> None:
        del sample_rate
        self.played.append(pcm)

    def stop(self) -> None:
        self.stops += 1


class FakeStt:
    def __init__(self, *results: tuple[str, str | None]) -> None:
        self.queue = [Transcript(text, lang) for text, lang in results]
        self.calls = 0

    async def transcribe(self, pcm: bytes) -> Transcript:
        del pcm
        self.calls += 1
        return self.queue.pop(0) if self.queue else Transcript("")


class FakeTts:
    def __init__(self) -> None:
        self.said: list[tuple[str, str, bool]] = []

    async def synthesize(
        self, text: str, language: str, whisper: bool
    ) -> AsyncIterator[tuple[bytes, int]]:
        self.said.append((text, language, whisper))
        yield tone(300, 0.3, level=0.3), 16_000


class FakeCore:
    def __init__(self, *scripts: list[CoreEvent]) -> None:
        self.scripts = list(scripts)
        self.asked: list[tuple[str, str | None]] = []
        self.confirmed: list[tuple[str, bool, bool]] = []
        self.stops = 0

    async def ask(self, text: str, session_id: str | None) -> AsyncIterator[CoreEvent]:
        self.asked.append((text, session_id))
        for event in self.scripts.pop(0) if self.scripts else []:
            yield event

    async def confirm(self, request_id: str, approve: bool, by_voice: bool) -> bool:
        self.confirmed.append((request_id, approve, by_voice))
        return True

    async def stop(self) -> None:
        self.stops += 1


class FakeIndicator:
    def __init__(self) -> None:
        self.states: list[State] = []
        self.cues: list[str] = []

    def show(self, state: State) -> None:
        self.states.append(state)

    def cue(self, name: str) -> None:
        self.cues.append(name)


class Speaker:
    def __init__(self, accept: bool = True) -> None:
        self.accept = accept

    def verify(self, pcm: bytes) -> bool:
        del pcm
        return self.accept


class Rig:
    def __init__(
        self,
        *,
        stt: FakeStt | None = None,
        core: FakeCore | None = None,
        config: VoiceConfig | None = None,
        speaker: Speaker | None = None,
        source: FakeSource | None = None,
    ) -> None:
        self.clock = Clock()
        self.source = source or FakeSource()
        self.sink = FakeSink()
        self.stt = stt or FakeStt()
        self.tts = FakeTts()
        self.core = core or FakeCore()
        self.indicator = FakeIndicator()
        config = config or VoiceConfig(end_silence_seconds=0.2, follow_up_seconds=8.0)
        self.session = VoiceSession(
            Parts(
                self.source, self.sink, FakeWake(), EnergyVad(), SilenceTurnDetector(0.2),
                self.stt, self.tts, self.core, self.indicator, speaker,
            ),
            config,
            clock=self.clock,
        )  # fmt: skip

    async def frames(self, *frames: bytes, step: float = FRAME_SECONDS) -> None:
        for frame in frames:
            self.clock.now += step
            await self.session.on_frame(frame)
            await asyncio.sleep(0)

    async def say_something(self) -> None:
        """Wake Word, eine kurze Äußerung, Stille: danach läuft die Verarbeitung."""
        await self.frames(WAKE)
        await self.frames(*([SPEECH] * 5 + [QUIET] * 8))
        await self.session.wait_idle()


def answer(*sentences: str) -> list[CoreEvent]:
    return [*(CoreDelta(text) for text in sentences), CoreDone("s1")]


# ----------------------------------------------------------------------------- Ablauf
async def test_wake_then_question_is_answered_sentence_by_sentence() -> None:
    rig = Rig(
        stt=FakeStt(("Wie wird das Wetter?", "de")),
        core=FakeCore(answer("Es wird sonnig. ", "Es hat zwanzig Grad.")),
    )
    await rig.say_something()
    assert rig.core.asked == [("Wie wird das Wetter?", None)]
    assert [t for t, _l, _w in rig.tts.said] == ["Es wird sonnig.", "Es hat zwanzig Grad."]
    assert len(rig.sink.played) == 2
    assert rig.session.session_id == "s1"
    assert rig.indicator.states[:4] == [
        State.WAITING,
        State.LISTENING,
        State.THINKING,
        State.SPEAKING,
    ]
    assert "wake" in rig.indicator.cues
    assert rig.session.state is State.LISTENING  # Folgefrage ohne Wake Word


async def test_follow_up_needs_no_wake_word_and_keeps_the_conversation() -> None:
    core = FakeCore(answer("Erste Antwort steht hier."), answer("Zweite Antwort steht hier."))
    rig = Rig(stt=FakeStt(("Frage eins", "de"), ("Und morgen?", "de")), core=core)
    await rig.say_something()
    await rig.frames(*([SPEECH] * 5 + [QUIET] * 8))  # ohne Wake Word
    await rig.session.wait_idle()
    assert [t for t, _ in core.asked] == ["Frage eins", "Und morgen?"]
    assert core.asked[1][1] == "s1"  # gleiche Sitzung


async def test_follow_up_window_expires_back_to_wake_word() -> None:
    rig = Rig(stt=FakeStt(("Frage", "de")), core=FakeCore(answer("Antwort kommt jetzt.")))
    await rig.say_something()
    assert rig.session.state is State.LISTENING
    await rig.frames(QUIET, step=9.0)  # länger als follow_up_seconds still
    assert rig.session.state is State.WAITING
    await rig.frames(SPEECH, QUIET)  # ohne Wake Word passiert nichts mehr
    assert rig.stt.calls == 1


async def test_no_speech_after_wake_word_times_out() -> None:
    rig = Rig()
    await rig.frames(WAKE)
    assert rig.session.state is State.LISTENING
    await rig.frames(QUIET, step=7.0)
    assert rig.session.state is State.WAITING


async def test_empty_recognition_does_not_ask_the_core() -> None:
    rig = Rig(stt=FakeStt(("", None)))
    await rig.say_something()
    assert rig.core.asked == []


# ------------------------------------------------------------------------- Sprache
async def test_reply_language_follows_the_recognized_language() -> None:
    rig = Rig(
        stt=FakeStt(("What is the weather?", "en")), core=FakeCore(answer("It is sunny today."))
    )
    await rig.say_something()
    assert rig.tts.said[0][1] == "en"


async def test_fixed_language_overrides_detection() -> None:
    config = VoiceConfig(language="de", end_silence_seconds=0.2)
    rig = Rig(
        stt=FakeStt(("What is the weather?", "en")),
        core=FakeCore(answer("Es ist sonnig heute.")),
        config=config,
    )
    await rig.say_something()
    assert rig.tts.said[0][1] == "de"


# ------------------------------------------------------------------------- Befehle
async def test_stop_phrase_interrupts_and_asks_nothing() -> None:
    rig = Rig(stt=FakeStt(("Stopp", "de")))
    await rig.say_something()
    assert rig.core.asked == [] and rig.core.stops == 1 and rig.sink.stops >= 1
    assert rig.session.state is State.WAITING


async def test_good_night_closes_the_microphone_until_woken() -> None:
    source = FakeSource([WAKE] + [SPEECH] * 5 + [QUIET] * 8)
    rig = Rig(stt=FakeStt(("Gute Nacht, Jarvis", "de")), source=source)
    runner = asyncio.create_task(rig.session.run())
    for _ in range(300):
        await asyncio.sleep(0.01)
        if rig.session.state is State.SLEEP and rig.source.closed:
            break
    assert rig.session.state is State.SLEEP and rig.source.closed >= 1  # Mikrofon wirklich zu
    assert rig.tts.said[0][0] == MESSAGES["de"]["sleep"]
    opened = rig.source.opened
    rig.session.wake()  # Tastenkürzel
    await asyncio.wait_for(runner, 5)
    assert rig.source.opened == opened + 1  # beim Wecken wieder geöffnet
    states = rig.indicator.states
    assert State.WAITING in states[states.index(State.SLEEP) :]  # nach dem Wecken wieder wach


async def test_sleeping_session_ignores_frames() -> None:
    rig = Rig()
    rig.session.sleep()
    await rig.frames(WAKE, SPEECH, QUIET)
    assert rig.session.state is State.SLEEP and rig.stt.calls == 0


async def test_whisper_mode_is_short_and_quiet() -> None:
    sentences = [
        "Erster langer Satz kommt.",
        "Zweiter langer Satz kommt.",
        "Dritter langer Satz kommt.",
    ]
    core = FakeCore(answer(*[s + " " for s in sentences]))
    rig = Rig(stt=FakeStt(("Flüstere", "de"), ("Erzähl mal", "de")), core=core)
    await rig.say_something()  # "Flüstere"
    assert rig.session.whisper
    await rig.frames(*([SPEECH] * 5 + [QUIET] * 8))
    await rig.session.wait_idle()
    assert all(w for _t, _l, w in rig.tts.said) and len(rig.tts.said) == 2


# ---------------------------------------------------------------------- Unterbrechen
async def _speaking_rig(barge_in: str) -> Rig:
    config = VoiceConfig(barge_in=barge_in, end_silence_seconds=0.2)  # type: ignore[arg-type]
    rig = Rig(config=config)
    rig.session.state = State.SPEAKING
    rig.session._echo.played(tone(300, 0.3, level=0.3), rig.clock.now, 0.3)  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
    return rig


async def test_voice_barge_in_stops_speaking_and_listens() -> None:
    rig = await _speaking_rig("voice")
    await rig.frames(*([LOUD] * 5))  # Master spricht deutlich lauter als die Wiedergabe
    await rig.session.wait_idle()
    assert rig.sink.stops >= 1 and rig.core.stops == 1
    assert rig.session.state is State.LISTENING


async def test_echo_is_not_mistaken_for_barge_in() -> None:
    rig = await _speaking_rig("voice")
    await rig.frames(*([tone(300, FRAME_SECONDS, level=0.25)] * 10), step=0.0)  # nur das Echo
    assert rig.sink.stops == 0 and rig.session.state is State.SPEAKING


async def test_barge_in_by_key_only_by_default() -> None:
    rig = await _speaking_rig("key")
    await rig.frames(*([LOUD] * 8))
    assert rig.sink.stops == 0 and rig.session.state is State.SPEAKING
    await rig.session.interrupt()  # Tastenkürzel
    assert rig.sink.stops == 1 and rig.core.stops == 1 and rig.session.state is State.WAITING


async def test_wake_word_is_ignored_while_speaking() -> None:
    rig = await _speaking_rig("off")
    await rig.frames(WAKE, WAKE, WAKE)
    assert rig.session.state is State.SPEAKING


# ----------------------------------------------------------------- Rückfragen per Stimme
def _confirm(risk: str) -> list[CoreEvent]:
    return [
        CoreConfirm("c1", "create_event", "Neuer Termin: Montag 15 Uhr", "Anlegen?", risk),
        CoreDone("s1"),
    ]


async def _confirm_flow(
    risk: str,
    reply: tuple[str, str | None] | None,
    *,
    speaker: Speaker | None,
    by_voice: bool = True,
) -> Rig:
    config = VoiceConfig(confirm_by_voice=by_voice, end_silence_seconds=0.2, follow_up_seconds=0.0)
    results = [("Trag einen Termin ein", "de")] + ([reply] if reply else [])
    rig = Rig(stt=FakeStt(*results), core=FakeCore(_confirm(risk)), config=config, speaker=speaker)
    await rig.frames(WAKE)
    await rig.frames(*([SPEECH] * 5 + [QUIET] * 8))
    for _ in range(50):  # die Rückfrage wird vorgelesen; dann wartet CONFIRM auf Antwort
        await asyncio.sleep(0)
        if rig.session.state is State.CONFIRM:
            break
    if reply and rig.session.state is State.CONFIRM:
        await rig.frames(*([SPEECH] * 5 + [QUIET] * 8))
    elif rig.session.state is State.CONFIRM:
        await rig.frames(QUIET, step=11.0)  # keine Antwort
    await rig.session.wait_idle()
    return rig


async def test_voice_confirmation_with_exact_phrase_and_speaker() -> None:
    rig = await _confirm_flow("WRITE_REVERSIBLE", ("Ja, bestätigt", "de"), speaker=Speaker())
    assert rig.core.confirmed == [("c1", True, True)]
    spoken = [t for t, _l, _w in rig.tts.said]
    assert any("Neuer Termin" in t for t in spoken)  # die Vorschau wird vorgelesen


async def test_voice_decline() -> None:
    rig = await _confirm_flow("WRITE_REVERSIBLE", ("Nein", "de"), speaker=Speaker())
    assert rig.core.confirmed == [("c1", False, True)]


async def test_plain_yes_is_not_enough() -> None:
    rig = await _confirm_flow("WRITE_REVERSIBLE", ("Ja", "de"), speaker=Speaker())
    assert rig.core.confirmed == []
    assert MESSAGES["de"]["screen"] in [t for t, _l, _w in rig.tts.said]


async def test_no_voice_confirmation_for_irreversible_actions() -> None:
    rig = await _confirm_flow("IRREVERSIBLE", ("Ja, bestätigt", "de"), speaker=Speaker())
    assert rig.core.confirmed == []
    assert MESSAGES["de"]["screen"] in [t for t, _l, _w in rig.tts.said]


async def test_no_voice_confirmation_without_speaker_check_or_when_off() -> None:
    for speaker, by_voice in [(None, True), (Speaker(), False)]:
        rig = await _confirm_flow(
            "WRITE_REVERSIBLE", ("Ja, bestätigt", "de"), speaker=speaker, by_voice=by_voice
        )
        assert rig.core.confirmed == []
        assert MESSAGES["de"]["screen"] in [t for t, _l, _w in rig.tts.said]


async def test_confirmation_by_a_foreign_voice_is_ignored() -> None:
    config = VoiceConfig(confirm_by_voice=True, end_silence_seconds=0.2, follow_up_seconds=0.0)
    gate = Speaker()
    rig = Rig(
        stt=FakeStt(("Trag ein", "de"), ("Ja, bestätigt", "de")),
        core=FakeCore(_confirm("WRITE_REVERSIBLE")),
        config=config,
        speaker=gate,
    )
    await rig.frames(WAKE)
    await rig.frames(*([SPEECH] * 5 + [QUIET] * 8))
    for _ in range(50):
        await asyncio.sleep(0)
        if rig.session.state is State.CONFIRM:
            break
    gate.accept = False  # jemand anderes spricht
    await rig.frames(*([SPEECH] * 5 + [QUIET] * 8))
    await rig.frames(QUIET, step=11.0)
    await rig.session.wait_idle()
    assert rig.core.confirmed == []


# --------------------------------------------------------------- Sprecher und Datenschutz
async def test_other_voices_are_ignored_when_the_speaker_check_is_on() -> None:
    rig = Rig(stt=FakeStt(("Wie spät ist es?", "de")), speaker=Speaker(accept=False))
    await rig.say_something()
    assert rig.core.asked == []


async def test_no_audio_or_transcript_is_written_to_disk(tmp_path: Path) -> None:
    before = set(os.listdir(tmp_path))
    rig = Rig(
        stt=FakeStt(("Streng geheime Frage", "de")),
        core=FakeCore(answer("Geheime Antwort steht hier.")),
    )
    await rig.say_something()
    await rig.session.shutdown()
    assert set(os.listdir(tmp_path)) == before


async def test_latency_stages_are_recorded_without_content() -> None:
    rig = Rig(stt=FakeStt(("Frage", "de")), core=FakeCore(answer("Antwort kommt jetzt.")))
    await rig.say_something()
    assert len(rig.session.turns) == 1
    stages = rig.session.turns[0].stages()
    assert set(stages) == {"aufnahme", "erkennung", "erster_text", "erster_ton"}
    assert set(rig.session.latency()) == set(stages)
    assert all("Frage" not in repr(v) for v in stages.values())
