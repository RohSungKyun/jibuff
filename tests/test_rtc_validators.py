"""Phase 5 — RTC Validator tests."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from validators.device import DeviceValidator
from validators.fallback import FALLBACK_KEYWORDS, FallbackValidator
from validators.firewall import FirewallScenario, FirewallValidator
from validators.network import NetworkScenario, NetworkValidator


def _mock_run(returncode: int, stdout: str = "", stderr: str = "") -> MagicMock:
    proc = MagicMock()
    proc.returncode = returncode
    proc.stdout = stdout
    proc.stderr = stderr
    return proc


# ---------------------------------------------------------------------------
# DeviceValidator
# ---------------------------------------------------------------------------


class TestDeviceValidator:
    def test_skips_when_no_script(self, tmp_path: Path) -> None:
        ok, out = DeviceValidator().run(tmp_path)
        assert ok is True
        assert out == ""

    def test_passes_all_targets(self, tmp_path: Path) -> None:
        script = tmp_path / "tests" / "e2e" / "device_compat.py"
        script.parent.mkdir(parents=True)
        script.touch()
        with patch("validators.device.subprocess.run", return_value=_mock_run(0)):
            ok, out = DeviceValidator(targets=["chromium", "firefox"]).run(tmp_path)
        assert ok is True

    def test_fails_on_one_target(self, tmp_path: Path) -> None:
        script = tmp_path / "tests" / "e2e" / "device_compat.py"
        script.parent.mkdir(parents=True)
        script.touch()
        responses = [_mock_run(0), _mock_run(1, stdout="webkit layout fail")]
        with patch("validators.device.subprocess.run", side_effect=responses):
            ok, out = DeviceValidator(targets=["chromium", "webkit"]).run(tmp_path)
        assert ok is False
        assert "webkit" in out
        assert "layout fail" in out

    def test_runs_once_per_target(self, tmp_path: Path) -> None:
        script = tmp_path / "tests" / "e2e" / "device_compat.py"
        script.parent.mkdir(parents=True)
        script.touch()
        with patch("validators.device.subprocess.run", return_value=_mock_run(0)) as mock:
            DeviceValidator(targets=["chromium", "firefox", "webkit"]).run(tmp_path)
        assert mock.call_count == 3


# ---------------------------------------------------------------------------
# NetworkValidator
# ---------------------------------------------------------------------------


class TestNetworkValidator:
    def test_skips_when_no_script(self, tmp_path: Path) -> None:
        ok, out = NetworkValidator().run(tmp_path)
        assert ok is True

    def test_passes_all_scenarios(self, tmp_path: Path) -> None:
        script = tmp_path / "tests" / "e2e" / "network_conditions.py"
        script.parent.mkdir(parents=True)
        script.touch()
        scenarios = [
            NetworkScenario(name="high_latency", latency_ms=500),
            NetworkScenario(name="offline", offline=True),
        ]
        with patch("validators.network.subprocess.run", return_value=_mock_run(0)):
            ok, _ = NetworkValidator(scenarios=scenarios).run(tmp_path)
        assert ok is True

    def test_fails_on_offline_scenario(self, tmp_path: Path) -> None:
        script = tmp_path / "tests" / "e2e" / "network_conditions.py"
        script.parent.mkdir(parents=True)
        script.touch()
        scenarios = [NetworkScenario(name="offline", offline=True, description="disconnect")]
        with patch(
            "validators.network.subprocess.run",
            return_value=_mock_run(1, stdout="connection refused"),
        ):
            ok, out = NetworkValidator(scenarios=scenarios).run(tmp_path)
        assert ok is False
        assert "offline" in out
        assert "connection refused" in out

    def test_latency_flag_passed_to_subprocess(self, tmp_path: Path) -> None:
        script = tmp_path / "tests" / "e2e" / "network_conditions.py"
        script.parent.mkdir(parents=True)
        script.touch()
        captured: list[list[str]] = []

        def fake_run(cmd: list[str], **_: object) -> MagicMock:
            captured.append(cmd)
            return _mock_run(0)

        scenarios = [NetworkScenario(name="slow", latency_ms=300)]
        with patch("validators.network.subprocess.run", side_effect=fake_run):
            NetworkValidator(scenarios=scenarios).run(tmp_path)

        assert any("--latency=300" in arg for arg in captured[0])


# ---------------------------------------------------------------------------
# FallbackValidator
# ---------------------------------------------------------------------------


class TestFallbackValidator:
    def test_skips_when_no_source_dirs(self, tmp_path: Path) -> None:
        ok, _ = FallbackValidator(
            source_dirs=["nonexistent"], require_static_evidence=True
        ).run(tmp_path)
        assert ok is True  # no dirs to check → skip

    def test_passes_when_fallback_keyword_found(self, tmp_path: Path) -> None:
        src = tmp_path / "src"
        src.mkdir()
        (src / "connection.py").write_text("def on_error():\n    fallback()\n", encoding="utf-8")
        ok, _ = FallbackValidator(source_dirs=["src"], require_static_evidence=True).run(tmp_path)
        assert ok is True

    def test_fails_when_no_fallback_keyword(self, tmp_path: Path) -> None:
        src = tmp_path / "src"
        src.mkdir()
        (src / "main.py").write_text("def connect(): pass\n", encoding="utf-8")
        ok, out = FallbackValidator(source_dirs=["src"], require_static_evidence=True).run(tmp_path)
        assert ok is False
        assert "fallback" in out.lower()

    def test_passes_e2e_script(self, tmp_path: Path) -> None:
        src = tmp_path / "src"
        src.mkdir()
        (src / "app.py").write_text("def retry(): pass\n", encoding="utf-8")
        script = tmp_path / "tests" / "e2e" / "fallback.py"
        script.parent.mkdir(parents=True)
        script.touch()
        with patch("validators.fallback.subprocess.run", return_value=_mock_run(0)):
            ok, _ = FallbackValidator(source_dirs=["src"]).run(tmp_path)
        assert ok is True

    def test_fails_e2e_script(self, tmp_path: Path) -> None:
        src = tmp_path / "src"
        src.mkdir()
        (src / "app.py").write_text("def reconnect(): pass\n", encoding="utf-8")
        script = tmp_path / "tests" / "e2e" / "fallback.py"
        script.parent.mkdir(parents=True)
        script.touch()
        with patch(
            "validators.fallback.subprocess.run",
            return_value=_mock_run(1, stdout="fallback path not triggered"),
        ):
            ok, out = FallbackValidator(source_dirs=["src"]).run(tmp_path)
        assert ok is False
        assert "fallback path not triggered" in out

    def test_all_fallback_keywords_checked(self, tmp_path: Path) -> None:
        # Verify keyword list is non-empty and contains expected entries
        assert "fallback" in FALLBACK_KEYWORDS
        assert "retry" in FALLBACK_KEYWORDS
        assert "reconnect" in FALLBACK_KEYWORDS


# ---------------------------------------------------------------------------
# FirewallValidator
# ---------------------------------------------------------------------------


class TestFirewallValidator:
    def test_skips_when_no_script(self, tmp_path: Path) -> None:
        ok, _ = FirewallValidator().run(tmp_path)
        assert ok is True

    def test_passes_all_scenarios(self, tmp_path: Path) -> None:
        script = tmp_path / "tests" / "e2e" / "firewall.py"
        script.parent.mkdir(parents=True)
        script.touch()
        scenarios = [FirewallScenario(name="ws_blocked", description="WS blocked")]
        with patch("validators.firewall.subprocess.run", return_value=_mock_run(0)):
            ok, _ = FirewallValidator(scenarios=scenarios).run(tmp_path)
        assert ok is True

    def test_fails_on_proxy_scenario(self, tmp_path: Path) -> None:
        script = tmp_path / "tests" / "e2e" / "firewall.py"
        script.parent.mkdir(parents=True)
        script.touch()
        scenarios = [FirewallScenario(
            name="http_proxy", description="proxy required",
            env={"HTTP_PROXY": "http://proxy:8080"}
        )]
        with patch(
            "validators.firewall.subprocess.run",
            return_value=_mock_run(1, stdout="proxy connection failed"),
        ):
            ok, out = FirewallValidator(scenarios=scenarios).run(tmp_path)
        assert ok is False
        assert "http_proxy" in out
        assert "proxy connection failed" in out

    def test_env_vars_injected_into_subprocess(self, tmp_path: Path) -> None:
        script = tmp_path / "tests" / "e2e" / "firewall.py"
        script.parent.mkdir(parents=True)
        script.touch()
        captured_envs: list[dict[str, str]] = []

        def fake_run(*args: object, **kwargs: object) -> MagicMock:
            env = kwargs.get("env", {})
            captured_envs.append(dict(env))  # type: ignore[call-overload]
            return _mock_run(0)

        scenarios = [FirewallScenario(
            name="proxy", description="x",
            env={"MY_PROXY": "http://test:9999"}
        )]
        with patch("validators.firewall.subprocess.run", side_effect=fake_run):
            FirewallValidator(scenarios=scenarios).run(tmp_path)

        assert any("MY_PROXY" in e for e in captured_envs)
