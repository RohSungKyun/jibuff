"""jibuff MCP server — exposes four tools for use inside Claude Code sessions.

Tools:
  jibuff_interview  Start or continue an interview session
  jibuff_run        Execute the loop for a given spec/task file
  jibuff_status     Query current loop state
  jibuff_cancel     Halt a running loop

Launch with:
  jibuff mcp serve
  uvx --from jibuff mcp serve
"""

from __future__ import annotations

import json
from pathlib import Path

try:
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import TextContent, Tool
    _MCP_AVAILABLE = True
except ImportError:
    _MCP_AVAILABLE = False

from orchestrator.config import get_mode
from storage.artifacts import ArtifactStore

_DEFAULT_STORAGE = Path.home() / ".jibuff" / "storage"
_DEFAULT_TASKS = Path("spec") / "tasks.md"
_DEFAULT_STATUS = Path("storage") / "task_status.json"


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

TOOLS: list[dict[str, object]] = [
    {
        "name": "jibuff_interview",
        "description": (
            "Start or continue a jibuff interview session to clarify requirements. "
            "Returns clarifying questions or signals completion when ambiguity is low enough."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "request": {
                    "type": "string",
                    "description": "The initial idea or feature request to clarify",
                },
                "mode": {
                    "type": "string",
                    "enum": ["quick", "rtc"],
                    "description": "Workflow mode (default: quick)",
                    "default": "quick",
                },
                "answer": {
                    "type": "string",
                    "description": (
                        "Answer to the previous round of questions (omit for first call)"
                    ),
                },
            },
            "required": ["request"],
        },
    },
    {
        "name": "jibuff_run",
        "description": (
            "Execute the jibuff loop for a spec. "
            "Picks up from the next incomplete task in tasks.md."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": ["quick", "rtc"],
                    "description": "Workflow mode (default: quick)",
                    "default": "quick",
                },
                "workspace": {
                    "type": "string",
                    "description": "Absolute path to the workspace directory (default: cwd)",
                },
                "dry_run": {
                    "type": "boolean",
                    "description": "If true, validate setup only without executing agent",
                    "default": False,
                },
            },
        },
    },
    {
        "name": "jibuff_status",
        "description": "Return current jibuff loop state: tasks done/todo/blocked, last failure.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "workspace": {
                    "type": "string",
                    "description": "Absolute path to workspace (default: cwd)",
                },
            },
        },
    },
    {
        "name": "jibuff_cancel",
        "description": "Halt a running jibuff loop and write a cancellation artifact.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "workspace": {
                    "type": "string",
                    "description": "Absolute path to workspace (default: cwd)",
                },
                "reason": {
                    "type": "string",
                    "description": "Why the loop is being cancelled",
                    "default": "user requested",
                },
            },
        },
    },
]


# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------


def handle_interview(args: dict[str, object]) -> str:
    """Run one interview step via InterviewEngine.

    Pass 'answer' to continue an in-progress session (session state is not
    persisted between MCP calls — each call is stateless; use the CLI for
    multi-round interactive sessions).
    """
    request = str(args.get("request", ""))
    mode = str(args.get("mode", "quick"))
    answer = args.get("answer")

    try:
        cfg = get_mode(mode)
    except ValueError as e:
        return f"Error: {e}"

    if not request:
        return "Error: 'request' is required."

    try:
        import asyncio

        from interview.engine import InterviewEngine

        engine = InterviewEngine(mode=mode)
        session = engine.start(request)

        if answer:
            session.transcript.append({"role": "user", "content": str(answer)})

        questions = asyncio.run(engine.step(session, user_answer=None))

        if session.complete:
            tasks_md = engine.generate_tasks_md(session)
            amb = session.last_ambiguity
            amb_str = f"{amb.score:.2f}" if amb else "n/a"
            return (
                f"[jibuff interview] mode={mode} | threshold={cfg.ambiguity_threshold}\n"
                f"Interview complete. Ambiguity score: {amb_str}\n\n"
                f"Generated tasks:\n{tasks_md}"
            )

        lines = [
            f"[jibuff interview] mode={mode} | threshold={cfg.ambiguity_threshold}",
            f"Round {session.rounds} — clarifying questions:",
            "",
        ]
        lines.extend(f"  {q}" for q in questions)
        return "\n".join(lines)

    except Exception as e:
        return f"Error running interview: {e}"


