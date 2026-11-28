"""Gesprächslogik des Sprachclients mit Attrappen: Wake Word, Sprechpausen, Folgefragen,
Unterbrechen, Schlaf- und Flüstermodus, Rückfragen per Stimme, Datenschutz."""

import asyncio
import contextlib
import os
from collections.abc import AsyncIterator, Callable
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
        self.events: list[str] = []
        self.reports: list[tuple[str, dict[str, str | bool | int]]] = []
        self.stops = 0

    async def ask(self, text: str, session_id: str | None) -> AsyncIterator[CoreEvent]:
        self.asked.append((text, session_id))
        for event in self.scripts.pop(0) if self.scripts else []:
            yield event

    async def confirm(self, request_id: str, approve: bool, by_voice: bool) -> bool:
        self.confirmed.append((request_id, approve, by_voice))
        return True

    async def voice_event(self, event: str) -> None:
        self.events.append(event)

    async def status(self, event: str, **fields: str | bool | int) -> None:
        self.reports.append((event, fields))

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
    # "Stopp" beendet nur das Sprechen und die eigene Anfrage, nicht den Not-Aus des Cores.
    assert rig.core.asked == [] and rig.core.stops == 0 and rig.sink.stops >= 1
    assert rig.session.state is State.WAITING


async def test_emergency_phrase_triggers_the_core_stop() -> None:
    rig = Rig(stt=FakeStt(("Not-Aus", "de")))
    await rig.say_something()
    assert rig.core.stops == 1 and rig.session.state is State.WAITING


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
    assert rig.sink.stops == 1 and rig.core.stops == 0  # ein einziges Unterbrechen, lokal
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
    assert rig.sink.stops == 1 and rig.core.stops == 0 and rig.session.state is State.WAITING


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


async def test_no_audio_or_transcript_is_written_to_disk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import builtins

    real_open = builtins.open

    def guarded_open(file: object, mode: str = "r", *args: object, **kwargs: object) -> object:
        assert not any(flag in mode for flag in "wax+"), f"Schreibzugriff auf {file!r}"
        return real_open(file, mode, *args, **kwargs)  # type: ignore[call-overload]

    monkeypatch.setattr(builtins, "open", guarded_open)
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


# ---------------------------------------------------------------- Prüfrunde-Funde (Block A)
async def test_external_emergency_stop_does_not_leave_the_session_stuck() -> None:
    from jarvis_voice.protocols import CoreNotice

    core = FakeCore([CoreNotice("Not-Aus: Die Anfrage wurde abgebrochen.")])
    rig = Rig(stt=FakeStt(("Frage", "de")), core=core)
    await rig.say_something()
    assert rig.session.state is State.WAITING  # nicht in DENKT hängen geblieben
    await rig.frames(WAKE)
    assert rig.session.state is State.LISTENING  # ein neues Wake Word wirkt wieder


async def test_confirm_state_stops_listening_after_one_utterance() -> None:
    rig = await _confirm_flow("WRITE_REVERSIBLE", ("Ja, bestätigt", "de"), speaker=Speaker())
    assert rig.stt.calls == 2  # eine Frage und eine Antwort, nicht eine je stillem Frame


async def test_repeated_loud_frames_interrupt_only_once() -> None:
    rig = await _speaking_rig("voice")
    await rig.frames(*([LOUD] * 30))
    await rig.session.wait_idle()
    assert rig.sink.stops == 1


async def test_foreign_voice_or_empty_recognition_opens_no_follow_up_window() -> None:
    for stt, speaker in [(FakeStt(("", None)), None), (FakeStt(("Hallo", "de")), Speaker(False))]:
        rig = Rig(stt=stt, speaker=speaker)
        await rig.say_something()
        assert rig.session.state is State.WAITING


async def test_hotkey_interrupt_keeps_the_sleep_state() -> None:
    rig = Rig()
    rig.session.sleep()
    await rig.session.interrupt()
    assert rig.session.state is State.SLEEP


