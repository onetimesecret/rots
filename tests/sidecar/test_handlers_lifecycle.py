# tests/sidecar/test_handlers_lifecycle.py

"""Tests for src/rots/sidecar/handlers_lifecycle.py

Covers lifecycle operations (start/stop/restart) for web, worker, and scheduler
instances using the modern @register_handler decorator pattern.
"""

from unittest.mock import patch

import pytest

from rots.sidecar.commands import Command, CommandResult, _import_handlers, dispatch
from rots.sidecar.handlers_lifecycle import (
    handle_restart_scheduler,
    handle_restart_web,
    handle_restart_worker,
    handle_start_scheduler,
    handle_start_web,
    handle_start_worker,
    handle_stop_scheduler,
    handle_stop_web,
    handle_stop_worker,
)


# Ensure handlers are registered before tests run
@pytest.fixture(scope="module", autouse=True)
def import_handlers():
    """Import handlers to trigger registration."""
    _import_handlers()


class TestLifecycleHandlerRegistration:
    """Tests that lifecycle handlers are properly registered."""

    def test_lifecycle_commands_registered(self):
        """All lifecycle commands have registered handlers."""
        _import_handlers()

        lifecycle_commands = [
            Command.RESTART_WEB,
            Command.STOP_WEB,
            Command.START_WEB,
            Command.RESTART_WORKER,
            Command.STOP_WORKER,
            Command.START_WORKER,
            Command.RESTART_SCHEDULER,
            Command.STOP_SCHEDULER,
            Command.START_SCHEDULER,
        ]

        for cmd in lifecycle_commands:
            # Dispatch should not fail with "No handler registered"
            result = dispatch(cmd.value, {})
            # Without identifier, should fail with missing param, not "no handler"
            assert isinstance(result, CommandResult)
            assert "identifier" in (result.error or "").lower() or result.success


class TestWebLifecycleHandlers:
    """Tests for web instance lifecycle operations."""

    @pytest.fixture
    def mock_systemd(self):
        """Mock systemd module for lifecycle tests."""
        with patch("rots.sidecar.handlers_lifecycle.systemd") as mock:
            mock.unit_name.side_effect = lambda t, i: f"onetime-{t}@{i}"
            yield mock

    def test_restart_web_success(self, mock_systemd):
        """restart.web calls systemd.restart with correct unit."""
        mock_systemd.restart.return_value = None

        result = handle_restart_web({"identifier": "7043"})

        assert result.success
        assert result.data["unit"] == "onetime-web@7043.service"
        assert result.data["action"] == "restart"
        mock_systemd.restart.assert_called_once_with("onetime-web@7043.service")

    def test_restart_web_missing_identifier(self, mock_systemd):
        """restart.web fails without identifier."""
        result = handle_restart_web({})

        assert not result.success
        assert "identifier" in result.error.lower()

    def test_restart_web_systemd_error(self, mock_systemd):
        """restart.web reports systemd errors."""
        from rots import systemd as real_systemd

        mock_systemd.SystemctlError = real_systemd.SystemctlError
        mock_systemd.restart.side_effect = real_systemd.SystemctlError(
            "onetime-web@7043.service",
            "restart",
            "Failed to restart: unit not found",
        )

        result = handle_restart_web({"identifier": "7043"})

        assert not result.success
        assert "failed" in result.error.lower()

    def test_stop_web_success(self, mock_systemd):
        """stop.web calls systemd.stop with correct unit."""
        mock_systemd.stop.return_value = None

        result = handle_stop_web({"identifier": "7044"})

        assert result.success
        assert result.data["action"] == "stop"
        mock_systemd.stop.assert_called_once_with("onetime-web@7044.service")

    def test_start_web_success(self, mock_systemd):
        """start.web calls systemd.start with correct unit."""
        mock_systemd.start.return_value = None

        result = handle_start_web({"identifier": "7045"})

        assert result.success
        assert result.data["action"] == "start"
        mock_systemd.start.assert_called_once_with("onetime-web@7045.service")


class TestWorkerLifecycleHandlers:
    """Tests for worker instance lifecycle operations."""

    @pytest.fixture
    def mock_systemd(self):
        """Mock systemd module for lifecycle tests."""
        with patch("rots.sidecar.handlers_lifecycle.systemd") as mock:
            mock.unit_name.side_effect = lambda t, i: f"onetime-{t}@{i}"
            yield mock

    def test_restart_worker_success(self, mock_systemd):
        """restart.worker handles worker identifiers."""
        mock_systemd.restart.return_value = None

        result = handle_restart_worker({"identifier": "billing"})

        assert result.success
        assert result.data["unit"] == "onetime-worker@billing.service"
        mock_systemd.restart.assert_called_once_with("onetime-worker@billing.service")

    def test_stop_worker_success(self, mock_systemd):
        """stop.worker calls systemd.stop for worker."""
        mock_systemd.stop.return_value = None

        result = handle_stop_worker({"identifier": "email"})

        assert result.success
        mock_systemd.stop.assert_called_once_with("onetime-worker@email.service")

    def test_start_worker_success(self, mock_systemd):
        """start.worker calls systemd.start for worker."""
        mock_systemd.start.return_value = None

        result = handle_start_worker({"identifier": "notifications"})

        assert result.success
        mock_systemd.start.assert_called_once_with("onetime-worker@notifications.service")


class TestSchedulerLifecycleHandlers:
    """Tests for scheduler instance lifecycle operations."""

    @pytest.fixture
    def mock_systemd(self):
        """Mock systemd module for lifecycle tests."""
        with patch("rots.sidecar.handlers_lifecycle.systemd") as mock:
            mock.unit_name.side_effect = lambda t, i: f"onetime-{t}@{i}"
            yield mock

    def test_restart_scheduler_success(self, mock_systemd):
        """restart.scheduler handles scheduler unit."""
        mock_systemd.restart.return_value = None

        result = handle_restart_scheduler({"identifier": "default"})

        assert result.success
        assert result.data["unit"] == "onetime-scheduler@default.service"

    def test_stop_scheduler_success(self, mock_systemd):
        """stop.scheduler calls systemd.stop for scheduler."""
        mock_systemd.stop.return_value = None

        result = handle_stop_scheduler({"identifier": "default"})

        assert result.success

    def test_start_scheduler_success(self, mock_systemd):
        """start.scheduler calls systemd.start for scheduler."""
        mock_systemd.start.return_value = None

        result = handle_start_scheduler({"identifier": "default"})

        assert result.success


class TestDispatchIntegration:
    """Tests that dispatch routes to lifecycle handlers correctly."""

    @pytest.fixture
    def mock_systemd(self):
        """Mock systemd module."""
        with patch("rots.sidecar.handlers_lifecycle.systemd") as mock:
            mock.unit_name.side_effect = lambda t, i: f"onetime-{t}@{i}"
            mock.restart.return_value = None
            mock.start.return_value = None
            mock.stop.return_value = None
            yield mock

    def test_dispatch_restart_web(self, mock_systemd):
        """Dispatch routes restart.web correctly."""
        result = dispatch("restart.web", {"identifier": "7043"})

        assert result.success
        mock_systemd.restart.assert_called_once()

    def test_dispatch_stop_worker(self, mock_systemd):
        """Dispatch routes stop.worker correctly."""
        result = dispatch("stop.worker", {"identifier": "billing"})

        assert result.success
        mock_systemd.stop.assert_called_once()

    def test_dispatch_start_scheduler(self, mock_systemd):
        """Dispatch routes start.scheduler correctly."""
        result = dispatch("start.scheduler", {"identifier": "default"})

        assert result.success
        mock_systemd.start.assert_called_once()
