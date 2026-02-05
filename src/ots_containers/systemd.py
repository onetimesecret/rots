# src/ots_containers/systemd.py

import re
import shutil
import subprocess
import sys


class SystemdNotAvailableError(Exception):
    """Raised when systemd/systemctl is not available on the system."""

    pass


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


def unit_name(instance_type: str, identifier: str) -> str:
    """Build a systemd unit name for an OTS instance.

    Args:
        instance_type: One of "web", "worker", "scheduler"
        identifier: Port number (for web) or ID (for worker/scheduler)

    Returns:
        Unit name like "onetime-web@7043" or "onetime-worker@billing"
    """
    return f"onetime-{instance_type}@{identifier}"


def discover_web_instances(running_only: bool = False) -> list[int]:
    """Find onetime-web@* units and return their ports.

    Args:
        running_only: If True, only return units that are active and running.
                      If False (default), return all loaded units regardless of state.
    """
    require_systemctl()
    result = subprocess.run(
        [
            "systemctl",
            "list-units",
            "onetime-web@*",
            "--plain",
            "--no-legend",
            "--all",
        ],
        capture_output=True,
        text=True,
    )
    ports = []
    for line in result.stdout.strip().splitlines():
        # Format: onetime-web@7043.service loaded active running Description...
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
        match = re.match(r"onetime-web@(\d+)\.service", unit)
        if match:
            ports.append(int(match.group(1)))
    return sorted(ports)


def discover_worker_instances(running_only: bool = False) -> list[str]:
    """Find onetime-worker@* units and return their instance IDs.

    Worker instance IDs can be numeric (1, 2, 3) or named (billing, emails).

    Args:
        running_only: If True, only return units that are active and running.
                      If False (default), return all loaded units regardless of state.
    """
    require_systemctl()
    result = subprocess.run(
        [
            "systemctl",
            "list-units",
            "onetime-worker@*",
            "--plain",
            "--no-legend",
            "--all",
        ],
        capture_output=True,
        text=True,
    )
    instances = []
    for line in result.stdout.strip().splitlines():
        # Format: onetime-worker@1.service loaded active running Description...
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
        # Match both numeric and named instances
        match = re.match(r"onetime-worker@([^.]+)\.service", unit)
        if match:
            instances.append(match.group(1))
    return sorted(instances)


def discover_scheduler_instances(running_only: bool = False) -> list[str]:
    """Find onetime-scheduler@* units and return their instance IDs.

    Scheduler instance IDs can be numeric (1, 2) or named (main, cron).

    Args:
        running_only: If True, only return units that are active and running.
                      If False (default), return all loaded units regardless of state.
    """
    require_systemctl()
    result = subprocess.run(
        [
            "systemctl",
            "list-units",
            "onetime-scheduler@*",
            "--plain",
            "--no-legend",
            "--all",
        ],
        capture_output=True,
        text=True,
    )
    instances = []
    for line in result.stdout.strip().splitlines():
        # Format: onetime-scheduler@main.service loaded active running Description...
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
        # Match both numeric and named instances
        match = re.match(r"onetime-scheduler@([^.]+)\.service", unit)
        if match:
            instances.append(match.group(1))
    return sorted(instances)


def daemon_reload() -> None:
    require_systemctl()
    cmd = ["sudo", "systemctl", "daemon-reload"]
    print(f"  $ {' '.join(cmd)}")
    subprocess.run(cmd, check=True)


def start(unit: str) -> None:
    require_systemctl()
    cmd = ["sudo", "systemctl", "start", unit]
    print(f"  $ {' '.join(cmd)}")
    subprocess.run(cmd, check=True)


def stop(unit: str) -> None:
    require_systemctl()
    cmd = ["sudo", "systemctl", "stop", unit]
    print(f"  $ {' '.join(cmd)}")
    subprocess.run(cmd, check=True)


def reset_failed(unit: str) -> None:
    """Clear failed state for a unit so it doesn't appear in discovery."""
    require_systemctl()
    cmd = ["sudo", "systemctl", "reset-failed", unit]
    print(f"  $ {' '.join(cmd)}")
    # Suppress stderr - it's fine if the unit wasn't in failed state
    subprocess.run(cmd, stderr=subprocess.DEVNULL)


def restart(unit: str) -> None:
    require_systemctl()
    cmd = ["sudo", "systemctl", "restart", unit]
    print(f"  $ {' '.join(cmd)}")
    subprocess.run(cmd, check=True)


def unit_to_container_name(unit: str) -> str:
    """Convert systemd unit name to Quadlet container name.

    Quadlet names containers as: systemd-{unit_with_underscores}
    Example: onetime-web@7044 -> systemd-onetime-web_7044
    """
    # Remove .service suffix if present
    name = unit.removesuffix(".service")
    # Replace @ with _ (Quadlet convention)
    name = name.replace("@", "_")
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
    stop_cmd = ["sudo", "systemctl", "stop", unit]
    print(f"  $ {' '.join(stop_cmd)}")
    subprocess.run(stop_cmd, check=True)

    # Remove the container (Quadlet uses systemd-{name} format with @ -> _)
    container_name = unit_to_container_name(unit)
    rm_cmd = ["sudo", "podman", "rm", "--ignore", container_name]
    print(f"  $ {' '.join(rm_cmd)}")
    subprocess.run(rm_cmd, check=True)

    # Start creates a fresh container from the updated quadlet
    start_cmd = ["sudo", "systemctl", "start", unit]
    print(f"  $ {' '.join(start_cmd)}")
    subprocess.run(start_cmd, check=True)


def status(unit: str, lines: int = 25) -> None:
    require_systemctl()
    cmd = ["sudo", "systemctl", "--no-pager", f"-n{lines}", "status", unit]
    print(f"  $ {' '.join(cmd)}")
    subprocess.run(
        cmd,
        check=False,  # status returns non-zero if not running
    )


def unit_exists(unit: str) -> bool:
    """Check if a systemd unit exists (loaded or not)."""
    require_systemctl()
    result = subprocess.run(
        ["systemctl", "list-unit-files", unit, "--plain", "--no-legend"],
        capture_output=True,
        text=True,
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
    )
    return result.returncode == 0
