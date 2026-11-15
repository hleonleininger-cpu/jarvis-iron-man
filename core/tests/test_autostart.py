"""Autostart über eine Startdatei; geschrieben wird nur in tmp_path, nie in den echten Ordner."""

import subprocess
import sys
import time
from pathlib import Path

import pytest

from jarvis_core.cli.autostart import (
    FILE_NAME,
    Result,
    disable,
    enable,
    start_script,
    startup_dir,
    status,
    supported,
)

PROJECT = Path("C:\\JARVIS\\core")
NOT_SUPPORTED = Result(ok=False, enabled=None, message="Autostart gibt es nur unter Windows.")


def test_supported_only_on_windows() -> None:
    assert supported("win32")
    assert not supported("linux")
    assert not supported("darwin")


def test_startup_dir_from_appdata() -> None:
    base = Path("C:\\Users\\Max\\AppData\\Roaming")
    expected = base / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
    assert startup_dir({"APPDATA": str(base)}) == expected


def test_startup_dir_without_appdata() -> None:
    assert startup_dir({}) is None
    assert startup_dir({"APPDATA": "  "}) is None


def test_file_name() -> None:
    assert FILE_NAME == "JARVIS.cmd"


def test_start_script_exact_content() -> None:
    assert start_script(PROJECT) == (
        "@echo off\r\n"
        "chcp 65001 >nul\r\n"
        'cd /d "C:\\JARVIS\\core" || exit /b 1\r\n'
        'start "JARVIS" /min uv run jarvis serve\r\n'
    )


def test_start_script_quotes_executable() -> None:
    script = start_script(PROJECT, executable="C:\\Users\\Max Muster\\bin\\uv.exe")
    assert script.splitlines()[-1] == (
        'start "JARVIS" /min "C:\\Users\\Max Muster\\bin\\uv.exe" run jarvis serve'
    )


@pytest.mark.parametrize(
    "path",
    [
        'C:\\a"b\\core',
        "C:\\a\nb\\core",
        "C:\\a\rb\\core",
        "C:\\%USERNAME%\\core",
        "core",  # relativ
        "\\\\server\\freigabe\\core",  # UNC: cd /d kann das nicht
    ],
)
def test_start_script_rejects_unsafe_paths(path: str) -> None:
    with pytest.raises(ValueError):
        start_script(Path(path))


@pytest.mark.parametrize("executable", ['u"v', "uv\nx", "%UV%", " "])
def test_start_script_rejects_unsafe_executable(executable: str) -> None:
    with pytest.raises(ValueError):
        start_script(PROJECT, executable=executable)


def test_enable_writes_file_with_space_and_umlaut(tmp_path: Path) -> None:
    result = enable(Path("C:\\Pfad mit Leer\\Jörg Müßig\\core"), directory=tmp_path)
    target = tmp_path / "JARVIS.cmd"
    assert result.ok and result.enabled is True
    assert str(target) in result.message
    # Bytes prüfen: CRLF, UTF-8, kein BOM.
    assert (
        target.read_bytes()
        == (
            "@echo off\r\n"
            "chcp 65001 >nul\r\n"
            'cd /d "C:\\Pfad mit Leer\\Jörg Müßig\\core" || exit /b 1\r\n'
            'start "JARVIS" /min uv run jarvis serve\r\n'
        ).encode()
    )
    assert [p.name for p in tmp_path.iterdir()] == ["JARVIS.cmd"]


def test_enable_overwrites_existing_file(tmp_path: Path) -> None:
    target = tmp_path / "JARVIS.cmd"
    target.write_text("alt, viel länger als der neue Inhalt " * 20, encoding="utf-8")
    assert enable(PROJECT, directory=tmp_path).ok
    assert target.read_bytes() == start_script(PROJECT).encode()


def test_enable_rejected_path_writes_nothing(tmp_path: Path) -> None:
    result = enable(Path('C:\\a"b\\core'), directory=tmp_path)
    assert not result.ok and result.enabled is None
    assert "Anführungszeichen" in result.message
    assert list(tmp_path.iterdir()) == []


def test_enable_missing_directory_is_message_not_exception(tmp_path: Path) -> None:
    missing = tmp_path / "gibt-es-nicht"
    result = enable(PROJECT, directory=missing)
    assert not result.ok and result.enabled is False
    assert result.message == f"Autostart-Ordner nicht gefunden: {missing}"
    assert not missing.exists()  # wird nicht angelegt


def test_enable_write_error_is_message_not_exception(tmp_path: Path) -> None:
    (tmp_path / "JARVIS.cmd").mkdir()  # ein Ordner steht im Weg: Schreiben scheitert überall
    result = enable(PROJECT, directory=tmp_path)
    assert not result.ok and result.enabled is None
    assert result.message.startswith("Autostart konnte nicht eingerichtet werden (")


def test_status_on_and_off(tmp_path: Path) -> None:
    target = tmp_path / "JARVIS.cmd"
    off = status(directory=tmp_path)
    assert off.ok and off.enabled is False
    assert off.message == f"Autostart ist nicht eingerichtet ({target} fehlt)."
    enable(PROJECT, directory=tmp_path)
    assert status(directory=tmp_path) == Result(
        True, True, f"Autostart ist eingerichtet ({target})."
    )


