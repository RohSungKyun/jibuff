"""Tests for issue escalation."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from orchestrator.task_queue import Task
from reporters.escalation import build_issue_body, create_github_issue


def _task() -> Task:
    return Task(id="P1-01", description="add WebRTC screen sharing", status="in_progress")


# ---------------------------------------------------------------------------
# build_issue_body
# ---------------------------------------------------------------------------


def test_build_issue_body_contains_task_info() -> None:
    body = build_issue_body(
        _task(), failure_count=3,
        last_errors={"lint": "E501 line too long"},
    )
    assert "P1-01" in body
    assert "WebRTC screen sharing" in body
    assert "3" in body
    assert "E501" in body
    assert "jibuff" in body


def test_build_issue_body_truncates_long_errors() -> None:
    body = build_issue_body(
        _task(), failure_count=5,
        last_errors={"tests": "x" * 1000},
    )
    # error text should be capped at 500 chars
    assert len(body) < 1200


def test_build_issue_body_multiple_gates() -> None:
    body = build_issue_body(
        _task(), failure_count=3,
        last_errors={"lint": "ruff error", "mypy": "type error", "tests": "1 failed"},
    )
    assert "lint" in body
    assert "mypy" in body
    assert "tests" in body


# ---------------------------------------------------------------------------
# create_github_issue
# ---------------------------------------------------------------------------


def test_create_github_issue_success(tmp_path: Path) -> None:
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = "https://github.com/owner/repo/issues/42"

    with patch("reporters.escalation.subprocess.run", return_value=mock_result) as mock_run:
        url = create_github_issue(
            _task(), failure_count=3,
            last_errors={"lint": "error"},
            workspace=tmp_path,
            labels=["jibuff", "bug"],
        )

    assert url == "https://github.com/owner/repo/issues/42"
    cmd = mock_run.call_args[0][0]
    assert "gh" in cmd
    assert "issue" in cmd
    assert "create" in cmd
    assert "--label" in cmd


def test_create_github_issue_failure(tmp_path: Path) -> None:
    mock_result = MagicMock()
    mock_result.returncode = 1

    with patch("reporters.escalation.subprocess.run", return_value=mock_result):
        url = create_github_issue(
            _task(), failure_count=3,
            last_errors={"lint": "error"},
            workspace=tmp_path,
        )

    assert url is None


def test_create_github_issue_gh_not_found(tmp_path: Path) -> None:
    with patch(
        "reporters.escalation.subprocess.run",
        side_effect=FileNotFoundError("gh not found"),
    ):
        url = create_github_issue(
            _task(), failure_count=3,
            last_errors={"lint": "error"},
            workspace=tmp_path,
        )

    assert url is None


# ---------------------------------------------------------------------------
# LoopController integration — escalation
# ---------------------------------------------------------------------------


def test_loop_controller_escalates_after_threshold(tmp_path: Path) -> None:
    from orchestrator.agent_runner import AgentRunner, RunResult
    from orchestrator.loop_controller import LoopController
    from orchestrator.task_queue import TaskQueue

    tasks_file = tmp_path / "spec" / "tasks.md"
    tasks_file.parent.mkdir()
    tasks_file.write_text("- [ ] P0-01: implement X\n", encoding="utf-8")
    status_file = tmp_path / "storage" / "task_status.json"
    status_file.parent.mkdir()

    queue = TaskQueue(tasks_file=tasks_file, status_file=status_file)

    mock_runner = MagicMock(spec=AgentRunner)
    mock_runner.run.return_value = RunResult(
        task_id="P0-01", success=False, stdout="", stderr="build error",
        returncode=1, duration_seconds=1.0,
    )

    mock_escalation = MagicMock(return_value="https://github.com/issues/1")

    controller = LoopController(
        queue=queue,
        runner=mock_runner,
        validators=[],
        storage_dir=tmp_path / "storage",
        workspace=tmp_path,
        max_iterations=5,
        auto_commit=False,
        escalation_handler=mock_escalation,
        escalation_threshold=3,
    )

    result = controller.run()

    assert mock_escalation.called
    assert len(result.escalated_issues) > 0
    assert "github.com" in result.escalated_issues[0]


def test_loop_controller_no_escalation_below_threshold(tmp_path: Path) -> None:
    from orchestrator.agent_runner import AgentRunner, RunResult
    from orchestrator.loop_controller import LoopController
    from orchestrator.task_queue import TaskQueue

    tasks_file = tmp_path / "spec" / "tasks.md"
    tasks_file.parent.mkdir()
    tasks_file.write_text("- [ ] P0-01: implement X\n", encoding="utf-8")
    status_file = tmp_path / "storage" / "task_status.json"
    status_file.parent.mkdir()

    queue = TaskQueue(tasks_file=tasks_file, status_file=status_file)

    mock_runner = MagicMock(spec=AgentRunner)
    mock_runner.run.return_value = RunResult(
        task_id="P0-01", success=False, stdout="", stderr="error",
        returncode=1, duration_seconds=1.0,
    )

    mock_escalation = MagicMock(return_value=None)

    controller = LoopController(
        queue=queue,
        runner=mock_runner,
        validators=[],
        storage_dir=tmp_path / "storage",
        workspace=tmp_path,
        max_iterations=2,  # only 2 iterations, threshold is 3
        auto_commit=False,
        escalation_handler=mock_escalation,
        escalation_threshold=3,
    )

    controller.run()
    assert not mock_escalation.called
