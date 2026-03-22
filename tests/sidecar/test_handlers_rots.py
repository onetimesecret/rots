# tests/sidecar/test_handlers_rots.py

"""Tests for the generic rots CLI invocation handler."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from rots.sidecar.handlers_rots import (
    ALLOWED_SUBCOMMANDS,
    BLOCKED_SUBCOMMANDS,
    _build_command,
    _is_subcommand_allowed,
    invoke_rots,
    is_rots_command,
    list_allowed_commands,
)


class TestIsRotsCommand:
    """Tests for is_rots_command function."""

    def test_rots_prefix_returns_true(self):
        assert is_rots_command("rots.env.process") is True
        assert is_rots_command("rots.proxy.reload") is True
        assert is_rots_command("rots.instance.redeploy") is True

    def test_no_prefix_returns_false(self):
        assert is_rots_command("restart.web") is False
        assert is_rots_command("env.process") is False
        assert is_rots_command("") is False

    def test_partial_prefix_returns_false(self):
        assert is_rots_command("rot.env") is False
        assert is_rots_command("rotss.env") is False


class TestIsSubcommandAllowed:
    """Tests for _is_subcommand_allowed function."""

    def test_explicitly_allowed(self):
        assert _is_subcommand_allowed(("env", "process")) is True
        assert _is_subcommand_allowed(("proxy", "reload")) is True
        assert _is_subcommand_allowed(("doctor",)) is True

    def test_blocked_subcommands(self):
        assert _is_subcommand_allowed(("sidecar",)) is False
        assert _is_subcommand_allowed(("sidecar", "run")) is False
        assert _is_subcommand_allowed(("host", "push")) is False
        assert _is_subcommand_allowed(("env", "push")) is False

    def test_unlisted_subcommands_blocked(self):
        # Not in allowed list and not a prefix match
        assert _is_subcommand_allowed(("foo", "bar")) is False
        assert _is_subcommand_allowed(("unknown",)) is False

    def test_nested_allowed_via_prefix(self):
        # If ("instance",) were in allowed, nested would work
        # But we have explicit entries like ("instance", "start")
        assert _is_subcommand_allowed(("instance", "start")) is True
        assert _is_subcommand_allowed(("instance", "restart")) is True

    def test_single_element_no_prefix_expansion(self):
        """Security test: single-element allowlist entries must not expand.

        Regression test for PR #35 feedback item #2.
        If ('init',) is allowed, ('init', 'anything') should NOT be allowed.
        This prevents attackers from invoking arbitrary subcommands by
        prefixing them with an allowed single-element command.
        """
        # Single-element entries are explicitly allowed
        assert _is_subcommand_allowed(("init",)) is True
        assert _is_subcommand_allowed(("doctor",)) is True
        assert _is_subcommand_allowed(("ps",)) is True
        assert _is_subcommand_allowed(("version",)) is True

        # But extending them with arbitrary subcommands must be blocked
        assert _is_subcommand_allowed(("init", "malicious")) is False
        assert _is_subcommand_allowed(("init", "delete-all-data")) is False
        assert _is_subcommand_allowed(("doctor", "exploit")) is False
        assert _is_subcommand_allowed(("ps", "rm", "-rf")) is False
        assert _is_subcommand_allowed(("version", "upgrade", "--force")) is False

    def test_multi_element_no_prefix_expansion(self):
        """Security test: multi-element allowlist entries must not expand either.

        If ('env', 'process') is allowed, ('env', 'process', 'extra') should NOT
        be allowed unless explicitly listed.
        """
        assert _is_subcommand_allowed(("env", "process")) is True
        assert _is_subcommand_allowed(("env", "process", "extra")) is False
        assert _is_subcommand_allowed(("self", "upgrade")) is True
        assert _is_subcommand_allowed(("self", "upgrade", "--malicious")) is False


class TestBuildCommand:
    """Tests for _build_command function."""

    def test_simple_subcommand(self):
        with patch("rots.sidecar.handlers_rots.shutil.which", return_value="/usr/bin/rots"):
            cmd = _build_command(("env", "process"), [])
            assert cmd == ["/usr/bin/rots", "env", "process"]

    def test_with_extra_args(self):
        with patch("rots.sidecar.handlers_rots.shutil.which", return_value="/usr/bin/rots"):
            cmd = _build_command(("instance", "redeploy"), ["7043", "7044"])
            assert cmd == ["/usr/bin/rots", "instance", "redeploy", "7043", "7044"]

    def test_with_flags(self):
        with patch("rots.sidecar.handlers_rots.shutil.which", return_value="/usr/bin/rots"):
            cmd = _build_command(("image", "pull"), ["--tag", "v0.24.0"])
            assert cmd == ["/usr/bin/rots", "image", "pull", "--tag", "v0.24.0"]

    def test_fallback_to_python_module(self):
        with patch("rots.sidecar.handlers_rots.shutil.which", return_value=None):
            with patch("rots.sidecar.handlers_rots.sys.executable", "/usr/bin/python3"):
                cmd = _build_command(("ps",), [])
                assert cmd == ["/usr/bin/python3", "-m", "rots", "ps"]


class TestInvokeRots:
    """Tests for invoke_rots function."""

    def test_invalid_prefix_rejected(self):
        result = invoke_rots("env.process", {})
        assert result["status"] == "error"
        assert "Must start with 'rots.'" in result["error"]

    def test_empty_command_rejected(self):
        result = invoke_rots("rots.", {})
        assert result["status"] == "error"
        assert "Empty command path" in result["error"]

    def test_blocked_command_rejected(self):
        result = invoke_rots("rots.sidecar.run", {})
        assert result["status"] == "error"
        assert "not allowed" in result["error"]

    def test_unknown_command_rejected(self):
        result = invoke_rots("rots.foobar.baz", {})
        assert result["status"] == "error"
        assert "not allowed" in result["error"]

    def test_successful_invocation(self):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "success output"
        mock_result.stderr = ""

        with patch("rots.sidecar.handlers_rots.subprocess.run", return_value=mock_result):
            with patch("rots.sidecar.handlers_rots.shutil.which", return_value="/usr/bin/rots"):
                result = invoke_rots("rots.env.process", {})

        assert result["status"] == "ok"
        assert result["returncode"] == 0
        assert result["stdout"] == "success output"

    def test_failed_invocation(self):
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = "error message"

        with patch("rots.sidecar.handlers_rots.subprocess.run", return_value=mock_result):
            with patch("rots.sidecar.handlers_rots.shutil.which", return_value="/usr/bin/rots"):
                result = invoke_rots("rots.doctor", {})

        assert result["status"] == "error"
        assert result["returncode"] == 1
        assert result["stderr"] == "error message"

    def test_with_args_param(self):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""
        mock_result.stderr = ""

        with patch(
            "rots.sidecar.handlers_rots.subprocess.run", return_value=mock_result
        ) as mock_run:
            with patch("rots.sidecar.handlers_rots.shutil.which", return_value="/usr/bin/rots"):
                invoke_rots("rots.instance.redeploy", {"args": ["7043", "7044", "--delay", "5"]})

        # Check the command that was passed
        called_cmd = mock_run.call_args[0][0]
        assert called_cmd == [
            "/usr/bin/rots",
            "instance",
            "redeploy",
            "7043",
            "7044",
            "--delay",
            "5",
        ]

    def test_args_converted_to_strings(self):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""
        mock_result.stderr = ""

        with patch(
            "rots.sidecar.handlers_rots.subprocess.run", return_value=mock_result
        ) as mock_run:
            with patch("rots.sidecar.handlers_rots.shutil.which", return_value="/usr/bin/rots"):
                # Pass integers - they should be converted to strings
                invoke_rots("rots.instance.redeploy", {"args": [7043, 7044]})

        called_cmd = mock_run.call_args[0][0]
        assert "7043" in called_cmd
        assert "7044" in called_cmd

    def test_timeout_handling(self):
        import subprocess

        with patch("rots.sidecar.handlers_rots.subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired(cmd=["rots"], timeout=10)
            with patch("rots.sidecar.handlers_rots.shutil.which", return_value="/usr/bin/rots"):
                result = invoke_rots("rots.doctor", {"timeout": 10})

        assert result["status"] == "error"
        assert "timed out" in result["error"]

    def test_custom_timeout(self):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""
        mock_result.stderr = ""

        with patch(
            "rots.sidecar.handlers_rots.subprocess.run", return_value=mock_result
        ) as mock_run:
            with patch("rots.sidecar.handlers_rots.shutil.which", return_value="/usr/bin/rots"):
                invoke_rots("rots.doctor", {"timeout": 60})

        # Check timeout was passed
        assert mock_run.call_args[1]["timeout"] == 60

    def test_exception_handling(self):
        with patch("rots.sidecar.handlers_rots.subprocess.run") as mock_run:
            mock_run.side_effect = OSError("No such file")
            with patch("rots.sidecar.handlers_rots.shutil.which", return_value="/usr/bin/rots"):
                result = invoke_rots("rots.doctor", {})

        assert result["status"] == "error"
        assert "No such file" in result["error"]


class TestListAllowedCommands:
    """Tests for list_allowed_commands function."""

    def test_returns_prefixed_list(self):
        allowed = list_allowed_commands()
        assert all(cmd.startswith("rots.") for cmd in allowed)

    def test_contains_expected_commands(self):
        allowed = list_allowed_commands()
        assert "rots.env.process" in allowed
        assert "rots.proxy.reload" in allowed
        assert "rots.doctor" in allowed

    def test_does_not_contain_blocked(self):
        allowed = list_allowed_commands()
        assert "rots.sidecar" not in allowed
        assert "rots.sidecar.run" not in allowed


class TestAllowedBlockedConsistency:
    """Tests to ensure ALLOWED and BLOCKED lists are consistent."""

    def test_no_overlap(self):
        overlap = ALLOWED_SUBCOMMANDS & BLOCKED_SUBCOMMANDS
        assert len(overlap) == 0, f"Overlap between allowed and blocked: {overlap}"

    def test_blocked_not_prefix_of_allowed(self):
        for blocked in BLOCKED_SUBCOMMANDS:
            for allowed in ALLOWED_SUBCOMMANDS:
                if allowed[: len(blocked)] == blocked:
                    pytest.fail(
                        f"Blocked {blocked} is prefix of allowed {allowed} - "
                        "the allowed command would be unreachable"
                    )


class TestNonListArgs:
    """Tests for non-list args parameter handling."""

    def test_single_string_arg_converted(self):
        """Single string arg should be wrapped in a list."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""
        mock_result.stderr = ""

        with patch(
            "rots.sidecar.handlers_rots.subprocess.run", return_value=mock_result
        ) as mock_run:
            with patch("rots.sidecar.handlers_rots.shutil.which", return_value="/usr/bin/rots"):
                invoke_rots("rots.doctor", {"args": "--verbose"})

        called_cmd = mock_run.call_args[0][0]
        assert "--verbose" in called_cmd


