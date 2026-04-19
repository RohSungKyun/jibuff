"""Structured JSON tracing for loop iterations.

Writes one trace record per task execution to storage/traces/.
Each record captures: timing, outcome, validator results, quality score.

Trace files can later be piped to HyperDX, Sentry, Kibana, etc.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

from orchestrator.task_queue import Task


def write_trace(
    task: Task,
    *,
    success: bool,
    duration_seconds: float,
    validator_errors: dict[str, str] | None = None,
    quality_score: float | None = None,
    quality_passed: bool | None = None,
    stopped_reason: str = "",
    iteration: int = 0,
    storage_dir: Path,
) -> str:
    """Append a trace record to storage/traces/{date}.jsonl.

    Returns the trace_id for correlation.
    """
    trace_id = uuid.uuid4().hex[:12]
    now = datetime.now(tz=UTC)

    record = {
        "trace_id": trace_id,
        "timestamp": now.isoformat(),
        "task_id": task.id,
        "task_description": task.description,
        "iteration": iteration,
        "success": success,
        "duration_seconds": round(duration_seconds, 2),
    }

    if validator_errors:
        record["validator_errors"] = {
            k: v.strip()[:200] for k, v in validator_errors.items()
        }

    if quality_score is not None:
        record["quality_score"] = round(quality_score, 3)
        record["quality_passed"] = quality_passed

    if stopped_reason:
        record["stopped_reason"] = stopped_reason

    traces_dir = storage_dir / "traces"
    traces_dir.mkdir(parents=True, exist_ok=True)

    log_file = traces_dir / f"{now.strftime('%Y-%m-%d')}.jsonl"
    with log_file.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")

    return trace_id
