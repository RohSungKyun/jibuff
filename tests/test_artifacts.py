"""Phase 4 — Memory/Artifact Layer tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from storage.artifacts import ArtifactStore, DecisionEntry, OpenIssue, TaskStatus


@pytest.fixture
def store(tmp_path: Path) -> ArtifactStore:
    return ArtifactStore(storage_dir=tmp_path / "storage")


# ---------------------------------------------------------------------------
# task_status.json
# ---------------------------------------------------------------------------


def test_write_and_read_task_statuses(store: ArtifactStore) -> None:
    statuses = [
        TaskStatus(id="P0-01", status="done", description="init project"),
        TaskStatus(id="P0-02", status="todo", description="create dirs"),
    ]
    store.write_task_statuses(statuses)
    result = store.read_task_statuses()
    assert len(result) == 2
    assert result[0].id == "P0-01"
    assert result[0].status == "done"
    assert result[1].status == "todo"


def test_read_task_statuses_empty(store: ArtifactStore) -> None:
    result = store.read_task_statuses()
    assert result == []


def test_task_status_roundtrip_preserves_fields(store: ArtifactStore) -> None:
    original = TaskStatus(id="P1-01", status="in_progress", description="build engine")
    store.write_task_statuses([original])
    loaded = store.read_task_statuses()[0]
    assert loaded.id == original.id
    assert loaded.status == original.status
    assert loaded.description == original.description


# ---------------------------------------------------------------------------
# open_issues.json
# ---------------------------------------------------------------------------


def test_append_and_read_issues(store: ArtifactStore) -> None:
    store.append_issue(OpenIssue(task_id="P0-01", gate="lint", summary="E501"))
    store.append_issue(OpenIssue(task_id="P0-01", gate="tests", summary="2 failed"))
    issues = store.read_issues()
    assert len(issues) == 2
    assert issues[0].gate == "lint"
    assert issues[1].gate == "tests"


def test_read_issues_empty(store: ArtifactStore) -> None:
    assert store.read_issues() == []


def test_resolve_issues_removes_task(store: ArtifactStore) -> None:
    store.append_issue(OpenIssue(task_id="P0-01", gate="lint", summary="error"))
    store.append_issue(OpenIssue(task_id="P0-02", gate="tests", summary="fail"))
    store.resolve_issues("P0-01")
    issues = store.read_issues()
    assert len(issues) == 1
    assert issues[0].task_id == "P0-02"


def test_resolve_issues_noop_when_not_found(store: ArtifactStore) -> None:
    store.append_issue(OpenIssue(task_id="P0-01", gate="lint", summary="error"))
    store.resolve_issues("P0-99")  # doesn't exist
    assert len(store.read_issues()) == 1


# ---------------------------------------------------------------------------
# decision_log.md
# ---------------------------------------------------------------------------


def test_append_and_read_decision(store: ArtifactStore) -> None:
    store.append_decision(DecisionEntry(
        decision="Use hatchling over setuptools",
        rationale="Better PEP 517 support",
        task_id="P0-01",
    ))
    log = store.read_decisions()
    assert "hatchling" in log
    assert "PEP 517" in log
    assert "P0-01" in log


def test_append_multiple_decisions(store: ArtifactStore) -> None:
    store.append_decision(DecisionEntry(decision="A", rationale="reason A"))
    store.append_decision(DecisionEntry(decision="B", rationale="reason B"))
    log = store.read_decisions()
    assert "reason A" in log
    assert "reason B" in log


def test_read_decisions_empty(store: ArtifactStore) -> None:
    assert store.read_decisions() == ""


def test_decision_without_task_id(store: ArtifactStore) -> None:
    store.append_decision(DecisionEntry(decision="X", rationale="Y", task_id=None))
    log = store.read_decisions()
    assert "X" in log
    assert "task:" not in log


# ---------------------------------------------------------------------------
# last_failure.md
# ---------------------------------------------------------------------------


def test_read_last_failure_when_absent(store: ArtifactStore) -> None:
    assert store.read_last_failure() == ""


def test_read_last_failure_returns_content(store: ArtifactStore) -> None:
    (store.storage_dir / "last_failure.md").write_text("# Failure\nerror detail", encoding="utf-8")
    assert "error detail" in store.read_last_failure()


def test_clear_last_failure(store: ArtifactStore) -> None:
    store.storage_dir.mkdir(parents=True, exist_ok=True)
    (store.storage_dir / "last_failure.md").write_text("old failure", encoding="utf-8")
    store.clear_last_failure()
    assert store.read_last_failure() == ""


# ---------------------------------------------------------------------------
# task_context — context injection
# ---------------------------------------------------------------------------


def test_task_context_includes_last_failure(store: ArtifactStore) -> None:
    store.storage_dir.mkdir(parents=True, exist_ok=True)
    (store.storage_dir / "last_failure.md").write_text("mypy error in foo.py", encoding="utf-8")
    ctx = store.task_context("P0-01")
    assert "mypy error" in ctx


def test_task_context_includes_only_own_issues(store: ArtifactStore) -> None:
    store.append_issue(OpenIssue(task_id="P0-01", gate="lint", summary="own issue"))
    store.append_issue(OpenIssue(task_id="P0-02", gate="tests", summary="other issue"))
    ctx = store.task_context("P0-01")
    assert "own issue" in ctx
    assert "other issue" not in ctx


def test_task_context_empty_when_nothing(store: ArtifactStore) -> None:
    ctx = store.task_context("P0-01")
    assert ctx == ""


def test_task_context_no_full_logs(store: ArtifactStore) -> None:
    """Context must never contain full conversation logs."""
    store.append_issue(OpenIssue(task_id="P0-01", gate="lint", summary="error"))
    ctx = store.task_context("P0-01")
    # Context should be short and structured, not a raw log dump
    assert len(ctx) < 2000