class TestDispatchIntegration:
    """Tests for handlers.dispatch routing rots.* commands."""

    def test_dispatch_routes_rots_commands(self):
        """dispatch() should route rots.* commands to invoke_rots."""
        from rots.sidecar.handlers import dispatch

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "version output"
        mock_result.stderr = ""

        with patch("rots.sidecar.handlers_rots.subprocess.run", return_value=mock_result):
            with patch("rots.sidecar.handlers_rots.shutil.which", return_value="/usr/bin/rots"):
                result = dispatch("rots.version", {})

        assert result["status"] == "ok"
        assert result["stdout"] == "version output"

    def test_dispatch_routes_builtin_commands(self):
        """dispatch() should still route built-in commands to their handlers."""
        from rots.sidecar.handlers import dispatch

        result = dispatch("health", {})
        assert result["status"] == "ok"
        assert result["health"] == "healthy"

    def test_dispatch_unknown_shows_both_command_sets(self):
        """Unknown command should list both built-in and rots.* commands."""
        from rots.sidecar.handlers import dispatch

        result = dispatch("unknown.command", {})
        assert result["status"] == "error"
        available = result["available_commands"]
        # Should include built-in handlers
        assert "health" in available
        assert "status" in available
        # Should include rots.* commands
        assert any(cmd.startswith("rots.") for cmd in available)

    def test_dispatch_blocked_rots_command(self):
        """Blocked rots.* commands should be rejected."""
        from rots.sidecar.handlers import dispatch

        result = dispatch("rots.sidecar.run", {})
        assert result["status"] == "error"
        assert "not allowed" in result["error"]
