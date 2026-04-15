# Product Spec

## What is jibuff?

jibuff is a **spec-driven workflow harness** for AI coding agents.

Named after the *jitter buffer* — a network component that absorbs timing irregularities before they reach the decoder — jibuff absorbs the ambiguity in human requirements before they reach the AI coding agent.

> Absorb the jitter in your requirements. Spec first, code second, verify always.

---

## Problem

AI coding agents fail not because of capability gaps, but because of input quality gaps.

| Root Cause | What Happens |
|-----------|--------------|
| Vague requirements | Agent guesses intent, builds on wrong assumptions |
| No risk assessment | Critical failure modes discovered after implementation |
| No verification loop | "Looks good" replaces actual QA |
| Context bleed | Long sessions degrade output quality over time |

---

## Solution

jibuff sits between the human and the AI coding agent. It enforces a structured pipeline:

```
[Human Idea]
     ↓
[Interview] — ambiguity scoring + risk level indexing
     ↓
[Spec Documents] — immutable once locked
     ↓
[Execution] — AI coding agent (Claude Code)
     ↓
[QA Gate] — mode-specific validation
     ↓
Pass → commit + next task
Fail → failure report → re-execution loop
```

---

## Modes

### quick
Lightweight mode for well-understood, low-risk tasks.

- Short interview (3–5 questions)
- Ambiguity threshold: <= 0.25
- QA gate: ruff + black + mypy + pytest
- No network or device simulation

### rtc
High-intensity mode for real-time communication features.

- Full interview (up to 15 questions)
- Ambiguity threshold: <= 0.15
- Risk level gate: must score < 3 to proceed
- QA gate: all quick gates + device compatibility + network simulation + fallback verification + firewall/proxy handling

### phaser *(planned)*
Game-focused mode for Phaser.js / game engine features.

- Full interview with game-specific dimensions
- QA gate: all rtc gates + game server state simulation + reconnection handling + sync failure recovery

---

## Target Users

- Individual developers using Claude Code or similar AI coding agents
- Teams building RTC applications (WebRTC, WebSocket, live collaboration)
- Game developers using Phaser or similar frameworks
- Anyone who wants spec-first discipline without heavyweight tooling

---

## Non-Goals

- jibuff does not replace the AI coding agent — it wraps it
- jibuff does not generate UI or visual assets
- jibuff does not manage cloud infrastructure
- jibuff does not replace a full CI/CD pipeline — it is a local development harness

---

## Key Design Constraints

- Python >= 3.12
- Primary agent: Claude Code (via CLI)
- MCP server support for tool-based integration
- All state stored locally in flat files — no external database required
- Must work offline except for AI agent API calls
