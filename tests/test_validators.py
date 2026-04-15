"""Phase 3 — Validation Layer tests."""

from __future__ import annotations

import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

from validators.lint import LintValidator
from validators.security import SecurityValidator
from validators.tests import PytestValidator
from validators.types import TypeValidator

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_run(returncode: int, stdout: str = "", stderr: str = "") -> MagicMock:
    proc = MagicMock()
    proc.returncode = returncode
    proc.stdout = stdout
    proc.stderr = stderr
    return proc


# ---------------------------------------------------------------------------
# LintValidator
# ---------------------------------------------------------------------------


class TestLintValidator:
    def test_passes_when_all_clean(self, tmp_path: Path) -> None:
        with patch("validators.lint.subprocess.run", return_value=_mock_run(0)) as mock:
            ok, out = LintValidator().run(tmp_path)
        assert ok is True
        assert out == ""
        assert mock.call_count == 2  # ruff + black

    def test_fails_on_ruff_error(self, tmp_path: Path) -> None:
        responses = [_mock_run(1, stdout="E501 line too long"), _mock_run(0)]
        with patch("validators.lint.subprocess.run", side_effect=responses):
            ok, out = LintValidator().run(tmp_path)
        assert ok is False
        assert "ruff" in out
        assert "E501" in out

    def test_fails_on_black_error(self, tmp_path: Path) -> None:
        responses = [_mock_run(0), _mock_run(1, stdout="would reformat main.py")]
        with patch("validators.lint.subprocess.run", side_effect=responses):
            ok, out = LintValidator().run(tmp_path)
        assert ok is False
        assert "black" in out

    def test_combines_multiple_failures(self, tmp_path: Path) -> None:
        responses = [
            _mock_run(1, stdout="E501 error"),
            _mock_run(1, stdout="would reformat"),
        ]
        with patch("validators.lint.subprocess.run", side_effect=responses):
            ok, out = LintValidator().run(tmp_path)
        assert ok is False
        assert "ruff" in out
        assert "black" in out


# ---------------------------------------------------------------------------
# TypeValidator
# ---------------------------------------------------------------------------


class TestTypeValidator:
    def test_passes_when_mypy_clean(self, tmp_path: Path) -> None:
        (tmp_path / "orchestrator").mkdir()
        with patch("validators.types.subprocess.run", return_value=_mock_run(0)):
            ok, out = TypeValidator().run(tmp_path)
        assert ok is True
        assert out == ""

    def test_fails_on_mypy_error(self, tmp_path: Path) -> None:
        (tmp_path / "orchestrator").mkdir()
        with patch(
            "validators.types.subprocess.run",
            return_value=_mock_run(1, stdout="error: Incompatible types"),
        ):
            ok, out = TypeValidator().run(tmp_path)
        assert ok is False
        assert "Incompatible" in out

    def test_skips_missing_dirs(self, tmp_path: Path) -> None:
        # No dirs exist — should skip and pass
        with patch("validators.types.subprocess.run") as mock:
            ok, out = TypeValidator(dirs=["nonexistent"]).run(tmp_path)
        assert ok is True
        mock.assert_not_called()

    def test_only_checks_existing_dirs(self, tmp_path: Path) -> None:
        (tmp_path / "orchestrator").mkdir()
        # "interview" doesn't exist
        captured: list[list[str]] = []

        def fake_run(cmd: list[str], **_: object) -> MagicMock:
            captured.append(cmd)
            return _mock_run(0)

        with patch("validators.types.subprocess.run", side_effect=fake_run):
            TypeValidator(dirs=["orchestrator", "interview"]).run(tmp_path)

        assert any("orchestrator" in arg for arg in captured[0])
        assert not any("interview" in arg for arg in captured[0])


# ---------------------------------------------------------------------------
# TestValidator
# ---------------------------------------------------------------------------


