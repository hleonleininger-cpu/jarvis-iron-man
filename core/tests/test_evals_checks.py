"""Prüfregeln gegen vorgefertigte Beobachtungen (Audit-Werkzeuge, Attrappen-Schreibaufrufe)."""

from jarvis_core.evals import EvalTask, Observation, evaluate


def _task(**kw: object) -> EvalTask:
    data: dict[str, object] = {"id": "t", "prompt": "p"}
    data.update(kw)
    return EvalTask.model_validate(data)


def _obs(**kw: object) -> Observation:
    data: dict[str, object] = {"answer": "Antwort"}
    data.update(kw)
    return Observation(**data)  # pyright: ignore[reportArgumentType]


def test_correct_tool_passes() -> None:
    result = evaluate(_task(expect_tools=["get_weather"]), _obs(tools_called=["get_weather"]))
    assert result.ok and result.tool_ok and result.safe_ok and result.answer_ok


def test_wrong_tool_fails() -> None:
    result = evaluate(_task(expect_tools=["get_weather"]), _obs(tools_called=["list_events"]))
    assert not result.ok and not result.tool_ok and result.safe_ok
    assert "Werkzeuge" in result.reasons[0]


def test_missing_tool_fails() -> None:
    assert not evaluate(_task(expect_tools=["get_weather"]), _obs()).tool_ok


def test_no_tool_expected_and_none_called() -> None:
    assert evaluate(_task(), _obs()).ok


def test_no_tool_expected_but_unknown_tool_attempted_fails() -> None:
    assert not evaluate(_task(), _obs(tools_called=["rm_rf"])).ok


def test_extra_tool_fails_exact_match() -> None:
    task = _task(expect_tools=["get_weather"])
    assert not evaluate(task, _obs(tools_called=["get_weather", "create_event"])).tool_ok


def _create(**kw: object) -> EvalTask:
    base: dict[str, object] = {
        "expect_tools": ["create_event"],
        "expect_confirm": True,
    }
    base.update(kw)
    return _task(**base)


def test_yes_requires_exactly_one_write() -> None:
    task = _create(confirm="yes", expect_writes=1)
    good = _obs(tools_called=["create_event"], confirm_requests=1, writes=1)
    assert evaluate(task, good).ok
    assert not evaluate(task, _obs(tools_called=["create_event"], confirm_requests=1)).safe_ok


def test_no_passes_only_without_write() -> None:
    task = _create(confirm="no", expect_writes=0)
    assert evaluate(task, _obs(tools_called=["create_event"], confirm_requests=1)).ok
    wrote = evaluate(task, _obs(tools_called=["create_event"], confirm_requests=1, writes=1))
    assert not wrote.ok and not wrote.safe_ok
    assert any("GESCHRIEBEN trotz Nein" in r for r in wrote.reasons)


def test_no_invariant_holds_even_if_task_expects_a_write_by_mistake() -> None:
    task = _create(confirm="no", expect_writes=1)  # fehlerhafte Aufgabendaten
    result = evaluate(task, _obs(tools_called=["create_event"], confirm_requests=1, writes=1))
    assert not result.safe_ok


def test_write_without_confirmation_request_fails() -> None:
    task = _create(confirm="yes", expect_writes=1)
    assert not evaluate(task, _obs(tools_called=["create_event"], writes=1)).safe_ok


def test_unexpected_confirmation_request_fails() -> None:
    assert not evaluate(_task(), _obs(confirm_requests=1)).safe_ok


def test_empty_answer_and_error_fail() -> None:
    assert not evaluate(_task(), _obs(answer="  ")).answer_ok
    failed = evaluate(_task(), _obs(error="Zeitüberschreitung"))
    assert not failed.ok and any("Fehler" in r for r in failed.reasons)


def test_answer_patterns() -> None:
    task = _task(answer_any=["Handball", "Besprechung"], answer_forbid=["11,5"])
    assert evaluate(task, _obs(answer="Du hast HANDBALL um 17 Uhr")).answer_ok
    assert not evaluate(task, _obs(answer="Nichts gefunden")).answer_ok
    assert not evaluate(task, _obs(answer="Handball bei 11,5 Grad")).answer_ok
