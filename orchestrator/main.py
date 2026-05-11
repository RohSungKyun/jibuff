from __future__ import annotations

import asyncio
import os
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Annotated

import typer

from orchestrator.config import get_mode

app = typer.Typer(
    name="jibuff",
    help="Absorb the jitter in your requirements. Spec first, code second, verify always.",
    no_args_is_help=True,
)


def _build_validators(mode: str, workspace: Path) -> list:  # type: ignore[type-arg]
    from validators.lint import LintValidator
    from validators.security import SecurityValidator
    from validators.tests import PytestValidator
    from validators.types import TypeValidator

    stack = [LintValidator(), TypeValidator(), PytestValidator(), SecurityValidator()]

    if mode == "rtc":
        from validators.device import DeviceValidator
        from validators.fallback import FallbackValidator
        from validators.firewall import FirewallValidator
        from validators.network import NetworkValidator

        stack += [DeviceValidator(), NetworkValidator(), FallbackValidator(), FirewallValidator()]

    return stack


@app.command()
def interview(
    request: Annotated[str, typer.Argument(help="Your initial idea or feature request")],
    mode: Annotated[str, typer.Option(help="Workflow mode: quick | rtc")] = "quick",
    workspace: Annotated[str, typer.Option(help="Workspace directory (default: cwd)")] = "",
) -> None:
    """Clarify requirements through structured dialogue, then generate spec/tasks.md."""
    try:
        get_mode(mode)
    except ValueError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1) from e

    ws = Path(workspace) if workspace else Path.cwd()

    from interview.engine import InterviewEngine

    engine = InterviewEngine(mode=mode)
    session = engine.start(request)

    typer.echo(f"[jibuff interview] mode={mode}")
    typer.echo(f"Request: {request}")
    typer.echo("")

    questions = asyncio.run(engine.step(session))

    while questions:
        for q in questions:
            for line in q.splitlines():
                typer.echo(f"  {line}")
        typer.echo("")

        answer = typer.prompt("Choose a/b/c or type your own answer")
        while not engine.validate_user_answer(session, answer):
            typer.echo("Invalid choice. Enter a, b, c, or type a custom answer.", err=True)
            answer = typer.prompt("Choose a/b/c or type your own answer")
        typer.echo("")
        questions = asyncio.run(engine.step(session, user_answer=answer))

    # Session complete
    ambiguity = session.last_ambiguity
    risk = session.last_risk

    typer.echo("[jibuff] Interview complete.")
    if ambiguity:
        typer.echo(
            f"  Ambiguity score : {ambiguity.final_score:.2f} (threshold {ambiguity.threshold})"
        )
    if risk:
        typer.echo(f"  Risk score      : {risk.score:.2f} (level {risk.level})")
    if session.rounds >= session.mode.max_interview_rounds:
        max_r = session.mode.max_interview_rounds
        typer.echo(f"  (max rounds {max_r} reached — proceeding with open items)")
    typer.echo("")

    # Generate tasks.md
    typer.echo("[jibuff] Generating spec/tasks.md ...")
    tasks_md = engine.generate_tasks_md(session)

    spec_dir = ws / "spec"
    spec_dir.mkdir(parents=True, exist_ok=True)
    tasks_file = spec_dir / "tasks.md"
    tasks_file.write_text(tasks_md + "\n", encoding="utf-8")

    typer.echo(f"[jibuff] spec/tasks.md written ({tasks_file})")
    typer.echo("")
    typer.echo("Next: run 'jb run' to start the agent loop.")


