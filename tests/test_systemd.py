# tests/test_systemd.py
"""Tests for systemd module — D-Bus backend (primary) and CLI fallback."""

import subprocess

import pytest

from rots._dbus import UnitInfo
from rots.systemd import SystemctlError

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_dbus_cache():
    """Reset the cached D-Bus availability flag between tests."""
    from rots.systemd import _dbus_is_available

    _dbus_is_available.cache_clear()
    yield
    _dbus_is_available.cache_clear()


@pytest.fixture(autouse=True)
def _reset_backend_override():
    """Reset the backend context variable between tests."""
    from rots import context

    token = context.backend_var.set(None)
    yield
    context.backend_var.reset(token)


@pytest.fixture(autouse=True)
def mock_systemctl_available(mocker):
    """Mock shutil.which to report systemctl as available for all tests."""
    mocker.patch("shutil.which", return_value="/mock/bin/systemctl")


@pytest.fixture()
def dbus_on(mocker):
    """Enable D-Bus backend for tests.

    Patches the availability check so the D-Bus code path is taken.
    Individual tests still need to mock the specific ``rots._dbus.*``
    functions they exercise.
    """
    mocker.patch("rots.systemd._dbus_is_available", return_value=True)


@pytest.fixture()
def dbus_off(mocker):
    """Disable D-Bus backend — forces CLI fallback path."""
    mocker.patch("rots.systemd._dbus_is_available", return_value=False)


# ===================================================================
# Pure helpers (no I/O, no D-Bus or CLI dependency)
# ===================================================================


class TestUnitName:
    """Test unit_name helper function."""

    def test_web_unit_name(self):
        from rots import systemd

        assert systemd.unit_name("web", "7043") == "onetime-web@7043"

    def test_worker_unit_name(self):
        from rots import systemd

        assert systemd.unit_name("worker", "1") == "onetime-worker@1"
        assert systemd.unit_name("worker", "billing") == "onetime-worker@billing"

    def test_scheduler_unit_name(self):
        from rots import systemd

        assert systemd.unit_name("scheduler", "main") == "onetime-scheduler@main"
        assert systemd.unit_name("scheduler", "1") == "onetime-scheduler@1"


class TestUnitToContainerName:
    """Test unit_to_container_name function."""

    def test_converts_web_template_instance_unit(self):
        from rots import systemd

        assert systemd.unit_to_container_name("onetime-web@7044") == "onetime-web-7044"

    def test_handles_service_suffix(self):
        from rots import systemd

        assert systemd.unit_to_container_name("onetime-web@7043.service") == "onetime-web-7043"

    def test_handles_different_ports(self):
        from rots import systemd

        assert systemd.unit_to_container_name("onetime-web@3000") == "onetime-web-3000"
        assert systemd.unit_to_container_name("onetime-web@8080") == "onetime-web-8080"

    def test_replaces_at_sign_in_container_name(self):
        from rots import systemd

        assert systemd.unit_to_container_name("onetime-web@7043") == "onetime-web-7043"
        assert "@" not in systemd.unit_to_container_name("onetime-web@7043")


class TestWorkerUnitToContainerName:
    """Test unit_to_container_name for worker units."""

    def test_converts_worker_unit_with_numeric_id(self):
        from rots import systemd

        assert systemd.unit_to_container_name("onetime-worker@1") == "onetime-worker-1"

    def test_converts_worker_unit_with_string_id(self):
        from rots import systemd

        assert systemd.unit_to_container_name("onetime-worker@billing") == "onetime-worker-billing"

    def test_handles_worker_service_suffix(self):
        from rots import systemd

        assert (
            systemd.unit_to_container_name("onetime-worker@emails.service")
            == "onetime-worker-emails"
        )


class TestSchedulerUnitToContainerName:
    """Test unit_to_container_name for scheduler units."""

    def test_converts_scheduler_unit(self):
        from rots import systemd

        assert systemd.unit_to_container_name("onetime-scheduler@main") == "onetime-scheduler-main"


# ===================================================================
# D-Bus backend tests (primary path)
# ===================================================================


