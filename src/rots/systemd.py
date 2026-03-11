# src/rots/systemd.py

from __future__ import annotations

import logging
import re
import shutil
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ots_shared.ssh.executor import Executor

logger = logging.getLogger(__name__)


def _get_executor(executor: Executor | None = None) -> Executor:
    """Return the given executor or a default LocalExecutor."""
    if executor is not None:
        return executor
    from ots_shared.ssh import LocalExecutor

    return LocalExecutor()


def _is_local(executor: Executor) -> bool:
    """Check if the executor runs commands locally."""
    from ots_shared.ssh import LocalExecutor

    return isinstance(executor, LocalExecutor)


class SystemdNotAvailableError(Exception):
    """Raised when systemd/systemctl is not available on the system."""

    pass


class SystemctlError(Exception):
    """Raised when a systemctl command fails, with journal context."""

    def __init__(self, unit: str, action: str, journal: str) -> None:
        self.unit = unit
        self.action = action
        self.journal = journal
        super().__init__(f"{unit} failed to {action}")


def _fetch_journal(
    unit: str,
    lines: int = 20,
    *,
    executor: Executor | None = None,
) -> str:
    """Fetch recent journal entries for a unit. Best-effort, never raises."""
    ex = _get_executor(executor)
    try:
        result = ex.run(
            ["journalctl", "--no-pager", "-n", str(lines), "-u", unit],
            sudo=True,
            timeout=10,
        )
        return result.stdout.strip()
    except Exception:
        return "(could not retrieve journal)"


def _run_systemctl(
    action: str,
    unit: str,
    *,
    executor: Executor | None = None,
) -> None:
    """Run a systemctl command with diagnostic output on failure."""
    ex = _get_executor(executor)
    cmd = ["systemctl", action, unit]
    logger.debug(f"  $ sudo -- {' '.join(cmd)}")
    result = ex.run(cmd, sudo=True, timeout=90)
    if not result.ok:
        journal = _fetch_journal(unit, executor=executor)
        raise SystemctlError(unit, action, journal)


def require_systemctl(*, executor: Executor | None = None) -> None:
    """Check that systemctl is available, exit with helpful message if not.

    Call this at the start of any function that requires systemd.

    When *executor* is remote, the check runs ``which systemctl`` on the
    remote host.  When local (or ``None``), uses :func:`shutil.which`.
    """
    if executor is not None and not _is_local(_get_executor(executor)):
        result = executor.run(["which", "systemctl"], timeout=10)
        if not result.ok:
            print(
                "Error: systemctl not found on remote host.",
                file=sys.stderr,
            )
            raise SystemExit(1)
        return

    if not shutil.which("systemctl"):
        print(
            "Error: systemctl not found. This command requires Linux with systemd.",
            file=sys.stderr,
        )
        print(
            "\nThis command manages containers via systemd Quadlets and is not available on macOS.",
            file=sys.stderr,
        )
        print(
            "Note: 'ots instance shell' works on macOS for local development.",
            file=sys.stderr,
        )
        raise SystemExit(1)


def require_podman(*, executor: Executor | None = None) -> None:
    """Check that podman is available, exit with helpful message if not.

    Call this at the start of any function that shells out to podman directly.

    When *executor* is remote, the check runs ``which podman`` on the
    remote host.  When local (or ``None``), uses :func:`shutil.which`.
    """
    if executor is not None and not _is_local(_get_executor(executor)):
        result = executor.run(["which", "podman"], timeout=10)
        if not result.ok:
            print(
                "Error: podman not found on remote host.",
                file=sys.stderr,
            )
            raise SystemExit(1)
        return

    if not shutil.which("podman"):
        print(
            "Error: podman not found. This command requires Podman to be installed.",
            file=sys.stderr,
        )
        print(
            "\nInstall Podman: https://podman.io/docs/installation",
            file=sys.stderr,
        )
        print(
            "  Linux (Debian/Ubuntu): sudo apt-get install -y podman",
            file=sys.stderr,
        )
        print(
            "  macOS: brew install podman && podman machine init && podman machine start",
            file=sys.stderr,
        )
        raise SystemExit(1)


