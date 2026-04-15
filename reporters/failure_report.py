"""Generates structured failure artifacts from validator output."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from orchestrator.task_queue import Task


def write_failure_report(
    task: Task,
    validator_errors: dict[str, str],
    storage_dir: Path,
) -> str:
    """Write last_failure.md and update open_issues.json.

    Returns the failure context string to inject into the next agent run.
    """
    timestamp = datetime.now(tz=UTC).isoformat()

    # Build markdown report
    lines = [
        "# Last Failure Report",
        "",
        f"**Task:** {task.id} — {task.description}",
        f"**Timestamp:** {timestamp}",
        "",
        "## Failures",
        "",
    ]
    for gate, error in validator_errors.items():
        lines += [f"### {gate}", "```", error.strip(), "```", ""]

    md_content = "\n".join(lines)
    storage_dir.mkdir(parents=True, exist_ok=True)
    (storage_dir / "last_failure.md").write_text(md_content, encoding="utf-8")

    # Append to open_issues.json
    issues_file = storage_dir / "open_issues.json"
    issues: list[dict[str, str]] = []
    if issues_file.exists():
        try:
            issues = json.loads(issues_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            issues = []

    for gate, error in validator_errors.items():
        issues.append({
            "task_id": task.id,
            "gate": gate,
            "summary": error.strip()[:200],
            "timestamp": timestamp,
        })

    issues_file.write_text(
        json.dumps(issues, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    # Return concise failure context for agent re-injection
    summary_lines = [f"[{gate}] {err.strip()[:120]}" for gate, err in validator_errors.items()]
    return "\n".join(summary_lines)
