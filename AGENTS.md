# jibuff — Codex project context

Spec-driven workflow harness for AI coding agents. "Jitter buffer" for requirements — absorbs ambiguity before it reaches the agent.

## Architecture

6-layer: CLI/MCP → Orchestrator (LoopController, TaskQueue, AgentRunner) → Interview (ambiguity scoring, risk indexer) → Validators → Evaluators (ralph cycle) → Storage (ArtifactStore)

## Modes

- `quick`: ambiguity ≤0.25, no risk gate, no ralph cycle, 4 validators (lint/types/tests/security)
- `rtc`: ambiguity ≤0.15, risk gate <0.4, ralph cycle (quality ≥0.7), 8 validators (+device/network/fallback/firewall)

## Key commands

```bash
jb interview "request"    # interactive Q&A → generates spec/tasks.md
jb run [--mode rtc]       # subprocess path: spawns external agent CLI per task
jb run --internal         # in-session path: prints MCP tool loop guide
jb status                 # show task progress
jb mcp serve              # start MCP stdio server
```

## In-session agent loop (MCP)

When jibuff is available as an MCP server, drive tasks from within the current
agent session — no external subprocess is spawned. State lives under
`.jibuff/runs/<run_id>/` and `storage/`.

```
1. jibuff_interview  request="..."  response_format="json"
   → repeat until status="complete"; spec/tasks.md is written automatically

2. jibuff_run  response_format="json"
   → returns run_id and next_guide; initializes RuntimeStore

3. jibuff_next_task  worker_id="<session-id>"  response_format="json"
   → returns task, claim_token

4. Implement only the claimed task in this session.

5. jibuff_finish_task  task_id="..."  claim_token="..."  worker_id="<session-id>"
   → validates, marks done or requeues; follow next_guide

6. Repeat from step 3 until next_guide indicates all_done.
```

## Code conventions

- Python 3.12+, ruff + black + mypy strict
- Line length: 100
- Tests: pytest, coverage ≥80% (currently ~97%)
- CLI entry point: `orchestrator/main.py` (omitted from coverage)
- mypy overrides: `mcp.server`, `orchestrator.main` (ignore_errors=true)
- All validators follow `ValidatorProtocol.run(workspace) -> tuple[bool, str]`

## CI/CD

- `.github/workflows/ci.yml`: ruff + mypy + pytest on push/PR to main
- `.github/workflows/publish.yml`: tag `v*.*.*` → test → build → PyPI publish
- Branch ruleset: PR + 1 approval required, admin bypass
- CODEOWNERS: `@code-mongster` (review bot)

## Release flow

```bash
# 1. bump version in pyproject.toml
# 2. commit + push to main
# 3. tag and push
git tag v0.x.0 && git push origin v0.x.0
# → GitHub Actions auto-publishes to PyPI
```

## What's NOT wired yet

- `jb interview` / `jb run` work but depend on Anthropic API key or Codex CLI
- Phase 7 (Phaser mode — game-focused validators) is planned/deferred
