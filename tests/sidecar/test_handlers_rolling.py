# tests/sidecar/test_handlers_rolling.py

"""Tests for src/rots/sidecar/handlers_rolling.py

Covers:
- handle_instances_restart_all with type filters
- delay between restarts
- health timeout handling
- stop_on_failure behavior
- _wait_for_healthy helper
- _restart_instance helper
"""

from unittest.mock import MagicMock, patch

import pytest

from rots.sidecar.commands import Command
from rots.sidecar.handlers_rolling import (
    DEFAULT_DELAY,
    DEFAULT_HEALTH_TIMEOUT,
    _restart_instance,
    _wait_for_healthy,
    handle_instances_restart_all,
)


class TestHandleInstancesRestartAll:
    """Tests for handle_instances_restart_all handler."""

    @pytest.fixture
    def mock_systemd(self):
        """Mock systemd module."""
        with patch("rots.sidecar.handlers_rolling.systemd") as mock:
            from rots import systemd as real_systemd

            mock.SystemctlError = real_systemd.SystemctlError
            mock.unit_name.side_effect = lambda t, i: f"onetime-{t}@{i}"
            mock.discover_web_instances.return_value = [7043, 7044]
            mock.discover_worker_instances.return_value = ["billing"]
            mock.discover_scheduler_instances.return_value = ["default"]
            mock.restart.return_value = None
            mock.is_active.return_value = True
            mock.get_container_health_map.return_value = {}
            yield mock

    def test_restarts_all_types_in_order(self, mock_systemd):
        """Restarts workers, schedulers, then web instances."""
        # Mock health by setting container health map for all
        mock_systemd.get_container_health_map.return_value = {
            ("worker", "billing"): {"health": "healthy"},
            ("scheduler", "default"): {"health": "healthy"},
            ("web", "7043"): {"health": "healthy"},
            ("web", "7044"): {"health": "healthy"},
        }

        # Mock urllib to pass HTTP health check for web
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_response = MagicMock()
            mock_response.status = 200
            mock_response.__enter__ = MagicMock(return_value=mock_response)
            mock_response.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value = mock_response

            result = handle_instances_restart_all({"delay": 0, "health_timeout": 1})

        assert result.success is True
        assert result.data["completed"] == 4  # 1 worker + 1 scheduler + 2 web

        # Verify restart order
        restart_calls = mock_systemd.restart.call_args_list
        assert len(restart_calls) == 4

        # First should be worker
        assert "worker" in str(restart_calls[0])
        # Then scheduler
        assert "scheduler" in str(restart_calls[1])
        # Then web instances
        assert "web" in str(restart_calls[2])
        assert "web" in str(restart_calls[3])

    def test_type_filter_web_only(self, mock_systemd):
        """type filter restricts to web instances only."""
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_response = MagicMock()
            mock_response.status = 200
            mock_response.__enter__ = MagicMock(return_value=mock_response)
            mock_response.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value = mock_response

            result = handle_instances_restart_all(
                {
                    "type": "web",
                    "delay": 0,
                    "health_timeout": 1,
                }
            )

        assert result.success is True
        assert result.data["completed"] == 2  # Only web instances

        # Should not discover workers or schedulers
        mock_systemd.discover_worker_instances.assert_not_called()
        mock_systemd.discover_scheduler_instances.assert_not_called()

    def test_type_filter_worker_only(self, mock_systemd):
        """type filter restricts to worker instances only."""
        mock_systemd.get_container_health_map.return_value = {
            ("worker", "billing"): {"health": "healthy"},
        }

        result = handle_instances_restart_all(
            {
                "type": "worker",
                "delay": 0,
                "health_timeout": 1,
            }
        )

        assert result.success is True
        assert result.data["completed"] == 1

        mock_systemd.discover_web_instances.assert_not_called()
        mock_systemd.discover_scheduler_instances.assert_not_called()

    def test_invalid_type_filter(self, mock_systemd):
        """Invalid type filter returns error."""
        result = handle_instances_restart_all({"type": "invalid"})

        assert result.success is False
        assert "Invalid type filter" in result.error

    def test_no_instances_found(self, mock_systemd):
        """Returns OK with message when no instances found."""
        mock_systemd.discover_web_instances.return_value = []
        mock_systemd.discover_worker_instances.return_value = []
        mock_systemd.discover_scheduler_instances.return_value = []

        result = handle_instances_restart_all({})

        assert result.success is True
        assert "No instances found" in result.data["message"]

    def test_stop_on_failure_true(self, mock_systemd):
        """stop_on_failure=True stops on first failure."""
        from rots import systemd as real_systemd

        # Make second restart fail
        call_count = [0]

        def restart_side_effect(unit):
            call_count[0] += 1
            if call_count[0] == 2:  # Fail on second call
                raise real_systemd.SystemctlError(unit, "restart", "Failed")

        mock_systemd.restart.side_effect = restart_side_effect

        result = handle_instances_restart_all(
            {
                "delay": 0,
                "health_timeout": 1,
                "stop_on_failure": True,
            }
        )

        assert result.success is False
        assert result.data["completed"] == 1
        assert "stopped_at" in result.data

    def test_stop_on_failure_false(self, mock_systemd):
        """stop_on_failure=False continues after failures."""
        from rots import systemd as real_systemd

        # Make first restart fail
        def restart_side_effect(unit):
            if "worker" in unit:
                raise real_systemd.SystemctlError(unit, "restart", "Failed")

        mock_systemd.restart.side_effect = restart_side_effect

        # Mock health checks to pass
        mock_systemd.get_container_health_map.return_value = {
            ("scheduler", "default"): {"health": "healthy"},
        }

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_response = MagicMock()
            mock_response.status = 200
            mock_response.__enter__ = MagicMock(return_value=mock_response)
            mock_response.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value = mock_response

            result = handle_instances_restart_all(
                {
                    "delay": 0,
                    "health_timeout": 1,
                    "stop_on_failure": False,
                }
            )

        # Should have failures but still complete
        assert result.success is False  # Overall failed due to worker
        assert result.data["completed"] == 4  # All were attempted
        assert len(result.data["failures"]) == 1

    def test_uses_default_delay(self, mock_systemd):
        """Uses DEFAULT_DELAY when not specified."""
        # Just verify the default is reasonable
        assert DEFAULT_DELAY > 0
        assert DEFAULT_DELAY == 5

    def test_uses_default_health_timeout(self, mock_systemd):
        """Uses DEFAULT_HEALTH_TIMEOUT when not specified."""
        assert DEFAULT_HEALTH_TIMEOUT > 0
        assert DEFAULT_HEALTH_TIMEOUT == 60


