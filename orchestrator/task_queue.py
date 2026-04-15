"""TaskQueue — reads tasks.md and returns the next incomplete task."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Task:
    id: str        # e.g. "P2-01"
    description: str
    status: str    # "todo" | "in_progress" | "done" | "blocked"


@dataclass
class TaskQueue:
    tasks_file: Path
    status_file: Path
    _tasks: list[Task] = field(default_factory=list, init=False)

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

    def mark_done(self, task_id: str) -> None:
        self._update_status(task_id, "done")

    def mark_in_progress(self, task_id: str) -> None:
        self._update_status(task_id, "in_progress")

    def requeue(self, task_id: str) -> None:
        """Reset an in_progress task back to todo for re-execution."""
        self._update_status(task_id, "todo")

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
        overrides: dict[str, str] = {
            entry["id"]: entry["status"]
            for entry in data.get("tasks", [])
            if "id" in entry and "status" in entry
        }
        for task in self._tasks:
            if task.id in overrides:
                task.status = overrides[task.id]

    def _update_status(self, task_id: str, new_status: str) -> None:
        for task in self._tasks:
            if task.id == task_id:
                task.status = new_status
                break
        self._flush_status_file()

    def _flush_status_file(self) -> None:
        data = {
            "version": "0.1.0",
            "tasks": [{"id": t.id, "status": t.status, "description": t.description}
                      for t in self._tasks],
        }
        self.status_file.write_text(
            json.dumps(data, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
