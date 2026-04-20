# src/rots/sidecar/handlers_phased.py

"""Phased restart handlers for graceful service restarts.

Phased restarts use signals to trigger graceful worker replacement
without dropping connections:
- Web (Puma): SIGUSR2 triggers phased restart
- Worker: SIGUSR1 triggers graceful shutdown (new worker spawned by systemd)
"""

from __future__ import annotations

import logging
import signal
import time
from typing import Any

from rots import systemd
from rots.podman import Podman

from .commands import Command, CommandResult, register_handler

logger = logging.getLogger(__name__)

# Default timeouts for health checks after phased restart
DEFAULT_HEALTH_TIMEOUT = 120  # seconds
DEFAULT_POLL_INTERVAL = 2.0  # seconds


def _get_container_pid(container_name: str) -> int | None:
    """Get the PID of the main process in a container.

    Returns None if the container is not running or PID cannot be determined.
    """
    podman = Podman()
    result = podman.inspect(
        container_name,
        format="{{ .State.Pid }}",
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None

    pid_str = result.stdout.strip()
    if not pid_str or pid_str == "0":
        return None

    try:
        return int(pid_str)
    except ValueError:
        return None


def _send_signal_to_container(
    container_name: str,
    sig: signal.Signals,
) -> tuple[bool, str]:
    """Send a signal to the main process of a container.

    Args:
        container_name: Name of the container (e.g., "systemd-onetime-web@7043")
        sig: Signal to send

    Returns:
        Tuple of (success, message)
    """
    podman = Podman()
    result = podman.kill(
        container_name,
        signal=sig.name,
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        return True, f"Sent {sig.name} to {container_name}"
    else:
        error = result.stderr.strip() if result.stderr else "unknown error"
        return False, f"Failed to send {sig.name}: {error}"


@register_handler(Command.PHASED_RESTART_WEB)
def handle_phased_restart_web(params: dict[str, Any]) -> CommandResult:
    """Phased restart for web instance using Puma's SIGUSR2.

    Puma handles SIGUSR2 by forking a new master process, gradually
    replacing workers while continuing to serve requests. Once the
    new master is ready, the old one exits.

    Params:
        port (required): Web instance port (e.g., 7043)
        timeout (optional): Health check timeout in seconds (default: 120)
        escalate (optional): If True, fall back to full restart on failure (default: True)

    Returns:
        CommandResult with status of the operation
    """
    # Validate required params
    port = params.get("port")
    if port is None:
        return CommandResult.fail("Missing required parameter: port")

    try:
        port = int(port)
    except (TypeError, ValueError):
        return CommandResult.fail(f"Invalid port: {port}")

    timeout = params.get("timeout", DEFAULT_HEALTH_TIMEOUT)
    escalate = params.get("escalate", True)

    # Build identifiers
    unit = systemd.unit_name("web", str(port))
    container_name = f"systemd-onetime-web@{port}"

    # Check unit is active first
    if not systemd.is_active(f"{unit}.service"):
        return CommandResult.fail(f"Unit {unit} is not active")

    logger.info("Starting phased restart for %s", unit)

    # Send SIGUSR2 to trigger Puma phased restart
    success, message = _send_signal_to_container(container_name, signal.SIGUSR2)
    if not success:
        if escalate:
            logger.warning(
                "Failed to send SIGUSR2 to %s, escalating to full restart: %s",
                container_name,
                message,
            )
            return _escalate_to_full_restart(unit, port, timeout)
        return CommandResult.fail(message)

    logger.info("Sent SIGUSR2 to %s, waiting for health check", container_name)

    # Wait for the instance to become healthy
    try:
        systemd.wait_for_http_healthy(port, timeout=timeout, poll_interval=DEFAULT_POLL_INTERVAL)
        return CommandResult.ok(
            {
                "action": "phased_restart",
                "instance_type": "web",
                "port": port,
                "method": "SIGUSR2",
            }
        )
    except systemd.HttpHealthCheckTimeoutError as e:
        if escalate:
            logger.warning(
                "Phased restart health check failed for %s, escalating: %s",
                unit,
                e,
            )
            return _escalate_to_full_restart(unit, port, timeout)
        return CommandResult.fail(f"Health check timeout after phased restart: {e}")


def _escalate_to_full_restart(
    unit: str,
    port: int,
    timeout: int,
) -> CommandResult:
    """Fall back to a full systemd restart when phased restart fails."""
    service = f"{unit}.service"
    logger.info("Performing full restart of %s", service)

    try:
        systemd.restart(service)
        systemd.wait_for_http_healthy(port, timeout=timeout, poll_interval=DEFAULT_POLL_INTERVAL)
        result = CommandResult.ok(
            {
                "action": "full_restart",
                "instance_type": "web",
                "port": port,
                "method": "systemctl restart",
                "escalated": True,
            }
        )
        result.warnings.append("Escalated from phased to full restart")
        return result
    except systemd.HttpHealthCheckTimeoutError as e:
        return CommandResult.fail(f"Full restart failed health check: {e}")
    except systemd.SystemctlError as e:
        return CommandResult.fail(f"Full restart failed: {e}")


@register_handler(Command.PHASED_RESTART_WORKER)
def handle_phased_restart_worker(params: dict[str, Any]) -> CommandResult:
    """Phased restart for worker instance.

    Workers handle SIGUSR1 for graceful shutdown - they finish current
    jobs before exiting. Systemd then spawns a new worker process.

    Params:
        worker_id (required): Worker instance identifier (e.g., "billing", "emails")
        timeout (optional): Health check timeout in seconds (default: 120)
        escalate (optional): If True, fall back to full restart on failure (default: True)

    Returns:
        CommandResult with status of the operation
    """
    # Validate required params
    worker_id = params.get("worker_id")
    if worker_id is None:
        return CommandResult.fail("Missing required parameter: worker_id")

    worker_id = str(worker_id)
    timeout = params.get("timeout", DEFAULT_HEALTH_TIMEOUT)
    escalate = params.get("escalate", True)

    # Build identifiers
    unit = systemd.unit_name("worker", worker_id)
    service = f"{unit}.service"
    container_name = f"systemd-onetime-worker@{worker_id}"

    # Check unit is active first
    if not systemd.is_active(service):
        return CommandResult.fail(f"Unit {unit} is not active")

    logger.info("Starting phased restart for %s", unit)

    # Send SIGUSR1 to trigger graceful shutdown
    # The worker will finish current jobs and exit, systemd restarts it
    success, message = _send_signal_to_container(container_name, signal.SIGUSR1)
    if not success:
        if escalate:
            logger.warning(
                "Failed to send SIGUSR1 to %s, escalating to full restart: %s",
                container_name,
                message,
            )
            return _escalate_worker_to_full_restart(unit, service, worker_id, timeout)
        return CommandResult.fail(message)

    logger.info("Sent SIGUSR1 to %s, waiting for restart", container_name)

    # Wait for the unit to become active again (systemd will restart it)
    try:
        # Give the worker a moment to shut down
        time.sleep(2)
        systemd.wait_for_healthy(service, timeout=timeout, poll_interval=DEFAULT_POLL_INTERVAL)
        return CommandResult.ok(
            {
                "action": "phased_restart",
                "instance_type": "worker",
                "worker_id": worker_id,
                "method": "SIGUSR1",
            }
        )
    except systemd.HealthCheckTimeoutError as e:
        if escalate:
            logger.warning(
                "Phased restart health check failed for %s, escalating: %s",
                unit,
                e,
            )
            return _escalate_worker_to_full_restart(unit, service, worker_id, timeout)
        return CommandResult.fail(f"Health check timeout after phased restart: {e}")


def _escalate_worker_to_full_restart(
    unit: str,
    service: str,
    worker_id: str,
    timeout: int,
) -> CommandResult:
    """Fall back to a full systemd restart when phased restart fails."""
    logger.info("Performing full restart of %s", service)

    try:
        systemd.restart(service)
        systemd.wait_for_healthy(service, timeout=timeout, poll_interval=DEFAULT_POLL_INTERVAL)
        result = CommandResult.ok(
            {
                "action": "full_restart",
                "instance_type": "worker",
                "worker_id": worker_id,
                "method": "systemctl restart",
                "escalated": True,
            }
        )
        result.warnings.append("Escalated from phased to full restart")
        return result
    except systemd.HealthCheckTimeoutError as e:
        return CommandResult.fail(f"Full restart failed health check: {e}")
    except systemd.SystemctlError as e:
        return CommandResult.fail(f"Full restart failed: {e}")