def unit_name(instance_type: str, identifier: str) -> str:
    """Build a systemd unit name for an OTS instance.

    Args:
        instance_type: One of "web", "worker", "scheduler"
        identifier: Port number (for web) or ID (for worker/scheduler)

    Returns:
        Unit name like "onetime-web@7043" or "onetime-worker@billing"
    """
    return f"onetime-{instance_type}@{identifier}"


def _discover_instances(
    unit_type: str,
    running_only: bool = False,
    *,
    executor: Executor | None = None,
) -> list[str]:
    """Find onetime-{unit_type}@* units and return their instance identifiers.

    This is the shared implementation for all discover_*_instances functions.
    Callers are responsible for converting identifiers to the appropriate type
    (e.g., int for web ports).

    Args:
        unit_type: The unit type segment, e.g. "web", "worker", "scheduler".
        running_only: If True, only return units that are active and running.
                      If False (default), return all loaded units regardless of state.
        executor: Executor for command dispatch. None uses LocalExecutor.

    Returns:
        Sorted list of instance identifier strings.
    """
    ex = _get_executor(executor)
    if _is_local(ex):
        require_systemctl()
    result = ex.run(
        [
            "systemctl",
            "list-units",
            f"onetime-{unit_type}@*",
            "--plain",
            "--no-legend",
            "--all",
        ],
        timeout=10,
    )
    instances = []
    pattern = re.compile(rf"onetime-{re.escape(unit_type)}@([^.]+)\.service")
    for line in result.stdout.strip().splitlines():
        # Format: onetime-{type}@<id>.service loaded active running Description...
        # Columns: UNIT LOAD ACTIVE SUB DESCRIPTION
        parts = line.split()
        if len(parts) < 4:
            continue
        unit, load, active, sub = parts[:4]
        # Skip units that aren't loaded
        if load != "loaded":
            continue
        # If running_only, filter to active+running
        if running_only and (active != "active" or sub != "running"):
            continue
        match = pattern.match(unit)
        if match:
            instances.append(match.group(1))
    return sorted(instances)


def discover_web_instances(
    running_only: bool = False,
    *,
    executor: Executor | None = None,
) -> list[int]:
    """Find onetime-web@* units and return their ports.

    Args:
        running_only: If True, only return units that are active and running.
                      If False (default), return all loaded units regardless of state.
        executor: Executor for command dispatch. None uses LocalExecutor.
    """
    ids = _discover_instances("web", running_only=running_only, executor=executor)
    # Web instances use numeric port identifiers; non-numeric entries are silently skipped.
    ports = [int(i) for i in ids if i.isdigit()]
    return sorted(ports)


def discover_worker_instances(
    running_only: bool = False,
    *,
    executor: Executor | None = None,
) -> list[str]:
    """Find onetime-worker@* units and return their instance IDs.

    Worker instance IDs can be numeric (1, 2, 3) or named (billing, emails).

    Args:
        running_only: If True, only return units that are active and running.
                      If False (default), return all loaded units regardless of state.
        executor: Executor for command dispatch. None uses LocalExecutor.
    """
    return _discover_instances("worker", running_only=running_only, executor=executor)


def discover_scheduler_instances(
    running_only: bool = False,
    *,
    executor: Executor | None = None,
) -> list[str]:
    """Find onetime-scheduler@* units and return their instance IDs.

    Scheduler instance IDs can be numeric (1, 2) or named (main, cron).

    Args:
        running_only: If True, only return units that are active and running.
                      If False (default), return all loaded units regardless of state.
        executor: Executor for command dispatch. None uses LocalExecutor.
    """
    return _discover_instances("scheduler", running_only=running_only, executor=executor)


def is_active(unit: str, *, executor: Executor | None = None) -> str:
    """Return the active state string for a unit (e.g. 'active', 'inactive', 'failed')."""
    ex = _get_executor(executor)
    if _is_local(ex):
        require_systemctl()
    result = ex.run(["systemctl", "is-active", unit], timeout=10)
    return result.stdout.strip()