class TestDiscoverWebInstancesDBus:
    """Test discover_web_instances via D-Bus."""

    def test_returns_sorted_ports(self, mocker, dbus_on):
        from rots import systemd

        mock_dbus = mocker.patch(
            "rots._dbus.list_units_by_pattern",
            return_value=[
                UnitInfo("onetime-web@7043.service", "loaded", "active", "running"),
                UnitInfo("onetime-web@7044.service", "loaded", "active", "running"),
                UnitInfo("onetime-web@7042.service", "loaded", "active", "running"),
            ],
        )

        ports = systemd.discover_web_instances()

        assert ports == [7042, 7043, 7044]
        mock_dbus.assert_called_once_with("onetime-web@*")

    def test_empty_output(self, mocker, dbus_on):
        from rots import systemd

        mocker.patch("rots._dbus.list_units_by_pattern", return_value=[])
        assert systemd.discover_web_instances() == []

    def test_ignores_malformed_and_non_numeric(self, mocker, dbus_on):
        from rots import systemd

        mocker.patch(
            "rots._dbus.list_units_by_pattern",
            return_value=[
                UnitInfo("onetime-web@7043.service", "loaded", "active", "running"),
                UnitInfo("some-other.service", "loaded", "active", "running"),
                UnitInfo("onetime-web@abc.service", "loaded", "active", "running"),
                UnitInfo("onetime-web@7044.service", "loaded", "active", "running"),
            ],
        )

        assert systemd.discover_web_instances() == [7043, 7044]

    def test_returns_all_loaded_by_default(self, mocker, dbus_on):
        from rots import systemd

        mocker.patch(
            "rots._dbus.list_units_by_pattern",
            return_value=[
                UnitInfo("onetime-web@7042.service", "loaded", "active", "running"),
                UnitInfo("onetime-web@7043.service", "loaded", "failed", "failed"),
                UnitInfo("onetime-web@7044.service", "loaded", "inactive", "dead"),
            ],
        )

        assert systemd.discover_web_instances() == [7042, 7043, 7044]

    def test_running_only_filters_failed(self, mocker, dbus_on):
        from rots import systemd

        mocker.patch(
            "rots._dbus.list_units_by_pattern",
            return_value=[
                UnitInfo("onetime-web@7043.service", "loaded", "failed", "failed"),
                UnitInfo("onetime-web@7044.service", "loaded", "failed", "failed"),
            ],
        )

        assert systemd.discover_web_instances(running_only=True) == []

    def test_running_only_mixed(self, mocker, dbus_on):
        from rots import systemd

        mocker.patch(
            "rots._dbus.list_units_by_pattern",
            return_value=[
                UnitInfo("onetime-web@7042.service", "loaded", "active", "running"),
                UnitInfo("onetime-web@7043.service", "loaded", "failed", "failed"),
                UnitInfo("onetime-web@7044.service", "loaded", "active", "running"),
            ],
        )

        assert systemd.discover_web_instances(running_only=True) == [7042, 7044]


class TestDiscoverWorkerInstancesDBus:
    """Test discover_worker_instances via D-Bus."""

    def test_returns_sorted_ids(self, mocker, dbus_on):
        from rots import systemd

        mocker.patch(
            "rots._dbus.list_units_by_pattern",
            return_value=[
                UnitInfo("onetime-worker@1.service", "loaded", "active", "running"),
                UnitInfo("onetime-worker@3.service", "loaded", "active", "running"),
                UnitInfo("onetime-worker@2.service", "loaded", "active", "running"),
            ],
        )

        assert systemd.discover_worker_instances() == ["1", "2", "3"]

    def test_string_ids(self, mocker, dbus_on):
        from rots import systemd

        mocker.patch(
            "rots._dbus.list_units_by_pattern",
            return_value=[
                UnitInfo("onetime-worker@billing.service", "loaded", "active", "running"),
                UnitInfo("onetime-worker@emails.service", "loaded", "active", "running"),
                UnitInfo("onetime-worker@cleanup.service", "loaded", "active", "running"),
            ],
        )

        assert systemd.discover_worker_instances() == ["billing", "cleanup", "emails"]

    def test_mixed_ids(self, mocker, dbus_on):
        from rots import systemd

        mocker.patch(
            "rots._dbus.list_units_by_pattern",
            return_value=[
                UnitInfo("onetime-worker@1.service", "loaded", "active", "running"),
                UnitInfo("onetime-worker@billing.service", "loaded", "active", "running"),
                UnitInfo("onetime-worker@2.service", "loaded", "active", "running"),
            ],
        )

        ids = systemd.discover_worker_instances()
        assert "1" in ids
        assert "2" in ids
        assert "billing" in ids

    def test_empty(self, mocker, dbus_on):
        from rots import systemd

        mocker.patch("rots._dbus.list_units_by_pattern", return_value=[])
        assert systemd.discover_worker_instances() == []

    def test_ignores_web_instances(self, mocker, dbus_on):
        from rots import systemd

        mocker.patch(
            "rots._dbus.list_units_by_pattern",
            return_value=[
                UnitInfo("onetime-worker@1.service", "loaded", "active", "running"),
                UnitInfo("onetime-web@7043.service", "loaded", "active", "running"),
                UnitInfo("onetime-worker@2.service", "loaded", "active", "running"),
            ],
        )

        ids = systemd.discover_worker_instances()
        assert ids == ["1", "2"]
        assert "7043" not in ids

    def test_running_only(self, mocker, dbus_on):
        from rots import systemd

        mocker.patch(
            "rots._dbus.list_units_by_pattern",
            return_value=[
                UnitInfo("onetime-worker@1.service", "loaded", "active", "running"),
                UnitInfo("onetime-worker@2.service", "loaded", "failed", "failed"),
                UnitInfo("onetime-worker@3.service", "loaded", "inactive", "dead"),
            ],
        )

        assert systemd.discover_worker_instances(running_only=True) == ["1"]

    def test_returns_all_by_default(self, mocker, dbus_on):
        from rots import systemd

        mocker.patch(
            "rots._dbus.list_units_by_pattern",
            return_value=[
                UnitInfo("onetime-worker@1.service", "loaded", "active", "running"),
                UnitInfo("onetime-worker@2.service", "loaded", "failed", "failed"),
                UnitInfo("onetime-worker@3.service", "loaded", "inactive", "dead"),
            ],
        )

        assert systemd.discover_worker_instances() == ["1", "2", "3"]


