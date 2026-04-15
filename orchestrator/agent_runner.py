"""AgentRunner — invokes Claude Code CLI with isolated, task-scoped context."""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from .task_queue import Task


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
    agent_cmd: list[str] = field(
        default_factory=lambda: ["claude", "--dangerously-skip-permissions", "-p"]
    )
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
