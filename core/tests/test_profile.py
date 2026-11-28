import pytest

from jarvis_core.hardware import HardwareInfo, Profile, parse_nvidia_smi, recommend


@pytest.mark.parametrize(
    ("info", "profile", "model"),
    [
        (HardwareInfo(ram_gb=32.0, gpu=None, vram_gb=None), Profile.S, "qwen3.5:4b"),
        (HardwareInfo(gpu="RTX 3070", vram_gb=8.0), Profile.M, "qwen3.5:9b"),
        (HardwareInfo(gpu="RTX 3070", vram_gb=7.96), Profile.M, "qwen3.5:9b"),
        (HardwareInfo(gpu="RTX 3060", vram_gb=6.0), Profile.S, "qwen3.5:4b"),
        (HardwareInfo(gpu="RTX 3060", vram_gb=12.0), Profile.M, "qwen3.5:9b"),
        (HardwareInfo(gpu="RTX 4090", vram_gb=24.0), Profile.L, "qwen3.6:27b"),
        (HardwareInfo(is_mac=True, ram_gb=8.0), Profile.S, "qwen3.5:4b"),
        (HardwareInfo(is_mac=True, ram_gb=16.0), Profile.M, "qwen3.5:9b"),
        (HardwareInfo(is_mac=True, ram_gb=32.0), Profile.L, "qwen3.6:27b"),
        (HardwareInfo(), Profile.S, "qwen3.5:4b"),
        (HardwareInfo(is_mac=True), Profile.S, "qwen3.5:4b"),
        (HardwareInfo(gpu="unbekannte Karte"), Profile.S, "qwen3.5:4b"),
    ],
)
def test_profile_selection(info: HardwareInfo, profile: Profile, model: str) -> None:
    rec = recommend(info)
    assert rec.profile is profile
    assert rec.model == model
    assert rec.pull_command == f"ollama pull {model}"


def test_parse_nvidia_smi() -> None:
    assert parse_nvidia_smi("NVIDIA GeForce RTX 3080, 10240") == ("NVIDIA GeForce RTX 3080", 10.0)
    multi = "GPU A, 4096\nGPU B, 24576"
    assert parse_nvidia_smi(multi) == ("GPU B", 24.0)


@pytest.mark.parametrize("raw", [None, "", "kaputt", "name, [N/A]"])
def test_parse_nvidia_smi_unreadable(raw: str | None) -> None:
    assert parse_nvidia_smi(raw) is None


def test_hardware_commands_run_without_a_console_window(monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess

    from jarvis_core.hardware import detect

    seen: dict[str, object] = {}

    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        seen.update(kwargs)
        return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert detect._run(["egal"]) == "ok"  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
    assert seen["creationflags"] == getattr(subprocess, "CREATE_NO_WINDOW", 0)
