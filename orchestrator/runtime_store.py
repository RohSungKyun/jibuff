from __future__ import annotations

import contextlib
import json
import os
import threading
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from orchestrator.task_queue import Task

try:
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - Windows fallback
    _fcntl = None  # type: ignore[assignment]


SCHEMA_VERSION = "0.1.0"
DEFAULT_WORKER_ID = "worker-1"
DEFAULT_HEARTBEAT_INTERVAL_SECONDS = 30.0
DEFAULT_STALE_AFTER_MINUTES = 10


@dataclass
class RecoverReport:
    requeued: list[str]
    skipped: list[str]
    missing_run: bool = False


class RuntimeStore:
    """File-backed run state for future parallel workers.

    ``storage/task_status.json`` remains a compatibility mirror. The runtime
    source of truth lives under ``.jibuff/runs/<run_id>/`` where each task and
    worker has its own JSON file to reduce write contention.
    """

    def __init__(self, workspace: Path, run_id: str) -> None:
        self.workspace = workspace
        self.run_id = run_id
        self.root = workspace / ".jibuff" / "runs" / run_id
        self._thread_lock = threading.Lock()

    @classmethod
    def start(
        cls,
        workspace: Path,
        tasks: list[Task],
        *,
        mode: str,
        worker_count: int = 1,
    ) -> RuntimeStore:
        run_id = _new_run_id()
        store = cls(workspace, run_id)
        store._init_dirs()
        now = _utc_timestamp()
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "run_id": run_id,
            "mode": mode,
            "workspace": str(workspace),
            "status": "running",
            "created_at": now,
            "updated_at": now,
            "worker_count": worker_count,
        }
        store._write_json(store.manifest_path, manifest)
        for task in tasks:
            store._write_json(
                store.task_path(task.id),
                {
                    "schema_version": SCHEMA_VERSION,
                    "id": task.id,
                    "description": task.description,
                    "status": task.status,
                    "revision": task.revision,
                    "claimed_by": task.claimed_by,
                    "claim_token": task.claim_token,
                    "claimed_at": task.claimed_at,
                    "heartbeat_at": task.heartbeat_at,
                    "started_at": None,
                    "completed_at": None,
                    "last_error": None,
                },
            )
        for index in range(worker_count):
            worker_id = f"worker-{index + 1}"
            store._write_json(
                store.worker_path(worker_id),
                {
                    "schema_version": SCHEMA_VERSION,
                    "id": worker_id,
                    "kind": "agent",
                    "status": "idle",
                    "current_task_id": None,
                    "pid": os.getpid(),
                    "started_at": now,
                    "heartbeat_at": now,
                    "last_seen_at": now,
                },
            )
        store._write_json(store.active_run_path(workspace), {"run_id": run_id})
        store.append_event("run_started", {"mode": mode, "worker_count": worker_count})
        return store

    @classmethod
    def active(cls, workspace: Path) -> RuntimeStore | None:
        path = cls.active_run_path(workspace)
        data = _read_json(path)
        run_id = data.get("run_id")
        if not isinstance(run_id, str):
            return None
        store = cls(workspace, run_id)
        return store if store.manifest_path.exists() else None

    @classmethod
    def latest(cls, workspace: Path) -> RuntimeStore | None:
        runs_dir = workspace / ".jibuff" / "runs"
        if not runs_dir.exists():
            return None
        candidates = [path for path in runs_dir.iterdir() if path.is_dir()]
        if not candidates:
            return None
        latest = max(candidates, key=lambda p: p.stat().st_mtime)
        return cls(workspace, latest.name)

    @staticmethod
    def active_run_path(workspace: Path) -> Path:
        return workspace / ".jibuff" / "runs" / "active.json"

    @property
    def manifest_path(self) -> Path:
        return self.root / "manifest.json"

    @property
    def events_path(self) -> Path:
        return self.root / "events.jsonl"

    def task_path(self, task_id: str) -> Path:
        return self.root / "tasks" / f"{task_id}.json"

    def worker_path(self, worker_id: str) -> Path:
        return self.root / "workers" / f"{worker_id}.json"

    def claim_task(
        self,
        task: Task,
        *,
        worker_id: str = DEFAULT_WORKER_ID,
        claim_token: str | None = None,
    ) -> str:
        token = claim_token or f"{task.id}:{uuid.uuid4().hex}"
        now = _utc_timestamp()
        with self._file_lock("tasks", task.id):
            state = self._read_json(self.task_path(task.id))
            state.update(
                {
                    "status": "in_progress",
                    "revision": int(state.get("revision", 0)) + 1,
                    "claimed_by": worker_id,
                    "claim_token": token,
                    "claimed_at": now,
                    "heartbeat_at": now,
                    "started_at": state.get("started_at") or now,
                }
            )
            self._write_json(self.task_path(task.id), state)
        self._update_worker(worker_id, status="busy", current_task_id=task.id)
        self.append_event("task_claimed", {"task_id": task.id, "worker_id": worker_id})
        return token

    def heartbeat(
        self,
        task_id: str,
        claim_token: str,
        *,
        worker_id: str = DEFAULT_WORKER_ID,
    ) -> bool:
        now = _utc_timestamp()
        with self._file_lock("tasks", task_id):
            state = self._read_json(self.task_path(task_id))
            if state.get("claim_token") != claim_token or state.get("status") != "in_progress":
                return False
            state["heartbeat_at"] = now
            self._write_json(self.task_path(task_id), state)
        self._update_worker(worker_id, status="busy", current_task_id=task_id, heartbeat_at=now)
        return True

    def complete_task(
        self,
        task_id: str,
        claim_token: str,
        *,
        worker_id: str = DEFAULT_WORKER_ID,
    ) -> None:
        self._transition_claimed_task(task_id, claim_token, "done", worker_id=worker_id)

    def requeue_task(
        self,
        task_id: str,
        claim_token: str,
        *,
        worker_id: str = DEFAULT_WORKER_ID,
    ) -> None:
        self._transition_claimed_task(task_id, claim_token, "todo", worker_id=worker_id)

    def finish(self, status: str) -> None:
        manifest = self._read_json(self.manifest_path)
        manifest["status"] = status
        manifest["updated_at"] = _utc_timestamp()
        self._write_json(self.manifest_path, manifest)
        self.append_event("run_finished", {"status": status})

    def inspect(self) -> dict[str, object]:
        tasks = [self._read_json(path) for path in sorted((self.root / "tasks").glob("*.json"))]
        workers = [
            self._read_json(path) for path in sorted((self.root / "workers").glob("*.json"))
        ]
        return {
            "run_id": self.run_id,
            "manifest": self._read_json(self.manifest_path),
            "tasks": tasks,
            "workers": workers,
        }

    def recover_stale(
        self,
        *,
        stale_after_minutes: int = DEFAULT_STALE_AFTER_MINUTES,
        force: bool = False,
    ) -> RecoverReport:
        cutoff = datetime.now(tz=UTC) - timedelta(minutes=stale_after_minutes)
        requeued: list[str] = []
        skipped: list[str] = []
        for path in sorted((self.root / "tasks").glob("*.json")):
            state = self._read_json(path)
            if state.get("status") != "in_progress":
                continue
            task_id = str(state.get("id", path.stem))
            stale = force or _is_stale_task(state, cutoff)
            if not stale:
                skipped.append(task_id)
                continue
            with self._file_lock("tasks", task_id):
                current = self._read_json(path)
                if current.get("status") != "in_progress":
                    continue
                worker_id = current.get("claimed_by")
                current["status"] = "todo"
                current["revision"] = int(current.get("revision", 0)) + 1
                current["claimed_by"] = None
                current["claim_token"] = None
                current["claimed_at"] = None
                current["heartbeat_at"] = None
                self._write_json(path, current)
            if isinstance(worker_id, str):
                self._update_worker(worker_id, status="idle", current_task_id=None)
            self.append_event("task_recovered", {"task_id": task_id, "force": force})
            requeued.append(task_id)
        return RecoverReport(requeued=requeued, skipped=skipped)

    def append_event(self, event_type: str, payload: dict[str, object]) -> None:
        event = {
            "ts": _utc_timestamp(),
            "type": event_type,
            "run_id": self.run_id,
            **payload,
        }
        self.events_path.parent.mkdir(parents=True, exist_ok=True)
        with self._file_lock("events", "events"), self.events_path.open(
            "a",
            encoding="utf-8",
        ) as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")

    def _transition_claimed_task(
        self,
        task_id: str,
        claim_token: str,
        new_status: str,
        *,
        worker_id: str,
    ) -> None:
        with self._file_lock("tasks", task_id):
            state = self._read_json(self.task_path(task_id))
            if state.get("claim_token") != claim_token:
                raise RuntimeError(f"stale runtime claim token for task {task_id}")
            state["status"] = new_status
            state["revision"] = int(state.get("revision", 0)) + 1
            state["claimed_by"] = None
            state["claim_token"] = None
            state["claimed_at"] = None
            state["heartbeat_at"] = None
            if new_status == "done":
                state["completed_at"] = _utc_timestamp()
            self._write_json(self.task_path(task_id), state)
        self._update_worker(worker_id, status="idle", current_task_id=None)
        self.append_event(
            "task_completed" if new_status == "done" else "task_requeued",
            {"task_id": task_id, "worker_id": worker_id},
        )

    def _update_worker(self, worker_id: str, **updates: object) -> None:
        path = self.worker_path(worker_id)
        now = _utc_timestamp()
        with self._file_lock("workers", worker_id):
            state = self._read_json(path)
            if not state:
                state = {
                    "schema_version": SCHEMA_VERSION,
                    "id": worker_id,
                    "kind": "agent",
                    "pid": os.getpid(),
                    "started_at": now,
                }
            state.update(updates)
            state["heartbeat_at"] = str(updates.get("heartbeat_at") or now)
            state["last_seen_at"] = now
            self._write_json(path, state)

    def _init_dirs(self) -> None:
        for path in (
            self.root / "tasks",
            self.root / "workers",
            self.root / "locks",
        ):
            path.mkdir(parents=True, exist_ok=True)

    def _read_json(self, path: Path) -> dict[str, Any]:
        return _read_json(path)

    def _write_json(self, path: Path, data: dict[str, object]) -> None:
        with self._thread_lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            tmp.replace(path)

    @contextlib.contextmanager
    def _file_lock(self, category: str, name: str):  # type: ignore[no-untyped-def]
        lock_dir = self.root / "locks" / category
        lock_dir.mkdir(parents=True, exist_ok=True)
        lock_path = lock_dir / f"{name}.lock"
        with lock_path.open("w", encoding="utf-8") as lock_file:
            if _fcntl is not None:
                _fcntl.flock(lock_file.fileno(), _fcntl.LOCK_EX)
            try:
                yield
            finally:
                if _fcntl is not None:
                    _fcntl.flock(lock_file.fileno(), _fcntl.LOCK_UN)


