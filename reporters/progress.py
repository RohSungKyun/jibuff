"""Updates progress.md and task_status.json after each loop iteration."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from orchestrator.task_queue import TaskQueue


def write_progress(queue: TaskQueue, storage_dir: Path) -> None:
    """Rewrite progress.md from current TaskQueue state."""
    summary = queue.summary()
    total = sum(summary.values())
    done = summary.get("done", 0)
    timestamp = datetime.now(tz=UTC).strftime("%Y-%m-%d %H:%M UTC")

    lines = [
        "# Progress",
        "",
        f"_Last updated: {timestamp}_",
        "",
        f"**{done} / {total}** tasks complete",
        "",
        "| Status | Count |",
        "|--------|------:|",
        f"| done | {summary.get('done', 0)} |",
        f"| in_progress | {summary.get('in_progress', 0)} |",
        f"| todo | {summary.get('todo', 0)} |",
        f"| blocked | {summary.get('blocked', 0)} |",
        "",
    ]

    storage_dir.mkdir(parents=True, exist_ok=True)
    (storage_dir / "progress.md").write_text("\n".join(lines), encoding="utf-8")
