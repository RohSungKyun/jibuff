"""Tests for QualityEvaluator (ralph cycle)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

from evaluators.quality import _WEIGHTS, QualityEvaluator, QualityResult
from orchestrator.task_queue import Task

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_client(json_payload: dict[str, object]) -> MagicMock:
    """Return a mock Anthropic client that returns the given JSON as text."""
    content = MagicMock()
    content.text = json.dumps(json_payload)
    response = MagicMock()
    response.content = [content]
    client = MagicMock()
    client.messages.create.return_value = response
    return client


def _task(desc: str = "implement feature X") -> Task:
    return Task(id="P1-01", description=desc, status="in_progress")


# ---------------------------------------------------------------------------
# QualityResult
# ---------------------------------------------------------------------------


def test_quality_result_passed() -> None:
    r = QualityResult(
        score=0.8, spec_adherence=0.9, code_quality=0.8, edge_cases=0.6,
        feedback="looks good", passed=True, threshold=0.7,
    )
    assert r.passed is True


def test_quality_result_failed() -> None:
    r = QualityResult(
        score=0.5, spec_adherence=0.4, code_quality=0.6, edge_cases=0.5,
        feedback="missing error handling", passed=False, threshold=0.7,
    )
    assert r.passed is False
    ctx = r.context()
    assert "Quality gate failed" in ctx
    assert "missing error handling" in ctx
    assert "0.50" in ctx


def test_quality_result_context_contains_all_dimensions() -> None:
    r = QualityResult(
        score=0.55, spec_adherence=0.5, code_quality=0.6, edge_cases=0.5,
        feedback="needs work", passed=False, threshold=0.7,
    )
    ctx = r.context()
    assert "spec_adherence" in ctx
    assert "code_quality" in ctx
    assert "edge_cases" in ctx


# ---------------------------------------------------------------------------
# QualityEvaluator.evaluate — happy path
# ---------------------------------------------------------------------------


def test_evaluate_passes_above_threshold(tmp_path: Path) -> None:
    client = _make_client({
        "spec_adherence": 0.9,
        "code_quality": 0.8,
        "edge_cases": 0.7,
        "feedback": "looks good",
    })
    evaluator = QualityEvaluator(threshold=0.7, client=client)
    result = evaluator.evaluate(_task(), agent_output="def foo(): pass", workspace=tmp_path)

    assert result.passed is True
    assert result.score >= 0.7
    assert result.feedback == "looks good"


def test_evaluate_fails_below_threshold(tmp_path: Path) -> None:
    client = _make_client({
        "spec_adherence": 0.3,
        "code_quality": 0.4,
        "edge_cases": 0.2,
        "feedback": "barely implemented",
    })
    evaluator = QualityEvaluator(threshold=0.7, client=client)
    result = evaluator.evaluate(_task(), agent_output="pass", workspace=tmp_path)

    assert result.passed is False
    assert result.score < 0.7


def test_evaluate_score_uses_weights(tmp_path: Path) -> None:
    payload = {"spec_adherence": 1.0, "code_quality": 0.0, "edge_cases": 0.0, "feedback": "x"}
    client = _make_client(payload)
    evaluator = QualityEvaluator(threshold=0.0, client=client)
    result = evaluator.evaluate(_task(), agent_output="x", workspace=tmp_path)

    expected = _WEIGHTS["spec_adherence"] * 1.0
    assert abs(result.score - expected) < 0.01


# ---------------------------------------------------------------------------
# QualityEvaluator.evaluate — fallback on bad JSON
# ---------------------------------------------------------------------------


def test_evaluate_fallback_on_bad_json(tmp_path: Path) -> None:
    content = MagicMock()
    content.text = "not valid json at all"
    response = MagicMock()
    response.content = [content]
    client = MagicMock()
    client.messages.create.return_value = response

    evaluator = QualityEvaluator(threshold=0.7, client=client)
    result = evaluator.evaluate(_task(), agent_output="x", workspace=tmp_path)

    assert result.passed is False
    assert result.score == 0.0
    assert "parse" in result.feedback.lower() or "failed" in result.feedback.lower()


def test_evaluate_fallback_on_missing_keys(tmp_path: Path) -> None:
    client = _make_client({"spec_adherence": 0.8})  # missing other keys
    evaluator = QualityEvaluator(threshold=0.7, client=client)
    result = evaluator.evaluate(_task(), agent_output="x", workspace=tmp_path)

    assert result.passed is False


# ---------------------------------------------------------------------------
# LoopController integration — ralph cycle
# ---------------------------------------------------------------------------


def test_loop_controller_ralph_requeues_on_low_quality(tmp_path: Path) -> None:
    from unittest.mock import MagicMock, patch

    from orchestrator.agent_runner import AgentRunner, RunResult
    from orchestrator.loop_controller import LoopController
    from orchestrator.task_queue import TaskQueue

    tasks_file = tmp_path / "spec" / "tasks.md"
    tasks_file.parent.mkdir()
    tasks_file.write_text("- [ ] P0-01: implement X\n", encoding="utf-8")
    status_file = tmp_path / "storage" / "task_status.json"
    status_file.parent.mkdir()

    queue = TaskQueue(tasks_file=tasks_file, status_file=status_file)

    low_quality = QualityResult(
        score=0.3, spec_adherence=0.3, code_quality=0.3, edge_cases=0.3,
        feedback="poor", passed=False, threshold=0.7,
    )
    mock_evaluator = MagicMock()
    mock_evaluator.evaluate.return_value = low_quality

    mock_runner = MagicMock(spec=AgentRunner)
    mock_runner.run.return_value = RunResult(
        task_id="P0-01", success=True, stdout="output", stderr="",
        returncode=0, duration_seconds=1.0,
    )

    controller = LoopController(
        queue=queue,
        runner=mock_runner,
        validators=[],
        storage_dir=tmp_path / "storage",
        workspace=tmp_path,
        max_iterations=10,
        auto_commit=False,
        quality_evaluator=mock_evaluator,
        max_quality_retries=2,
    )

    with patch("evaluators.quality.QualityEvaluator", return_value=mock_evaluator):
        result = controller.run()

    # task requeued each time quality fails; stops at max_quality_retries
    assert len(result.failed_tasks) > 0
    assert mock_evaluator.evaluate.call_count == 2  # max_quality_retries


def test_loop_controller_no_ralph_in_quick_mode(tmp_path: Path) -> None:
    """quality_evaluator=None means ralph cycle is skipped entirely."""
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
        task_id="P0-01", success=True, stdout="", stderr="",
        returncode=0, duration_seconds=1.0,
    )

    controller = LoopController(
        queue=queue,
        runner=mock_runner,
        validators=[],
        storage_dir=tmp_path / "storage",
        workspace=tmp_path,
        auto_commit=False,
        quality_evaluator=None,  # quick mode — no ralph
    )
    result = controller.run()

    assert result.stopped_reason == "all_done"
    assert "P0-01" in result.completed_tasks
