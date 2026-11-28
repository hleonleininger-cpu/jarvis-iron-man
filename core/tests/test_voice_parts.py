"""Kleine Bausteine des Sprachclients: Sätze, Befehle, Echo-Schutz, Satzende, Core-Strom."""

import pytest
from pydantic import ValidationError

from jarvis_core.config import Settings
from jarvis_core.config.settings import VoiceConfig
from jarvis_voice.audio import rms, silence, tone
from jarvis_voice.commands import Command, normalize, parse_command
from jarvis_voice.core_client import parse_sse, to_event
from jarvis_voice.echo import EchoGuard
from jarvis_voice.protocols import (
    FRAME_SECONDS,
    CoreConfirm,
    CoreDelta,
    CoreDone,
    CoreError,
    CoreNotice,
)
from jarvis_voice.sentences import SentenceChunker, speakable
from jarvis_voice.turn import EnergyVad, SilenceTurnDetector

LOUD = tone(440, FRAME_SECONDS, level=0.5)
QUIET = silence(FRAME_SECONDS)


def test_audio_helpers() -> None:
    assert len(LOUD) == 1024 and rms(QUIET) == 0.0
    assert 0.3 < rms(LOUD) < 0.4  # Sinus mit Spitze 0,5: Effektivwert ca. 0,35
    assert rms(b"") == 0.0


# --------------------------------------------------------------------------- Sätze
def test_chunker_emits_sentences_as_they_complete() -> None:
    chunker = SentenceChunker()
    assert chunker.feed("Heute wird es sonnig. Es hat ") == ["Heute wird es sonnig."]
    assert chunker.feed("zwanzig Grad! Und morgen") == ["Es hat zwanzig Grad!"]
    assert chunker.flush() == ["Und morgen"]


def test_chunker_keeps_decimals_and_abbreviations() -> None:
    chunker = SentenceChunker()
    out = chunker.feed("Es hat 3.5 Grad, z. B. in Dortmund. Danach wird es wärmer.")
    out += chunker.flush()
    assert out == ["Es hat 3.5 Grad, z. B. in Dortmund.", "Danach wird es wärmer."]


def test_chunker_merges_very_short_pieces() -> None:
    chunker = SentenceChunker()
    assert chunker.feed("Ja. Das stimmt natürlich genau so.") == []
    assert chunker.flush() == ["Ja. Das stimmt natürlich genau so."]


def test_speakable_drops_markdown_and_links() -> None:
    assert speakable("**Wichtig:** siehe https://example.com `code`") == "Wichtig: siehe code"


# ------------------------------------------------------------------------- Befehle
@pytest.mark.parametrize(
    ("text", "command"),
    [
        ("Stopp!", Command.STOP),
        ("Jarvis, stopp.", Command.STOP),
        ("Not-Aus", Command.EMERGENCY),
        ("Gute Nacht, Jarvis", Command.SLEEP),
        ("Flüstere", Command.WHISPER_ON),
        ("Normal sprechen", Command.WHISPER_OFF),
        ("Diktiermodus an", Command.DICTATION_ON),
        ("Diktat Ende", Command.DICTATION_OFF),
        ("Ja, bestätigt.", Command.YES),
        ("Nein", Command.NO),
    ],
)
def test_commands_match_whole_phrases(text: str, command: Command) -> None:
    assert parse_command(text) is command


@pytest.mark.parametrize(
    "text",
    [
        "Bitte stopp die Musik",
        "Ja",
        "Ja klar",
        "Ich sage gute Nacht zu meiner Tochter",
        "",
        "Wetter?",
    ],
)
def test_commands_do_not_fire_inside_sentences(text: str) -> None:
    assert parse_command(text) is None


def test_normalize() -> None:
    assert normalize("  Hey Jarvis, Stopp!!  ") == "stopp"


# ----------------------------------------------------------------------- Echo-Schutz
def test_echo_guard_needs_louder_speech_while_playing() -> None:
    guard = EchoGuard()
    guard.played(tone(300, 0.5, level=0.5), now=0.0, duration=0.5)
    echo = tone(300, FRAME_SECONDS, level=0.2)  # leiser als die Wiedergabe: Echo
    master = tone(200, FRAME_SECONDS, level=0.9)  # deutlich lauter: Master
    assert not guard.is_user_speech(echo, now=0.2)
    assert guard.is_user_speech(master, now=0.2)


def test_echo_guard_accepts_normal_speech_when_nothing_plays() -> None:
    guard = EchoGuard()
    guard.played(tone(300, 0.2, level=0.5), now=0.0, duration=0.2)
    assert guard.is_user_speech(tone(200, FRAME_SECONDS, level=0.2), now=5.0)
    assert not guard.is_user_speech(silence(FRAME_SECONDS), now=5.0)


# ------------------------------------------------------------------------ Satzende
def test_silence_turn_detector_ends_after_speech_and_silence() -> None:
    vad = EnergyVad()
    turn = SilenceTurnDetector(end_silence=0.2)  # ca. 6 Frames
    assert vad.is_speech(LOUD) and not vad.is_speech(QUIET)
    results = [turn.feed(QUIET, False) for _ in range(10)]  # Stille vorab beendet nichts
    assert not any(results)
    assert not turn.feed(LOUD, True)
    ended = [turn.feed(QUIET, False) for _ in range(10)]
    assert ended.index(True) == 5  # 6. Stille-Frame
    turn.reset()
    assert not turn.feed(QUIET, False)


# ------------------------------------------------------------------------ Core-Strom
def test_parse_sse_and_events() -> None:
    assert parse_sse([": keepalive"]) is None
    assert parse_sse(["event: delta", 'data: {"text": "Hallo"}']) == ("delta", {"text": "Hallo"})
    assert parse_sse(["event: delta", "data: kaputt"]) is None
    assert to_event("delta", {"text": "Hi"}) == CoreDelta("Hi")
    assert to_event("notice", {"message": "x"}) == CoreNotice("x")
    assert to_event("done", {"session_id": "s1"}) == CoreDone("s1")
    assert to_event("error", {"message": "m"}) == CoreError("m")
    assert to_event("tool", {"name": "t"}) is None  # Werkzeuge werden nicht vorgelesen
    confirm = to_event(
        "confirm", {"id": "1", "tool": "t", "preview": "p", "question": "q", "risk": "READ"}
    )
    assert confirm == CoreConfirm("1", "t", "p", "q", "READ")


# ---------------------------------------------------------------------- Konfiguration
def test_voice_is_off_by_default_and_core_must_be_local() -> None:
    assert Settings().voice.enabled is False
    with pytest.raises(ValidationError):
        VoiceConfig(core_url="https://example.com")
    with pytest.raises(ValidationError):
        VoiceConfig(core_url="ftp://localhost")
    assert VoiceConfig(core_url="http://100.64.1.2:8765/").core_url == "http://100.64.1.2:8765"
    assert VoiceConfig(confirm_by_voice=False).barge_in == "key"


def test_console_indicator_survives_a_broken_console(monkeypatch: pytest.MonkeyPatch) -> None:
    """Windows error 6 (ungültiges Konsolenhandle) darf den Sprachclient nicht beenden."""
    import typer

    from jarvis_voice.cli_parts import ConsoleIndicator
    from jarvis_voice.protocols import State

    def broken_echo(*_args: object, **_kwargs: object) -> None:
        raise OSError(6, "Windows error 6")

    monkeypatch.setattr(typer, "echo", broken_echo)
    ConsoleIndicator().show(State.LISTENING)  # darf nicht werfen