class TestDiscoverSchedulerInstancesDBus:
    """Test discover_scheduler_instances via D-Bus."""

    def test_returns_sorted_ids(self, mocker, dbus_on):
        from rots import systemd

        mocker.patch(
            "rots._dbus.list_units_by_pattern",
            return_value=[
                UnitInfo("onetime-scheduler@main.service", "loaded", "active", "running"),
                UnitInfo("onetime-scheduler@cron.service", "loaded", "active", "running"),
            ],
        )

        assert systemd.discover_scheduler_instances() == ["cron", "main"]

    def test_empty(self, mocker, dbus_on):
        from rots import systemd

        mocker.patch("rots._dbus.list_units_by_pattern", return_value=[])
        assert systemd.discover_scheduler_instances() == []

    def test_running_only(self, mocker, dbus_on):
        from rots import systemd

        mocker.patch(
            "rots._dbus.list_units_by_pattern",
            return_value=[
                UnitInfo("onetime-scheduler@main.service", "loaded", "active", "running"),
                UnitInfo("onetime-scheduler@cron.service", "loaded", "failed", "failed"),
            ],
        )

        assert systemd.discover_scheduler_instances(running_only=True) == ["main"]


class TestIsActiveDBus:
    """Test is_active via D-Bus."""

    def test_returns_active(self, mocker, dbus_on):
        from rots import systemd

        mocker.patch("rots._dbus.get_active_state", return_value="active")
        assert systemd.is_active("onetime-web@7043") == "active"

    def test_returns_inactive(self, mocker, dbus_on):
        from rots import systemd

        mocker.patch("rots._dbus.get_active_state", return_value="inactive")
        assert systemd.is_active("onetime-web@7043") == "inactive"

    def test_returns_failed(self, mocker, dbus_on):
        from rots import systemd

        mocker.patch("rots._dbus.get_active_state", return_value="failed")
        assert systemd.is_active("onetime-web@7043") == "failed"


class TestUnitExistsDBus:
    """Test unit_exists via D-Bus."""

    def test_returns_true_when_found(self, mocker, dbus_on):
        from rots import systemd

        mocker.patch("rots._dbus.unit_file_exists", return_value=True)
        assert systemd.unit_exists("onetime-web@7043") is True

    def test_returns_false_when_not_found(self, mocker, dbus_on):
        from rots import systemd

        mocker.patch("rots._dbus.unit_file_exists", return_value=False)
        assert systemd.unit_exists("onetime-web@7043") is False


class TestStartDBus:
    """Test start via D-Bus."""

    def test_calls_dbus_start(self, mocker, dbus_on):
        from rots import systemd

        mock_start = mocker.patch("rots._dbus.start_unit")
        systemd.start("onetime-web@7043")
        mock_start.assert_called_once_with("onetime-web@7043")

    def test_raises_systemctl_error_on_dbus_failure(self, mocker, dbus_on):
        from rots import systemd

        mocker.patch("rots._dbus.start_unit", side_effect=RuntimeError("D-Bus error"))
        mocker.patch(
            "subprocess.run",
            return_value=subprocess.CompletedProcess(
                [],
                0,
                stdout="journal output",
                stderr="",
            ),
        )

        with pytest.raises(SystemctlError, match="failed to start"):
            systemd.start("onetime-web@7043")


