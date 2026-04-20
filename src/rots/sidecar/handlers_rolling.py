# src/rots/sidecar/handlers_rolling.py

"""Rolling restart handler for bulk instance operations.

Implements instances.restart_all which restarts all instances with
health checks and optional type filtering. Uses systemd discovery
and database for instance tracking.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from rots import systemd

from .commands import Command, CommandResult, register_handler

logger = logging.getLogger(__name__)

# Default configuration
DEFAULT_DELAY = 5  # seconds between instance restarts
DEFAULT_HEALTH_TIMEOUT = 60  # seconds to wait for health
DEFAULT_POLL_INTERVAL = 2.0  # seconds between health polls


def _wait_for_healthy(
    instance_type: str,
    identifier: str,
    timeout: int = DEFAULT_HEALTH_TIMEOUT,
) -> bool:
    """Wait for an instance to become healthy.

    Args:
        instance_type: One of "web", "worker", "scheduler"
        identifier: Port (web) or instance ID (worker/scheduler)
        timeout: Maximum seconds to wait

    Returns:
        True if healthy within timeout, False otherwise
    """
    unit = f"{systemd.unit_name(instance_type, str(identifier))}.service"
    deadline = time.monotonic() + timeout

    while time.monotonic() < deadline:
        # First check systemd status
        if not systemd.is_active(unit):
            time.sleep(DEFAULT_POLL_INTERVAL)
            continue

        # For web instances, check HTTP health
        if instance_type == "web":
            try:
                port = int(identifier)
                # Quick HTTP check without full wait_for_http_healthy
                import urllib.error
                import urllib.request

                url = f"http://localhost:{port}/health"
                with urllib.request.urlopen(url, timeout=5) as response:  # noqa: S310
                    if response.status == 200:
                        return True
            except (urllib.error.URLError, OSError, ValueError):
                pass
        else:
            # For workers/scheduler, check container health via podman
            health_map = systemd.get_container_health_map()
            health_info = health_map.get((instance_type, str(identifier)), {})
            if health_info.get("health") == "healthy":
                return True

        time.sleep(DEFAULT_POLL_INTERVAL)

    return False


def _restart_instance(
    instance_type: str,
    identifier: str,
    health_timeout: int,
) -> dict[str, Any]:
    """Restart a single instance and wait for health.

    Returns a dict with status and details.
    """
    unit = f"{systemd.unit_name(instance_type, str(identifier))}.service"

    try:
        systemd.restart(unit)
    except systemd.SystemctlError as e:
        return {
            "identifier": identifier,
            "type": instance_type,
            "success": False,
            "error": str(e),
            "journal": e.journal,
        }
    except Exception as e:
        return {
            "identifier": identifier,
            "type": instance_type,
            "success": False,
            "error": str(e),
        }

    # Wait for health
    healthy = _wait_for_healthy(instance_type, identifier, timeout=health_timeout)

    return {
        "identifier": identifier,
        "type": instance_type,
        "success": True,
        "healthy": healthy,
    }


@register_handler(Command.INSTANCES_RESTART_ALL)
def handle_instances_restart_all(params: dict[str, Any]) -> CommandResult:
    """Rolling restart of all instances with health checks.

    Restarts instances in order: workers -> schedulers -> web
    This order ensures background jobs complete before web restarts,
    and web instances (user-facing) are restarted last.

    Params:
        type (optional): Filter to specific type ("web", "worker", "scheduler")
        delay (optional): Seconds between instance restarts (default: 5)
        health_timeout (optional): Seconds to wait for health (default: 60)
        stop_on_failure (optional): Stop rolling restart on first failure (default: True)

    Returns:
        CommandResult with restart status for each instance
    """
    type_filter = params.get("type")
    delay = params.get("delay", DEFAULT_DELAY)
    health_timeout = params.get("health_timeout", DEFAULT_HEALTH_TIMEOUT)
    stop_on_failure = params.get("stop_on_failure", True)

    # Validate type filter if provided
    valid_types = {"web", "worker", "scheduler"}
    if type_filter and type_filter not in valid_types:
        return CommandResult.fail(
            f"Invalid type filter: {type_filter}. Valid: {', '.join(sorted(valid_types))}"
        )

    # Collect instances to restart
    instances_to_restart: list[tuple[str, str]] = []  # (type, identifier)

    if type_filter:
        # Single type requested
        types_to_process = [type_filter]
    else:
        # All types in order: workers, schedulers, web
        types_to_process = ["worker", "scheduler", "web"]

    for instance_type in types_to_process:
        if instance_type == "web":
            identifiers = [str(p) for p in systemd.discover_web_instances()]
        elif instance_type == "worker":
            identifiers = systemd.discover_worker_instances()
        elif instance_type == "scheduler":
            identifiers = systemd.discover_scheduler_instances()
        else:
            continue

        for identifier in identifiers:
            instances_to_restart.append((instance_type, identifier))

    if not instances_to_restart:
        return CommandResult.ok(
            {
                "message": "No instances found to restart",
                "type_filter": type_filter,
            }
        )

    logger.info(
        "Starting rolling restart of %d instances (type=%s, delay=%ds)",
        len(instances_to_restart),
        type_filter or "all",
        delay,
    )

    # Execute rolling restart
    results: list[dict[str, Any]] = []
    failures: list[str] = []
    warnings: list[str] = []

    for i, (instance_type, identifier) in enumerate(instances_to_restart):
        # Delay between restarts (except first)
        if i > 0 and delay > 0:
            time.sleep(delay)

        logger.info(
            "Restarting %s/%s (%d/%d)",
            instance_type,
            identifier,
            i + 1,
            len(instances_to_restart),
        )

        result = _restart_instance(instance_type, identifier, health_timeout)
        results.append(result)

        if not result["success"]:
            failures.append(f"{instance_type}/{identifier}")
            if stop_on_failure:
                return CommandResult(
                    success=False,
                    data={
                        "completed": i,
                        "total": len(instances_to_restart),
                        "results": results,
                        "stopped_at": f"{instance_type}/{identifier}",
                    },
                    error=(
                        f"Rolling restart stopped at {instance_type}/{identifier}: "
                        f"{result.get('error')}"
                    ),
                    warnings=warnings,
                )
        elif not result.get("healthy", False):
            # Restarted but not healthy - add warning but continue
            warnings.append(f"{instance_type}/{identifier} restarted but health check timed out")

    # Determine overall success
    success = len(failures) == 0

    return CommandResult(
        success=success,
        data={
            "completed": len(results),
            "total": len(instances_to_restart),
            "failures": failures,
            "results": results,
        },
        error=f"Rolling restart completed with {len(failures)} failure(s)" if failures else None,
        warnings=warnings,
    )
