# packages/rots/tests/sidecar/test_commands.py

"""Tests for src/rots/sidecar/commands.py

Covers:
- Command enum values
- CommandResult.ok and CommandResult.fail factory methods
- dispatch function with registered and unregistered handlers
- register_handler decorator
"""

import pytest

from rots.sidecar.commands import (
    Command,
    CommandResult,
    _handlers,
    _import_handlers,
    dispatch,
    get_all_commands,
    get_registered_commands,
    register_handler,
)


class TestCommandEnum:
    """Tests for Command enum."""

    def test_lifecycle_web_commands(self):
        """Verify web lifecycle commands exist."""
        assert Command.RESTART_WEB.value == "restart.web"
        assert Command.STOP_WEB.value == "stop.web"
        assert Command.START_WEB.value == "start.web"

    def test_lifecycle_worker_commands(self):
        """Verify worker lifecycle commands exist."""
        assert Command.RESTART_WORKER.value == "restart.worker"
        assert Command.STOP_WORKER.value == "stop.worker"
        assert Command.START_WORKER.value == "start.worker"

    def test_lifecycle_scheduler_commands(self):
        """Verify scheduler lifecycle commands exist."""
        assert Command.RESTART_SCHEDULER.value == "restart.scheduler"
        assert Command.STOP_SCHEDULER.value == "stop.scheduler"
        assert Command.START_SCHEDULER.value == "start.scheduler"

    def test_phased_restart_commands(self):
        """Verify phased restart commands exist."""
        assert Command.PHASED_RESTART_WEB.value == "phased_restart.web"
        assert Command.PHASED_RESTART_WORKER.value == "phased_restart.worker"

    def test_bulk_operations(self):
        """Verify bulk operation commands exist."""
        assert Command.INSTANCES_RESTART_ALL.value == "instances.restart_all"

    def test_config_commands(self):
        """Verify config commands exist."""
        assert Command.CONFIG_STAGE.value == "config.stage"
        assert Command.CONFIG_APPLY.value == "config.apply"
        assert Command.CONFIG_DISCARD.value == "config.discard"
        assert Command.CONFIG_GET.value == "config.get"

    def test_status_commands(self):
        """Verify status commands exist."""
        assert Command.HEALTH.value == "health"
        assert Command.STATUS.value == "status"

    def test_command_from_string(self):
        """Command can be created from string value."""
        cmd = Command("restart.web")
        assert cmd == Command.RESTART_WEB

    def test_command_invalid_string(self):
        """Invalid string raises ValueError."""
        with pytest.raises(ValueError):
            Command("invalid.command")


class TestCommandResult:
    """Tests for CommandResult dataclass."""

    def test_ok_no_data(self):
        """Create successful result without data."""
        result = CommandResult.ok()
        assert result.success is True
        assert result.data is None
        assert result.error is None
        assert result.warnings == []

    def test_ok_with_data(self):
        """Create successful result with data."""
        result = CommandResult.ok({"count": 42, "items": ["a", "b"]})
        assert result.success is True
        assert result.data == {"count": 42, "items": ["a", "b"]}
        assert result.error is None

    def test_fail_with_error(self):
        """Create failed result with error message."""
        result = CommandResult.fail("Something went wrong")
        assert result.success is False
        assert result.data is None
        assert result.error == "Something went wrong"

    def test_manual_construction(self):
        """Manual construction with all fields."""
        result = CommandResult(
            success=True,
            data={"partial": True},
            error=None,
            warnings=["Watch out", "Also this"],
        )
        assert result.success is True
        assert result.data == {"partial": True}
        assert result.warnings == ["Watch out", "Also this"]


class TestRegisterHandler:
    """Tests for register_handler decorator."""

    def test_register_new_handler(self):
        """Registering a handler adds it to registry."""
        # Use a command that might not have a handler
        test_cmd = Command.CONFIG_GET

        # Clear any existing handler
        _handlers.pop(test_cmd, None)

        @register_handler(test_cmd)
        def my_handler(params):
            return CommandResult.ok({"key": params.get("key")})

        assert test_cmd in _handlers
        assert _handlers[test_cmd] is my_handler

        # Clean up
        _handlers.pop(test_cmd, None)

    def test_handler_can_be_called(self):
        """Registered handler is callable."""
        test_cmd = Command.CONFIG_DISCARD

        _handlers.pop(test_cmd, None)

        @register_handler(test_cmd)
        def discard_handler(params):
            return CommandResult.ok({"discarded": True})

        result = _handlers[test_cmd]({"some": "params"})
        assert result.success is True
        assert result.data == {"discarded": True}

        _handlers.pop(test_cmd, None)


