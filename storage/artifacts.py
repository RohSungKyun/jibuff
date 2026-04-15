"""Artifact writer/reader — structured context handoff between loop iterations.

All artifacts are flat files in the storage directory.
No full conversation logs are ever persisted — only structured summaries.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


@dataclass
class TaskStatus:
    id: str
    status: str       # todo | in_progress | done | blocked
    description: str
    updated_at: str = field(default_factory=lambda: datetime.now(tz=UTC).isoformat())


@dataclass
class OpenIssue:
    task_id: str
    gate: str
    summary: str      # truncated to 200 chars
    timestamp: str = field(default_factory=lambda: datetime.now(tz=UTC).isoformat())


@dataclass
class DecisionEntry:
    decision: str
    rationale: str
    task_id: str | None = None
    timestamp: str = field(default_factory=lambda: datetime.now(tz=UTC).isoformat())


# ---------------------------------------------------------------------------
# ArtifactStore
# ---------------------------------------------------------------------------


class ArtifactStore:
    """Read and write all structured artifacts for a jibuff session."""

    def __init__(self, storage_dir: Path) -> None:
        self.storage_dir = storage_dir
        storage_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # task_status.json
    # ------------------------------------------------------------------

    def write_task_statuses(self, statuses: list[TaskStatus]) -> None:
        data = {
            "version": "0.1.0",
            "tasks": [asdict(s) for s in statuses],
        }
        self._write_json("task_status.json", data)

    def read_task_statuses(self) -> list[TaskStatus]:
        data = self._read_json("task_status.json")
        return [TaskStatus(**entry) for entry in data.get("tasks", [])]

    # ------------------------------------------------------------------
    # open_issues.json
    # ------------------------------------------------------------------

    def append_issue(self, issue: OpenIssue) -> None:
        issues = self._read_json_list("open_issues.json")
        issues.append(asdict(issue))
        self._write_json_list("open_issues.json", issues)

    def read_issues(self) -> list[OpenIssue]:
        return [OpenIssue(**entry) for entry in self._read_json_list("open_issues.json")]

    def resolve_issues(self, task_id: str) -> None:
        """Remove all open issues for a task (called on pass)."""
        issues = self._read_json_list("open_issues.json")
        remaining = [i for i in issues if i.get("task_id") != task_id]
        self._write_json_list("open_issues.json", remaining)

    # ------------------------------------------------------------------
    # decision_log.md
    # ------------------------------------------------------------------

    def append_decision(self, entry: DecisionEntry) -> None:
        log_file = self.storage_dir / "decision_log.md"
        existing = log_file.read_text(encoding="utf-8") if log_file.exists() else "# Decision Log\n"
        task_tag = f" (task: {entry.task_id})" if entry.task_id else ""
        block = (
            f"\n## {entry.timestamp}{task_tag}\n"
            f"**Decision:** {entry.decision}\n"
            f"**Rationale:** {entry.rationale}\n"
        )
        log_file.write_text(existing + block, encoding="utf-8")

    def read_decisions(self) -> str:
        log_file = self.storage_dir / "decision_log.md"
        return log_file.read_text(encoding="utf-8") if log_file.exists() else ""

    # ------------------------------------------------------------------
    # last_failure.md (read-only here — written by failure_report.py)
    # ------------------------------------------------------------------

    def read_last_failure(self) -> str:
        f = self.storage_dir / "last_failure.md"
        return f.read_text(encoding="utf-8") if f.exists() else ""

    def clear_last_failure(self) -> None:
        f = self.storage_dir / "last_failure.md"
        if f.exists():
            f.unlink()

    # ------------------------------------------------------------------
    # Context injection — task-scoped artifacts only
    # ------------------------------------------------------------------

    def task_context(self, task_id: str) -> str:
        """Return only the artifacts relevant to the given task.

        Never returns full logs or unrelated task history.
        """
        parts: list[str] = []

        failure = self.read_last_failure()
        if failure:
            parts.append(f"=== Last Failure ===\n{failure}")

        issues = [i for i in self.read_issues() if i.task_id == task_id]
        if issues:
            issue_lines = "\n".join(f"- [{i.gate}] {i.summary}" for i in issues)
            parts.append(f"=== Open Issues for {task_id} ===\n{issue_lines}")

        return "\n\n".join(parts)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _write_json(self, filename: str, data: object) -> None:
        (self.storage_dir / filename).write_text(
            json.dumps(data, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    def _read_json(self, filename: str) -> dict[str, Any]:
        f = self.storage_dir / filename
        if not f.exists():
            return {}
        try:
            result = json.loads(f.read_text(encoding="utf-8"))
            return result if isinstance(result, dict) else {}
        except json.JSONDecodeError:
            return {}

    def _write_json_list(self, filename: str, data: list[Any]) -> None:
        (self.storage_dir / filename).write_text(
            json.dumps(data, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    def _read_json_list(self, filename: str) -> list[dict[str, Any]]:
        f = self.storage_dir / filename
        if not f.exists():
            return []
        try:
            result = json.loads(f.read_text(encoding="utf-8"))
            return result if isinstance(result, list) else []
        except json.JSONDecodeError:
            return []
