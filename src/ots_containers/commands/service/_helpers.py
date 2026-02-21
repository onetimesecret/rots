# src/ots_containers/commands/service/_helpers.py

"""Helper functions for service command operations."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from .packages import ServicePackage

if TYPE_CHECKING:
    from ots_shared.ssh.executor import Executor, Result


def _get_executor(executor: Executor | None = None) -> Executor:
    """Return the given executor or a default LocalExecutor."""
    if executor is not None:
        return executor
    from ots_shared.ssh.executor import LocalExecutor

    return LocalExecutor()


def ensure_instances_dir(pkg: ServicePackage) -> Path:
    """Ensure the config directory (or instances subdir) exists with correct ownership.

    For packages using instances subdirectory (use_instances_subdir=True):
        Creates /etc/{pkg}/instances/
    For packages placing configs directly in config_dir (use_instances_subdir=False):
        Ensures /etc/{pkg}/ exists (typically already created by package manager)

    Args:
        pkg: Service package definition

    Returns:
        Path to the directory where instance configs will be placed
    """
    if pkg.use_instances_subdir:
        config_dir = pkg.instances_dir
    else:
        config_dir = pkg.config_dir

    if not config_dir.exists():
        config_dir.mkdir(parents=True, mode=0o755)
        # Set ownership if running as root and service user exists
        if pkg.service_user:
            try:
                shutil.chown(config_dir, user=pkg.service_user, group=pkg.service_group)
            except (LookupError, PermissionError):
                pass  # User doesn't exist or not root
    return config_dir


def copy_default_config(pkg: ServicePackage, instance: str) -> Path:
    """Copy default config to instance-specific config file.

    Args:
        pkg: Service package definition
        instance: Instance identifier (usually port number)

    Returns:
        Path to the new config file

    Raises:
        FileNotFoundError: If default config doesn't exist
    """
    if not pkg.default_config or not pkg.default_config.exists():
        raise FileNotFoundError(
            f"Default config not found: {pkg.default_config}. Is {pkg.name} package installed?"
        )

    ensure_instances_dir(pkg)
    dest = pkg.config_file(instance)

    if dest.exists():
        raise FileExistsError(f"Config already exists: {dest}")

    shutil.copy2(pkg.default_config, dest)
    dest.chmod(0o644)

    if pkg.service_user:
        try:
            shutil.chown(dest, user=pkg.service_user, group=pkg.service_group)
        except (LookupError, PermissionError):
            pass

    return dest


def update_config_value(
    config_path: Path,
    key: str,
    value: str,
    pkg: ServicePackage,
) -> None:
    """Update or add a config value in a service config file.

    Args:
        config_path: Path to config file
        key: Config key to set
        value: Value to set
        pkg: Service package (for format info)
    """
    content = config_path.read_text()
    lines = content.splitlines()

    # Determine separator based on config format
    sep = " " if pkg.config_format == "space" else "="
    new_line = f"{key}{sep}{value}"

    # Find and replace existing key, or add to end
    found = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        # Skip comments and empty lines
        if not stripped or stripped.startswith(pkg.comment_prefix):
            continue
        # Check if this line sets our key
        parts = stripped.replace("=", " ").split()
        if parts and parts[0] == key:
            lines[i] = new_line
            found = True
            break

    if not found:
        lines.append(new_line)

    config_path.write_text("\n".join(lines) + "\n")


def create_secrets_file(
    pkg: ServicePackage,
    instance: str,
    secrets: dict[str, str] | None = None,
) -> Path | None:
    """Create a secrets file for an instance.

    Args:
        pkg: Service package definition
        instance: Instance identifier
        secrets: Dict of secret key -> value pairs

    Returns:
        Path to secrets file, or None if package doesn't use separate secrets
    """
    if not pkg.secrets or not pkg.secrets.secrets_file_pattern:
        return None

    ensure_instances_dir(pkg)
    secrets_path = pkg.secrets_file(instance)
    if secrets_path is None:
        return None

    # Create secrets file with restrictive permissions
    sep = " " if pkg.config_format == "space" else "="
    lines = [f"# Secrets for {pkg.name} instance {instance}"]
    lines.append(f"# Mode: {oct(pkg.secrets.secrets_file_mode)}")
    lines.append("")

    if secrets:
        for key, value in secrets.items():
            lines.append(f"{key}{sep}{value}")

    secrets_path.write_text("\n".join(lines) + "\n")
    secrets_path.chmod(pkg.secrets.secrets_file_mode)

    if pkg.secrets.secrets_owned_by_service and pkg.service_user:
        try:
            shutil.chown(secrets_path, user=pkg.service_user, group=pkg.service_group)
        except (LookupError, PermissionError):
            pass

    return secrets_path


def add_secrets_include(
    config_path: Path,
    secrets_path: Path,
    pkg: ServicePackage,
) -> None:
    """Add include directive for secrets file to main config.

    Args:
        config_path: Path to main config file
        secrets_path: Path to secrets file
        pkg: Service package definition
    """
    if not pkg.secrets or not pkg.secrets.include_directive:
        return

    include_line = pkg.secrets.include_directive.format(secrets_path=secrets_path)
    content = config_path.read_text()

    # Check if include already exists
    if include_line in content:
        return

    # Add include at the end of the file
    if not content.endswith("\n"):
        content += "\n"
    content += f"\n# Include secrets file\n{include_line}\n"
    config_path.write_text(content)


def ensure_data_dir(pkg: ServicePackage, instance: str) -> Path:
    """Ensure instance data directory exists with correct ownership.

    Args:
        pkg: Service package definition
        instance: Instance identifier

    Returns:
        Path to the data directory
    """
    data_path = pkg.data_path(instance)
    if not data_path.exists():
        data_path.mkdir(parents=True, mode=0o750)

    if pkg.service_user:
        try:
            shutil.chown(data_path, user=pkg.service_user, group=pkg.service_group)
        except (LookupError, PermissionError):
            pass

    return data_path


def systemctl_json(
    *args: str,
    executor: Executor | None = None,
) -> dict | list | None:
    """Run systemctl with JSON output (systemd 255+ on Debian 13).

    Args:
        *args: Arguments to pass to systemctl
        executor: Executor for command dispatch. None uses LocalExecutor.

    Returns:
        Parsed JSON output, or None if command failed
    """
    import json

    from ots_shared.ssh.executor import CommandError

    ex = _get_executor(executor)
    try:
        result = ex.run(
            ["systemctl", "--output=json", *args],
            timeout=10,
            check=True,
        )
        if result.stdout.strip():
            return json.loads(result.stdout)
        return None
    except (CommandError, json.JSONDecodeError):
        return None


def systemctl(
    *args: str,
    check: bool = True,
    executor: Executor | None = None,
) -> Result | subprocess.CompletedProcess:
    """Run a systemctl command.

    Args:
        *args: Arguments to pass to systemctl
        check: Whether to raise on non-zero exit
        executor: Executor for command dispatch. None uses subprocess directly
            for backward compatibility.

    Returns:
        Result (with executor) or CompletedProcess (without)
    """
    if executor is not None:
        return executor.run(
            ["systemctl", *args],
            timeout=30,
            check=check,
        )
    return subprocess.run(
        ["systemctl", *args],
        capture_output=True,
        text=True,
        check=check,
        timeout=30,
    )


def is_service_active(unit: str, *, executor: Executor | None = None) -> bool:
    """Check if a systemd unit is active."""
    result = systemctl("is-active", unit, check=False, executor=executor)
    return result.returncode == 0


def is_service_enabled(unit: str, *, executor: Executor | None = None) -> bool:
    """Check if a systemd unit is enabled."""
    result = systemctl("is-enabled", unit, check=False, executor=executor)
    return result.returncode == 0


def check_default_service_conflict(
    pkg: ServicePackage,
    *,
    executor: Executor | None = None,
) -> bool:
    """Check if the default (non-template) service is running.

    When using template instances, the default service should be stopped and disabled
    to avoid port conflicts and configuration confusion.

    Args:
        pkg: Service package definition
        executor: Executor for command dispatch. None uses LocalExecutor.

    Returns:
        True if there's a conflict (default service is active), False otherwise
    """
    if not pkg.default_service:
        return False

    if is_service_active(pkg.default_service, executor=executor):
        print(f"WARNING: Default service {pkg.default_service} is running!")
        print(f"  This may conflict with template instances ({pkg.template_unit})")
        print("  To use multiple instances, stop and disable the default service:")
        print(f"    sudo systemctl stop {pkg.default_service}")
        print(f"    sudo systemctl disable {pkg.default_service}")
        print()
        return True

    return False
