"""Security validator — bandit (static analysis) + pip-audit (dependency CVEs)."""

from __future__ import annotations

import subprocess
import tomllib
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
                "-r",
                ".",
                "-ll",  # only medium+high
                "--exit-zero",  # don't exit non-zero on findings; we parse output
                "-f",
                "txt",
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
        failures: list[str] = []
        for cmd in self._pip_audit_commands(workspace):
            result = subprocess.run(
                cmd,
                cwd=workspace,
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                output = (result.stdout + result.stderr).strip()
                failures.append(output)
        if failures:
            return "[pip-audit] Vulnerabilities found:\n" + "\n\n".join(failures)
        return None

    def _pip_audit_commands(self, workspace: Path) -> list[list[str]]:
        base = ["pip-audit", "--progress-spinner=off"]
        requirement_files = [p for p in sorted(workspace.glob("requirements*.txt")) if p.is_file()]
        if requirement_files:
            return [base + ["-r", str(path)] for path in requirement_files]

        pyproject = workspace / "pyproject.toml"
        if self._pyproject_has_dependencies(pyproject):
            return [base + [str(workspace)]]

        return []

    def _pyproject_has_dependencies(self, pyproject: Path) -> bool:
        if not pyproject.is_file():
            return False
        try:
            data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError:
            return False

        project = data.get("project")
        if isinstance(project, dict) and (
            project.get("dependencies") or project.get("optional-dependencies")
        ):
            return True

        tool = data.get("tool")
        if not isinstance(tool, dict):
            return False

        poetry = tool.get("poetry")
        if isinstance(poetry, dict) and (
            poetry.get("dependencies") or poetry.get("group") or poetry.get("dev-dependencies")
        ):
            return True

        pdm = tool.get("pdm")
        return isinstance(pdm, dict) and bool(
            pdm.get("dependencies") or pdm.get("dev-dependencies")
        )