def enable(unit: str, *, executor: Executor | None = None) -> None:
    """Enable a unit to auto-start on reboot."""
    ex = _get_executor(executor)
    if _is_local(ex):
        require_systemctl()
    _run_systemctl("enable", unit, executor=executor)


def daemon_reload(*, executor: Executor | None = None) -> None:
    ex = _get_executor(executor)
    if _is_local(ex):
        require_systemctl()
    cmd = ["systemctl", "daemon-reload"]
    logger.debug(f"  $ sudo -- {' '.join(cmd)}")
    result = ex.run(cmd, sudo=True, timeout=30)
    if not result.ok:
        raise SystemctlError("(all units)", "daemon-reload", result.stderr.strip())


def start(unit: str, *, executor: Executor | None = None) -> None:
    ex = _get_executor(executor)
    if _is_local(ex):
        require_systemctl()
    _run_systemctl("start", unit, executor=executor)


def stop(unit: str, *, executor: Executor | None = None) -> None:
    ex = _get_executor(executor)
    if _is_local(ex):
        require_systemctl()
    _run_systemctl("stop", unit, executor=executor)


def reset_failed(unit: str, *, executor: Executor | None = None) -> None:
    """Clear failed state for a unit so it doesn't appear in discovery."""
    ex = _get_executor(executor)
    if _is_local(ex):
        require_systemctl()
    cmd = ["systemctl", "reset-failed", unit]
    logger.debug(f"  $ sudo -- {' '.join(cmd)}")
    # Suppress stderr - it's fine if the unit wasn't in failed state
    ex.run(cmd, sudo=True, timeout=10)


def restart(unit: str, *, executor: Executor | None = None) -> None:
    ex = _get_executor(executor)
    if _is_local(ex):
        require_systemctl()
    _run_systemctl("restart", unit, executor=executor)


def disable(unit: str, *, executor: Executor | None = None) -> None:
    """Disable a unit so it does not auto-start on reboot.

    Non-fatal if the unit is not currently enabled — systemctl disable is
    idempotent and exits 0 even when the unit was never enabled.
    """
    ex = _get_executor(executor)
    if _is_local(ex):
        require_systemctl()
    cmd = ["systemctl", "disable", unit]
    logger.debug(f"  $ sudo -- {' '.join(cmd)}")
    # Suppress stderr — 'not enabled' is not an error worth surfacing
    ex.run(cmd, sudo=True, timeout=10)


def get_container_health_map(
    *,
    executor: Executor | None = None,
) -> dict[tuple[str, str], dict[str, str]]:
    """Fetch health and uptime for all OTS containers via a single ``podman ps``.

    Returns a dict keyed by ``(instance_type, identifier)`` tuples, e.g.
    ``("web", "7043")``, with values like ``{"health": "healthy", "uptime": "Up 3 days"}``.

    Handles both ``_`` and ``--`` separators in container names
    (``systemd-onetime-web_7043`` and ``systemd-onetime-web--7043``).
    """
    import json as _json

    ex = _get_executor(executor)
    result = ex.run(
        [
            "podman",
            "ps",
            "--no-trunc",
            "--format",
            "json",
            "--filter",
            "name=onetime-",
        ],
        timeout=15,
    )

    if not result.ok or not result.stdout.strip():
        return {}

    try:
        containers = _json.loads(result.stdout)
    except (ValueError, TypeError):
        logger.debug("Failed to parse podman ps JSON output")
        return {}

    health_map: dict[tuple[str, str], dict[str, str]] = {}
    # Match container names like onetime-web@7043 or onetime-worker@1
    name_pattern = re.compile(r"onetime-(web|worker|scheduler)@(.+)")

    for container in containers:
        # podman JSON uses "Names" (list) or "Name" (string) depending on version
        names = container.get("Names") or [container.get("Name", "")]
        if isinstance(names, str):
            names = [names]

        for name in names:
            m = name_pattern.match(name)
            if not m:
                continue
            inst_type = m.group(1)
            inst_id = m.group(2)

            status_str = container.get("Status", "")
            # Extract health: "Up 3 days (healthy)" → "healthy"
            health_match = re.search(r"\((healthy|unhealthy|starting)\)", status_str)
            health = health_match.group(1) if health_match else ""
            # Extract uptime: everything before the parenthetical
            uptime = re.sub(r"\s*\(.*?\)\s*$", "", status_str).strip()

            health_map[(inst_type, inst_id)] = {
                "health": health,
                "uptime": uptime,
            }

    return health_map


