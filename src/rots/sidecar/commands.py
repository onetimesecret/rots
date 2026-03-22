# src/rots/sidecar/commands.py

"""Command enum, result type, and dispatcher for sidecar operations.

Commands follow a dotted naming convention: category.action[.target]
For example: restart.web, config.stage, instances.restart_all
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

logger = logging.getLogger(__name__)


class Command(StrEnum):
    """All supported sidecar commands.

    Commands are grouped by category:
    - Lifecycle: start, stop, restart for web/worker/scheduler
    - Phased: graceful restarts using signals
    - Instances: bulk operations across all instances
    - Config: staged configuration management
    - Status: health checks and status queries
    """

    # Lifecycle commands - web
    RESTART_WEB = "restart.web"
    STOP_WEB = "stop.web"
    START_WEB = "start.web"

    # Lifecycle commands - worker
    RESTART_WORKER = "restart.worker"
    STOP_WORKER = "stop.worker"
    START_WORKER = "start.worker"

    # Lifecycle commands - scheduler
    RESTART_SCHEDULER = "restart.scheduler"
    STOP_SCHEDULER = "stop.scheduler"
    START_SCHEDULER = "start.scheduler"

    # Phased restart commands (graceful, signal-based)
    PHASED_RESTART_WEB = "phased_restart.web"
    PHASED_RESTART_WORKER = "phased_restart.worker"

    # Bulk instance operations
    INSTANCES_RESTART_ALL = "instances.restart_all"

    # Configuration management
    CONFIG_STAGE = "config.stage"
    CONFIG_APPLY = "config.apply"
    CONFIG_DISCARD = "config.discard"
    CONFIG_GET = "config.get"

    # Status and health
    HEALTH = "health"
    STATUS = "status"


@dataclass
class CommandResult:
    """Result returned by command handlers.

    Attributes:
        success: Whether the command completed successfully
        data: Optional result data (varies by command)
        error: Error message if success is False
        warnings: Non-fatal warnings that occurred during execution
    """

    success: bool
    data: Any = None
    error: str | None = None
    warnings: list[str] = field(default_factory=list)

    @classmethod
    def ok(cls, data: Any = None) -> CommandResult:
        """Create a successful result."""
        return cls(success=True, data=data)

    @classmethod
    def fail(cls, error: str) -> CommandResult:
        """Create a failed result."""
        return cls(success=False, error=error)


# Type alias for handler functions
Handler = Callable[[dict[str, Any]], CommandResult]

# Registry of command handlers, populated by handler modules
_handlers: dict[Command, Handler] = {}


def register_handler(command: Command) -> Callable[[Handler], Handler]:
    """Decorator to register a handler for a command.

    Usage:
        @register_handler(Command.RESTART_WEB)
        def handle_restart_web(params: dict[str, Any]) -> CommandResult:
            ...
    """

    def decorator(func: Handler) -> Handler:
        if command in _handlers:
            logger.warning("Overwriting handler for command: %s", command.value)
        _handlers[command] = func
        logger.debug("Registered handler for command: %s", command.value)
        return func

    return decorator


def dispatch(command_name: str, params: dict[str, Any]) -> CommandResult:
    """Dispatch a command to its registered handler.

    Args:
        command_name: The command string (e.g., "restart.web")
        params: Parameters for the command handler

    Returns:
        CommandResult from the handler, or failure if command is unknown
    """
    # Validate command exists
    try:
        command = Command(command_name)
    except ValueError:
        valid_commands = ", ".join(c.value for c in Command)
        return CommandResult.fail(
            f"Unknown command: {command_name}. Valid commands: {valid_commands}"
        )

    # Check handler is registered
    handler = _handlers.get(command)
    if handler is None:
        return CommandResult.fail(f"No handler registered for command: {command_name}")

    # Execute handler
    logger.info("Executing command: %s", command_name)
    try:
        result = handler(params)
        if result.success:
            logger.info("Command %s completed successfully", command_name)
        else:
            logger.warning("Command %s failed: %s", command_name, result.error)
        return result
    except Exception as e:
        logger.exception("Handler for %s raised an exception", command_name)
        return CommandResult.fail(f"Handler error: {e}")


def get_registered_commands() -> list[str]:
    """Return list of commands that have registered handlers."""
    return [cmd.value for cmd in _handlers.keys()]


def get_all_commands() -> list[str]:
    """Return list of all defined commands."""
    return [cmd.value for cmd in Command]


# Import handlers to trigger registration
# These imports happen at module load time so handlers are available
# when the dispatcher is used
def _import_handlers() -> None:
    """Import handler modules to trigger registration.

    Called lazily to avoid circular imports. Handler modules use
    the @register_handler decorator which populates _handlers.
    """
    # Import handler modules to trigger @register_handler decorators
    from . import (
        handlers_config,  # noqa: F401
        handlers_phased,  # noqa: F401
        handlers_rolling,  # noqa: F401
        handlers_rots,  # noqa: F401
    )

    # TODO: Import additional handler modules as they are implemented
    # from . import handlers_lifecycle  # noqa: F401
    # from . import handlers_status  # noqa: F401


# Note: Handlers are registered when their modules are imported.
# The daemon's startup code should call _import_handlers() or
# explicitly import handler modules to ensure registration.
