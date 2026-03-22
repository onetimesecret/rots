# tests/test_systemd.py
"""Tests for systemd module - systemctl wrapper functions."""

import subprocess

import pytest

from rots.systemd import SystemctlError


@pytest.fixture(autouse=True)
def mock_systemctl_available(mocker):
    """Mock shutil.which to report systemctl as available for all tests."""
    mocker.patch("shutil.which", return_value="/mock/bin/systemctl")


class TestUnitName:
    """Test unit_name helper function."""

    def test_web_unit_name(self):
        """Should generate correct web unit name."""
        from rots import systemd

        assert systemd.unit_name("web", "7043") == "onetime-web@7043"

    def test_worker_unit_name(self):
        """Should generate correct worker unit name."""
        from rots import systemd

        assert systemd.unit_name("worker", "1") == "onetime-worker@1"
        assert systemd.unit_name("worker", "billing") == "onetime-worker@billing"

    def test_scheduler_unit_name(self):
        """Should generate correct scheduler unit name."""
        from rots import systemd

        assert systemd.unit_name("scheduler", "main") == "onetime-scheduler@main"
        assert systemd.unit_name("scheduler", "1") == "onetime-scheduler@1"


class TestDiscoverWebInstances:
    """Test discover_web_instances function for web containers."""

    def test_discover_web_instances_returns_sorted_ports(self, mocker):
        """Should parse systemctl output and return sorted port list."""
        from rots import systemd

        mock_result = mocker.Mock()
        mock_result.stdout = (
            "onetime-web@7043.service loaded active running OTS 7043\n"
            "onetime-web@7044.service loaded active running OTS 7044\n"
            "onetime-web@7042.service loaded active running OTS 7042\n"
        )
        mocker.patch("subprocess.run", return_value=mock_result)

        ports = systemd.discover_web_instances()

        assert ports == [7042, 7043, 7044]

    def test_discover_web_instances_empty_output(self, mocker):
        """Should return empty list when no instances running."""
        from rots import systemd

        mock_result = mocker.Mock()
        mock_result.stdout = ""
        mocker.patch("subprocess.run", return_value=mock_result)

        ports = systemd.discover_web_instances()

        assert ports == []

    def test_discover_web_instances_ignores_malformed_lines(self, mocker):
        """Should skip lines that don't match expected format."""
        from rots import systemd

        mock_result = mocker.Mock()
        mock_result.stdout = (
            "onetime-web@7043.service loaded active running OTS\n"
            "some-other.service loaded active running Other\n"
            "onetime-web@abc.service loaded active running Bad port\n"
            "onetime-web@7044.service loaded active running OTS\n"
        )
        mocker.patch("subprocess.run", return_value=mock_result)

        ports = systemd.discover_web_instances()

        assert ports == [7043, 7044]

    def test_discover_web_instances_returns_all_loaded_by_default(self, mocker):
        """Should return all loaded units regardless of state by default."""
        from rots import systemd

        mock_result = mocker.Mock()
        mock_result.stdout = (
            "onetime-web@7042.service loaded active running OneTimeSecret Container 7042\n"
            "onetime-web@7043.service loaded failed failed OneTimeSecret Container 7043\n"
            "onetime-web@7044.service loaded inactive dead OneTimeSecret Container 7044\n"
        )
        mocker.patch("subprocess.run", return_value=mock_result)

        ports = systemd.discover_web_instances()

        assert ports == [7042, 7043, 7044]

    def test_discover_web_instances_running_only_filters_failed(self, mocker):
        """With running_only=True, should exclude failed units."""
        from rots import systemd

        mock_result = mocker.Mock()
        mock_result.stdout = (
            "onetime-web@7043.service loaded failed failed OneTimeSecret Container 7043\n"
            "onetime-web@7044.service loaded failed failed OneTimeSecret Container 7044\n"
        )
        mocker.patch("subprocess.run", return_value=mock_result)

        ports = systemd.discover_web_instances(running_only=True)

        assert ports == []

    def test_discover_web_instances_running_only_mixed(self, mocker):
        """With running_only=True, should return only running units."""
        from rots import systemd

        mock_result = mocker.Mock()
        mock_result.stdout = (
            "onetime-web@7042.service loaded active running OneTimeSecret Container 7042\n"
            "onetime-web@7043.service loaded failed failed OneTimeSecret Container 7043\n"
            "onetime-web@7044.service loaded active running OneTimeSecret Container 7044\n"
        )
        mocker.patch("subprocess.run", return_value=mock_result)

        ports = systemd.discover_web_instances(running_only=True)

        assert ports == [7042, 7044]

    def test_discover_web_instances_calls_systemctl_correctly(self, mocker):
        """Should call systemctl with --all flag to show all units."""
        from rots import systemd

        mock_result = mocker.Mock()
        mock_result.stdout = ""
        mock_run = mocker.patch("subprocess.run", return_value=mock_result)

        systemd.discover_web_instances()

        mock_run.assert_called_once_with(
            ["systemctl", "list-units", "onetime-web@*", "--plain", "--no-legend", "--all"],
            capture_output=True,
            text=True,
            timeout=10,
        )


class TestIsActive:
    """Test is_active function."""

    def test_is_active_returns_active_state(self, mocker):
        """Should return the state string from systemctl is-active."""
        from rots import systemd

        mock_result = mocker.Mock()
        mock_result.stdout = "active\n"
        mock_result.returncode = 0
        mock_result.ok = True
        mocker.patch("subprocess.run", return_value=mock_result)

        state = systemd.is_active("onetime-web@7043")
        assert state == "active"

    def test_is_active_returns_inactive(self, mocker):
        """Should return 'inactive' for stopped units."""
        from rots import systemd

        mock_result = mocker.Mock()
        mock_result.stdout = "inactive\n"
        mock_result.returncode = 3
        mock_result.ok = False
        mocker.patch("subprocess.run", return_value=mock_result)

        state = systemd.is_active("onetime-web@7043")
        assert state == "inactive"

    def test_is_active_returns_failed(self, mocker):
        """Should return 'failed' for failed units."""
        from rots import systemd

        mock_result = mocker.Mock()
        mock_result.stdout = "failed\n"
        mock_result.returncode = 3
        mock_result.ok = False
        mocker.patch("subprocess.run", return_value=mock_result)

        state = systemd.is_active("onetime-web@7043")
        assert state == "failed"

    def test_is_active_calls_systemctl_correctly(self, mocker):
        """Should call systemctl is-active with the unit name."""
        from rots import systemd

        mock_result = mocker.Mock()
        mock_result.stdout = "active\n"
        mock_result.returncode = 0
        mock_result.ok = True
        mock_run = mocker.patch("subprocess.run", return_value=mock_result)

        systemd.is_active("onetime-web@7043")

        mock_run.assert_called_once_with(
            ["systemctl", "is-active", "onetime-web@7043"],
            capture_output=True,
            text=True,
            timeout=10,
        )


class TestEnable:
    """Test enable function."""

    def test_enable_calls_systemctl_enable(self, mocker):
        """Should call sudo systemctl enable with unit name."""
        from rots import systemd

        mock_run = mocker.patch(
            "subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, stdout="", stderr=""),
        )

        systemd.enable("onetime-web@7043")

        mock_run.assert_called_once_with(
            ["sudo", "--", "systemctl", "enable", "onetime-web@7043"],
            capture_output=True,
            text=True,
            timeout=90,
        )

    def test_enable_raises_systemctl_error_on_failure(self, mocker):
        """Should raise SystemctlError with journal context on failure."""
        from rots import systemd

        mocker.patch(
            "subprocess.run",
            return_value=subprocess.CompletedProcess([], 1, stdout="", stderr=""),
        )

        with pytest.raises(SystemctlError, match="failed to enable"):
            systemd.enable("onetime-web@7043")