class TestStopDBus:
    """Test stop via D-Bus."""

    def test_calls_dbus_stop(self, mocker, dbus_on):
        from rots import systemd

        mock_stop = mocker.patch("rots._dbus.stop_unit")
        systemd.stop("onetime-web@7043")
        mock_stop.assert_called_once_with("onetime-web@7043")

    def test_raises_systemctl_error_on_dbus_failure(self, mocker, dbus_on):
        from rots import systemd

        mocker.patch("rots._dbus.stop_unit", side_effect=RuntimeError("D-Bus error"))
        mocker.patch(
            "subprocess.run",
            return_value=subprocess.CompletedProcess(
                [],
                0,
                stdout="journal output",
                stderr="",
            ),
        )

        with pytest.raises(SystemctlError, match="failed to stop"):
            systemd.stop("onetime-web@7043")


class TestRestartDBus:
    """Test restart via D-Bus."""

    def test_calls_dbus_restart(self, mocker, dbus_on):
        from rots import systemd

        mock_restart = mocker.patch("rots._dbus.restart_unit")
        systemd.restart("onetime-web@7043")
        mock_restart.assert_called_once_with("onetime-web@7043")

    def test_raises_systemctl_error_on_dbus_failure(self, mocker, dbus_on):
        from rots import systemd

        mocker.patch("rots._dbus.restart_unit", side_effect=RuntimeError("D-Bus error"))
        mocker.patch(
            "subprocess.run",
            return_value=subprocess.CompletedProcess(
                [],
                0,
                stdout="journal output",
                stderr="",
            ),
        )

        with pytest.raises(SystemctlError, match="failed to restart"):
            systemd.restart("onetime-web@7043")


class TestEnableDBus:
    """Test enable via D-Bus."""

    def test_calls_dbus_enable(self, mocker, dbus_on):
        from rots import systemd

        mock_enable = mocker.patch("rots._dbus.enable_unit_files")
        systemd.enable("onetime-web@7043")
        mock_enable.assert_called_once_with(["onetime-web@7043"])

    def test_raises_systemctl_error_on_failure(self, mocker, dbus_on):
        from rots import systemd

        mocker.patch("rots._dbus.enable_unit_files", side_effect=RuntimeError("D-Bus error"))
        mocker.patch(
            "subprocess.run",
            return_value=subprocess.CompletedProcess(
                [],
                0,
                stdout="journal",
                stderr="",
            ),
        )

        with pytest.raises(SystemctlError, match="failed to enable"):
            systemd.enable("onetime-web@7043")


class TestDisableDBus:
    """Test disable via D-Bus."""

    def test_calls_dbus_disable(self, mocker, dbus_on):
        from rots import systemd

        mock_disable = mocker.patch("rots._dbus.disable_unit_files")
        systemd.disable("onetime-web@7043")
        mock_disable.assert_called_once_with(["onetime-web@7043"])

    def test_suppresses_errors(self, mocker, dbus_on):
        """disable() should not raise on D-Bus errors (matches CLI behavior)."""
        from rots import systemd

        mocker.patch("rots._dbus.disable_unit_files", side_effect=RuntimeError("not enabled"))
        systemd.disable("onetime-web@7043")  # Should not raise


class TestDaemonReloadDBus:
    """Test daemon_reload via D-Bus."""

    def test_calls_dbus_reload(self, mocker, dbus_on):
        from rots import systemd

        mock_reload = mocker.patch("rots._dbus.reload_manager")
        systemd.daemon_reload()
        mock_reload.assert_called_once()

    def test_raises_systemctl_error_on_failure(self, mocker, dbus_on):
        from rots import systemd

        mocker.patch("rots._dbus.reload_manager", side_effect=RuntimeError("D-Bus error"))

        with pytest.raises(SystemctlError):
            systemd.daemon_reload()


class TestResetFailedDBus:
    """Test reset_failed via D-Bus."""

    def test_calls_dbus_reset(self, mocker, dbus_on):
        from rots import systemd

        mock_reset = mocker.patch("rots._dbus.reset_failed_unit")
        systemd.reset_failed("onetime-web@7043")
        mock_reset.assert_called_once_with("onetime-web@7043")

    def test_suppresses_errors(self, mocker, dbus_on):
        from rots import systemd

        mocker.patch("rots._dbus.reset_failed_unit", side_effect=RuntimeError("no such unit"))
        systemd.reset_failed("onetime-web@7043")  # Should not raise


