# tests/commands/instance/test_helpers.py
"""Tests for instance command helpers."""

import fcntl

import pytest

from ots_containers.commands.instance._helpers import (
    _resolve_lock_path,
    deploy_lock,
    for_each_instance,
    format_command,
    format_journalctl_hint,
    resolve_identifiers,
)
from ots_containers.commands.instance.annotations import InstanceType


class TestDeployLock:
    """Tests for deploy_lock() context manager."""

    def test_yields_normally_when_lock_acquired(self, tmp_path):
        """deploy_lock() should yield without error when lock file can be created."""
        lock_path = tmp_path / "deploy.lock"
        reached = []
        with deploy_lock(lock_path):
            reached.append(True)
        assert reached == [True]

    def test_raises_system_exit_when_lock_already_held(self, tmp_path):
        """deploy_lock() should raise SystemExit(1) when another process holds the lock."""
        lock_path = tmp_path / "deploy.lock"
        # Hold the lock manually before entering the context manager
        lock_path.touch()
        fh = lock_path.open("a")
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            with pytest.raises(SystemExit) as exc_info:
                with deploy_lock(lock_path):
                    pass  # Should not reach here
            assert exc_info.value.code == 1
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)
            fh.close()

    def test_error_message_mentions_lock_file(self, tmp_path, capsys):
        """SystemExit message should mention the lock file path."""
        lock_path = tmp_path / "deploy.lock"
        lock_path.touch()
        fh = lock_path.open("a")
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            with pytest.raises(SystemExit):
                with deploy_lock(lock_path):
                    pass
            captured = capsys.readouterr()
            assert str(lock_path) in captured.err
            assert "deploy" in captured.err.lower()
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)
            fh.close()

    def test_releases_lock_after_context_exits(self, tmp_path):
        """Lock should be released in the finally block so a second acquire works."""
        lock_path = tmp_path / "deploy.lock"
        with deploy_lock(lock_path):
            pass
        # If the lock were not released this would raise BlockingIOError
        with deploy_lock(lock_path):
            pass

    def test_releases_lock_even_on_exception(self, tmp_path):
        """Lock must be released even when body raises."""
        lock_path = tmp_path / "deploy.lock"
        with pytest.raises(RuntimeError):
            with deploy_lock(lock_path):
                raise RuntimeError("boom")
        # Lock should be free now
        with deploy_lock(lock_path):
            pass

    def test_falls_back_to_stderr_warning_when_file_cannot_be_opened(self, tmp_path, capsys):
        """When the lock file cannot be opened, warn to stderr and yield (no-op)."""
        # Point at a path where the parent cannot be created/written to
        unwritable = tmp_path / "deploy.lock"
        reached = []

        with pytest.MonkeyPatch.context() as mp:
            # Make open() raise OSError to simulate unwritable path after mkdir
            def fake_open(self, mode="r", **kwargs):
                raise OSError("Permission denied")

            mp.setattr(unwritable.__class__, "open", fake_open)
            # Also make mkdir a no-op so we don't fail on that
            mp.setattr(unwritable.__class__, "mkdir", lambda *a, **kw: None)

            with deploy_lock(unwritable):
                reached.append(True)

        captured = capsys.readouterr()
        assert "Warning" in captured.err
        assert reached == [True]

    def test_multiple_sequential_deploys_succeed(self, tmp_path):
        """Sequential deploy_lock() calls must all succeed without interference."""
        lock_path = tmp_path / "deploy.lock"
        results = []
        for i in range(3):
            with deploy_lock(lock_path):
                results.append(i)
        assert results == [0, 1, 2]


