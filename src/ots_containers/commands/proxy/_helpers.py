# src/ots_containers/commands/proxy/_helpers.py

"""Helper functions for proxy commands.

IMPORTANT: These functions use HOST environment variables via envsubst,
NOT container .env files. This separation is intentional to keep
reverse proxy configuration independent from container runtime config.

All functions accept an optional ``executor`` parameter for remote
execution via SSH.  When None, they operate locally via subprocess.
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from ots_shared.ssh import is_remote as _is_remote

if TYPE_CHECKING:
    from ots_shared.ssh import Executor


class ProxyError(Exception):
    """Error during proxy configuration."""


def render_template(template_path: Path, *, executor: Executor | None = None) -> str:
    """Render template using envsubst with HOST environment.

    Args:
        template_path: Path to the template file.
        executor: Executor for command dispatch.

    Returns:
        Rendered content as string.

    Raises:
        ProxyError: If envsubst fails or template not found.

    Note:
        On remote hosts, envsubst uses the remote host's environment
        variables — not the local operator's. This is intentional:
        proxy config needs HOST-specific values (domain, ports, etc.).
    """
    if _is_remote(executor):
        # Read template from remote filesystem
        result = executor.run(["test", "-f", str(template_path)])  # type: ignore[union-attr]
        if not result.ok:
            raise ProxyError(f"Template not found: {template_path}")
        result = executor.run(["cat", str(template_path)])  # type: ignore[union-attr]
        if not result.ok:
            raise ProxyError(f"Failed to read template: {result.stderr}")
        template_content = result.stdout
        # Pipe through envsubst on the remote host
        result = executor.run(["envsubst"], input=template_content, timeout=30)  # type: ignore[union-attr]
        if not result.ok:
            raise ProxyError(f"envsubst failed: {result.stderr}")
        return result.stdout

    # Local execution
    if not template_path.exists():
        raise ProxyError(f"Template not found: {template_path}")

    try:
        with template_path.open() as f:
            result = subprocess.run(
                ["envsubst"],
                stdin=f,
                capture_output=True,
                text=True,
                check=True,
            )
        return result.stdout
    except subprocess.CalledProcessError as e:
        raise ProxyError(f"envsubst failed: {e.stderr}") from e
    except FileNotFoundError as e:
        raise ProxyError("envsubst not found - install gettext package") from e


def validate_caddy_config(content: str, *, executor: Executor | None = None) -> None:
    """Validate Caddy configuration syntax.

    Args:
        content: Caddyfile content to validate.
        executor: Executor for command dispatch.

    Raises:
        ProxyError: If validation fails.
    """
    if _is_remote(executor):
        # Create unique temp file on remote host (CWE-377: avoid predictable paths)
        mktemp_result = executor.run(  # type: ignore[union-attr]
            ["mktemp", "/tmp/ots-caddy-validate.XXXXXXXXXX"],
            timeout=10,
        )
        if not mktemp_result.ok:
            raise ProxyError(f"Failed to create temp file on remote: {mktemp_result.stderr}")
        tmp_remote = mktemp_result.stdout.strip()
        if not tmp_remote:
            raise ProxyError("mktemp returned empty path")

        result = executor.run(["tee", tmp_remote], input=content)  # type: ignore[union-attr]
        if not result.ok:
            raise ProxyError(f"Failed to write temp file on remote: {result.stderr}")
        try:
            result = executor.run(  # type: ignore[union-attr]
                ["caddy", "validate", "--config", tmp_remote, "--adapter", "caddyfile"],
                timeout=30,
            )
            if not result.ok:
                raise ProxyError(f"Caddy validation failed:\n{result.stderr}")
        finally:
            executor.run(["rm", "-f", tmp_remote], timeout=10)  # type: ignore[union-attr]
        return

    # Local execution
    with tempfile.NamedTemporaryFile(mode="w", suffix=".Caddyfile", delete=False) as f:
        f.write(content)
        temp_path = f.name

    try:
        result = subprocess.run(
            [
                "caddy",
                "validate",
                "--config",
                temp_path,
                "--adapter",
                "caddyfile",
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise ProxyError(f"Caddy validation failed:\n{result.stderr}")
    except FileNotFoundError as e:
        raise ProxyError("caddy not found in PATH") from e
    finally:
        Path(temp_path).unlink(missing_ok=True)


def reload_caddy(*, executor: Executor | None = None) -> None:
    """Reload Caddy service via systemctl.

    Args:
        executor: Executor for command dispatch.

    Raises:
        ProxyError: If reload fails.
    """
    if _is_remote(executor):
        from ots_shared.ssh.executor import CommandError

        try:
            executor.run(["systemctl", "reload", "caddy"], sudo=True, timeout=30, check=True)  # type: ignore[union-attr]
        except CommandError as e:
            raise ProxyError(f"Failed to reload caddy: {e}") from e
    else:
        try:
            subprocess.run(
                ["sudo", "systemctl", "reload", "caddy"],
                check=True,
            )
        except subprocess.CalledProcessError as e:
            raise ProxyError(f"Failed to reload caddy: {e}") from e
