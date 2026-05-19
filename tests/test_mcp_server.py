"""Phase 6 — MCP Server tests (handlers only, no real MCP transport)."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from interview.engine import InterviewSession, QuestionBlock
from jibuff_mcp.server import (
    TOOLS,
    handle_cancel,
    handle_finish_task,
    handle_interview,
    handle_next_task,
    handle_run,
    handle_status,
)
from orchestrator.runtime_store import RuntimeStore

# ---------------------------------------------------------------------------
# Tool registry
# ---------------------------------------------------------------------------


def test_tools_defined() -> None:
    names = {t["name"] for t in TOOLS}
    assert "jibuff_interview" in names
    assert "jibuff_run" in names
    assert "jibuff_next_task" in names
    assert "jibuff_finish_task" in names
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
    result = asyncio.run(handle_interview({}))
    assert "required" in result.lower() or "error" in result.lower()


def test_interview_unknown_mode() -> None:
    result = asyncio.run(handle_interview({"request": "build x", "mode": "phaser"}))
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
        result = asyncio.run(
            handle_interview({"request": "build a task CLI", "mode": "quick"})
        )
    assert "quick" in result
    assert "0.25" in result  # threshold


def test_interview_rtc_mode() -> None:
    engine = _mock_engine(["Latency budget?"])
    with patch("interview.engine.InterviewEngine", return_value=engine):
        result = asyncio.run(
            handle_interview({"request": "build WebRTC app", "mode": "rtc"})
        )
    assert "rtc" in result
    assert "0.15" in result


def test_interview_answer_requires_session_id() -> None:
    result = asyncio.run(handle_interview({
        "request": "build something",
        "answer": "admin users only",
    }))
    assert "session_id" in result
    assert "required" in result.lower()


def test_interview_rejects_invalid_session_id(tmp_path: Path) -> None:
    result = asyncio.run(handle_interview({
        "session_id": "../bad",
        "revision": 1,
        "answer": "b",
        "workspace": str(tmp_path),
    }))
    assert "invalid 'session_id'" in result


def test_interview_cleans_broken_expired_session_file(tmp_path: Path) -> None:
    sessions_dir = tmp_path / ".jibuff" / "mcp" / "interviews"
    sessions_dir.mkdir(parents=True)
    broken = sessions_dir / "broken.md"
    orphan_lock = sessions_dir / "orphan.lock"
    broken.write_text("not a jibuff session\n", encoding="utf-8")
    orphan_lock.write_text("", encoding="utf-8")
    old = time.time() - (25 * 60 * 60)
    os.utime(broken, (old, old))

    result = asyncio.run(handle_interview({"workspace": str(tmp_path)}))

    assert "required" in result.lower()
    assert not broken.exists()
    assert not orphan_lock.exists()


def _response_value(response: str, key: str) -> str:
    prefix = f"{key}: "
    return next(
        line.removeprefix(prefix)
        for line in response.splitlines()
        if line.startswith(prefix)
    )


def test_interview_mcp_session_file_roundtrip(tmp_path: Path) -> None:
    class FakeEngine:
        def __init__(self, mode: str = "quick") -> None:
            self.mode = mode

        def start(self, request: str) -> InterviewSession:
            from orchestrator.config import get_mode

            return InterviewSession(mode=get_mode(self.mode), original_request=request)

        async def step(
            self,
            session: InterviewSession,
            user_answer: str | None = None,
        ) -> list[str]:
            if user_answer and session.pending_question:
                session.transcript.append({
                    "role": "user",
                    "content": session.pending_question.resolve_answer(user_answer) or "",
                })
                session.pending_question = None

            session.pending_question = QuestionBlock.from_text(
                "Who is the user?\n"
                "a) Admin\n"
                "b) Guest\n"
                "c) Operator\n"
                "직접 입력: custom"
            )
            session.transcript.append({
                "role": "assistant",
                "content": session.pending_question.render(),
            })
            session.rounds += 1
            return [session.pending_question.render()]

        def generate_tasks_md(self, session: InterviewSession) -> str:
            return "- [ ] P0-01: scaffold"

    with patch("interview.engine.InterviewEngine", FakeEngine):
        first = asyncio.run(handle_interview({
            "request": "build a task CLI",
            "workspace": str(tmp_path),
        }))

    session_id = _response_value(first, "session_id")
    revision = int(_response_value(first, "revision"))
    state_file = tmp_path / ".jibuff" / "mcp" / "interviews" / f"{session_id}.md"
    assert state_file.exists()

    with patch("interview.engine.InterviewEngine", FakeEngine):
        conflict = asyncio.run(handle_interview({
            "session_id": session_id,
            "revision": revision + 99,
            "answer": "b",
            "workspace": str(tmp_path),
        }))
    assert "revision conflict" in conflict

    missing_revision = asyncio.run(handle_interview({
        "session_id": session_id,
        "answer": "b",
        "workspace": str(tmp_path),
    }))
    assert "'revision' is required" in missing_revision

    invalid_revision = asyncio.run(handle_interview({
        "session_id": session_id,
        "revision": "latest",
        "answer": "b",
        "workspace": str(tmp_path),
    }))
    assert "'revision' must be an integer" in invalid_revision

    with patch("interview.engine.InterviewEngine", FakeEngine):
        second = asyncio.run(handle_interview({
            "session_id": session_id,
            "revision": revision,
            "answer": "b",
            "workspace": str(tmp_path),
        }))
    assert _response_value(second, "revision") == "2"
    assert state_file.exists()

    cancelled = asyncio.run(handle_interview({
        "session_id": session_id,
        "action": "cancel",
        "workspace": str(tmp_path),
    }))
    assert "cancelled" in cancelled
    assert not state_file.exists()


def test_interview_mcp_json_response_and_structured_answer(tmp_path: Path) -> None:
    class FakeEngine:
        def __init__(self, mode: str = "quick") -> None:
            self.mode = mode

        def start(self, request: str) -> InterviewSession:
            from orchestrator.config import get_mode

            return InterviewSession(mode=get_mode(self.mode), original_request=request)

        async def step(
            self,
            session: InterviewSession,
            user_answer: str | None = None,
        ) -> list[str]:
            if user_answer and session.pending_question:
                session.transcript.append({
                    "role": "user",
                    "content": session.pending_question.resolve_answer(user_answer) or "",
                })
                session.pending_question = None

            session.pending_question = QuestionBlock.from_text(
                "Who is the user?\n"
                "a) Admin\n"
                "b) Guest\n"
                "c) Operator\n"
                "직접 입력: custom"
            )
            session.transcript.append({
                "role": "assistant",
                "content": session.pending_question.render(),
            })
            session.rounds += 1
            return [session.pending_question.render()]

        def generate_tasks_md(self, session: InterviewSession) -> str:
            return "- [ ] P0-01: scaffold"

    with patch("interview.engine.InterviewEngine", FakeEngine):
        first = asyncio.run(handle_interview({
            "request": "build a task CLI",
            "workspace": str(tmp_path),
            "response_format": "json",
        }))

    payload = json.loads(first)
    assert payload["kind"] == "jibuff.interview.pending"
    assert payload["status"] == "active"
    assert payload["question"]["type"] == "single-answerable"
    assert payload["question"]["allow_other"] is True
    assert payload["question"]["options"][1] == {
        "label": "Guest",
        "value": "b",
        "description": "Guest",
    }
    assert payload["question"]["fallback_text"].startswith("Who is the user?")

    with patch("interview.engine.InterviewEngine", FakeEngine):
        second = asyncio.run(handle_interview({
            "session_id": payload["session_id"],
            "revision": payload["revision"],
            "answer": {"value": "b"},
            "workspace": str(tmp_path),
            "response_format": "json",
        }))

    continued = json.loads(second)
    assert continued["revision"] == 2

    state_file = Path(continued["state_file"])
    state = json.loads(
        state_file.read_text(encoding="utf-8").split("```json jibuff-session\n", 1)[1]
        .split("\n```", 1)[0]
    )
    assert {"role": "user", "content": "Selected b: Guest"} in state["transcript"]


def test_interview_rejects_unknown_response_format(tmp_path: Path) -> None:
    result = asyncio.run(handle_interview({
        "request": "build a task CLI",
        "workspace": str(tmp_path),
        "response_format": "yaml",
    }))
    assert "'response_format'" in result
    assert "text" in result
    assert "json" in result


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


def test_run_dry_run_json(tmp_path: Path) -> None:
    tasks = tmp_path / "spec" / "tasks.md"
    tasks.parent.mkdir(parents=True)
    tasks.write_text("- [ ] P0-01: test task\n", encoding="utf-8")
    result = json.loads(handle_run(
        {"workspace": str(tmp_path), "dry_run": True, "response_format": "json"},
        cwd=tmp_path,
    ))
    assert result["kind"] == "jibuff.run.dry_run"
    assert result["status"] == "ok"


def test_run_unknown_mode(tmp_path: Path) -> None:
    result = handle_run({"mode": "phaser"}, cwd=tmp_path)
    assert "Error" in result


def test_run_uses_cwd_when_no_workspace(tmp_path: Path) -> None:
    tasks = tmp_path / "spec" / "tasks.md"
    tasks.parent.mkdir(parents=True)
    tasks.write_text("- [ ] P0-01: test\n", encoding="utf-8")
    result = handle_run({"dry_run": True}, cwd=tmp_path)
    assert "Setup OK" in result


def test_run_initializes_runtime_store(tmp_path: Path) -> None:
    tasks = tmp_path / "spec" / "tasks.md"
    tasks.parent.mkdir(parents=True)
    tasks.write_text("- [ ] P0-01: test task\n", encoding="utf-8")
    result = handle_run({"workspace": str(tmp_path), "response_format": "json"}, cwd=tmp_path)
    payload = json.loads(result)
    assert payload["kind"] == "jibuff.run.started"
    assert payload["status"] == "started"
    assert payload["run_id"] is not None
    assert "jibuff_next_task" in payload["next_guide"]
    runtime = RuntimeStore.active(tmp_path)
    assert runtime is not None
    assert runtime.run_id == payload["run_id"]


def test_run_returns_all_done_when_tasks_complete(tmp_path: Path) -> None:
    tasks = tmp_path / "spec" / "tasks.md"
    tasks.parent.mkdir(parents=True)
    tasks.write_text("- [x] P0-01: already done\n", encoding="utf-8")
    result = json.loads(handle_run(
        {"workspace": str(tmp_path), "response_format": "json"}, cwd=tmp_path
    ))
    assert result["status"] == "all_done"
    assert result["run_id"] is None


def test_run_text_includes_guide(tmp_path: Path) -> None:
    tasks = tmp_path / "spec" / "tasks.md"
    tasks.parent.mkdir(parents=True)
    tasks.write_text("- [ ] P0-01: test task\n", encoding="utf-8")
    result = handle_run({"workspace": str(tmp_path)}, cwd=tmp_path)
    assert "jibuff_next_task" in result
    assert "jibuff_finish_task" in result


# ---------------------------------------------------------------------------
# in-session task execution
# ---------------------------------------------------------------------------


def test_next_task_claims_task_for_in_session_agent(tmp_path: Path) -> None:
    tasks = tmp_path / "spec" / "tasks.md"
    tasks.parent.mkdir(parents=True)
    tasks.write_text("- [ ] P0-01: test task\n", encoding="utf-8")

    result = handle_next_task({
        "workspace": str(tmp_path),
        "worker_id": "codex-session",
        "response_format": "json",
    }, cwd=tmp_path)

    payload = json.loads(result)
    assert payload["kind"] == "jibuff.in_session.task"
    assert payload["status"] == "claimed"
    assert payload["task"]["id"] == "P0-01"
    assert payload["task"]["claimed_by"] == "codex-session"
    assert payload["claim_token"]
    assert "jibuff_finish_task" in payload["next_guide"]
    runtime = RuntimeStore.active(tmp_path)
    assert runtime is not None
    task_state = json.loads(runtime.task_path("P0-01").read_text(encoding="utf-8"))
    assert task_state["status"] == "in_progress"
    assert task_state["claim_token"] == payload["claim_token"]


def test_next_task_does_not_duplicate_claim_active_task(tmp_path: Path) -> None:
    tasks = tmp_path / "spec" / "tasks.md"
    tasks.parent.mkdir(parents=True)
    tasks.write_text("- [ ] P0-01: test task\n", encoding="utf-8")

    first = json.loads(handle_next_task({
        "workspace": str(tmp_path),
        "worker_id": "codex-session-1",
        "response_format": "json",
    }, cwd=tmp_path))
    second = json.loads(handle_next_task({
        "workspace": str(tmp_path),
        "worker_id": "codex-session-2",
        "response_format": "json",
    }, cwd=tmp_path))

    assert first["status"] == "claimed"
    assert second["status"] == "empty"
    status = json.loads((tmp_path / "storage" / "task_status.json").read_text())
    assert status["tasks"][0]["claimed_by"] == "codex-session-1"


def test_next_task_reports_completion_guide_when_all_done(tmp_path: Path) -> None:
    tasks = tmp_path / "spec" / "tasks.md"
    tasks.parent.mkdir(parents=True)
    tasks.write_text("- [x] P0-01: done task\n", encoding="utf-8")

    result = handle_next_task({
        "workspace": str(tmp_path),
        "response_format": "json",
    }, cwd=tmp_path)

    payload = json.loads(result)
    assert payload["kind"] == "jibuff.in_session.empty"
    assert payload["status"] == "empty"
    assert "All tasks are complete" in payload["next_guide"]


def test_finish_task_marks_done_without_external_agent(tmp_path: Path) -> None:
    tasks = tmp_path / "spec" / "tasks.md"
    tasks.parent.mkdir(parents=True)
    tasks.write_text("- [ ] P0-01: test task\n", encoding="utf-8")
    claimed = json.loads(handle_next_task({
        "workspace": str(tmp_path),
        "response_format": "json",
    }, cwd=tmp_path))

    result = handle_finish_task({
        "workspace": str(tmp_path),
        "task_id": "P0-01",
        "claim_token": claimed["claim_token"],
        "validate": False,
        "response_format": "json",
    }, cwd=tmp_path)

    payload = json.loads(result)
    assert payload["kind"] == "jibuff.in_session.finish"
    assert payload["status"] == "passed"
    assert payload["task"]["status"] == "done"
    assert payload["all_done"] is True
    assert "All tasks are complete" in payload["next_guide"]
    assert RuntimeStore.active(tmp_path) is None

    status = json.loads((tmp_path / "storage" / "task_status.json").read_text())
    assert status["tasks"][0]["status"] == "done"


def test_finish_task_requeues_on_validator_failure(tmp_path: Path) -> None:
    tasks = tmp_path / "spec" / "tasks.md"
    tasks.parent.mkdir(parents=True)
    tasks.write_text("- [ ] P0-01: test task\n", encoding="utf-8")
    claimed = json.loads(handle_next_task({
        "workspace": str(tmp_path),
        "response_format": "json",
    }, cwd=tmp_path))

    with patch("jibuff_mcp.server._run_validator_stack", return_value={"tests": "boom"}):
        result = handle_finish_task({
            "workspace": str(tmp_path),
            "task_id": "P0-01",
            "claim_token": claimed["claim_token"],
            "response_format": "json",
        }, cwd=tmp_path)

    payload = json.loads(result)
    assert payload["status"] == "failed"
    assert payload["task"]["status"] == "todo"
    assert payload["validator_errors"] == {"tests": "boom"}
    assert "requeued" in payload["next_guide"]
    assert (tmp_path / "storage" / "last_failure.md").exists()

    status = json.loads((tmp_path / "storage" / "task_status.json").read_text())
    assert status["tasks"][0]["status"] == "todo"


def test_finish_task_rejects_stale_claim_before_validators(tmp_path: Path) -> None:
    tasks = tmp_path / "spec" / "tasks.md"
    tasks.parent.mkdir(parents=True)
    tasks.write_text("- [ ] P0-01: test task\n", encoding="utf-8")
    claimed = json.loads(handle_next_task({
        "workspace": str(tmp_path),
        "response_format": "json",
    }, cwd=tmp_path))

    with patch("jibuff_mcp.server._run_validator_stack") as validators:
        result = handle_finish_task({
            "workspace": str(tmp_path),
            "task_id": "P0-01",
            "claim_token": claimed["claim_token"] + "-stale",
            "response_format": "json",
        }, cwd=tmp_path)

    validators.assert_not_called()
    assert "stale runtime claim token" in result
    assert not (tmp_path / "storage" / "last_failure.md").exists()

    status = json.loads((tmp_path / "storage" / "task_status.json").read_text())
    assert status["tasks"][0]["status"] == "in_progress"


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


# ---------------------------------------------------------------------------
# _watch_parent — orphan detection
# ---------------------------------------------------------------------------


class _FakeExit(BaseException):
    """Stand-in for os._exit so tests can observe termination."""


def test_watch_parent_exits_when_reparented() -> None:
    """If getppid() differs from initial value, _watch_parent calls os._exit(0)."""
    from jibuff_mcp import server

    ppids = iter([1234, 9999])  # initial → reparented

    async def instant_sleep(_seconds: float) -> None:
        return

    def fake_exit(code: int) -> None:
        raise _FakeExit(code)

    with (
        patch("jibuff_mcp.server.os.getppid", side_effect=lambda: next(ppids)),
        patch("jibuff_mcp.server.os._exit", side_effect=fake_exit),
        patch("jibuff_mcp.server.asyncio.sleep", new=instant_sleep),
    ):
        try:
            asyncio.run(server._watch_parent())
        except _FakeExit as e:
            assert e.args == (0,)
        else:
            raise AssertionError("_watch_parent did not exit")


def test_watch_parent_continues_when_ppid_stable() -> None:
    """Watcher loops without exiting while getppid() stays constant."""
    from jibuff_mcp import server

    sleep_count = 0

    async def counting_sleep(_seconds: float) -> None:
        nonlocal sleep_count
        sleep_count += 1
        if sleep_count >= 3:
            raise asyncio.CancelledError
        return

    def fail_exit(_code: int) -> None:
        raise AssertionError("os._exit should not be called when PPID is stable")

    with (
        patch("jibuff_mcp.server.os.getppid", return_value=4321),
        patch("jibuff_mcp.server.os._exit", side_effect=fail_exit),
        patch("jibuff_mcp.server.asyncio.sleep", new=counting_sleep),
        contextlib.suppress(asyncio.CancelledError),
    ):
        asyncio.run(server._watch_parent())

    assert sleep_count == 3
