"""Phase 2 — Execution Layer tests."""

from __future__ import annotations

import json
import textwrap
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from orchestrator.agent_runner import AgentRunner
from orchestrator.loop_controller import LoopController, ValidatorProtocol
from orchestrator.task_queue import Task, TaskQueue
from reporters.failure_report import write_failure_report
from reporters.progress import write_progress

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_TASKS_MD = textwrap.dedent("""\
    # Tasks

    ## Phase 0

    - [ ] P0-01: Initialize pyproject.toml
    - [ ] P0-02: Create directory skeleton
    - [x] P0-03: Write smoke test
    - [~] P0-04: Set up CI
    - [!] P0-05: Configure secrets
""")


@pytest.fixture
def tmp_tasks(tmp_path: Path) -> tuple[Path, Path]:
    tasks_file = tmp_path / "tasks.md"
    status_file = tmp_path / "task_status.json"
    tasks_file.write_text(SAMPLE_TASKS_MD, encoding="utf-8")
    return tasks_file, status_file


# ---------------------------------------------------------------------------
# TaskQueue
# ---------------------------------------------------------------------------


def test_task_queue_parses_all_tasks(tmp_tasks: tuple[Path, Path]) -> None:
    tasks_file, status_file = tmp_tasks
    q = TaskQueue(tasks_file=tasks_file, status_file=status_file)
    assert len(q._tasks) == 5


def test_task_queue_status_markers(tmp_tasks: tuple[Path, Path]) -> None:
    tasks_file, status_file = tmp_tasks
    q = TaskQueue(tasks_file=tasks_file, status_file=status_file)
    statuses = {t.id: t.status for t in q._tasks}
    assert statuses["P0-01"] == "todo"
    assert statuses["P0-03"] == "done"
    assert statuses["P0-04"] == "in_progress"
    assert statuses["P0-05"] == "blocked"


def test_task_queue_next_returns_first_todo(tmp_tasks: tuple[Path, Path]) -> None:
    tasks_file, status_file = tmp_tasks
    q = TaskQueue(tasks_file=tasks_file, status_file=status_file)
    task = q.next()
    assert task is not None
    assert task.id == "P0-01"


def test_task_queue_next_returns_none_when_all_done(tmp_tasks: tuple[Path, Path]) -> None:
    tasks_file, status_file = tmp_tasks
    q = TaskQueue(tasks_file=tasks_file, status_file=status_file)
    for task in q._tasks:
        task.status = "done"
    assert q.next() is None


def test_task_queue_mark_done_persists(tmp_tasks: tuple[Path, Path]) -> None:
    tasks_file, status_file = tmp_tasks
    q = TaskQueue(tasks_file=tasks_file, status_file=status_file)
    q.mark_done("P0-01")

    # Reload from status file
    q2 = TaskQueue(tasks_file=tasks_file, status_file=status_file)
    statuses = {t.id: t.status for t in q2._tasks}
    assert statuses["P0-01"] == "done"


def test_task_queue_requeue_resets_to_todo(tmp_tasks: tuple[Path, Path]) -> None:
    tasks_file, status_file = tmp_tasks
    q = TaskQueue(tasks_file=tasks_file, status_file=status_file)
    q.mark_in_progress("P0-01")
    q.requeue("P0-01")
    statuses = {t.id: t.status for t in q._tasks}
    assert statuses["P0-01"] == "todo"


def test_task_queue_all_done_false(tmp_tasks: tuple[Path, Path]) -> None:
    tasks_file, status_file = tmp_tasks
    q = TaskQueue(tasks_file=tasks_file, status_file=status_file)
    assert q.all_done() is False


def test_task_queue_summary(tmp_tasks: tuple[Path, Path]) -> None:
    tasks_file, status_file = tmp_tasks
    q = TaskQueue(tasks_file=tasks_file, status_file=status_file)
    s = q.summary()
    assert s["todo"] == 2
    assert s["done"] == 1
    assert s["in_progress"] == 1
    assert s["blocked"] == 1


def test_task_queue_status_json_overrides_marker(tmp_tasks: tuple[Path, Path]) -> None:
    tasks_file, status_file = tmp_tasks
    status_file.write_text(json.dumps({
        "version": "0.1.0",
        "tasks": [{"id": "P0-01", "status": "done", "description": "..."}],
    }), encoding="utf-8")
    q = TaskQueue(tasks_file=tasks_file, status_file=status_file)
    statuses = {t.id: t.status for t in q._tasks}
    assert statuses["P0-01"] == "done"


# ---------------------------------------------------------------------------
# AgentRunner
# ---------------------------------------------------------------------------


def test_agent_runner_success(tmp_path: Path) -> None:
    runner = AgentRunner(workspace=tmp_path, agent_cmd=["echo"])
    task = Task(id="P0-01", description="test task", status="todo")
    result = runner.run(task)
    assert result.success is True
    assert result.returncode == 0
    assert result.task_id == "P0-01"


def test_agent_runner_failure(tmp_path: Path) -> None:
    runner = AgentRunner(workspace=tmp_path, agent_cmd=["false"])
    task = Task(id="P0-01", description="test task", status="todo")
    result = runner.run(task)
    assert result.success is False
    assert result.returncode != 0


def test_agent_runner_not_found(tmp_path: Path) -> None:
    runner = AgentRunner(workspace=tmp_path, agent_cmd=["__no_such_cmd__"])
    task = Task(id="P0-01", description="test task", status="todo")
    result = runner.run(task)
    assert result.success is False
    assert result.returncode == -1
    assert "not found" in result.stderr