class TestDispatch:
    """Tests for dispatch function."""

    def setup_method(self):
        """Set up test handlers."""
        self.original_handlers = _handlers.copy()

    def teardown_method(self):
        """Restore original handlers."""
        _handlers.clear()
        _handlers.update(self.original_handlers)

    def test_dispatch_unknown_command(self):
        """Dispatching unknown command returns failure."""
        result = dispatch("completely.unknown.command", {})

        assert result.success is False
        assert "Unknown command" in result.error
        assert "completely.unknown.command" in result.error
        assert "Valid commands" in result.error

    def test_dispatch_no_handler_registered(self):
        """Dispatching command without handler returns failure."""
        # Use a valid command but remove its handler
        cmd = Command.HEALTH
        _handlers.pop(cmd, None)

        result = dispatch("health", {})

        assert result.success is False
        assert "No handler registered" in result.error

    def test_dispatch_success(self):
        """Dispatching to registered handler succeeds."""

        @register_handler(Command.STATUS)
        def status_handler(params):
            return CommandResult.ok({"status": "running"})

        result = dispatch("status", {})

        assert result.success is True
        assert result.data == {"status": "running"}

    def test_dispatch_passes_params(self):
        """Params are passed to handler."""
        received_params = {}

        @register_handler(Command.CONFIG_GET)
        def config_get_handler(params):
            received_params.update(params)
            return CommandResult.ok()

        dispatch("config.get", {"key": "REDIS_URL", "section": "main"})

        assert received_params == {"key": "REDIS_URL", "section": "main"}

    def test_dispatch_handler_exception(self):
        """Exception in handler returns failure with message."""

        @register_handler(Command.CONFIG_APPLY)
        def exploding_handler(params):
            raise RuntimeError("Database connection failed")

        result = dispatch("config.apply", {})

        assert result.success is False
        assert "Handler error" in result.error
        assert "Database connection failed" in result.error


class TestHelperFunctions:
    """Tests for get_registered_commands and get_all_commands."""

    def test_get_all_commands(self):
        """get_all_commands returns all defined commands."""
        all_cmds = get_all_commands()

        assert "restart.web" in all_cmds
        assert "health" in all_cmds
        assert "config.stage" in all_cmds
        assert len(all_cmds) == len(Command)

    def test_get_registered_commands_subset(self):
        """get_registered_commands returns only registered handlers."""
        # The registered commands should be a subset of all commands
        registered = set(get_registered_commands())
        all_cmds = set(get_all_commands())

        assert registered <= all_cmds


class TestRotsDispatch:
    """Tests for rots.* prefix routing in dispatch()."""

    def test_rots_command_routes_to_invoke(self, monkeypatch):
        """rots.* commands should route through invoke_rots()."""
        mock_result = {"status": "ok", "stdout": "output", "returncode": 0}
        monkeypatch.setattr(
            "rots.sidecar.handlers_rots.invoke_rots", lambda cmd, params: mock_result
        )
        _import_handlers()
        result = dispatch("rots.service.status", {})
        assert result.success is True
        assert result.data == mock_result

    def test_rots_error_maps_to_failure(self, monkeypatch):
        """rots.* error results should map to CommandResult with success=False."""
        mock_result = {"status": "error", "error": "something broke", "returncode": 1}
        monkeypatch.setattr(
            "rots.sidecar.handlers_rots.invoke_rots", lambda cmd, params: mock_result
        )
        _import_handlers()
        result = dispatch("rots.service.status", {})
        assert result.success is False
        assert result.error == "something broke"

    def test_rots_blocked_command_fails(self):
        """rots.sidecar.start should be blocked by allowlist."""
        _import_handlers()
        result = dispatch("rots.sidecar.start", {})
        assert result.success is False
        assert "not allowed" in (result.error or "").lower()

    def test_rots_allowed_command_dispatches(self, monkeypatch):
        """rots.service.status should dispatch (mock subprocess)."""
        import subprocess

        mock_proc = subprocess.CompletedProcess(args=[], returncode=0, stdout="active", stderr="")
        monkeypatch.setattr("subprocess.run", lambda *a, **kw: mock_proc)
        _import_handlers()
        result = dispatch("rots.service.status", {"args": ["valkey", "5212"]})
        assert result.success is True
        assert result.data["stdout"] == "active"

    def test_non_rots_command_still_dispatches_normally(self):
        """Non-rots commands should use the existing Command enum path."""
        _import_handlers()
        # "health" is a valid Command enum value, dispatch should use normal path
        # (may fail if no handler is registered, but should NOT hit the rots path)
        result = dispatch("completely.unknown.command", {})
        assert result.success is False
        assert "Unknown command" in (result.error or "")
