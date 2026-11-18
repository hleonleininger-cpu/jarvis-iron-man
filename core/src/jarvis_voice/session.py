"""Gesprächslogik des Sprachclients (M2): Zustandsautomat über Frames und Äußerungen.

WARTET (nur Wake Word) -> HÖRT ZU -> DENKT -> SPRICHT -> (Folgefrage ohne Wake Word) -> ...
Alles läuft gegen die Protokolle aus `protocols.py`; das Mikrofon wird nur geöffnet, solange nicht
SCHLAF ist. Aufnahmen bleiben im Arbeitsspeicher; Log und Audit bekommen nie Transkripte.
"""

import asyncio
import contextlib
import time
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from typing import Any

from jarvis_core.config.settings import VoiceConfig
from jarvis_voice.commands import Command, parse_command
from jarvis_voice.echo import EchoGuard
from jarvis_voice.protocols import (
    SAMPLE_RATE,
    AudioSink,
    AudioSource,
    CoreClient,
    CoreConfirm,
    CoreDelta,
    CoreDone,
    CoreError,
    Indicator,
    SpeakerGate,
    SpeechToText,
    State,
    TextToSpeech,
    TurnDetector,
    VoiceActivity,
    WakeWordDetector,
)
from jarvis_voice.sentences import SentenceChunker

MAX_TURNS = 50  # so viele Zeitmessungen bleiben für `jarvis voice bench` im Speicher
BARGE_IN_FRAMES = 4  # so viele Sprach-Frames in Folge (ca. 130 ms) unterbrechen die Ausgabe
CONFIRM_SECONDS = 10.0  # Zeitfenster für "Ja, bestätigt"
WHISPER_MAX_SENTENCES = 2
MESSAGES = {
    "de": {
        "screen": "Bitte am Bildschirm oder am Handy bestätigen.",
        "sleep": "Gute Nacht.",
        "core_down": "Der Kern ist nicht erreichbar.",
    },
    "en": {
        "screen": "Please confirm on the screen or on your phone.",
        "sleep": "Good night.",
        "core_down": "The core is not reachable.",
    },
}


@dataclass
class Turn:
    """Zeiten einer Runde in Sekunden seit dem Wake Word; nie Inhalte (für `jarvis voice bench`)."""

    wake_at: float
    utterance_end: float | None = None
    transcribed: float | None = None
    first_text: float | None = None
    first_audio: float | None = None

    def stages(self) -> dict[str, float]:
        marks = {
            "aufnahme": self.utterance_end,
            "erkennung": self.transcribed,
            "erster_text": self.first_text,
            "erster_ton": self.first_audio,
        }
        out: dict[str, float] = {}
        previous = self.wake_at
        for name, mark in marks.items():
            if mark is not None:
                out[name] = round(mark - previous, 3)
                previous = mark
        return out


@dataclass
class Parts:
    """Die Bausteine, aus denen die Sitzung besteht (Engines oder Attrappen)."""

    source: AudioSource
    sink: AudioSink
    wake: WakeWordDetector
    vad: VoiceActivity
    turn: TurnDetector
    stt: SpeechToText
    tts: TextToSpeech
    core: CoreClient
    indicator: Indicator
    speaker: SpeakerGate | None = None


