# src/rots/sidecar/handlers_config.py

"""Staged configuration handlers for safe config updates.

Implements a staged configuration workflow:
1. config.stage - Write changes to a .staged file (validated against allowlist)
2. config.apply - Backup current config, apply staged changes, test with rolling restart
3. config.discard - Remove staged file without applying
4. config.get - Read current configuration values

The workflow ensures:
- Only allowlisted keys can be modified
- Changes are staged before being applied
- Atomic file operations prevent corruption
- Automatic backup enables rollback
- Health checks validate the new configuration
"""

from __future__ import annotations

import logging
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from rots.environment_file import EnvFile

from .allowlist import validate_config_update
from .commands import Command, CommandResult, register_handler

logger = logging.getLogger(__name__)

# Default paths
DEFAULT_ENV_PATH = Path("/etc/onetimesecret/.env")
STAGED_SUFFIX = ".staged"
BACKUP_SUFFIX = ".backup"

# Rolling restart settings
DEFAULT_RESTART_DELAY = 5
DEFAULT_HEALTH_TIMEOUT = 60


def _get_env_path(params: dict[str, Any]) -> Path:
    """Get the env file path from params or default."""
    path_str = params.get("env_path")
    if path_str:
        return Path(path_str)
    return DEFAULT_ENV_PATH


def _get_staged_path(env_path: Path) -> Path:
    """Get the staged file path for a given env file."""
    return env_path.with_suffix(env_path.suffix + STAGED_SUFFIX)