class TestResolveLockPath:
    """Tests for _resolve_lock_path() helper."""

    def test_returns_given_path_when_parent_is_writable(self, tmp_path):
        """Should return the original path when its parent directory is writable."""
        lock_path = tmp_path / "deploy.lock"
        result = _resolve_lock_path(lock_path)
        assert result == lock_path

    def test_returns_tempfile_path_when_parent_is_not_writable(self, tmp_path):
        """Should fall back to a temp dir path when the requested parent is not writable."""
        import tempfile

        nonexistent = tmp_path / "nonexistent" / "deeply" / "nested" / "deploy.lock"
        # Patch mkdir on Path to raise OSError, simulating an unwritable filesystem
        with pytest.MonkeyPatch.context() as mp:

            def failing_mkdir(self, *args, **kwargs):
                raise OSError("read-only filesystem")

            mp.setattr(nonexistent.__class__, "mkdir", failing_mkdir)
            result = _resolve_lock_path(nonexistent)

        expected = str(tempfile.gettempdir())
        assert str(result).startswith(expected)
        assert "ots" in result.name.lower() or "lock" in result.name.lower()

    def test_probe_file_is_cleaned_up(self, tmp_path):
        """The temporary probe file used for writeability testing must be removed."""
        lock_path = tmp_path / "deploy.lock"
        _resolve_lock_path(lock_path)
        probe = tmp_path / ".ots_lock_probe"
        assert not probe.exists()


class TestFormatJournalctlHint:
    """Test format_journalctl_hint helper."""

    def test_single_web_instance(self):
        """Should generate journalctl command for single web instance."""
        instances = {InstanceType.WEB: ["7043"]}
        result = format_journalctl_hint(instances)
        assert result == "journalctl -t onetime-web-7043 -f"

    def test_multiple_web_instances(self):
        """Should generate journalctl command for multiple web instances."""
        instances = {InstanceType.WEB: ["7043", "7044"]}
        result = format_journalctl_hint(instances)
        assert result == "journalctl -t onetime-web-7043 -t onetime-web-7044 -f"

    def test_mixed_instance_types(self):
        """Should generate journalctl command for mixed instance types."""
        instances = {
            InstanceType.WEB: ["7043"],
            InstanceType.WORKER: ["billing"],
            InstanceType.SCHEDULER: ["main"],
        }
        result = format_journalctl_hint(instances)
        assert "-t onetime-web-7043" in result
        assert "-t onetime-worker-billing" in result
        assert "-t onetime-scheduler-main" in result
        assert result.endswith(" -f")

    def test_empty_instances(self):
        """Should return empty string for empty instances."""
        result = format_journalctl_hint({})
        assert result == ""

    def test_worker_instance(self):
        """Should generate journalctl command for worker instance."""
        instances = {InstanceType.WORKER: ["1"]}
        result = format_journalctl_hint(instances)
        assert result == "journalctl -t onetime-worker-1 -f"


class TestFormatCommand:
    """Test format_command helper."""

    def test_simple_command(self):
        """Simple args should remain unquoted."""
        result = format_command(["systemctl", "restart", "myservice"])
        assert result == "systemctl restart myservice"

    def test_args_with_spaces(self):
        """Args with spaces should be quoted."""
        result = format_command(["echo", "hello world"])
        assert result == "echo 'hello world'"

    def test_empty_args(self):
        """Empty args should be quoted as empty strings."""
        result = format_command(["cmd", ""])
        assert result == "cmd ''"


