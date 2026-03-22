# src/rots/sidecar/handlers_rots.py

"""Generic rots CLI invocation handler.

Allows the sidecar to execute any rots subcommand by translating
dotted command paths to CLI arguments:

    rots.env.process → rots env process
    rots.proxy.reload → rots proxy reload
    rots.instance.redeploy → rots instance redeploy <ports>

Security: Only invokes the rots CLI binary, never a shell. Arguments
are passed as a list to subprocess, preventing injection.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys
from typing import Any

logger = logging.getLogger(__name__)

# Subcommands that are safe to invoke via sidecar.
# These are operations that make sense for remote/automated control.
ALLOWED_SUBCOMMANDS: set[tuple[str, ...]] = {
    # Environment management
    ("env", "process"),
    ("env", "verify"),
    ("env", "show"),
    # Proxy management
    ("proxy", "reload"),
    ("proxy", "status"),
    ("proxy", "validate"),
    # Instance lifecycle
    ("instance", "start"),
    ("instance", "stop"),
    ("instance", "restart"),
    ("instance", "redeploy"),
    ("instance", "status"),
    ("instance", "logs"),
    ("instance", "list"),
    # Image management
    ("image", "pull"),
    ("image", "current"),
    ("image", "rollback"),
    # Service management
    ("service", "start"),
    ("service", "stop"),
    ("service", "restart"),
    ("service", "status"),
    # Assets
    ("assets", "sync"),
    # Init/setup
    ("init",),
    # Diagnostics
    ("doctor",),
    ("ps",),
    ("version",),
    # Self-management
    ("self", "upgrade"),
    ("self", "check"),
}

# Subcommands that should NEVER be allowed (destructive or inappropriate)
BLOCKED_SUBCOMMANDS: set[tuple[str, ...]] = {
    # Host commands push/pull files - should be explicit operator action
    ("host", "push"),
    ("host", "pull"),
    # Cloud-init generation - infrastructure concern
    ("cloudinit",),
    # Sidecar management - would be recursive
    ("sidecar",),
    # Env push - should be explicit operator action
    ("env", "push"),
}


def _find_rots_binary() -> str:
    """Find the rots binary path.

    Prefers the installed binary, falls back to python -m rots.
    """
    rots_path = shutil.which("rots")
    if rots_path:
        return rots_path
    # Fall back to module invocation
    return sys.executable


def _build_command(subcommand_parts: tuple[str, ...], args: list[str]) -> list[str]:
    """Build the full command list.

    Args:
        subcommand_parts: Tuple of subcommand parts (e.g., ("env", "process"))
        args: Additional CLI arguments

    Returns:
        Full command as list (e.g., ["rots", "env", "process", "--file", "..."])
    """
    rots_bin = _find_rots_binary()

    if rots_bin == sys.executable:
        # Using python -m rots
        cmd = [rots_bin, "-m", "rots"]
    else:
        cmd = [rots_bin]

    cmd.extend(subcommand_parts)
    cmd.extend(args)
    return cmd


def _is_subcommand_allowed(parts: tuple[str, ...]) -> bool:
    """Check if a subcommand is allowed.

    A subcommand is allowed if it exactly matches an entry in ALLOWED_SUBCOMMANDS.
    No prefix expansion is performed - each allowed command must be explicitly listed.

    The blocklist is checked first and uses prefix matching to block entire
    command trees (e.g., blocking ('sidecar',) blocks all sidecar subcommands).
    """
    # Check blocked list first (prefix matching for blocklist is intentional -
    # we want to block entire command trees)
    for blocked in BLOCKED_SUBCOMMANDS:
        if parts[: len(blocked)] == blocked:
            return False

    # Exact match only - no prefix expansion for allowlist
    # This prevents ('init',) from allowing ('init', 'malicious-subcommand')
    return parts in ALLOWED_SUBCOMMANDS


def invoke_rots(command_path: str, params: dict[str, Any]) -> dict[str, Any]:
    """Invoke a rots CLI command.

    Args:
        command_path: Dotted command path (e.g., "rots.env.process")
        params: Parameters dict, may contain:
            - args: List of additional CLI arguments
            - timeout: Command timeout in seconds (default: 300)
            - capture_output: Whether to capture stdout/stderr (default: True)

    Returns:
        Result dict with status, stdout, stderr, returncode
    """
    # Parse command path
    if not command_path.startswith("rots."):
        return {
            "status": "error",
            "error": f"Invalid command path: {command_path}. Must start with 'rots.'",
        }

    # Extract subcommand parts (e.g., "rots.env.process" → ("env", "process"))
    parts = command_path.split(".")[1:]  # Remove "rots" prefix
    # Filter out empty strings (handles "rots." and "rots.env." cases)
    parts = [p for p in parts if p]
    if not parts:
        return {
            "status": "error",
            "error": "Empty command path after 'rots.'",
        }

    subcommand_parts = tuple(parts)

    # Security check
    if not _is_subcommand_allowed(subcommand_parts):
        return {
            "status": "error",
            "error": f"Subcommand not allowed: {'.'.join(parts)}",
            "allowed": [".".join(s) for s in sorted(ALLOWED_SUBCOMMANDS)],
        }

    # Extract params
    extra_args = params.get("args", [])
    if not isinstance(extra_args, list):
        extra_args = [str(extra_args)]
    # Ensure all args are strings
    extra_args = [str(a) for a in extra_args]

    timeout = params.get("timeout", 300)
    capture_output = params.get("capture_output", True)

    # Build command
    cmd = _build_command(subcommand_parts, extra_args)
    logger.info("Invoking rots command: %s", " ".join(cmd))

    try:
        result = subprocess.run(
            cmd,
            capture_output=capture_output,
            text=True,
            timeout=timeout,
            check=False,  # Don't raise on non-zero exit
        )

        return {
            "status": "ok" if result.returncode == 0 else "error",
            "returncode": result.returncode,
            "stdout": result.stdout if capture_output else None,
            "stderr": result.stderr if capture_output else None,
            "command": " ".join(cmd),
        }

    except subprocess.TimeoutExpired:
        logger.error("Command timed out after %d seconds: %s", timeout, " ".join(cmd))
        return {
            "status": "error",
            "error": f"Command timed out after {timeout} seconds",
            "command": " ".join(cmd),
        }
    except Exception as e:
        logger.exception("Failed to invoke rots command: %s", " ".join(cmd))
        return {
            "status": "error",
            "error": str(e),
            "command": " ".join(cmd),
        }


def is_rots_command(command: str) -> bool:
    """Check if a command should be handled by the rots invoker."""
    return command.startswith("rots.")


def list_allowed_commands() -> list[str]:
    """Return list of allowed rots subcommand paths."""
    return ["rots." + ".".join(parts) for parts in sorted(ALLOWED_SUBCOMMANDS)]
