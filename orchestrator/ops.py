from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from orchestrator.agent_runner import resolve_agent_cmd
from orchestrator.runtime_store import DEFAULT_STALE_AFTER_MINUTES, RuntimeStore
from orchestrator.task_queue import TaskQueue

MCP_INTERVIEW_TTL_HOURS = 24


JIBUFF_SKILL_MD = """---
name: jibuff
description: >
  Use this skill when a coding task needs requirement clarification, spec locking,
  or validation-driven execution before handing work to an agent.
---

# jibuff

Use this skill when a coding task needs requirement clarification, spec locking, or
validation-driven execution before handing work to an agent.

## Commands

- `jb interview "<request>"`: clarify requirements and write `spec/tasks.md`.
- `jb interview "<request>" --mode rtc`: use stricter RTC/WebRTC clarification.
- `jb run`: execute the locked tasks through the configured agent CLI.
- `jb status`: show current task counts.
- `jb inspect`: inspect task state, failure artifacts, and MCP interview sessions.
- `jb doctor`: verify local jibuff readiness.

## MCP structured interviews

When MCP tools are available, prefer `jibuff_interview` with
`response_format="json"` for in-session interviews. JSON responses include a
transport-neutral `jibuff.interview.question` payload with three selectable
options, `allow_other=true`, and `fallback_text` for clients that cannot render
a structured question UI. Continue sessions with `session_id`, `revision`, and
either a legacy string answer (`"a"`, `"b"`, custom text) or a structured answer
such as `{"value": "a"}`.

## Workflow

1. Run `jb interview` when the request is ambiguous.
2. Review `spec/tasks.md` before execution if scope is sensitive.
3. Run `jb run` after the spec is acceptable.
4. Use `jb inspect` or `jb recover` if the session is interrupted.
"""


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str
    required: bool = True


@dataclass
class InspectResult:
    workspace: Path
    has_tasks: bool
    summary: dict[str, int] = field(default_factory=dict)
    tasks: list[dict[str, object]] = field(default_factory=list)
    last_failure: bool = False
    open_issue_count: int = 0
    interview_sessions: list[dict[str, object]] = field(default_factory=list)
    runtime_run: dict[str, object] | None = None


def run_doctor(workspace: Path) -> list[CheckResult]:
    checks: list[CheckResult] = []

    try:
        agent_cmd = resolve_agent_cmd()
        checks.append(CheckResult("agent_cli", True, " ".join(agent_cmd)))
    except RuntimeError as exc:
        checks.append(CheckResult("agent_cli", False, str(exc)))

    checks.append(
        CheckResult(
            "spec_tasks",
            (workspace / "spec" / "tasks.md").exists(),
            str(workspace / "spec" / "tasks.md"),
            required=False,
        )
    )
    checks.append(
        CheckResult(
            "storage_dir",
            (workspace / "storage").exists(),
            str(workspace / "storage"),
            required=False,
        )
    )

    try:
        import mcp  # noqa: F401

        checks.append(CheckResult("mcp_extra", True, "mcp package importable"))
    except ImportError:
        checks.append(
            CheckResult(
                "mcp_extra",
                False,
                "install with: pip install 'jibuff[mcp]'",
                required=False,
            )
        )

    for command in ("git", "python"):
        path = shutil.which(command)
        checks.append(CheckResult(command, path is not None, path or "not found on PATH"))

    return checks


def inspect_workspace(workspace: Path) -> InspectResult:
    tasks_file = workspace / "spec" / "tasks.md"
    status_file = workspace / "storage" / "task_status.json"
    result = InspectResult(workspace=workspace, has_tasks=tasks_file.exists())

    if tasks_file.exists():
        queue = TaskQueue(tasks_file=tasks_file, status_file=status_file)
        result.summary = queue.summary()
        result.tasks = [
            {
                "id": task.id,
                "status": task.status,
                "revision": task.revision,
                "claimed_by": task.claimed_by,
                "claimed_at": task.claimed_at,
                "claim_token": task.claim_token,
                "heartbeat_at": task.heartbeat_at,
                "description": task.description,
            }
            for task in queue._tasks
        ]

    storage_dir = workspace / "storage"
    result.last_failure = (storage_dir / "last_failure.md").exists()
    result.open_issue_count = _json_list_len(storage_dir / "open_issues.json")
    result.interview_sessions = list_interview_sessions(workspace)
    runtime_store = RuntimeStore.active(
        workspace,
        running_only=False,
    ) or RuntimeStore.latest(workspace)
    if runtime_store is not None:
        result.runtime_run = runtime_store.inspect()
    return result


def cleanup_workspace(workspace: Path, *, include_storage_failures: bool = False) -> list[Path]:
    removed: list[Path] = []
    removed.extend(cleanup_interview_sessions(workspace))

    if include_storage_failures:
        for path in (
            workspace / "storage" / "last_failure.md",
            workspace / "storage" / "open_issues.json",
        ):
            if path.exists():
                path.unlink()
                removed.append(path)

    return removed


