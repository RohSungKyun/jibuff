# Constitution

> These principles are non-negotiable. Every implementation decision must pass through this filter first.

---

## 1. Spec First, Always

- No code is written without a finalized spec
- Ambiguity score must reach <= 0.2 before execution begins
- Risk level must be assessed before any implementation starts
- The spec is immutable once execution begins — changes require a new interview cycle

## 2. Code Quality

- All Python code must pass `ruff` (linting) and `black` (formatting) without errors
- Type annotations are required for all public functions and classes
- `mypy` strict mode must pass on all core modules (`orchestrator/`, `validators/`)
- No unused imports, no dead code, no commented-out blocks

## 3. Testing

- Every validator must have a corresponding unit test
- New features require tests before merging
- Coverage threshold: >= 80% for `orchestrator/` and `validators/`
- Tests must be runnable in isolation — no shared mutable state between test cases

## 4. Security

- No secrets, tokens, or credentials in source code or committed files
- All shell commands executed via the orchestrator must be sanitized (no injection vectors)
- `bandit` must pass with no HIGH severity findings
- `pip-audit` must pass before any release

## 5. Failure is Data

- Every QA failure must produce a structured `last_failure.md` artifact
- Failures must be machine-readable (JSON) and human-readable (Markdown)
- The orchestrator must never silently swallow errors — all failures are logged and surfaced
- A failed loop iteration is not a crash — it is an input to the next iteration

## 6. Context Hygiene

- No full conversation logs are passed between loop iterations
- Only structured artifacts are carried forward: `progress.md`, `last_failure.md`, `task_status.json`, `open_issues.json`
- Each agent invocation receives only what it needs for that task — no more

## 7. Mode Integrity

- `quick` mode must never silently escalate to `rtc` gate checks
- Each mode's QA gates are fixed and documented — no runtime gate expansion
- Mode selection is explicit at invocation time, not inferred

## 8. Dependency Policy

- Prefer stdlib over third-party where feasible
- Every new dependency must be justified in `plan.md`
- Pinned versions in `pyproject.toml` or `requirements.lock`
