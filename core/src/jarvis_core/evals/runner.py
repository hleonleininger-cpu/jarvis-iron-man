"""Eval-Runner: derselbe Agent-/Hub-/Policy-Pfad wie im Betrieb, nur mit Attrappen-Servern.

Gemessen wird über das Audit-Log (welche Werkzeuge) und das Protokoll der Kalender-Attrappe
(Schreibaufrufe). Je Durchlauf frisches Audit-Log und frisches Protokoll.
"""

import asyncio
import sqlite3
import tempfile
import time
from collections.abc import Callable
from contextlib import aclosing
from datetime import datetime
from pathlib import Path

from pydantic_ai.models import Model

from jarvis_core.agent import ChatService, ConfirmRequested, TextDelta, build_service
from jarvis_core.audit import AuditLog
from jarvis_core.config import Settings
from jarvis_core.config.settings import McpServerConfig
from jarvis_core.evals.checks import Observation, RunResult, evaluate
from jarvis_core.evals.report import Report, TaskSummary
from jarvis_core.evals.tasks import EvalTask
from jarvis_core.policy import RiskClass

DEFAULT_TIMEOUT_SECONDS = 240.0
EVAL_CHANNEL = "eval"


class EvalAbort(Exception):
    """Lauf abbrechen (z. B. Ollama nicht erreichbar); es wird kein Teilergebnis gespeichert."""


# Beispiel-IDs für die Notion-Attrappe (UUID-Form wie die echten Data-Source-IDs).
EVAL_NOTION_ENV = {
    "NOTION_DB_MISSION_CONTROL": "00000000-0000-0000-0000-0000000000a1",
    "NOTION_DB_ACADEMY": "00000000-0000-0000-0000-0000000000a2",
}


def eval_servers(writes_log: Path) -> dict[str, McpServerConfig]:
    """Dieselben Werkzeuge und Risikoklassen wie jarvis.example.toml, aber mit Attrappen."""
    return {
        "weather": McpServerConfig(
            command="python",
            args=["-m", "mcp_servers.doubles.weather"],
            tools={"get_weather": RiskClass.READ},
        ),
        "notion": McpServerConfig(
            command="python",
            args=["-m", "mcp_servers.doubles.notion"],
            env=EVAL_NOTION_ENV,
            tools={
                "notion_search": RiskClass.READ,
                "notion_query": RiskClass.READ,
                "notion_get": RiskClass.READ,
            },
        ),
        "calendar": McpServerConfig(
            command="python",
            args=["-m", "mcp_servers.doubles.calendar", str(writes_log)],
            tools={"list_events": RiskClass.READ, "create_event": RiskClass.WRITE_REVERSIBLE},
            previews={"create_event": "preview_create_event"},
        ),
    }


def _tools_from_audit(db_path: Path) -> list[str]:
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute("SELECT tool FROM audit WHERE tool IS NOT NULL ORDER BY id").fetchall()
    seen: list[str] = []
    for (tool,) in rows:
        if tool not in seen:
            seen.append(tool)
    return seen


def _count_writes(log: Path) -> int:
    if not log.exists():
        return 0
    return len([line for line in log.read_text(encoding="utf-8").splitlines() if line.strip()])


async def _consume(service: ChatService, task: EvalTask) -> tuple[str, int]:
    answer: list[str] = []
    confirms = 0
    async with aclosing(service.stream(task.prompt, channel=EVAL_CHANNEL)) as events:
        async for event in events:
            if isinstance(event, TextDelta):
                answer.append(event.text)
            elif isinstance(event, ConfirmRequested):
                confirms += 1
                service.broker.resolve(event.id, task.confirm == "yes")
    return "".join(answer), confirms


async def run_once(
    task: EvalTask,
    base: Settings,
    model_name: str,
    model: Model | None,
    limit_s: float,
) -> RunResult:
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="jarvis-eval-") as tmp:
        workdir = Path(tmp)
        writes_log = workdir / "writes.jsonl"
        settings = base.model_copy(
            update={"data_dir": workdir, "mcp_servers": eval_servers(writes_log)}
        )
        audit = AuditLog(settings.audit_db_path)
        service = build_service(settings, audit, model, model_name)
        answer, confirms, error = "", 0, None
        try:
            answer, confirms = await asyncio.wait_for(_consume(service, task), limit_s)
        except TimeoutError:
            error = f"Zeitüberschreitung nach {limit_s:.0f} s"
        except Exception as exc:  # Einzelfehler zählen als Fehlschlag, der Lauf geht weiter
            error = f"{type(exc).__name__}: {str(exc)[:200]}"
        observation = Observation(
            tools_called=_tools_from_audit(settings.audit_db_path),
            writes=_count_writes(writes_log),
            confirm_requests=confirms,
            answer=answer,
            error=error,
            duration_s=time.monotonic() - started,
        )
    return evaluate(task, observation)


async def run_evals(
    tasks: list[EvalTask],
    base: Settings,
    model_name: str,
    runs: int = 3,
    *,
    model: Model | None = None,
    limit_s: float = DEFAULT_TIMEOUT_SECONDS,
    before_run: Callable[[], None] | None = None,
    on_run: Callable[[EvalTask, int, RunResult], None] | None = None,
) -> Report:
    """Jede Aufgabe `runs`-mal. `before_run` darf EvalAbort werfen (z. B. Ollama weg)."""
    started = datetime.now()
    clock = time.monotonic()
    summaries: list[TaskSummary] = []
    for task in tasks:
        results: list[RunResult] = []
        for index in range(1, runs + 1):
            if before_run is not None:
                before_run()
            result = await run_once(task, base, model_name, model, limit_s)
            results.append(result)
            if on_run is not None:
                on_run(task, index, result)
        summaries.append(TaskSummary(task, results))
    return Report(model_name, runs, started, time.monotonic() - clock, summaries)
