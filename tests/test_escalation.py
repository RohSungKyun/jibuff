"""Tests for issue escalation."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from orchestrator.task_queue import Task
from reporters import escalation
from reporters.escalation import build_issue_body, create_github_issue, prompt_escalation


@pytest.fixture(autouse=True)
def _reset_gh_cache() -> None:
    escalation._check_gh_ready.cache_clear()


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


# ---------------------------------------------------------------------------
# _check_gh_ready — cached gh auth detection
# ---------------------------------------------------------------------------


def _ok() -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")


def _fail() -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="not logged in")


def test_check_gh_ready_returns_true_when_authenticated() -> None:
    with patch("reporters.escalation.subprocess.run", return_value=_ok()) as mock_run:
        assert escalation._check_gh_ready() is True
        # Cached on second call.
        assert escalation._check_gh_ready() is True
        assert mock_run.call_count == 1


def test_check_gh_ready_returns_false_when_not_authenticated() -> None:
    with patch("reporters.escalation.subprocess.run", return_value=_fail()):
        assert escalation._check_gh_ready() is False


def test_check_gh_ready_handles_missing_gh_cli() -> None:
    with patch("reporters.escalation.subprocess.run", side_effect=FileNotFoundError()):
        assert escalation._check_gh_ready() is False


def test_prompt_escalation_skips_when_gh_not_ready(tmp_path: Path) -> None:
    """When gh is not authenticated, prompt_escalation must not call typer.prompt."""
    with (
        patch("reporters.escalation.subprocess.run", return_value=_fail()),
        patch("reporters.escalation.typer.prompt") as mock_prompt,
    ):
        url = prompt_escalation(_task(), 3, {"lint": "E501"}, tmp_path)
    assert url is None
    assert not mock_prompt.called


def test_prompt_escalation_proceeds_when_gh_ready(tmp_path: Path) -> None:
    """When gh is authenticated, prompt_escalation should ask the user."""
    with (
        patch("reporters.escalation.subprocess.run", return_value=_ok()),
        patch("reporters.escalation.typer.prompt", return_value="n") as mock_prompt,
    ):
        prompt_escalation(_task(), 3, {"lint": "E501"}, tmp_path)
    assert mock_prompt.called
