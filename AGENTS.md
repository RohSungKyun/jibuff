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
jb run [--mode rtc]       # agent loop against spec/tasks.md
jb status                 # show task progress
jb mcp serve              # start MCP stdio server
```

## Code conventions

- Python 3.12+, ruff + black + mypy strict
- Line length: 100
- Tests: pytest, coverage ≥80% (currently ~97%)
- CLI entry point: `orchestrator/main.py` (omitted from coverage)
- mypy overrides: `mcp.server`, `orchestrator.main` (ignore_errors=true)
- All validators follow `ValidatorProtocol.run(workspace) -> tuple[bool, str]`
- Agent: `Codex --dangerously-skip-permissions -p` subprocess per task

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

- `jb interview` / `jb run` work but depend on Anthropic API key + Codex CLI
- Phase 7 (Phaser mode — game-focused validators) is planned/deferred
