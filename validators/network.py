"""Network condition validator — simulates degraded network and verifies behavior.

Checks that the target application handles:
- High latency (500ms+)
- Packet loss simulation (offline toggle)
- Slow connection (throttled bandwidth)
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class NetworkScenario:
    name: str
    latency_ms: int = 0
    offline: bool = False
    description: str = ""


DEFAULT_SCENARIOS: list[NetworkScenario] = [
    NetworkScenario(name="high_latency", latency_ms=500, description="500ms network latency"),
    NetworkScenario(name="offline", offline=True, description="complete network disconnect"),
    NetworkScenario(name="slow_3g", latency_ms=200, description="slow 3G simulation"),
]


@dataclass
class NetworkValidator:
    name = "network"
    scenarios: list[NetworkScenario] = field(default_factory=lambda: list(DEFAULT_SCENARIOS))
    test_script: str = "tests/e2e/network_conditions.py"

    def run(self, workspace: Path) -> tuple[bool, str]:
        script = workspace / self.test_script
        if not script.exists():
            return True, ""  # no network test script — skip

        errors: list[str] = []
        for scenario in self.scenarios:
            result = self._run_scenario(workspace, script, scenario)
            if result:
                errors.append(result)

        if errors:
            return False, "\n\n".join(errors)
        return True, ""

    def _run_scenario(
        self, workspace: Path, script: Path, scenario: NetworkScenario
    ) -> str | None:
        env_args = [f"--scenario={scenario.name}"]
        if scenario.latency_ms:
            env_args.append(f"--latency={scenario.latency_ms}")
        if scenario.offline:
            env_args.append("--offline")

        result = subprocess.run(
            ["python", "-m", "pytest", str(script), *env_args, "--tb=short", "-q"],
            cwd=workspace,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            output = (result.stdout + result.stderr).strip()
            return f"[{scenario.name}] {scenario.description}\n{output}"
        return None