async def test_failed_voice_confirmation_is_reported() -> None:
    class Refusing(FakeCore):
        async def confirm(self, request_id: str, approve: bool, by_voice: bool) -> bool:
            del request_id, approve, by_voice
            return False  # z. B. 403 oder schon beantwortet

    config = VoiceConfig(confirm_by_voice=True, end_silence_seconds=0.2, follow_up_seconds=0.0)
    rig = Rig(
        stt=FakeStt(("Trag ein", "de"), ("Ja, bestätigt", "de")),
        core=Refusing(_confirm("WRITE_REVERSIBLE")),
        config=config,
        speaker=Speaker(),
    )
    await rig.frames(WAKE)
    await rig.frames(*([SPEECH] * 5 + [QUIET] * 8))
    for _ in range(50):
        await asyncio.sleep(0)
        if rig.session.state is State.CONFIRM:
            break
    await rig.frames(*([SPEECH] * 5 + [QUIET] * 8))
    await rig.session.wait_idle()
    assert MESSAGES["de"]["screen"] in [t for t, _l, _w in rig.tts.said]


async def test_recognition_failure_does_not_get_stuck() -> None:
    class Broken(FakeStt):
        async def transcribe(self, pcm: bytes) -> Transcript:
            raise OSError("Modell weg")

    rig = Rig(stt=Broken())
    await rig.say_something()
    assert rig.session.state is State.WAITING


async def test_turn_history_is_bounded() -> None:
    rig = Rig()
    from jarvis_voice.session import MAX_TURNS, Turn

    rig.session.turns.extend(Turn(wake_at=0.0) for _ in range(MAX_TURNS + 20))
    rig.session._turn = Turn(wake_at=1.0)  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
    rig.session.parts.core = FakeCore(answer("Antwort kommt jetzt."))
    await rig.session.handle_text("Frage")
    assert len(rig.session.turns) <= MAX_TURNS


async def test_vad_state_is_reset_for_every_new_utterance() -> None:
    resets: list[int] = []

    class CountingVad(EnergyVad):
        def reset(self) -> None:
            resets.append(1)

    rig = Rig(stt=FakeStt(("Frage", "de")), core=FakeCore(answer("Antwort kommt jetzt.")))
    rig.session.parts.vad = CountingVad()
    await rig.say_something()
    assert len(resets) >= 2  # nach dem Wake Word und für die Folgefrage


# ------------------------------------------------------------------------- Block C (M2.9-M2.13)
class FakeTyper:
    def __init__(self) -> None:
        self.typed: list[str] = []

    def type(self, text: str) -> None:
        self.typed.append(text)


class FakeWindow:
    def __init__(self) -> None:
        self.handle: object = "fenster-1"

    def current(self) -> object | None:
        return self.handle


def _rig_with(
    typer: FakeTyper | None = None,
    window: FakeWindow | None = None,
    *,
    stt: FakeStt | None = None,
    speaker: Speaker | None = None,
) -> Rig:
    rig = Rig(stt=stt, speaker=speaker)
    rig.session.parts.typer = typer
    rig.session.parts.window = window
    return rig


async def _utter(rig: Rig) -> None:
    await rig.frames(*([SPEECH] * 5 + [QUIET] * 8))
    await rig.session.wait_idle()


async def _start_dictation(rig: Rig) -> None:
    await rig.frames(WAKE)
    await rig.frames(*([SPEECH] * 5 + [QUIET] * 8))
    await rig.session.wait_idle()


async def test_dictation_types_text_not_commands_and_audits_start_and_end() -> None:
    typer, window = FakeTyper(), FakeWindow()
    stt = FakeStt(
        ("Diktiermodus an", "de"), ("Hallo Welt, das ist ein Test", "de"), ("Diktat Ende", "de")
    )
    rig = _rig_with(typer, window, stt=stt)
    await _start_dictation(rig)
    assert rig.session.state is State.DICTATION and rig.core.events == ["dictation_start"]
    await _utter(rig)
    assert typer.typed == ["Hallo Welt, das ist ein Test "] and rig.core.asked == []  # nie zum Core
    assert rig.session.state is State.DICTATION  # bleibt an, hört weiter zu
    await _utter(rig)
    assert rig.session.state is State.WAITING
    assert rig.core.events == ["dictation_start", "dictation_end"]
    assert typer.typed == ["Hallo Welt, das ist ein Test "]  # "Diktat Ende" wird nicht getippt