def unit_to_container_name(unit: str) -> str:
    """Convert systemd unit name to the explicit ``ContainerName=`` we set.

    We set ``ContainerName=`` in each Quadlet template using the same
    ``@`` convention as the systemd unit itself::

        onetime-web@7043.service  ->  onetime-web@7043
        onetime-worker@1          ->  onetime-worker@1
        onetime-scheduler@main    ->  onetime-scheduler@main
    """
    return unit.removesuffix(".service")


def recreate(unit: str, *, executor: Executor | None = None) -> None:
    """Stop, remove, and start a Quadlet service to force container recreation.

    Use this instead of restart() when the Quadlet .container file has
    been modified and you need to ensure the container is recreated
    with the new configuration (e.g., new volume mounts, environment, etc.).

    The container must be removed between stop and start because podman
    preserves stopped containers. Without removal, start just restarts
    the existing container with its old configuration.
    """
    ex = _get_executor(executor)
    if _is_local(ex):
        require_systemctl()
    # Stop the systemd unit
    _run_systemctl("stop", unit, executor=executor)

    # Remove the container (ContainerName= set to onetime-{type}@{id})
    container_name = unit_to_container_name(unit)
    rm_cmd = ["podman", "rm", "--ignore", container_name]
    logger.debug(f"  $ sudo -- {' '.join(rm_cmd)}")
    ex.run(rm_cmd, sudo=True, timeout=30, check=True)

    # Start creates a fresh container from the updated quadlet
    _run_systemctl("start", unit, executor=executor)


def status(
    unit: str,
    lines: int = 25,
    *,
    executor: Executor | None = None,
) -> None:
    ex = _get_executor(executor)
    if _is_local(ex):
        require_systemctl()
    cmd = ["systemctl", "--no-pager", f"-n{lines}", "status", unit]
    logger.debug(f"  $ sudo -- {' '.join(cmd)}")
    result = ex.run(cmd, sudo=True, timeout=30)
    # Print output directly (status is for human consumption)
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)


def unit_exists(unit: str, *, executor: Executor | None = None) -> bool:
    """Check if a systemd unit exists (loaded or not)."""
    ex = _get_executor(executor)
    if _is_local(ex):
        require_systemctl()
    result = ex.run(
        ["systemctl", "list-unit-files", unit, "--plain", "--no-legend"],
        timeout=10,
    )
    return bool(result.stdout.strip())


def container_exists(unit: str, *, executor: Executor | None = None) -> bool:
    """Check if the Quadlet container for a unit exists (running or stopped).

    This is more reliable than unit_exists for template instances like
    onetime@7044, since list-unit-files only shows the template, not instances.
    """
    ex = _get_executor(executor)
    container_name = unit_to_container_name(unit)
    result = ex.run(
        ["podman", "container", "exists", container_name],
        timeout=10,
    )
    return result.ok


class HealthCheckTimeoutError(Exception):
    """Raised when a unit fails to become active within the configured timeout."""

    def __init__(self, unit: str, timeout: int, last_state: str) -> None:
        self.unit = unit
        self.timeout = timeout
        self.last_state = last_state
        super().__init__(
            f"{unit} did not become active within {timeout}s (last state: {last_state})"
        )