@app.command()
def run(
    mode: Annotated[str, typer.Option(help="Workflow mode: quick | rtc")] = "quick",
    workspace: Annotated[str, typer.Option(help="Workspace directory (default: cwd)")] = "",
    max_iterations: Annotated[int, typer.Option(help="Max loop iterations")] = 30,
    no_commit: Annotated[bool, typer.Option("--no-commit", help="Skip auto git commit")] = False,
    agent: Annotated[
        str,
        typer.Option(
            "--agent",
            help="Agent CLI invocation (e.g. 'claude -p', 'codex exec'). "
            "Overrides JIBUFF_AGENT_CMD and autodetect.",
        ),
    ] = "",
) -> None:
    """Run the agent loop against spec/tasks.md until all tasks are done."""
    try:
        get_mode(mode)
    except ValueError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1) from e

    ws = Path(workspace) if workspace else Path.cwd()
    tasks_file = ws / "spec" / "tasks.md"
    storage_dir = ws / "storage"

    if not tasks_file.exists():
        typer.echo(
            f"Error: spec/tasks.md not found at {tasks_file}\n"
            "Run 'jb interview' first to generate it.",
            err=True,
        )
        raise typer.Exit(1)

    storage_dir.mkdir(parents=True, exist_ok=True)
    status_file = storage_dir / "task_status.json"

    from orchestrator.agent_runner import AgentRunner, resolve_agent_cmd
    from orchestrator.loop_controller import LoopController
    from orchestrator.task_queue import TaskQueue

    queue = TaskQueue(tasks_file=tasks_file, status_file=status_file)
    summary = queue.summary()
    typer.echo(
        f"[jibuff run] mode={mode} | "
        f"todo={summary['todo']} done={summary['done']} "
        f"blocked={summary['blocked']}"
    )
    typer.echo(f"workspace: {ws}")
    typer.echo("")

    if queue.all_done():
        typer.echo("[jibuff] All tasks already complete.")
        return

    try:
        agent_cmd = resolve_agent_cmd(shlex.split(agent) if agent else None)
    except RuntimeError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1) from e

    runner = AgentRunner(workspace=ws, agent_cmd=agent_cmd)
    validators = _build_validators(mode, ws)

    quality_evaluator = None
    cfg = get_mode(mode)
    if cfg.quality_threshold is not None:
        from evaluators.quality import QualityEvaluator

        quality_evaluator = QualityEvaluator(threshold=cfg.quality_threshold)

    from reporters.escalation import prompt_escalation

    controller = LoopController(
        queue=queue,
        runner=runner,
        validators=validators,
        storage_dir=storage_dir,
        workspace=ws,
        max_iterations=max_iterations,
        auto_commit=not no_commit,
        quality_evaluator=quality_evaluator,
        max_quality_retries=cfg.max_quality_retries,
        escalation_handler=prompt_escalation,
        escalation_threshold=3,
        mode=mode,
    )

    result = controller.run()

    typer.echo("")
    typer.echo(f"[jibuff] Loop finished — {result.stopped_reason}")
    typer.echo(f"  completed : {len(result.completed_tasks)}")
    typer.echo(f"  failed    : {len(result.failed_tasks)}")
    typer.echo(f"  iterations: {result.total_iterations}")
    if result.escalated_issues:
        typer.echo(f"  escalated : {len(result.escalated_issues)} issues created")
        for url in result.escalated_issues:
            typer.echo(f"    {url}")

    if result.stopped_reason == "agent_unavailable":
        typer.echo(
            f"\nError: agent CLI '{agent_cmd[0]}' not found on PATH. "
            "Install claude (npm install -g @anthropic-ai/claude-code) or codex, "
            "or set JIBUFF_AGENT_CMD / use --agent to point at your CLI.",
            err=True,
        )
        raise typer.Exit(1)
    if result.stopped_reason == "no_runnable_tasks":
        typer.echo(
            "\nNo todo tasks are runnable. If a previous run was interrupted, "
            "run 'jb recover' after confirming no active jibuff run is still working.",
            err=True,
        )


@app.command()
def status(
    workspace: Annotated[str, typer.Option(help="Workspace directory (default: cwd)")] = "",
) -> None:
    """Show current loop state."""
    ws = Path(workspace) if workspace else Path.cwd()
    tasks_file = ws / "spec" / "tasks.md"
    status_file = ws / "storage" / "task_status.json"

    if not tasks_file.exists():
        typer.echo("[jibuff status] No spec/tasks.md found. Run 'jb interview' first.")
        return

    from orchestrator.task_queue import TaskQueue

    queue = TaskQueue(tasks_file=tasks_file, status_file=status_file)
    summary = queue.summary()

    typer.echo("[jibuff status]")
    typer.echo(
        f"  done={summary['done']} | todo={summary['todo']} | "
        f"in_progress={summary['in_progress']} | blocked={summary['blocked']}"
    )

    last_failure = ws / "storage" / "last_failure.md"
    if last_failure.exists():
        typer.echo("  last failure: present")


@app.command()
def doctor(
    workspace: Annotated[str, typer.Option(help="Workspace directory (default: cwd)")] = "",
) -> None:
    """Check local jibuff readiness and common runtime prerequisites."""
    ws = Path(workspace) if workspace else Path.cwd()
    from orchestrator.ops import run_doctor

    checks = run_doctor(ws)
    typer.echo("[jibuff doctor]")
    failed = 0
    for check in checks:
        marker = "OK" if check.ok else "WARN"
        if not check.ok and check.required:
            marker = "FAIL"
            failed += 1
        typer.echo(f"  [{marker}] {check.name}: {check.detail}")
    if failed:
        raise typer.Exit(1)


