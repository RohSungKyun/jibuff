"""LoopController — drives the run → validate → pass/fail cycle."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from reporters.failure_report import write_failure_report
from reporters.progress import write_progress

from .agent_runner import AgentRunner
from .task_queue import Task, TaskQueue


@dataclass
class LoopResult:
    completed_tasks: list[str] = field(default_factory=list)
    failed_tasks: list[str] = field(default_factory=list)
    total_iterations: int = 0
    stopped_reason: str = ""  # "all_done" | "max_iterations" | "agent_unavailable"


@dataclass
class LoopController:
    queue: TaskQueue
    runner: AgentRunner
    validators: list[ValidatorProtocol]
    storage_dir: Path
    workspace: Path
    max_iterations: int = 30
    auto_commit: bool = True

    def run(self) -> LoopResult:
        result = LoopResult()
        failure_context: str | None = None

        while not self.queue.all_done():
            if result.total_iterations >= self.max_iterations:
                result.stopped_reason = "max_iterations"
                break

            task = self.queue.next()
            if task is None:
                result.stopped_reason = "all_done"
                break

            result.total_iterations += 1
            self.queue.mark_in_progress(task.id)
            write_progress(self.queue, self.storage_dir)

            # Execute
            run = self.runner.run(task, failure_context=failure_context)

            if not run.success:
                # Agent itself failed (timeout, not found, non-zero exit)
                if run.returncode == -1 and "not found" in run.stderr:
                    result.stopped_reason = "agent_unavailable"
                    self.queue.requeue(task.id)
                    break
                failure_context = write_failure_report(
                    task=task,
                    validator_errors={"agent": run.stderr or run.stdout},
                    storage_dir=self.storage_dir,
                )
                result.failed_tasks.append(task.id)
                self.queue.requeue(task.id)
                write_progress(self.queue, self.storage_dir)
                continue

            # Validate
            errors = self._run_validators()
            if errors:
                failure_context = write_failure_report(
                    task=task,
                    validator_errors=errors,
                    storage_dir=self.storage_dir,
                )
                result.failed_tasks.append(task.id)
                self.queue.requeue(task.id)
                write_progress(self.queue, self.storage_dir)
                continue

            # Pass
            failure_context = None
            self.queue.mark_done(task.id)
            result.completed_tasks.append(task.id)
            write_progress(self.queue, self.storage_dir)

            if self.auto_commit:
                self._git_commit(task)

        if not result.stopped_reason:
            result.stopped_reason = "all_done"

        return result

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _run_validators(self) -> dict[str, str]:
        """Run all validators; collect errors from failing ones."""
        errors: dict[str, str] = {}
        for validator in self.validators:
            ok, output = validator.run(self.workspace)
            if not ok:
                errors[validator.name] = output
        return errors

    def _git_commit(self, task: Task) -> None:
        msg = f"task({task.id}): {task.description[:72]}"
        try:
            subprocess.run(
                ["git", "add", "-A"],
                cwd=self.workspace,
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["git", "commit", "-m", msg],
                cwd=self.workspace,
                check=True,
                capture_output=True,
            )
        except subprocess.CalledProcessError:
            pass  # nothing staged — skip commit


class ValidatorProtocol:
    """Interface that all validators must implement."""

    name: str = "base"

    def run(self, workspace: Path) -> tuple[bool, str]:
        raise NotImplementedError
