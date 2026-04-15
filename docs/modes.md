# Modes

jibuff runs in one of three modes. The mode determines the interview intensity, ambiguity threshold, risk gate, and QA gate stack.

---

## quick

**For:** well-understood, low-risk tasks with a clear scope.

**When to use:**
- Internal utilities, scripts, CLI tools
- Features with no network-critical path
- Tasks where the developer already has a clear mental model

**Interview:**
- Max 5 rounds
- Ambiguity threshold: <= 0.25
- Risk index: informational only, no gate

**QA Gate Stack:**
```
1. ruff + black + isort    (lint + format)
2. mypy                    (type check)
3. pytest --cov            (unit tests + coverage >= 80%)
4. bandit + pip-audit      (security)
```

**Invocation:**
```bash
jibuff run --mode quick "add CSV export to the report module"
```

---

## rtc

**For:** real-time communication features where network conditions, device compatibility, fallback behavior, and firewall handling are first-class concerns.

**When to use:**
- WebRTC / WebSocket features
- Live collaboration or streaming
- Features that must degrade gracefully under poor network conditions
- Features deployed behind enterprise firewalls or proxies

**Interview:**
- Max 15 rounds
- Ambiguity threshold: <= 0.15
- Risk gate: Level 1–2 only (score < 0.4); Level 3+ requires extended risk-focused interview
- Additional question pools: device targets, network assumptions, fallback requirements, firewall constraints

**QA Gate Stack:**
```
1. ruff + black + isort    (lint + format)
2. mypy                    (type check)
3. pytest --cov            (unit tests + coverage >= 80%)
4. bandit + pip-audit      (security)
5. device compatibility    (Playwright, multi-browser/device)
6. network simulation      (throttle, packet loss, disconnect)
7. fallback verification   (graceful degradation path check)
8. firewall/proxy scenario (blocked port handling)
```

Gates run in order. Any gate failure halts the stack and produces a structured failure report.

**Invocation:**
```bash
jibuff run --mode rtc "add WebSocket reconnection with exponential backoff"
```

---

## phaser *(planned)*

**For:** game features built on Phaser.js or similar frameworks where game server state, reconnection handling, and sync failure recovery are critical.

**Status:** not yet implemented. Depends on rtc mode reaching stable validation.

**Planned QA additions over rtc:**
- Game server state simulation (connection loss mid-session)
- Player reconnection flow verification
- State sync failure and recovery
- Latency compensation checks

**Invocation (future):**
```bash
jibuff run --mode phaser "implement player position interpolation"
```

---

## Mode Comparison

| | quick | rtc | phaser |
|--|:-----:|:---:|:------:|
| Max interview rounds | 5 | 15 | 15+ |
| Ambiguity threshold | 0.25 | 0.15 | 0.15 |
| Risk gate | none | < 0.4 | < 0.4 |
| Lint + format | ✓ | ✓ | ✓ |
| Type check | ✓ | ✓ | ✓ |
| Tests + coverage | ✓ | ✓ | ✓ |
| Security scan | ✓ | ✓ | ✓ |
| Device compat | — | ✓ | ✓ |
| Network simulation | — | ✓ | ✓ |
| Fallback verification | — | ✓ | ✓ |
| Firewall scenario | — | ✓ | ✓ |
| Game server state sim | — | — | ✓ |
| Reconnection flow | — | — | ✓ |
| Sync failure recovery | — | — | ✓ |
| Status | stable | stable | planned |

---

## Mode Integrity Rules

- Mode is set explicitly at invocation time — never inferred
- `quick` mode never silently activates `rtc` gates
- Gates within a mode are fixed — no runtime expansion
- Switching modes mid-task requires a new interview cycle