class TestTestValidator:
    def test_passes_when_pytest_clean(self, tmp_path: Path) -> None:
        with patch(
            "validators.tests.subprocess.run",
            return_value=_mock_run(0, stdout="5 passed"),
        ):
            ok, out = PytestValidator().run(tmp_path)
        assert ok is True
        assert out == ""

    def test_fails_on_test_failure(self, tmp_path: Path) -> None:
        with patch(
            "validators.tests.subprocess.run",
            return_value=_mock_run(1, stdout="2 failed, 3 passed"),
        ):
            ok, out = PytestValidator().run(tmp_path)
        assert ok is False
        assert "failed" in out

    def test_fails_on_coverage_below_threshold(self, tmp_path: Path) -> None:
        cov_output = textwrap.dedent("""\
            FAIL Required test coverage of 80% not reached. Total coverage: 72%
            TOTAL    150     42    72%
        """)
        with patch(
            "validators.tests.subprocess.run",
            return_value=_mock_run(1, stdout=cov_output),
        ):
            ok, out = PytestValidator(coverage_threshold=80).run(tmp_path)
        assert ok is False
        assert "Coverage below 80%" in out

    def test_threshold_passed_to_pytest(self, tmp_path: Path) -> None:
        captured: list[list[str]] = []

        def fake_run(cmd: list[str], **_: object) -> MagicMock:
            captured.append(cmd)
            return _mock_run(0)

        with patch("validators.tests.subprocess.run", side_effect=fake_run):
            PytestValidator(coverage_threshold=90).run(tmp_path)

        assert any("--cov-fail-under=90" in arg for arg in captured[0])


# ---------------------------------------------------------------------------
# SecurityValidator
# ---------------------------------------------------------------------------


class TestSecurityValidator:
    def test_passes_when_clean(self, tmp_path: Path) -> None:
        responses = [_mock_run(0, stdout="No issues identified."), _mock_run(0)]
        with patch("validators.security.subprocess.run", side_effect=responses):
            ok, out = SecurityValidator().run(tmp_path)
        assert ok is True
        assert out == ""

    def test_fails_on_bandit_high_severity(self, tmp_path: Path) -> None:
        bandit_output = textwrap.dedent("""\
            >> Issue: [B608:hardcoded_sql_expressions]
               Severity: HIGH   Confidence: MEDIUM
        """)
        responses = [_mock_run(0, stdout=bandit_output), _mock_run(0)]
        with patch("validators.security.subprocess.run", side_effect=responses):
            ok, out = SecurityValidator().run(tmp_path)
        assert ok is False
        assert "bandit" in out
        assert "HIGH" in out

    def test_passes_on_bandit_medium_only(self, tmp_path: Path) -> None:
        # MEDIUM severity should not fail (default gate is HIGH)
        bandit_output = "Severity: MEDIUM   Confidence: LOW"
        responses = [_mock_run(0, stdout=bandit_output), _mock_run(0)]
        with patch("validators.security.subprocess.run", side_effect=responses):
            ok, out = SecurityValidator().run(tmp_path)
        assert ok is True

    def test_fails_on_pip_audit_vulnerability(self, tmp_path: Path) -> None:
        audit_output = "requests 2.27.0 GHSA-j8r2-6x86-q33q"
        responses = [
            _mock_run(0, stdout="No issues identified."),
            _mock_run(1, stdout=audit_output),
        ]
        with patch("validators.security.subprocess.run", side_effect=responses):
            ok, out = SecurityValidator().run(tmp_path)
        assert ok is False
        assert "pip-audit" in out
        assert "requests" in out

    def test_combines_both_failures(self, tmp_path: Path) -> None:
        bandit_output = "Severity: HIGH   Confidence: HIGH"
        audit_output = "requests 2.27.0 CVE-2023-xxxx"
        responses = [
            _mock_run(0, stdout=bandit_output),
            _mock_run(1, stdout=audit_output),
        ]
        with patch("validators.security.subprocess.run", side_effect=responses):
            ok, out = SecurityValidator().run(tmp_path)
        assert ok is False
        assert "bandit" in out
        assert "pip-audit" in out
