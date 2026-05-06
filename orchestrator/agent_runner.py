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

# Known agents and their non-interactive flag sets, in autodetect priority
# order (claude first, then codex). The prompt is appended at call time. If
# a user's installed CLI version uses different flags, set JIBUFF_AGENT_CMD
# to override the entire invocation.
_AGENT_DEFAULTS: dict[str, list[str]] = {
    "claude": ["--dangerously-skip-permissions", "-p"],
    "codex": ["exec", "--dangerously-bypass-approvals-and-sandbox"],
}

_CODEX_NON_INTERACTIVE_FLAGS = {
    "--dangerously-bypass-approvals-and-sandbox",
    "--ask-for-approval",
}


def resolve_agent_cmd(override: list[str] | None = None) -> list[str]:
    """Resolve which agent CLI invocation to use for task execution.

    Priority:
      1. ``override`` (e.g. from ``jb run --agent ...``)
      2. ``JIBUFF_AGENT_CMD`` env var (shlex-split into argv)
      3. Auto-detect on PATH following ``_AGENT_DEFAULTS`` key order

    Raises ``RuntimeError`` if nothing is set and no known CLI is on PATH.
    """
    if override is not None:
        return _normalize_agent_cmd(list(override))

    env_cmd = os.environ.get("JIBUFF_AGENT_CMD")
    if env_cmd:
        return _normalize_agent_cmd(shlex.split(env_cmd))

    for name in _AGENT_DEFAULTS:
        if shutil.which(name):
            return [name, *_AGENT_DEFAULTS[name]]

    raise RuntimeError(
        "No agent CLI found. Install claude or codex on PATH, "
        "or set JIBUFF_AGENT_CMD to your full agent invocation "
        "(e.g. JIBUFF_AGENT_CMD='codex exec --some-flag')."
    )


def _normalize_agent_cmd(cmd: list[str]) -> list[str]:
    """Add required non-interactive flags for known agent invocations."""
    if len(cmd) < 2 or Path(cmd[0]).name != "codex" or cmd[1] != "exec":
        return cmd

    if any(flag in cmd for flag in _CODEX_NON_INTERACTIVE_FLAGS):
        return cmd

    return [cmd[0], cmd[1], "--dangerously-bypass-approvals-and-sandbox", *cmd[2:]]


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
        """Invoke a coding agent for a single task with isolated context."""
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