class Heartbeat:
    def __init__(
        self,
        store: RuntimeStore,
        task_id: str,
        claim_token: str,
        *,
        worker_id: str = DEFAULT_WORKER_ID,
        interval_seconds: float = DEFAULT_HEARTBEAT_INTERVAL_SECONDS,
    ) -> None:
        self.store = store
        self.task_id = task_id
        self.claim_token = claim_token
        self.worker_id = worker_id
        self.interval_seconds = interval_seconds
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)

    def __enter__(self) -> Heartbeat:
        self.store.heartbeat(self.task_id, self.claim_token, worker_id=self.worker_id)
        self._thread.start()
        return self

    def __exit__(self, *args: object) -> None:
        self._stop.set()
        self._thread.join(timeout=1.0)

    def _loop(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            if not self.store.heartbeat(self.task_id, self.claim_token, worker_id=self.worker_id):
                self._stop.set()


def _new_run_id() -> str:
    return f"{datetime.now(tz=UTC).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"


def _utc_timestamp() -> str:
    return datetime.now(tz=UTC).isoformat()


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _is_stale_task(state: dict[str, object], cutoff: datetime) -> bool:
    heartbeat_at = state.get("heartbeat_at")
    claimed_at = state.get("claimed_at")
    timestamp = heartbeat_at if isinstance(heartbeat_at, str) else claimed_at
    if not isinstance(timestamp, str):
        return True
    try:
        return datetime.fromisoformat(timestamp) <= cutoff
    except ValueError:
        return True
