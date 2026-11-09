"""Kurzer Live-Lauf gegen echtes Ollama; ohne Ollama/Modell übersprungen."""

from pathlib import Path

import pytest

from jarvis_core.config import Settings
from jarvis_core.evals import default_evals_dir, load_tasks, run_evals
from jarvis_core.hardware import check_ollama, is_installed, recommend
from jarvis_core.hardware.detect import detect_hardware

pytestmark = [pytest.mark.live, pytest.mark.anyio]


async def test_live_short_eval(tmp_path: Path) -> None:
    base = Settings(data_dir=tmp_path)
    status = check_ollama(base.ollama_base_url)
    model = base.model or recommend(detect_hardware()).model
    if not status.running:
        pytest.skip("Ollama nicht erreichbar")
    if not is_installed(model, status.models):
        pytest.skip(f"Modell {model} nicht installiert (ollama pull {model})")
    tasks = [
        t
        for t in load_tasks(default_evals_dir() / "tasks.toml")
        if t.id in {"chat-identity", "calendar-create-no"}
    ]
    report = await run_evals(tasks, base, model, runs=1)
    assert report.total_runs == 2
    by_id = {s.task.id: s for s in report.summaries}
    # Unabhängig von der Modellqualität: bei Nein darf NIE geschrieben worden sein.
    assert by_id["calendar-create-no"].results[0].observation.writes == 0
