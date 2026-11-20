# pyright: basic, reportMissingImports=false, reportMissingModuleSource=false
"""Sprecherprofil und Sprecherprüfung (M2.9), Diktat-Bausteine (M2.12), Tastenkürzel im Betrieb."""

import json
import os
import sys
import types
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from jarvis_core.cli import main as cli_main
from jarvis_core.config import Settings
from jarvis_core.config.settings import VoiceConfig
from jarvis_voice import engines, hotkeys, run, speaker
from jarvis_voice.models import ModelError, load_registry
from jarvis_voice.speaker import (
    ProfileError,
    average,
    cosine,
    delete_profile,
    load_profile,
    save_profile,
)

pytestmark = pytest.mark.anyio


# -------------------------------------------------------------------------------- Profil
def test_average_and_cosine() -> None:
    assert cosine([1, 0], [1, 0]) == pytest.approx(1.0)
    assert cosine([1, 0], [0, 1]) == pytest.approx(0.0)
    mean = average([[1, 0, 0], [1, 0.1, 0], [0.9, 0, 0.1]])
    assert sum(x * x for x in mean) == pytest.approx(1.0)
    with pytest.raises(ProfileError, match="mindestens"):
        average([[1, 0], [1, 0]])
    with pytest.raises(ProfileError, match="Längen"):
        average([[1, 0], [1, 0], [1, 0, 0]])
    with pytest.raises(ProfileError):
        speaker.normalize([0, 0])


def test_profile_roundtrip_holds_only_a_vector_and_can_be_deleted(tmp_path: Path) -> None:
    path = speaker.profile_path(tmp_path)
    assert path == tmp_path / "voice" / "sprecher.json"
    save_profile(path, [3.0, 4.0], "titanet")
    data = json.loads(path.read_text(encoding="utf-8"))
    assert set(data) == {"version", "model", "embedding"}  # kein Audio, keine Namen, keine Texte
    assert data["embedding"] == pytest.approx([0.6, 0.8])
    vector, model = load_profile(path)
    assert vector == pytest.approx([0.6, 0.8]) and model == "titanet"
    if sys.platform != "win32":
        assert path.stat().st_mode & 0o077 == 0  # nur für den Benutzer
    assert delete_profile(path) is True and not path.exists()
    assert delete_profile(path) is False


def test_missing_or_damaged_profile_gives_a_german_message(tmp_path: Path) -> None:
    path = speaker.profile_path(tmp_path)
    with pytest.raises(ProfileError, match="anlernen"):
        load_profile(path)
    path.parent.mkdir(parents=True)
    path.write_text("kein json", encoding="utf-8")
    with pytest.raises(ProfileError, match="unbrauchbar"):
        load_profile(path)
    path.write_text(json.dumps({"version": 99, "model": "x", "embedding": [1]}), encoding="utf-8")
    with pytest.raises(ProfileError):
        load_profile(path)


# ------------------------------------------------------------------------------- Prüfung
def test_gate_accepts_close_voices_rejects_far_and_short_ones() -> None:
    pytest.importorskip("numpy")
    from jarvis_voice.engines.speaker_sherpa import MIN_SECONDS, SherpaSpeakerGate

    class Embedder:
        model_name = "fake"

        def embed(self, pcm: bytes) -> list[float]:
            return [1.0, 0.0] if pcm.startswith(b"M") else [0.0, 1.0]

    gate = SherpaSpeakerGate(Embedder(), [1.0, 0.0], threshold=0.5)  # type: ignore[arg-type]
    long = int(MIN_SECONDS * 16_000 * 2) + 100
    assert gate.verify(b"M" * long) and gate.last_score == pytest.approx(1.0)
    assert not gate.verify(b"X" * long)
    assert not gate.verify(b"M" * 100)  # zu kurz: im Zweifel nicht Master


MODELS = os.environ.get("JARVIS_VOICE_MODELS")


@pytest.mark.skipif(not MODELS, reason="JARVIS_VOICE_MODELS nicht gesetzt")
def test_real_model_separates_two_voices(tmp_path: Path) -> None:
    pytest.importorskip("sherpa_onnx")
    pytest.importorskip("piper")
    import numpy as np
    from piper import PiperVoice

    from jarvis_voice.engines.speaker_sherpa import SherpaEmbedder, SherpaSpeakerGate

    def clip(voice: str, text: str) -> bytes:
        path = str(Path(MODELS or ".") / voice)
        model = PiperVoice.load(path, config_path=path + ".json")
        pcm = b"".join(c.audio_int16_bytes for c in model.synthesize(text))
        audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
        count = int(len(audio) * 16_000 / model.config.sample_rate)
        out = np.interp(np.linspace(0, len(audio) - 1, count), np.arange(len(audio)), audio)
        return out.astype(np.int16).tobytes()

    german = "de_DE-thorsten-medium.onnx"
    english = "en_GB-northern_english_male-medium.onnx"
    embedder = SherpaEmbedder(Path(MODELS or ".") / "nemo_en_titanet_small.onnx")
    texts = [
        "Heute ist ein guter Tag, um etwas Neues zu lernen.",
        "Bitte lies mir die Nachrichten von heute Morgen vor.",
        "Der schnelle braune Fuchs springt über den faulen Hund.",
    ]
    profile = average([embedder.embed(clip(german, t)) for t in texts])
    gate = SherpaSpeakerGate(embedder, profile, threshold=0.5)
    assert gate.verify(clip(german, "Wie wird das Wetter morgen in Dortmund und am Wochenende?"))
    assert not gate.verify(clip(english, "What is the weather like tomorrow in London today?"))


