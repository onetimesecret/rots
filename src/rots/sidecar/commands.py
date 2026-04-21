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

    # Discovery
    DISCOVER_PING = "discover.ping"

    # Provisioning
    PROVISION_SOCKS_KEY_READ = "provision.socks_key_read"
    PROVISION_SOCKS_KEY_WRITE = "provision.socks_key_write"

    # Two-phase provisioning (issue #55) — postgres
    POSTGRES_BOOTSTRAP_APP = "postgres.bootstrap_app"
    POSTGRES_ADD_HBA = "postgres.add_hba"
    POSTGRES_ROTATE_PASSWORD = "postgres.rotate_password"

    # Two-phase provisioning (issue #55) — valkey
    VALKEY_CREATE_ACL_USER = "valkey.create_acl_user"
    VALKEY_RELOAD_ACL = "valkey.reload_acl"

    # Two-phase provisioning (issue #55) — secrets delivery
    SECRETS_DELIVER = "secrets.deliver"

    # Two-phase provisioning (issue #55) — backup
    BACKUP_INSTALL = "backup.install"
    BACKUP_UNINSTALL = "backup.uninstall"


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

# Known sidecar roles. Downstream agents filter handler registration by role
# so that, e.g., a web sidecar only exposes secrets.deliver + container-lifecycle
# commands but not postgres/valkey/backup handlers.
ROLE_DB = "db"
ROLE_WEB = "web"
ALL_ROLES: frozenset[str] = frozenset({ROLE_DB, ROLE_WEB})

# Default role set applied when a handler does not declare one. This preserves
# behaviour for the handlers that predate role gating (lifecycle, config, etc.)
# — they are generic enough to be useful on both sidecar roles.
DEFAULT_ROLES: frozenset[str] = ALL_ROLES

# Per-command role metadata. Populated by @register_handler. Used by
# _import_handlers(role=...) to filter which handlers are actually installed
# into the active dispatcher for a given sidecar role.
_handler_roles: dict[Command, frozenset[str]] = {}

# Registry of command handlers, populated by handler modules
_handlers: dict[Command, Handler] = {}


def register_handler(
    command: Command,
    *,
    roles: set[str] | frozenset[str] | None = None,
) -> Callable[[Handler], Handler]:
    """Decorator to register a handler for a command.

    Args:
        command: The Command enum member this handler implements.
        roles: Which sidecar roles should expose this handler. Defaults to
            ``{"db", "web"}`` (``DEFAULT_ROLES``) so legacy handlers work
            unchanged. New handlers that are role-specific must declare this
            explicitly — e.g. postgres handlers pass ``roles={"db"}``.

    Usage:
        @register_handler(Command.RESTART_WEB)
        def handle_restart_web(params: dict[str, Any]) -> CommandResult:
            ...

        @register_handler(Command.POSTGRES_BOOTSTRAP_APP, roles={"db"})
        def handle_postgres_bootstrap(params: dict[str, Any]) -> CommandResult:
            ...
    """

    role_set: frozenset[str] = DEFAULT_ROLES if roles is None else frozenset(roles)

    unknown = role_set - ALL_ROLES
    if unknown:
        raise ValueError(
            f"Unknown role(s) for {command.value}: {sorted(unknown)}. "
            f"Expected subset of {sorted(ALL_ROLES)}."
        )

    def decorator(func: Handler) -> Handler:
        if command in _handlers:
            logger.warning("Overwriting handler for command: %s", command.value)
        _handlers[command] = func
        _handler_roles[command] = role_set
        logger.debug(
            "Registered handler for command: %s (roles=%s)",
            command.value,
            sorted(role_set),
        )
        return func

    return decorator


def dispatch(command_name: str, params: dict[str, Any]) -> CommandResult:
    """Dispatch a command to its registered handler.

    Routes rots.* delegated commands to invoke_rots() before checking
    the Command enum, so that rots.service.status etc. are reachable.

    Args:
        command_name: The command string (e.g., "restart.web" or "rots.service.status")
        params: Parameters for the command handler

    Returns:
        CommandResult from the handler, or failure if command is unknown
    """
    from .handlers_rots import invoke_rots, is_rots_command

    # Route rots.* delegated commands first
    if is_rots_command(command_name):
        result_dict = invoke_rots(command_name, params)
        return CommandResult(
            success=result_dict.get("status") == "ok",
            data=result_dict,
            error=result_dict.get("error"),
        )

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
def _import_handlers(role: str | None = None) -> None:
    """Import handler modules and filter the active dispatcher by role.

    Called lazily to avoid circular imports. Handler modules use the
    ``@register_handler`` decorator which populates ``_handlers`` and
    ``_handler_roles``.

    Args:
        role: If provided, the active dispatcher (``_handlers``) is filtered
            after import so only commands whose declared roles include
            ``role`` remain registered. If ``None`` (the default), every
            registered handler stays in the dispatcher. Use ``None`` for
            tests and tooling that need the full command surface; pass
            ``"db"`` or ``"web"`` from the sidecar daemon's ``run``
            command to install only the commands that role should expose.

    Raises:
        ValueError: if ``role`` is not a member of ``ALL_ROLES``.
    """
    # Import existing handler modules to trigger @register_handler decorators
    # Import new two-phase provisioning handler modules (issue #55). These live
    # under the sibling commands tree because they carry heavier deps and share
    # shared types (_types.py) / transport (_transport.py) with the operator
    # command. Registration still lands in this module's _handlers registry.
    from rots.commands.sidecar.handlers import (
        backup,  # noqa: F401
        postgres,  # noqa: F401
        secrets,  # noqa: F401
        valkey,  # noqa: F401
    )

    from . import (
        handlers_config,  # noqa: F401
        handlers_discovery,  # noqa: F401
        handlers_lifecycle,  # noqa: F401
        handlers_phased,  # noqa: F401
        handlers_provision,  # noqa: F401
        handlers_rolling,  # noqa: F401
        handlers_rots,  # noqa: F401
        handlers_status,  # noqa: F401
    )

    if role is None:
        return

    if role not in ALL_ROLES:
        raise ValueError(f"Unknown sidecar role: {role!r}. Expected one of {sorted(ALL_ROLES)}.")

    # Filter: drop handlers whose declared role set excludes the active role.
    # Handlers without declared roles default to DEFAULT_ROLES, which includes
    # every role, so they stay registered.
    to_remove = [
        cmd for cmd in list(_handlers.keys()) if role not in _handler_roles.get(cmd, DEFAULT_ROLES)
    ]
    for cmd in to_remove:
        logger.debug(
            "Dropping handler %s for role=%s (declared roles=%s)",
            cmd.value,
            role,
            sorted(_handler_roles.get(cmd, DEFAULT_ROLES)),
        )
        _handlers.pop(cmd, None)


# Note: Handlers are registered when their modules are imported.
# The daemon's startup code should call _import_handlers() or
# explicitly import handler modules to ensure registration.