class TestResolveIdentifiers:
    """Test resolve_identifiers helper."""

    def test_explicit_identifiers_require_type(self):
        """Should raise SystemExit if identifiers given without type."""
        with pytest.raises(SystemExit) as exc_info:
            resolve_identifiers(("7043", "7044"), instance_type=None)
        assert "Instance type required" in str(exc_info.value)

    def test_explicit_identifiers_with_type(self):
        """Should return dict with provided identifiers."""
        result = resolve_identifiers(("7043", "7044"), instance_type=InstanceType.WEB)
        assert result == {InstanceType.WEB: ["7043", "7044"]}

    def test_explicit_worker_identifiers(self):
        """Should return dict for worker identifiers."""
        result = resolve_identifiers(("1", "billing"), instance_type=InstanceType.WORKER)
        assert result == {InstanceType.WORKER: ["1", "billing"]}

    def test_explicit_scheduler_identifiers(self):
        """Should return dict for scheduler identifiers."""
        result = resolve_identifiers(("main", "cron"), instance_type=InstanceType.SCHEDULER)
        assert result == {InstanceType.SCHEDULER: ["main", "cron"]}

    def test_invalid_web_port_non_numeric(self):
        """Should raise SystemExit for non-numeric web port."""
        with pytest.raises(SystemExit) as exc_info:
            resolve_identifiers(("foo",), instance_type=InstanceType.WEB)
        assert "Invalid port for web instance" in str(exc_info.value)

    def test_invalid_web_port_out_of_range(self):
        """Should raise SystemExit for out-of-range port."""
        with pytest.raises(SystemExit) as exc_info:
            resolve_identifiers(("70000",), instance_type=InstanceType.WEB)
        assert "Invalid port number" in str(exc_info.value)
        assert "must be 1-65535" in str(exc_info.value)

    def test_invalid_web_port_zero(self):
        """Should raise SystemExit for port 0."""
        with pytest.raises(SystemExit) as exc_info:
            resolve_identifiers(("0",), instance_type=InstanceType.WEB)
        assert "Invalid port number" in str(exc_info.value)

    def test_auto_discover_web_only(self, mocker):
        """Should discover only web instances when type is WEB."""
        mocker.patch(
            "ots_containers.commands.instance._helpers.systemd.discover_web_instances",
            return_value=[7043, 7044],
        )
        result = resolve_identifiers((), instance_type=InstanceType.WEB)
        assert result == {InstanceType.WEB: ["7043", "7044"]}

    def test_auto_discover_worker_only(self, mocker):
        """Should discover only worker instances when type is WORKER."""
        mocker.patch(
            "ots_containers.commands.instance._helpers.systemd.discover_worker_instances",
            return_value=["1", "billing"],
        )
        result = resolve_identifiers((), instance_type=InstanceType.WORKER)
        assert result == {InstanceType.WORKER: ["1", "billing"]}

    def test_auto_discover_scheduler_only(self, mocker):
        """Should discover only scheduler instances when type is SCHEDULER."""
        mocker.patch(
            "ots_containers.commands.instance._helpers.systemd.discover_scheduler_instances",
            return_value=["main"],
        )
        result = resolve_identifiers((), instance_type=InstanceType.SCHEDULER)
        assert result == {InstanceType.SCHEDULER: ["main"]}

    def test_auto_discover_all_types(self, mocker):
        """Should discover all types when no type specified."""
        mocker.patch(
            "ots_containers.commands.instance._helpers.systemd.discover_web_instances",
            return_value=[7043],
        )
        mocker.patch(
            "ots_containers.commands.instance._helpers.systemd.discover_worker_instances",
            return_value=["1"],
        )
        mocker.patch(
            "ots_containers.commands.instance._helpers.systemd.discover_scheduler_instances",
            return_value=["main"],
        )
        result = resolve_identifiers((), instance_type=None)
        assert result == {
            InstanceType.WEB: ["7043"],
            InstanceType.WORKER: ["1"],
            InstanceType.SCHEDULER: ["main"],
        }

    def test_auto_discover_empty_results_omitted(self, mocker):
        """Should omit types with no discovered instances."""
        mocker.patch(
            "ots_containers.commands.instance._helpers.systemd.discover_web_instances",
            return_value=[7043],
        )
        mocker.patch(
            "ots_containers.commands.instance._helpers.systemd.discover_worker_instances",
            return_value=[],
        )
        mocker.patch(
            "ots_containers.commands.instance._helpers.systemd.discover_scheduler_instances",
            return_value=[],
        )
        result = resolve_identifiers((), instance_type=None)
        assert result == {InstanceType.WEB: ["7043"]}
        assert InstanceType.WORKER not in result
        assert InstanceType.SCHEDULER not in result

    def test_running_only_flag_passed(self, mocker):
        """Should pass running_only flag to discovery functions."""
        mock_web = mocker.patch(
            "ots_containers.commands.instance._helpers.systemd.discover_web_instances",
            return_value=[],
        )
        mock_worker = mocker.patch(
            "ots_containers.commands.instance._helpers.systemd.discover_worker_instances",
            return_value=[],
        )
        mock_scheduler = mocker.patch(
            "ots_containers.commands.instance._helpers.systemd.discover_scheduler_instances",
            return_value=[],
        )
        resolve_identifiers((), instance_type=None, running_only=True)
        mock_web.assert_called_once_with(running_only=True)
        mock_worker.assert_called_once_with(running_only=True)
        mock_scheduler.assert_called_once_with(running_only=True)


