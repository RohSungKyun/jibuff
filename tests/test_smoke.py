"""Smoke tests — Phase 0 baseline."""

from orchestrator.config import MODES, get_mode


def test_modes_defined() -> None:
    assert "quick" in MODES
    assert "rtc" in MODES


def test_quick_mode_thresholds() -> None:
    cfg = get_mode("quick")
    assert cfg.ambiguity_threshold == 0.25
    assert cfg.risk_gate is None
    assert cfg.max_interview_rounds == 5


def test_rtc_mode_thresholds() -> None:
    cfg = get_mode("rtc")
    assert cfg.ambiguity_threshold == 0.15
    assert cfg.risk_gate == 0.4
    assert cfg.max_interview_rounds == 15


def test_unknown_mode_raises() -> None:
    import pytest
    with pytest.raises(ValueError, match="Unknown mode"):
        get_mode("phaser")