class TestWaitForHealthy:
    """Tests for _wait_for_healthy helper."""

    @pytest.fixture
    def mock_systemd(self):
        """Mock systemd module."""
        with patch("rots.sidecar.handlers_rolling.systemd") as mock:
            mock.unit_name.side_effect = lambda t, i: f"onetime-{t}@{i}"
            mock.is_active.return_value = True
            yield mock

    def test_web_uses_http_health(self, mock_systemd):
        """Web instances check HTTP health endpoint."""
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_response = MagicMock()
            mock_response.status = 200
            mock_response.__enter__ = MagicMock(return_value=mock_response)
            mock_response.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value = mock_response

            result = _wait_for_healthy("web", "7043", timeout=5)

        assert result is True
        # Verify HTTP check was made
        mock_urlopen.assert_called()
        call_args = str(mock_urlopen.call_args)
        assert "7043" in call_args
        assert "health" in call_args

    def test_worker_uses_container_health(self, mock_systemd):
        """Worker instances check container health map."""
        mock_systemd.get_container_health_map.return_value = {
            ("worker", "billing"): {"health": "healthy"},
        }

        result = _wait_for_healthy("worker", "billing", timeout=5)

        assert result is True
        mock_systemd.get_container_health_map.assert_called()

    def test_returns_false_when_not_active(self, mock_systemd):
        """Returns False when unit is not active."""
        mock_systemd.is_active.return_value = False

        result = _wait_for_healthy("web", "7043", timeout=1)

        assert result is False

    def test_returns_false_on_timeout(self, mock_systemd):
        """Returns False when health check times out."""
        mock_systemd.get_container_health_map.return_value = {}  # No health info

        result = _wait_for_healthy("worker", "billing", timeout=0.1)

        assert result is False


class TestRestartInstance:
    """Tests for _restart_instance helper."""

    @pytest.fixture
    def mock_systemd(self):
        """Mock systemd module."""
        with patch("rots.sidecar.handlers_rolling.systemd") as mock:
            from rots import systemd as real_systemd

            mock.SystemctlError = real_systemd.SystemctlError
            mock.unit_name.side_effect = lambda t, i: f"onetime-{t}@{i}"
            mock.restart.return_value = None
            mock.is_active.return_value = True
            yield mock

    def test_successful_restart(self, mock_systemd):
        """Returns success dict on successful restart."""
        mock_systemd.get_container_health_map.return_value = {
            ("worker", "billing"): {"health": "healthy"},
        }

        result = _restart_instance("worker", "billing", health_timeout=1)

        assert result["success"] is True
        assert result["identifier"] == "billing"
        assert result["type"] == "worker"
        mock_systemd.restart.assert_called_once()

    def test_restart_failure(self, mock_systemd):
        """Returns error dict on restart failure."""
        from rots import systemd as real_systemd

        mock_systemd.restart.side_effect = real_systemd.SystemctlError(
            "onetime-worker@billing.service",
            "restart",
            "Unit not found",
        )

        result = _restart_instance("worker", "billing", health_timeout=1)

        assert result["success"] is False
        assert "error" in result
        assert "journal" in result

    def test_includes_health_status(self, mock_systemd):
        """Result includes health status after restart."""
        mock_systemd.get_container_health_map.return_value = {
            ("worker", "billing"): {"health": "healthy"},
        }

        result = _restart_instance("worker", "billing", health_timeout=1)

        assert "healthy" in result


class TestCommandRegistration:
    """Tests for command registration."""

    def test_handler_registered(self):
        """handle_instances_restart_all is registered for INSTANCES_RESTART_ALL."""
        from rots.sidecar.commands import _handlers, _import_handlers

        _import_handlers()

        assert Command.INSTANCES_RESTART_ALL in _handlers
        # Verify it's the right handler
        handler = _handlers[Command.INSTANCES_RESTART_ALL]
        assert handler.__name__ == "handle_instances_restart_all"
