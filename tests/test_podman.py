# tests/test_podman.py
"""Tests for the Podman CLI wrapper."""

import subprocess

import pytest
from ots_shared.ssh.executor import CommandError, Result

from ots_containers.podman import Podman, podman


class TestPodmanWrapper:
    """Test the Podman class wrapper."""

    def test_default_executable(self):
        """Default executable should be 'podman'."""
        p = Podman()
        assert p.executable == "podman"

    def test_custom_executable(self):
        """Should allow custom executable path."""
        p = Podman(executable="/usr/local/bin/podman")
        assert p.executable == "/usr/local/bin/podman"

    def test_subcommand_chaining(self):
        """Should support nested subcommands like volume.create."""
        p = Podman()
        volume_create = p.volume.create
        assert volume_create._subcommand == ["volume", "create"]

    def test_underscore_to_hyphen_conversion(self):
        """Underscores in method names should become hyphens."""
        p = Podman()
        system_prune = p.system_prune
        assert system_prune._subcommand == ["system-prune"]

    def test_module_level_instance(self):
        """Module should export a ready-to-use podman instance."""
        assert isinstance(podman, Podman)
        assert podman.executable == "podman"
        assert podman._executor is None

    def test_default_executor_is_none(self):
        """Default executor should be None (subprocess path)."""
        p = Podman()
        assert p._executor is None

    def test_custom_executor(self):
        """Should accept an executor parameter."""
        from ots_shared.ssh.executor import LocalExecutor

        ex = LocalExecutor()
        p = Podman(executor=ex)
        assert p._executor is ex


class TestPodmanCommandBuilding:
    """Test command building logic."""

    def test_positional_args(self, mocker):
        """Positional args should be appended to command."""
        mock_run = mocker.patch("subprocess.run")
        mock_run.return_value = subprocess.CompletedProcess([], 0)

        p = Podman()
        p.ps("arg1", "arg2")

        call_args = mock_run.call_args[0][0]
        assert call_args == ["podman", "ps", "arg1", "arg2"]

    def test_keyword_to_flag_conversion(self, mocker):
        """Keyword args should become --flags."""
        mock_run = mocker.patch("subprocess.run")
        mock_run.return_value = subprocess.CompletedProcess([], 0)

        p = Podman()
        p.ps(filter="name=foo")

        call_args = mock_run.call_args[0][0]
        assert "--filter" in call_args
        assert "name=foo" in call_args

    def test_underscore_to_hyphen_in_flags(self, mocker):
        """Underscores in kwargs should become hyphens in flags."""
        mock_run = mocker.patch("subprocess.run")
        mock_run.return_value = subprocess.CompletedProcess([], 0)

        p = Podman()
        p.run(rm_after="true")

        call_args = mock_run.call_args[0][0]
        assert "--rm-after" in call_args

    def test_boolean_flag_true(self, mocker):
        """Boolean True should add flag without value."""
        mock_run = mocker.patch("subprocess.run")
        mock_run.return_value = subprocess.CompletedProcess([], 0)

        p = Podman()
        p.ps(all=True)

        call_args = mock_run.call_args[0][0]
        assert "--all" in call_args
        # Flag should not have a value following it
        idx = call_args.index("--all")
        if idx < len(call_args) - 1:
            # Next item shouldn't be the value for this flag
            assert call_args[idx + 1].startswith("-") or idx == len(call_args) - 1

    def test_boolean_flag_false(self, mocker):
        """Boolean False should not add flag."""
        mock_run = mocker.patch("subprocess.run")
        mock_run.return_value = subprocess.CompletedProcess([], 0)

        p = Podman()
        p.ps(all=False)

        call_args = mock_run.call_args[0][0]
        assert "--all" not in call_args

    def test_list_values_repeat_flag(self, mocker):
        """List values should repeat the flag for each item."""
        mock_run = mocker.patch("subprocess.run")
        mock_run.return_value = subprocess.CompletedProcess([], 0)

        p = Podman()
        p.run(env=["FOO=bar", "BAZ=qux"])

        call_args = mock_run.call_args[0][0]
        # Should have --env twice
        assert call_args.count("--env") == 2
        assert "FOO=bar" in call_args
        assert "BAZ=qux" in call_args


class TestPodmanSubprocessOptions:
    """Test subprocess.run options passthrough."""

    def test_capture_output(self, mocker):
        """capture_output should be passed to subprocess.run."""
        mock_run = mocker.patch("subprocess.run")
        mock_run.return_value = subprocess.CompletedProcess([], 0)

        p = Podman()
        p.ps(capture_output=True)

        assert mock_run.call_args[1]["capture_output"] is True

    def test_text_mode(self, mocker):
        """text should be passed to subprocess.run."""
        mock_run = mocker.patch("subprocess.run")
        mock_run.return_value = subprocess.CompletedProcess([], 0)

        p = Podman()
        p.ps(text=True)

        assert mock_run.call_args[1]["text"] is True

    def test_check_mode(self, mocker):
        """check should be passed to subprocess.run."""
        mock_run = mocker.patch("subprocess.run")
        mock_run.return_value = subprocess.CompletedProcess([], 0)

        p = Podman()
        p.ps(check=True)

        assert mock_run.call_args[1]["check"] is True

    def test_special_kwargs_not_in_command(self, mocker):
        """capture_output, text, check should not become flags."""
        mock_run = mocker.patch("subprocess.run")
        mock_run.return_value = subprocess.CompletedProcess([], 0)

        p = Podman()
        p.ps(capture_output=True, text=True, check=True)

        call_args = mock_run.call_args[0][0]
        assert "--capture-output" not in call_args
        assert "--text" not in call_args
        assert "--check" not in call_args


