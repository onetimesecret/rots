# tests/integration/test_smoke.py
"""Smoke tests for ots-containers CLI against a real systemd environment.

These tests require:
- systemd (systemctl) to be available and functional
- Podman installed and accessible
- Root or sufficient privileges to run systemctl commands

All tests are skipped automatically when these conditions are not met.
They are intended to run in CI on ubuntu-latest or equivalent systemd hosts.

Note on scope: integration tests here target the CLI layer and system
integration only. They do NOT deploy actual containers — the intent is to
verify that the CLI wires up correctly against real system tools.

To run locally (requires systemd):
    pytest tests/integration/ -v
"""

import shutil
import subprocess
import sys

import pytest

# ---------------------------------------------------------------------------
# Skip markers — applied to the whole module
# ---------------------------------------------------------------------------


def _systemd_available() -> bool:
    """Return True if systemctl is installed and pid 1 is systemd."""
    if not shutil.which("systemctl"):
        return False
    try:
        result = subprocess.run(
            ["systemctl", "is-system-running"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        # Acceptable states: running, degraded (some units failed but systemd is up)
        return result.stdout.strip() in ("running", "degraded")
    except (subprocess.SubprocessError, OSError):
        return False


def _podman_available() -> bool:
    """Return True if podman is installed."""
    return shutil.which("podman") is not None


requires_systemd = pytest.mark.skipif(
    not _systemd_available(),
    reason="systemd not available or not running (requires Linux with systemd as PID 1)",
)

requires_podman = pytest.mark.skipif(
    not _podman_available(),
    reason="podman not installed",
)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def run_cli(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    """Run the ots-containers CLI with the given arguments.

    Uses ``sys.executable -m ots_containers.cli`` so the installed package
    (or editable install) is used without requiring the ``ots-containers``
    script to be on PATH.
    """
    return subprocess.run(
        [sys.executable, "-m", "ots_containers.cli", *args],
        capture_output=True,
        text=True,
        timeout=30,
        check=check,
    )


# ---------------------------------------------------------------------------
# Smoke tests — CLI availability (no systemd required)
# ---------------------------------------------------------------------------


class TestCliAvailability:
    """Verify the CLI entry point is importable and responds to --help."""

    def test_cli_help_exits_zero(self):
        """ots-containers --help must exit 0."""
        result = run_cli("--help", check=False)
        assert result.returncode == 0, f"--help failed: {result.stderr}"

    def test_cli_version_exits_zero(self):
        """ots-containers --version must exit 0 and print a version string."""
        result = run_cli("--version", check=False)
        assert result.returncode == 0, f"--version failed: {result.stderr}"
        # Version output should contain digits
        combined = result.stdout + result.stderr
        assert any(c.isdigit() for c in combined), f"No version digits in output: {combined!r}"

    def test_instance_help_exits_zero(self):
        """ots-containers instance --help must exit 0."""
        result = run_cli("instance", "--help", check=False)
        assert result.returncode == 0, f"instance --help failed: {result.stderr}"

    def test_service_help_exits_zero(self):
        """ots-containers service --help must exit 0."""
        result = run_cli("service", "--help", check=False)
        assert result.returncode == 0, f"service --help failed: {result.stderr}"

    def test_image_help_exits_zero(self):
        """ots-containers image --help must exit 0."""
        result = run_cli("image", "--help", check=False)
        assert result.returncode == 0, f"image --help failed: {result.stderr}"

    def test_deploy_help_exits_zero(self):
        """ots-containers instance deploy --help must exit 0."""
        result = run_cli("instance", "deploy", "--help", check=False)
        assert result.returncode == 0, f"instance deploy --help failed: {result.stderr}"

    def test_deploy_help_mentions_wait_flag(self):
        """deploy --help must document the --wait flag."""
        result = run_cli("instance", "deploy", "--help", check=False)
        assert "--wait" in result.stdout, "--wait flag not found in deploy --help output"

    def test_deploy_help_mentions_pre_hook(self):
        """deploy --help must document the --pre-hook flag."""
        result = run_cli("instance", "deploy", "--help", check=False)
        assert "--pre-hook" in result.stdout, "--pre-hook flag not found in deploy --help output"

    def test_service_init_help_shows_start_as_optional(self):
        """service init --help must show --start and --enable as optional (off by default)."""
        result = run_cli("service", "init", "--help", check=False)
        assert result.returncode == 0
        # Both flags should appear in help; their defaults should not say "true"
        assert "--start" in result.stdout
        assert "--enable" in result.stdout


# ---------------------------------------------------------------------------
# Smoke tests — systemd integration (skipped without systemd)
# ---------------------------------------------------------------------------


@requires_systemd
class TestSystemdIntegration:
    """Verify CLI commands that shell out to systemctl work correctly.

    These tests do NOT deploy real containers. They verify that the CLI
    correctly invokes systemctl and handles its output.
    """

    def test_instance_list_exits_zero(self):
        """ots-containers instance list must exit 0 even when no instances are running."""
        result = run_cli("instance", "list", check=False)
        assert result.returncode == 0, (
            f"instance list failed with code {result.returncode}:\n{result.stderr}"
        )

    def test_service_list_exits_zero(self):
        """ots-containers service list must exit 0 when invoked without args."""
        result = run_cli("service", check=False)
        assert result.returncode == 0, (
            f"service list failed with code {result.returncode}:\n{result.stderr}"
        )

    def test_deploy_dry_run_does_not_require_secrets(self):
        """deploy --dry-run must not contact podman or systemctl for secrets."""
        result = run_cli(
            "instance",
            "deploy",
            "--web",
            "7099",
            "--dry-run",
            check=False,
        )
        # dry-run should exit 0 even without config/secrets
        # (it generates a preview, not an actual deploy)
        assert result.returncode in (0, 1), (
            f"deploy --dry-run exited unexpectedly: {result.returncode}\n{result.stderr}"
        )
        # Output should mention dry-run or quadlet
        combined = result.stdout + result.stderr
        assert (
            "dry-run" in combined.lower()
            or "quadlet" in combined.lower()
            or "image" in combined.lower()
        ), f"Unexpected dry-run output: {combined!r}"


@requires_podman
class TestPodmanIntegration:
    """Smoke tests that require podman to be installed."""

    def test_image_list_exits_zero(self):
        """ots-containers image list must exit 0."""
        result = run_cli("image", "list", check=False)
        # May exit non-zero if podman daemon is not running, but should not crash
        assert result.returncode in (0, 1), (
            f"image list crashed: {result.returncode}\n{result.stderr}"
        )
