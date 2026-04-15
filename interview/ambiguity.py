"""3-stage hybrid ambiguity scorer.

Stage 1: Keyword coverage (free, no LLM)
Stage 2: Contradiction detection (cheap LLM call)
Stage 3: Dimensional clarity scoring (LLM, temperature=0)
"""

from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Stage 1 — Keyword coverage
# ---------------------------------------------------------------------------

MANDATORY_DIMENSIONS: dict[str, list[str]] = {
    "user_type": [
        "user", "users", "client", "admin", "role", "persona", "actor", "developer",
    ],
    "failure_conditions": [
        "error", "fail", "timeout", "retry", "fallback", "exception", "crash", "unavailable",
    ],
    "environment": [
        "deploy", "platform", "os", "browser", "server", "cloud", "mobile", "desktop", "docker",
    ],
    "success_criteria": [
        "complete", "done", "pass", "threshold", "metric",
        "measure", "verify", "validate", "accept",
    ],
    "constraints": [
        "must not", "forbidden", "limit", "budget", "deadline", "restrict", "only", "no more than",
    ],
}

RTC_EXTRA_DIMENSIONS: dict[str, list[str]] = {
    "network_conditions": [
        "offline", "latency", "bandwidth", "packet", "reconnect", "disconnect", "jitter",
    ],
    "device_targets": [
        "mobile", "tablet", "desktop", "ios", "android", "chrome", "firefox", "safari",
    ],
    "fallback_behavior": [
        "fallback", "degrade", "graceful", "offline mode", "cache", "retry", "queue",
    ],
    "firewall_proxy": [
        "firewall", "proxy", "vpn", "corporate", "port", "block", "whitelist",
    ],
}


@dataclass
class KeywordCoverageResult:
    score: float  # 0.0 = all covered, 1.0 = nothing covered
    covered: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)

    @property
    def needs_followup(self) -> bool:
        return self.score > 0.6


def check_keyword_coverage(text: str, mode: str = "quick") -> KeywordCoverageResult:
    """Stage 1: check whether mandatory topic areas appear in the input."""
    lower = text.lower()
    dimensions = dict(MANDATORY_DIMENSIONS)
    if mode == "rtc":
        dimensions.update(RTC_EXTRA_DIMENSIONS)

    covered: list[str] = []
    missing: list[str] = []

    for dimension, keywords in dimensions.items():
        if any(kw in lower for kw in keywords):
            covered.append(dimension)
        else:
            missing.append(dimension)

    score = 1.0 - (len(covered) / len(dimensions)) if dimensions else 0.0
    return KeywordCoverageResult(score=round(score, 4), covered=covered, missing=missing)


# ---------------------------------------------------------------------------
# Stage 3 — Dimensional scoring (data model; LLM call lives in engine.py)
# ---------------------------------------------------------------------------

DIMENSION_WEIGHTS: dict[str, dict[str, float]] = {
    "quick": {
        "goal": 0.30,
        "constraint": 0.20,
        "risk": 0.20,
        "environment": 0.15,
        "success": 0.15,
    },
    "rtc": {
        "goal": 0.25,
        "constraint": 0.20,
        "risk": 0.25,
        "environment": 0.15,
        "success": 0.15,
    },
}


@dataclass
class DimensionalScore:
    goal: float        # 0–1: is the goal specific and bounded?
    constraint: float  # 0–1: are limitations and non-goals defined?
    risk: float        # 0–1: are failure modes and edge cases identified?
    environment: float # 0–1: is the deployment/runtime context clear?
    success: float     # 0–1: are completion conditions measurable?

    def ambiguity(self, mode: str) -> float:
        weights = DIMENSION_WEIGHTS.get(mode, DIMENSION_WEIGHTS["quick"])
        clarity = (
            self.goal * weights["goal"]
            + self.constraint * weights["constraint"]
            + self.risk * weights["risk"]
            + self.environment * weights["environment"]
            + self.success * weights["success"]
        )
        return round(1.0 - clarity, 4)

    def weakest_dimensions(self, n: int = 2) -> list[str]:
        """Return the n lowest-scoring dimensions (worst clarity first)."""
        scores = {
            "goal": self.goal,
            "constraint": self.constraint,
            "risk": self.risk,
            "environment": self.environment,
            "success": self.success,
        }
        return sorted(scores, key=lambda k: scores[k])[:n]


# ---------------------------------------------------------------------------
# Composite result
# ---------------------------------------------------------------------------


@dataclass
class AmbiguityResult:
    stage1: KeywordCoverageResult
    contradictions: list[str]            # Stage 2 output
    dimensions: DimensionalScore | None  # Stage 3 output (None if not yet scored)
    final_score: float                   # 0.0–1.0
    threshold: float
    passed: bool                         # final_score <= threshold

    @classmethod
    def from_stages(
        cls,
        stage1: KeywordCoverageResult,
        contradictions: list[str],
        dimensions: DimensionalScore,
        mode: str,
        threshold: float,
    ) -> "AmbiguityResult":
        final = dimensions.ambiguity(mode)
        return cls(
            stage1=stage1,
            contradictions=contradictions,
            dimensions=dimensions,
            final_score=final,
            threshold=threshold,
            passed=final <= threshold,
        )
