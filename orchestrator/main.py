from __future__ import annotations

import asyncio
import json
import os
import shutil
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
            typer.echo(f"  {q}")
        typer.echo("")

        answer = typer.prompt("Your answer")
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

    from orchestrator.agent_runner import AgentRunner
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

    runner = AgentRunner(workspace=ws)
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
            "\nError: claude CLI not found. Install: npm install -g @anthropic-ai/claude-code",
            err=True,
        )
        raise typer.Exit(1)


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


def _mcp_config_path() -> Path:
    return Path.home() / ".claude" / "mcp.json"


def _detect_jb_command() -> str:
    path = shutil.which("jb") or shutil.which("jibuff")
    if not path:
        typer.echo(
            "Error: jb/jibuff not found on PATH.\n"
            "Install with: pip install jibuff",
            err=True,
        )
        raise typer.Exit(1)
    return path


def _build_mcp_entry() -> dict[str, object]:
    cmd = _detect_jb_command()
    entry: dict[str, object] = {
        "command": cmd,
        "args": ["mcp", "serve"],
    }
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if api_key:
        entry["env"] = {"OPENROUTER_API_KEY": api_key}
    return entry


def _read_mcp_config() -> dict[str, object]:
    path = _mcp_config_path()
    if not path.exists():
        return {"mcpServers": {}}
    try:
        return json.loads(path.read_text(encoding="utf-8"))  # type: ignore[no-any-return]
    except json.JSONDecodeError:
        typer.echo(f"Warning: {path} is malformed JSON. Creating fresh config.", err=True)
        return {"mcpServers": {}}


def _write_mcp_config(config: dict[str, object]) -> None:
    path = _mcp_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


@app.command()
def setup(
    check: Annotated[bool, typer.Option("--check", help="Check current MCP registration")] = False,
    unregister: Annotated[
        bool, typer.Option("--unregister", help="Remove jibuff from MCP config")
    ] = False,
) -> None:
    """Register jibuff as an MCP server in Claude Code."""
    config = _read_mcp_config()
    servers = config.setdefault("mcpServers", {})
    mcp_path = _mcp_config_path()

    if check:
        if "jibuff" in servers:
            entry = servers["jibuff"]
            typer.echo(f"[jibuff setup] Registered in {mcp_path}")
            typer.echo(f"  command: {entry.get('command')}")  # type: ignore[union-attr]
            typer.echo(f"  args: {' '.join(entry.get('args', []))}")  # type: ignore[union-attr]
            env = entry.get("env", {})  # type: ignore[union-attr]
            if env:
                typer.echo(f"  env: {', '.join(env.keys())}")  # type: ignore[union-attr]
        else:
            typer.echo("[jibuff setup] Not registered. Run 'jb setup' to register.")
            raise typer.Exit(1)
        return

    if unregister:
        if "jibuff" not in servers:
            typer.echo("[jibuff setup] Not registered — nothing to remove.")
            return
        del servers["jibuff"]
        _write_mcp_config(config)
        typer.echo(f"[jibuff setup] Removed jibuff from {mcp_path}")
        return

    entry = _build_mcp_entry()

    if "jibuff" in servers and servers["jibuff"] == entry:
        typer.echo("[jibuff setup] Already registered with current config.")
        return

    action = "Updating" if "jibuff" in servers else "Registering"
    servers["jibuff"] = entry
    _write_mcp_config(config)

    typer.echo(f"[jibuff setup] {action} MCP server...")
    typer.echo(f"  command: {entry['command']} {' '.join(entry['args'])}")  # type: ignore[arg-type]
    if "env" in entry:
        typer.echo(f"  env: {', '.join(entry['env'].keys())}")  # type: ignore[union-attr]
    typer.echo(f"  config: {mcp_path}")
    typer.echo("[jibuff setup] Done. Restart Claude Code to pick up changes.")


mcp_app = typer.Typer(help="MCP server commands")
app.add_typer(mcp_app, name="mcp")


@mcp_app.command("serve")
def mcp_serve() -> None:
    """Start the jibuff MCP stdio server."""
    from jibuff_mcp.server import serve as jibuff_serve  # type: ignore[import]
    asyncio.run(jibuff_serve())
