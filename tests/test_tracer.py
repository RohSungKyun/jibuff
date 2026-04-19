"""Tests for structured tracing."""

from __future__ import annotations

import json
from pathlib import Path

from orchestrator.task_queue import Task
from reporters.tracer import write_trace


def _task() -> Task:
    return Task(id="P0-01", description="implement X", status="in_progress")


def test_write_trace_creates_jsonl(tmp_path: Path) -> None:
    trace_id = write_trace(
        _task(), success=True, duration_seconds=1.5,
        iteration=1, storage_dir=tmp_path,
    )
    assert len(trace_id) == 12

    traces_dir = tmp_path / "traces"
    assert traces_dir.exists()

    files = list(traces_dir.glob("*.jsonl"))
    assert len(files) == 1

    lines = files[0].read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["task_id"] == "P0-01"
    assert record["success"] is True
    assert record["duration_seconds"] == 1.5
    assert record["iteration"] == 1


def test_write_trace_appends_multiple(tmp_path: Path) -> None:
    write_trace(_task(), success=True, duration_seconds=1.0, iteration=1, storage_dir=tmp_path)
    write_trace(_task(), success=False, duration_seconds=2.0, iteration=2, storage_dir=tmp_path)

    files = list((tmp_path / "traces").glob("*.jsonl"))
    lines = files[0].read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2


def test_write_trace_includes_validator_errors(tmp_path: Path) -> None:
    write_trace(
        _task(), success=False, duration_seconds=0.5,
        validator_errors={"lint": "line too long", "mypy": "type error"},
        iteration=1, storage_dir=tmp_path,
    )
    files = list((tmp_path / "traces").glob("*.jsonl"))
    record = json.loads(files[0].read_text(encoding="utf-8").strip())
    assert "lint" in record["validator_errors"]
    assert "mypy" in record["validator_errors"]


def test_write_trace_includes_quality_score(tmp_path: Path) -> None:
    write_trace(
        _task(), success=False, duration_seconds=1.0,
        quality_score=0.55, quality_passed=False,
        iteration=1, storage_dir=tmp_path,
    )
    files = list((tmp_path / "traces").glob("*.jsonl"))
    record = json.loads(files[0].read_text(encoding="utf-8").strip())
    assert record["quality_score"] == 0.55
    assert record["quality_passed"] is False


def test_write_trace_includes_stopped_reason(tmp_path: Path) -> None:
    write_trace(
        _task(), success=False, duration_seconds=0.1,
        stopped_reason="agent_unavailable",
        iteration=1, storage_dir=tmp_path,
    )
    files = list((tmp_path / "traces").glob("*.jsonl"))
    record = json.loads(files[0].read_text(encoding="utf-8").strip())
    assert record["stopped_reason"] == "agent_unavailable"
