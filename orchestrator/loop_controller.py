"""LoopController — drives the run → validate → ralph → pass/fail cycle."""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from reporters.failure_report import write_failure_report
from reporters.progress import write_progress
from reporters.tracer import write_trace

from .agent_runner import AgentRunner
from .task_queue import Task, TaskQueue


class QualityEvaluatorProtocol(Protocol):
    def evaluate(self, task: Task, agent_output: str, workspace: Path) -> Any:
        ...


class EscalationHandler(Protocol):
    def __call__(
        self,
        task: Task,
        failure_count: int,
        last_errors: dict[str, str],
        workspace: Path,
    ) -> str | None:
        ...


@dataclass
class LoopResult:
    completed_tasks: list[str] = field(default_factory=list)
    failed_tasks: list[str] = field(default_factory=list)
    total_iterations: int = 0
    stopped_reason: str = ""  # "all_done" | "max_iterations" | "agent_unavailable"
    escalated_issues: list[str] = field(default_factory=list)  # issue URLs


@dataclass
class LoopController:
    queue: TaskQueue
    runner: AgentRunner
    validators: list[ValidatorProtocol]
    storage_dir: Path
    workspace: Path
    max_iterations: int = 30
    auto_commit: bool = True
    quality_evaluator: QualityEvaluatorProtocol | None = None
    max_quality_retries: int = 2
    escalation_handler: EscalationHandler | None = None
    escalation_threshold: int = 3  # consecutive failures before escalation

    def run(self) -> LoopResult:
        result = LoopResult()
        failure_context: str | None = None
        quality_retries: dict[str, int] = {}
        consecutive_failures: dict[str, int] = {}
        last_errors: dict[str, dict[str, str]] = {}

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

            iter_start = time.monotonic()

            # Execute
            run = self.runner.run(task, failure_context=failure_context)

            if not run.success:
                duration = time.monotonic() - iter_start
                errors = {"agent": run.stderr or run.stdout}

                if run.returncode == -1 and "not found" in run.stderr:
                    result.stopped_reason = "agent_unavailable"
                    self.queue.requeue(task.id)
                    write_trace(
                        task, success=False, duration_seconds=duration,
                        stopped_reason="agent_unavailable",
                        iteration=result.total_iterations,
                        storage_dir=self.storage_dir,
                    )
                    break

                failure_context = write_failure_report(
                    task=task, validator_errors=errors,
                    storage_dir=self.storage_dir,
                )
                result.failed_tasks.append(task.id)
                self.queue.requeue(task.id)
                write_progress(self.queue, self.storage_dir)
                write_trace(
                    task, success=False, duration_seconds=duration,
                    validator_errors=errors,
                    iteration=result.total_iterations,
                    storage_dir=self.storage_dir,
                )

                # Track consecutive failures for escalation
                consecutive_failures[task.id] = consecutive_failures.get(task.id, 0) + 1
                last_errors[task.id] = errors
                self._maybe_escalate(
                    task, consecutive_failures, last_errors, result
                )
                continue

            # Validate
            errors = self._run_validators()
            if errors:
                duration = time.monotonic() - iter_start
                failure_context = write_failure_report(
                    task=task, validator_errors=errors,
                    storage_dir=self.storage_dir,
                )
                result.failed_tasks.append(task.id)
                self.queue.requeue(task.id)
                write_progress(self.queue, self.storage_dir)
                write_trace(
                    task, success=False, duration_seconds=duration,
                    validator_errors=errors,
                    iteration=result.total_iterations,
                    storage_dir=self.storage_dir,
                )

                consecutive_failures[task.id] = consecutive_failures.get(task.id, 0) + 1
                last_errors[task.id] = errors
                self._maybe_escalate(
                    task, consecutive_failures, last_errors, result
                )
                continue

            # Ralph cycle — quality gate (only when evaluator is set)
            quality_score: float | None = None
            quality_passed: bool | None = None
            if self.quality_evaluator is not None:
                retries = quality_retries.get(task.id, 0)
                if retries < self.max_quality_retries:
                    quality = self.quality_evaluator.evaluate(
                        task=task,
                        agent_output=run.stdout,
                        workspace=self.workspace,
                    )
                    quality_score = quality.score
                    quality_passed = quality.passed
                    if not quality.passed:
                        duration = time.monotonic() - iter_start
                        quality_retries[task.id] = retries + 1
                        failure_context = quality.context()
                        result.failed_tasks.append(task.id)
                        self.queue.requeue(task.id)
                        write_progress(self.queue, self.storage_dir)
                        write_trace(
                            task, success=False, duration_seconds=duration,
                            quality_score=quality_score,
                            quality_passed=False,
                            iteration=result.total_iterations,
                            storage_dir=self.storage_dir,
                        )
                        continue

            # Pass
            duration = time.monotonic() - iter_start
            failure_context = None
            quality_retries.pop(task.id, None)
            consecutive_failures.pop(task.id, None)
            last_errors.pop(task.id, None)
            self.queue.mark_done(task.id)
            result.completed_tasks.append(task.id)
            write_progress(self.queue, self.storage_dir)
            write_trace(
                task, success=True, duration_seconds=duration,
                quality_score=quality_score,
                quality_passed=quality_passed,
                iteration=result.total_iterations,
                storage_dir=self.storage_dir,
            )

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

    def _maybe_escalate(
        self,
        task: Task,
        consecutive_failures: dict[str, int],
        last_errors: dict[str, dict[str, str]],
        result: LoopResult,
    ) -> None:
        """Escalate to human if consecutive failure threshold is reached."""
        count = consecutive_failures.get(task.id, 0)
        if count < self.escalation_threshold or self.escalation_handler is None:
            return
        errors = last_errors.get(task.id, {})
        url = self.escalation_handler(
            task, count, errors, self.workspace
        )
        if url:
            result.escalated_issues.append(url)
        # Reset counter so we don't re-escalate every iteration
        consecutive_failures[task.id] = 0

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