async def test_dictation_never_types_control_characters() -> None:
    typer = FakeTyper()
    rig = _rig_with(
        typer,
        FakeWindow(),
        stt=FakeStt(("Diktiermodus an", "de"), ("Zeile eins\nZeile zwei\t\x1b[A\r", "de")),
    )
    await _start_dictation(rig)
    await _utter(rig)
    (typed,) = typer.typed
    assert not any(ord(c) < 32 or ord(c) == 127 for c in typed)
    assert typed == "Zeile einsZeile zwei[A "


async def test_dictation_ends_when_the_window_changes_and_types_nothing_more() -> None:
    typer, window = FakeTyper(), FakeWindow()
    rig = _rig_with(
        typer,
        window,
        stt=FakeStt(("Diktiermodus an", "de"), ("erster Satz", "de"), ("zweiter Satz", "de")),
    )
    await _start_dictation(rig)
    await _utter(rig)
    window.handle = "fenster-2"  # Master wechselt das Fenster
    await _utter(rig)
    await rig.session.wait_idle()
    assert typer.typed == ["erster Satz "]  # der zweite Satz landet nirgends
    assert rig.session.state is State.WAITING and rig.core.events[-1] == "dictation_end"


async def test_dictation_ends_after_long_silence() -> None:
    rig = _rig_with(FakeTyper(), FakeWindow(), stt=FakeStt(("Diktiermodus an", "de")))
    rig.session.config = VoiceConfig(end_silence_seconds=0.2, dictation_idle_seconds=5.0)
    await _start_dictation(rig)
    assert rig.session.state is State.DICTATION
    await rig.frames(QUIET, step=6.0)
    await rig.session.wait_idle()
    assert rig.session.state is State.WAITING and rig.core.events[-1] == "dictation_end"


async def test_dictation_by_foreign_voice_is_ignored() -> None:
    typer, gate = FakeTyper(), Speaker()
    rig = _rig_with(
        typer,
        FakeWindow(),
        stt=FakeStt(("Diktiermodus an", "de"), ("fremde Stimme", "de")),
        speaker=gate,
    )
    await _start_dictation(rig)
    gate.accept = False
    await _utter(rig)
    assert typer.typed == []


async def test_dictation_without_typer_says_so_and_audits_nothing() -> None:
    rig = _rig_with(None, None, stt=FakeStt(("Diktiermodus an", "de")))
    await _start_dictation(rig)
    assert rig.core.events == [] and rig.session.state is not State.DICTATION
    assert MESSAGES["de"]["no_dictation"] in [t for t, _l, _w in rig.tts.said]


async def test_dictation_hotkey_toggles_and_sleep_or_interrupt_end_it() -> None:
    rig = _rig_with(FakeTyper(), FakeWindow())
    rig.session.toggle_dictation()
    await rig.session.wait_idle()
    assert rig.session.state is State.DICTATION
    rig.session.toggle_dictation()
    await rig.session.wait_idle()
    assert rig.core.events == ["dictation_start", "dictation_end"]
    rig.session.toggle_dictation()
    await rig.session.wait_idle()
    rig.session.sleep()  # Schlaf beendet das Diktat
    await rig.session.wait_idle()
    assert rig.core.events[-1] == "dictation_end" and rig.session.state is State.SLEEP


# ------------------------------------------------------------------------- Tastenkürzel
async def test_listen_hotkey_starts_listening_like_the_wake_word() -> None:
    rig = Rig(stt=FakeStt(("Wie spät ist es?", "de")), core=FakeCore(answer("Es ist spät genug.")))
    rig.session.trigger_listen()
    assert rig.session.state is State.LISTENING and "wake" in rig.indicator.cues
    await rig.frames(*([SPEECH] * 5 + [QUIET] * 8))
    await rig.session.wait_idle()
    assert rig.core.asked == [("Wie spät ist es?", None)]


async def test_listen_hotkey_interrupts_an_answer_and_listens() -> None:
    rig = await _speaking_rig("key")
    rig.session.trigger_listen()
    await rig.session.wait_idle()
    assert rig.sink.stops >= 1 and rig.session.state is State.LISTENING


async def test_listen_hotkey_wakes_from_sleep_and_listens() -> None:
    source = FakeSource([QUIET] * 3)
    rig = Rig(source=source)
    rig.session.sleep()
    runner = asyncio.create_task(rig.session.run())
    await asyncio.sleep(0.05)
    assert rig.source.closed >= 1  # Mikrofon zu
    rig.session.trigger_listen()
    await asyncio.wait_for(runner, 5)
    assert rig.source.opened >= 2
    assert State.LISTENING in rig.indicator.states