def _get_backup_path(env_path: Path) -> Path:
    """Get the backup file path with timestamp."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return env_path.with_suffix(f"{env_path.suffix}.{timestamp}{BACKUP_SUFFIX}")


@register_handler(Command.CONFIG_STAGE)
def handle_config_stage(params: dict[str, Any]) -> CommandResult:
    """Stage configuration changes for later application.

    Changes are written to a .staged file and validated against the allowlist.
    Multiple calls to config.stage will merge changes, with later values
    overwriting earlier ones.

    Params:
        updates (required): Dict of key=value pairs to stage
        env_path (optional): Path to env file (default: /etc/onetimesecret/.env)

    Returns:
        CommandResult with staged keys and any rejected keys
    """
    updates = params.get("updates")
    if not updates or not isinstance(updates, dict):
        return CommandResult.fail("Missing or invalid 'updates' parameter (expected dict)")

    env_path = _get_env_path(params)
    staged_path = _get_staged_path(env_path)

    # Validate updates against allowlist
    valid_updates, rejected_keys = validate_config_update(updates)

    if not valid_updates:
        return CommandResult.fail(
            f"No valid updates to stage. Rejected keys: {', '.join(rejected_keys)}"
        )

    # Load existing staged changes or start fresh
    if staged_path.exists():
        staged = EnvFile.parse(staged_path)
    else:
        staged = EnvFile(path=staged_path, entries=[], _variables={})

    # Merge new changes
    for key, value in valid_updates.items():
        staged.set(key, value)

    # Write staged file
    try:
        staged.write()
    except OSError as e:
        return CommandResult.fail(f"Failed to write staged file: {e}")

    result_data = {
        "staged_keys": list(valid_updates.keys()),
        "staged_path": str(staged_path),
        "total_staged": len(list(staged.iter_variables())),
    }

    if rejected_keys:
        result = CommandResult.ok(result_data)
        result.warnings.append(f"Rejected keys (not in allowlist): {', '.join(rejected_keys)}")
        return result

    return CommandResult.ok(result_data)


@register_handler(Command.CONFIG_APPLY)
def handle_config_apply(params: dict[str, Any]) -> CommandResult:
    """Apply staged configuration changes.

    This operation:
    1. Creates a timestamped backup of the current config
    2. Merges staged changes into the current config
    3. Writes the new config atomically
    4. Triggers a rolling restart to apply changes
    5. Monitors health after restart

    Params:
        env_path (optional): Path to env file (default: /etc/onetimesecret/.env)
        skip_restart (optional): If True, don't restart instances (default: False)
        restart_delay (optional): Seconds between instance restarts (default: 5)
        health_timeout (optional): Seconds to wait for health (default: 60)

    Returns:
        CommandResult with backup path and restart status
    """
    env_path = _get_env_path(params)
    staged_path = _get_staged_path(env_path)
    skip_restart = params.get("skip_restart", False)
    restart_delay = params.get("restart_delay", DEFAULT_RESTART_DELAY)
    health_timeout = params.get("health_timeout", DEFAULT_HEALTH_TIMEOUT)

    # Verify staged file exists
    if not staged_path.exists():
        return CommandResult.fail("No staged configuration to apply")

    # Verify env file exists
    if not env_path.exists():
        return CommandResult.fail(f"Config file not found: {env_path}")

    # Load both files
    try:
        staged = EnvFile.parse(staged_path)
        current = EnvFile.parse(env_path)
    except Exception as e:
        return CommandResult.fail(f"Failed to parse config files: {e}")

    staged_vars = list(staged.iter_variables())
    if not staged_vars:
        return CommandResult.fail("Staged file contains no changes")

    # Create backup
    backup_path = _get_backup_path(env_path)
    try:
        shutil.copy2(env_path, backup_path)
        logger.info("Created backup: %s", backup_path)
    except OSError as e:
        return CommandResult.fail(f"Failed to create backup: {e}")

    # Merge staged changes into current config
    applied_keys: list[str] = []
    for key, value in staged_vars:
        current.set(key, value)
        applied_keys.append(key)

    # Write atomically (write to temp, then rename)
    temp_path = env_path.with_suffix(env_path.suffix + ".tmp")
    try:
        current.write(temp_path)
        temp_path.rename(env_path)
        logger.info("Applied config changes: %s", applied_keys)
    except OSError as e:
        # Attempt to restore backup
        try:
            shutil.copy2(backup_path, env_path)
        except OSError:
            pass
        return CommandResult.fail(f"Failed to write config: {e}")

    # Remove staged file
    try:
        staged_path.unlink()
    except OSError as e:
        logger.warning("Failed to remove staged file: %s", e)

    result_data: dict[str, Any] = {
        "applied_keys": applied_keys,
        "backup_path": str(backup_path),
    }

    # Rolling restart if requested
    if not skip_restart:
        logger.info("Starting rolling restart after config apply")
        restart_result = _rolling_restart_after_config(restart_delay, health_timeout)
        result_data["restart"] = restart_result

        if not restart_result.get("success", False):
            result = CommandResult(
                success=True,  # Config was applied successfully
                data=result_data,
                error=None,
                warnings=[f"Config applied but restart had issues: {restart_result.get('error')}"],
            )
            return result

    return CommandResult.ok(result_data)


def _rolling_restart_after_config(delay: int, health_timeout: int) -> dict[str, Any]:
    """Perform rolling restart after config changes."""
    # Import here to avoid circular import
    from .handlers_rolling import handle_instances_restart_all

    result = handle_instances_restart_all(
        {
            "delay": delay,
            "health_timeout": health_timeout,
            "stop_on_failure": False,  # Continue even if some fail
        }
    )

    return {
        "success": result.success,
        "data": result.data,
        "error": result.error,
        "warnings": result.warnings,
    }


@register_handler(Command.CONFIG_DISCARD)
def handle_config_discard(params: dict[str, Any]) -> CommandResult:
    """Discard staged configuration changes.

    Removes the .staged file without applying any changes.

    Params:
        env_path (optional): Path to env file (default: /etc/onetimesecret/.env)

    Returns:
        CommandResult indicating whether staged file was removed
    """
    env_path = _get_env_path(params)
    staged_path = _get_staged_path(env_path)

    if not staged_path.exists():
        return CommandResult.ok(
            {
                "message": "No staged configuration to discard",
                "staged_path": str(staged_path),
            }
        )

    # Read what was staged before removing
    try:
        staged = EnvFile.parse(staged_path)
        discarded_keys = [key for key, _ in staged.iter_variables()]
    except Exception:
        discarded_keys = []

    try:
        staged_path.unlink()
    except OSError as e:
        return CommandResult.fail(f"Failed to remove staged file: {e}")

    return CommandResult.ok(
        {
            "message": "Staged configuration discarded",
            "staged_path": str(staged_path),
            "discarded_keys": discarded_keys,
        }
    )


@register_handler(Command.CONFIG_GET)
def handle_config_get(params: dict[str, Any]) -> CommandResult:
    """Read current configuration values.

    Can read specific keys or all keys. Does not return values for
    secret keys (those in SECRET_VARIABLE_NAMES).

    Params:
        keys (optional): List of keys to read. If empty, returns all non-secret keys.
        env_path (optional): Path to env file (default: /etc/onetimesecret/.env)
        include_staged (optional): If True, also show staged changes (default: False)

    Returns:
        CommandResult with configuration values
    """
    env_path = _get_env_path(params)
    requested_keys = params.get("keys", [])
    include_staged = params.get("include_staged", False)

    if not env_path.exists():
        return CommandResult.fail(f"Config file not found: {env_path}")

    try:
        env_file = EnvFile.parse(env_path)
    except Exception as e:
        return CommandResult.fail(f"Failed to parse config file: {e}")

    # Get secret variable names to mask
    secret_names = set(env_file.secret_variable_names)

    # Collect values
    config: dict[str, str | None] = {}

    if requested_keys:
        # Return only requested keys
        for key in requested_keys:
            if key in secret_names:
                config[key] = "<secret>"
            elif env_file.has(key):
                config[key] = env_file.get(key)
            else:
                config[key] = None
    else:
        # Return all non-secret keys
        for key, value in env_file.iter_variables():
            if key in secret_names:
                continue  # Skip secrets entirely in full listing
            config[key] = value

    result_data: dict[str, Any] = {
        "config": config,
        "env_path": str(env_path),
    }

    # Include staged changes if requested
    if include_staged:
        staged_path = _get_staged_path(env_path)
        if staged_path.exists():
            try:
                staged = EnvFile.parse(staged_path)
                staged_config: dict[str, str] = {}
                for key, value in staged.iter_variables():
                    if key in secret_names:
                        staged_config[key] = "<secret>"
                    else:
                        staged_config[key] = value
                result_data["staged"] = staged_config
            except Exception as e:
                result_data["staged_error"] = str(e)
        else:
            result_data["staged"] = None

    return CommandResult.ok(result_data)