def test_agent_runner_prompt_includes_failure_context(tmp_path: Path) -> None:
    runner = AgentRunner(workspace=tmp_path, agent_cmd=["echo"])
    task = Task(id="P0-01", description="test task", status="todo")
    prompt = runner._build_prompt(task, failure_context="[lint] E501 line too long")
    assert "Previous attempt failed" in prompt
    assert "E501 line too long" in prompt


def test_agent_runner_prompt_no_failure_context(tmp_path: Path) -> None:
    runner = AgentRunner(workspace=tmp_path, agent_cmd=["echo"])
    task = Task(id="P0-01", description="test task", status="todo")
    prompt = runner._build_prompt(task, failure_context=None)
    assert "Previous attempt" not in prompt


# ---------------------------------------------------------------------------
# Reporters
# ---------------------------------------------------------------------------


def test_failure_report_creates_md(tmp_path: Path) -> None:
    task = Task(id="P0-01", description="init project", status="todo")
    errors = {"lint": "E501 line too long in main.py", "tests": "2 failed"}
    ctx = write_failure_report(task, errors, tmp_path)
    md = (tmp_path / "last_failure.md").read_text()
    assert "P0-01" in md
    assert "E501" in md
    assert ctx != ""


def test_failure_report_appends_open_issues(tmp_path: Path) -> None:
    task = Task(id="P0-01", description="init project", status="todo")
    write_failure_report(task, {"lint": "error 1"}, tmp_path)
    write_failure_report(task, {"tests": "error 2"}, tmp_path)
    issues = json.loads((tmp_path / "open_issues.json").read_text())
    assert len(issues) == 2


def test_progress_report_creates_md(tmp_tasks: tuple[Path, Path]) -> None:
    tasks_file, status_file = tmp_tasks
    q = TaskQueue(tasks_file=tasks_file, status_file=status_file)
    storage = status_file.parent / "storage"
    write_progress(q, storage)
    md = (storage / "progress.md").read_text()
    assert "Progress" in md
    assert "todo" in md


# ---------------------------------------------------------------------------
# LoopController
# ---------------------------------------------------------------------------


def _make_passing_validator(name: str = "mock") -> ValidatorProtocol:
    v = ValidatorProtocol()
    v.name = name
    v.run = MagicMock(return_value=(True, ""))  # type: ignore[method-assign]
    return v


def _make_failing_validator(name: str, error: str) -> ValidatorProtocol:
    v = ValidatorProtocol()
    v.name = name
    v.run = MagicMock(return_value=(False, error))  # type: ignore[method-assign]
    return v


def test_loop_controller_all_done_on_success(
    tmp_path: Path, tmp_tasks: tuple[Path, Path]
) -> None:
    tasks_file, status_file = tmp_tasks
    q = TaskQueue(tasks_file=tasks_file, status_file=status_file)
    runner = AgentRunner(workspace=tmp_path, agent_cmd=["echo"])

    ctrl = LoopController(
        queue=q,
        runner=runner,
        validators=[_make_passing_validator()],
        storage_dir=tmp_path / "storage",
        workspace=tmp_path,
        auto_commit=False,
    )
    result = ctrl.run()
    # todo tasks (P0-01, P0-02) should be completed; in_progress/blocked are not processed
    assert result.stopped_reason == "all_done"
    assert len(result.completed_tasks) == 2
    assert q.next() is None  # no more todo tasks


def test_loop_controller_fails_and_requeues(
    tmp_path: Path, tmp_tasks: tuple[Path, Path]
) -> None:
    tasks_file, status_file = tmp_tasks
    q = TaskQueue(tasks_file=tasks_file, status_file=status_file)
    runner = AgentRunner(workspace=tmp_path, agent_cmd=["echo"])

    call_count = 0

    def _toggle(workspace: Path) -> tuple[bool, str]:
        nonlocal call_count
        call_count += 1
        # Fail first 2 calls per task, then pass
        return (call_count % 3 == 0, "mock error")

    v = _make_passing_validator()
    v.run = _toggle  # type: ignore[method-assign]

    ctrl = LoopController(
        queue=q,
        runner=runner,
        validators=[v],
        storage_dir=tmp_path / "storage",
        workspace=tmp_path,
        max_iterations=30,
        auto_commit=False,
    )
    result = ctrl.run()
    assert result.total_iterations > 0


def test_loop_controller_stops_on_max_iterations(
    tmp_path: Path, tmp_tasks: tuple[Path, Path]
) -> None:
    tasks_file, status_file = tmp_tasks
    q = TaskQueue(tasks_file=tasks_file, status_file=status_file)
    runner = AgentRunner(workspace=tmp_path, agent_cmd=["echo"])

    ctrl = LoopController(
        queue=q,
        runner=runner,
        validators=[_make_failing_validator("lint", "always fails")],
        storage_dir=tmp_path / "storage",
        workspace=tmp_path,
        max_iterations=3,
        auto_commit=False,
    )
    result = ctrl.run()
    assert result.total_iterations == 3
    assert result.stopped_reason == "max_iterations"


def test_loop_controller_stops_on_agent_unavailable(
    tmp_path: Path, tmp_tasks: tuple[Path, Path]
) -> None:
    tasks_file, status_file = tmp_tasks
    q = TaskQueue(tasks_file=tasks_file, status_file=status_file)
    runner = AgentRunner(workspace=tmp_path, agent_cmd=["__no_such_cmd__"])

    ctrl = LoopController(
        queue=q,
        runner=runner,
        validators=[_make_passing_validator()],
        storage_dir=tmp_path / "storage",
        workspace=tmp_path,
        auto_commit=False,
    )
    result = ctrl.run()
    assert result.stopped_reason == "agent_unavailable"