@app.command()
def inspect(
    workspace: Annotated[str, typer.Option(help="Workspace directory (default: cwd)")] = "",
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Print machine-readable JSON"),
    ] = False,
) -> None:
    """Inspect tasks, failure artifacts, and MCP interview sessions."""
    import json

    ws = Path(workspace) if workspace else Path.cwd()
    from orchestrator.ops import inspect_workspace

    result = inspect_workspace(ws)
    if json_output:
        payload = {
            "workspace": str(result.workspace),
            "has_tasks": result.has_tasks,
            "summary": result.summary,
            "tasks": result.tasks,
            "last_failure": result.last_failure,
            "open_issue_count": result.open_issue_count,
            "interview_sessions": result.interview_sessions,
            "runtime_run": result.runtime_run,
        }
        typer.echo(json.dumps(payload, indent=2, ensure_ascii=False))
        return

    typer.echo("[jibuff inspect]")
    typer.echo(f"  workspace: {result.workspace}")
    if not result.has_tasks:
        typer.echo("  spec/tasks.md: missing")
    else:
        typer.echo(
            "  tasks: "
            f"done={result.summary.get('done', 0)} | "
            f"todo={result.summary.get('todo', 0)} | "
            f"in_progress={result.summary.get('in_progress', 0)} | "
            f"blocked={result.summary.get('blocked', 0)}"
        )
        for task in result.tasks:
            claim = ""
            if task.get("claimed_by"):
                claim = f" claimed_by={task.get('claimed_by')}"
            typer.echo(
                f"    {task['id']} [{task['status']}] "
                f"rev={task['revision']}{claim} - {task['description']}"
            )
    typer.echo(f"  last_failure: {'present' if result.last_failure else 'none'}")
    typer.echo(f"  open_issues: {result.open_issue_count}")
    if result.runtime_run:
        manifest = result.runtime_run.get("manifest", {})
        workers = result.runtime_run.get("workers", [])
        run_id = result.runtime_run.get("run_id")
        typer.echo(f"  runtime_run: {run_id}")
        if isinstance(manifest, dict):
            typer.echo(f"    status: {manifest.get('status')}")
        if isinstance(workers, list):
            typer.echo(f"    workers: {len(workers)}")
    typer.echo(f"  mcp_interviews: {len(result.interview_sessions)}")
    for session in result.interview_sessions:
        typer.echo(
            f"    {session['session_id']} status={session['status']} "
            f"rev={session['revision']} mode={session['mode']}"
        )


@app.command()
def cleanup(
    workspace: Annotated[str, typer.Option(help="Workspace directory (default: cwd)")] = "",
    failures: Annotated[
        bool,
        typer.Option("--failures", help="Also remove last_failure.md and open_issues.json"),
    ] = False,
) -> None:
    """Remove expired MCP interview sessions and orphan locks."""
    ws = Path(workspace) if workspace else Path.cwd()
    from orchestrator.ops import cleanup_workspace

    removed = cleanup_workspace(ws, include_storage_failures=failures)
    typer.echo("[jibuff cleanup]")
    typer.echo(f"  removed: {len(removed)}")
    for path in removed:
        typer.echo(f"    {path}")


@app.command()
def recover(
    workspace: Annotated[str, typer.Option(help="Workspace directory (default: cwd)")] = "",
    stale_after_minutes: Annotated[
        int,
        typer.Option(help="Requeue in-progress tasks with stale heartbeat older than this"),
    ] = 10,
    force: Annotated[
        bool,
        typer.Option("--force", help="Requeue all in-progress tasks, even with fresh heartbeat"),
    ] = False,
) -> None:
    """Recover from interrupted runs by requeueing stale in-progress tasks."""
    ws = Path(workspace) if workspace else Path.cwd()
    from orchestrator.ops import recover_workspace

    typer.echo("[jibuff recover]")
    for action in recover_workspace(
        ws,
        stale_after_minutes=stale_after_minutes,
        force=force,
    ):
        typer.echo(f"  {action}")


@app.command("setup-skill")
def setup_skill(
    destination: Annotated[
        str,
        typer.Option(help="Codex home directory to install into (default: CODEX_HOME or ~/.codex)"),
    ] = "",
) -> None:
    """Install a thin jibuff SKILL.md wrapper for Codex skill discovery."""
    from orchestrator.ops import install_skill

    target = Path(destination) if destination else None
    skill_file = install_skill(target)
    typer.echo("[jibuff setup-skill]")
    typer.echo(f"  installed: {skill_file}")


def _detect_jb_command() -> str:
    path = shutil.which("jb") or shutil.which("jibuff")
    if not path:
        typer.echo(
            "Error: jb/jibuff not found on PATH.\n" "Install with: pip install jibuff",
            err=True,
        )
        raise typer.Exit(1)
    return path


