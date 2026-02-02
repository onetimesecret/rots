# tests/commands/instance/test_helpers.py
"""Tests for instance command helpers."""

import pytest

from ots_containers.commands.instance._helpers import (
    for_each_instance,
    format_command,
    format_journalctl_hint,
    resolve_identifiers,
)
from ots_containers.commands.instance.annotations import InstanceType


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
