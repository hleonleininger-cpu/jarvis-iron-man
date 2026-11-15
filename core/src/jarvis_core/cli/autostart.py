"""Autostart: JARVIS startet mit Windows, über eine Startdatei im Autostart-Ordner des Nutzers."""

import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath

TASK_NAME = "JARVIS"
FILE_NAME = f"{TASK_NAME}.cmd"
NOT_SUPPORTED = "Autostart gibt es nur unter Windows."


@dataclass(frozen=True)
class Result:
    ok: bool
    enabled: bool | None  # None: unbekannt
    message: str  # für den Nutzer


def supported(platform: str = sys.platform) -> bool:
    return platform == "win32"


def startup_dir(env: Mapping[str, str] = os.environ) -> Path | None:
    """Autostart-Ordner des angemeldeten Nutzers; None, wenn APPDATA fehlt."""
    appdata = env.get("APPDATA", "").strip()
    if not appdata:
        return None
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def _quoted(value: str, what: str) -> str:
    # In cmd gibt es kein Escape für Anführungszeichen; %NAME% würde auch in Anführungszeichen
    # ersetzt. Beides kommt in echten Pfaden praktisch nicht vor, also ablehnen statt raten.
    if not value.strip() or any(ch in value for ch in '"%\r\n'):
        raise ValueError(
            f"{what} darf nicht leer sein und weder Anführungszeichen, Prozentzeichen noch "
            f"Zeilenumbrüche enthalten: {value!r}"
        )
    return f'"{value}"'


def start_script(project_dir: Path, executable: str | None = None) -> str:
    """Inhalt der Startdatei: in den Ordner core wechseln, `uv run jarvis serve` minimiert starten.

    cmd liest eine Stapeldatei zeilenweise in der gerade aktiven Codepage. Nach `chcp 65001`
    gelten die folgenden Zeilen als UTF-8, unabhängig von der Windows-Sprache; die Zeilen davor
    sind reines ASCII. Deshalb: UTF-8 ohne BOM (ein BOM würde die erste Zeile verderben).
    """
    text = str(project_dir)
    path = PureWindowsPath(text)
    # `cd /d` braucht Laufwerk und absoluten Pfad (kein UNC); relativ wäre bei der Anmeldung falsch.
    if not path.is_absolute() or path.drive[1:] != ":":
        raise ValueError(f"Der Projektordner braucht einen absoluten Pfad mit Laufwerk: {text!r}")
    uv = "uv" if executable is None else _quoted(executable, "Der Pfad zu uv")
    lines = (
        "@echo off",
        "chcp 65001 >nul",
        # Gibt es den Ordner nicht mehr (verschoben), nicht irgendwo anders starten.
        f"cd /d {_quoted(text, 'Der Projektordner')} || exit /b 1",
        # Das erste Argument in Anführungszeichen ist für `start` immer der Fenstertitel.
        f'start "{TASK_NAME}" /min {uv} run jarvis serve',
    )
    return "".join(f"{line}\r\n" for line in lines)


def _target(directory: Path | None, platform: str) -> Path | Result:
    """Pfad der Startdatei. Ein ausdrücklich genannter Ordner gilt auf jeder Plattform."""
    if directory is None:
        if not supported(platform):
            return Result(ok=False, enabled=None, message=NOT_SUPPORTED)
        directory = startup_dir()
        if directory is None:
            return Result(
                ok=False,
                enabled=None,
                message="APPDATA ist nicht gesetzt; der Autostart-Ordner ist unbekannt.",
            )
    return directory / FILE_NAME


def enable(
    project_dir: Path,
    directory: Path | None = None,
    platform: str = sys.platform,
    executable: str | None = None,
) -> Result:
    """Schreibt die Startdatei (überschreibt eine vorhandene); legt keinen Ordner an."""
    target = _target(directory, platform)
    if isinstance(target, Result):
        return target
    try:
        content = start_script(project_dir, executable).encode("utf-8")
    except ValueError as exc:  # auch: Pfad nicht als UTF-8 darstellbar
        return Result(ok=False, enabled=None, message=str(exc))
    if not target.parent.is_dir():
        return Result(
            ok=False, enabled=False, message=f"Autostart-Ordner nicht gefunden: {target.parent}"
        )
    try:
        target.write_bytes(content)
    except OSError as exc:
        return Result(
            ok=False,
            enabled=None,  # eine ältere Datei kann noch bestehen
            message=f"Autostart konnte nicht eingerichtet werden ({target}): {exc.strerror or exc}",
        )
    return Result(
        ok=True,
        enabled=True,
        message=f"Autostart eingerichtet: JARVIS startet bei der Anmeldung ({target}).",
    )


def status(directory: Path | None = None, platform: str = sys.platform) -> Result:
    """Meldet, ob die Startdatei existiert."""
    target = _target(directory, platform)
    if isinstance(target, Result):
        return target
    if target.is_file():
        return Result(ok=True, enabled=True, message=f"Autostart ist eingerichtet ({target}).")
    return Result(
        ok=True, enabled=False, message=f"Autostart ist nicht eingerichtet ({target} fehlt)."
    )


def disable(directory: Path | None = None, platform: str = sys.platform) -> Result:
    """Löscht die Startdatei; gibt es sie nicht, ist das kein Fehler."""
    target = _target(directory, platform)
    if isinstance(target, Result):
        return target
    try:
        target.unlink()
    except FileNotFoundError:
        return Result(ok=True, enabled=False, message="Autostart war nicht eingerichtet.")
    except OSError as exc:
        return Result(
            ok=False,
            enabled=True,
            message=f"Autostart konnte nicht entfernt werden ({target}): {exc.strerror or exc}",
        )
    return Result(ok=True, enabled=False, message=f"Autostart entfernt ({target}).")