class TestPodmanNestedCommands:
    """Test nested subcommand patterns."""

    def test_volume_create(self, mocker):
        """podman.volume.create should build correct command."""
        mock_run = mocker.patch("subprocess.run")
        mock_run.return_value = subprocess.CompletedProcess([], 0)

        p = Podman()
        p.volume.create("myvolume")

        call_args = mock_run.call_args[0][0]
        assert call_args == ["podman", "volume", "create", "myvolume"]

    def test_volume_mount(self, mocker):
        """podman.volume.mount should build correct command."""
        mock_run = mocker.patch("subprocess.run")
        mock_run.return_value = subprocess.CompletedProcess([], 0)

        p = Podman()
        p.volume.mount("myvolume")

        call_args = mock_run.call_args[0][0]
        assert call_args == ["podman", "volume", "mount", "myvolume"]

    def test_system_connection_list(self, mocker):
        """Three-level nesting should work."""
        mock_run = mocker.patch("subprocess.run")
        mock_run.return_value = subprocess.CompletedProcess([], 0)

        p = Podman()
        p.system.connection.list()

        call_args = mock_run.call_args[0][0]
        assert call_args == ["podman", "system", "connection", "list"]


class TestPodmanExecutorSupport:
    """Test executor-based command dispatch."""

    def test_executor_propagates_through_getattr(self):
        """Executor should propagate through subcommand chaining."""
        from ots_shared.ssh.executor import LocalExecutor

        ex = LocalExecutor()
        p = Podman(executor=ex)
        volume_create = p.volume.create
        assert volume_create._executor is ex

    def test_executor_propagates_through_deep_chain(self):
        """Executor should propagate through three-level nesting."""
        from ots_shared.ssh.executor import LocalExecutor

        ex = LocalExecutor()
        p = Podman(executor=ex)
        deep = p.system.connection.list
        assert deep._executor is ex

    def test_call_delegates_to_executor(self, mocker):
        """When executor is set, __call__ should use executor.run()."""
        mock_executor = mocker.Mock()
        mock_executor.run.return_value = Result(
            command="podman ps", returncode=0, stdout="CONTAINER ID\n", stderr=""
        )

        p = Podman(executor=mock_executor)
        result = p.ps()

        mock_executor.run.assert_called_once()
        call_args = mock_executor.run.call_args
        assert call_args[0][0] == ["podman", "ps"]
        assert result.returncode == 0

    def test_executor_receives_check_flag(self, mocker):
        """check=True should be forwarded to executor.run()."""
        mock_executor = mocker.Mock()
        mock_executor.run.return_value = Result(
            command="podman ps", returncode=0, stdout="", stderr=""
        )

        p = Podman(executor=mock_executor)
        p.ps(check=True)

        call_kwargs = mock_executor.run.call_args[1]
        assert call_kwargs["check"] is True

    def test_executor_receives_timeout(self, mocker):
        """timeout kwarg should be forwarded to executor.run()."""
        mock_executor = mocker.Mock()
        mock_executor.run.return_value = Result(
            command="podman ps", returncode=0, stdout="", stderr=""
        )

        p = Podman(executor=mock_executor)
        p.ps(timeout=30)

        call_kwargs = mock_executor.run.call_args[1]
        assert call_kwargs["timeout"] == 30

    def test_executor_raises_command_error_on_check_failure(self, mocker):
        """When executor is set and check=True, CommandError should be raised on failure."""
        mock_executor = mocker.Mock()
        mock_executor.run.side_effect = CommandError(
            Result(command="podman ps", returncode=1, stdout="", stderr="error")
        )

        p = Podman(executor=mock_executor)
        with pytest.raises(CommandError) as exc_info:
            p.ps(check=True)
        assert exc_info.value.result.returncode == 1

    def test_no_executor_uses_subprocess(self, mocker):
        """Without executor, should use subprocess.run (backward compat)."""
        mock_run = mocker.patch("subprocess.run")
        mock_run.return_value = subprocess.CompletedProcess([], 0)

        p = Podman()
        p.ps(capture_output=True, text=True)

        mock_run.assert_called_once()
        assert mock_run.call_args[1]["capture_output"] is True

    def test_executor_builds_flags_correctly(self, mocker):
        """Executor path should still convert kwargs to flags."""
        mock_executor = mocker.Mock()
        mock_executor.run.return_value = Result(
            command="podman volume create myvol", returncode=0, stdout="", stderr=""
        )

        p = Podman(executor=mock_executor)
        p.volume.create("myvol", label="app=ots")

        cmd = mock_executor.run.call_args[0][0]
        assert cmd == ["podman", "volume", "create", "--label", "app=ots", "myvol"]

    def test_timeout_not_added_as_flag(self, mocker):
        """timeout should not become a --timeout flag."""
        mock_executor = mocker.Mock()
        mock_executor.run.return_value = Result(
            command="podman ps", returncode=0, stdout="", stderr=""
        )

        p = Podman(executor=mock_executor)
        p.ps(timeout=15)

        cmd = mock_executor.run.call_args[0][0]
        assert "--timeout" not in cmd