def test_disable_removes_file(tmp_path: Path) -> None:
    enable(PROJECT, directory=tmp_path)
    result = disable(directory=tmp_path)
    assert result.ok and result.enabled is False
    assert result.message.startswith("Autostart entfernt")
    assert list(tmp_path.iterdir()) == []
    assert status(directory=tmp_path).enabled is False


def test_disable_missing_file_is_no_error(tmp_path: Path) -> None:
    assert disable(directory=tmp_path) == Result(True, False, "Autostart war nicht eingerichtet.")


def test_disable_error_is_message_not_exception(tmp_path: Path) -> None:
    blocker = tmp_path / "JARVIS.cmd"
    blocker.mkdir()  # unlink auf einen Ordner scheitert überall
    result = disable(directory=tmp_path)
    assert not result.ok and result.enabled is True
    assert result.message.startswith("Autostart konnte nicht entfernt werden (")
    assert blocker.is_dir()


@pytest.mark.parametrize("platform", ["linux", "darwin"])
def test_other_platforms_without_directory_touch_nothing(
    platform: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Selbst ein gesetztes APPDATA darf hier nicht benutzt werden.
    monkeypatch.setenv("APPDATA", str(tmp_path))
    assert enable(PROJECT, platform=platform) == NOT_SUPPORTED
    assert disable(platform=platform) == NOT_SUPPORTED
    assert status(platform=platform) == NOT_SUPPORTED
    assert list(tmp_path.iterdir()) == []


def test_windows_without_appdata_reports_message(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("APPDATA", raising=False)
    for result in (
        enable(PROJECT, platform="win32"),
        disable(platform="win32"),
        status(platform="win32"),
    ):
        assert not result.ok and result.enabled is None
        assert "APPDATA" in result.message


def test_windows_uses_startup_dir_from_appdata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    startup = tmp_path / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
    monkeypatch.setenv("APPDATA", str(tmp_path))
    missing = enable(PROJECT, platform="win32")
    assert not missing.ok and "nicht gefunden" in missing.message
    assert not startup.exists()  # der Ordner wird nie angelegt
    startup.mkdir(parents=True)
    assert enable(PROJECT, platform="win32").ok
    assert (startup / "JARVIS.cmd").is_file()
    assert status(platform="win32").enabled is True
    assert disable(platform="win32").ok
    assert not (startup / "JARVIS.cmd").exists()


def test_start_file_runs_under_real_cmd(tmp_path: Path) -> None:
    """Statt uv läuft Python: `"python" run jarvis serve` startet das Skript `run` im Zielordner.
    Das klappt nur, wenn cmd den Pfad mit Leerzeichen und Umlaut richtig liest (chcp 65001)."""
    if sys.platform != "win32":
        pytest.skip("braucht das echte cmd.exe")
    project = tmp_path / "Pfad mit Leer" / "Jörg Müßig" / "core"
    project.mkdir(parents=True)
    (project / "run").write_text(
        "import os, sys\n"
        "with open('marke.tmp', 'w', encoding='utf-8') as fh:\n"
        "    fh.write(' '.join(sys.argv[1:]))\n"
        "os.replace('marke.tmp', 'marke.txt')\n",
        encoding="utf-8",
    )
    assert enable(project, directory=tmp_path, executable=sys.executable).ok
    # Eigene Konsole: chcp braucht eine, und die CI hat nicht immer eine.
    done = subprocess.run(
        ["cmd", "/c", str(tmp_path / "JARVIS.cmd")],
        capture_output=True,
        timeout=60,
        check=False,
        creationflags=subprocess.CREATE_NEW_CONSOLE,
    )
    assert done.returncode == 0, done.stderr
    marker = project / "marke.txt"
    deadline = time.monotonic() + 30  # `start` wartet nicht auf das gestartete Programm
    while not marker.exists() and time.monotonic() < deadline:
        time.sleep(0.2)
    assert marker.read_text(encoding="utf-8") == "jarvis serve"


# --------------------------------------------------------------------------- Befehl jarvis autostart
def test_cli_autostart_reports_and_sets_the_exit_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from typer.testing import CliRunner

    from jarvis_core.cli import main as cli_main

    monkeypatch.chdir(tmp_path)
    (tmp_path / "jarvis.toml").write_text("", encoding="utf-8")
    calls: list[tuple[str, Path | None]] = []

    def fake_enable(project_dir: Path) -> Result:
        calls.append(("an", project_dir))
        return Result(ok=True, enabled=True, message="eingerichtet")

    def fake_disable() -> Result:
        calls.append(("aus", None))
        return Result(ok=False, enabled=None, message="geht nicht")

    monkeypatch.setattr(cli_main.autostart_task, "enable", fake_enable)
    monkeypatch.setattr(cli_main.autostart_task, "disable", fake_disable)
    on = CliRunner().invoke(cli_main.app, ["autostart", "AN"])
    assert on.exit_code == 0 and "eingerichtet" in on.output
    off = CliRunner().invoke(cli_main.app, ["autostart", "aus"])
    assert off.exit_code == 1 and "geht nicht" in off.output
    assert calls == [("an", tmp_path.resolve()), ("aus", None)]  # Ordner mit jarvis.toml
    assert CliRunner().invoke(cli_main.app, ["autostart", "vielleicht"]).exit_code == 2