class TestRecreateDBus:
    """Test recreate via D-Bus (stop/start) + CLI (podman rm)."""

    def test_stops_removes_and_starts(self, mocker, dbus_on):
        from rots import systemd

        mock_stop = mocker.patch("rots._dbus.stop_unit")
        mock_start = mocker.patch("rots._dbus.start_unit")
        mock_run = mocker.patch(
            "subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, stdout="", stderr=""),
        )

        systemd.recreate("onetime-web@7044")

        mock_stop.assert_called_once_with("onetime-web@7044")
        mock_start.assert_called_once_with("onetime-web@7044")
        # podman rm still goes through subprocess
        mock_run.assert_called_once_with(
            ["sudo", "--", "podman", "rm", "--ignore", "onetime-web-7044"],
            capture_output=True,
            text=True,
            timeout=30,
        )

    def test_raises_on_stop_failure(self, mocker, dbus_on):
        from rots import systemd

        mocker.patch("rots._dbus.stop_unit", side_effect=RuntimeError("stop failed"))
        mocker.patch(
            "subprocess.run",
            return_value=subprocess.CompletedProcess(
                [],
                0,
                stdout="journal",
                stderr="",
            ),
        )

        with pytest.raises(SystemctlError, match="failed to stop"):
            systemd.recreate("onetime-web@7044")


# ===================================================================
# CLI fallback tests (D-Bus unavailable)
# ===================================================================


class TestDiscoverWebInstancesCLI:
    """Test discover_web_instances CLI fallback."""

    def test_returns_sorted_ports(self, mocker, dbus_off):
        from rots import systemd

        mock_result = mocker.Mock()
        mock_result.stdout = (
            "onetime-web@7043.service loaded active running OTS 7043\n"
            "onetime-web@7044.service loaded active running OTS 7044\n"
            "onetime-web@7042.service loaded active running OTS 7042\n"
        )
        mocker.patch("subprocess.run", return_value=mock_result)

        assert systemd.discover_web_instances() == [7042, 7043, 7044]

    def test_empty_output(self, mocker, dbus_off):
        from rots import systemd

        mock_result = mocker.Mock()
        mock_result.stdout = ""
        mocker.patch("subprocess.run", return_value=mock_result)

        assert systemd.discover_web_instances() == []

    def test_calls_systemctl_correctly(self, mocker, dbus_off):
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


class TestDiscoverWorkerInstancesCLI:
    """Test discover_worker_instances CLI fallback."""

    def test_returns_sorted_ids(self, mocker, dbus_off):
        from rots import systemd

        mock_result = mocker.Mock()
        mock_result.stdout = (
            "onetime-worker@1.service loaded active running OTS Worker 1\n"
            "onetime-worker@3.service loaded active running OTS Worker 3\n"
            "onetime-worker@2.service loaded active running OTS Worker 2\n"
        )
        mocker.patch("subprocess.run", return_value=mock_result)

        assert systemd.discover_worker_instances() == ["1", "2", "3"]

    def test_calls_systemctl_correctly(self, mocker, dbus_off):
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


class TestDiscoverSchedulerInstancesCLI:
    """Test discover_scheduler_instances CLI fallback."""

    def test_returns_sorted_ids(self, mocker, dbus_off):
        from rots import systemd

        mock_result = mocker.Mock()
        mock_result.stdout = (
            "onetime-scheduler@main.service loaded active running OTS Scheduler main\n"
            "onetime-scheduler@cron.service loaded active running OTS Scheduler cron\n"
        )
        mocker.patch("subprocess.run", return_value=mock_result)

        assert systemd.discover_scheduler_instances() == ["cron", "main"]

    def test_calls_systemctl_correctly(self, mocker, dbus_off):
        from rots import systemd

        mock_result = mocker.Mock()
        mock_result.stdout = ""
        mock_run = mocker.patch("subprocess.run", return_value=mock_result)

        systemd.discover_scheduler_instances()

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


class TestIsActiveCLI:
    """Test is_active CLI fallback."""

    def test_returns_active(self, mocker, dbus_off):
        from rots import systemd

        mock_result = mocker.Mock()
        mock_result.stdout = "active\n"
        mock_result.returncode = 0
        mock_result.ok = True
        mocker.patch("subprocess.run", return_value=mock_result)

        assert systemd.is_active("onetime-web@7043") == "active"

    def test_returns_inactive(self, mocker, dbus_off):
        from rots import systemd

        mock_result = mocker.Mock()
        mock_result.stdout = "inactive\n"
        mock_result.returncode = 3
        mock_result.ok = False
        mocker.patch("subprocess.run", return_value=mock_result)

        assert systemd.is_active("onetime-web@7043") == "inactive"

    def test_calls_systemctl_correctly(self, mocker, dbus_off):
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


