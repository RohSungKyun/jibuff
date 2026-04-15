"""Type validator — mypy strict mode."""

from __future__ import annotations

import subprocess
from pathlib import Path

# Directories to type-check (must exist in workspace)
CHECK_DIRS = ["orchestrator", "interview", "validators", "reporters"]


class TypeValidator:
    name = "types"

    def __init__(self, dirs: list[str] | None = None) -> None:
        self.dirs = dirs or CHECK_DIRS

    def run(self, workspace: Path) -> tuple[bool, str]:
        targets = [d for d in self.dirs if (workspace / d).exists()]
        if not targets:
            return True, ""

        result = subprocess.run(
            ["python", "-m", "mypy", "--ignore-missing-imports", *targets],
            cwd=workspace,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return False, (result.stdout + result.stderr).strip()
        return True, ""
