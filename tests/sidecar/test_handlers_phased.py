# tests/sidecar/test_handlers_phased.py

"""Tests for src/rots/sidecar/handlers_phased.py

Covers:
- handle_phased_restart_web with SIGUSR2
- handle_phased_restart_worker with SIGUSR1
- escalation to full restart
- _send_signal_to_container helper
- _get_container_pid helper
"""

import signal
from unittest.mock import MagicMock, patch

from rots.sidecar.commands import CommandResult
from rots.sidecar.handlers_phased import (
    _escalate_to_full_restart,
    _escalate_worker_to_full_restart,
    _get_container_pid,
    _send_signal_to_container,
    handle_phased_restart_web,
    handle_phased_restart_worker,
)


class TestGetContainerPid:
    """Tests for _get_container_pid helper."""

    def test_returns_pid_on_success(self):
        """Return PID when podman inspect succeeds."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "12345\n"

        with patch("rots.sidecar.handlers_phased.Podman") as mock_podman_cls:
            mock_podman = MagicMock()
            mock_podman.inspect.return_value = mock_result
            mock_podman_cls.return_value = mock_podman

            pid = _get_container_pid("test-container")

            assert pid == 12345
            mock_podman.inspect.assert_called_once_with(
                "test-container",
                format="{{ .State.Pid }}",
                capture_output=True,
                text=True,
            )

    def test_returns_none_on_failure(self):
        """Return None when podman inspect fails."""
        mock_result = MagicMock()
        mock_result.returncode = 1

        with patch("rots.sidecar.handlers_phased.Podman") as mock_podman_cls:
            mock_podman = MagicMock()
            mock_podman.inspect.return_value = mock_result
            mock_podman_cls.return_value = mock_podman

            pid = _get_container_pid("nonexistent")

            assert pid is None

    def test_returns_none_on_zero_pid(self):
        """Return None when PID is 0 (container not running)."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "0"

        with patch("rots.sidecar.handlers_phased.Podman") as mock_podman_cls:
            mock_podman = MagicMock()
            mock_podman.inspect.return_value = mock_result
            mock_podman_cls.return_value = mock_podman

            pid = _get_container_pid("stopped-container")

            assert pid is None

    def test_returns_none_on_invalid_pid(self):
        """Return None when PID is not a valid integer."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "not-a-number"

        with patch("rots.sidecar.handlers_phased.Podman") as mock_podman_cls:
            mock_podman = MagicMock()
            mock_podman.inspect.return_value = mock_result
            mock_podman_cls.return_value = mock_podman

            pid = _get_container_pid("weird-container")

            assert pid is None


class TestSendSignalToContainer:
    """Tests for _send_signal_to_container helper."""

    def test_success_returns_true(self):
        """Successful signal send returns (True, message)."""
        mock_result = MagicMock()
        mock_result.returncode = 0

        with patch("rots.sidecar.handlers_phased.Podman") as mock_podman_cls:
            mock_podman = MagicMock()
            mock_podman.kill.return_value = mock_result
            mock_podman_cls.return_value = mock_podman

            success, message = _send_signal_to_container("my-container", signal.SIGUSR2)

            assert success is True
            assert "SIGUSR2" in message
            assert "my-container" in message
            mock_podman.kill.assert_called_once_with(
                "my-container",
                signal="SIGUSR2",
                capture_output=True,
                text=True,
            )

    def test_failure_returns_false(self):
        """Failed signal send returns (False, error message)."""
        mock_result = MagicMock()
        mock_result.returncode = 125
        mock_result.stderr = "no such container"

        with patch("rots.sidecar.handlers_phased.Podman") as mock_podman_cls:
            mock_podman = MagicMock()
            mock_podman.kill.return_value = mock_result
            mock_podman_cls.return_value = mock_podman

            success, message = _send_signal_to_container("missing", signal.SIGUSR1)

            assert success is False
            assert "Failed" in message
            assert "no such container" in message


class TestHandlePhasedRestartWeb:
    """Tests for handle_phased_restart_web."""

    def test_missing_port_returns_error(self):
        """Missing port parameter returns failure."""
        result = handle_phased_restart_web({})

        assert result.success is False
        assert "Missing required parameter: port" in result.error

    def test_invalid_port_returns_error(self):
        """Non-numeric port returns failure."""
        result = handle_phased_restart_web({"port": "not-a-number"})

        assert result.success is False
        assert "Invalid port" in result.error

    @patch("rots.sidecar.handlers_phased.systemd")
    @patch("rots.sidecar.handlers_phased._send_signal_to_container")
    def test_inactive_unit_returns_error(self, mock_send_signal, mock_systemd):
        """Inactive unit returns failure without sending signal."""
        mock_systemd.is_active.return_value = False
        mock_systemd.unit_name.return_value = "onetime-web@7043"

        result = handle_phased_restart_web({"port": 7043})

        assert result.success is False
        assert "not active" in result.error
        mock_send_signal.assert_not_called()

    @patch("rots.sidecar.handlers_phased.systemd")
    @patch("rots.sidecar.handlers_phased._send_signal_to_container")
    def test_successful_phased_restart(self, mock_send_signal, mock_systemd):
        """Successful SIGUSR2 and health check returns success."""
        mock_systemd.is_active.return_value = True
        mock_systemd.unit_name.return_value = "onetime-web@7043"
        mock_send_signal.return_value = (True, "Sent SIGUSR2")

        result = handle_phased_restart_web({"port": 7043})

        assert result.success is True
        assert result.data["action"] == "phased_restart"
        assert result.data["method"] == "SIGUSR2"
        assert result.data["port"] == 7043
        mock_send_signal.assert_called_once()
        # Verify SIGUSR2 was sent
        call_args = mock_send_signal.call_args
        assert call_args[0][1] == signal.SIGUSR2

    @patch("rots.sidecar.handlers_phased._escalate_to_full_restart")
    @patch("rots.sidecar.handlers_phased.systemd")
    @patch("rots.sidecar.handlers_phased._send_signal_to_container")
    def test_escalates_on_signal_failure(self, mock_send_signal, mock_systemd, mock_escalate):
        """Failed signal send escalates to full restart when enabled."""
        mock_systemd.is_active.return_value = True
        mock_systemd.unit_name.return_value = "onetime-web@7043"
        mock_send_signal.return_value = (False, "container not found")
        mock_escalate.return_value = CommandResult.ok({"escalated": True})

        result = handle_phased_restart_web({"port": 7043, "escalate": True})

        assert result.success is True
        mock_escalate.assert_called_once()

    @patch("rots.sidecar.handlers_phased.systemd")
    @patch("rots.sidecar.handlers_phased._send_signal_to_container")
    def test_no_escalate_on_signal_failure(self, mock_send_signal, mock_systemd):
        """Failed signal send without escalate returns failure."""
        mock_systemd.is_active.return_value = True
        mock_systemd.unit_name.return_value = "onetime-web@7043"
        mock_send_signal.return_value = (False, "container not found")

        result = handle_phased_restart_web({"port": 7043, "escalate": False})

        assert result.success is False
        assert "container not found" in result.error


class TestHandlePhasedRestartWorker:
    """Tests for handle_phased_restart_worker."""

    def test_missing_worker_id_returns_error(self):
        """Missing worker_id parameter returns failure."""
        result = handle_phased_restart_worker({})

        assert result.success is False
        assert "Missing required parameter: worker_id" in result.error

    @patch("rots.sidecar.handlers_phased.systemd")
    @patch("rots.sidecar.handlers_phased._send_signal_to_container")
    def test_inactive_unit_returns_error(self, mock_send_signal, mock_systemd):
        """Inactive unit returns failure without sending signal."""
        mock_systemd.is_active.return_value = False
        mock_systemd.unit_name.return_value = "onetime-worker@billing"

        result = handle_phased_restart_worker({"worker_id": "billing"})

        assert result.success is False
        assert "not active" in result.error
        mock_send_signal.assert_not_called()

    @patch("rots.sidecar.handlers_phased.time")
    @patch("rots.sidecar.handlers_phased.systemd")
    @patch("rots.sidecar.handlers_phased._send_signal_to_container")
    def test_successful_phased_restart(self, mock_send_signal, mock_systemd, mock_time):
        """Successful SIGUSR1 and health check returns success."""
        mock_systemd.is_active.return_value = True
        mock_systemd.unit_name.return_value = "onetime-worker@billing"
        mock_send_signal.return_value = (True, "Sent SIGUSR1")

        result = handle_phased_restart_worker({"worker_id": "billing"})

        assert result.success is True
        assert result.data["action"] == "phased_restart"
        assert result.data["method"] == "SIGUSR1"
        assert result.data["worker_id"] == "billing"
        # Verify SIGUSR1 was sent
        call_args = mock_send_signal.call_args
        assert call_args[0][1] == signal.SIGUSR1


class TestEscalateToFullRestart:
    """Tests for _escalate_to_full_restart and _escalate_worker_to_full_restart."""

    @patch("rots.sidecar.handlers_phased.systemd")
    def test_web_escalation_success(self, mock_systemd):
        """Successful web escalation returns success with warning."""
        result = _escalate_to_full_restart("onetime-web@7043", 7043, 60)

        assert result.success is True
        assert result.data["escalated"] is True
        assert result.data["method"] == "systemctl restart"
        assert len(result.warnings) == 1
        assert "Escalated" in result.warnings[0]
        mock_systemd.restart.assert_called_once()

    def test_web_escalation_restart_failure(self):
        """Failed restart during escalation returns failure."""
        from rots import systemd as real_systemd
        from rots.systemd import SystemctlError

        with patch.object(
            real_systemd,
            "restart",
            side_effect=SystemctlError("onetime-web@7043", "restart", "journal output"),
        ):
            result = _escalate_to_full_restart("onetime-web@7043", 7043, 60)

        assert result.success is False
        assert "Full restart failed" in result.error

    @patch("rots.sidecar.handlers_phased.systemd")
    def test_worker_escalation_success(self, mock_systemd):
        """Successful worker escalation returns success with warning."""
        result = _escalate_worker_to_full_restart(
            "onetime-worker@billing",
            "onetime-worker@billing.service",
            "billing",
            60,
        )

        assert result.success is True
        assert result.data["escalated"] is True
        assert result.data["worker_id"] == "billing"
        assert len(result.warnings) == 1
        mock_systemd.restart.assert_called_once()
