from __future__ import annotations

import json
import textwrap
from datetime import UTC, datetime, timedelta
from pathlib import Path

from orchestrator.ops import (
    cleanup_workspace,
    inspect_workspace,
    install_skill,
    recover_workspace,
)
from orchestrator.runtime_store import RuntimeStore
from orchestrator.task_queue import Task


def test_inspect_workspace_reports_tasks_and_failures(tmp_path: Path) -> None:
    spec_dir = tmp_path / "spec"
    storage_dir = tmp_path / "storage"
    spec_dir.mkdir()
    storage_dir.mkdir()
    (spec_dir / "tasks.md").write_text(
        textwrap.dedent("""\
        # Tasks

        - [ ] P0-01: First task
        - [x] P0-02: Done task
        """),
        encoding="utf-8",
    )
    (storage_dir / "last_failure.md").write_text("failed", encoding="utf-8")
    (storage_dir / "open_issues.json").write_text(
        json.dumps([{"task_id": "P0-01"}]),
        encoding="utf-8",
    )

    result = inspect_workspace(tmp_path)

    assert result.has_tasks is True
    assert result.summary["todo"] == 1
    assert result.summary["done"] == 1
    assert result.last_failure is True
    assert result.open_issue_count == 1


def test_recover_workspace_requeues_in_progress_task(tmp_path: Path) -> None:
    spec_dir = tmp_path / "spec"
    storage_dir = tmp_path / "storage"
    spec_dir.mkdir()
    storage_dir.mkdir()
    (spec_dir / "tasks.md").write_text("- [~] P0-01: Stale task\n", encoding="utf-8")

    actions = recover_workspace(tmp_path)
    result = inspect_workspace(tmp_path)

    assert actions == ["Requeued stale in-progress task P0-01."]
    assert result.tasks[0]["status"] == "todo"


def test_recover_workspace_skips_active_runtime_task(tmp_path: Path) -> None:
    spec_dir = tmp_path / "spec"
    storage_dir = tmp_path / "storage"
    spec_dir.mkdir()
    storage_dir.mkdir()
    (spec_dir / "tasks.md").write_text("- [ ] P0-01: Active task\n", encoding="utf-8")
    task = Task(id="P0-01", description="Active task", status="todo")
    store = RuntimeStore.start(tmp_path, [task], mode="quick")
    store.claim_task(task)

    actions = recover_workspace(tmp_path, stale_after_minutes=10)

    assert actions == ["Skipped active/recent in-progress task P0-01; use --force to requeue."]


def test_cleanup_workspace_removes_expired_interview_and_orphan_lock(tmp_path: Path) -> None:
    session_dir = tmp_path / ".jibuff" / "mcp" / "interviews"
    session_dir.mkdir(parents=True)
    expired = datetime.now(tz=UTC) - timedelta(hours=1)
    session = session_dir / "abc.md"
    session.write_text(
        "```json jibuff-session\n"
        + json.dumps({"expires_at": expired.isoformat()})
        + "\n```\n",
        encoding="utf-8",
    )
    lock = session_dir / "orphan.lock"
    lock.write_text("", encoding="utf-8")

    removed = cleanup_workspace(tmp_path)

    assert session in removed
    assert lock in removed
    assert not session.exists()
    assert not lock.exists()


def test_install_skill_writes_skill_md(tmp_path: Path) -> None:
    skill_file = install_skill(tmp_path)
    content = skill_file.read_text(encoding="utf-8")

    assert skill_file == tmp_path / "skills" / "jibuff" / "SKILL.md"
    assert content.startswith("---\n")
    assert "name: jibuff" in content
    assert "description:" in content
    assert "jb interview" in content
