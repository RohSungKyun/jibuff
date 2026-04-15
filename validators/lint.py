"""Lint validator — ruff + black + isort."""

from __future__ import annotations

import subprocess
from pathlib import Path


class LintValidator:
    name = "lint"

    def run(self, workspace: Path) -> tuple[bool, str]:
        errors: list[str] = []

        for cmd, label in [
            (["ruff", "check", "."], "ruff"),
            (["black", "--check", "."], "black"),
        ]:
            result = subprocess.run(
                cmd,
                cwd=workspace,
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                output = (result.stdout + result.stderr).strip()
                errors.append(f"[{label}]\n{output}")

        if errors:
            return False, "\n\n".join(errors)
        return True, ""