class TestForEachInstance:
    """Test for_each_instance helper."""

    def test_empty_instances_returns_zero(self, capsys):
        """Should return 0 when no instances provided."""
        called = []
        result = for_each_instance(
            {}, delay=0, action=lambda t, i: called.append((t, i)), verb="Testing"
        )
        assert result == 0
        assert called == []
        output = capsys.readouterr().out
        assert "No instances found to operate on." in output

    def test_single_instance(self, capsys):
        """Should process single instance."""
        called = []
        instances = {InstanceType.WEB: ["7043"]}
        result = for_each_instance(
            instances, delay=0, action=lambda t, i: called.append((t, i)), verb="Testing"
        )
        assert result == 1
        assert called == [(InstanceType.WEB, "7043")]
        output = capsys.readouterr().out
        assert "[1/1] Testing onetime-web@7043" in output
        assert "Processed 1 instance(s)" in output

    def test_multiple_instances_same_type(self, capsys):
        """Should process multiple instances of same type."""
        called = []
        instances = {InstanceType.WORKER: ["1", "2"]}
        result = for_each_instance(
            instances, delay=0, action=lambda t, i: called.append((t, i)), verb="Starting"
        )
        assert result == 2
        assert called == [(InstanceType.WORKER, "1"), (InstanceType.WORKER, "2")]
        output = capsys.readouterr().out
        assert "[1/2] Starting onetime-worker@1" in output
        assert "[2/2] Starting onetime-worker@2" in output

    def test_mixed_types(self, capsys):
        """Should process instances of different types."""
        called = []
        instances = {
            InstanceType.WEB: ["7043"],
            InstanceType.WORKER: ["1"],
            InstanceType.SCHEDULER: ["main"],
        }
        result = for_each_instance(
            instances, delay=0, action=lambda t, i: called.append((t, i)), verb="Stopping"
        )
        assert result == 3
        assert (InstanceType.WEB, "7043") in called
        assert (InstanceType.WORKER, "1") in called
        assert (InstanceType.SCHEDULER, "main") in called

    def test_delay_between_instances(self, mocker, capsys):
        """Should wait between instances when delay > 0."""
        mock_sleep = mocker.patch("ots_containers.commands.instance._helpers.time.sleep")
        instances = {InstanceType.WEB: ["7043", "7044", "7045"]}
        for_each_instance(instances, delay=5, action=lambda t, i: None, verb="Restarting")
        # Should sleep twice (between 1-2 and 2-3, but not after last)
        assert mock_sleep.call_count == 2
        mock_sleep.assert_called_with(5)
        output = capsys.readouterr().out
        assert "Waiting 5s..." in output

    def test_no_delay_when_zero(self, mocker):
        """Should not sleep when delay is 0."""
        mock_sleep = mocker.patch("ots_containers.commands.instance._helpers.time.sleep")
        instances = {InstanceType.WEB: ["7043", "7044"]}
        for_each_instance(instances, delay=0, action=lambda t, i: None, verb="Testing")
        mock_sleep.assert_not_called()