class TestDaemonReload:
    """Test daemon_reload function."""

    def test_daemon_reload_calls_systemctl(self, mocker):
        """Should call sudo systemctl daemon-reload."""
        from rots import systemd

        mock_run = mocker.patch(
            "subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, stdout="", stderr=""),
        )

        systemd.daemon_reload()

        mock_run.assert_called_once_with(
            ["sudo", "--", "systemctl", "daemon-reload"],
            capture_output=True,
            text=True,
            timeout=30,
        )

    def test_daemon_reload_raises_on_failure(self, mocker):
        """Should raise SystemctlError on failure."""
        from rots import systemd

        mocker.patch(
            "subprocess.run",
            return_value=subprocess.CompletedProcess([], 1, stdout="", stderr="fail"),
        )

        with pytest.raises(SystemctlError):
            systemd.daemon_reload()


class TestStart:
    """Test start function."""

    def test_start_calls_systemctl_start(self, mocker):
        """Should call sudo systemctl start with unit name."""
        from rots import systemd

        mock_run = mocker.patch(
            "subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, stdout="", stderr=""),
        )

        systemd.start("onetime-web@7043")

        mock_run.assert_called_once_with(
            ["sudo", "--", "systemctl", "start", "onetime-web@7043"],
            capture_output=True,
            text=True,
            timeout=90,
        )

    def test_start_raises_systemctl_error_on_failure(self, mocker):
        """Should raise SystemctlError with journal context on failure."""
        from rots import systemd

        mocker.patch(
            "subprocess.run",
            return_value=subprocess.CompletedProcess([], 1, stdout="", stderr=""),
        )

        with pytest.raises(SystemctlError, match="failed to start"):
            systemd.start("onetime-web@7043")


class TestStop:
    """Test stop function."""

    def test_stop_calls_systemctl_stop(self, mocker):
        """Should call sudo systemctl stop with unit name."""
        from rots import systemd

        mock_run = mocker.patch(
            "subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, stdout="", stderr=""),
        )

        systemd.stop("onetime-web@7043")

        mock_run.assert_called_once_with(
            ["sudo", "--", "systemctl", "stop", "onetime-web@7043"],
            capture_output=True,
            text=True,
            timeout=90,
        )

    def test_stop_raises_systemctl_error_on_failure(self, mocker):
        """Should raise SystemctlError with journal context on failure."""
        from rots import systemd

        mocker.patch(
            "subprocess.run",
            return_value=subprocess.CompletedProcess([], 1, stdout="", stderr=""),
        )

        with pytest.raises(SystemctlError, match="failed to stop"):
            systemd.stop("onetime-web@7043")


class TestRestart:
    """Test restart function."""

    def test_restart_calls_systemctl_restart(self, mocker):
        """Should call sudo systemctl restart with unit name."""
        from rots import systemd

        mock_run = mocker.patch(
            "subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, stdout="", stderr=""),
        )

        systemd.restart("onetime-web@7043")

        mock_run.assert_called_once_with(
            ["sudo", "--", "systemctl", "restart", "onetime-web@7043"],
            capture_output=True,
            text=True,
            timeout=90,
        )

    def test_restart_raises_systemctl_error_on_failure(self, mocker):
        """Should raise SystemctlError with journal context on failure."""
        from rots import systemd

        mocker.patch(
            "subprocess.run",
            return_value=subprocess.CompletedProcess([], 1, stdout="", stderr=""),
        )

        with pytest.raises(SystemctlError, match="failed to restart"):
            systemd.restart("onetime-web@7043")


