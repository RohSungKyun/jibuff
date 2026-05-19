"""jibuff MCP server — exposes tools for use inside Claude Code sessions.

Tools:
  jibuff_interview  Start or continue an interview session
  jibuff_run        Execute the loop for a given spec/task file
  jibuff_next_task  Claim the next in-session task
  jibuff_finish_task Validate and finish an in-session task
  jibuff_status     Query current loop state
  jibuff_cancel     Halt a running loop

Launch with:
  jibuff mcp serve
  uvx --from jibuff mcp serve
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

try:
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - Windows fallback
    _fcntl = None  # type: ignore[assignment]

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
_PARENT_POLL_INTERVAL_SECONDS = 10.0
_MCP_INTERVIEW_TTL_HOURS = 24
_SESSION_BLOCK_RE = re.compile(
    r"```json jibuff-session\n(?P<json>.*?)\n```",
    flags=re.DOTALL,
)
_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


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
                "session_id": {
                    "type": "string",
                    "description": "Existing MCP interview session id to continue",
                },
                "revision": {
                    "type": "integer",
                    "description": "Expected session revision for optimistic concurrency",
                },
                "mode": {
                    "type": "string",
                    "enum": ["quick", "rtc"],
                    "description": "Workflow mode (default: quick)",
                    "default": "quick",
                },
                "answer": {
                    "oneOf": [
                        {"type": "string"},
                        {
                            "type": "object",
                            "properties": {
                                "value": {"type": "string"},
                                "text": {"type": "string"},
                                "custom": {"type": "string"},
                            },
                        },
                    ],
                    "description": (
                        "Answer to the previous round. Use a/b/c, custom text, "
                        "or an object such as {'value': 'a'}."
                    ),
                },
                "response_format": {
                    "type": "string",
                    "enum": ["text", "json"],
                    "description": "Return legacy text or a structured JSON payload.",
                    "default": "text",
                },
                "action": {
                    "type": "string",
                    "enum": ["continue", "cancel"],
                    "description": "Set to cancel to remove an active MCP interview session",
                    "default": "continue",
                },
                "workspace": {
                    "type": "string",
                    "description": "Workspace used for .jibuff/mcp/interviews state files",
                },
            },
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
        "name": "jibuff_next_task",
        "description": (
            "Claim the next task for in-session execution by the current AI agent. "
            "This does not spawn an external agent CLI."
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
                "worker_id": {
                    "type": "string",
                    "description": "Identifier for the current agent session",
                    "default": "jibuff-agent",
                },
                "response_format": {
                    "type": "string",
                    "enum": ["text", "json"],
                    "description": "Return human-readable text or JSON.",
                    "default": "text",
                },
            },
        },
    },
    {
        "name": "jibuff_finish_task",
        "description": (
            "Validate and finish an in-session task. Marks done on pass, or requeues "
            "and writes a failure report on validator failure."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["task_id", "claim_token"],
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "Task id previously returned by jibuff_next_task",
                },
                "claim_token": {
                    "type": "string",
                    "description": "Claim token previously returned by jibuff_next_task",
                },
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
                "validate": {
                    "type": "boolean",
                    "description": "Run the mode validator stack before marking done",
                    "default": True,
                },
                "response_format": {
                    "type": "string",
                    "enum": ["text", "json"],
                    "description": "Return human-readable text or JSON.",
                    "default": "text",
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


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _interview_dir(workspace: Path) -> Path:
    return workspace / ".jibuff" / "mcp" / "interviews"


def _session_path(workspace: Path, session_id: str) -> Path:
    return _interview_dir(workspace) / f"{session_id}.md"


def _session_lock_path(workspace: Path, session_id: str) -> Path:
    return _interview_dir(workspace) / f"{session_id}.lock"


def _valid_session_id(session_id: str) -> bool:
    return bool(_SESSION_ID_RE.fullmatch(session_id))


def _flock_ex(fd: int) -> None:
    if _fcntl is not None:
        _fcntl.flock(fd, _fcntl.LOCK_EX)


def _flock_un(fd: int) -> None:
    if _fcntl is not None:
        _fcntl.flock(fd, _fcntl.LOCK_UN)


@contextlib.contextmanager
def _session_lock(workspace: Path, session_id: str):  # type: ignore[no-untyped-def]
    lock_path = _session_lock_path(workspace, session_id)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("w", encoding="utf-8") as lock_file:
        _flock_ex(lock_file.fileno())
        try:
            yield
        finally:
            _flock_un(lock_file.fileno())


def _file_mtime(path: Path) -> datetime | None:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, UTC)
    except FileNotFoundError:
        return None


def _cleanup_expired_interview_sessions(workspace: Path) -> None:
    now = _utc_now()
    sessions_dir = _interview_dir(workspace)
    if not sessions_dir.exists():
        return

    for path in sessions_dir.glob("*.md"):
        state = _read_session_state(path)
        expires_at = state.get("expires_at") if state else None
        if not isinstance(expires_at, str):
            fallback_expiry = now - timedelta(hours=_MCP_INTERVIEW_TTL_HOURS)
            mtime = _file_mtime(path)
            if mtime is not None and mtime <= fallback_expiry:
                path.unlink(missing_ok=True)
            continue
        try:
            if datetime.fromisoformat(expires_at) <= now:
                path.unlink(missing_ok=True)
        except ValueError:
            fallback_expiry = now - timedelta(hours=_MCP_INTERVIEW_TTL_HOURS)
            mtime = _file_mtime(path)
            if mtime is not None and mtime <= fallback_expiry:
                path.unlink(missing_ok=True)

    for path in sessions_dir.glob("*.lock"):
        if not path.with_suffix(".md").exists():
            path.unlink(missing_ok=True)


def _read_session_state(path: Path) -> dict[str, object] | None:
    if not path.exists():
        return None
    match = _SESSION_BLOCK_RE.search(path.read_text(encoding="utf-8"))
    if not match:
        return None
    try:
        data = json.loads(match.group("json"))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _render_session_md(state: dict[str, object]) -> str:
    transcript = state.get("transcript", [])
    pending = state.get("pending_question")
    lines = [
        "```json jibuff-session",
        json.dumps(state, indent=2, ensure_ascii=False),
        "```",
        "",
        "# Jibuff MCP Interview Session",
        "",
        f"- Session: {state.get('session_id')}",
        f"- Revision: {state.get('revision')}",
        f"- Mode: {state.get('mode')}",
        f"- Status: {state.get('status')}",
        f"- Expires: {state.get('expires_at')}",
        "",
        "## Original Request",
        "",
        str(state.get("original_request", "")),
        "",
    ]

    if isinstance(pending, dict):
        lines.extend([
            "## Pending Question",
            "",
            str(pending.get("question", "")),
            "",
            "Choices:",
        ])
        choices = pending.get("choices", {})
        if isinstance(choices, dict):
            for key in ("a", "b", "c"):
                if key in choices:
                    lines.append(f"- {key}: {choices[key]}")
        lines.extend(["", str(pending.get("custom_label", "직접 입력")), ""])

    lines.extend(["## Transcript", ""])
    if isinstance(transcript, list):
        for turn in transcript:
            if not isinstance(turn, dict):
                continue
            role = str(turn.get("role", "unknown")).title()
            content = str(turn.get("content", ""))
            lines.extend([f"### {role}", "", content, ""])
    return "\n".join(lines).rstrip() + "\n"


def _question_to_state(question: object) -> dict[str, object] | None:
    if question is None:
        return None
    return {
        "question": getattr(question, "question", ""),
        "choices": dict(getattr(question, "choices", {})),
        "custom_label": getattr(question, "custom_label", "직접 입력"),
    }


def _state_from_session(
    session: object,
    *,
    session_id: str,
    revision: int,
    mode: str,
    original_request: str,
    created_at: str,
) -> dict[str, object]:
    now = _utc_now()
    return {
        "schema_version": 1,
        "session_id": session_id,
        "revision": revision,
        "mode": mode,
        "status": "complete" if getattr(session, "complete", False) else "active",
        "original_request": original_request,
        "rounds": int(getattr(session, "rounds", 0)),
        "transcript": list(getattr(session, "transcript", [])),
        "pending_question": _question_to_state(getattr(session, "pending_question", None)),
        "created_at": created_at,
        "updated_at": now.isoformat(),
        "expires_at": (now + timedelta(hours=_MCP_INTERVIEW_TTL_HOURS)).isoformat(),
    }


def _answer_to_text(answer: object) -> str | None:
    if answer is None:
        return None
    if isinstance(answer, dict):
        for key in ("text", "custom"):
            value = answer.get(key)
            if isinstance(value, str) and value.strip():
                return value
        value = answer.get("value")
        return str(value) if value is not None else ""
    return str(answer)


def _question_payload(question: object, fallback_text: str = "") -> dict[str, object] | None:
    if hasattr(question, "structured_payload"):
        payload = question.structured_payload()
        return payload if isinstance(payload, dict) else None

    if question is None and fallback_text:
        from interview.engine import QuestionBlock

        return QuestionBlock.from_text(fallback_text).structured_payload()

    return None


def _json_response(payload: dict[str, object]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _coerce_response_format(args: dict[str, object]) -> str:
    response_format = str(args.get("response_format", "text"))
    if response_format not in {"text", "json"}:
        raise ValueError("'response_format' must be 'text' or 'json'.")
    return response_format


def _workspace_from_args(args: dict[str, object], cwd: Path) -> Path:
    return Path(str(args["workspace"])) if "workspace" in args else cwd


def _queue_for_workspace(workspace: Path) -> object:
    from orchestrator.task_queue import TaskQueue

    return TaskQueue(
        tasks_file=workspace / _DEFAULT_TASKS,
        status_file=workspace / _DEFAULT_STATUS,
    )


def _runtime_store_for_workspace(workspace: Path, queue: object, mode: str) -> object:
    from orchestrator.runtime_store import RuntimeRunActiveError, RuntimeStore

    active = RuntimeStore.active(workspace, running_only=True)
    if active is not None:
        return active

    try:
        return RuntimeStore.start(
            workspace,
            list(getattr(queue, "_tasks", [])),
            mode=mode,
            worker_count=1,
        )
    except RuntimeRunActiveError:
        active = RuntimeStore.active(workspace, running_only=True)
        if active is not None:
            return active
        raise


def _find_task(queue: object, task_id: str) -> object | None:
    return next(
        (
            task
            for task in getattr(queue, "_tasks", [])
            if getattr(task, "id", "") == task_id
        ),
        None,
    )


def _claimable_tasks(queue: object) -> list[object]:
    return [
        task
        for task in getattr(queue, "_tasks", [])
        if getattr(task, "status", "") == "todo"
    ]


def _task_to_payload(task: object) -> dict[str, object]:
    return {
        "id": getattr(task, "id", ""),
        "description": getattr(task, "description", ""),
        "status": getattr(task, "status", ""),
        "revision": getattr(task, "revision", 0),
        "claimed_by": getattr(task, "claimed_by", None),
        "claimed_at": getattr(task, "claimed_at", None),
        "claim_token": getattr(task, "claim_token", None),
    }


def _internal_next_guide(status: str) -> str:
    guides = {
        "claimed": (
            "Implement only the claimed task in the current AI agent session. "
            "When edits are complete, call jibuff_finish_task with task_id and claim_token."
        ),
        "passed_more": (
            "Task passed validation and is marked done. Call jibuff_next_task to claim the "
            "next task."
        ),
        "passed_done": (
            "All tasks are complete. Run any final project-level verification you need, "
            "then summarize the completed work and artifacts."
        ),
        "failed": (
            "Validation failed and the task was requeued. Use the failure report, fix the "
            "issues in the current session, then call jibuff_next_task to reclaim the task."
        ),
        "empty": (
            "No runnable todo task is available. Check jibuff_status for in-progress or "
            "blocked tasks before starting new work."
        ),
    }
    return guides[status]


def _build_validator_stack(mode: str) -> list[object]:
    from validators.lint import LintValidator
    from validators.security import SecurityValidator
    from validators.tests import PytestValidator
    from validators.types import TypeValidator

    validators: list[object] = [
        LintValidator(),
        TypeValidator(),
        PytestValidator(),
        SecurityValidator(),
    ]
    if mode == "rtc":
        from validators.device import DeviceValidator
        from validators.fallback import FallbackValidator
        from validators.firewall import FirewallValidator
        from validators.network import NetworkValidator

        validators += [
            DeviceValidator(),
            NetworkValidator(),
            FallbackValidator(),
            FirewallValidator(),
        ]
    return validators


def _run_validator_stack(workspace: Path, mode: str) -> dict[str, str]:
    errors: dict[str, str] = {}
    for validator in _build_validator_stack(mode):
        ok, output = validator.run(workspace)
        if not ok:
            errors[str(validator.name)] = output
    return errors


def _session_from_state(state: dict[str, object]) -> object:
    from interview.engine import InterviewSession, QuestionBlock

    mode = str(state["mode"])
    session = InterviewSession(
        mode=get_mode(mode),
        original_request=str(state["original_request"]),
        rounds=int(state.get("rounds", 0)),
        transcript=[
            {"role": str(turn.get("role", "")), "content": str(turn.get("content", ""))}
            for turn in state.get("transcript", [])
            if isinstance(turn, dict)
        ],
    )
    pending = state.get("pending_question")
    if isinstance(pending, dict):
        choices = pending.get("choices", {})
        parsed_choices = (
            {str(k): str(v) for k, v in choices.items()}
            if isinstance(choices, dict)
            else {}
        )
        session.pending_question = QuestionBlock(
            question=str(pending.get("question", "")),
            choices=parsed_choices,
            custom_label=str(pending.get("custom_label", "직접 입력")),
        )
    return session


async def handle_interview(args: dict[str, object], cwd: Path | None = None) -> str:
    """Run one interview step via InterviewEngine.

    MCP calls persist interview state in workspace-local markdown artifacts.
    The CLI path keeps using in-memory sessions and does not create these files.
    """
    workspace = Path(str(args["workspace"])) if "workspace" in args else (cwd or Path.cwd())
    request = str(args.get("request", ""))
    session_id = str(args.get("session_id", ""))
    mode = str(args.get("mode", "quick"))
    answer = args.get("answer")
    response_format = str(args.get("response_format", "text"))
    action = str(args.get("action", "continue"))
    expected_revision = args.get("revision")

    _cleanup_expired_interview_sessions(workspace)

    if session_id and not _valid_session_id(session_id):
        return "Error: invalid 'session_id'. Use only letters, numbers, '.', '_', and '-'."

    if action == "cancel":
        if not session_id:
            return "Error: 'session_id' is required when cancelling an interview session."
        with _session_lock(workspace, session_id):
            state_path = _session_path(workspace, session_id)
            state_path.unlink(missing_ok=True)
        return f"[jibuff interview] cancelled session_id: {session_id}"

    if response_format not in {"text", "json"}:
        return "Error: 'response_format' must be 'text' or 'json'."

    try:
        cfg = get_mode(mode)
    except ValueError as e:
        return f"Error: {e}"

    if answer is not None and not session_id:
        return "Error: 'session_id' is required when providing 'answer'."

    if not request and not session_id:
        return "Error: either 'request' or 'session_id' is required."

    try:
        expected_revision_int = (
            int(expected_revision)
            if expected_revision is not None
            else None
        )
    except (TypeError, ValueError):
        return "Error: 'revision' must be an integer."

    try:
        from interview.engine import InterviewEngine, InterviewSession

        if session_id and expected_revision_int is None:
            return "Error: 'revision' is required when continuing an interview session."

        lock_context = (
            _session_lock(workspace, session_id)
            if session_id
            else contextlib.nullcontext()
        )
        with lock_context:
            engine = InterviewEngine(mode=mode)
            created_at = _utc_now().isoformat()
            revision = 0

            if session_id:
                state_path = _session_path(workspace, session_id)
                state = _read_session_state(state_path)
                if not state:
                    return f"Error: interview session not found or expired: {session_id}"

                revision = int(state.get("revision", 0))
                if expected_revision_int != revision:
                    return (
                        "Error: interview session revision conflict. "
                        f"Expected {expected_revision_int}, found {revision}."
                    )

                mode = str(state["mode"])
                cfg = get_mode(mode)
                engine = InterviewEngine(mode=mode)
                session = _session_from_state(state)
                request = str(state["original_request"])
                created_at = str(state.get("created_at", created_at))
            else:
                session_id = uuid.uuid4().hex
                session = engine.start(request)
                state_path = _session_path(workspace, session_id)

            user_answer = _answer_to_text(answer)
            questions = await engine.step(session, user_answer=user_answer)
            next_revision = revision + 1

            if session.complete:
                tasks_md = engine.generate_tasks_md(session)
                amb = session.last_ambiguity
                amb_str = f"{amb.final_score:.2f}" if amb else "n/a"
                state_path.unlink(missing_ok=True)
                if response_format == "json":
                    return _json_response({
                        "kind": "jibuff.interview.complete",
                        "session_id": session_id,
                        "revision": next_revision,
                        "mode": mode,
                        "status": "complete",
                        "ambiguity_score": amb_str,
                        "tasks_md": tasks_md,
                    })
                return (
                    f"[jibuff interview] mode={mode} | threshold={cfg.ambiguity_threshold}\n"
                    f"session_id: {session_id}\n"
                    f"Interview complete. Ambiguity score: {amb_str}\n\n"
                    f"Generated tasks:\n{tasks_md}"
                )

            # Unit tests often patch InterviewEngine with MagicMock sessions; only
            # real sessions have persistable state.
            if isinstance(session, InterviewSession):
                state = _state_from_session(
                    session,
                    session_id=session_id,
                    revision=next_revision,
                    mode=mode,
                    original_request=request,
                    created_at=created_at,
                )
                _atomic_write_text(state_path, _render_session_md(state))

            if response_format == "json":
                fallback_text = questions[0] if questions else ""
                question = _question_payload(
                    getattr(session, "pending_question", None),
                    fallback_text=fallback_text,
                )
                return _json_response({
                    "kind": "jibuff.interview.pending",
                    "session_id": session_id,
                    "revision": next_revision,
                    "mode": mode,
                    "status": "active",
                    "round": int(getattr(session, "rounds", 0)),
                    "threshold": cfg.ambiguity_threshold,
                    "state_file": str(state_path),
                    "question": question,
                    "fallback_text": fallback_text,
                })

            lines = [
                f"[jibuff interview] mode={mode} | threshold={cfg.ambiguity_threshold}",
                f"session_id: {session_id}",
                f"revision: {next_revision}",
                f"state_file: {state_path}",
                f"Round {session.rounds} — clarifying questions:",
                "Reply with a/b/c, or provide a custom answer.",
                "",
            ]
            lines.extend(f"  {line}" for q in questions for line in q.splitlines())
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


def handle_next_task(args: dict[str, object], cwd: Path) -> str:
    """Claim the next task for execution inside the current agent session."""
    mode = str(args.get("mode", "quick"))
    worker_id = str(args.get("worker_id", "jibuff-agent"))
    workspace = _workspace_from_args(args, cwd)

    try:
        response_format = _coerce_response_format(args)
        get_mode(mode)
    except ValueError as e:
        return f"Error: {e}"

    tasks_file = workspace / _DEFAULT_TASKS
    if not tasks_file.exists():
        return f"Error: tasks file not found at {tasks_file}"

    try:
        from reporters.progress import write_progress
        from orchestrator.runtime_store import RuntimeClaimError

        queue = _queue_for_workspace(workspace)
        storage_dir = workspace / "storage"
        claimable = _claimable_tasks(queue)
        task = None
        claim_token = ""

        if claimable:
            runtime_store = _runtime_store_for_workspace(workspace, queue, mode)
        else:
            runtime_store = None

        for candidate in claimable:
            try:
                if runtime_store is None:  # pragma: no cover - defensive guard
                    break
                claim_token = runtime_store.claim_task(
                    candidate,
                    worker_id=worker_id,
                    claim_token=f"{getattr(candidate, 'id', '')}:{uuid.uuid4().hex}",
                    expected_revision=int(getattr(candidate, "revision", 0)),
                )
            except RuntimeClaimError:
                continue
            queue.mark_in_progress(
                getattr(candidate, "id", ""),
                claimed_by=worker_id,
                claim_token=claim_token,
            )
            task = _find_task(queue, getattr(candidate, "id", "")) or candidate
            break

        if task is None:
            payload = {
                "kind": "jibuff.in_session.empty",
                "mode": mode,
                "status": "empty",
                "summary": queue.summary(),
                "next_guide": _internal_next_guide("passed_done" if queue.all_done() else "empty"),
            }
            if response_format == "json":
                return _json_response(payload)
            return (
                "[jibuff next-task] no runnable task\n"
                f"summary: {queue.summary()}\n"
                f"next: {payload['next_guide']}"
            )

        write_progress(queue, storage_dir)
        payload = {
            "kind": "jibuff.in_session.task",
            "mode": mode,
            "status": "claimed",
            "task": _task_to_payload(task),
            "claim_token": claim_token,
            "next_guide": _internal_next_guide("claimed"),
            "instructions": [
                "Complete only this task in the current AI agent session.",
                "Do not call jibuff_run for this task; that path spawns an external agent.",
                "After edits, call jibuff_finish_task with task_id and claim_token.",
            ],
        }
        if response_format == "json":
            return _json_response(payload)
        return (
            f"[jibuff next-task] claimed {task.id}\n"
            f"task: {task.description}\n"
            f"claim_token: {claim_token}\n"
            f"next: {payload['next_guide']}"
        )
    except Exception as e:
        return f"Error claiming next task: {e}"


def handle_finish_task(args: dict[str, object], cwd: Path) -> str:
    """Validate and finish an in-session task."""
    mode = str(args.get("mode", "quick"))
    workspace = _workspace_from_args(args, cwd)
    task_id = str(args.get("task_id", ""))
    claim_token = str(args.get("claim_token", ""))
    should_validate = bool(args.get("validate", True))

    try:
        response_format = _coerce_response_format(args)
        get_mode(mode)
    except ValueError as e:
        return f"Error: {e}"

    if not task_id or not claim_token:
        return "Error: 'task_id' and 'claim_token' are required."

    tasks_file = workspace / _DEFAULT_TASKS
    if not tasks_file.exists():
        return f"Error: tasks file not found at {tasks_file}"

    try:
        from orchestrator.task_queue import TaskClaimError
        from orchestrator.runtime_store import RuntimeClaimError
        from reporters.failure_report import write_failure_report
        from reporters.progress import write_progress

        queue = _queue_for_workspace(workspace)
        task = _find_task(queue, task_id)
        if task is None:
            return f"Error: task not found: {task_id}"

        storage_dir = workspace / "storage"
        runtime_store = _runtime_store_for_workspace(workspace, queue, mode)
        worker_id = str(getattr(task, "claimed_by", "") or "jibuff-agent")
        if not runtime_store.heartbeat(task_id, claim_token, worker_id=worker_id):
            return f"Error: stale runtime claim token for task {task_id}"

        errors = _run_validator_stack(workspace, mode) if should_validate else {}

        if errors:
            failure_context = write_failure_report(
                task=task,
                validator_errors=errors,
                storage_dir=storage_dir,
            )
            runtime_store.requeue_task(task_id, claim_token, worker_id=worker_id)
            queue.requeue(task_id, claim_token=claim_token)
            write_progress(queue, storage_dir)
            task = _find_task(queue, task_id) or task
            payload = {
                "kind": "jibuff.in_session.finish",
                "mode": mode,
                "status": "failed",
                "task": _task_to_payload(task),
                "validator_errors": errors,
                "failure_context": failure_context,
                "next_guide": _internal_next_guide("failed"),
            }
            if response_format == "json":
                return _json_response(payload)
            return (
                f"[jibuff finish-task] validation failed for {task_id}\n"
                f"failures: {', '.join(errors)}\n"
                f"next: {payload['next_guide']}"
            )

        runtime_store.complete_task(task_id, claim_token, worker_id=worker_id)
        queue.mark_done(task_id, claim_token=claim_token)
        write_progress(queue, storage_dir)
        task = _find_task(queue, task_id) or task
        all_done = queue.all_done()
        if all_done:
            runtime_store.finish("all_done")
        guide_key = "passed_done" if all_done else "passed_more"
        payload = {
            "kind": "jibuff.in_session.finish",
            "mode": mode,
            "status": "passed",
            "task": _task_to_payload(task),
            "summary": queue.summary(),
            "all_done": all_done,
            "next_guide": _internal_next_guide(guide_key),
        }
        if response_format == "json":
            return _json_response(payload)
        return (
            f"[jibuff finish-task] task passed: {task_id}\n"
            f"summary: {queue.summary()}\n"
            f"next: {payload['next_guide']}"
        )
    except RuntimeClaimError as e:
        return f"Error: {e}"
    except TaskClaimError as e:
        return f"Error: {e}"
    except Exception as e:
        return f"Error finishing task: {e}"


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
            text = await handle_interview(arguments, cwd)
        elif name == "jibuff_run":
            text = await asyncio.to_thread(handle_run, arguments, cwd)
        elif name == "jibuff_next_task":
            text = await asyncio.to_thread(handle_next_task, arguments, cwd)
        elif name == "jibuff_finish_task":
            text = await asyncio.to_thread(handle_finish_task, arguments, cwd)
        elif name == "jibuff_status":
            text = await asyncio.to_thread(handle_status, arguments, cwd)
        elif name == "jibuff_cancel":
            text = await asyncio.to_thread(handle_cancel, arguments, cwd)
        else:
            text = f"Unknown tool: {name}"
        return [TextContent(type="text", text=text)]

    return server


async def _watch_parent() -> None:
    """Exit immediately if the parent process dies (we get reparented).

    On Unix the kernel reparents an orphaned child to PID 1 (init/launchd),
    so getppid() changes. Polling this is the cross-platform fallback for
    PR_SET_PDEATHSIG (Linux-only). Without it, the MCP server can persist
    indefinitely if the client (Claude Code, Codex) crashes or is SIGKILLed
    without closing stdin.
    """
    initial_ppid = os.getppid()
    while True:
        await asyncio.sleep(_PARENT_POLL_INTERVAL_SECONDS)
        if os.getppid() != initial_ppid:
            os._exit(0)


async def serve() -> None:
    if not _MCP_AVAILABLE:
        raise ImportError("mcp package not installed. Run: pip install jibuff[mcp]")
    server = create_server()
    async with stdio_server() as (read_stream, write_stream):
        watcher = asyncio.create_task(_watch_parent())
        try:
            await server.run(read_stream, write_stream, server.create_initialization_options())  # type: ignore[attr-defined]
        except (EOFError, BrokenPipeError, ConnectionResetError):
            pass
        finally:
            watcher.cancel()