class TestRunHook:
    """Tests for the run_hook helper."""

    def test_successful_hook_does_not_raise(self, mocker):
        """A hook exiting 0 should complete without raising."""
        from ots_containers.commands.instance._helpers import run_hook

        mocker.patch(
            "subprocess.run",
            return_value=mocker.MagicMock(returncode=0),
        )

        # Should not raise
        run_hook("echo ok", "pre-hook")

    def test_failed_hook_raises_system_exit(self, mocker):
        """A hook exiting non-zero should raise SystemExit(1)."""
        import pytest

        from ots_containers.commands.instance._helpers import run_hook

        mocker.patch(
            "subprocess.run",
            return_value=mocker.MagicMock(returncode=1),
        )

        with pytest.raises(SystemExit) as exc_info:
            run_hook("./failing-scan.sh", "pre-hook")

        assert exc_info.value.code == 1

    def test_failed_hook_message_includes_command_and_stage(self, mocker, capsys):
        """Error output should identify both the stage and command."""
        import pytest

        from ots_containers.commands.instance._helpers import run_hook

        mocker.patch(
            "subprocess.run",
            return_value=mocker.MagicMock(returncode=2),
        )

        with pytest.raises(SystemExit):
            run_hook("./custom-scan.sh", "pre-hook")

        captured = capsys.readouterr()
        assert "pre-hook" in captured.err
        assert "./custom-scan.sh" in captured.err

    def test_hook_is_run_via_shell(self, mocker):
        """Hook commands must be run through the shell (shell=True)."""
        from ots_containers.commands.instance._helpers import run_hook

        mock_run = mocker.patch(
            "subprocess.run",
            return_value=mocker.MagicMock(returncode=0),
        )

        run_hook("echo ok", "post-hook")

        call_kwargs = mock_run.call_args[1]
        assert call_kwargs.get("shell") is True

    def test_quiet_mode_suppresses_output(self, mocker, capsys):
        """quiet=True should not print hook stage messages."""
        from ots_containers.commands.instance._helpers import run_hook

        mocker.patch(
            "subprocess.run",
            return_value=mocker.MagicMock(returncode=0),
        )

        run_hook("echo ok", "pre-hook", quiet=True)

        captured = capsys.readouterr()
        assert "pre-hook" not in captured.out

    def test_hook_receives_correct_command_string(self, mocker):
        """subprocess.run should receive the exact command string provided."""
        from ots_containers.commands.instance._helpers import run_hook

        mock_run = mocker.patch(
            "subprocess.run",
            return_value=mocker.MagicMock(returncode=0),
        )

        run_hook("./scripts/scan.sh --verbose", "pre-hook")

        call_args = mock_run.call_args[0]
        assert call_args[0] == "./scripts/scan.sh --verbose"

    def test_verbose_mode_prints_progress_messages(self, mocker, capsys):
        """quiet=False (default) should print stage name and pass confirmation."""
        from ots_containers.commands.instance._helpers import run_hook

        mocker.patch(
            "subprocess.run",
            return_value=mocker.MagicMock(returncode=0),
        )

        run_hook("echo ok", "pre-hook", quiet=False)

        captured = capsys.readouterr()
        assert "pre-hook" in captured.out
        assert "passed" in captured.out

    def test_failed_hook_error_message_includes_exit_code(self, mocker, capsys):
        """Error output should include the non-zero exit code."""
        from ots_containers.commands.instance._helpers import run_hook

        mocker.patch(
            "subprocess.run",
            return_value=mocker.MagicMock(returncode=42),
        )

        with pytest.raises(SystemExit):
            run_hook("./scan.sh", "pre-hook")

        captured = capsys.readouterr()
        assert "42" in captured.err

    def test_successful_hook_returns_none(self, mocker):
        """run_hook should return None when the hook exits 0."""
        from ots_containers.commands.instance._helpers import run_hook

        mocker.patch(
            "subprocess.run",
            return_value=mocker.MagicMock(returncode=0),
        )

        result = run_hook("echo ok", "post-hook")

        assert result is None
