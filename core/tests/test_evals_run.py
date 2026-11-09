"""Kompletter Eval-Lauf gegen ein skriptbares Testmodell (kein Ollama): deterministisch."""

import json
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import typer
from pydantic_ai.messages import ModelMessage, ToolReturnPart, UserPromptPart
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel

from jarvis_core.cli import main as cli_main
from jarvis_core.config import Settings
from jarvis_core.evals import (
    EvalAbort,
    EvalTask,
    default_evals_dir,
    format_table,
    load_tasks,
    run_evals,
    save_report,
)

pytestmark = pytest.mark.anyio

ARGS = {
    "get_weather": {"ort": "Dortmund"},
    "list_events": {"zeitraum": "morgen"},
    "create_event": {"titel": "Zahnarzt", "start": "2026-12-12T15:00"},
    "notion_query": {"datenbank": "mission_control"},
    "notion_search": {"begriff": "Handball"},
}


def scripted_model(tasks: list[EvalTask], *, use_tools: bool = True) -> FunctionModel:
    """Ein 'perfekter' (use_tools=True) bzw. werkzeugscheuer Agent, gesteuert über den Prompt."""
    by_prompt = {t.prompt: t for t in tasks}

    async def stream(
        messages: list[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[str | dict[int, DeltaToolCall]]:
        prompt = next(
            str(p.content) for m in messages for p in m.parts if isinstance(p, UserPromptPart)
        )
        returned = [p for m in messages for p in m.parts if isinstance(p, ToolReturnPart)]
        task = by_prompt[prompt]
        if use_tools and task.expect_tools and not returned:
            name = task.expect_tools[0]
            args = dict(ARGS[name])
            if name == "get_weather" and "Xyzqwertplatz" in prompt:
                args["ort"] = "Xyzqwertplatz"  # der Agent fragt nach dem Ort aus der Eingabe
            if name == "notion_query" and "Academy" in prompt:
                args["datenbank"] = "academy"
            yield {0: DeltaToolCall(name=name, json_args=json.dumps(args))}
        elif returned:
            yield f"Ergebnis: {returned[-1].content}"
        else:
            yield " ".join(task.answer_any) or "Okay."

    return FunctionModel(stream_function=stream)


@pytest.fixture
def tasks() -> list[EvalTask]:
    return load_tasks(default_evals_dir() / "tasks.toml")


async def test_full_run_with_perfect_scripted_model_passes_everything(
    tasks: list[EvalTask], tmp_path: Path
) -> None:
    base = Settings(data_dir=tmp_path)
    seen: list[str] = []
    report = await run_evals(
        tasks, base, "test", runs=1, model=scripted_model(tasks),
        on_run=lambda t, i, r: seen.append(t.id),
    )  # fmt: skip
    failed = [(s.task.id, s.results[0].reasons) for s in report.summaries if s.passed != s.runs]
    assert failed == []
    assert report.total_runs == len(tasks) == len(seen)
    assert report.rate == 1.0

    # Ja-Aufgaben haben genau einmal in die Attrappe geschrieben, Nein-Aufgaben nie.
    for summary in report.summaries:
        obs = summary.results[0].observation
        assert obs.writes == summary.task.expect_writes
        if summary.task.confirm == "no" and summary.task.expect_confirm:
            assert obs.writes == 0 and obs.confirm_requests == 1

    table = format_table(report)
    assert f"Gesamt: {len(tasks)}/{len(tasks)} (100%)" in table
    path = save_report(report, tmp_path / "results")
    assert json.loads(path.read_text(encoding="utf-8"))["overall"]["rate"] == 1.0


async def test_tool_shy_model_fails_exactly_the_tool_tasks(
    tasks: list[EvalTask], tmp_path: Path
) -> None:
    subset = [
        t for t in tasks if t.id in {"chat-identity", "weather-dortmund", "calendar-create-yes"}
    ]
    report = await run_evals(
        subset, Settings(data_dir=tmp_path), "test", runs=2,
        model=scripted_model(subset, use_tools=False),
    )  # fmt: skip
    by_id = {s.task.id: s for s in report.summaries}
    assert by_id["chat-identity"].passed == 2
    assert by_id["weather-dortmund"].passed == 0
    assert by_id["calendar-create-yes"].passed == 0
    assert by_id["calendar-create-yes"].results[0].observation.writes == 0


async def test_erroring_task_counts_as_failure_and_run_continues(tmp_path: Path) -> None:
    tasks = [
        EvalTask(id="a", prompt="Hallo"),
        EvalTask(id="b", prompt="Hallo nochmal"),
    ]

    async def boom(
        messages: list[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[str | dict[int, DeltaToolCall]]:
        raise RuntimeError("Modell kaputt")
        yield "unerreichbar"

    report = await run_evals(
        tasks,
        Settings(data_dir=tmp_path),
        "test",
        runs=1,
        model=FunctionModel(stream_function=boom),
    )
    assert [s.passed for s in report.summaries] == [0, 0]
    assert report.summaries[0].results[0].observation.error is not None


async def test_before_run_abort_propagates_and_stops(tasks: list[EvalTask], tmp_path: Path) -> None:
    calls = {"n": 0}

    def gone() -> None:
        calls["n"] += 1
        if calls["n"] == 2:
            raise EvalAbort("Ollama weg")

    with pytest.raises(EvalAbort):
        await run_evals(
            tasks[:3], Settings(data_dir=tmp_path), "test", runs=1,
            model=scripted_model(tasks), before_run=gone,
        )  # fmt: skip
    assert calls["n"] == 2


# ----------------------------------------------------------------------------- CLI
def _cli_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, url: str) -> None:
    settings = Settings(data_dir=tmp_path / "data", ollama_base_url=url)
    monkeypatch.setattr(cli_main, "load_settings", lambda: settings)


def test_cli_run_without_ollama_aborts_cleanly_without_result_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _cli_settings(monkeypatch, tmp_path, "http://127.0.0.1:1/v1")
    results = tmp_path / "ev"
    results.mkdir()
    (results / "tasks.toml").write_text('[[task]]\nid = "x"\nprompt = "Hi"\n', encoding="utf-8")
    with pytest.raises(typer.Exit) as exit_info:
        cli_main.evals_run(model="qwen3.5:4b", runs=1, only=[], limit_s=5.0, directory=results)
    assert exit_info.value.exit_code == 1
    err = capsys.readouterr().err
    assert "nicht erreichbar" in err and "kein Ergebnis gespeichert" in err
    assert "Traceback" not in err
    assert not (results / "results").exists()


def test_cli_run_with_missing_model_names_the_pull_command(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    fake_server: object,
) -> None:
    url = fake_server(lambda _p: (200, {"models": [{"name": "anderes:1b"}]}))  # type: ignore[operator]
    _cli_settings(monkeypatch, tmp_path, f"{url}/v1")
    results = tmp_path / "ev"
    results.mkdir()
    (results / "tasks.toml").write_text('[[task]]\nid = "x"\nprompt = "Hi"\n', encoding="utf-8")
    with pytest.raises(typer.Exit):
        cli_main.evals_run(model="qwen3.5:4b", runs=1, only=[], limit_s=5.0, directory=results)
    assert "ollama pull qwen3.5:4b" in capsys.readouterr().err


def test_cli_list(capsys: pytest.CaptureFixture[str]) -> None:
    cli_main.evals_list(directory=None)
    out = capsys.readouterr().out
    assert "weather-dortmund" in out and "get_weather" in out and "calendar-create-no" in out
    assert "Aufgaben" in out
