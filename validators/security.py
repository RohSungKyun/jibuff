"""Security validator — bandit (static analysis) + pip-audit (dependency CVEs)."""

from __future__ import annotations

import subprocess
from pathlib import Path


class SecurityValidator:
    name = "security"

    def __init__(self, bandit_severity: str = "HIGH") -> None:
        # Only fail on HIGH or above by default
        self.bandit_severity = bandit_severity

    def run(self, workspace: Path) -> tuple[bool, str]:
        errors: list[str] = []

        # bandit — static security analysis
        bandit_result = self._run_bandit(workspace)
        if bandit_result:
            errors.append(bandit_result)

        # pip-audit — dependency vulnerability scan
        audit_result = self._run_pip_audit(workspace)
        if audit_result:
            errors.append(audit_result)

        if errors:
            return False, "\n\n".join(errors)
        return True, ""

    def _run_bandit(self, workspace: Path) -> str | None:
        result = subprocess.run(
            [
                "bandit",
                "-r", ".",
                "-ll",              # only medium+high
                "--exit-zero",      # don't exit non-zero on findings; we parse output
                "-f", "txt",
            ],
            cwd=workspace,
            capture_output=True,
            text=True,
        )
        output = (result.stdout + result.stderr).strip()

        # Fail if HIGH severity issues found
        if f"Severity: {self.bandit_severity}" in output:
            return f"[bandit] HIGH severity finding(s):\n{output}"
        return None

    def _run_pip_audit(self, workspace: Path) -> str | None:
        result = subprocess.run(
            ["pip-audit", "--progress-spinner=off"],
            cwd=workspace,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            output = (result.stdout + result.stderr).strip()
            return f"[pip-audit] Vulnerabilities found:\n{output}"
        return None
