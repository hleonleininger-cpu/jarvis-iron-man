from pathlib import Path

import pytest

from jarvis_core.evals import EvalTaskError, default_evals_dir, load_tasks


def test_shipped_tasks_are_valid_and_cover_the_required_categories() -> None:
    tasks = load_tasks(default_evals_dir() / "tasks.toml")
    assert 18 <= len(tasks) <= 50
    ids = [t.id for t in tasks]
    assert len(ids) == len(set(ids))
    assert any(not t.expect_tools for t in tasks)  # reiner Chat / unsinnig
    assert any(t.expect_tools == ["get_weather"] for t in tasks)
    assert any(t.expect_tools == ["list_events"] for t in tasks)
    assert any(t.expect_writes == 1 and t.confirm == "yes" for t in tasks)
    assert any(t.expect_tools == ["create_event"] and t.confirm == "no" for t in tasks)
    assert any("Xyzqwertplatz" in t.prompt for t in tasks)  # unbekannter Ort
    assert any("Montag" in t.prompt for t in tasks)  # mehrdeutige Zeit
    assert any(t.id.startswith("nonsense") for t in tasks)  # unsinnige Bitte
    assert any(t.expect_tools == ["notion_query"] for t in tasks)  # Notion lesen
    assert any(t.expect_tools == ["notion_search"] for t in tasks)
    assert any(t.expect_tools == ["web_search"] for t in tasks)  # Websuche
    for t in tasks:  # Nein auf eine Rückfrage darf nie schreiben
        if t.expect_confirm and t.confirm == "no":
            assert t.expect_writes == 0
    assert any(t.expect_tools == ["add_note"] and not t.expect_confirm for t in tasks)


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "tasks.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_new_task_needs_no_code(tmp_path: Path) -> None:
    path = _write(
        tmp_path, '[[task]]\nid = "neu"\nprompt = "Hallo"\nexpect_tools = ["get_weather"]\n'
    )
    (task,) = load_tasks(path)
    assert task.id == "neu" and task.expect_tools == ["get_weather"] and task.confirm == "no"


@pytest.mark.parametrize(
    "text",
    [
        "",
        "kaputt = [",
        '[[task]]\nid = "x"\n',
        '[[task]]\nid = "x"\nprompt = "p"\nunbekanntes_feld = 1\n',
        '[[task]]\nid = "x"\nprompt = "p"\nconfirm = "vielleicht"\n',
        '[[task]]\nid = "x"\nprompt = "p"\n[[task]]\nid = "x"\nprompt = "q"\n',
    ],
)
def test_invalid_task_files_give_clear_errors(tmp_path: Path, text: str) -> None:
    with pytest.raises(EvalTaskError):
        load_tasks(_write(tmp_path, text))


def test_missing_file(tmp_path: Path) -> None:
    with pytest.raises(EvalTaskError):
        load_tasks(tmp_path / "gibt-es-nicht.toml")