class TestStartCLI:
    """Test start CLI fallback."""

    def test_calls_systemctl_start(self, mocker, dbus_off):
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

    def test_raises_systemctl_error_on_failure(self, mocker, dbus_off):
        from rots import systemd

        mocker.patch(
            "subprocess.run",
            return_value=subprocess.CompletedProcess([], 1, stdout="", stderr=""),
        )

        with pytest.raises(SystemctlError, match="failed to start"):
            systemd.start("onetime-web@7043")


class TestStopCLI:
    """Test stop CLI fallback."""

    def test_calls_systemctl_stop(self, mocker, dbus_off):
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

    def test_raises_systemctl_error_on_failure(self, mocker, dbus_off):
        from rots import systemd

        mocker.patch(
            "subprocess.run",
            return_value=subprocess.CompletedProcess([], 1, stdout="", stderr=""),
        )

        with pytest.raises(SystemctlError, match="failed to stop"):
            systemd.stop("onetime-web@7043")


class TestRestartCLI:
    """Test restart CLI fallback."""

    def test_calls_systemctl_restart(self, mocker, dbus_off):
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

    def test_raises_systemctl_error_on_failure(self, mocker, dbus_off):
        from rots import systemd

        mocker.patch(
            "subprocess.run",
            return_value=subprocess.CompletedProcess([], 1, stdout="", stderr=""),
        )

        with pytest.raises(SystemctlError, match="failed to restart"):
            systemd.restart("onetime-web@7043")


class TestEnableCLI:
    """Test enable CLI fallback."""

    def test_calls_systemctl_enable(self, mocker, dbus_off):
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

    def test_raises_systemctl_error_on_failure(self, mocker, dbus_off):
        from rots import systemd

        mocker.patch(
            "subprocess.run",
            return_value=subprocess.CompletedProcess([], 1, stdout="", stderr=""),
        )

        with pytest.raises(SystemctlError, match="failed to enable"):
            systemd.enable("onetime-web@7043")


class TestDaemonReloadCLI:
    """Test daemon_reload CLI fallback."""

    def test_calls_systemctl(self, mocker, dbus_off):
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

    def test_raises_on_failure(self, mocker, dbus_off):
        from rots import systemd

        mocker.patch(
            "subprocess.run",
            return_value=subprocess.CompletedProcess([], 1, stdout="", stderr="fail"),
        )

        with pytest.raises(SystemctlError):
            systemd.daemon_reload()


class TestStatusCLI:
    """Test status function (always CLI — for human-readable output)."""

    def test_calls_systemctl_status(self, mocker, dbus_off):
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

    def test_custom_lines(self, mocker, dbus_off):
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

    def test_does_not_raise_on_nonzero_exit(self, mocker, dbus_off):
        from rots import systemd

        mocker.patch(
            "subprocess.run",
            return_value=subprocess.CompletedProcess([], 3, stdout="inactive", stderr=""),
        )

        systemd.status("onetime-web@7043")  # Should not raise


class TestUnitExistsCLI:
    """Test unit_exists CLI fallback."""

    def test_returns_true_when_found(self, mocker, dbus_off):
        from rots import systemd

        mock_result = mocker.Mock()
        mock_result.stdout = "onetime-web@.container enabled enabled"
        mocker.patch("subprocess.run", return_value=mock_result)

        assert systemd.unit_exists("onetime-web@7043") is True

    def test_returns_false_when_not_found(self, mocker, dbus_off):
        from rots import systemd

        mock_result = mocker.Mock()
        mock_result.stdout = ""
        mocker.patch("subprocess.run", return_value=mock_result)

        assert systemd.unit_exists("onetime-web@7043") is False

    def test_calls_systemctl_correctly(self, mocker, dbus_off):
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


class TestContainerExists:
    """Test container_exists (always CLI — podman operation)."""

    def test_returns_true_when_found(self, mocker, dbus_off):
        from rots import systemd

        mock_result = mocker.Mock()
        mock_result.returncode = 0
        mocker.patch("subprocess.run", return_value=mock_result)

        assert systemd.container_exists("onetime-web@7044") is True

    def test_returns_false_when_not_found(self, mocker, dbus_off):
        from rots import systemd

        mock_result = mocker.Mock()
        mock_result.returncode = 1
        mocker.patch("subprocess.run", return_value=mock_result)

        assert systemd.container_exists("onetime-web@7044") is False

    def test_uses_correct_container_name(self, mocker, dbus_off):
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


