"""Sprecherprofil (M2.9): nur ein Vektor, nie Audio. Liegt lokal in `data/voice/sprecher.json`
(Rechte nur für den Benutzer, wo das System es kennt), wird nie geloggt und mit
`jarvis voice vergessen` gelöscht."""

import contextlib
import json
import math
import os
from collections.abc import Sequence
from pathlib import Path

PROFILE_VERSION = 1
MIN_CLIPS = 3


class ProfileError(Exception):
    """Profil fehlt oder ist unbrauchbar (deutsche Meldung)."""


def profile_path(data_dir: Path) -> Path:
    return data_dir / "voice" / "sprecher.json"


def normalize(vector: Sequence[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vector))
    if norm == 0:
        raise ProfileError("Der Sprecher-Vektor ist leer.")
    return [x / norm for x in vector]


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    na, nb = normalize(a), normalize(b)
    return sum(x * y for x, y in zip(na, nb, strict=True))


def average(vectors: Sequence[Sequence[float]]) -> list[float]:
    """Mittelwert normierter Vektoren (wieder normiert)."""
    if len(vectors) < MIN_CLIPS:
        raise ProfileError(f"Zum Anlernen braucht es mindestens {MIN_CLIPS} Aufnahmen.")
    dim = len(vectors[0])
    if any(len(v) != dim for v in vectors):
        raise ProfileError("Die Vektoren haben unterschiedliche Längen.")
    normed = [normalize(v) for v in vectors]
    return normalize([sum(v[i] for v in normed) / len(normed) for i in range(dim)])


def save_profile(path: Path, vector: Sequence[float], model: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": PROFILE_VERSION, "model": model, "embedding": normalize(vector)}
    path.write_text(json.dumps(payload), encoding="utf-8")
    with contextlib.suppress(OSError, NotImplementedError):
        os.chmod(path, 0o600)  # nur der Benutzer; unter Windows ohne Wirkung


def load_profile(path: Path) -> tuple[list[float], str]:
    """(Vektor, Modellname). Wirft ProfileError, wenn es fehlt oder beschädigt ist."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        vector = [float(x) for x in data["embedding"]]
        model = str(data["model"])
        if data.get("version") != PROFILE_VERSION or not vector:
            raise ValueError
    except FileNotFoundError:
        raise ProfileError(
            "Es gibt noch kein Sprecherprofil. Anlernen mit: jarvis voice anlernen"
        ) from None
    except (OSError, ValueError, KeyError, TypeError):
        raise ProfileError(
            "Das Sprecherprofil ist unbrauchbar. Neu anlernen mit: jarvis voice anlernen"
        ) from None
    return normalize(vector), model


def delete_profile(path: Path) -> bool:
    """Löscht das Profil. True, wenn es eines gab."""
    try:
        path.unlink()
    except FileNotFoundError:
        return False
    return True