async def test_mute_hotkey_closes_and_reopens_the_microphone() -> None:
    rig = Rig()
    rig.session.toggle_mute()
    assert rig.session.state is State.SLEEP
    rig.session.toggle_mute()
    assert rig.session._wake_event.is_set()  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]


async def test_interrupt_hotkey_stops_speaking() -> None:
    rig = await _speaking_rig("key")
    rig.session.request_interrupt()
    await rig.session.wait_idle()
    assert rig.sink.stops == 1 and rig.session.state is State.WAITING


# ------------------------------------------------------------------------ Doppelklatschen
async def test_double_clap_starts_listening_and_speech_does_not() -> None:
    from jarvis_voice.clap import ClapDetector

    rig = Rig()
    rig.session.parts.clap = ClapDetector(0.3)
    bang = tone(1000, FRAME_SECONDS, level=0.9)
    await rig.frames(bang, QUIET, QUIET, QUIET, QUIET, QUIET, QUIET)  # ein Klatschen
    assert rig.session.state is State.WAITING
    await rig.frames(bang, QUIET, QUIET)  # zweites nach ca. 0,2 s
    assert rig.session.state is State.LISTENING
    rig2 = Rig()
    rig2.session.parts.clap = ClapDetector(0.3)
    await rig2.frames(*([tone(250, FRAME_SECONDS, level=0.9)] * 20 + [QUIET] * 5))
    assert rig2.session.state is State.WAITING  # Sprache ist kein Klatschen


async def test_clap_does_not_wake_the_sleeping_session() -> None:
    from jarvis_voice.clap import ClapDetector

    rig = Rig()
    rig.session.parts.clap = ClapDetector(0.3)
    rig.session.sleep()
    bang = tone(1000, FRAME_SECONDS, level=0.9)
    await rig.frames(bang, QUIET, QUIET, QUIET, QUIET, QUIET, bang, QUIET, QUIET)
    assert rig.session.state is State.SLEEP  # das Mikrofon ist zu, nur das Kürzel weckt


# ---------------------------------------------------------------------- Wake Word mitten im Satz
async def _pre_roll_rig(seconds: float, text: str = "Wie spät ist es") -> Rig:
    config = VoiceConfig(end_silence_seconds=0.2, pre_roll_seconds=seconds, follow_up_seconds=0.0)
    return Rig(
        stt=FakeStt((text, "de")), core=FakeCore(answer("Es ist spät genug.")), config=config
    )


async def test_audio_before_the_wake_word_goes_into_the_recognition() -> None:
    sizes: list[int] = []

    class Measuring(FakeStt):
        async def transcribe(self, pcm: bytes) -> Transcript:
            sizes.append(len(pcm))
            return await super().transcribe(pcm)

    rig = await _pre_roll_rig(6.0)
    rig.session.parts.stt = Measuring(("Wie spät ist es hey jarvis", "de"))
    await rig.frames(*([SPEECH] * 60))  # das Gespräch davor (länger als der Wake-Satz)
    await rig.frames(WAKE)
    await rig.frames(*([QUIET] * 8))  # danach Stille: das Satzende
    await rig.session.wait_idle()
    assert sizes and sizes[0] >= 61 * FRAME_SAMPLES_BYTES  # Gespräch davor + Wake Word
    assert rig.core.asked and rig.core.asked[0][0].startswith("Wie spät ist es")


FRAME_SAMPLES_BYTES = 1024


async def test_without_pre_roll_nothing_is_kept_in_memory() -> None:
    rig = await _pre_roll_rig(0.0)
    assert rig.session._ring is None  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
    await rig.frames(*([SPEECH] * 30))
    await rig.frames(WAKE)
    assert rig.session.state is State.LISTENING
    assert len(rig.session._buffer) == 0  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]


