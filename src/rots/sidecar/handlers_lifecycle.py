# src/rots/sidecar/handlers_lifecycle.py

"""Lifecycle handlers for start/stop/restart operations.

Handles individual instance lifecycle operations for web, worker, and
scheduler instance types. These are the basic building blocks used by
more complex orchestration handlers (rolling restart, phased restart, etc.).
"""

from __future__ import annotations

import logging
from typing import Any

from rots import systemd

from .commands import Command, CommandResult, register_handler

logger = logging.getLogger(__name__)


def _unit_name(instance_type: str, identifier: str) -> str:
    """Build systemd unit name with .service suffix."""
    return f"{systemd.unit_name(instance_type, identifier)}.service"


def _lifecycle_operation(
    action: str,
    instance_type: str,
    identifier: str | None,
) -> CommandResult:
    """Generic lifecycle operation (start/stop/restart).

    Args:
        action: One of "start", "stop", "restart"
        instance_type: One of "web", "worker", "scheduler"
        identifier: Port or instance ID

    Returns:
        CommandResult indicating success or failure
    """
    if not identifier:
        return CommandResult.fail("Missing 'identifier' in params")

    unit = _unit_name(instance_type, str(identifier))
    logger.info("Executing %s on %s", action, unit)

    try:
        if action == "start":
            systemd.start(unit)
        elif action == "stop":
            systemd.stop(unit)
        elif action == "restart":
            systemd.restart(unit)
        else:
            return CommandResult.fail(f"Unknown action: {action}")

        return CommandResult.ok({"unit": unit, "action": action})
    except systemd.SystemctlError as e:
        logger.error("systemctl %s %s failed: %s", action, unit, e.journal)
        return CommandResult.fail(f"{action} failed for {unit}: {e.journal}")
    except Exception as e:
        logger.exception("Unexpected error in %s %s", action, unit)
        return CommandResult.fail(str(e))


# --- Web instance handlers ---


@register_handler(Command.RESTART_WEB)
def handle_restart_web(params: dict[str, Any]) -> CommandResult:
    """Restart a web instance.

    Params:
        identifier: Port number (e.g., 7043)
    """
    return _lifecycle_operation("restart", "web", params.get("identifier"))


@register_handler(Command.STOP_WEB)
def handle_stop_web(params: dict[str, Any]) -> CommandResult:
    """Stop a web instance.

    Params:
        identifier: Port number (e.g., 7043)
    """
    return _lifecycle_operation("stop", "web", params.get("identifier"))


@register_handler(Command.START_WEB)
def handle_start_web(params: dict[str, Any]) -> CommandResult:
    """Start a web instance.

    Params:
        identifier: Port number (e.g., 7043)
    """
    return _lifecycle_operation("start", "web", params.get("identifier"))


# --- Worker instance handlers ---


@register_handler(Command.RESTART_WORKER)
def handle_restart_worker(params: dict[str, Any]) -> CommandResult:
    """Restart a worker instance.

    Params:
        identifier: Worker ID (e.g., "billing", "email")
    """
    return _lifecycle_operation("restart", "worker", params.get("identifier"))


@register_handler(Command.STOP_WORKER)
def handle_stop_worker(params: dict[str, Any]) -> CommandResult:
    """Stop a worker instance.

    Params:
        identifier: Worker ID
    """
    return _lifecycle_operation("stop", "worker", params.get("identifier"))


@register_handler(Command.START_WORKER)
def handle_start_worker(params: dict[str, Any]) -> CommandResult:
    """Start a worker instance.

    Params:
        identifier: Worker ID
    """
    return _lifecycle_operation("start", "worker", params.get("identifier"))


# --- Scheduler instance handlers ---


@register_handler(Command.RESTART_SCHEDULER)
def handle_restart_scheduler(params: dict[str, Any]) -> CommandResult:
    """Restart the scheduler instance.

    Params:
        identifier: Scheduler ID (usually "default")
    """
    return _lifecycle_operation("restart", "scheduler", params.get("identifier"))


@register_handler(Command.STOP_SCHEDULER)
def handle_stop_scheduler(params: dict[str, Any]) -> CommandResult:
    """Stop the scheduler instance.

    Params:
        identifier: Scheduler ID
    """
    return _lifecycle_operation("stop", "scheduler", params.get("identifier"))


@register_handler(Command.START_SCHEDULER)
def handle_start_scheduler(params: dict[str, Any]) -> CommandResult:
    """Start the scheduler instance.

    Params:
        identifier: Scheduler ID
    """
    return _lifecycle_operation("start", "scheduler", params.get("identifier"))
