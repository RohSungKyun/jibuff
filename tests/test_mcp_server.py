"""Phase 6 — MCP Server tests (handlers only, no real MCP transport)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from jibuff_mcp.server import (
    TOOLS,
    handle_cancel,
    handle_interview,
    handle_run,
    handle_status,
)

# ---------------------------------------------------------------------------
# Tool registry
# ---------------------------------------------------------------------------


def test_tools_defined() -> None:
    names = {t["name"] for t in TOOLS}
    assert "jibuff_interview" in names
    assert "jibuff_run" in names
    assert "jibuff_status" in names
    assert "jibuff_cancel" in names


def test_all_tools_have_required_fields() -> None:
    for tool in TOOLS:
        assert "name" in tool
        assert "description" in tool
        assert "inputSchema" in tool


# ---------------------------------------------------------------------------
# handle_interview
# ---------------------------------------------------------------------------


def test_interview_requires_request() -> None:
    result = handle_interview({})
    assert "required" in result.lower() or "error" in result.lower()


def test_interview_unknown_mode() -> None:
    result = handle_interview({"request": "build x", "mode": "phaser"})
    assert "Error" in result


def _mock_engine(questions: list[str], complete: bool = False) -> MagicMock:
    """Return a mock InterviewEngine that yields the given questions."""
    session = MagicMock()
    session.complete = complete
    session.rounds = 1
    session.last_ambiguity = MagicMock(score=0.1)
    session.transcript = []

    engine = MagicMock()
    engine.start.return_value = session
    engine.step = AsyncMock(return_value=questions)
    engine.generate_tasks_md.return_value = "- [ ] P0-01: scaffold"
    return engine


def test_interview_quick_mode() -> None:
    engine = _mock_engine(["What is the target user?"])
    with patch("interview.engine.InterviewEngine", return_value=engine):
        result = handle_interview({"request": "build a task CLI", "mode": "quick"})
    assert "quick" in result
    assert "0.25" in result  # threshold


def test_interview_rtc_mode() -> None:
    engine = _mock_engine(["Latency budget?"])
    with patch("interview.engine.InterviewEngine", return_value=engine):
        result = handle_interview({"request": "build WebRTC app", "mode": "rtc"})
    assert "rtc" in result
    assert "0.15" in result


def test_interview_with_answer() -> None:
    engine = _mock_engine(["Next question?"])
    with patch("interview.engine.InterviewEngine", return_value=engine):
        result = handle_interview({
            "request": "build something",
            "answer": "admin users only",
        })
    # answer is recorded in transcript; result should show a question round
    assert "question" in result.lower() or "round" in result.lower()


# ---------------------------------------------------------------------------
# handle_run
# ---------------------------------------------------------------------------


def test_run_fails_without_tasks_file(tmp_path: Path) -> None:
    result = handle_run({"workspace": str(tmp_path)}, cwd=tmp_path)
    assert "Error" in result
    assert "tasks" in result.lower()


def test_run_dry_run_passes_with_tasks(tmp_path: Path) -> None:
    tasks = tmp_path / "spec" / "tasks.md"
    tasks.parent.mkdir(parents=True)
    tasks.write_text("- [ ] P0-01: test task\n", encoding="utf-8")
    result = handle_run({"workspace": str(tmp_path), "dry_run": True}, cwd=tmp_path)
    assert "Setup OK" in result
    assert "dry_run=true" in result


def test_run_unknown_mode(tmp_path: Path) -> None:
    result = handle_run({"mode": "phaser"}, cwd=tmp_path)
    assert "Error" in result


def test_run_uses_cwd_when_no_workspace(tmp_path: Path) -> None:
    tasks = tmp_path / "spec" / "tasks.md"
    tasks.parent.mkdir(parents=True)
    tasks.write_text("- [ ] P0-01: test\n", encoding="utf-8")
    result = handle_run({"dry_run": True}, cwd=tmp_path)
    assert "Setup OK" in result


# ---------------------------------------------------------------------------
# handle_status
# ---------------------------------------------------------------------------


def test_status_no_task_file(tmp_path: Path) -> None:
    result = handle_status({}, cwd=tmp_path)
    assert "No task status" in result


def test_status_with_tasks(tmp_path: Path) -> None:
    storage = tmp_path / "storage"
    storage.mkdir()
    status_data = {
        "version": "0.1.0",
        "tasks": [
            {"id": "P0-01", "status": "done", "description": "x", "updated_at": "now"},
            {"id": "P0-02", "status": "todo", "description": "y", "updated_at": "now"},
        ],
    }
    (storage / "task_status.json").write_text(
        json.dumps(status_data), encoding="utf-8"
    )
    result = handle_status({}, cwd=tmp_path)
    assert "done: 1" in result
    assert "todo: 1" in result


def test_status_shows_last_failure_presence(tmp_path: Path) -> None:
    storage = tmp_path / "storage"
    storage.mkdir()
    status_data = {"version": "0.1.0", "tasks": [
        {"id": "P0-01", "status": "todo", "description": "x", "updated_at": "now"}
    ]}
    (storage / "task_status.json").write_text(json.dumps(status_data), encoding="utf-8")
    (storage / "last_failure.md").write_text("# Failure\nerror", encoding="utf-8")
    result = handle_status({}, cwd=tmp_path)
    assert "last failure: present" in result


# ---------------------------------------------------------------------------
# handle_cancel
# ---------------------------------------------------------------------------


def test_cancel_creates_artifact(tmp_path: Path) -> None:
    result = handle_cancel({"reason": "test cancel"}, cwd=tmp_path)
    assert "cancelled" in result.lower()
    cancel_file = tmp_path / "storage" / "cancelled.md"
    assert cancel_file.exists()
    assert "test cancel" in cancel_file.read_text()


def test_cancel_updates_state_json(tmp_path: Path) -> None:
    storage = tmp_path / "storage"
    storage.mkdir()
    state = {"loop": {"status": "running", "mode": "quick", "current_task_id": None,
                      "iteration": 1, "started_at": None, "updated_at": None},
             "spec": {"seed_path": None, "locked": False}}
    (storage / "state.json").write_text(json.dumps(state), encoding="utf-8")

    handle_cancel({}, cwd=tmp_path)

    updated = json.loads((storage / "state.json").read_text())
    assert updated["loop"]["status"] == "cancelled"


def test_cancel_default_reason(tmp_path: Path) -> None:
    result = handle_cancel({}, cwd=tmp_path)
    assert "user requested" in result
