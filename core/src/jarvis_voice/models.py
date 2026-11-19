"""Modelle des Sprachclients: Verzeichnis (`models.toml`), Prüfung und ausdrückliches Laden.

`jarvis voice setup` ist der einzige Netzzugriff der Sprachfunktion. Jede Datei wird gegen
Größe und SHA-256 aus `models.toml` geprüft; im Betrieb bekommen die Engines nur lokale Pfade.
"""

import hashlib
import http.client
import tomllib
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any

CHUNK = 1 << 20


class ModelError(Exception):
    """Modell fehlt, ist beschädigt oder der Download schlug fehl (deutsche Meldung)."""


@dataclass(frozen=True)
class ModelFile:
    name: str
    url: str
    sha256: str
    bytes: int


@dataclass(frozen=True)
class ModelEntry:
    key: str
    zweck: str
    lizenz: str
    folder: str  # Unterordner in models_dir; leer = direkt
    files: tuple[ModelFile, ...]

    @property
    def total_bytes(self) -> int:
        return sum(f.bytes for f in self.files)


def load_registry(text: str | None = None) -> dict[str, ModelEntry]:
    if text is None:
        text = resources.files("jarvis_voice").joinpath("models.toml").read_text("utf-8")
    raw: dict[str, Any] = tomllib.loads(text)
    registry: dict[str, ModelEntry] = {}
    for key, item in raw.items():
        files = tuple(ModelFile(**f) for f in item["dateien"])
        registry[key] = ModelEntry(
            key, str(item["zweck"]), str(item["lizenz"]), str(item.get("ordner", "")), files
        )
    return registry


def entry_dir(models_dir: Path, entry: ModelEntry) -> Path:
    return models_dir / entry.folder if entry.folder else models_dir


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(CHUNK):
            digest.update(block)
    return digest.hexdigest()


def verify(models_dir: Path, entry: ModelEntry, deep: bool = False) -> list[str]:
    """Probleme einer Modellgruppe (leer = in Ordnung). `deep` prüft auch den Hash."""
    problems: list[str] = []
    folder = entry_dir(models_dir, entry)
    for file in entry.files:
        path = folder / file.name
        if not path.is_file():
            problems.append(f"{file.name}: fehlt")
        elif path.stat().st_size != file.bytes:
            problems.append(f"{file.name}: falsche Größe")
        elif deep and sha256_of(path) != file.sha256:
            problems.append(f"{file.name}: Prüfsumme stimmt nicht")
    return problems


def model_path(models_dir: Path, entry: ModelEntry, name: str | None = None) -> Path:
    """Pfad einer (geprüften) Datei; ohne `name` der Ordner der Gruppe."""
    problems = verify(models_dir, entry)
    if problems:
        raise ModelError(
            f"Modell '{entry.key}' ist nicht vollständig ({'; '.join(problems)}). "
            "Laden mit: jarvis voice setup"
        )
    folder = entry_dir(models_dir, entry)
    return folder / name if name else folder


def download(
    file: ModelFile,
    target: Path,
    progress: Callable[[int, int], None] | None = None,
    opener: urllib.request.OpenerDirector | None = None,
) -> None:
    """Lädt eine Datei, prüft Größe und SHA-256 und legt sie erst dann endgültig ab."""
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(target.suffix + ".part")
    digest = hashlib.sha256()
    size = 0
    request = urllib.request.Request(file.url, headers={"User-Agent": "jarvis-voice-setup"})  # noqa: S310
    try:
        with (
            (opener or urllib.request.build_opener()).open(request, timeout=60) as response,  # noqa: S310
            partial.open("wb") as out,
        ):
            while block := response.read(CHUNK):
                size += len(block)
                if size > file.bytes:  # mehr als erwartet: abbrechen, nicht die Platte füllen
                    raise OSError("Datei ist größer als in models.toml angegeben")
                out.write(block)
                digest.update(block)
                if progress is not None:
                    progress(size, file.bytes)
    except (OSError, http.client.HTTPException) as exc:
        partial.unlink(missing_ok=True)
        raise ModelError(
            f"Download von {file.name} fehlgeschlagen ({type(exc).__name__})."
        ) from None
    if size != file.bytes or digest.hexdigest() != file.sha256:
        partial.unlink(missing_ok=True)
        raise ModelError(
            f"{file.name}: Größe oder Prüfsumme stimmt nicht mit models.toml überein. "
            "Die Datei wurde verworfen."
        )
    partial.replace(target)


def fetch(
    models_dir: Path,
    entry: ModelEntry,
    progress: Callable[[str, int, int], None] | None = None,
    opener: urllib.request.OpenerDirector | None = None,
) -> int:
    """Lädt, was in der Gruppe fehlt oder beschädigt ist. Gibt die Zahl geladener Dateien zurück."""
    folder = entry_dir(models_dir, entry)
    loaded = 0
    for file in entry.files:
        path = folder / file.name
        if path.is_file() and path.stat().st_size == file.bytes and sha256_of(path) == file.sha256:
            continue
        download(
            file,
            path,
            (lambda done, total, name=file.name: progress(name, done, total)) if progress else None,
            opener,
        )
        loaded += 1
    return loaded