class TestStatus:
    """Test status function."""

    def test_status_calls_systemctl_status(self, mocker):
        """Should call sudo systemctl status with unit name."""
        from rots import systemd

        mock_run = mocker.patch(
            "subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, stdout="", stderr=""),
        )

        systemd.status("onetime-web@7043")

        mock_run.assert_called_once_with(
            [
                "sudo",
                "--",
                "systemctl",
                "--no-pager",
                "-n25",
                "status",
                "onetime-web@7043",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )

    def test_status_custom_lines(self, mocker):
        """Should use custom line count when specified."""
        from rots import systemd

        mock_run = mocker.patch(
            "subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, stdout="", stderr=""),
        )

        systemd.status("onetime-web@7043", lines=50)

        mock_run.assert_called_once_with(
            [
                "sudo",
                "--",
                "systemctl",
                "--no-pager",
                "-n50",
                "status",
                "onetime-web@7043",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )

    def test_status_does_not_raise_on_nonzero_exit(self, mocker):
        """Should not raise when unit is not running (non-zero exit)."""
        from rots import systemd

        mock_run = mocker.patch(
            "subprocess.run",
            return_value=subprocess.CompletedProcess([], 3, stdout="inactive", stderr=""),
        )

        systemd.status("onetime-web@7043")  # Should not raise

        mock_run.assert_called_once()


class TestUnitToContainerName:
    """Test unit_to_container_name function."""

    def test_converts_web_template_instance_unit(self):
        """Should replace @ with - for valid podman container name."""
        from rots import systemd

        assert systemd.unit_to_container_name("onetime-web@7044") == "onetime-web-7044"

    def test_handles_service_suffix(self):
        """Should strip .service suffix and replace @."""
        from rots import systemd

        assert systemd.unit_to_container_name("onetime-web@7043.service") == "onetime-web-7043"

    def test_handles_different_ports(self):
        """Should work with various port numbers."""
        from rots import systemd

        assert systemd.unit_to_container_name("onetime-web@3000") == "onetime-web-3000"
        assert systemd.unit_to_container_name("onetime-web@8080") == "onetime-web-8080"

    def test_replaces_at_sign_in_container_name(self):
        """Container name replaces @ with - (@ is invalid in podman names)."""
        from rots import systemd

        assert systemd.unit_to_container_name("onetime-web@7043") == "onetime-web-7043"
        assert "@" not in systemd.unit_to_container_name("onetime-web@7043")


class TestRecreate:
    """Test recreate function."""

    def test_recreate_stops_removes_and_starts(self, mocker):
        """Should stop unit, remove container, then start unit."""
        from rots import systemd

        mock_run = mocker.patch(
            "subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, stdout="", stderr=""),
        )

        systemd.recreate("onetime-web@7044")

        assert mock_run.call_count == 3
        calls = mock_run.call_args_list
        assert calls[0][0][0] == ["sudo", "--", "systemctl", "stop", "onetime-web@7044"]
        assert calls[1][0][0] == [
            "sudo",
            "--",
            "podman",
            "rm",
            "--ignore",
            "onetime-web-7044",
        ]
        assert calls[2][0][0] == ["sudo", "--", "systemctl", "start", "onetime-web@7044"]

    def test_recreate_raises_on_stop_failure(self, mocker):
        """Should raise SystemctlError if stop fails."""
        from rots import systemd

        mocker.patch(
            "subprocess.run",
            return_value=subprocess.CompletedProcess([], 1, stdout="", stderr=""),
        )

        with pytest.raises(SystemctlError, match="failed to stop"):
            systemd.recreate("onetime-web@7044")


class TestContainerExists:
    """Test container_exists function."""

    def test_container_exists_returns_true_when_found(self, mocker):
        """Should return True when container exists."""
        from rots import systemd

        mock_result = mocker.Mock()
        mock_result.returncode = 0
        mocker.patch("subprocess.run", return_value=mock_result)

        assert systemd.container_exists("onetime-web@7044") is True

    def test_container_exists_returns_false_when_not_found(self, mocker):
        """Should return False when container doesn't exist."""
        from rots import systemd

        mock_result = mocker.Mock()
        mock_result.returncode = 1
        mocker.patch("subprocess.run", return_value=mock_result)

        assert systemd.container_exists("onetime-web@7044") is False

    def test_container_exists_uses_correct_container_name(self, mocker):
        """Should convert unit name to container name for podman check."""
        from rots import systemd

        mock_result = mocker.Mock()
        mock_result.returncode = 0
        mock_run = mocker.patch("subprocess.run", return_value=mock_result)

        systemd.container_exists("onetime-web@7044")

        mock_run.assert_called_once_with(
            ["podman", "container", "exists", "onetime-web-7044"],
            capture_output=True,
            text=True,
            timeout=10,
        )


class TestUnitExists:
    """Test unit_exists function."""

    def test_unit_exists_returns_true_when_found(self, mocker):
        """Should return True when unit file exists."""
        from rots import systemd

        mock_result = mocker.Mock()
        mock_result.stdout = "onetime-web@.container enabled enabled"
        mocker.patch("subprocess.run", return_value=mock_result)

        assert systemd.unit_exists("onetime-web@7043") is True

    def test_unit_exists_returns_false_when_not_found(self, mocker):
        """Should return False when unit file doesn't exist."""
        from rots import systemd

        mock_result = mocker.Mock()
        mock_result.stdout = ""
        mocker.patch("subprocess.run", return_value=mock_result)

        assert systemd.unit_exists("onetime-web@7043") is False

    def test_unit_exists_calls_systemctl_correctly(self, mocker):
        """Should call systemctl list-unit-files with correct args."""
        from rots import systemd

        mock_result = mocker.Mock()
        mock_result.stdout = ""
        mock_run = mocker.patch("subprocess.run", return_value=mock_result)

        systemd.unit_exists("onetime-web@7043")

        mock_run.assert_called_once_with(
            [
                "systemctl",
                "list-unit-files",
                "onetime-web@7043",
                "--plain",
                "--no-legend",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )


class TestDiscoverWorkerInstances:
    """Test discover_worker_instances function for background worker containers."""

    def test_discover_worker_instances_returns_sorted_ids(self, mocker):
        """Should parse systemctl output and return sorted worker ID list."""
        from rots import systemd

        mock_result = mocker.Mock()
        mock_result.stdout = (
            "onetime-worker@1.service loaded active running OTS Worker 1\n"
            "onetime-worker@3.service loaded active running OTS Worker 3\n"
            "onetime-worker@2.service loaded active running OTS Worker 2\n"
        )
        mocker.patch("subprocess.run", return_value=mock_result)

        ids = systemd.discover_worker_instances()

        # Numeric IDs should be sorted numerically
        assert ids == ["1", "2", "3"]

    def test_discover_worker_instances_with_string_ids(self, mocker):
        """Should correctly parse worker instances with string IDs."""
        from rots import systemd

        mock_result = mocker.Mock()
        mock_result.stdout = (
            "onetime-worker@billing.service loaded active running OTS Worker billing\n"
            "onetime-worker@emails.service loaded active running OTS Worker emails\n"
            "onetime-worker@cleanup.service loaded active running OTS Worker cleanup\n"
        )
        mocker.patch("subprocess.run", return_value=mock_result)

        ids = systemd.discover_worker_instances()

        # String IDs should be sorted alphabetically
        assert ids == ["billing", "cleanup", "emails"]

    def test_discover_worker_instances_mixed_ids(self, mocker):
        """Should handle mix of numeric and string worker IDs."""
        from rots import systemd

        mock_result = mocker.Mock()
        mock_result.stdout = (
            "onetime-worker@1.service loaded active running OTS Worker 1\n"
            "onetime-worker@billing.service loaded active running OTS Worker billing\n"
            "onetime-worker@2.service loaded active running OTS Worker 2\n"
        )
        mocker.patch("subprocess.run", return_value=mock_result)

        ids = systemd.discover_worker_instances()

        # Should return all IDs as strings, sorted
        assert "1" in ids
        assert "2" in ids
        assert "billing" in ids

    def test_discover_worker_instances_empty_output(self, mocker):
        """Should return empty list when no worker instances running."""
        from rots import systemd

        mock_result = mocker.Mock()
        mock_result.stdout = ""
        mocker.patch("subprocess.run", return_value=mock_result)

        ids = systemd.discover_worker_instances()

        assert ids == []

    def test_discover_worker_instances_ignores_web_instances(self, mocker):
        """Should ignore onetime-web@* (web) units, only return onetime-worker@* units."""
        from rots import systemd

        mock_result = mocker.Mock()
        mock_result.stdout = (
            "onetime-worker@1.service loaded active running OTS Worker 1\n"
            "onetime-web@7043.service loaded active running OTS Web 7043\n"
            "onetime-worker@2.service loaded active running OTS Worker 2\n"
        )
        mocker.patch("subprocess.run", return_value=mock_result)

        ids = systemd.discover_worker_instances()

        # Should only include worker instances, not web instances
        assert ids == ["1", "2"]
        assert "7043" not in ids

    def test_discover_worker_instances_calls_systemctl_correctly(self, mocker):
        """Should call systemctl with correct pattern for worker units."""
        from rots import systemd

        mock_result = mocker.Mock()
        mock_result.stdout = ""
        mock_run = mocker.patch("subprocess.run", return_value=mock_result)

        systemd.discover_worker_instances()

        mock_run.assert_called_once_with(
            ["systemctl", "list-units", "onetime-worker@*", "--plain", "--no-legend", "--all"],
            capture_output=True,
            text=True,
            timeout=10,
        )

    def test_discover_worker_instances_running_only(self, mocker):
        """With running_only=True, should only return running worker units."""
        from rots import systemd

        mock_result = mocker.Mock()
        mock_result.stdout = (
            "onetime-worker@1.service loaded active running OTS Worker 1\n"
            "onetime-worker@2.service loaded failed failed OTS Worker 2\n"
            "onetime-worker@3.service loaded inactive dead OTS Worker 3\n"
        )
        mocker.patch("subprocess.run", return_value=mock_result)

        ids = systemd.discover_worker_instances(running_only=True)

        assert ids == ["1"]

    def test_discover_worker_instances_returns_all_by_default(self, mocker):
        """Without running_only, should return all loaded worker units."""
        from rots import systemd

        mock_result = mocker.Mock()
        mock_result.stdout = (
            "onetime-worker@1.service loaded active running OTS Worker 1\n"
            "onetime-worker@2.service loaded failed failed OTS Worker 2\n"
            "onetime-worker@3.service loaded inactive dead OTS Worker 3\n"
        )
        mocker.patch("subprocess.run", return_value=mock_result)

        ids = systemd.discover_worker_instances()

        assert ids == ["1", "2", "3"]


class TestDiscoverSchedulerInstances:
    """Test discover_scheduler_instances function for scheduler containers."""

    def test_discover_scheduler_instances_returns_sorted_ids(self, mocker):
        """Should parse systemctl output and return sorted scheduler ID list."""
        from rots import systemd

        mock_result = mocker.Mock()
        mock_result.stdout = (
            "onetime-scheduler@main.service loaded active running OTS Scheduler main\n"
            "onetime-scheduler@cron.service loaded active running OTS Scheduler cron\n"
        )
        mocker.patch("subprocess.run", return_value=mock_result)

        ids = systemd.discover_scheduler_instances()

        assert ids == ["cron", "main"]

    def test_discover_scheduler_instances_empty_output(self, mocker):
        """Should return empty list when no scheduler instances running."""
        from rots import systemd

        mock_result = mocker.Mock()
        mock_result.stdout = ""
        mocker.patch("subprocess.run", return_value=mock_result)

        ids = systemd.discover_scheduler_instances()

        assert ids == []

    def test_discover_scheduler_instances_calls_systemctl_correctly(self, mocker):
        """Should call systemctl with correct pattern for scheduler units."""
        from rots import systemd

        mock_result = mocker.Mock()
        mock_result.stdout = ""
        mock_run = mocker.patch("subprocess.run", return_value=mock_result)

        systemd.discover_scheduler_instances()

        mock_run.assert_called_once_with(
            ["systemctl", "list-units", "onetime-scheduler@*", "--plain", "--no-legend", "--all"],
            capture_output=True,
            text=True,
            timeout=10,
        )

    def test_discover_scheduler_instances_running_only(self, mocker):
        """With running_only=True, should only return running scheduler units."""
        from rots import systemd

        mock_result = mocker.Mock()
        mock_result.stdout = (
            "onetime-scheduler@main.service loaded active running OTS Scheduler main\n"
            "onetime-scheduler@cron.service loaded failed failed OTS Scheduler cron\n"
        )
        mocker.patch("subprocess.run", return_value=mock_result)

        ids = systemd.discover_scheduler_instances(running_only=True)

        assert ids == ["main"]


class TestWorkerUnitToContainerName:
    """Test unit_to_container_name for worker units."""

    def test_converts_worker_unit_with_numeric_id(self):
        """Should replace @ with - for worker container name."""
        from rots import systemd

        assert systemd.unit_to_container_name("onetime-worker@1") == "onetime-worker-1"

    def test_converts_worker_unit_with_string_id(self):
        """Should replace @ with - for named worker."""
        from rots import systemd

        assert systemd.unit_to_container_name("onetime-worker@billing") == "onetime-worker-billing"

    def test_handles_worker_service_suffix(self):
        """Should strip .service suffix and replace @ in worker units."""
        from rots import systemd

        assert (
            systemd.unit_to_container_name("onetime-worker@emails.service")
            == "onetime-worker-emails"
        )


class TestSchedulerUnitToContainerName:
    """Test unit_to_container_name for scheduler units."""

    def test_converts_scheduler_unit(self):
        """Should replace @ with - for scheduler container name."""
        from rots import systemd

        assert systemd.unit_to_container_name("onetime-scheduler@main") == "onetime-scheduler-main"


class TestWorkerContainerExists:
    """Test container_exists for worker containers."""

    def test_worker_container_exists_checks_correct_name(self, mocker):
        """Should check for worker container with correct naming convention."""
        from rots import systemd

        mock_result = mocker.Mock()
        mock_result.returncode = 0
        mock_run = mocker.patch("subprocess.run", return_value=mock_result)

        systemd.container_exists("onetime-worker@billing")

        mock_run.assert_called_once_with(
            ["podman", "container", "exists", "onetime-worker-billing"],
            capture_output=True,
            text=True,
            timeout=10,
        )

    def test_worker_container_exists_with_numeric_id(self, mocker):
        """Should check for worker container with numeric ID."""
        from rots import systemd

        mock_result = mocker.Mock()
        mock_result.returncode = 0
        mock_run = mocker.patch("subprocess.run", return_value=mock_result)

        systemd.container_exists("onetime-worker@1")

        mock_run.assert_called_once_with(
            ["podman", "container", "exists", "onetime-worker-1"],
            capture_output=True,
            text=True,
            timeout=10,
        )


class TestRequireSystemctl:
    """Test require_systemctl function behavior when systemctl is missing."""

    def test_require_systemctl_exits_when_systemctl_missing(self, mocker):
        """Should raise SystemExit(1) when systemctl is not found."""
        from rots import systemd

        # Override the autouse fixture to simulate missing systemctl
        mocker.patch("shutil.which", return_value=None)

        with pytest.raises(SystemExit) as exc_info:
            systemd.require_systemctl()

        assert exc_info.value.code == 1

    def test_require_systemctl_message_mentions_shell_command(self, mocker, capsys):
        """Should print error message mentioning 'ots instance shell' for macOS."""
        from rots import systemd

        # Override the autouse fixture to simulate missing systemctl
        mocker.patch("shutil.which", return_value=None)

        with pytest.raises(SystemExit):
            systemd.require_systemctl()

        captured = capsys.readouterr()
        assert "ots instance shell" in captured.err
        assert "macOS" in captured.err

    def test_require_systemctl_passes_when_systemctl_available(self, mocker):
        """Should not raise when systemctl is available."""
        from rots import systemd

        # The autouse fixture already mocks this, but be explicit
        mocker.patch("shutil.which", return_value="/mock/bin/systemctl")

        # Should not raise
        systemd.require_systemctl()


class TestRequirePodman:
    """Test require_podman function behavior."""

    def test_require_podman_exits_when_podman_missing(self, mocker):
        """Should raise SystemExit(1) when podman is not found."""
        from rots import systemd

        mocker.patch("shutil.which", return_value=None)

        with pytest.raises(SystemExit) as exc_info:
            systemd.require_podman()

        assert exc_info.value.code == 1

    def test_require_podman_message_mentions_installation(self, mocker, capsys):
        """Should print error message with installation instructions."""
        from rots import systemd

        mocker.patch("shutil.which", return_value=None)

        with pytest.raises(SystemExit):
            systemd.require_podman()

        captured = capsys.readouterr()
        assert "podman" in captured.err.lower()
        assert "install" in captured.err.lower()

    def test_require_podman_passes_when_podman_available(self, mocker):
        """Should not raise when podman is available."""
        from rots import systemd

        mocker.patch("shutil.which", return_value="/usr/bin/podman")

        # Should not raise
        systemd.require_podman()


class TestWaitForHealthy:
    """Test wait_for_healthy polling logic."""

    def _make_is_active_result(self, mocker, state: str, returncode: int | None = None):
        """Build a CompletedProcess mock for systemctl is-active output."""
        if returncode is None:
            returncode = 0 if state == "active" else 1
        result = mocker.Mock()
        result.stdout = state
        result.returncode = returncode
        return result

    def test_returns_immediately_when_already_active(self, mocker):
        """Should return without sleeping when unit is active on first poll."""
        from rots import systemd

        active = self._make_is_active_result(mocker, "active", returncode=0)
        mocker.patch("subprocess.run", return_value=active)
        mock_sleep = mocker.patch("time.sleep")

        systemd.wait_for_healthy("onetime-web@7043", timeout=10)

        mock_sleep.assert_not_called()

    def test_raises_timeout_error_when_never_active(self, mocker):
        """Should raise HealthCheckTimeoutError when unit stays activating past timeout."""
        from rots import systemd
        from rots.systemd import HealthCheckTimeoutError

        activating = self._make_is_active_result(mocker, "activating")
        mocker.patch("subprocess.run", return_value=activating)
        mocker.patch("time.sleep")

        # Use a very short timeout and fake monotonic so we can control time
        times = iter([0.0, 0.0, 5.0, 5.0, 11.0])
        mocker.patch("time.monotonic", side_effect=times)

        with pytest.raises(HealthCheckTimeoutError) as exc_info:
            systemd.wait_for_healthy("onetime-web@7043", timeout=10)

        assert exc_info.value.unit == "onetime-web@7043"
        assert exc_info.value.last_state == "activating"

    def test_single_failed_does_not_exit_early(self, mocker):
        """A single 'failed' poll should not abort — it may be transient."""
        from rots import systemd

        failed = self._make_is_active_result(mocker, "failed")
        active = self._make_is_active_result(mocker, "active", returncode=0)
        # First poll: failed (transient), second poll: active
        mocker.patch("subprocess.run", side_effect=[failed, active])
        mock_sleep = mocker.patch("time.sleep")

        # Should NOT raise; should recover after the transient failure
        systemd.wait_for_healthy("onetime-web@7043", timeout=30, poll_interval=0.1)

        assert mock_sleep.call_count == 1

    def test_two_consecutive_failed_does_not_exit_early(self, mocker):
        """Two consecutive 'failed' polls should still not abort (threshold is 3)."""
        from rots import systemd

        failed = self._make_is_active_result(mocker, "failed")
        active = self._make_is_active_result(mocker, "active", returncode=0)
        mocker.patch("subprocess.run", side_effect=[failed, failed, active])
        mock_sleep = mocker.patch("time.sleep")

        systemd.wait_for_healthy("onetime-web@7043", timeout=30, poll_interval=0.1)

        assert mock_sleep.call_count == 2

    def test_three_consecutive_failed_exits_early(self, mocker):
        """Three consecutive 'failed' polls must raise immediately (terminal failure)."""
        from rots import systemd
        from rots.systemd import HealthCheckTimeoutError

        failed = self._make_is_active_result(mocker, "failed")
        mocker.patch("subprocess.run", return_value=failed)
        mocker.patch("time.sleep")
        # Ensure deadline never expires so we confirm it's the counter, not the clock
        mocker.patch("time.monotonic", return_value=0.0)

        with pytest.raises(HealthCheckTimeoutError) as exc_info:
            systemd.wait_for_healthy(
                "onetime-web@7043",
                timeout=9999,
                poll_interval=0.01,
                consecutive_failures_threshold=3,
            )

        assert exc_info.value.last_state == "failed"

    def test_failed_counter_resets_on_non_failed_state(self, mocker):
        """Counter should reset when a non-failed state is seen between failures."""
        from rots import systemd

        failed = self._make_is_active_result(mocker, "failed")
        activating = self._make_is_active_result(mocker, "activating")
        active = self._make_is_active_result(mocker, "active", returncode=0)
        # Pattern: failed, failed, activating (resets counter), failed, failed, active
        mocker.patch(
            "subprocess.run",
            side_effect=[failed, failed, activating, failed, failed, active],
        )
        mock_sleep = mocker.patch("time.sleep")

        # With threshold=3, the counter never reaches 3 because activating resets it
        systemd.wait_for_healthy(
            "onetime-web@7043",
            timeout=30,
            poll_interval=0.1,
            consecutive_failures_threshold=3,
        )

        assert mock_sleep.call_count == 5

    def test_custom_consecutive_failures_threshold(self, mocker):
        """Should respect a custom threshold value."""
        from rots import systemd
        from rots.systemd import HealthCheckTimeoutError

        failed = self._make_is_active_result(mocker, "failed")
        mocker.patch("subprocess.run", return_value=failed)
        mocker.patch("time.sleep")
        mocker.patch("time.monotonic", return_value=0.0)

        with pytest.raises(HealthCheckTimeoutError):
            systemd.wait_for_healthy(
                "onetime-web@7043",
                timeout=9999,
                poll_interval=0.01,
                consecutive_failures_threshold=1,
            )


class TestSystemctlErrorOnPortConflict:
    """Verify that a port-conflict start failure surfaces as a SystemctlError.

    In production, if port 7043 is already bound by another process, podman
    will fail to start the container. systemctl propagates the failure and
    returns a non-zero exit code, which _run_systemctl converts to
    SystemctlError. These tests confirm that behaviour.
    """

    def test_start_raises_systemctl_error_when_port_in_use(self, mocker):
        """start() must raise SystemctlError when systemctl reports non-zero exit."""
        from rots import systemd
        from rots.systemd import SystemctlError

        # Simulate: systemctl start fails (port conflict → podman error → non-zero)
        failed_result = subprocess.CompletedProcess(
            args=["sudo", "systemctl", "start", "onetime-web@7043"],
            returncode=1,
            stdout="",
            stderr="",
        )
        mocker.patch("subprocess.run", return_value=failed_result)

        with pytest.raises(SystemctlError) as exc_info:
            systemd.start("onetime-web@7043")

        assert exc_info.value.unit == "onetime-web@7043"
        assert exc_info.value.action == "start"

    def test_systemctl_error_message_identifies_unit_and_action(self, mocker):
        """SystemctlError str representation must name the unit and action."""
        from rots import systemd
        from rots.systemd import SystemctlError

        mocker.patch(
            "subprocess.run",
            return_value=subprocess.CompletedProcess([], 1, stdout="", stderr=""),
        )

        with pytest.raises(SystemctlError) as exc_info:
            systemd.start("onetime-web@7143")

        err_str = str(exc_info.value)
        assert "onetime-web@7143" in err_str
        assert "start" in err_str

    def test_journal_context_attached_to_error(self, mocker):
        """SystemctlError should carry journal output from _fetch_journal."""
        from rots import systemd
        from rots.systemd import SystemctlError

        journal_text = "Error: address already in use: bind: 0.0.0.0:7043"

        def fake_run(cmd, **kwargs):
            if "journalctl" in cmd:
                return subprocess.CompletedProcess(cmd, 0, stdout=journal_text)
            # systemctl start fails
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="")

        mocker.patch("subprocess.run", side_effect=fake_run)

        with pytest.raises(SystemctlError) as exc_info:
            systemd.start("onetime-web@7043")

        assert exc_info.value.journal == journal_text


class TestQuadletWritePermissionError:
    """Verify behaviour when /etc/containers/systemd/ is not writable.

    The quadlet _write_template function calls path.write_text(content).
    If the directory is owned by root and the process is unprivileged,
    Python raises PermissionError. We confirm the error propagates
    unchanged so callers (deploy command) can handle or surface it.
    """

    def test_write_template_propagates_permission_error(self, tmp_path, mocker):
        """PermissionError from path.write_text must propagate out of _write_template."""
        from rots import quadlet
        from rots.config import Config

        cfg = mocker.MagicMock(spec=Config)
        cfg.existing_config_files = []
        cfg.memory_max = None
        cfg.cpu_quota = None
        cfg.valkey_service = None
        cfg.registry = None
        cfg.config_dir = tmp_path / "etc"
        cfg.resolved_image_with_tag.return_value = "ghcr.io/test/image:v1.0.0"

        # secrets section: use a real env file with no secrets → force bypasses check
        env_file = tmp_path / "onetimesecret"
        env_file.write_text("")  # empty → no secrets

        # Use WORKER_TEMPLATE which needs no extra_vars (no valkey placeholders)
        target = tmp_path / "quadlet" / "onetime-worker@.container"

        # Patch path.write_text on the Path class to simulate a permission denied
        # error only for our specific target path
        original_write = type(target).write_text

        def patched_write(self, *args, **kwargs):
            if self == target:
                raise PermissionError(f"[Errno 13] Permission denied: '{target}'")
            return original_write(self, *args, **kwargs)

        mocker.patch.object(type(target), "write_text", patched_write)

        # Also mock daemon_reload so it doesn't try real systemctl
        mocker.patch("rots.quadlet.systemd.daemon_reload")

        with pytest.raises(PermissionError, match="Permission denied"):
            quadlet._write_template(
                quadlet.WORKER_TEMPLATE,
                target,
                cfg,
                env_file,
                force=True,  # skip secrets check
            )


class TestWaitForHttpHealthy:
    """Test wait_for_http_healthy polling logic."""

    def test_returns_immediately_when_health_endpoint_returns_200(self, mocker):
        """Should return without sleeping when endpoint responds 200 on first poll."""
        from rots import systemd

        mock_response = mocker.MagicMock()
        mock_response.__enter__ = mocker.MagicMock(return_value=mock_response)
        mock_response.__exit__ = mocker.MagicMock(return_value=False)
        mock_response.status = 200
        mocker.patch("urllib.request.urlopen", return_value=mock_response)
        mock_sleep = mocker.patch("time.sleep")

        systemd.wait_for_http_healthy(7043, timeout=10)

        mock_sleep.assert_not_called()

    def test_raises_timeout_error_when_endpoint_never_responds(self, mocker):
        """Should raise HttpHealthCheckTimeoutError when endpoint stays unavailable."""
        import urllib.error

        from rots import systemd
        from rots.systemd import HttpHealthCheckTimeoutError

        mocker.patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.URLError("Connection refused"),
        )
        mocker.patch("time.sleep")
        times = iter([0.0, 0.0, 5.0, 5.0, 11.0])
        mocker.patch("time.monotonic", side_effect=times)

        with pytest.raises(HttpHealthCheckTimeoutError) as exc_info:
            systemd.wait_for_http_healthy(7043, timeout=10)

        assert exc_info.value.port == 7043
        assert exc_info.value.timeout == 10

    def test_retries_on_connection_error_then_succeeds(self, mocker):
        """Should retry and succeed after initial connection errors."""
        import urllib.error

        from rots import systemd

        mock_response = mocker.MagicMock()
        mock_response.__enter__ = mocker.MagicMock(return_value=mock_response)
        mock_response.__exit__ = mocker.MagicMock(return_value=False)
        mock_response.status = 200

        mocker.patch(
            "urllib.request.urlopen",
            side_effect=[
                urllib.error.URLError("Connection refused"),
                mock_response,
            ],
        )
        mock_sleep = mocker.patch("time.sleep")

        systemd.wait_for_http_healthy(7043, timeout=30, poll_interval=0.1)

        assert mock_sleep.call_count == 1

    def test_retries_on_non_200_response(self, mocker):
        """Should retry when endpoint returns a non-200 status code."""

        from rots import systemd

        # First response: 503, second response: 200
        mock_bad = mocker.MagicMock()
        mock_bad.__enter__ = mocker.MagicMock(return_value=mock_bad)
        mock_bad.__exit__ = mocker.MagicMock(return_value=False)
        mock_bad.status = 503

        mock_ok = mocker.MagicMock()
        mock_ok.__enter__ = mocker.MagicMock(return_value=mock_ok)
        mock_ok.__exit__ = mocker.MagicMock(return_value=False)
        mock_ok.status = 200

        mocker.patch(
            "urllib.request.urlopen",
            side_effect=[mock_bad, mock_ok],
        )
        mock_sleep = mocker.patch("time.sleep")

        systemd.wait_for_http_healthy(7043, timeout=30, poll_interval=0.1)

        assert mock_sleep.call_count == 1

    def test_error_message_includes_port_and_timeout(self, mocker):
        """Error message should reference the port and timeout."""
        import urllib.error

        from rots import systemd
        from rots.systemd import HttpHealthCheckTimeoutError

        mocker.patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.URLError("refused"),
        )
        mocker.patch("time.sleep")
        mocker.patch("time.monotonic", side_effect=iter([0.0, 0.0, 11.0]))

        with pytest.raises(HttpHealthCheckTimeoutError) as exc_info:
            systemd.wait_for_http_healthy(7043, timeout=10)

        msg = str(exc_info.value)
        assert "7043" in msg
        assert "10" in msg

    def test_polls_correct_url(self, mocker):
        """Should poll http://localhost:{port}/health."""
        import urllib.error

        from rots import systemd

        calls = []

        def capture_urlopen(url, timeout=None):
            calls.append(url)
            raise urllib.error.URLError("refused")

        mocker.patch("urllib.request.urlopen", side_effect=capture_urlopen)
        mocker.patch("time.sleep")
        mocker.patch("time.monotonic", side_effect=iter([0.0, 0.0, 11.0]))

        try:
            systemd.wait_for_http_healthy(7044, timeout=10)
        except Exception:
            pass

        assert calls[0] == "http://localhost:7044/health"


