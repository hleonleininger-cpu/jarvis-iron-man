"""`jarvis voice doctor|bench|run` ohne Modelle: verständliche Meldungen statt Absturz."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from jarvis_core.cli import main as cli_main
from jarvis_core.config import Settings
from jarvis_core.config.settings import VoiceConfig
from jarvis_voice import doctor
from jarvis_voice.doctor import estimate_first_sound, model_checks, package_checks, run_doctor


def _settings(tmp_path: Path) -> Settings:
    return Settings(data_dir=tmp_path / "data", voice=VoiceConfig(models_dir=tmp_path / "leer"))


def test_estimate_adds_the_stages() -> None:
    stages = {
        "satzende_pruefung": 0.06,
        "stt_je_aufnahme": 1.0,
        "core_erster_text": 3.0,
        "tts_erstes_stueck": 0.1,
    }
    assert estimate_first_sound(stages) == pytest.approx(0.25 + 0.06 + 1.0 + 3.0 + 0.1)
    assert estimate_first_sound({}) == 0.25


def test_resample_halves_the_length_for_32khz_and_keeps_16khz() -> None:
    pytest.importorskip("numpy")
    pcm = bytes(2 * 3200)
    assert doctor.resample_16k(pcm, 16_000) is pcm
    assert len(doctor.resample_16k(pcm, 32_000)) == 2 * 1600


def test_missing_models_are_reported_per_group(tmp_path: Path) -> None:
    checks = model_checks(tmp_path)
    assert len(checks) == 6 and not any(c.ok for c in checks)
    assert all("fehlt" in c.detail for c in checks)


def test_package_checks_name_every_package() -> None:
    names = {c.name for c in package_checks()}
    assert {"Paket numpy", "Paket faster_whisper", "Paket piper", "Paket sounddevice"} <= names


def test_doctor_skips_the_function_probe_without_models(tmp_path: Path) -> None:
    checks = run_doctor(_settings(tmp_path))
    probe = next(c for c in checks if c.name == "Funktionsprobe")
    assert not probe.ok and "jarvis voice setup" in probe.detail
    assert checks[0].name == "Hardware" and checks[0].ok


def test_cli_doctor_exits_nonzero_and_names_the_problem(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "jarvis.toml"
    config.write_text(
        f'data_dir = "{(tmp_path / "data").as_posix()}"\n[voice]\nmodels_dir = "{(tmp_path / "x").as_posix()}"\n'
    )
    monkeypatch.setenv("JARVIS_CONFIG", str(config))
    result = CliRunner().invoke(cli_main.app, ["voice", "doctor"])
    assert result.exit_code == 1
    assert "FEHLT" in result.output and "Modell silero_vad" in result.output


def test_cli_run_and_bench_explain_missing_models(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "jarvis.toml"
    config.write_text(
        f'data_dir = "{(tmp_path / "data").as_posix()}"\n[voice]\nenabled = true\n'
        f'models_dir = "{(tmp_path / "x").as_posix()}"\n'
    )
    monkeypatch.setenv("JARVIS_CONFIG", str(config))
    for command in ("run", "bench"):
        result = CliRunner().invoke(cli_main.app, ["voice", command])
        assert result.exit_code == 1, command
        assert "jarvis voice setup" in result.output, command


def test_run_refuses_when_voice_is_switched_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "jarvis.toml"
    config.write_text(f'data_dir = "{(tmp_path / "data").as_posix()}"\n')
    monkeypatch.setenv("JARVIS_CONFIG", str(config))
    result = CliRunner().invoke(cli_main.app, ["voice", "run"])
    assert result.exit_code == 1 and "enabled = true" in result.output
