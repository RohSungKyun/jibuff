"""Firewall/proxy validator — verifies application handles blocked ports and proxy scenarios.

Checks that the application:
- Handles connection refused on expected ports
- Supports HTTP_PROXY / HTTPS_PROXY environment variables
- Does not crash when corporate firewall blocks WebSocket upgrade
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class FirewallScenario:
    name: str
    description: str
    env: dict[str, str] = field(default_factory=dict)


DEFAULT_SCENARIOS: list[FirewallScenario] = [
    FirewallScenario(
        name="websocket_blocked",
        description="WebSocket upgrade blocked (falls back to HTTP polling)",
        env={"JIBUFF_BLOCK_WS": "1"},
    ),
    FirewallScenario(
        name="http_proxy",
        description="Traffic routed through HTTP proxy",
        env={"HTTP_PROXY": "http://proxy.internal:8080", "HTTPS_PROXY": "http://proxy.internal:8080"},
    ),
    FirewallScenario(
        name="port_443_only",
        description="Only port 443 allowed (no custom ports)",
        env={"JIBUFF_ALLOWED_PORTS": "443"},
    ),
]


@dataclass
class FirewallValidator:
    name = "firewall"
    scenarios: list[FirewallScenario] = field(default_factory=lambda: list(DEFAULT_SCENARIOS))
    test_script: str = "tests/e2e/firewall.py"

    def run(self, workspace: Path) -> tuple[bool, str]:
        script = workspace / self.test_script
        if not script.exists():
            return True, ""  # no firewall test — skip

        errors: list[str] = []
        for scenario in self.scenarios:
            result = self._run_scenario(workspace, script, scenario)
            if result:
                errors.append(result)

        if errors:
            return False, "\n\n".join(errors)
        return True, ""

    def _run_scenario(
        self, workspace: Path, script: Path, scenario: FirewallScenario
    ) -> str | None:
        import os
        env = os.environ.copy()
        env.update(scenario.env)

        result = subprocess.run(
            [
                "python", "-m", "pytest", str(script),
                f"--scenario={scenario.name}",
                "--tb=short", "-q",
            ],
            cwd=workspace,
            capture_output=True,
            text=True,
            env=env,
        )
        if result.returncode != 0:
            output = (result.stdout + result.stderr).strip()
            return f"[{scenario.name}] {scenario.description}\n{output}"
        return None