class TestDiscoverInstancesSharedImpl:
    """Direct tests for the _discover_instances shared implementation.

    These target _discover_instances() itself to ensure the shared logic is
    fully covered independent of the public wrappers (discover_web_instances,
    discover_worker_instances, discover_scheduler_instances).
    """

    def test_unit_type_used_in_systemctl_pattern(self, mocker):
        """The unit_type arg must appear in the systemctl list-units glob pattern."""
        from rots import systemd

        mock_result = mocker.Mock()
        mock_result.stdout = ""
        mock_run = mocker.patch("subprocess.run", return_value=mock_result)

        systemd._discover_instances("scheduler")

        mock_run.assert_called_once_with(
            [
                "systemctl",
                "list-units",
                "onetime-scheduler@*",
                "--plain",
                "--no-legend",
                "--all",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )

    def test_regex_extracts_identifier_for_arbitrary_unit_type(self, mocker):
        """Pattern must correctly extract the identifier segment for any unit type."""
        from rots import systemd

        mock_result = mocker.Mock()
        # Simulate a hypothetical "relay" unit type
        mock_result.stdout = (
            "onetime-relay@primary.service loaded active running OTS Relay primary\n"
            "onetime-relay@secondary.service loaded active running OTS Relay secondary\n"
        )
        mocker.patch("subprocess.run", return_value=mock_result)

        ids = systemd._discover_instances("relay")

        assert ids == ["primary", "secondary"]

    def test_running_only_filters_inactive_for_arbitrary_type(self, mocker):
        """running_only=True must exclude non-running units for any unit type."""
        from rots import systemd

        mock_result = mocker.Mock()
        mock_result.stdout = (
            "onetime-relay@primary.service loaded active running OTS Relay primary\n"
            "onetime-relay@secondary.service loaded inactive dead OTS Relay secondary\n"
            "onetime-relay@tertiary.service loaded failed failed OTS Relay tertiary\n"
        )
        mocker.patch("subprocess.run", return_value=mock_result)

        ids = systemd._discover_instances("relay", running_only=True)

        assert ids == ["primary"]

    def test_running_only_false_returns_all_loaded_units(self, mocker):
        """running_only=False (default) must include all loaded units regardless of state."""
        from rots import systemd

        mock_result = mocker.Mock()
        mock_result.stdout = (
            "onetime-relay@primary.service loaded active running OTS Relay primary\n"
            "onetime-relay@secondary.service loaded inactive dead OTS Relay secondary\n"
            "onetime-relay@tertiary.service loaded failed failed OTS Relay tertiary\n"
        )
        mocker.patch("subprocess.run", return_value=mock_result)

        ids = systemd._discover_instances("relay", running_only=False)

        assert ids == ["primary", "secondary", "tertiary"]

    def test_non_digit_identifiers_returned_as_strings(self, mocker):
        """Named (non-numeric) identifiers must be returned as plain strings."""
        from rots import systemd

        mock_result = mocker.Mock()
        mock_result.stdout = (
            "onetime-worker@billing.service loaded active running OTS Worker billing\n"
            "onetime-worker@emails.service loaded active running OTS Worker emails\n"
        )
        mocker.patch("subprocess.run", return_value=mock_result)

        ids = systemd._discover_instances("worker")

        # Must be str, not int
        for id_ in ids:
            assert isinstance(id_, str), f"Expected str, got {type(id_)} for {id_!r}"
        assert ids == ["billing", "emails"]

    def test_numeric_identifiers_also_returned_as_strings(self, mocker):
        """Numeric identifiers returned by _discover_instances are strings too.

        The int conversion (for web ports) happens in discover_web_instances(),
        not in the shared _discover_instances() helper.
        """
        from rots import systemd

        mock_result = mocker.Mock()
        mock_result.stdout = (
            "onetime-worker@1.service loaded active running OTS Worker 1\n"
            "onetime-worker@2.service loaded active running OTS Worker 2\n"
        )
        mocker.patch("subprocess.run", return_value=mock_result)

        ids = systemd._discover_instances("worker")

        # _discover_instances always returns strings
        assert ids == ["1", "2"]
        for id_ in ids:
            assert isinstance(id_, str)

    def test_lines_with_fewer_than_four_parts_are_skipped(self, mocker):
        """Lines with < 4 columns must be silently ignored."""
        from rots import systemd

        mock_result = mocker.Mock()
        mock_result.stdout = (
            "onetime-worker@1.service loaded\n"  # only 2 parts - skip
            "onetime-worker@2.service loaded active running OTS Worker 2\n"  # valid
        )
        mocker.patch("subprocess.run", return_value=mock_result)

        ids = systemd._discover_instances("worker")

        assert ids == ["2"]

    def test_unloaded_units_are_skipped(self, mocker):
        """Units with load state != 'loaded' must be excluded."""
        from rots import systemd

        mock_result = mocker.Mock()
        mock_result.stdout = (
            "onetime-worker@1.service not-found active running OTS Worker 1\n"
            "onetime-worker@2.service loaded active running OTS Worker 2\n"
        )
        mocker.patch("subprocess.run", return_value=mock_result)

        ids = systemd._discover_instances("worker")

        assert ids == ["2"]
        assert "1" not in ids

    def test_results_are_sorted(self, mocker):
        """Returned identifiers must be in sorted order."""
        from rots import systemd

        mock_result = mocker.Mock()
        mock_result.stdout = (
            "onetime-worker@zeta.service loaded active running OTS Worker zeta\n"
            "onetime-worker@alpha.service loaded active running OTS Worker alpha\n"
            "onetime-worker@beta.service loaded active running OTS Worker beta\n"
        )
        mocker.patch("subprocess.run", return_value=mock_result)

        ids = systemd._discover_instances("worker")

        assert ids == sorted(ids)

    def test_non_matching_units_are_ignored_by_regex(self, mocker):
        """Units not matching the onetime-{type}@*.service pattern are skipped."""
        from rots import systemd

        mock_result = mocker.Mock()
        mock_result.stdout = (
            "onetime-worker@1.service loaded active running OTS Worker 1\n"
            "onetime-web@7043.service loaded active running OTS Web 7043\n"  # wrong type
            "caddy.service loaded active running Caddy\n"  # unrelated
        )
        mocker.patch("subprocess.run", return_value=mock_result)

        ids = systemd._discover_instances("worker")

        assert ids == ["1"]
        assert "7043" not in ids


class TestRequireSystemctlRemote:
    """Test require_systemctl() with a remote executor."""

    def test_require_systemctl_remote_found(self, mocker):
        """require_systemctl with remote executor should pass when 'which' succeeds."""
        from unittest.mock import MagicMock

        from rots import systemd

        mock_ex = MagicMock()
        mock_result = MagicMock()
        mock_result.ok = True
        mock_ex.run.return_value = mock_result
        # Make _is_local return False for this executor
        mocker.patch("rots.systemd._is_local", return_value=False)

        # Should not raise
        systemd.require_systemctl(executor=mock_ex)

        mock_ex.run.assert_called_once_with(["which", "systemctl"], timeout=10)

    def test_require_systemctl_remote_not_found(self, mocker):
        """require_systemctl with remote executor should exit when 'which' fails."""
        from unittest.mock import MagicMock

        from rots import systemd

        mock_ex = MagicMock()
        mock_result = MagicMock()
        mock_result.ok = False
        mock_ex.run.return_value = mock_result
        mocker.patch("rots.systemd._is_local", return_value=False)

        with pytest.raises(SystemExit) as exc_info:
            systemd.require_systemctl(executor=mock_ex)

        assert exc_info.value.code == 1

    def test_require_systemctl_local_fallback(self, mocker):
        """require_systemctl without executor should use shutil.which (existing behavior)."""
        from rots import systemd

        # The autouse fixture already mocks shutil.which to return a path
        # Should not raise
        systemd.require_systemctl()

    def test_require_systemctl_local_missing(self, mocker):
        """require_systemctl without executor exits when systemctl not on local PATH."""
        from rots import systemd

        mocker.patch("shutil.which", return_value=None)

        with pytest.raises(SystemExit) as exc_info:
            systemd.require_systemctl()

        assert exc_info.value.code == 1


class TestRequirePodmanRemote:
    """Test require_podman() with a remote executor."""

    def test_require_podman_remote_found(self, mocker):
        """require_podman with remote executor should pass when 'which' succeeds."""
        from unittest.mock import MagicMock

        from rots import systemd

        mock_ex = MagicMock()
        mock_result = MagicMock()
        mock_result.ok = True
        mock_ex.run.return_value = mock_result
        mocker.patch("rots.systemd._is_local", return_value=False)

        systemd.require_podman(executor=mock_ex)

        mock_ex.run.assert_called_once_with(["which", "podman"], timeout=10)

    def test_require_podman_remote_not_found(self, mocker):
        """require_podman with remote executor should exit when 'which' fails."""
        from unittest.mock import MagicMock

        from rots import systemd

        mock_ex = MagicMock()
        mock_result = MagicMock()
        mock_result.ok = False
        mock_ex.run.return_value = mock_result
        mocker.patch("rots.systemd._is_local", return_value=False)

        with pytest.raises(SystemExit) as exc_info:
            systemd.require_podman(executor=mock_ex)

        assert exc_info.value.code == 1

    def test_require_podman_local_fallback(self, mocker):
        """require_podman without executor should use shutil.which (existing behavior)."""
        from rots import systemd

        systemd.require_podman()

    def test_require_podman_local_missing(self, mocker):
        """require_podman without executor exits when podman not on local PATH."""
        from rots import systemd

        mocker.patch("shutil.which", return_value=None)

        with pytest.raises(SystemExit) as exc_info:
            systemd.require_podman()

        assert exc_info.value.code == 1


class TestWaitForHealthyRemote:
    """Test wait_for_healthy() with a remote executor."""

    def test_wait_for_healthy_remote_returns_when_active(self, mocker):
        """wait_for_healthy with remote executor should return when unit is active."""
        from unittest.mock import MagicMock

        from rots import systemd

        mock_ex = MagicMock()
        mock_result = MagicMock()
        mock_result.ok = True
        mock_result.stdout = "active"
        mock_ex.run.return_value = mock_result
        mocker.patch("rots.systemd._is_local", return_value=False)
        mock_sleep = mocker.patch("time.sleep")

        systemd.wait_for_healthy("onetime-web@7043", timeout=10, executor=mock_ex)

        mock_sleep.assert_not_called()
        mock_ex.run.assert_called_once_with(
            ["systemctl", "is-active", "onetime-web@7043"],
            timeout=10,
        )

    def test_wait_for_healthy_remote_polls_until_active(self, mocker):
        """wait_for_healthy with remote executor should poll until unit becomes active."""
        from unittest.mock import MagicMock

        from rots import systemd

        mock_ex = MagicMock()
        activating_result = MagicMock()
        activating_result.ok = False
        activating_result.stdout = "activating"
        active_result = MagicMock()
        active_result.ok = True
        active_result.stdout = "active"
        mock_ex.run.side_effect = [activating_result, active_result]
        mocker.patch("rots.systemd._is_local", return_value=False)
        mock_sleep = mocker.patch("time.sleep")

        systemd.wait_for_healthy("onetime-web@7043", timeout=30, executor=mock_ex)

        assert mock_sleep.call_count == 1

    def test_wait_for_healthy_remote_raises_timeout(self, mocker):
        """wait_for_healthy with remote executor should raise on timeout."""
        from unittest.mock import MagicMock

        from rots import systemd
        from rots.systemd import HealthCheckTimeoutError

        mock_ex = MagicMock()
        activating_result = MagicMock()
        activating_result.ok = False
        activating_result.stdout = "activating"
        mock_ex.run.return_value = activating_result
        mocker.patch("rots.systemd._is_local", return_value=False)
        mocker.patch("time.sleep")
        mocker.patch("time.monotonic", side_effect=iter([0.0, 0.0, 5.0, 5.0, 11.0]))

        with pytest.raises(HealthCheckTimeoutError) as exc_info:
            systemd.wait_for_healthy("onetime-web@7043", timeout=10, executor=mock_ex)

        assert exc_info.value.unit == "onetime-web@7043"
        assert exc_info.value.last_state == "activating"

    def test_wait_for_healthy_remote_skips_require_systemctl(self, mocker):
        """wait_for_healthy with remote executor should NOT call require_systemctl."""
        from unittest.mock import MagicMock

        from rots import systemd

        mock_ex = MagicMock()
        mock_result = MagicMock()
        mock_result.ok = True
        mock_result.stdout = "active"
        mock_ex.run.return_value = mock_result
        mocker.patch("rots.systemd._is_local", return_value=False)
        mock_require = mocker.patch("rots.systemd.require_systemctl")

        systemd.wait_for_healthy("onetime-web@7043", timeout=10, executor=mock_ex)

        mock_require.assert_not_called()


class TestWaitForHttpHealthyRemote:
    """Test wait_for_http_healthy() with a remote executor (curl branch)."""

    def test_returns_when_curl_succeeds(self, mocker):
        """wait_for_http_healthy with remote executor should return when curl ok."""
        from unittest.mock import MagicMock

        from rots import systemd

        mock_ex = MagicMock()
        mock_result = MagicMock()
        mock_result.ok = True
        mock_ex.run.return_value = mock_result
        mocker.patch("rots.systemd._is_local", return_value=False)
        mock_sleep = mocker.patch("time.sleep")

        systemd.wait_for_http_healthy(7043, timeout=10, executor=mock_ex)

        mock_sleep.assert_not_called()
        mock_ex.run.assert_called_once_with(
            ["curl", "-sf", "http://localhost:7043/health"],
            timeout=10,
        )

    def test_polls_until_curl_succeeds(self, mocker):
        """wait_for_http_healthy remote should poll until curl succeeds."""
        from unittest.mock import MagicMock

        from rots import systemd

        mock_ex = MagicMock()
        fail_result = MagicMock()
        fail_result.ok = False
        fail_result.returncode = 7
        ok_result = MagicMock()
        ok_result.ok = True
        mock_ex.run.side_effect = [fail_result, ok_result]
        mocker.patch("rots.systemd._is_local", return_value=False)
        mock_sleep = mocker.patch("time.sleep")

        systemd.wait_for_http_healthy(7043, timeout=30, poll_interval=0.1, executor=mock_ex)

        assert mock_sleep.call_count == 1

    def test_raises_timeout_when_curl_never_succeeds(self, mocker):
        """wait_for_http_healthy remote should raise timeout if curl always fails."""
        from unittest.mock import MagicMock

        from rots import systemd
        from rots.systemd import HttpHealthCheckTimeoutError

        mock_ex = MagicMock()
        fail_result = MagicMock()
        fail_result.ok = False
        fail_result.returncode = 7
        mock_ex.run.return_value = fail_result
        mocker.patch("rots.systemd._is_local", return_value=False)
        mocker.patch("time.sleep")
        mocker.patch("time.monotonic", side_effect=iter([0.0, 0.0, 5.0, 5.0, 11.0]))

        with pytest.raises(HttpHealthCheckTimeoutError) as exc_info:
            systemd.wait_for_http_healthy(7043, timeout=10, executor=mock_ex)

        assert exc_info.value.port == 7043
        assert exc_info.value.timeout == 10
        assert "curl exit 7" in exc_info.value.last_error

    def test_uses_curl_not_urllib_for_remote(self, mocker):
        """wait_for_http_healthy remote should use curl, not urllib."""
        from unittest.mock import MagicMock

        from rots import systemd

        mock_ex = MagicMock()
        mock_result = MagicMock()
        mock_result.ok = True
        mock_ex.run.return_value = mock_result
        mocker.patch("rots.systemd._is_local", return_value=False)
        mock_urlopen = mocker.patch("urllib.request.urlopen")

        systemd.wait_for_http_healthy(7043, timeout=10, executor=mock_ex)

        # urllib should NOT have been called (remote uses curl)
        mock_urlopen.assert_not_called()
        # curl should have been called via executor
        mock_ex.run.assert_called_once()
        call_args = mock_ex.run.call_args[0][0]
        assert call_args[0] == "curl"

    def test_correct_curl_url_includes_port(self, mocker):
        """wait_for_http_healthy remote should use the correct port in curl URL."""
        from unittest.mock import MagicMock

        from rots import systemd

        mock_ex = MagicMock()
        mock_result = MagicMock()
        mock_result.ok = True
        mock_ex.run.return_value = mock_result
        mocker.patch("rots.systemd._is_local", return_value=False)

        systemd.wait_for_http_healthy(8080, timeout=10, executor=mock_ex)

        call_args = mock_ex.run.call_args[0][0]
        assert "http://localhost:8080/health" in call_args


# ---------------------------------------------------------------------------
# Parametrized cross-executor tests
# ---------------------------------------------------------------------------


def _make_local_executor(mocker):
    """Build a LocalExecutor with subprocess.run mocked."""

    # Let _get_executor return a real LocalExecutor
    from ots_shared.ssh import LocalExecutor

    ex = LocalExecutor()
    return ex


def _make_remote_executor(mocker):
    """Build a mock SSHExecutor that _is_local() recognises as remote."""
    from unittest.mock import MagicMock

    mock_ex = MagicMock()
    # _is_local checks isinstance(ex, LocalExecutor), so MagicMock returns False
    return mock_ex


@pytest.fixture(
    params=["local", "remote"],
    ids=["local-executor", "remote-executor"],
)
def executor_pair(request, mocker):
    """Parametrized fixture that yields (executor, run_mock) for both types.

    For local: executor is a real LocalExecutor; run_mock patches subprocess.run.
    For remote: executor is a MagicMock; run_mock is executor.run itself.

    Both run_mocks return a successful result by default.
    """
    from unittest.mock import MagicMock

    if request.param == "local":
        from ots_shared.ssh import LocalExecutor

        ex = LocalExecutor()
        mock_run = mocker.patch(
            "subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, stdout="", stderr=""),
        )
        return ex, mock_run, "local"
    else:
        mock_ex = MagicMock()
        mock_result = MagicMock()
        mock_result.ok = True
        mock_result.stdout = ""
        mock_result.stderr = ""
        mock_result.returncode = 0
        mock_ex.run.return_value = mock_result
        mocker.patch("rots.systemd._is_local", return_value=False)
        return mock_ex, mock_ex.run, "remote"


def _extract_systemctl_cmd(run_mock, kind):
    """Extract the core systemctl command from a run mock.

    For local: subprocess.run receives ["sudo", "--", "systemctl", ...], so strip the prefix.
    For remote: executor.run receives ["systemctl", ...] directly (sudo is a kwarg).
    """
    cmd = run_mock.call_args[0][0]
    if kind == "local" and cmd[:2] == ["sudo", "--"]:
        return cmd[2:]
    return cmd


class TestCrossExecutorSystemdCommands:
    """Verify that systemd operations produce consistent commands across executor types.

    These parametrized tests run the same operation through both LocalExecutor and
    a mock SSHExecutor, verifying the systemctl command payload is identical.

    LocalExecutor prepends ``["sudo", "--"]`` at the subprocess.run layer,
    while SSHExecutor passes ``sudo=True`` as a kwarg to executor.run().
    The core systemctl command list must be the same in both cases.
    """

    def test_start_command_is_consistent(self, executor_pair):
        """start() should produce the same systemctl command payload via both executors."""
        from rots import systemd

        ex, run_mock, kind = executor_pair
        systemd.start("onetime-web@7043", executor=ex)

        assert _extract_systemctl_cmd(run_mock, kind) == [
            "systemctl",
            "start",
            "onetime-web@7043",
        ]

    def test_stop_command_is_consistent(self, executor_pair):
        """stop() should produce the same systemctl command payload via both executors."""
        from rots import systemd

        ex, run_mock, kind = executor_pair
        systemd.stop("onetime-web@7043", executor=ex)

        assert _extract_systemctl_cmd(run_mock, kind) == [
            "systemctl",
            "stop",
            "onetime-web@7043",
        ]

    def test_restart_command_is_consistent(self, executor_pair):
        """restart() should produce the same systemctl command payload via both executors."""
        from rots import systemd

        ex, run_mock, kind = executor_pair
        systemd.restart("onetime-web@7043", executor=ex)

        assert _extract_systemctl_cmd(run_mock, kind) == [
            "systemctl",
            "restart",
            "onetime-web@7043",
        ]

    def test_daemon_reload_command_is_consistent(self, executor_pair):
        """daemon_reload() should produce the same command payload via both executors."""
        from rots import systemd

        ex, run_mock, kind = executor_pair
        systemd.daemon_reload(executor=ex)

        assert _extract_systemctl_cmd(run_mock, kind) == [
            "systemctl",
            "daemon-reload",
        ]

    def test_is_active_command_and_result_consistent(self, executor_pair):
        """is_active() should produce the same command and parse output identically."""
        from rots import systemd

        ex, run_mock, kind = executor_pair

        if kind == "local":
            run_mock.return_value = subprocess.CompletedProcess([], 0, stdout="active\n", stderr="")
        else:
            mock_result = run_mock.return_value
            mock_result.stdout = "active\n"

        state = systemd.is_active("onetime-web@7043", executor=ex)

        assert state == "active"
        # is_active does NOT use sudo, so command is the same for both
        cmd = run_mock.call_args[0][0]
        assert cmd == ["systemctl", "is-active", "onetime-web@7043"]

    def test_enable_command_is_consistent(self, executor_pair):
        """enable() should produce the same systemctl command payload via both executors."""
        from rots import systemd

        ex, run_mock, kind = executor_pair
        systemd.enable("onetime-web@7043", executor=ex)

        assert _extract_systemctl_cmd(run_mock, kind) == [
            "systemctl",
            "enable",
            "onetime-web@7043",
        ]

    def test_sudo_used_for_mutating_commands(self, executor_pair):
        """Mutating systemctl commands should use sudo via both executor types."""
        from rots import systemd

        ex, run_mock, kind = executor_pair
        systemd.start("onetime-web@7043", executor=ex)

        if kind == "local":
            cmd = run_mock.call_args[0][0]
            # LocalExecutor prepends sudo -- at subprocess.run layer
            assert cmd[:2] == ["sudo", "--"]
        else:
            call_kwargs = run_mock.call_args[1]
            assert call_kwargs.get("sudo") is True

    def test_failure_raises_systemctl_error(self, executor_pair):
        """Both executor types should raise SystemctlError on start failure."""
        from rots import systemd
        from rots.systemd import SystemctlError

        ex, run_mock, kind = executor_pair

        if kind == "local":
            run_mock.return_value = subprocess.CompletedProcess([], 1, stdout="", stderr="")
        else:
            mock_result = run_mock.return_value
            mock_result.ok = False
            mock_result.returncode = 1
            mock_result.stdout = ""
            mock_result.stderr = ""

        with pytest.raises(SystemctlError, match="failed to start"):
            systemd.start("onetime-web@7043", executor=ex)
