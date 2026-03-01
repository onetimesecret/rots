# tests/test_main.py
"""Tests for __main__.py entry point (python -m rots)."""

import subprocess
import sys


class TestMainEntryPoint:
    """Test python -m rots invocation covers __main__.py."""

    def test_module_invocation_help_exits_zero(self):
        """python -m rots --help should exit 0."""
        result = subprocess.run(
            [sys.executable, "-m", "rots", "--help"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0

    def test_module_invocation_help_contains_usage(self):
        """python -m rots --help should print usage information."""
        result = subprocess.run(
            [sys.executable, "-m", "rots", "--help"],
            capture_output=True,
            text=True,
        )
        output = result.stdout + result.stderr
        assert len(output) > 0

    def test_module_invocation_version_exits_zero(self):
        """python -m rots --version should exit 0."""
        result = subprocess.run(
            [sys.executable, "-m", "rots", "--version"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0

    def test_module_invocation_no_args_exits_zero(self):
        """python -m rots with no args should exit 0 (shows help)."""
        result = subprocess.run(
            [sys.executable, "-m", "rots"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
