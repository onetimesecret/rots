# src/ots_containers/commands/service/_helpers.py

"""Helper functions for service command operations."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from ots_shared.ssh import is_remote as _is_remote

from .packages import ServicePackage

if TYPE_CHECKING:
    from ots_shared.ssh.executor import Executor, Result


def _get_executor(executor: Executor | None = None) -> Executor:
    """Return the given executor or a default LocalExecutor."""
    if executor is not None:
        return executor
    from ots_shared.ssh.executor import LocalExecutor

    return LocalExecutor()


# ---------------------------------------------------------------------------
# Remote-aware file operation primitives
# ---------------------------------------------------------------------------


def _file_exists(path: Path, executor: Executor | None) -> bool:
    """Check if a file exists (local or remote)."""
    if _is_remote(executor):
        result = executor.run(["test", "-f", str(path)])  # type: ignore[union-attr]
        return result.ok
    return path.exists()


def _dir_exists(path: Path, executor: Executor | None) -> bool:
    """Check if a directory exists (local or remote)."""
    if _is_remote(executor):
        result = executor.run(["test", "-d", str(path)])  # type: ignore[union-attr]
        return result.ok
    return path.is_dir()


def _read_text(path: Path, executor: Executor | None) -> str:
    """Read text content from a file (local or remote)."""
    if _is_remote(executor):
        result = executor.run(["cat", str(path)], timeout=10)  # type: ignore[union-attr]
        return result.stdout
    return path.read_text()


def _write_text(path: Path, content: str, executor: Executor | None) -> None:
    """Write text content to a file (local or remote)."""
    if _is_remote(executor):
        executor.run(["tee", str(path)], input=content, timeout=10)  # type: ignore[union-attr]
        return
    path.write_text(content)


def _mkdir_p(path: Path, mode: int, executor: Executor | None) -> None:
    """Create directory with parents (local or remote)."""
    if _is_remote(executor):
        executor.run(["mkdir", "-p", "-m", f"{mode:o}", str(path)])  # type: ignore[union-attr]
        return
    path.mkdir(parents=True, mode=mode, exist_ok=True)


def _copy_file(src: Path, dest: Path, executor: Executor | None) -> None:
    """Copy a file preserving attributes (local or remote)."""
    if _is_remote(executor):
        executor.run(["cp", "-p", str(src), str(dest)])  # type: ignore[union-attr]
        return
    shutil.copy2(src, dest)


def _chmod(path: Path, mode: int, executor: Executor | None) -> None:
    """Set file permissions (local or remote)."""
    if _is_remote(executor):
        executor.run(["chmod", f"{mode:o}", str(path)])  # type: ignore[union-attr]
        return
    path.chmod(mode)


def _chown(
    path: Path,
    user: str | None,
    group: str | None,
    executor: Executor | None,
) -> None:
    """Set file ownership (local or remote). Best-effort — silences errors."""
    if not user:
        return
    owner = f"{user}:{group}" if group else user
    if _is_remote(executor):
        executor.run(["chown", owner, str(path)])  # type: ignore[union-attr]
        return
    try:
        shutil.chown(path, user=user, group=group)
    except (LookupError, PermissionError):
        pass


def _unlink(path: Path, executor: Executor | None) -> None:
    """Remove a file (local or remote)."""
    if _is_remote(executor):
        executor.run(["rm", "-f", str(path)])  # type: ignore[union-attr]
        return
    import os

    os.unlink(path)


# ---------------------------------------------------------------------------
# Service helpers
# ---------------------------------------------------------------------------


def ensure_instances_dir(
    pkg: ServicePackage,
    *,
    executor: Executor | None = None,
) -> Path:
    """Ensure the config directory (or instances subdir) exists with correct ownership.

    For packages using instances subdirectory (use_instances_subdir=True):
        Creates /etc/{pkg}/instances/
    For packages placing configs directly in config_dir (use_instances_subdir=False):
        Ensures /etc/{pkg}/ exists (typically already created by package manager)

    Args:
        pkg: Service package definition
        executor: Executor for command dispatch. None uses local filesystem.

    Returns:
        Path to the directory where instance configs will be placed
    """
    if pkg.use_instances_subdir:
        config_dir = pkg.instances_dir
    else:
        config_dir = pkg.config_dir

    if not _dir_exists(config_dir, executor):
        _mkdir_p(config_dir, 0o755, executor)
        _chown(config_dir, pkg.service_user, pkg.service_group, executor)
    return config_dir


def copy_default_config(
    pkg: ServicePackage,
    instance: str,
    *,
    executor: Executor | None = None,
) -> Path:
    """Copy default config to instance-specific config file.

    Args:
        pkg: Service package definition
        instance: Instance identifier (usually port number)
        executor: Executor for command dispatch. None uses local filesystem.

    Returns:
        Path to the new config file

    Raises:
        FileNotFoundError: If default config doesn't exist
        FileExistsError: If instance config already exists
    """
    if not pkg.default_config or not _file_exists(pkg.default_config, executor):
        raise FileNotFoundError(
            f"Default config not found: {pkg.default_config}. Is {pkg.name} package installed?"
        )

    ensure_instances_dir(pkg, executor=executor)
    dest = pkg.config_file(instance)

    if _file_exists(dest, executor):
        raise FileExistsError(f"Config already exists: {dest}")

    _copy_file(pkg.default_config, dest, executor)
    _chmod(dest, 0o644, executor)
    _chown(dest, pkg.service_user, pkg.service_group, executor)

    return dest


def update_config_value(
    config_path: Path,
    key: str,
    value: str,
    pkg: ServicePackage,
    *,
    executor: Executor | None = None,
) -> None:
    """Update or add a config value in a service config file.

    Args:
        config_path: Path to config file
        key: Config key to set
        value: Value to set
        pkg: Service package (for format info)
        executor: Executor for command dispatch. None uses local filesystem.
    """
    content = _read_text(config_path, executor)
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

    _write_text(config_path, "\n".join(lines) + "\n", executor)


def create_secrets_file(
    pkg: ServicePackage,
    instance: str,
    secrets: dict[str, str] | None = None,
    *,
    executor: Executor | None = None,
) -> Path | None:
    """Create a secrets file for an instance.

    Args:
        pkg: Service package definition
        instance: Instance identifier
        secrets: Dict of secret key -> value pairs
        executor: Executor for command dispatch. None uses local filesystem.

    Returns:
        Path to secrets file, or None if package doesn't use separate secrets
    """
    if not pkg.secrets or not pkg.secrets.secrets_file_pattern:
        return None

    ensure_instances_dir(pkg, executor=executor)
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

    _write_text(secrets_path, "\n".join(lines) + "\n", executor)
    _chmod(secrets_path, pkg.secrets.secrets_file_mode, executor)

    if pkg.secrets.secrets_owned_by_service:
        _chown(secrets_path, pkg.service_user, pkg.service_group, executor)

    return secrets_path


def add_secrets_include(
    config_path: Path,
    secrets_path: Path,
    pkg: ServicePackage,
    *,
    executor: Executor | None = None,
) -> None:
    """Add include directive for secrets file to main config.

    Args:
        config_path: Path to main config file
        secrets_path: Path to secrets file
        pkg: Service package definition
        executor: Executor for command dispatch. None uses local filesystem.
    """
    if not pkg.secrets or not pkg.secrets.include_directive:
        return

    include_line = pkg.secrets.include_directive.format(secrets_path=secrets_path)
    content = _read_text(config_path, executor)

    # Check if include already exists
    if include_line in content:
        return

    # Add include at the end of the file
    if not content.endswith("\n"):
        content += "\n"
    content += f"\n# Include secrets file\n{include_line}\n"
    _write_text(config_path, content, executor)


def ensure_data_dir(
    pkg: ServicePackage,
    instance: str,
    *,
    executor: Executor | None = None,
) -> Path:
    """Ensure instance data directory exists with correct ownership.

    Args:
        pkg: Service package definition
        instance: Instance identifier
        executor: Executor for command dispatch. None uses local filesystem.

    Returns:
        Path to the data directory
    """
    data_path = pkg.data_path(instance)
    if not _dir_exists(data_path, executor):
        _mkdir_p(data_path, 0o750, executor)

    _chown(data_path, pkg.service_user, pkg.service_group, executor)

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
) -> Result:
    """Run a systemctl command.

    Args:
        *args: Arguments to pass to systemctl
        check: Whether to raise on non-zero exit
        executor: Executor for command dispatch. When None, uses a
            LocalExecutor so the return type is always Result.

    Returns:
        Result from the executor.
    """
    ex = _get_executor(executor)
    return ex.run(
        ["systemctl", *args],
        timeout=30,
        check=check,
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