async def test_pre_roll_is_capped_cleared_after_use_and_on_sleep() -> None:
    rig = await _pre_roll_rig(1.0)
    ring = rig.session._ring  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
    assert ring is not None
    await rig.frames(*([SPEECH] * 100))
    assert len(ring) == 31  # nie mehr als ca. 1 s (gerundet) vorgehalten
    await rig.frames(WAKE)
    assert len(ring) == 0  # einmal verwendet, nicht aufgehoben
    rig.session._to_waiting()  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
    await rig.frames(*([SPEECH] * 10))
    assert len(ring) == 10
    rig.session.sleep()
    assert len(ring) == 0  # im Schlaf bleibt nichts zurück


# ----------------------------------------------------------- Prüffunde Block C (Nachbesserung)
class _SlowStt(FakeStt):
    """Erkennung, die erst nach einem Signal fertig wird (Diktat endet währenddessen)."""

    def __init__(self, *results: tuple[str, str | None]) -> None:
        super().__init__(*results)
        self.gate = asyncio.Event()

    async def transcribe(self, pcm: bytes) -> Transcript:
        await self.gate.wait()
        return await super().transcribe(pcm)


async def test_dictation_utterance_never_reaches_the_core_when_dictation_ended_meanwhile() -> None:
    typer = FakeTyper()
    stt = _SlowStt(("Das ist ein vertraulicher Satz", "de"))
    rig = _rig_with(typer, FakeWindow(), stt=stt)
    rig.session.toggle_dictation()
    await rig.session.wait_idle()
    await rig.frames(*([SPEECH] * 5 + [QUIET] * 8))  # die Erkennung hängt noch
    rig.session.toggle_dictation()  # Diktat endet währenddessen
    stt.gate.set()
    await rig.session.wait_idle()
    assert rig.core.asked == []
    assert typer.typed == []


async def test_sleep_cancels_a_running_reply() -> None:
    stt = _SlowStt(("Wie spät ist es", "de"))
    rig = _rig_with(stt=stt)
    await rig.frames(WAKE)
    await rig.frames(*([SPEECH] * 5 + [QUIET] * 8))
    rig.session.sleep()
    stt.gate.set()
    await rig.session.wait_idle()
    assert rig.core.asked == []
    assert rig.session.state is State.SLEEP


async def test_state_stays_asleep_until_wake() -> None:
    rig = _rig_with()
    rig.session.sleep()
    rig.session._to_waiting()  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
    assert rig.session.state is State.SLEEP


async def test_shutdown_audits_the_end_of_a_running_dictation() -> None:
    rig = _rig_with(FakeTyper(), FakeWindow())
    rig.session.toggle_dictation()
    await rig.session.wait_idle()
    await rig.session.shutdown()
    assert rig.core.events == ["dictation_start", "dictation_end"]


# ------------------------------------------------------------------- Meldungen ans HUD (M3)
async def _until(condition: Callable[[], bool], steps: int = 300) -> None:
    for _ in range(steps):
        if condition():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("Bedingung nicht erreicht")


async def test_run_reports_state_and_microphone_to_the_hud_without_content() -> None:
    source = FakeSource([WAKE] + [SPEECH] * 5 + [QUIET] * 8)
    rig = Rig(stt=FakeStt(("Gute Nacht, Jarvis", "de")), source=source)
    runner = asyncio.create_task(rig.session.run())
    await _until(lambda: rig.session.state is State.SLEEP and bool(rig.source.closed))
    # Schlaf: "aus" kommt, solange die Sitzung noch läuft (nicht erst beim Beenden).
    await _until(lambda: ("mic", {"on": False}) in rig.core.reports)
    asleep = len(rig.core.reports)
    rig.session.wake()
    await asyncio.wait_for(runner, 5)
    reports = rig.core.reports
    assert reports[0] == ("state", {"state": "WARTET"})
    assert ("mic", {"on": True}) in reports[:2]  # "an" schon beim Öffnen
    assert ("state", {"state": "HÖRT ZU"}) in reports
    assert ("mic", {"on": True}) in reports[asleep:]  # nach dem Wecken wieder an
    assert reports[-1] == ("mic", {"on": False})  # Ende: Mikrofon zu
    assert all(kind in {"state", "mic", "heartbeat", "level"} for kind, _ in reports)
    assert "Gute Nacht" not in repr(reports)


