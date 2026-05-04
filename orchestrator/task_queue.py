"""TaskQueue — reads tasks.md and returns the next incomplete task."""

from __future__ import annotations

import json
import re
import threading
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path


@dataclass
class Task:
    id: str        # e.g. "P2-01"
    description: str
    status: str    # "todo" | "in_progress" | "done" | "blocked"
    revision: int = 0
    claimed_by: str | None = None
    claimed_at: str | None = None
    claim_token: str | None = None
    heartbeat_at: str | None = None


class TaskClaimError(RuntimeError):
    """Raised when a task status mutation uses a stale claim token."""


@dataclass
class TaskQueue:
    tasks_file: Path
    status_file: Path
    _tasks: list[Task] = field(default_factory=list, init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def __post_init__(self) -> None:
        self._tasks = self._parse_tasks()
        self._apply_status_overrides()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def next(self) -> Task | None:
        """Return the next todo task, or None if all are complete."""
        for task in self._tasks:
            if task.status == "todo":
                return task
        return None

    def mark_done(self, task_id: str, claim_token: str | None = None) -> None:
        self._require_claim(task_id, claim_token)
        self._update_status(task_id, "done", clear_claim=True)

    def mark_in_progress(
        self,
        task_id: str,
        claimed_by: str = "jibuff-run",
        claim_token: str | None = None,
    ) -> str:
        token = claim_token or f"{task_id}:{self.utc_timestamp()}"
        now = self.utc_timestamp()
        self._update_status(
            task_id,
            "in_progress",
            claimed_by=claimed_by,
            claimed_at=now,
            claim_token=token,
            heartbeat_at=now,
        )
        return token

    def requeue(self, task_id: str, claim_token: str | None = None) -> None:
        """Reset an in_progress task back to todo for re-execution."""
        self._require_claim(task_id, claim_token)
        self._update_status(task_id, "todo", clear_claim=True)

    def all_done(self) -> bool:
        return all(t.status == "done" for t in self._tasks)

    def summary(self) -> dict[str, int]:
        counts: dict[str, int] = {"todo": 0, "in_progress": 0, "done": 0, "blocked": 0}
        for t in self._tasks:
            counts[t.status] = counts.get(t.status, 0) + 1
        return counts

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------

    _TASK_RE = re.compile(
        r"^-\s+\[(?P<marker>[ x~!])\]\s+(?P<id>[A-Z0-9]+-\d+):\s+(?P<desc>.+)$",
        re.MULTILINE,
    )
    _MARKER_MAP = {" ": "todo", "x": "done", "~": "in_progress", "!": "blocked"}

    def _parse_tasks(self) -> list[Task]:
        if not self.tasks_file.exists():
            return []
        text = self.tasks_file.read_text(encoding="utf-8")
        tasks = []
        for m in self._TASK_RE.finditer(text):
            status = self._MARKER_MAP.get(m.group("marker"), "todo")
            tasks.append(Task(
                id=m.group("id"),
                description=m.group("desc").strip(),
                status=status,
            ))
        return tasks

    def _apply_status_overrides(self) -> None:
        """Overlay task_status.json on top of tasks.md markers."""
        if not self.status_file.exists():
            return
        try:
            data = json.loads(self.status_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return
        overrides = {
            entry["id"]: entry
            for entry in data.get("tasks", [])
            if isinstance(entry, dict) and "id" in entry and "status" in entry
        }
        for task in self._tasks:
            if task.id in overrides:
                entry = overrides[task.id]
                task.status = str(entry["status"])
                task.revision = int(entry.get("revision", 0))
                task.claimed_by = self._optional_str(entry.get("claimed_by"))
                task.claimed_at = self._optional_str(entry.get("claimed_at"))
                task.claim_token = self._optional_str(entry.get("claim_token"))
                task.heartbeat_at = self._optional_str(entry.get("heartbeat_at"))

    def touch_heartbeat(self, task_id: str, claim_token: str) -> bool:
        with self._lock:
            for task in self._tasks:
                if task.id != task_id:
                    continue
                if task.claim_token != claim_token or task.status != "in_progress":
                    return False
                task.heartbeat_at = self.utc_timestamp()
                self._flush_status_file()
                return True
        return False

    def _update_status(
        self,
        task_id: str,
        new_status: str,
        *,
        claimed_by: str | None = None,
        claimed_at: str | None = None,
        claim_token: str | None = None,
        heartbeat_at: str | None = None,
        clear_claim: bool = False,
    ) -> None:
        with self._lock:
            for task in self._tasks:
                if task.id == task_id:
                    task.status = new_status
                    task.revision += 1
                    if clear_claim:
                        task.claimed_by = None
                        task.claimed_at = None
                        task.claim_token = None
                        task.heartbeat_at = None
                    else:
                        task.claimed_by = claimed_by
                        task.claimed_at = claimed_at
                        task.claim_token = claim_token
                        task.heartbeat_at = heartbeat_at
                    break
            self._flush_status_file()

    def _flush_status_file(self) -> None:
        data = {
            "version": "0.2.0",
            "tasks": [asdict(t) for t in self._tasks],
        }
        self.status_file.parent.mkdir(parents=True, exist_ok=True)
        self.status_file.write_text(
            json.dumps(data, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    def _require_claim(self, task_id: str, claim_token: str | None) -> None:
        if claim_token is None:
            return
        for task in self._tasks:
            if task.id == task_id:
                if task.claim_token != claim_token:
                    raise TaskClaimError(f"stale claim token for task {task_id}")
                return

    @staticmethod
    def utc_timestamp() -> str:
        return datetime.now(tz=UTC).isoformat()

    @staticmethod
    def _optional_str(value: object) -> str | None:
        return value if isinstance(value, str) else None
