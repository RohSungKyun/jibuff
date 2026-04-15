# Implementation Plan

## Guiding Principle

Build the smallest thing that proves the loop works, then layer modes on top.

---

## Phase 0 — Foundation

Goal: repo structure, tooling, CI skeleton. No business logic yet.

- [ ] Initialize Python project (`pyproject.toml`, `ruff`, `black`, `mypy`, `pytest`)
- [ ] Define directory structure (`orchestrator/`, `validators/`, `spec/`, `docs/`, `workspace/`)
- [ ] Set up `state.json` schema (loop state persistence)
- [ ] Set up `task_status.json` schema (per-task completion tracking)
- [ ] Write `constitution.md` compliance checklist as a test fixture

---

## Phase 1 — Interview Layer

Goal: take a raw human idea and produce a locked spec document.

- [ ] Implement `InterviewEngine` — question generator backed by Claude API
- [ ] Implement hybrid ambiguity scorer (3 stages: keyword coverage → contradiction detection → LLM dimensional scoring)
- [ ] Implement `RiskIndexer` — LLM-assisted risk dimension scoring (security, network dependency, state complexity, external API surface)
- [ ] Define ambiguity thresholds per mode (`quick`: 0.25, `rtc`: 0.15)
- [ ] Define risk level gate per mode (`rtc`: must score < 3 to proceed)
- [ ] Output: auto-generate `spec/product_spec.md`, `spec/acceptance.md` from interview answers
- [ ] Output: produce locked `seed.yaml` (immutable spec snapshot)

---

## Phase 2 — Execution Layer (quick mode)

Goal: take `tasks.md` and drive Claude Code through each task in isolation.

- [ ] Implement `TaskQueue` — reads `tasks.md`, returns next incomplete task
- [ ] Implement `AgentRunner` — invokes Claude Code CLI with per-task context only
- [ ] Implement loop controller — run → validate → pass/fail → next
- [ ] On pass: mark task complete in `task_status.json`, git commit
- [ ] On fail: write `last_failure.md`, re-queue task with failure context

---

## Phase 3 — Validation Layer (quick mode gates)

Goal: automated QA after each agent execution.

- [ ] `validators/lint.py` — ruff + black + isort
- [ ] `validators/types.py` — mypy
- [ ] `validators/tests.py` — pytest + coverage threshold
- [ ] `validators/security.py` — bandit + pip-audit
- [ ] `reporters/failure_report.py` — structured failure artifact generator
- [ ] `reporters/progress.py` — updates `progress.md` and `task_status.json`

---

## Phase 4 — Memory/Artifact Layer

Goal: structured context handoff between loop iterations.

- [ ] Define artifact schema: `progress.md`, `last_failure.md`, `open_issues.json`, `decision_log.md`, `task_status.json`
- [ ] Implement artifact writer/reader utilities
- [ ] Enforce context hygiene: agent runner injects only task-relevant artifacts, never full logs

---

## Phase 5 — RTC Mode

Goal: extend validation layer with RTC-specific gates.

- [ ] `validators/device.py` — device/browser compatibility checks (via Playwright)
- [ ] `validators/network.py` — network condition simulation (throttle, packet loss, disconnect)
- [ ] `validators/fallback.py` — fallback path verification (graceful degradation checks)
- [ ] `validators/firewall.py` — proxy/firewall scenario simulation
- [ ] E2E validator integration with screenshot diff
- [ ] Extend `InterviewEngine` with RTC-specific question dimensions

---

## Phase 6 — MCP Server

Goal: expose jibuff as an MCP server so Claude Code can invoke it as a tool.

- [ ] Implement `mcp/server.py` — MCP stdio server
- [ ] Expose tools: `jibuff_interview`, `jibuff_run`, `jibuff_status`, `jibuff_cancel`
- [ ] Register server in Claude Code settings

---

## Phase 7 — Phaser Mode *(planned, not scoped)*

Deferred. Depends on Phase 5 completion and real-world RTC mode validation.

---

## Dependency Justification

| Package | Purpose | Phase |
|---------|---------|-------|
| `anthropic` | Claude API for interview + scoring | 1 |
| `typer` | CLI interface | 2 |
| `pytest` | Unit + integration tests | 3 |
| `ruff` | Linting | 3 |
| `black` | Formatting | 3 |
| `mypy` | Type checking | 3 |
| `bandit` | Security scanning | 3 |
| `pip-audit` | Dependency vulnerability scan | 3 |
| `playwright` | E2E + screenshot diff | 5 |
| `mcp` | MCP server/client | 6 |