class TestWorkerContainerExists:
    """Test container_exists for worker containers."""

    def test_checks_correct_name(self, mocker, dbus_off):
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

    def test_numeric_id(self, mocker, dbus_off):
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


class TestRecreateCLI:
    """Test recreate CLI fallback."""

    def test_stops_removes_and_starts(self, mocker, dbus_off):
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

    def test_raises_on_stop_failure(self, mocker, dbus_off):
        from rots import systemd

        mocker.patch(
            "subprocess.run",
            return_value=subprocess.CompletedProcess([], 1, stdout="", stderr=""),
        )

        with pytest.raises(SystemctlError, match="failed to stop"):
            systemd.recreate("onetime-web@7044")


class TestRequireSystemctl:
    """Test require_systemctl when systemctl is missing."""

    def test_exits_when_systemctl_missing(self, mocker):
        from rots import systemd

        mocker.patch("shutil.which", return_value=None)

        with pytest.raises(SystemExit) as exc_info:
            systemd.require_systemctl()

        assert exc_info.value.code == 1


# ===================================================================
# Unit name normalization (_dbus._normalize)
# ===================================================================


class TestNormalize:
    """Test _normalize helper in _dbus module."""

    def test_appends_service_suffix(self):
        from rots._dbus import _normalize

        assert _normalize("onetime-web@7043") == "onetime-web@7043.service"

    def test_preserves_existing_service_suffix(self):
        from rots._dbus import _normalize

        assert _normalize("onetime-web@7043.service") == "onetime-web@7043.service"

    def test_preserves_other_suffixes(self):
        from rots._dbus import _normalize

        assert _normalize("myunit.timer") == "myunit.timer"
        assert _normalize("myunit.socket") == "myunit.socket"

    def test_template_instance_without_suffix(self):
        from rots._dbus import _normalize

        assert _normalize("onetime-worker@billing") == "onetime-worker@billing.service"

    def test_plain_unit_without_suffix(self):
        from rots._dbus import _normalize

        assert _normalize("sshd") == "sshd.service"


# ===================================================================
# Remote executor tests (D-Bus should never be used over SSH)
# ===================================================================


class TestRemoteExecutorUsesCLI:
    """Verify that remote executors always use CLI, even when D-Bus is available."""

    def _make_remote_executor(self, mocker):
        """Create a mock executor that is NOT a LocalExecutor."""
        mock_ex = mocker.Mock()
        # _is_local checks isinstance(executor, LocalExecutor) — a Mock is not.
        return mock_ex

    def test_start_uses_cli_for_remote(self, mocker):
        """start() should shell out via the remote executor, not D-Bus."""
        from rots import systemd

        # Even if D-Bus reports available, remote executor must use CLI
        mocker.patch("rots.systemd._dbus_is_available", return_value=True)
        mock_ex = self._make_remote_executor(mocker)
        mock_ex.run.return_value = mocker.Mock(ok=True, stdout="", stderr="")

        systemd.start("onetime-web@7043", executor=mock_ex)

        mock_ex.run.assert_called_once_with(
            ["systemctl", "start", "onetime-web@7043"],
            sudo=True,
            timeout=90,
        )

    def test_discover_uses_cli_for_remote(self, mocker):
        """discover_web_instances() should shell out via the remote executor."""
        from rots import systemd

        mocker.patch("rots.systemd._dbus_is_available", return_value=True)
        mock_ex = self._make_remote_executor(mocker)
        mock_ex.run.return_value = mocker.Mock(
            ok=True,
            stdout="onetime-web@7043.service loaded active running OTS\n",
        )

        ports = systemd.discover_web_instances(executor=mock_ex)

        assert ports == [7043]
        mock_ex.run.assert_called_once()

    def test_is_active_uses_cli_for_remote(self, mocker):
        """is_active() should shell out via the remote executor."""
        from rots import systemd

        mocker.patch("rots.systemd._dbus_is_available", return_value=True)
        mock_ex = self._make_remote_executor(mocker)
        mock_ex.run.return_value = mocker.Mock(stdout="active\n")

        state = systemd.is_active("onetime-web@7043", executor=mock_ex)

        assert state == "active"
        mock_ex.run.assert_called_once_with(
            ["systemctl", "is-active", "onetime-web@7043"],
            timeout=10,
        )


# ===================================================================
# wait_for_healthy tests (both D-Bus and CLI paths)
# ===================================================================


