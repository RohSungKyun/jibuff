"""AgentRunner — invokes a coding agent CLI with isolated, task-scoped context."""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from .task_queue import Task

# Known agents and their non-interactive flag sets. The prompt is appended
# at call time. If a user's installed CLI version uses different flags, set
# JIBUFF_AGENT_CMD to override the entire invocation.
_AGENT_DEFAULTS: dict[str, list[str]] = {
    "claude": ["--dangerously-skip-permissions", "-p"],
    "codex": ["exec"],
}
_DETECT_ORDER: tuple[str, ...] = ("claude", "codex")


def resolve_agent_cmd(override: list[str] | None = None) -> list[str]:
    """Resolve which agent CLI invocation to use for task execution.

    Priority:
      1. ``override`` (e.g. from ``jb run --agent ...``)
      2. ``JIBUFF_AGENT_CMD`` env var (shlex-split into argv)
      3. Auto-detect on PATH in ``_DETECT_ORDER`` (claude first, then codex)

    Raises ``RuntimeError`` if nothing is set and no known CLI is on PATH.
    """
    if override:
        return list(override)

    env_cmd = os.environ.get("JIBUFF_AGENT_CMD")
    if env_cmd:
        return shlex.split(env_cmd)

    for name in _DETECT_ORDER:
        if shutil.which(name):
            return [name, *_AGENT_DEFAULTS[name]]

    raise RuntimeError(
        "No agent CLI found. Install claude or codex on PATH, "
        "or set JIBUFF_AGENT_CMD to your full agent invocation "
        "(e.g. JIBUFF_AGENT_CMD='codex exec --some-flag')."
    )


@dataclass
class RunResult:
    task_id: str
    success: bool
    stdout: str
    stderr: str
    returncode: int
    duration_seconds: float


@dataclass
class AgentRunner:
    workspace: Path
    agent_cmd: list[str] = field(default_factory=resolve_agent_cmd)
    timeout_seconds: int = 300

    def run(self, task: Task, failure_context: str | None = None) -> RunResult:
        """Invoke Claude Code for a single task with isolated context."""
        prompt = self._build_prompt(task, failure_context)
        start = time.monotonic()

        try:
            result = subprocess.run(
                self.agent_cmd + [prompt],
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                cwd=self.workspace,
            )
        except subprocess.TimeoutExpired:
            elapsed = time.monotonic() - start
            return RunResult(
                task_id=task.id,
                success=False,
                stdout="",
                stderr=f"Agent timed out after {self.timeout_seconds}s",
                returncode=-1,
                duration_seconds=elapsed,
            )
        except FileNotFoundError:
            return RunResult(
                task_id=task.id,
                success=False,
                stdout="",
                stderr=f"Agent command not found: {self.agent_cmd[0]}",
                returncode=-1,
                duration_seconds=time.monotonic() - start,
            )

        elapsed = time.monotonic() - start
        return RunResult(
            task_id=task.id,
            success=result.returncode == 0,
            stdout=result.stdout,
            stderr=result.stderr,
            returncode=result.returncode,
            duration_seconds=elapsed,
        )

    # ------------------------------------------------------------------
    # Prompt construction
    # ------------------------------------------------------------------

    def _build_prompt(self, task: Task, failure_context: str | None) -> str:
        parts = [
            f"Task ID: {task.id}",
            f"Task: {task.description}",
            "",
            "Instructions:",
            "- Complete ONLY this task. Do not modify anything outside its scope.",
            "- Follow the project constitution in spec/constitution.md.",
            "- All code must pass ruff, mypy, and pytest after your changes.",
        ]

        if failure_context:
            parts += [
                "",
                "Previous attempt failed. Failure context:",
                failure_context,
                "",
                "Address the failure points above before proceeding.",
            ]

        return "\n".join(parts)