def test_run_needs_model_and_profile_when_the_speaker_check_is_on(tmp_path: Path) -> None:
    settings = Settings(
        data_dir=tmp_path / "data", voice=VoiceConfig(models_dir=tmp_path / "m", speaker_check=True)
    )
    with pytest.raises(ModelError, match="jarvis voice setup"):
        run.build_speaker(settings, tmp_path / "m", load_registry())


def test_cli_forget_and_status(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = tmp_path / "jarvis.toml"
    config.write_text(f'data_dir = "{(tmp_path / "data").as_posix()}"\n', encoding="utf-8")
    monkeypatch.setenv("JARVIS_CONFIG", str(config))
    save_profile(speaker.profile_path(tmp_path / "data"), [1.0, 0.0], "m")
    status = CliRunner().invoke(cli_main.app, ["voice", "status"])
    assert "Profil vorhanden" in status.output
    assert "gelöscht" in CliRunner().invoke(cli_main.app, ["voice", "vergessen"]).output
    assert "nicht angelernt" in CliRunner().invoke(cli_main.app, ["voice", "status"]).output
    assert "kein Sprecherprofil" in CliRunner().invoke(cli_main.app, ["voice", "vergessen"]).output


def test_cli_enroll_explains_the_missing_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("numpy")
    config = tmp_path / "jarvis.toml"
    config.write_text(
        f'data_dir = "{(tmp_path / "data").as_posix()}"\n[voice]\nmodels_dir = "{(tmp_path / "m").as_posix()}"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("JARVIS_CONFIG", str(config))
    result = CliRunner().invoke(cli_main.app, ["voice", "anlernen"])
    assert result.exit_code == 1 and "setup --nur speaker_titanet" in result.output


def test_speaker_model_is_optional_for_doctor(tmp_path: Path) -> None:
    from jarvis_voice.doctor import model_checks

    check = next(c for c in model_checks(tmp_path) if c.name == "Modell speaker_titanet")
    assert check.ok and "optional" in check.detail


# ---------------------------------------------------------------------- Diktat-Bausteine
def test_typer_types_only_printable_characters(monkeypatch: pytest.MonkeyPatch) -> None:
    from jarvis_voice.engines import typer_pynput

    typed: list[str] = []

    class Controller:
        def type(self, text: str) -> None:
            typed.append(text)

    keyboard = types.SimpleNamespace(Controller=Controller)
    real_need = engines.need

    def need(name: str, why: str) -> Any:
        return keyboard if name == "pynput.keyboard" else real_need(name, why)

    monkeypatch.setattr(typer_pynput, "need", need)
    pynput_typer = typer_pynput.PynputTyper()
    pynput_typer.type("Hallo\nWelt\t\x1b[A\r!")
    pynput_typer.type("\n\t\r")  # nur Steuerzeichen: nichts wird getippt
    assert typed == ["HalloWelt[A!"]


def test_window_watcher_is_neutral_outside_windows() -> None:
    from jarvis_voice.engines.window_win import ForegroundWatcher

    watcher = ForegroundWatcher()
    if sys.platform != "win32":
        assert watcher.current() is None
    else:
        assert isinstance(watcher.current(), int)


# ------------------------------------------------------------------ Tastenkürzel im Betrieb
class FakeSession:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def trigger_listen(self) -> None:
        self.calls.append("listen")

    def toggle_mute(self) -> None:
        self.calls.append("mute")

    def request_interrupt(self) -> None:
        self.calls.append("interrupt")

    def toggle_dictation(self) -> None:
        self.calls.append("dictation")


async def test_start_hotkeys_binds_all_four_and_skips_empty_ones(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    class FakeGlobal:
        def __init__(self, mapping: dict[str, Any]) -> None:
            captured.update(mapping)

        def start(self) -> None:
            captured["started"] = True

        def stop(self) -> None:
            pass

    real_need = engines.need

    def need(name: str, why: str) -> Any:
        return (
            types.SimpleNamespace(GlobalHotKeys=FakeGlobal)
            if name == "pynput.keyboard"
            else real_need(name, why)
        )

    monkeypatch.setattr(hotkeys, "need", need)
    session: Any = FakeSession()
    settings = Settings(voice=VoiceConfig(hotkey_dictation=""))
    listener = run.start_hotkeys(settings, session)
    assert listener is not None and captured["started"]
    assert set(k for k in captured if k != "started") == {
        "<ctrl>+<alt>+j",
        "<ctrl>+<alt>+m",
        "<ctrl>+<alt>+x",
    }
    assert (
        run.start_hotkeys(
            Settings(
                voice=VoiceConfig(
                    hotkey_listen="", hotkey_mute="", hotkey_interrupt="", hotkey_dictation=""
                )
            ),
            session,
        )
        is None
    )


async def test_start_hotkeys_survives_a_missing_package(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def need(name: str, why: str) -> Any:
        raise engines.MissingPackage(f"Das Paket '{name}' fehlt ({why}).")

    monkeypatch.setattr(hotkeys, "need", need)
    assert run.start_hotkeys(Settings(), FakeSession()) is None  # type: ignore[arg-type]
    assert "Tastenkürzel sind aus" in capsys.readouterr().err
