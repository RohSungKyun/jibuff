from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from orchestrator.runtime_store import RuntimeClaimError, RuntimeStore
from orchestrator.task_queue import Task


def test_runtime_store_creates_run_layout(tmp_path: Path) -> None:
    task = Task(id="P0-01", description="first task", status="todo")
    store = RuntimeStore.start(tmp_path, [task], mode="quick")

    assert (tmp_path / ".jibuff" / "runs" / "active.json").exists()
    assert store.manifest_path.exists()
    assert store.task_path("P0-01").exists()
    assert store.worker_path("worker-1").exists()
    assert store.events_path.exists()


def test_runtime_store_claim_heartbeat_and_complete(tmp_path: Path) -> None:
    task = Task(id="P0-01", description="first task", status="todo")
    store = RuntimeStore.start(tmp_path, [task], mode="quick")
    token = store.claim_task(task, claim_token="claim-1")

    assert token == "claim-1"
    assert store.heartbeat("P0-01", token) is True
    store.complete_task("P0-01", token)

    task_state = json.loads(store.task_path("P0-01").read_text(encoding="utf-8"))
    worker_state = json.loads(store.worker_path("worker-1").read_text(encoding="utf-8"))
    assert task_state["status"] == "done"
    assert task_state["claim_token"] is None
    assert worker_state["status"] == "idle"


def test_runtime_store_rejects_duplicate_claim(tmp_path: Path) -> None:
    task = Task(id="P0-01", description="first task", status="todo")
    store = RuntimeStore.start(tmp_path, [task], mode="quick")
    store.claim_task(task)

    try:
        store.claim_task(task)
    except RuntimeClaimError as exc:
        assert "not claimable" in str(exc)
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("duplicate claim should fail")


def test_runtime_store_active_ignores_finished_run(tmp_path: Path) -> None:
    task = Task(id="P0-01", description="first task", status="todo")
    store = RuntimeStore.start(tmp_path, [task], mode="quick")

    assert RuntimeStore.active(tmp_path) is not None
    store.finish("all_done")

    assert RuntimeStore.active(tmp_path) is None
    assert RuntimeStore.active(tmp_path, running_only=False) is None
    assert RuntimeStore.latest(tmp_path) is not None


def test_runtime_store_recover_skips_fresh_heartbeat(tmp_path: Path) -> None:
    task = Task(id="P0-01", description="first task", status="todo")
    store = RuntimeStore.start(tmp_path, [task], mode="quick")
    store.claim_task(task)

    report = store.recover_stale(stale_after_minutes=10)

    assert report.requeued == []
    assert report.skipped == ["P0-01"]


def test_runtime_store_recovers_stale_heartbeat(tmp_path: Path) -> None:
    task = Task(id="P0-01", description="first task", status="todo")
    store = RuntimeStore.start(tmp_path, [task], mode="quick")
    store.claim_task(task)
    state = json.loads(store.task_path("P0-01").read_text(encoding="utf-8"))
    state["heartbeat_at"] = (datetime.now(tz=UTC) - timedelta(minutes=20)).isoformat()
    store.task_path("P0-01").write_text(json.dumps(state), encoding="utf-8")

    report = store.recover_stale(stale_after_minutes=10)

    task_state = json.loads(store.task_path("P0-01").read_text(encoding="utf-8"))
    assert report.requeued == ["P0-01"]
    assert task_state["status"] == "todo"
    assert task_state["claim_token"] is None


def test_runtime_store_rechecks_stale_after_lock(tmp_path: Path) -> None:
    task = Task(id="P0-01", description="first task", status="todo")
    store = RuntimeStore.start(tmp_path, [task], mode="quick")
    store.claim_task(task)
    state = json.loads(store.task_path("P0-01").read_text(encoding="utf-8"))
    state["heartbeat_at"] = (datetime.now(tz=UTC) - timedelta(minutes=20)).isoformat()
    store.task_path("P0-01").write_text(json.dumps(state), encoding="utf-8")
    store.heartbeat("P0-01", str(state["claim_token"]))

    report = store.recover_stale(stale_after_minutes=10)

    assert report.requeued == []
    assert report.skipped == ["P0-01"]
