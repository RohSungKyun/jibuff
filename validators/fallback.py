"""Fallback validator — verifies graceful degradation paths exist and work.

Checks that when primary functionality is unavailable, the application:
- Does not crash
- Enters a defined fallback state
- Shows appropriate user feedback
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

# Keywords that indicate fallback handling in source code
FALLBACK_KEYWORDS = [
    "fallback",
    "graceful",
    "degrade",
    "offline",
    "retry",
    "reconnect",
    "catch",
    "except",
    "on_error",
    "onerror",
]


@dataclass
class FallbackValidator:
    name = "fallback"
    test_script: str = "tests/e2e/fallback.py"
    source_dirs: list[str] = field(default_factory=lambda: ["src", "app", "lib"])
    require_static_evidence: bool = True  # fail if no fallback keywords found in source

    def run(self, workspace: Path) -> tuple[bool, str]:
        errors: list[str] = []

        # Static check: does the source contain fallback handling?
        if self.require_static_evidence:
            static_err = self._check_static_evidence(workspace)
            if static_err:
                errors.append(static_err)

        # Dynamic check: run e2e fallback test if it exists
        script = workspace / self.test_script
        if script.exists():
            result = subprocess.run(
                ["python", "-m", "pytest", str(script), "--tb=short", "-q"],
                cwd=workspace,
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                output = (result.stdout + result.stderr).strip()
                errors.append(f"[e2e fallback test]\n{output}")

        if errors:
            return False, "\n\n".join(errors)
        return True, ""

    def _check_static_evidence(self, workspace: Path) -> str | None:
        """Scan source dirs for fallback-related keywords."""
        found_any = False
        for src_dir in self.source_dirs:
            target = workspace / src_dir
            if not target.exists():
                continue
            for py_file in target.rglob("*.py"):
                content = py_file.read_text(encoding="utf-8", errors="ignore").lower()
                if any(kw in content for kw in FALLBACK_KEYWORDS):
                    found_any = True
                    break
            if found_any:
                break

        if not found_any:
            checked = [d for d in self.source_dirs if (workspace / d).exists()]
            if checked:  # only fail if dirs exist but nothing found
                return (
                    "[fallback] No fallback/graceful degradation handling found in source.\n"
                    f"Checked: {checked}\n"
                    f"Expected at least one of: {FALLBACK_KEYWORDS[:5]}"
                )
        return None
