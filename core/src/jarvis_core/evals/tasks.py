"""Eval-Aufgaben als Daten (evals/tasks.toml); neue Aufgaben brauchen keine Codeänderung."""

import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class EvalTask(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    prompt: str = Field(min_length=1)
    expect_tools: list[str] = []
    confirm: Literal["yes", "no"] = "no"
    expect_confirm: bool = False
    expect_writes: int = Field(default=0, ge=0)
    answer_any: list[str] = []
    answer_forbid: list[str] = []


class EvalTaskError(Exception):
    """Aufgabendatei fehlt oder ist ungültig (deutsche Meldung)."""


def default_evals_dir() -> Path:
    """<Repo>/evals (src/jarvis_core/evals/tasks.py -> vier Ebenen nach oben)."""
    return Path(__file__).resolve().parents[4] / "evals"


def load_tasks(path: Path) -> list[EvalTask]:
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except OSError:
        raise EvalTaskError(f"Aufgabendatei nicht lesbar: {path}") from None
    except tomllib.TOMLDecodeError as exc:
        raise EvalTaskError(f"Aufgabendatei ist kein gültiges TOML: {exc}") from None
    entries = raw.get("task")
    if not isinstance(entries, list) or not entries:
        raise EvalTaskError("Die Aufgabendatei enthält keine [[task]]-Einträge.")
    tasks: list[EvalTask] = []
    for index, entry in enumerate(entries, start=1):  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
        try:
            tasks.append(EvalTask.model_validate(entry))
        except ValidationError as exc:
            first = exc.errors()[0]
            where = ".".join(str(p) for p in first["loc"])
            raise EvalTaskError(f"Aufgabe #{index} ungültig ({where}): {first['msg']}") from None
    ids = [t.id for t in tasks]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        raise EvalTaskError(f"Doppelte Aufgaben-IDs: {', '.join(duplicates)}")
    return tasks
