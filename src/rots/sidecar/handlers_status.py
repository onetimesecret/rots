# src/rots/sidecar/handlers_status.py

"""Health and status handlers for sidecar operations."""

from __future__ import annotations

import logging
import os
import time
from typing import Any

from .commands import Command, CommandResult, register_handler

logger = logging.getLogger(__name__)


@register_handler(Command.HEALTH)
def handle_health(params: dict[str, Any]) -> CommandResult:
    """Health check for the sidecar daemon itself.

    Returns basic health info about the sidecar service.
    """
    return CommandResult.ok(
        {
            "health": "healthy",
            "pid": os.getpid(),
            "timestamp": time.time(),
        }
    )


@register_handler(Command.STATUS)
def handle_status(params: dict[str, Any]) -> CommandResult:
    """Get status of all or specific instances.

    Params:
        instance_type: Optional, one of "web", "worker", "scheduler"
        identifier: Optional, specific instance to query

    Returns:
        List of instance statuses with health info.
    """
    from rots import systemd

    instance_type = params.get("instance_type")
    identifier = params.get("identifier")

    try:
        instances: list[dict[str, Any]] = []

        # Get health map for all containers
        health_map = systemd.get_container_health_map()

        # Determine which types to query
        types_to_query = [instance_type] if instance_type else ["web", "worker", "scheduler"]

        for inst_type in types_to_query:
            # Get appropriate discover function
            if inst_type == "web":
                ids = systemd.discover_web_instances()
                ids = [str(i) for i in ids]  # Convert ports to strings
            elif inst_type == "worker":
                ids = systemd.discover_worker_instances()
            elif inst_type == "scheduler":
                ids = systemd.discover_scheduler_instances()
            else:
                continue

            # Filter by identifier if specified
            if identifier:
                ids = [i for i in ids if str(i) == str(identifier)]

            for inst_id in ids:
                unit = f"{systemd.unit_name(inst_type, str(inst_id))}.service"
                is_active = systemd.is_active(unit)
                health_info = health_map.get((inst_type, str(inst_id)), {})

                instances.append(
                    {
                        "type": inst_type,
                        "identifier": inst_id,
                        "unit": unit,
                        "active": is_active,
                        "health": health_info.get("health", "unknown"),
                        "uptime": health_info.get("uptime", ""),
                    }
                )

        return CommandResult.ok({"instances": instances})
    except Exception as e:
        logger.exception("Error getting status")
        return CommandResult.fail(str(e))
