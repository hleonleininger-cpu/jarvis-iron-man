import json
from datetime import datetime
from pathlib import Path

from jarvis_core.evals import (
    EvalTask,
    Observation,
    Report,
    TaskSummary,
    evaluate,
    format_table,
    save_report,
)


def _summary(task_id: str, outcomes: list[bool], **task_kw: object) -> TaskSummary:
    data: dict[str, object] = {"id": task_id, "prompt": "p"}
    data.update(task_kw)
    task = EvalTask.model_validate(data)
    results = [
        evaluate(task, Observation(answer="x" if ok else "", duration_s=1.0)) for ok in outcomes
    ]
    return TaskSummary(task, results)


def _report() -> Report:
    return Report(
        model="qwen3.5:4b",
        runs=3,
        started=datetime(2026, 11, 9, 15, 30, 5),
        duration_s=125.0,
        summaries=[
            _summary("a", [True, True, True]),
            _summary("b", [True, False, True], expect_tools=["get_weather"]),
            _summary("c", [False, False, False], expect_confirm=True, confirm="no"),
        ],
    )


def test_aggregation() -> None:
    # b erwartet ein Werkzeug, das nie aufgerufen wird -> alle Durchläufe fehlgeschlagen
    report = _report()
    assert [s.passed for s in report.summaries] == [3, 0, 0]
    assert report.passed_runs == 3
    assert round(report.rate, 3) == 0.333


def test_table_has_x_of_n_and_overall() -> None:
    table = format_table(_report())
    assert "3/3" in table and "0/3" in table
    assert "Gesamt: 3/9 (33%)" in table
    assert "Aufgaben komplett bestanden: 1/3" in table
    assert "get_weather" in table and "keins" in table and "[Nein]" in table
    assert "2.1 min" in table


def test_save_format_and_filename(tmp_path: Path) -> None:
    path = save_report(_report(), tmp_path / "results")
    assert path.name == "20261109-153005-qwen3.5-4b.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["model"] == "qwen3.5:4b" and data["runs_per_task"] == 3
    assert data["overall"] == {
        "passed": 3, "total": 9, "rate": 0.3333, "tasks_fully_passed": 1, "tasks": 3,
    }  # fmt: skip
    task_b = data["tasks"][1]
    assert task_b["id"] == "b" and task_b["runs"] == 3 and len(task_b["results"]) == 3
    assert {"ok", "tools_called", "writes", "error", "reasons", "answer"} <= set(
        task_b["results"][0]
    )