def recover_workspace(
    workspace: Path,
    *,
    stale_after_minutes: int = DEFAULT_STALE_AFTER_MINUTES,
    force: bool = False,
) -> list[str]:
    tasks_file = workspace / "spec" / "tasks.md"
    status_file = workspace / "storage" / "task_status.json"
    actions: list[str] = []
    if not tasks_file.exists():
        return ["No spec/tasks.md found."]

    runtime_store = RuntimeStore.active(workspace, running_only=True)
    if runtime_store is None:
        latest = RuntimeStore.latest(workspace)
        if latest is not None and _runtime_has_in_progress_tasks(latest):
            runtime_store = latest
    if runtime_store is not None:
        report = runtime_store.recover_stale(
            stale_after_minutes=stale_after_minutes,
            force=force,
        )
        if report.requeued:
            queue = TaskQueue(tasks_file=tasks_file, status_file=status_file)
            for task_id in report.requeued:
                try:
                    queue.requeue(task_id)
                except OSError as exc:
                    actions.append(
                        f"Requeued stale in-progress task {task_id}, "
                        f"but legacy task_status mirror update failed: {exc}"
                    )
                else:
                    actions.append(f"Requeued stale in-progress task {task_id}.")
        for task_id in report.skipped:
            actions.append(
                f"Skipped active/recent in-progress task {task_id}; use --force to requeue."
            )
    else:
        actions.extend(
            _recover_legacy_task_status(
                tasks_file,
                status_file,
                stale_after_minutes=stale_after_minutes,
                force=force,
            )
        )

    removed = cleanup_interview_sessions(workspace)
    if removed:
        actions.append(f"Removed {len(removed)} expired MCP interview files.")
    return actions or ["No recovery action needed."]


def _recover_legacy_task_status(
    tasks_file: Path,
    status_file: Path,
    *,
    stale_after_minutes: int,
    force: bool,
) -> list[str]:
    actions: list[str] = []
    cutoff = datetime.now(tz=UTC) - timedelta(minutes=stale_after_minutes)
    queue = TaskQueue(tasks_file=tasks_file, status_file=status_file)
    for task in list(queue._tasks):
        if task.status != "in_progress":
            continue
        if force or _legacy_task_is_stale(task.heartbeat_at or task.claimed_at, cutoff):
            queue.requeue(task.id)
            actions.append(f"Requeued stale in-progress task {task.id}.")
        else:
            actions.append(
                f"Skipped active/recent in-progress task {task.id}; use --force to requeue."
            )
    return actions


def install_skill(destination: Path | None = None) -> Path:
    base = destination or Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
    skill_dir = base / "skills" / "jibuff"
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text(JIBUFF_SKILL_MD, encoding="utf-8")
    return skill_file


def list_interview_sessions(workspace: Path) -> list[dict[str, object]]:
    sessions_dir = workspace / ".jibuff" / "mcp" / "interviews"
    if not sessions_dir.exists():
        return []

    sessions: list[dict[str, object]] = []
    for path in sorted(sessions_dir.glob("*.md")):
        state = _read_session_state(path)
        sessions.append(
            {
                "session_id": path.stem,
                "path": str(path),
                "status": state.get("status") if state else "unknown",
                "revision": state.get("revision") if state else None,
                "mode": state.get("mode") if state else None,
                "expires_at": state.get("expires_at") if state else None,
            }
        )
    return sessions


def cleanup_interview_sessions(workspace: Path) -> list[Path]:
    now = datetime.now(tz=UTC)
    cutoff = now - timedelta(hours=MCP_INTERVIEW_TTL_HOURS)
    sessions_dir = workspace / ".jibuff" / "mcp" / "interviews"
    removed: list[Path] = []
    if not sessions_dir.exists():
        return removed

    for path in sessions_dir.glob("*.md"):
        state = _read_session_state(path)
        expires_at = state.get("expires_at") if state else None
        expired = False
        if isinstance(expires_at, str):
            try:
                expired = datetime.fromisoformat(expires_at) <= now
            except ValueError:
                expired = _mtime_before(path, cutoff)
        else:
            expired = _mtime_before(path, cutoff)

        if expired:
            path.unlink(missing_ok=True)
            removed.append(path)

    for path in sessions_dir.glob("*.lock"):
        if not path.with_suffix(".md").exists():
            path.unlink(missing_ok=True)
            removed.append(path)

    return removed


def _json_list_len(path: Path) -> int:
    if not path.exists():
        return 0
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return 0
    return len(data) if isinstance(data, list) else 0


def _read_session_state(path: Path) -> dict[str, object] | None:
    text = path.read_text(encoding="utf-8")
    start = "```json jibuff-session\n"
    end = "\n```"
    if start not in text:
        return None
    payload = text.split(start, 1)[1].split(end, 1)[0]
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _mtime_before(path: Path, cutoff: datetime) -> bool:
    try:
        mtime = datetime.fromtimestamp(path.stat().st_mtime, UTC)
    except FileNotFoundError:
        return False
    return mtime <= cutoff


def _legacy_task_is_stale(timestamp: str | None, cutoff: datetime) -> bool:
    if timestamp is None:
        return True
    try:
        return datetime.fromisoformat(timestamp) <= cutoff
    except ValueError:
        return True


def _runtime_has_in_progress_tasks(runtime_store: RuntimeStore) -> bool:
    data = runtime_store.inspect()
    tasks = data.get("tasks")
    if not isinstance(tasks, list):
        return False
    return any(isinstance(task, dict) and task.get("status") == "in_progress" for task in tasks)
