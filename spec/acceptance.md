# Acceptance Criteria

> A phase is complete only when ALL criteria in that phase pass. Partial completion does not count.

---

## Phase 0 — Foundation

- [ ] `pyproject.toml` exists with ruff, black, mypy, pytest configured
- [ ] `ruff check .` passes with zero errors on empty scaffold
- [ ] `mypy .` passes with zero errors on empty scaffold
- [ ] `pytest` runs and at least one smoke test passes
- [ ] Directory structure matches spec in `plan.md`

---

## Phase 1 — Interview Layer

- [ ] `InterviewEngine` produces at least 3 relevant clarifying questions from a one-sentence input
- [ ] Stage 1 scorer correctly flags missing mandatory keywords (user type, failure conditions, environment)
- [ ] Stage 2 scorer detects at least one intentionally planted contradiction in test fixtures
- [ ] Stage 3 scorer produces a float in [0.0, 1.0] for each dimension with temperature=0 reproducibility
- [ ] `RiskIndexer` produces an integer level in [1, 5] with justification string
- [ ] Interview halts and requests more answers when ambiguity > mode threshold
- [ ] `product_spec.md` auto-generated from interview answers is non-empty and structured
- [ ] `acceptance.md` auto-generated contains at least one measurable criterion per spec section
- [ ] `seed.yaml` is written and cannot be overwritten once created (immutability enforced)

---

## Phase 2 — Execution Layer

- [ ] `TaskQueue` returns the correct next `[ ]` task from a sample `tasks.md`
- [ ] `TaskQueue` returns `None` when all tasks are complete
- [ ] `AgentRunner` invokes Claude Code CLI and captures stdout/stderr
- [ ] Loop controller routes to pass path when all validators return success
- [ ] Loop controller routes to fail path when any validator returns failure
- [ ] On pass: `task_status.json` is updated and `git commit` is created
- [ ] On fail: `last_failure.md` is written with structured failure info
- [ ] Re-queued task injects `last_failure.md` content into next agent context

---

## Phase 3 — Validation Layer (quick gates)

- [ ] `validators/lint.py` fails correctly on a file with known ruff violations
- [ ] `validators/types.py` fails correctly on a file with known type errors
- [ ] `validators/tests.py` fails correctly when coverage drops below threshold
- [ ] `validators/security.py` fails correctly on a file with a known bandit HIGH finding
- [ ] `reporters/failure_report.py` produces valid Markdown and valid JSON from a sample failure
- [ ] `reporters/progress.py` correctly increments completed task count

---

## Phase 4 — Memory/Artifact Layer

- [ ] All artifact schemas are documented with field descriptions and example values
- [ ] `artifact_writer.py` round-trips (write → read) all artifact types without data loss
- [ ] `AgentRunner` context injection test: agent receives only the artifacts scoped to its current task

---

## Phase 5 — RTC Mode

- [ ] `validators/device.py` detects at least one layout/API difference across two Playwright browser targets
- [ ] `validators/network.py` successfully simulates packet loss and verifies fallback path is triggered
- [ ] `validators/fallback.py` fails correctly when graceful degradation is missing from target code
- [ ] `validators/firewall.py` verifies that blocked port scenarios are handled without crash
- [ ] `InterviewEngine` in RTC mode asks at least one question each about: device targets, network conditions, fallback behavior, firewall/proxy constraints
- [ ] Full RTC loop completes on a sample WebSocket feature end-to-end

---

## Phase 6 — MCP Server

- [ ] MCP server starts without error via `jibuff mcp serve`
- [ ] `jibuff_interview` tool accepts a string input and returns structured interview state
- [ ] `jibuff_run` tool accepts a `lineage_id` and returns execution status
- [ ] `jibuff_status` tool returns current loop state from `state.json`
- [ ] `jibuff_cancel` tool halts a running loop and writes cancellation artifact
- [ ] Claude Code can invoke all four tools via MCP in an integration test

---

## Cross-Cutting Criteria (all phases)

- [ ] `bandit` passes with no HIGH severity findings on all source files
- [ ] `pip-audit` passes with no known vulnerabilities
- [ ] All public functions and classes have type annotations
- [ ] No secrets or credentials appear in any committed file
- [ ] `pytest --cov` reports >= 80% coverage on `orchestrator/` and `validators/`
