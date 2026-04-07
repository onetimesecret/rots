# tests/sidecar/test_handlers_discovery.py

"""Tests for the discover.ping handler.

Covers:
- handle_discover_ping returns success with expected keys
- Response includes host_id, pid, and timestamp
- DISCOVER_PING enum value is wired correctly
- _import_handlers loads the discovery module without error
"""

import pytest

from rots.sidecar.commands import Command, _import_handlers

pytestmark = pytest.mark.quick


class TestDiscoverPingEnum:
    """DISCOVER_PING should be a valid Command member."""

    def test_discover_ping_value(self):
        """Command enum includes discover.ping."""
        assert Command.DISCOVER_PING.value == "discover.ping"

    def test_discover_ping_from_string(self):
        """Command('discover.ping') resolves to DISCOVER_PING."""
        assert Command("discover.ping") == Command.DISCOVER_PING


class TestImportHandlersDiscovery:
    """_import_handlers should load handlers_discovery without error."""

    def test_import_handlers_includes_discovery(self):
        """Importing handlers registers the discover.ping handler."""
        from rots.sidecar.commands import _handlers

        _import_handlers()
        assert Command.DISCOVER_PING in _handlers


class TestHandleDiscoverPing:
    """handle_discover_ping should return host identity data."""

    def test_returns_success(self, monkeypatch):
        """Result has success=True."""
        monkeypatch.setattr(
            "rots.sidecar.handlers_discovery.get_host_id",
            lambda: "test-host-1",
        )
        from rots.sidecar.handlers_discovery import handle_discover_ping

        result = handle_discover_ping({})
        assert result.success is True

    def test_result_contains_host_id(self, monkeypatch):
        """Result data contains the host_id returned by get_host_id."""
        monkeypatch.setattr(
            "rots.sidecar.handlers_discovery.get_host_id",
            lambda: "eu-prod-db-1",
        )
        from rots.sidecar.handlers_discovery import handle_discover_ping

        result = handle_discover_ping({})
        assert result.data["host_id"] == "eu-prod-db-1"

    def test_result_contains_pid(self, monkeypatch):
        """Result data contains a pid key with an integer value."""
        monkeypatch.setattr(
            "rots.sidecar.handlers_discovery.get_host_id",
            lambda: "test-host",
        )
        from rots.sidecar.handlers_discovery import handle_discover_ping

        result = handle_discover_ping({})
        assert "pid" in result.data
        assert isinstance(result.data["pid"], int)

    def test_result_contains_timestamp(self, monkeypatch):
        """Result data contains a timestamp key with a float value."""
        monkeypatch.setattr(
            "rots.sidecar.handlers_discovery.get_host_id",
            lambda: "test-host",
        )
        from rots.sidecar.handlers_discovery import handle_discover_ping

        result = handle_discover_ping({})
        assert "timestamp" in result.data
        assert isinstance(result.data["timestamp"], float)

    def test_result_has_exactly_three_keys(self, monkeypatch):
        """Result data contains only host_id, pid, and timestamp."""
        monkeypatch.setattr(
            "rots.sidecar.handlers_discovery.get_host_id",
            lambda: "test-host",
        )
        from rots.sidecar.handlers_discovery import handle_discover_ping

        result = handle_discover_ping({})
        assert set(result.data.keys()) == {"host_id", "pid", "timestamp"}

    def test_ignores_params(self, monkeypatch):
        """Handler ignores any params passed to it."""
        monkeypatch.setattr(
            "rots.sidecar.handlers_discovery.get_host_id",
            lambda: "test-host",
        )
        from rots.sidecar.handlers_discovery import handle_discover_ping

        result = handle_discover_ping({"extra": "ignored", "keys": 42})
        assert result.success is True
        assert result.data["host_id"] == "test-host"
