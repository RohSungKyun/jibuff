"""Device compatibility validator — Playwright multi-browser/device check.

Launches the target URL (or runs a Playwright test script) across configured
browser/device targets and reports any failures.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_TARGETS = ["chromium", "firefox", "webkit"]


@dataclass
class DeviceValidator:
    name = "device"
    targets: list[str] = field(default_factory=lambda: list(DEFAULT_TARGETS))
    test_script: str = "tests/e2e/device_compat.py"  # relative to workspace

    def run(self, workspace: Path) -> tuple[bool, str]:
        script = workspace / self.test_script
        if not script.exists():
            return True, ""  # no e2e script — skip silently

        errors: list[str] = []
        for target in self.targets:
            result = subprocess.run(
                [
                    "python", "-m", "pytest", str(script),
                    f"--browser={target}",
                    "--tb=short", "--no-header", "-q",
                ],
                cwd=workspace,
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                output = (result.stdout + result.stderr).strip()
                errors.append(f"[{target}]\n{output}")

        if errors:
            return False, "\n\n".join(errors)
        return True, ""
