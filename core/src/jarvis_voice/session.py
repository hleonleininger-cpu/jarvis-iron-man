"""Gesprächslogik des Sprachclients (M2): Zustandsautomat über Frames und Äußerungen.

WARTET (nur Wake Word) -> HÖRT ZU -> DENKT -> SPRICHT -> (Folgefrage ohne Wake Word) -> ...
Alles läuft gegen die Protokolle aus `protocols.py`; das Mikrofon wird nur geöffnet, solange nicht
SCHLAF ist. Aufnahmen bleiben im Arbeitsspeicher; Log und Audit bekommen nie Transkripte.
"""

import asyncio
import contextlib
import time
from collections import deque
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from typing import Any

from jarvis_core.config.settings import VoiceConfig
from jarvis_voice.audio import level as audio_level
from jarvis_voice.clap import ClapDetector
from jarvis_voice.commands import Command, parse_command
from jarvis_voice.echo import EchoGuard
from jarvis_voice.protocols import (
    FRAME_SECONDS,
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
    TextTyper,
    TurnDetector,
    VoiceActivity,
    WakeWordDetector,
    WindowWatcher,
)
from jarvis_voice.sentences import SentenceChunker

WAKE_PHRASE_FRAMES = 40  # ca. 1,3 s: so lang ist "Hey Jarvis" samt Nachlauf höchstens
MIN_CONTEXT_FRAMES = 6  # so viel Sprache davor gilt als Gespräch (ca. 0,2 s)
HEARTBEAT_SECONDS = 5.0  # Lebenszeichen ans HUD, wenn sonst nichts zu melden ist
LEVEL_SECONDS = 0.125  # höchstens 8 Pegelmeldungen je Sekunde ans HUD (M3.4)
REPORT_TIMEOUT_SECONDS = 2.0  # höchstens so lange wartet eine Meldung auf den Core
MAX_TEXT_CHARS = 500  # so viel Text tippt das Diktat je Äußerung höchstens
MAX_TURNS = 50  # so viele Zeitmessungen bleiben für `jarvis voice bench` im Speicher
BARGE_IN_FRAMES = 4  # so viele Sprach-Frames in Folge (ca. 130 ms) unterbrechen die Ausgabe
CONFIRM_SECONDS = 10.0  # Zeitfenster für "Ja, bestätigt"
WHISPER_MAX_SENTENCES = 2
MESSAGES = {
    "de": {
        "screen": "Bitte am Bildschirm oder am Handy bestätigen.",
        "sleep": "Gute Nacht.",
        "core_down": "Der Kern ist nicht erreichbar.",
        "no_dictation": "Das Diktieren ist hier nicht verfügbar.",
    },
    "en": {
        "screen": "Please confirm on the screen or on your phone.",
        "sleep": "Good night.",
        "core_down": "The core is not reachable.",
        "no_dictation": "Dictation is not available here.",
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
    typer: TextTyper | None = None  # Diktiermodus (M2.12); None = nicht verfügbar
    window: WindowWatcher | None = None  # erkennt den Fensterwechsel beim Diktieren
    clap: ClapDetector | None = None  # Doppelklatschen als Start (M2.13)


@dataclass
class VoiceSession:
    parts: Parts
    config: VoiceConfig
    clock: Callable[[], float] = time.monotonic
    state: State = State.WAITING
    whisper: bool = False
    session_id: str | None = None
    turns: list[Turn] = field(default_factory=lambda: [])

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
        # Wake Word mitten im Satz (M2.10): die letzten Sekunden bleiben NUR im Arbeitsspeicher.
        pre_roll = self.config.pre_roll_seconds
        self._ring: deque[bytes] | None = (
            deque(maxlen=max(1, round(pre_roll / FRAME_SECONDS))) if pre_roll > 0 else None
        )
        self._dictating = False
        self._dictation_window: object | None = None
        self._listen_after_wake = False
        self._waking = False
        # Meldungen ans HUD (M3): laufen über eine eigene Schleife, damit `_set` nie wartet.
        # Je Art nur die neueste Meldung (begrenzt, nie gestaut): ein langsamer Core bekommt
        # den aktuellen Zustand, nicht eine Schlange veralteter.
        self._reports: dict[str, dict[str, str | bool | int]] | None = None
        self._report_ready = asyncio.Event()
        self._level_at: dict[str, float] = {}  # je Quelle: Mikrofon und Ausgabe getrennt
        self._reporter: asyncio.Task[None] | None = None
        self.parts.indicator.show(self.state)

    # ------------------------------------------------------------------ Zustand und Anzeige
    def _set(self, state: State) -> None:
        if self.state is State.SLEEP and state is not State.SLEEP and not self._waking:
            return  # aus dem Schlaf kommt nur `run()` nach `wake()` heraus
        if state is not self.state:
            self.state = state
            self.parts.indicator.show(state)
            self._report("state", state=state.value)

    def _report(self, event: str, **fields: str | bool | int) -> None:
        if self._reports is not None:
            self._reports.pop(event, None)  # neu einreihen: Reihenfolge der letzten Änderung
            self._reports[event] = fields
            self._report_ready.set()

    def _report_level(self, pcm: bytes, source: str) -> None:
        """Pegel des Mikrofons fürs HUD: gedrosselt, nur als Stufe 0..7, nur wenn das HUD zuhören
        kann."""
        if self._reports is None:
            return
        now = self.clock()
        if now - self._level_at.get(source, -LEVEL_SECONDS) < LEVEL_SECONDS:
            return
        self._level_at[source] = now
        self._report("level", level=audio_level(pcm), source=source)

    def _speaker_levels(self, pcm: bytes, rate: int) -> asyncio.Task[None]:
        """Pegel der Ausgabe fürs HUD (M3.4) über die ganze Satzdauer: ein Satz ist oft mehrere
        Sekunden Ton in einem Stück. Der erste Wert geht sofort hinaus, der Rest im Takt der
        Wiedergabe; die Task endet mit der Wiedergabe."""
        step = max(2, int(rate * LEVEL_SECONDS) * 2)
        self._report("level", level=audio_level(pcm[:step]), source="speaker")

        async def follow() -> None:
            rest = pcm[step:]
            values = await asyncio.to_thread(
                lambda: [audio_level(rest[i : i + step]) for i in range(0, len(rest), step)]
            )
            for value in values:
                await asyncio.sleep(LEVEL_SECONDS)
                if self.state is not State.SPEAKING:
                    return
                self._report("level", level=value, source="speaker")

        return asyncio.create_task(follow())

    async def _report_loop(self, reports: dict[str, dict[str, str | bool | int]]) -> None:
        """Schickt die neuesten Meldungen; ist länger nichts passiert, ein Lebenszeichen, damit
        das HUD einen abgestürzten Sprachclient erkennt. Jede Meldung hat eine kurze Frist."""
        while True:
            try:
                await asyncio.wait_for(self._report_ready.wait(), HEARTBEAT_SECONDS)
            except TimeoutError:
                reports.setdefault("heartbeat", {})
            self._report_ready.clear()
            while reports:
                event = next(iter(reports))
                fields = reports.pop(event)
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(
                        self.parts.core.status(event, **fields), REPORT_TIMEOUT_SECONDS
                    )

    def _begin_listening(
        self, state: State, timeout: float, seed: list[bytes] | None = None
    ) -> None:
        self._buffer.clear()
        self._heard = False
        self._speech_frames = 0
        self.parts.turn.reset()
        self.parts.vad.reset()
        self._deadline = self.clock() + timeout
        if self._ring is not None:
            self._ring.clear()  # in jedem Fall: was vor dem Zuhören war, bleibt nicht liegen
        if seed:  # Audio vor dem Wake Word geht mit in die Erkennung (M2.10)
            self._buffer += b"".join(seed)
            # Sprache VOR dem Wake-Word-Satz? Dann ist die Äußerung schon da und endet mit der
            # nächsten Pause. Steht nur "Hey Jarvis" im Puffer, kommt die Frage erst danach.
            body = seed[: max(0, len(seed) - WAKE_PHRASE_FRAMES)]
            speech = [f for f in body if self.parts.vad.is_speech(f)]
            if len(speech) >= MIN_CONTEXT_FRAMES:
                self._heard = True
                self.parts.turn.feed(speech[-1], True)  # der Satzende-Erkenner kennt die Sprache
        self._set(state)

    def _to_waiting(self) -> None:
        self.parts.wake.reset()
        self._set(State.WAITING)

    # ------------------------------------------------------------------------ Hauptschleife
    async def run(self) -> None:
        """Liest Frames, bis die Quelle endet. In SCHLAF wird das Mikrofon geschlossen."""
        self._reports = {}
        self._reporter = asyncio.create_task(self._report_loop(self._reports))
        self._report("state", state=self.state.value)
        # "an" schon vor dem Öffnen: das HUD zeigt nie "aus", während das Mikrofon offen ist.
        # Scheitert das Öffnen, meldet `shutdown` "aus".
        self._report("mic", on=True)
        await self.parts.source.open()
        try:
            while True:
                if self.state is State.SLEEP:
                    self.parts.source.close()
                    self._report("mic", on=False)
                    self._wake_event.clear()
                    await self._wake_event.wait()
                    self._report("mic", on=True)
                    await self.parts.source.open()
                    self._waking = True
                    self._to_waiting()
                    self._waking = False
                    self.parts.indicator.cue("wake")
                    if self._listen_after_wake:  # Tastenkürzel "zuhören" im Schlaf
                        self._listen_after_wake = False
                        self._start_listening(self.clock())
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
        reporter, self._reporter, self._reports = self._reporter, None, None
        if reporter is not None:  # das HUD soll das geschlossene Mikrofon sofort sehen
            reporter.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await reporter
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self.parts.core.status("mic", on=False), 2.0)
        if self._ring is not None:
            self._ring.clear()
        if self._dictating:  # auch bei Strg+C: das Ende gehört ins Audit
            self._dictating = False
            with contextlib.suppress(Exception):
                await self.parts.core.voice_event("dictation_end")
        for task in list(self._tasks):
            task.cancel()
        for task in list(self._tasks):
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task

    # ---------------------------------------------------------------------------- Frames
    async def on_frame(self, frame: bytes) -> None:
        now = self.clock()
        if self.state is State.WAITING:
            if self._ring is not None:
                self._ring.append(frame)
            clap = self.parts.clap
            if self.parts.wake.process(frame) or (clap is not None and clap.process(frame, now)):
                self._start_listening(now, with_context=True)
        elif self.state in (State.LISTENING, State.CONFIRM, State.DICTATION):
            self._report_level(frame, "mic")
            self._listen(frame, now)
        elif self.state is State.SPEAKING:
            self._maybe_barge_in(frame, now)

    def _start_listening(self, now: float, with_context: bool = False) -> None:
        """Wake Word, Doppelklatschen oder Tastenkürzel: ab jetzt zuhören."""
        self._turn = Turn(wake_at=now)
        self.parts.indicator.cue("wake")
        seed: list[bytes] | None = None
        if with_context and self._ring is not None:
            seed = list(self._ring)
        if self._ring is not None:
            self._ring.clear()  # einmal verwendet, nie aufgehoben
        self._begin_listening(State.LISTENING, self.config.listen_timeout_seconds, seed)

    def _listen(self, frame: bytes, now: float) -> None:
        speech = self.parts.vad.is_speech(frame)
        self._buffer += frame
        if speech:
            self._heard = True
            if self._dictating:  # wer spricht, diktiert noch: die Ruhe-Frist beginnt neu
                self._deadline = now + self.config.dictation_idle_seconds
        ended = self._heard and self.parts.turn.feed(frame, speech)
        too_long = len(self._buffer) / 2 / SAMPLE_RATE >= self.config.max_utterance_seconds
        if ended or too_long:
            self._finish_utterance(now)
        elif not self._heard and now > self._deadline:
            self._timeout()

    def _timeout(self) -> None:
        if self._dictating:  # lange Ruhe: Diktat beenden, nicht ewig mitschreiben
            self._end_dictation()
            return
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
        if not self._dictating:  # beim Diktat bleibt die Anzeige "DIKTAT" und das Zuhören an
            self._set(State.THINKING)  # auch bei CONFIRM: nach einer Äußerung nicht weiter zuhören
        self._spawn(self._process(pcm, dictation=self._dictating))

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

    async def _process(self, pcm: bytes, dictation: bool = False) -> None:
        try:
            await self._recognize_and_answer(pcm, dictation)
        except asyncio.CancelledError:
            raise
        except (
            Exception
        ):  # Erkennung oder Ausgabe gescheitert: nicht in DENKT/SPRICHT hängen bleiben
            if self._answer is not None and not self._answer.done():
                self._answer.set_result(None)
            self._to_waiting()

    async def _recognize_and_answer(self, pcm: bytes, dictation: bool = False) -> None:
        transcript = await self.parts.stt.transcribe(pcm)
        if self._turn is not None:
            self._turn.transcribed = self.clock()
        text = transcript.text.strip()
        if self.config.language != "auto":
            self._language = self.config.language
        elif transcript.language in ("de", "en"):
            self._language = transcript.language or "de"
        # Mit Sprecherprüfung zählt nur Masters Stimme (M2.9); sonst jede.
        speaker = self.parts.speaker
        # Das Urteil rechnet das Modell: nicht in der Event-Loop, sonst steht alles still.
        if text and speaker is not None and not await asyncio.to_thread(speaker.verify, pcm):
            text = ""
        if self._answer is not None and not self._answer.done():
            self._resolve_answer(text)
            return
        if dictation:
            # Eine Äußerung, die beim Diktat aufgenommen wurde, geht NIE zum Core (ADR-023): ist
            # das Diktat inzwischen beendet, wird sie verworfen.
            if self._dictating:
                await self._dictate(text)
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
            case Command.DICTATION_ON:
                await self.start_dictation()
            case Command.DICTATION_OFF:  # ohne laufendes Diktat nichts zu beenden
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
            levels = self._speaker_levels(pcm, rate) if self._reports is not None else None
            try:
                await self.parts.sink.play(pcm, rate)
            finally:
                if levels is not None:
                    levels.cancel()

    def _after_reply(self) -> None:
        """Nach der Antwort: Folgefrage ohne Wake Word, sonst zurück zum Warten."""
        if self.state is State.SLEEP:
            return
        if self.config.follow_up_seconds > 0:
            self.parts.indicator.cue("listen")
            self._begin_listening(State.LISTENING, self.config.follow_up_seconds)
        else:
            self._to_waiting()

    # ---------------------------------------------------------------------- Diktat (M2.12)
    async def start_dictation(self) -> None:
        """Diktiermodus: das Gesprochene wird als Text in das aktive Fenster getippt (nur
        Zeichen, nie Enter/Tab/Steuerzeichen, keine Zwischenablage). Endet mit "Diktat Ende",
        dem Tastenkürzel, bei Fensterwechsel, bei langer Ruhe, beim Schlafmodus."""
        if self.parts.typer is None:
            await self._say(MESSAGES[self._language]["no_dictation"])
            self._after_reply()
            return
        self._dictating = True
        window = self.parts.window
        self._dictation_window = window.current() if window is not None else None
        self.parts.indicator.cue("wake")
        self._begin_listening(State.DICTATION, self.config.dictation_idle_seconds)
        await self.parts.core.voice_event("dictation_start")

    def _end_dictation(self) -> None:
        """Beendet das Diktat sofort; der Audit-Eintrag (nur Ereignis, nie Text) folgt."""
        if not self._dictating:
            return
        self._dictating = False
        self._dictation_window = None
        self.parts.indicator.cue("listen_done")
        self._to_waiting()
        self._spawn(self.parts.core.voice_event("dictation_end"))

    def toggle_dictation(self) -> None:
        """Tastenkürzel: Diktat an oder aus."""
        if self._dictating:
            self._end_dictation()
        elif self.state in (State.WAITING, State.LISTENING):
            self._spawn(self.start_dictation())

    async def _dictate(self, text: str) -> None:
        command = parse_command(text) if text else None
        if command is Command.SLEEP:
            self._end_dictation()
            self.sleep()
            return
        if command in (Command.DICTATION_OFF, Command.STOP, Command.EMERGENCY):
            self._end_dictation()
            return
        window = self.parts.window
        if window is not None and window.current() != self._dictation_window:
            self._end_dictation()  # anderes Fenster: nichts tippen, das Diktat endet
            return
        clean = "".join(ch for ch in text if ch.isprintable())[:MAX_TEXT_CHARS].strip()
        if clean and self.parts.typer is not None:
            self.parts.typer.type(clean + " ")
        self._deadline = self.clock() + self.config.dictation_idle_seconds

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
        self._end_dictation()
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
        current = asyncio.current_task()
        for task in list(self._tasks):  # laufende Antwort und Erkennung enden mit dem Mikrofon
            if task is not current:
                task.cancel()
        self._end_dictation()  # erst danach: sein Audit-Ereignis darf nicht mit abgebrochen werden
        if self._ring is not None:
            self._ring.clear()  # nichts vom Mitgehörten bleibt im Schlaf zurück
        if self.parts.clap is not None:
            self.parts.clap.reset()
        self._set(State.SLEEP)

    def wake(self) -> None:
        """Tastenkürzel: aus dem Schlaf aufwecken (Mikrofon wird wieder geöffnet)."""
        self._wake_event.set()
        if self.state is not State.SLEEP:
            self._to_waiting()

    # ------------------------------------------------------------- Tastenkürzel (M2.13)
    def trigger_listen(self) -> None:
        """Tastenkürzel "zuhören": wie das Wake Word, weckt auch aus dem Schlaf und beendet
        eine laufende Antwort."""
        if self.state is State.SLEEP:
            self._listen_after_wake = True
            self.wake()
        elif self.state is State.WAITING:
            self._start_listening(self.clock())
        elif self.state in (State.THINKING, State.SPEAKING):
            self._spawn(self.interrupt(listen_again=True))

    def toggle_mute(self) -> None:
        """Tastenkürzel "stumm": Mikrofon ganz schließen oder wieder öffnen."""
        if self.state is State.SLEEP:
            self.wake()
        else:
            self.sleep()

    def request_interrupt(self) -> None:
        """Tastenkürzel "unterbrechen" (aus dem Thread-sicheren Aufruf der Event-Loop)."""
        self._spawn(self.interrupt())

    def latency(self) -> dict[str, float]:
        """Mittelwerte der gemessenen Stufen über alle Runden (nur Zeiten)."""
        sums: dict[str, list[float]] = {}
        for turn in self.turns:
            for name, value in turn.stages().items():
                sums.setdefault(name, []).append(value)
        return {name: round(sum(v) / len(v), 3) for name, v in sums.items()}
