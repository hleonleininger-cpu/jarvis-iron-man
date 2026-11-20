"""Klatsch-Erkennung und Tastenkürzel (M2.13) als eigene Bausteine."""

import asyncio
import sys
import types
from typing import Any

import pytest

from jarvis_voice import engines, hotkeys
from jarvis_voice.audio import silence, tone
from jarvis_voice.clap import ClapDetector
from jarvis_voice.protocols import FRAME_SECONDS

BANG = tone(1000, FRAME_SECONDS, level=0.9)
TAIL = tone(1000, FRAME_SECONDS, level=0.1)  # Ausklingen
QUIET = silence(FRAME_SECONDS)


def feed(detector: ClapDetector, frames: list[bytes]) -> list[bool]:
    return [detector.process(f, i * FRAME_SECONDS) for i, f in enumerate(frames)]


def test_two_short_bangs_in_time_are_a_double_clap() -> None:
    # Abstand der Einsätze: 6 Frames = 0,19 s
    frames = [BANG, QUIET, QUIET, QUIET, QUIET, QUIET, BANG, QUIET, QUIET]
    assert feed(ClapDetector(), frames).count(True) == 1


def test_single_clap_or_too_slow_second_clap_does_not_count() -> None:
    assert not any(feed(ClapDetector(), [BANG] + [QUIET] * 40))
    slow = [BANG] + [QUIET] * 30 + [BANG] + [QUIET] * 3  # fast 1 s Abstand
    assert not any(feed(ClapDetector(), slow))


def test_echo_of_the_same_clap_is_not_a_second_clap() -> None:
    frames = [BANG, QUIET, BANG, QUIET, QUIET, QUIET]  # 64 ms: Nachhall
    assert not any(feed(ClapDetector(), frames))


def test_sustained_loud_sound_is_speech_not_a_clap() -> None:
    frames = [BANG] * 20 + [QUIET] * 6 + [BANG] * 20 + [QUIET] * 6
    assert not any(feed(ClapDetector(), frames))


def test_quiet_noise_below_the_threshold_is_ignored() -> None:
    soft = tone(1000, FRAME_SECONDS, level=0.1)
    frames = [soft, QUIET, QUIET, QUIET, QUIET, QUIET, soft, QUIET]
    assert not any(feed(ClapDetector(0.3), frames))


def test_refractory_period_after_a_hit() -> None:
    detector = ClapDetector()
    frames = [BANG, QUIET, QUIET, QUIET, QUIET, QUIET, BANG, QUIET, QUIET]
    frames += [QUIET] * 4 + [BANG, QUIET, QUIET, QUIET, QUIET, QUIET, BANG, QUIET, QUIET]
    assert feed(detector, frames).count(True) == 1  # das zweite Paar kommt zu früh danach


# ------------------------------------------------------------------------ Tastenkürzel
@pytest.mark.parametrize(
    ("combo", "ok"),
    [
        ("<ctrl>+<alt>+j", True),
        ("<ctrl>+<shift>+<f9>", True),
        ("<ctrl>+j", True),
        ("j", False),
        ("ctrl+j", False),
        ("<ctrl>+<alt>", False),
        ("<ctrl>+<alt>+ja", False),
        ("", False),
    ],
)
def test_hotkey_format(combo: str, ok: bool) -> None:
    assert hotkeys.valid_hotkey(combo) is ok


@pytest.mark.anyio
async def test_hotkey_listener_forwards_actions_into_the_event_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Any] = {}

    class FakeGlobalHotKeys:
        def __init__(self, mapping: dict[str, Any]) -> None:
            seen["mapping"] = mapping
            self.started = False

        def start(self) -> None:
            seen["started"] = True

        def stop(self) -> None:
            seen["stopped"] = True

    keyboard = types.SimpleNamespace(GlobalHotKeys=FakeGlobalHotKeys)
    real_need = engines.need

    def need(name: str, why: str) -> Any:
        return keyboard if name == "pynput.keyboard" else real_need(name, why)

    monkeypatch.setattr(hotkeys, "need", need)
    done = asyncio.Event()
    listener = hotkeys.HotkeyListener({"<ctrl>+<alt>+j": done.set}, asyncio.get_running_loop())
    listener.start()
    assert seen["started"] and list(seen["mapping"]) == ["<ctrl>+<alt>+j"]
    # aus einem anderen Thread aufgerufen: die Aktion läuft trotzdem in der Event-Loop
    await asyncio.to_thread(seen["mapping"]["<ctrl>+<alt>+j"])
    await asyncio.wait_for(done.wait(), 2)
    listener.stop()
    assert seen["stopped"]


def test_hotkey_listener_rejects_invalid_combinations() -> None:
    with pytest.raises(ValueError, match="Tastenkürzel"):
        hotkeys.HotkeyListener({"strg+j": lambda: None}, asyncio.new_event_loop())


def test_config_validates_hotkeys_and_allows_switching_off() -> None:
    from pydantic import ValidationError

    from jarvis_core.config.settings import VoiceConfig

    assert VoiceConfig(hotkey_listen="").hotkey_listen == ""
    with pytest.raises(ValidationError):
        VoiceConfig(hotkey_mute="ctrl m")
    assert sys.platform  # (Modul genutzt)