@dataclass
class VoiceSession:
    parts: Parts
    config: VoiceConfig
    clock: Callable[[], float] = time.monotonic
    state: State = State.WAITING
    whisper: bool = False
    session_id: str | None = None
    turns: list[Turn] = field(default_factory=lambda: [])
    on_dictation: Callable[[bool], None] | None = None  # Block C (M2.12)

    def __post_init__(self) -> None:
        self._buffer = bytearray()
        self._heard = False
        self._deadline = 0.0
        self._speech_frames = 0
        self._echo = EchoGuard()
        self._reply: asyncio.Task[None] | None = None
        self._turn: Turn | None = None
        self._language = "de"
        self._answer: asyncio.Future[bool | None] | None = None
        self._wake_event = asyncio.Event()
        self._tasks: set[asyncio.Task[None]] = set()
        self.parts.indicator.show(self.state)

    # ------------------------------------------------------------------ Zustand und Anzeige
    def _set(self, state: State) -> None:
        if state is not self.state:
            self.state = state
            self.parts.indicator.show(state)

    def _begin_listening(self, state: State, timeout: float) -> None:
        self._buffer.clear()
        self._heard = False
        self._speech_frames = 0
        self.parts.turn.reset()
        self._deadline = self.clock() + timeout
        self._set(state)

    def _to_waiting(self) -> None:
        self.parts.wake.reset()
        self._set(State.WAITING)

    # ------------------------------------------------------------------------ Hauptschleife
    async def run(self) -> None:
        """Liest Frames, bis die Quelle endet. In SCHLAF wird das Mikrofon geschlossen."""
        await self.parts.source.open()
        try:
            while True:
                if self.state is State.SLEEP:
                    self.parts.source.close()
                    self._wake_event.clear()
                    await self._wake_event.wait()
                    await self.parts.source.open()
                    self._to_waiting()
                    self.parts.indicator.cue("wake")
                async for frame in self.parts.source.frames():
                    await self.on_frame(frame)
                    if self.state is State.SLEEP:
                        break
                else:
                    return  # Quelle zu Ende (Tests, Gerät weg)
        finally:
            await self.shutdown()

    async def wait_idle(self) -> None:
        """Wartet, bis alle laufenden Verarbeitungen (Erkennung, Antwort) fertig sind."""
        while pending := [task for task in self._tasks if not task.done()]:
            await asyncio.gather(*pending, return_exceptions=True)

    async def shutdown(self) -> None:
        self.parts.source.close()
        self.parts.sink.stop()
        for task in list(self._tasks):
            task.cancel()
        for task in list(self._tasks):
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task

    # ---------------------------------------------------------------------------- Frames
    async def on_frame(self, frame: bytes) -> None:
        now = self.clock()
        if self.state is State.WAITING:
            if self.parts.wake.process(frame):
                self._turn = Turn(wake_at=now)
                self.parts.indicator.cue("wake")
                self._begin_listening(State.LISTENING, self.config.listen_timeout_seconds)
        elif self.state in (State.LISTENING, State.CONFIRM):
            self._listen(frame, now)
        elif self.state is State.SPEAKING:
            self._maybe_barge_in(frame, now)

    def _listen(self, frame: bytes, now: float) -> None:
        speech = self.parts.vad.is_speech(frame)
        self._buffer += frame
        if speech:
            self._heard = True
        ended = self._heard and self.parts.turn.feed(frame, speech)
        too_long = len(self._buffer) / 2 / SAMPLE_RATE >= self.config.max_utterance_seconds
        if ended or too_long:
            self._finish_utterance(now)
        elif not self._heard and now > self._deadline:
            self._timeout()

    def _timeout(self) -> None:
        if self._answer is not None and not self._answer.done():
            self._set(State.THINKING)  # nicht weiter zuhören; die Bestätigung bleibt offen
            self._answer.set_result(None)
            return
        self._to_waiting()

    def _finish_utterance(self, now: float) -> None:
        pcm = bytes(self._buffer)
        self._buffer.clear()
        if self._turn is not None:
            self._turn.utterance_end = now
        self.parts.indicator.cue("listen_done")
        self._heard = False
        self.parts.turn.reset()
        self._set(
            State.THINKING
        )  # auch bei CONFIRM: nach einer Äußerung wird nicht weiter zugehört
        self._spawn(self._process(pcm))

    def _maybe_barge_in(self, frame: bytes, now: float) -> None:
        """Unterbrechen per Stimme (nur `barge_in = "voice"`); Wake Word wird hier ignoriert."""
        if self.config.barge_in != "voice":
            return
        user = self.parts.vad.is_speech(frame) and self._echo.is_user_speech(frame, now)
        self._speech_frames = self._speech_frames + 1 if user else 0
        if self._speech_frames >= BARGE_IN_FRAMES:
            # Zustand sofort umsetzen, damit nur EIN Unterbrechen startet.
            self._begin_listening(State.LISTENING, self.config.listen_timeout_seconds)
            self._buffer += frame
            self._heard = True
            self._spawn(self._cut_off())

    # ---------------------------------------------------------------------- Äußerungen
    def _spawn(self, coro: Coroutine[Any, Any, None]) -> None:
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _process(self, pcm: bytes) -> None:
        try:
            await self._recognize_and_answer(pcm)
        except asyncio.CancelledError:
            raise
        except (
            Exception
        ):  # Erkennung oder Ausgabe gescheitert: nicht in DENKT/SPRICHT hängen bleiben
            if self._answer is not None and not self._answer.done():
                self._answer.set_result(None)
            self._to_waiting()

    async def _recognize_and_answer(self, pcm: bytes) -> None:
        transcript = await self.parts.stt.transcribe(pcm)
        if self._turn is not None:
            self._turn.transcribed = self.clock()
        text = transcript.text.strip()
        if self.config.language != "auto":
            self._language = self.config.language
        elif transcript.language in ("de", "en"):
            self._language = transcript.language or "de"
        # Mit Sprecherprüfung zählt nur Masters Stimme (M2.9); sonst jede.
        if text and self.parts.speaker is not None and not self.parts.speaker.verify(pcm):
            text = ""
        if self._answer is not None and not self._answer.done():
            self._resolve_answer(text)
            return
        if not text:  # nichts oder eine fremde Stimme: kein neues Folgefragen-Fenster öffnen
            self._to_waiting()
            return
        await self.handle_text(text)

    async def handle_text(self, text: str, language: str | None = None) -> None:
        """Eine fertig erkannte Äußerung (auch vom Textmodus `jarvis voice text` genutzt)."""
        if language in ("de", "en"):
            self._language = language or self._language
        command = parse_command(text)
        if command is not None:
            await self._run_command(command)
            return
        self._reply = asyncio.ensure_future(self._answer_with_core(text))
        self._tasks.add(self._reply)
        self._reply.add_done_callback(self._tasks.discard)
        with contextlib.suppress(asyncio.CancelledError):
            await self._reply

    async def _run_command(self, command: Command) -> None:
        match command:
            case Command.STOP | Command.EMERGENCY:
                await self.interrupt(listen_again=False, emergency=command is Command.EMERGENCY)
            case Command.SLEEP:
                await self.interrupt(listen_again=False)
                await self._say(MESSAGES[self._language]["sleep"])
                self.sleep()
            case Command.WHISPER_ON | Command.WHISPER_OFF:
                self.whisper = command is Command.WHISPER_ON
                self._after_reply()
            case Command.DICTATION_ON | Command.DICTATION_OFF:
                if self.on_dictation is not None:
                    self.on_dictation(command is Command.DICTATION_ON)
                self._after_reply()
            case _:  # "Ja"/"Nein" ohne offene Rückfrage
                self._after_reply()

    # ------------------------------------------------------------------- Antwort des Cores
    async def _answer_with_core(self, text: str) -> None:
        self._set(State.THINKING)
        chunker = SentenceChunker()
        spoken = 0
        aborted = False
        try:
            async for event in self.parts.core.ask(text, self.session_id):
                if isinstance(event, CoreDelta):
                    if self._turn is not None and self._turn.first_text is None:
                        self._turn.first_text = self.clock()
                    for sentence in chunker.feed(event.text):
                        spoken = await self._say_limited(sentence, spoken)
                elif isinstance(event, CoreConfirm):
                    await self._handle_confirm(event)
                elif isinstance(event, CoreDone):
                    self.session_id = event.session_id or self.session_id
                elif isinstance(event, CoreError):
                    await self._say(MESSAGES[self._language]["core_down"])
                elif "Not-Aus" in event.message:  # CoreNotice: Abbruch durch den Not-Aus
                    aborted = True
                    break
            if not aborted:
                for sentence in chunker.flush():
                    spoken = await self._say_limited(sentence, spoken)
        except asyncio.CancelledError:
            raise
        finally:
            if self._turn is not None:
                self.turns.append(self._turn)
                del self.turns[:-MAX_TURNS]
                self._turn = None
        if aborted:  # Not-Aus von außen (Web, `jarvis stop`): nicht weiter zuhören
            self._to_waiting()
        else:
            self._after_reply()

    async def _say_limited(self, sentence: str, spoken: int) -> int:
        if self.whisper and spoken >= WHISPER_MAX_SENTENCES:
            return spoken
        await self._say(sentence)
        return spoken + 1

    async def _say(self, sentence: str) -> None:
        self._set(State.SPEAKING)
        async for pcm, rate in self.parts.tts.synthesize(sentence, self._language, self.whisper):
            if self._turn is not None and self._turn.first_audio is None:
                self._turn.first_audio = self.clock()
            self._echo.played(pcm, self.clock(), len(pcm) / 2 / rate)
            await self.parts.sink.play(pcm, rate)

    def _after_reply(self) -> None:
        """Nach der Antwort: Folgefrage ohne Wake Word, sonst zurück zum Warten."""
        if self.state is State.SLEEP:
            return
        if self.config.follow_up_seconds > 0:
            self.parts.indicator.cue("listen")
            self._begin_listening(State.LISTENING, self.config.follow_up_seconds)
        else:
            self._to_waiting()

    # ------------------------------------------------------------------ Rückfragen
    async def _handle_confirm(self, event: CoreConfirm) -> None:
        voice_ok = (
            self.config.confirm_by_voice
            and self.parts.speaker is not None
            and event.risk == "WRITE_REVERSIBLE"
        )
        await self._say(f"{event.preview} {event.question}")
        if not voice_ok:
            await self._say(MESSAGES[self._language]["screen"])
            return
        self._answer = asyncio.get_running_loop().create_future()
        self._begin_listening(State.CONFIRM, CONFIRM_SECONDS)
        try:
            answer = await self._answer
        finally:
            self._answer = None
        if answer is None:
            await self._say(MESSAGES[self._language]["screen"])
            return
        if not await self.parts.core.confirm(event.id, answer, by_voice=True):
            await self._say(MESSAGES[self._language]["screen"])  # abgelehnt oder schon erledigt

    def _resolve_answer(self, text: str) -> None:
        command = parse_command(text) if text else None
        answer: bool | None = None
        if command is Command.YES:
            answer = True
        elif command is Command.NO:
            answer = False
        if self._answer is not None and not self._answer.done():
            self._answer.set_result(answer)

    # -------------------------------------------------------------------- Steuerung
    async def interrupt(self, listen_again: bool = False, emergency: bool = False) -> None:
        """Unterbrechen (Tastenkürzel, "Stopp"): Ausgabe verstummt sofort und die eigene
        Anfrage wird beendet (der Strom wird geschlossen, der Core bricht sie dann ab). Den
        Not-Aus des Cores, der ALLE Anfragen aller Geräte abbricht, löst nur die Phrase "Not-Aus"
        aus (`emergency=True`)."""
        if self.state is State.SLEEP:  # das Mikrofon ist zu: am Zustand nichts ändern
            self.parts.sink.stop()
            return
        await self._cut_off(emergency)
        if listen_again:
            self._begin_listening(State.LISTENING, self.config.listen_timeout_seconds)
        else:
            self._to_waiting()

    async def _cut_off(self, emergency: bool = False) -> None:
        self.parts.sink.stop()
        reply = self._reply
        if reply is not None and not reply.done() and reply is not asyncio.current_task():
            reply.cancel()
        if emergency:
            await self.parts.core.stop()
        self._speech_frames = 0

    def sleep(self) -> None:
        """Mikrofon ganz schließen (Schlafmodus, harte Stummschaltung). Nur `wake()` weckt."""
        self.parts.sink.stop()
        self._set(State.SLEEP)

    def wake(self) -> None:
        """Tastenkürzel: aus dem Schlaf aufwecken (Mikrofon wird wieder geöffnet)."""
        self._wake_event.set()
        if self.state is not State.SLEEP:
            self._to_waiting()

    def latency(self) -> dict[str, float]:
        """Mittelwerte der gemessenen Stufen über alle Runden (nur Zeiten)."""
        sums: dict[str, list[float]] = {}
        for turn in self.turns:
            for name, value in turn.stages().items():
                sums.setdefault(name, []).append(value)
        return {name: round(sum(v) / len(v), 3) for name, v in sums.items()}
