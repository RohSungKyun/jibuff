# Tasks

Status legend: `[ ]` todo · `[~]` in progress · `[x]` done · `[!]` blocked

---

## Phase 0 — Foundation

- [ ] P0-01: Initialize `pyproject.toml` with ruff, black, mypy, pytest config
- [ ] P0-02: Create directory skeleton (`orchestrator/`, `validators/`, `reporters/`, `storage/`, `mcp/`, `workspace/`)
- [ ] P0-03: Define `storage/state.json` schema
- [ ] P0-04: Define `storage/task_status.json` schema
- [ ] P0-05: Set up `pytest` baseline with one passing smoke test

---

## Phase 1 — Interview Layer

- [ ] P1-01: Implement `InterviewEngine` class with question generation via Claude API
- [ ] P1-02: Implement Stage 1 ambiguity scorer — keyword coverage check
- [ ] P1-03: Implement Stage 2 ambiguity scorer — contradiction/conflict detection
- [ ] P1-04: Implement Stage 3 ambiguity scorer — LLM dimensional scoring (goal / constraint / risk / environment / success criteria)
- [ ] P1-05: Implement `RiskIndexer` — score security, network dependency, state complexity, external API surface
- [ ] P1-06: Define per-mode thresholds (`quick`: ambiguity <= 0.25 / `rtc`: ambiguity <= 0.15, risk < 3)
- [ ] P1-07: Auto-generate `spec/product_spec.md` from interview output
- [ ] P1-08: Auto-generate `spec/acceptance.md` from interview output
- [ ] P1-09: Produce locked `seed.yaml` snapshot at end of interview

---

## Phase 2 — Execution Layer

- [ ] P2-01: Implement `TaskQueue` — parse `tasks.md`, return next `[ ]` task
- [ ] P2-02: Implement `AgentRunner` — invoke Claude Code CLI with isolated context
- [ ] P2-03: Implement loop controller — run → validate → pass/fail routing
- [ ] P2-04: On pass: update `task_status.json`, run `git commit`
- [ ] P2-05: On fail: write `storage/last_failure.md`, re-queue task with failure context injected

---

## Phase 3 — Validation Layer (quick gates)

- [ ] P3-01: `validators/lint.py` — ruff + black + isort runner
- [ ] P3-02: `validators/types.py` — mypy runner
- [ ] P3-03: `validators/tests.py` — pytest runner with coverage threshold check
- [ ] P3-04: `validators/security.py` — bandit + pip-audit runner
- [ ] P3-05: `reporters/failure_report.py` — generate `last_failure.md` from validator output
- [ ] P3-06: `reporters/progress.py` — update `progress.md` and `task_status.json`

---

## Phase 4 — Memory/Artifact Layer

- [ ] P4-01: Define and document artifact schemas (`progress.md`, `last_failure.md`, `open_issues.json`, `decision_log.md`)
- [ ] P4-02: Implement `storage/artifact_writer.py` — write/read structured artifacts
- [ ] P4-03: Enforce context injection rules in `AgentRunner` — task-scoped artifacts only

---

## Phase 5 — RTC Mode

- [ ] P5-01: `validators/device.py` — Playwright-based device/browser compatibility
- [ ] P5-02: `validators/network.py` — network condition simulation (throttle, loss, disconnect)
- [ ] P5-03: `validators/fallback.py` — graceful degradation path verification
- [ ] P5-04: `validators/firewall.py` — proxy/firewall scenario simulation
- [ ] P5-05: Extend `InterviewEngine` with RTC-specific question dimensions
- [ ] P5-06: Wire RTC gate stack into loop controller

---

## Phase 6 — MCP Server

- [ ] P6-01: Implement `mcp/server.py` — MCP stdio server skeleton
- [ ] P6-02: Expose `jibuff_interview` tool
- [ ] P6-03: Expose `jibuff_run` tool
- [ ] P6-04: Expose `jibuff_status` tool
- [ ] P6-05: Expose `jibuff_cancel` tool
- [ ] P6-06: Document Claude Code MCP registration steps

---

## Phase 7 — Phaser Mode *(planned)*

- [ ] P7-01: Define Phaser-specific QA dimensions (deferred)
