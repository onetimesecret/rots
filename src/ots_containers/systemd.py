# src/ots_containers/systemd.py

import logging
import re
import shutil
import subprocess
import sys

logger = logging.getLogger(__name__)


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


def _fetch_journal(unit: str, lines: int = 20) -> str:
    """Fetch recent journal entries for a unit. Best-effort, never raises."""
    try:
        result = subprocess.run(
            ["sudo", "journalctl", "--no-pager", "-n", str(lines), "-u", unit],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.stdout.strip()
    except (subprocess.SubprocessError, OSError):
        return "(could not retrieve journal)"


def _run_systemctl(action: str, unit: str) -> None:
    """Run a systemctl command with diagnostic output on failure."""
    cmd = ["sudo", "systemctl", action, unit]
    logger.debug("  $ %s", " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
    if result.returncode != 0:
        journal = _fetch_journal(unit)
        raise SystemctlError(unit, action, journal)


def require_systemctl() -> None:
    """Check that systemctl is available, exit with helpful message if not.

    Call this at the start of any function that requires systemd.
    """
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


def require_podman() -> None:
    """Check that podman is available, exit with helpful message if not.

    Call this at the start of any function that shells out to podman directly.
    """
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


def _discover_instances(unit_type: str, running_only: bool = False) -> list[str]:
    """Find onetime-{unit_type}@* units and return their instance identifiers.

    This is the shared implementation for all discover_*_instances functions.
    Callers are responsible for converting identifiers to the appropriate type
    (e.g., int for web ports).

    Args:
        unit_type: The unit type segment, e.g. "web", "worker", "scheduler".
        running_only: If True, only return units that are active and running.
                      If False (default), return all loaded units regardless of state.

    Returns:
        Sorted list of instance identifier strings.
    """
    require_systemctl()
    result = subprocess.run(
        [
            "systemctl",
            "list-units",
            f"onetime-{unit_type}@*",
            "--plain",
            "--no-legend",
            "--all",
        ],
        capture_output=True,
        text=True,
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


def discover_web_instances(running_only: bool = False) -> list[int]:
    """Find onetime-web@* units and return their ports.

    Args:
        running_only: If True, only return units that are active and running.
                      If False (default), return all loaded units regardless of state.
    """
    ids = _discover_instances("web", running_only=running_only)
    # Web instances use numeric port identifiers; non-numeric entries are silently skipped.
    ports = [int(i) for i in ids if i.isdigit()]
    return sorted(ports)


def discover_worker_instances(running_only: bool = False) -> list[str]:
    """Find onetime-worker@* units and return their instance IDs.

    Worker instance IDs can be numeric (1, 2, 3) or named (billing, emails).

    Args:
        running_only: If True, only return units that are active and running.
                      If False (default), return all loaded units regardless of state.
    """
    return _discover_instances("worker", running_only=running_only)


def discover_scheduler_instances(running_only: bool = False) -> list[str]:
    """Find onetime-scheduler@* units and return their instance IDs.

    Scheduler instance IDs can be numeric (1, 2) or named (main, cron).

    Args:
        running_only: If True, only return units that are active and running.
                      If False (default), return all loaded units regardless of state.
    """
    return _discover_instances("scheduler", running_only=running_only)


def daemon_reload() -> None:
    require_systemctl()
    cmd = ["sudo", "systemctl", "daemon-reload"]
    logger.debug("  $ %s", " ".join(cmd))
    subprocess.run(cmd, check=True, timeout=30)  # no unit context, let CalledProcessError propagate


def start(unit: str) -> None:
    require_systemctl()
    _run_systemctl("start", unit)


def stop(unit: str) -> None:
    require_systemctl()
    _run_systemctl("stop", unit)


def reset_failed(unit: str) -> None:
    """Clear failed state for a unit so it doesn't appear in discovery."""
    require_systemctl()
    cmd = ["sudo", "systemctl", "reset-failed", unit]
    logger.debug("  $ %s", " ".join(cmd))
    # Suppress stderr - it's fine if the unit wasn't in failed state
    subprocess.run(cmd, stderr=subprocess.DEVNULL, timeout=10)


def restart(unit: str) -> None:
    require_systemctl()
    _run_systemctl("restart", unit)


def disable(unit: str) -> None:
    """Disable a unit so it does not auto-start on reboot.

    Non-fatal if the unit is not currently enabled — systemctl disable is
    idempotent and exits 0 even when the unit was never enabled.
    """
    require_systemctl()
    cmd = ["sudo", "systemctl", "disable", unit]
    logger.debug("  $ %s", " ".join(cmd))
    # Suppress stderr — 'not enabled' is not an error worth surfacing
    subprocess.run(cmd, stderr=subprocess.DEVNULL, timeout=10)


def unit_to_container_name(unit: str) -> str:
    """Convert systemd unit name to Quadlet container name.

    Quadlet names containers as: systemd-{unit_with_separator}
    Example: onetime-web@7044 -> systemd-onetime-web--7044

    Uses ``--`` (double dash) as the separator between the template base name
    and the instance identifier.  A single ``_`` was previously used but is
    ambiguous because ``_`` can also appear in unit names, making it impossible
    to distinguish ``onetime-web@7043`` from a hypothetical ``onetime-web_7043``.
    Double dashes are the conventional systemd escaping character and will not
    collide with naturally occurring underscores in unit names.
    """
    # Remove .service suffix if present
    name = unit.removesuffix(".service")
    # Replace @ with -- (distinctive separator that won't collide with underscores)
    name = name.replace("@", "--")
    return f"systemd-{name}"


def recreate(unit: str) -> None:
    """Stop, remove, and start a Quadlet service to force container recreation.

    Use this instead of restart() when the Quadlet .container file has
    been modified and you need to ensure the container is recreated
    with the new configuration (e.g., new volume mounts, environment, etc.).

    The container must be removed between stop and start because podman
    preserves stopped containers. Without removal, start just restarts
    the existing container with its old configuration.
    """
    require_systemctl()
    # Stop the systemd unit
    _run_systemctl("stop", unit)

    # Remove the container (Quadlet uses systemd-{name} format with @ -> _)
    container_name = unit_to_container_name(unit)
    rm_cmd = ["sudo", "podman", "rm", "--ignore", container_name]
    logger.debug("  $ %s", " ".join(rm_cmd))
    subprocess.run(rm_cmd, check=True, timeout=30)

    # Start creates a fresh container from the updated quadlet
    _run_systemctl("start", unit)


def status(unit: str, lines: int = 25) -> None:
    require_systemctl()
    cmd = ["sudo", "systemctl", "--no-pager", f"-n{lines}", "status", unit]
    logger.debug("  $ %s", " ".join(cmd))
    subprocess.run(
        cmd,
        check=False,  # status returns non-zero if not running
        timeout=30,
    )


def unit_exists(unit: str) -> bool:
    """Check if a systemd unit exists (loaded or not)."""
    require_systemctl()
    result = subprocess.run(
        ["systemctl", "list-unit-files", unit, "--plain", "--no-legend"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    return bool(result.stdout.strip())


def container_exists(unit: str) -> bool:
    """Check if the Quadlet container for a unit exists (running or stopped).

    This is more reliable than unit_exists for template instances like
    onetime@7044, since list-unit-files only shows the template, not instances.
    """
    container_name = unit_to_container_name(unit)
    result = subprocess.run(
        ["podman", "container", "exists", container_name],
        capture_output=True,
        timeout=10,
    )
    return result.returncode == 0


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

    Raises:
        HealthCheckTimeoutError: If the unit is not active within ``timeout`` seconds.
    """
    import time

    require_systemctl()
    deadline = time.monotonic() + timeout
    last_state = "unknown"
    consecutive_failures = 0

    while time.monotonic() < deadline:
        result = subprocess.run(
            ["systemctl", "is-active", unit],
            capture_output=True,
            text=True,
            timeout=10,
        )
        last_state = result.stdout.strip()
        if result.returncode == 0 and last_state == "active":
            logger.debug("%s is active", unit)
            return
        if last_state == "failed":
            consecutive_failures += 1
            logger.debug(
                "%s is failed (consecutive: %d/%d), waiting...",
                unit,
                consecutive_failures,
                consecutive_failures_threshold,
            )
            # Only exit early once the failure has persisted across multiple
            # consecutive polls — a transient "failed" during restart is normal.
            if consecutive_failures >= consecutive_failures_threshold:
                break
        else:
            consecutive_failures = 0
            logger.debug("%s is %s, waiting...", unit, last_state)
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
) -> None:
    """Poll the HTTP health endpoint until it returns 200 or timeout is reached.

    Useful for web instances where systemd may report active before the
    application is ready to serve requests.

    Args:
        port: The port to check (e.g., 7043 for onetime-web@7043).
        timeout: Maximum seconds to wait (default: 60).
        poll_interval: Seconds between checks (default: 2.0).

    Raises:
        HttpHealthCheckTimeoutError: If the health endpoint does not return 200
            within ``timeout`` seconds.
    """
    import time
    import urllib.error
    import urllib.request

    url = f"http://localhost:{port}/health"
    deadline = time.monotonic() + timeout
    last_error = "not started"

    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=5) as response:  # noqa: S310
                if response.status == 200:
                    logger.debug("HTTP health check passed: %s", url)
                    return
                last_error = f"HTTP {response.status}"
        except urllib.error.HTTPError as e:
            last_error = f"HTTP {e.code}"
        except (urllib.error.URLError, OSError) as e:
            last_error = str(e)
        logger.debug("HTTP health check pending (%s): %s", last_error, url)
        time.sleep(poll_interval)

    raise HttpHealthCheckTimeoutError(port, timeout, last_error)
