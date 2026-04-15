"""RiskIndexer — scores risk level (1–5) across four dimensions.

Runs in parallel with Stage 3 ambiguity scoring.
LLM call lives in engine.py; this module provides data models and level mapping.
"""

from dataclasses import dataclass

RISK_WEIGHTS: dict[str, float] = {
    "security": 0.30,      # auth, PII, injection surface, privilege scope
    "network": 0.30,       # real-time sync, WebSocket, API reliance, offline handling
    "state": 0.20,         # shared mutable state, race conditions, session management
    "external_api": 0.20,  # third-party services, rate limits, version stability
}

LEVEL_LABELS: dict[int, str] = {
    1: "Minimal",
    2: "Low",
    3: "Moderate",
    4: "High",
    5: "Critical",
}


def score_to_level(score: float) -> int:
    """Map a weighted risk score [0.0, 1.0] to a level integer [1, 5]."""
    if score < 0.2:
        return 1
    if score < 0.4:
        return 2
    if score < 0.6:
        return 3
    if score < 0.8:
        return 4
    return 5


@dataclass
class RiskDimensions:
    security: float     # 0–1
    network: float      # 0–1
    state: float        # 0–1
    external_api: float # 0–1

    def weighted_score(self) -> float:
        return round(
            self.security * RISK_WEIGHTS["security"]
            + self.network * RISK_WEIGHTS["network"]
            + self.state * RISK_WEIGHTS["state"]
            + self.external_api * RISK_WEIGHTS["external_api"],
            4,
        )

    def highest_dimensions(self, n: int = 2) -> list[str]:
        """Return the n highest-risk dimensions."""
        scores = {
            "security": self.security,
            "network": self.network,
            "state": self.state,
            "external_api": self.external_api,
        }
        return sorted(scores, key=lambda k: scores[k], reverse=True)[:n]


@dataclass
class RiskResult:
    dimensions: RiskDimensions
    score: float     # weighted 0–1
    level: int       # 1–5
    label: str       # Minimal / Low / Moderate / High / Critical
    justification: str
    gate_passed: bool  # True if no gate or score < gate threshold

    @classmethod
    def from_dimensions(
        cls,
        dimensions: RiskDimensions,
        justification: str,
        gate: float | None,
    ) -> "RiskResult":
        score = dimensions.weighted_score()
        level = score_to_level(score)
        passed = gate is None or score < gate
        return cls(
            dimensions=dimensions,
            score=score,
            level=level,
            label=LEVEL_LABELS[level],
            justification=justification,
            gate_passed=passed,
        )