class TestWaitForHealthyDBus:
    """Test wait_for_healthy via D-Bus polling."""

    def test_returns_immediately_when_active(self, mocker, dbus_on):
        from rots import systemd

        mocker.patch("rots._dbus.get_active_state", return_value="active")

        systemd.wait_for_healthy("onetime-web@7043", timeout=5, poll_interval=0.01)

    def test_raises_on_persistent_failure(self, mocker, dbus_on):
        from rots import systemd
        from rots.systemd import HealthCheckTimeoutError

        mocker.patch("rots._dbus.get_active_state", return_value="failed")

        with pytest.raises(HealthCheckTimeoutError, match="failed"):
            systemd.wait_for_healthy(
                "onetime-web@7043",
                timeout=0.1,
                poll_interval=0.01,
                consecutive_failures_threshold=2,
            )

    def test_tolerates_dbus_exceptions_as_unknown(self, mocker, dbus_on):
        """D-Bus exceptions during polling should be treated as unknown state."""
        from rots import systemd
        from rots.systemd import HealthCheckTimeoutError

        mocker.patch("rots._dbus.get_active_state", side_effect=RuntimeError("D-Bus gone"))

        with pytest.raises(HealthCheckTimeoutError, match="unknown"):
            systemd.wait_for_healthy(
                "onetime-web@7043",
                timeout=0.1,
                poll_interval=0.01,
            )


class TestWaitForHealthyCLI:
    """Test wait_for_healthy CLI fallback polling."""

    def test_returns_immediately_when_active(self, mocker, dbus_off):
        from rots import systemd

        mock_result = mocker.Mock()
        mock_result.ok = True
        mock_result.stdout = "active"
        mocker.patch("subprocess.run", return_value=mock_result)

        systemd.wait_for_healthy("onetime-web@7043", timeout=5, poll_interval=0.01)

    def test_raises_on_timeout(self, mocker, dbus_off):
        from rots import systemd
        from rots.systemd import HealthCheckTimeoutError

        mock_result = mocker.Mock()
        mock_result.ok = False
        mock_result.stdout = "activating"
        mocker.patch("subprocess.run", return_value=mock_result)

        with pytest.raises(HealthCheckTimeoutError, match="activating"):
            systemd.wait_for_healthy(
                "onetime-web@7043",
                timeout=0.1,
                poll_interval=0.01,
            )


# ===================================================================
# Backend override (--backend flag)
# ===================================================================


class TestBackendOverride:
    """Tests for the --backend dbus|cli context variable override."""

    def test_cli_override_forces_cli_path(self, mocker):
        """--backend=cli forces CLI even when D-Bus is available."""
        from rots import context, systemd

        mocker.patch("rots.systemd._dbus_is_available", return_value=True)
        context.backend_var.set("cli")
        assert systemd._use_dbus(None) is False

    def test_dbus_override_forces_dbus_path(self, mocker):
        """--backend=dbus forces D-Bus when it is available."""
        from rots import context, systemd

        mocker.patch("rots.systemd._dbus_is_available", return_value=True)
        context.backend_var.set("dbus")
        assert systemd._use_dbus(None) is True

    def test_dbus_override_warns_when_unavailable(self, mocker, caplog):
        """--backend=dbus falls back to CLI with a warning when D-Bus is unavailable."""
        from rots import context, systemd

        mocker.patch("rots.systemd._dbus_is_available", return_value=False)
        context.backend_var.set("dbus")
        assert systemd._use_dbus(None) is False
        assert "--backend=dbus requested but D-Bus is not available" in caplog.text

    def test_dbus_override_still_requires_local_executor(self, mocker):
        """--backend=dbus cannot enable D-Bus for remote executors."""
        from rots import context, systemd

        mocker.patch("rots.systemd._dbus_is_available", return_value=True)
        remote_exec = mocker.Mock()
        remote_exec.__class__.__name__ = "SSHExecutor"
        mocker.patch("rots.systemd._is_local", return_value=False)
        context.backend_var.set("dbus")
        assert systemd._use_dbus(remote_exec) is False

    def test_no_override_uses_auto_detect(self, mocker):
        """Without --backend, auto-detection is used (default behavior)."""
        from rots import context, systemd

        mocker.patch("rots.systemd._dbus_is_available", return_value=True)
        assert context.backend_var.get(None) is None
        assert systemd._use_dbus(None) is True

        mocker.patch("rots.systemd._dbus_is_available", return_value=False)
        assert systemd._use_dbus(None) is False

    def test_remote_executor_always_uses_cli_in_auto_detect(self, mocker):
        """Remote executors always use CLI even when D-Bus is available (no override)."""
        from rots import systemd

        mocker.patch("rots.systemd._dbus_is_available", return_value=True)
        mocker.patch("rots.systemd._is_local", return_value=False)
        remote_exec = mocker.Mock()
        assert systemd._use_dbus(remote_exec) is False