def wait_for_healthy(
    unit: str,
    timeout: int = 60,
    poll_interval: float = 2.0,
    consecutive_failures_threshold: int = 3,
    *,
    executor: Executor | None = None,
) -> None:
    """Poll systemctl until the unit is active or timeout is reached.

    Args:
        unit: Systemd unit name (e.g., "onetime-web@7043")
        timeout: Maximum seconds to wait (default: 60)
        poll_interval: Seconds between checks (default: 2.0)
        consecutive_failures_threshold: Number of consecutive "failed" polls
            required before treating the failure as terminal and stopping early
            (default: 3).  A single "failed" reading can be transient — the unit
            may be in the process of restarting — so we tolerate a few before
            giving up.
        executor: Executor for command dispatch. None uses LocalExecutor.

    Raises:
        HealthCheckTimeoutError: If the unit is not active within ``timeout`` seconds.
    """
    import time

    ex = _get_executor(executor)
    if _is_local(ex):
        require_systemctl()
    deadline = time.monotonic() + timeout
    last_state = "unknown"
    consecutive_failures = 0

    while time.monotonic() < deadline:
        result = ex.run(
            ["systemctl", "is-active", unit],
            timeout=10,
        )
        last_state = result.stdout.strip()
        if result.ok and last_state == "active":
            logger.debug(f"{unit} is active")
            return
        if last_state == "failed":
            consecutive_failures += 1
            logger.debug(
                f"{unit} is failed"
                f" (consecutive: {consecutive_failures}/{consecutive_failures_threshold}),"
                " waiting..."
            )
            # Only exit early once the failure has persisted across multiple
            # consecutive polls — a transient "failed" during restart is normal.
            if consecutive_failures >= consecutive_failures_threshold:
                break
        else:
            consecutive_failures = 0
            logger.debug(f"{unit} is {last_state}, waiting...")
        time.sleep(poll_interval)

    raise HealthCheckTimeoutError(unit, timeout, last_state)


class HttpHealthCheckTimeoutError(Exception):
    """Raised when HTTP health endpoint does not return 200 within the timeout."""

    def __init__(self, port: int, timeout: int, last_error: str) -> None:
        self.port = port
        self.timeout = timeout
        self.last_error = last_error
        super().__init__(
            f"http://localhost:{port}/health did not return 200 within {timeout}s "
            f"(last error: {last_error})"
        )


def wait_for_http_healthy(
    port: int,
    timeout: int = 60,
    poll_interval: float = 2.0,
    *,
    executor: Executor | None = None,
) -> None:
    """Poll the HTTP health endpoint until it returns 200 or timeout is reached.

    Useful for web instances where systemd may report active before the
    application is ready to serve requests.

    When *executor* is a :class:`LocalExecutor` (or ``None``), the check uses
    ``urllib`` directly — no external process needed.  When the executor is
    remote (e.g. :class:`SSHExecutor`), the check runs
    ``curl -sf http://localhost:{port}/health`` on the remote host so that
    ``localhost`` resolves to the correct machine.

    Args:
        port: The port to check (e.g., 7043 for onetime-web@7043).
        timeout: Maximum seconds to wait (default: 60).
        poll_interval: Seconds between checks (default: 2.0).
        executor: Executor for command dispatch. None uses LocalExecutor.

    Raises:
        HttpHealthCheckTimeoutError: If the health endpoint does not return 200
            within ``timeout`` seconds.
    """
    import time

    ex = _get_executor(executor)
    url = f"http://localhost:{port}/health"
    deadline = time.monotonic() + timeout
    last_error = "not started"

    if _is_local(ex):
        # Local: use urllib directly (no subprocess overhead)
        import urllib.error
        import urllib.request

        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(url, timeout=5) as response:  # noqa: S310
                    if response.status == 200:
                        logger.debug(f"HTTP health check passed: {url}")
                        return
                    last_error = f"HTTP {response.status}"
            except urllib.error.HTTPError as e:
                last_error = f"HTTP {e.code}"
            except (urllib.error.URLError, OSError) as e:
                last_error = str(e)
            logger.debug(f"HTTP health check pending ({last_error}): {url}")
            time.sleep(poll_interval)
    else:
        # Remote: run curl on the target host so localhost is correct
        curl_cmd = ["curl", "-sf", url]
        while time.monotonic() < deadline:
            result = ex.run(curl_cmd, timeout=10)
            if result.ok:
                logger.debug(f"HTTP health check passed (remote): {url}")
                return
            last_error = f"curl exit {result.returncode}"
            logger.debug(f"HTTP health check pending ({last_error}): {url}")
            time.sleep(poll_interval)

    raise HttpHealthCheckTimeoutError(port, timeout, last_error)
