"""Test validator — pytest + coverage threshold."""

from __future__ import annotations

import subprocess
from pathlib import Path


class PytestValidator:
    name = "tests"

    def __init__(self, coverage_threshold: int = 80) -> None:
        self.coverage_threshold = coverage_threshold

    def run(self, workspace: Path) -> tuple[bool, str]:
        result = subprocess.run(
            [
                "python", "-m", "pytest",
                "--tb=short",
                "--no-header",
                f"--cov-fail-under={self.coverage_threshold}",
            ],
            cwd=workspace,
            capture_output=True,
            text=True,
        )

        output = (result.stdout + result.stderr).strip()

        if result.returncode != 0:
            # Surface coverage failure separately for clearer failure context
            if "FAIL Required test coverage" in output or "Coverage failure" in output:
                cov_line = self._extract_coverage_line(output)
                return False, f"Coverage below {self.coverage_threshold}%.\n{cov_line}\n\n{output}"
            return False, output

        return True, ""

    @staticmethod
    def _extract_coverage_line(output: str) -> str:
        for line in output.splitlines():
            if "TOTAL" in line or "coverage" in line.lower():
                return line.strip()
        return ""
