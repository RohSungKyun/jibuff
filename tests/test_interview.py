"""Phase 1 — Interview Layer tests."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from interview.ambiguity import (
    AmbiguityResult,
    DimensionalScore,
    check_keyword_coverage,
)
from interview.engine import InterviewEngine, InterviewSession
from interview.risk import RiskDimensions, RiskResult, score_to_level

# ---------------------------------------------------------------------------
# Stage 1 — Keyword coverage
# ---------------------------------------------------------------------------


def test_keyword_coverage_all_missing() -> None:
    result = check_keyword_coverage("build me a thing")
    assert result.score == 1.0
    assert result.needs_followup is True
    assert len(result.missing) == len(result.covered) + len(result.missing)


def test_keyword_coverage_partial() -> None:
    text = "admin users need to login, deploy to AWS, and it must complete within 200ms"
    result = check_keyword_coverage(text, mode="quick")
    assert result.score < 1.0
    assert "user_type" in result.covered
    assert "environment" in result.covered
    assert "success_criteria" in result.covered


def test_keyword_coverage_rtc_extra_dimensions() -> None:
    text = "build a WebRTC video call app for mobile and desktop users with fallback to audio"
    quick = check_keyword_coverage(text, mode="quick")
    rtc = check_keyword_coverage(text, mode="rtc")
    # rtc mode has more dimensions, so coverage score can be higher (more to cover)
    assert len(rtc.covered) + len(rtc.missing) > len(quick.covered) + len(quick.missing)


def test_keyword_coverage_no_followup_when_covered() -> None:
    text = (
        "admin users deploy to docker, error handling with retry, "
        "must complete in under 500ms, no external APIs allowed"
    )
    result = check_keyword_coverage(text, mode="quick")
    assert result.score <= 0.6


# ---------------------------------------------------------------------------
# Stage 3 — Dimensional scoring
# ---------------------------------------------------------------------------


def test_dimensional_score_ambiguity_quick() -> None:
    score = DimensionalScore(goal=0.9, constraint=0.8, risk=0.7, environment=0.9, success=0.8)
    ambiguity = score.ambiguity("quick")
    assert 0.0 <= ambiguity <= 1.0
    assert ambiguity < 0.25  # should pass quick threshold


def test_dimensional_score_ambiguity_rtc() -> None:
    score = DimensionalScore(goal=0.9, constraint=0.8, risk=0.6, environment=0.9, success=0.8)
    ambiguity = score.ambiguity("rtc")
    assert 0.0 <= ambiguity <= 1.0


def test_dimensional_score_weakest_dimensions() -> None:
    score = DimensionalScore(goal=0.9, constraint=0.2, risk=0.1, environment=0.9, success=0.8)
    weak = score.weakest_dimensions(n=2)
    assert "risk" in weak
    assert "constraint" in weak


def test_ambiguity_result_passed() -> None:
    text = "admin user deploys to AWS, retry on error, done when tests pass"
    stage1 = check_keyword_coverage(text)
    dims = DimensionalScore(goal=0.9, constraint=0.8, risk=0.8, environment=0.9, success=0.9)
    result = AmbiguityResult.from_stages(
        stage1=stage1,
        contradictions=[],
        dimensions=dims,
        mode="quick",
        threshold=0.25,
    )
    assert result.passed is True
    assert result.final_score <= 0.25


def test_ambiguity_result_failed() -> None:
    stage1 = check_keyword_coverage("build me something")
    dims = DimensionalScore(goal=0.2, constraint=0.1, risk=0.1, environment=0.2, success=0.1)
    result = AmbiguityResult.from_stages(
        stage1=stage1,
        contradictions=["offline" " contradicts " "real-time sync"],
        dimensions=dims,
        mode="rtc",
        threshold=0.15,
    )
    assert result.passed is False


# ---------------------------------------------------------------------------
# RiskIndexer
# ---------------------------------------------------------------------------


def test_score_to_level_boundaries() -> None:
    assert score_to_level(0.0) == 1
    assert score_to_level(0.19) == 1
    assert score_to_level(0.2) == 2
    assert score_to_level(0.39) == 2
    assert score_to_level(0.4) == 3
    assert score_to_level(0.6) == 4
    assert score_to_level(0.8) == 5
    assert score_to_level(1.0) == 5


def test_risk_dimensions_weighted_score() -> None:
    dims = RiskDimensions(security=0.5, network=0.5, state=0.5, external_api=0.5)
    assert dims.weighted_score() == pytest.approx(0.5, abs=0.01)


def test_risk_result_gate_passed() -> None:
    dims = RiskDimensions(security=0.1, network=0.1, state=0.1, external_api=0.1)
    result = RiskResult.from_dimensions(dims, justification="low risk", gate=0.4)
    assert result.gate_passed is True
    assert result.level <= 2


def test_risk_result_gate_failed() -> None:
    dims = RiskDimensions(security=0.9, network=0.8, state=0.7, external_api=0.6)
    result = RiskResult.from_dimensions(dims, justification="high risk", gate=0.4)
    assert result.gate_passed is False
    assert result.level >= 4


def test_risk_result_no_gate() -> None:
    dims = RiskDimensions(security=0.9, network=0.9, state=0.9, external_api=0.9)
    result = RiskResult.from_dimensions(dims, justification="critical but no gate", gate=None)
    assert result.gate_passed is True  # no gate = always passes


def test_risk_highest_dimensions() -> None:
    dims = RiskDimensions(security=0.9, network=0.1, state=0.2, external_api=0.8)
    top = dims.highest_dimensions(n=2)
    assert "security" in top
    assert "external_api" in top


# ---------------------------------------------------------------------------
# InterviewEngine (mocked LLM)
# ---------------------------------------------------------------------------


def _make_engine(mode: str = "quick") -> InterviewEngine:
    mock_client = MagicMock()
    engine = InterviewEngine(mode=mode, client=mock_client)
    return engine


def _mock_call(engine: InterviewEngine, responses: list[str]) -> None:
    """Patch engine._call to return responses in sequence."""
    engine._call = MagicMock(side_effect=responses)  # type: ignore[method-assign]


@pytest.mark.asyncio
async def test_engine_start_creates_session() -> None:
    engine = _make_engine("quick")
    session = engine.start("build a task management CLI")
    assert isinstance(session, InterviewSession)
    assert session.rounds == 0
    assert session.complete is False


@pytest.mark.asyncio
async def test_engine_step_stage1_followup() -> None:
    """If Stage 1 needs followup, engine returns questions without LLM scoring."""
    engine = _make_engine("quick")
    _mock_call(engine, ["What type of users will use this?\nWhat is the expected environment?"])
    session = engine.start("build me a thing")  # triggers stage1 followup
    questions = await engine.step(session)
    assert len(questions) >= 1
    assert session.rounds == 1
    assert session.complete is False


@pytest.mark.asyncio
async def test_engine_step_completes_on_good_input() -> None:
    """Well-specified input should complete after Stage 3 scoring."""
    engine = _make_engine("quick")

    dim_json = json.dumps({
        "goal": 0.95, "constraint": 0.90, "risk": 0.85,
        "environment": 0.90, "success": 0.90, "reasoning": "clear"
    })
    risk_json = json.dumps({
        "security": 0.1, "network": 0.1, "state": 0.1,
        "external_api": 0.1, "justification": "minimal risk"
    })
    _mock_call(engine, ["NONE", dim_json, risk_json])

    well_specified = (
        "admin users deploy to AWS Docker, error handling with retry on timeout, "
        "must complete in under 500ms, no external APIs, done when all tests pass"
    )
    session = engine.start(well_specified)
    questions = await engine.step(session)

    assert session.complete is True
    assert questions == []
    assert session.last_ambiguity is not None
    assert session.last_ambiguity.passed is True


@pytest.mark.asyncio
async def test_engine_max_rounds_stops_loop() -> None:
    """Engine must not loop beyond max_interview_rounds."""
    engine = _make_engine("quick")  # max 5 rounds
    # Always return questions to simulate stuck interview
    engine._call = MagicMock(  # type: ignore[method-assign]
        side_effect=["Question 1?"] * 20
    )
    session = engine.start("build me a thing")
    for _ in range(10):
        await engine.step(session, user_answer="I don't know")
        if session.complete:
            break
    assert session.rounds <= engine.mode_cfg.max_interview_rounds