def _detect_claude_command() -> str:
    path = shutil.which("claude")
    if not path:
        typer.echo(
            "Error: claude CLI not found on PATH.\n"
            "Install Claude Code first: https://docs.claude.com/claude-code",
            err=True,
        )
        raise typer.Exit(1)
    return path


def _run_claude_mcp(args: list[str]) -> subprocess.CompletedProcess[str]:
    claude = _detect_claude_command()
    try:
        return subprocess.run(
            [claude, "mcp", *args],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        typer.echo("Error: claude CLI command timed out after 30s.", err=True)
        raise typer.Exit(1) from None


def _format_cli_error(result: subprocess.CompletedProcess[str]) -> str:
    msg = result.stderr.rstrip() or result.stdout.rstrip()
    if msg:
        return msg
    return f"claude mcp exited with code {result.returncode} (no output)"


def _check_jibuff_registration() -> tuple[bool, str]:
    """Return (is_registered, stdout). Exit with CLI error on unknown failure."""
    result = _run_claude_mcp(["get", "jibuff"])
    if result.returncode == 0:
        return True, result.stdout
    if "no mcp server" in result.stderr.lower():
        return False, ""
    typer.echo(
        f"Error: `claude mcp get jibuff` failed (exit {result.returncode}).\n"
        f"{_format_cli_error(result)}",
        err=True,
    )
    raise typer.Exit(1)


def _registration_matches(stdout: str, jb_cmd: str, api_key: str | None) -> bool:
    """Best-effort check that the current registration matches desired config."""
    if not stdout or jb_cmd not in stdout or "serve" not in stdout:
        return False
    has_env = "OPENROUTER_API_KEY" in stdout
    return has_env == (api_key is not None)


@app.command()
def setup(
    check: Annotated[bool, typer.Option("--check", help="Check current MCP registration")] = False,
    unregister: Annotated[
        bool, typer.Option("--unregister", help="Remove jibuff from MCP config")
    ] = False,
) -> None:
    """Register jibuff as an MCP server in Claude Code (user scope)."""
    if check:
        is_registered, stdout = _check_jibuff_registration()
        if is_registered:
            typer.echo("[jibuff setup] Registered.")
            typer.echo(stdout.rstrip())
        else:
            typer.echo("[jibuff setup] Not registered. Run 'jb setup' to register.")
            raise typer.Exit(1)
        return

    if unregister:
        is_registered, _ = _check_jibuff_registration()
        if not is_registered:
            typer.echo("[jibuff setup] Not registered — nothing to remove.")
            return
        result = _run_claude_mcp(["remove", "jibuff", "-s", "user"])
        if result.returncode != 0:
            typer.echo(_format_cli_error(result), err=True)
            raise typer.Exit(1)
        typer.echo("[jibuff setup] Removed jibuff from Claude Code MCP config.")
        return

    jb_cmd = _detect_jb_command()
    api_key = os.environ.get("OPENROUTER_API_KEY")

    is_registered, current_stdout = _check_jibuff_registration()
    if is_registered and _registration_matches(current_stdout, jb_cmd, api_key):
        typer.echo("[jibuff setup] Already registered with current config.")
        return

    if is_registered:
        remove_result = _run_claude_mcp(["remove", "jibuff", "-s", "user"])
        if remove_result.returncode != 0:
            typer.echo(_format_cli_error(remove_result), err=True)
            raise typer.Exit(1)
        action = "Updating"
    else:
        action = "Registering"

    add_args = ["add", "-s", "user", "jibuff"]
    if api_key:
        add_args += ["-e", f"OPENROUTER_API_KEY={api_key}"]
    add_args += ["--", jb_cmd, "mcp", "serve"]

    add_result = _run_claude_mcp(add_args)
    if add_result.returncode != 0:
        typer.echo(_format_cli_error(add_result), err=True)
        raise typer.Exit(1)

    typer.echo(f"[jibuff setup] {action} MCP server...")
    typer.echo(f"  command: {jb_cmd} mcp serve")
    if api_key:
        typer.echo("  env: OPENROUTER_API_KEY")
    typer.echo("  scope: user")
    typer.echo("[jibuff setup] Done. Restart Claude Code to pick up changes.")


mcp_app = typer.Typer(help="MCP server commands")
app.add_typer(mcp_app, name="mcp")


@mcp_app.command("serve")
def mcp_serve() -> None:
    """Start the jibuff MCP stdio server."""
    from jibuff_mcp.server import serve as jibuff_serve  # type: ignore[import]

    asyncio.run(jibuff_serve())
