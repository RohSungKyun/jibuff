from dataclasses import dataclass


@dataclass(frozen=True)
class ModeConfig:
    name: str
    ambiguity_threshold: float
    risk_gate: float | None  # None = informational only
    max_interview_rounds: int
    coverage_threshold: int  # percent


MODES: dict[str, ModeConfig] = {
    "quick": ModeConfig(
        name="quick",
        ambiguity_threshold=0.25,
        risk_gate=None,
        max_interview_rounds=5,
        coverage_threshold=80,
    ),
    "rtc": ModeConfig(
        name="rtc",
        ambiguity_threshold=0.15,
        risk_gate=0.4,
        max_interview_rounds=15,
        coverage_threshold=80,
    ),
}


def get_mode(name: str) -> ModeConfig:
    if name not in MODES:
        raise ValueError(f"Unknown mode '{name}'. Available modes: {list(MODES)}")
    return MODES[name]