def handle_run(args: dict[str, object], cwd: Path) -> str:
    """Execute the LoopController for the given workspace."""
    mode = str(args.get("mode", "quick"))
    workspace = Path(str(args["workspace"])) if "workspace" in args else cwd
    dry_run = bool(args.get("dry_run", False))

    try:
        cfg = get_mode(mode)
    except ValueError as e:
        return f"Error: {e}"

    tasks_file = workspace / _DEFAULT_TASKS
    if not tasks_file.exists():
        return f"Error: tasks file not found at {tasks_file}"

    if dry_run:
        return (
            f"[jibuff run] dry_run=true | mode={mode}\n"
            f"workspace: {workspace}\n"
            f"tasks: {tasks_file}\n"
            f"ambiguity_threshold: {cfg.ambiguity_threshold}\n"
            "Setup OK — ready to execute."
        )

    try:
        from orchestrator.agent_runner import AgentRunner
        from orchestrator.loop_controller import LoopController
        from orchestrator.task_queue import TaskQueue
        from validators.lint import LintValidator
        from validators.security import SecurityValidator
        from validators.tests import PytestValidator
        from validators.types import TypeValidator

        storage_dir = workspace / "storage"
        storage_dir.mkdir(parents=True, exist_ok=True)
        status_file = storage_dir / "task_status.json"

        queue = TaskQueue(tasks_file=tasks_file, status_file=status_file)
        runner = AgentRunner(workspace=workspace)
        validators = [LintValidator(), TypeValidator(), PytestValidator(), SecurityValidator()]

        if mode == "rtc":
            from validators.device import DeviceValidator
            from validators.fallback import FallbackValidator
            from validators.firewall import FirewallValidator
            from validators.network import NetworkValidator
            validators += [
                DeviceValidator(), NetworkValidator(), FallbackValidator(), FirewallValidator()
            ]

        controller = LoopController(
            queue=queue,
            runner=runner,
            validators=validators,  # type: ignore[arg-type]
            storage_dir=storage_dir,
            workspace=workspace,
        )
        result = controller.run()

        return (
            f"[jibuff run] mode={mode} | {result.stopped_reason}\n"
            f"completed : {len(result.completed_tasks)}\n"
            f"failed    : {len(result.failed_tasks)}\n"
            f"iterations: {result.total_iterations}"
        )

    except Exception as e:
        return f"Error running loop: {e}"


def handle_status(args: dict[str, object], cwd: Path) -> str:
    workspace = Path(str(args["workspace"])) if "workspace" in args else cwd
    storage_dir = workspace / "storage"
    store = ArtifactStore(storage_dir=storage_dir)

    statuses = store.read_task_statuses()
    issues = store.read_issues()
    last_failure = store.read_last_failure()

    if not statuses:
        return f"[jibuff status]\nNo task status found in {storage_dir}"

    counts: dict[str, int] = {}
    for s in statuses:
        counts[s.status] = counts.get(s.status, 0) + 1

    lines = [
        "[jibuff status]",
        f"done: {counts.get('done', 0)} | todo: {counts.get('todo', 0)} "
        f"| in_progress: {counts.get('in_progress', 0)} | blocked: {counts.get('blocked', 0)}",
        f"open issues: {len(issues)}",
    ]
    if last_failure:
        lines.append("last failure: present (use jibuff_cancel to clear)")
    return "\n".join(lines)


def handle_cancel(args: dict[str, object], cwd: Path) -> str:
    workspace = Path(str(args["workspace"])) if "workspace" in args else cwd
    reason = str(args.get("reason", "user requested"))
    storage_dir = workspace / "storage"

    state_file = storage_dir / "state.json"
    if state_file.exists():
        try:
            state = json.loads(state_file.read_text(encoding="utf-8"))
            state["loop"]["status"] = "cancelled"
            state_file.write_text(
                json.dumps(state, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
        except (json.JSONDecodeError, KeyError):
            pass

    cancel_file = storage_dir / "cancelled.md"
    storage_dir.mkdir(parents=True, exist_ok=True)
    cancel_file.write_text(
        f"# Loop Cancelled\n\n**Reason:** {reason}\n",
        encoding="utf-8",
    )
    return f"[jibuff cancel] Loop cancelled.\nReason: {reason}\nArtifact: {cancel_file}"


# ---------------------------------------------------------------------------
# Server entry point
# ---------------------------------------------------------------------------


def create_server() -> object:
    """Create and configure the MCP server instance."""
    if not _MCP_AVAILABLE:
        raise ImportError(
            "mcp package not installed. Run: pip install jibuff[mcp]"
        )

    server = Server("jibuff")
    cwd = Path.cwd()

    @server.list_tools()  # type: ignore[untyped-decorator]
    async def list_tools() -> list[Tool]:
        return [Tool(**t) for t in TOOLS]  # type: ignore[arg-type]

    @server.call_tool()  # type: ignore[untyped-decorator]
    async def call_tool(name: str, arguments: dict[str, object]) -> list[TextContent]:
        if name == "jibuff_interview":
            text = handle_interview(arguments)
        elif name == "jibuff_run":
            text = handle_run(arguments, cwd)
        elif name == "jibuff_status":
            text = handle_status(arguments, cwd)
        elif name == "jibuff_cancel":
            text = handle_cancel(arguments, cwd)
        else:
            text = f"Unknown tool: {name}"
        return [TextContent(type="text", text=text)]

    return server


async def serve() -> None:
    if not _MCP_AVAILABLE:
        raise ImportError("mcp package not installed. Run: pip install jibuff[mcp]")
    server = create_server()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())  # type: ignore[attr-defined]
