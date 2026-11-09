"""Aggregation (x/N je Aufgabe, gesamt), Terminal-Tabelle und Ergebnisdatei."""

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from jarvis_core.evals.checks import RunResult
from jarvis_core.evals.tasks import EvalTask


@dataclass(frozen=True)
class TaskSummary:
    task: EvalTask
    results: list[RunResult]

    @property
    def runs(self) -> int:
        return len(self.results)

    @property
    def passed(self) -> int:
        return sum(r.ok for r in self.results)

    def aspect(self, name: str) -> int:
        return sum(bool(getattr(r, name)) for r in self.results)


@dataclass(frozen=True)
class Report:
    model: str
    runs: int
    started: datetime
    duration_s: float
    summaries: list[TaskSummary] = field(default_factory=lambda: [])

    @property
    def total_runs(self) -> int:
        return sum(s.runs for s in self.summaries)

    @property
    def passed_runs(self) -> int:
        return sum(s.passed for s in self.summaries)

    @property
    def rate(self) -> float:
        return self.passed_runs / self.total_runs if self.total_runs else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "runs_per_task": self.runs,
            "started": self.started.isoformat(timespec="seconds"),
            "duration_s": round(self.duration_s, 1),
            "overall": {
                "passed": self.passed_runs,
                "total": self.total_runs,
                "rate": round(self.rate, 4),
                "tasks_fully_passed": sum(s.passed == s.runs for s in self.summaries),
                "tasks": len(self.summaries),
            },
            "tasks": [
                {
                    "id": s.task.id,
                    "prompt": s.task.prompt,
                    "expect_tools": s.task.expect_tools,
                    "confirm": s.task.confirm,
                    "expect_writes": s.task.expect_writes,
                    "passed": s.passed,
                    "runs": s.runs,
                    "tool_ok": s.aspect("tool_ok"),
                    "safe_ok": s.aspect("safe_ok"),
                    "answer_ok": s.aspect("answer_ok"),
                    "results": [
                        {
                            "ok": r.ok,
                            "tool_ok": r.tool_ok,
                            "safe_ok": r.safe_ok,
                            "answer_ok": r.answer_ok,
                            "tools_called": r.observation.tools_called,
                            "writes": r.observation.writes,
                            "confirm_requests": r.observation.confirm_requests,
                            "duration_s": round(r.observation.duration_s, 1),
                            "error": r.observation.error,
                            "reasons": r.reasons,
                            "answer": r.observation.answer[:500],
                        }
                        for r in s.results
                    ],
                }
                for s in self.summaries
            ],
        }


def format_table(report: Report) -> str:
    rows = [("Aufgabe", "Erwartet", "Erfolg", "Werkzeug", "Sicher", "Antwort")]
    for s in report.summaries:
        expected = ", ".join(s.task.expect_tools) or "keins"
        if s.task.expect_confirm:
            expected += f" [{'Ja' if s.task.confirm == 'yes' else 'Nein'}]"
        n = s.runs
        rows.append(
            (
                s.task.id,
                expected,
                f"{s.passed}/{n}",
                f"{s.aspect('tool_ok')}/{n}",
                f"{s.aspect('safe_ok')}/{n}",
                f"{s.aspect('answer_ok')}/{n}",
            )
        )
    widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
    lines = ["  ".join(c.ljust(widths[i]) for i, c in enumerate(r)) for r in rows]
    lines.insert(1, "  ".join("-" * w for w in widths))
    fully = sum(s.passed == s.runs for s in report.summaries)
    lines += [
        "",
        f"Modell: {report.model}   Durchläufe je Aufgabe: {report.runs}",
        f"Gesamt: {report.passed_runs}/{report.total_runs} ({report.rate:.0%})   "
        f"Aufgaben komplett bestanden: {fully}/{len(report.summaries)}   "
        f"Dauer: {report.duration_s / 60:.1f} min",
    ]
    return "\n".join(lines)


def result_filename(report: Report) -> str:
    safe_model = "".join(c if c.isalnum() or c in "._-" else "-" for c in report.model)
    return f"{report.started:%Y%m%d-%H%M%S}-{safe_model}.json"


def save_report(report: Report, results_dir: Path) -> Path:
    results_dir.mkdir(parents=True, exist_ok=True)
    path = results_dir / result_filename(report)
    path.write_text(
        json.dumps(report.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return path
