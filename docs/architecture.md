# Architecture

## Overview

jibuff is a **6-layer workflow harness** that sits between a human developer and an AI coding agent. Its job is to absorb ambiguity before it reaches the agent, and to verify output before it reaches the codebase.

```
┌─────────────────────────────────────────┐
│              Human Input                │
│         (vague idea / feature)          │
└──────────────────┬──────────────────────┘
                   ↓
┌─────────────────────────────────────────┐
│           1. Spec Layer                 │
│  constitution / product_spec / plan /   │
│  tasks / acceptance / seed.yaml         │
└──────────────────┬──────────────────────┘
                   ↓
┌─────────────────────────────────────────┐
│           2. Interview Layer            │
│  InterviewEngine → ambiguity scorer     │
│  RiskIndexer → risk level gate          │
└──────────────────┬──────────────────────┘
                   ↓ (ambiguity <= threshold AND risk < gate)
┌─────────────────────────────────────────┐
│           3. Execution Layer            │
│  TaskQueue → AgentRunner (Claude Code)  │
│  loop controller                        │
└──────────────────┬──────────────────────┘
                   ↓
┌─────────────────────────────────────────┐
│           4. Validation Layer           │
│  mode-specific QA gate stack            │
│  lint / types / tests / security /      │
│  device / network / fallback / firewall │
└──────────────────┬──────────────────────┘
         pass ↙           ↘ fail
┌──────────────┐   ┌──────────────────────┐
│  git commit  │   │  failure_report.py   │
│  next task   │   │  → re-queue task     │
└──────────────┘   └──────────────────────┘
                   ↓
┌─────────────────────────────────────────┐
│           5. Memory/Artifact Layer      │
│  progress.md / last_failure.md /        │
│  task_status.json / open_issues.json /  │
│  decision_log.md                        │
└─────────────────────────────────────────┘
                   ↓
┌─────────────────────────────────────────┐
│           6. Sandbox/Infra Layer        │
│  git worktree isolation                 │
│  (Docker: optional)                     │
└─────────────────────────────────────────┘
```

---

## Directory Structure

```
jibuff/
├── orchestrator/
│   ├── main.py              # CLI entrypoint
│   ├── config.py            # mode config, thresholds
│   ├── task_queue.py        # reads tasks.md, returns next task
│   ├── agent_runner.py      # invokes Claude Code CLI
│   └── loop_controller.py   # pass/fail routing, re-queue logic
├── interview/
│   ├── engine.py            # InterviewEngine
│   ├── ambiguity.py         # 3-stage hybrid scorer
│   └── risk.py              # RiskIndexer
├── validators/
│   ├── lint.py              # ruff + black + isort
│   ├── types.py             # mypy
│   ├── tests.py             # pytest + coverage
│   ├── security.py          # bandit + pip-audit
│   ├── device.py            # Playwright device compat [rtc]
│   ├── network.py           # network condition simulation [rtc]
│   ├── fallback.py          # graceful degradation checks [rtc]
│   └── firewall.py          # proxy/firewall scenario sim [rtc]
├── reporters/
│   ├── failure_report.py    # structured failure artifact
│   └── progress.py          # progress.md + task_status.json
├── storage/
│   ├── state.json           # loop state
│   ├── task_status.json     # per-task completion
│   ├── progress.md          # human-readable progress
│   ├── last_failure.md      # last QA failure detail
│   ├── open_issues.json     # open issues from failures
│   └── decision_log.md      # key decisions made during loop
├── mcp/
│   └── server.py            # MCP stdio server
├── spec/                    # spec documents (this repo's own spec)
├── docs/                    # design documentation
├── workspace/               # git worktree or repo clone target
└── pyproject.toml
```

---

## Layer Details

### 1. Spec Layer

All spec documents live in `spec/`. They are generated from the interview phase and locked as `seed.yaml` before execution begins.

- `constitution.md` — immutable principles, checked before every phase
- `product_spec.md` — what is being built
- `plan.md` — implementation phases and dependency justification
- `tasks.md` — atomic, checkable unit tasks
- `acceptance.md` — measurable completion criteria per phase
- `seed.yaml` — immutable snapshot of the above, write-once

### 2. Interview Layer

The `InterviewEngine` takes a raw human input and drives a structured dialogue until ambiguity reaches the mode threshold.

Three-stage hybrid scoring:

```
Stage 1 (free):     Keyword coverage check
                    — are mandatory dimensions mentioned at all?
Stage 2 (cheap):    Contradiction detection
                    — do any answers conflict with each other?
Stage 3 (LLM):      Dimensional clarity scoring
                    — 5 dimensions scored 0–1, weighted sum → ambiguity score
```

The `RiskIndexer` runs in parallel with Stage 3 and produces a risk level (1–5) across four dimensions: security exposure, network dependency, state complexity, external API surface.

### 3. Execution Layer

The loop controller drives one task at a time:

```python
while task := task_queue.next():
    agent_runner.run(task, artifacts=load_artifacts(task))
    result = validator_stack.run(mode)
    if result.passed:
        mark_complete(task)
        git_commit(task)
    else:
        write_failure_report(result)
        task_queue.requeue(task, failure_context=result)
```

Each agent invocation is a **fresh process** — no session carryover. Context is injected via structured artifacts only.

### 4. Validation Layer

QA gates are fixed per mode. No runtime expansion.

| Gate | quick | rtc |
|------|:-----:|:---:|
| ruff + black + isort | ✓ | ✓ |
| mypy | ✓ | ✓ |
| pytest + coverage | ✓ | ✓ |
| bandit + pip-audit | ✓ | ✓ |
| Playwright device compat | — | ✓ |
| Network simulation | — | ✓ |
| Fallback verification | — | ✓ |
| Firewall/proxy scenario | — | ✓ |

### 5. Memory/Artifact Layer

Structured artifacts replace conversation history as the inter-iteration context carrier.

| Artifact | Format | Purpose |
|----------|--------|---------|
| `progress.md` | Markdown | Human-readable loop state |
| `last_failure.md` | Markdown | Failure detail for re-queue |
| `task_status.json` | JSON | Machine-readable task completion |
| `open_issues.json` | JSON | Unresolved issues accumulator |
| `decision_log.md` | Markdown | Key decisions and rationale |

### 6. Sandbox/Infra Layer

Default: **git worktree isolation** — each execution loop works in a dedicated worktree branch.

Optional: Docker container for full process isolation (recommended for RTC network simulation validators).

---

## MCP Integration

jibuff exposes four MCP tools for use inside Claude Code sessions:

| Tool | Description |
|------|-------------|
| `jibuff_interview` | Start or continue an interview session |
| `jibuff_run` | Execute the loop for a given spec |
| `jibuff_status` | Query current loop state |
| `jibuff_cancel` | Halt a running loop |

MCP server is launched via:
```bash
jibuff mcp serve
```

And registered in Claude Code settings as a stdio MCP server.