async def test_heartbeat_is_sent_when_nothing_happens(monkeypatch: pytest.MonkeyPatch) -> None:
    from jarvis_voice import session as session_module

    monkeypatch.setattr(session_module, "HEARTBEAT_SECONDS", 0.05)
    rig = Rig()
    task = asyncio.create_task(rig.session._report_loop({}))  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
    await _until(lambda: ("heartbeat", {}) in rig.core.reports)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


async def test_a_slow_core_gets_only_the_newest_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hängt der Core, staut sich nichts: je Art bleibt nur die neueste Meldung, und jede
    Meldung hat eine Frist. Das Zuhören läuft unterdessen weiter."""
    from jarvis_voice import session as session_module

    monkeypatch.setattr(session_module, "REPORT_TIMEOUT_SECONDS", 0.05)
    gate = asyncio.Event()

    class SlowCore(FakeCore):
        async def status(self, event: str, **fields: str | bool | int) -> None:
            self.reports.append((event, fields))
            await gate.wait()  # hängt, bis die Frist greift

    rig = Rig(core=SlowCore())
    reports: dict[str, dict[str, str | bool | int]] = {}
    rig.session._reports = reports  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
    task = asyncio.create_task(rig.session._report_loop(reports))  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
    for _ in range(50):
        rig.session._set(State.LISTENING)  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
        rig.session._set(State.WAITING)  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
        assert len(reports) <= 1  # nie mehr als eine Meldung je Art wartet
    await rig.say_something()  # das Zuhören läuft weiter, obwohl der Core hängt
    await _until(lambda: not reports)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    assert len(rig.core.reports) < 10  # 100 Zustandswechsel, aber nur wenige Meldungen


async def test_levels_go_to_the_hud_throttled_and_only_as_numbers() -> None:
    from jarvis_voice.audio import level

    assert level(QUIET) == 0 and 1 <= level(SPEECH) <= 7 and level(WAKE) >= level(SPEECH)
    rig = Rig(stt=FakeStt(("Wie spät ist es", "de")), core=FakeCore(answer("Es ist spät.")))
    reports: dict[str, dict[str, str | bool | int]] = {}
    rig.session._reports = reports  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
    seen: list[dict[str, str | bool | int]] = []
    original = rig.session._report  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]

    def spy(event: str, **fields: str | bool | int) -> None:
        if event == "level":
            seen.append(fields)
        original(event, **fields)

    rig.session._report = spy  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
    await rig.frames(WAKE)
    await rig.frames(*([SPEECH] * 16 + [QUIET] * 8))  # 0,77 s Zuhören
    await rig.session.wait_idle()
    mic = [f for f in seen if f["source"] == "mic"]
    speaker = [f for f in seen if f["source"] == "speaker"]
    assert 3 <= len(mic) <= 8  # gedrosselt: höchstens 8 je Sekunde
    assert speaker, "beim Sprechen kommt der Pegel der Ausgabe"
    assert all(set(f) == {"level", "source"} and isinstance(f["level"], int) for f in seen)


async def test_speaker_level_follows_the_whole_sentence() -> None:
    """Ein Satz kommt als ein langes Stück; der Pegel läuft im Takt der Wiedergabe mit."""

    class RealTimeSink(FakeSink):
        async def play(self, pcm: bytes, sample_rate: int) -> None:
            await super().play(pcm, sample_rate)
            await asyncio.sleep(len(pcm) / 2 / sample_rate)  # so lange klingt es

    class LongTts(FakeTts):
        async def synthesize(
            self, text: str, language: str, whisper: bool
        ) -> AsyncIterator[tuple[bytes, int]]:
            self.said.append((text, language, whisper))
            yield tone(300, 0.6, level=0.3), 16_000

    rig = Rig()
    rig.session.parts.sink = RealTimeSink()
    rig.session.parts.tts = LongTts()
    rig.session._reports = {}  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
    levels: list[dict[str, str | bool | int]] = []
    original = rig.session._report  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]

    def spy(event: str, **fields: str | bool | int) -> None:
        if event == "level" and fields["source"] == "speaker":
            levels.append(fields)
        original(event, **fields)

    rig.session._report = spy  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
    await rig.session._say("Ein langer Satz.")  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
    assert len(levels) >= 3  # 0,6 s Ton, ein Wert je 0,125 s (Spielraum für langsame Rechner)
    assert all(isinstance(f["level"], int) and f["level"] > 0 for f in levels)
