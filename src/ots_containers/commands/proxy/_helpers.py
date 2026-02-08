# src/ots_containers/commands/proxy/_helpers.py

"""Helper functions for proxy commands.

IMPORTANT: These functions use HOST environment variables via envsubst,
NOT container .env files. This separation is intentional to keep
reverse proxy configuration independent from container runtime config.
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path


class ProxyError(Exception):
    """Error during proxy configuration."""


def render_template(template_path: Path) -> str:
    """Render template using envsubst with HOST environment.

    Args:
        template_path: Path to the template file.

    Returns:
        Rendered content as string.

    Raises:
        ProxyError: If envsubst fails or template not found.

    Note:
        envsubst inherits the parent process environment, which means
        it uses the HOST's shell variables - not any container .env files.
        This is the intended behavior for reverse proxy configuration.
    """
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


def validate_caddy_config(content: str) -> None:
    """Validate Caddy configuration syntax.

    Args:
        content: Caddyfile content to validate.

    Raises:
        ProxyError: If validation fails.
    """
    with tempfile.NamedTemporaryFile(mode="w", suffix=".Caddyfile", delete=False) as f:
        f.write(content)
        temp_path = f.name

    try:
        result = subprocess.run(
            ["caddy", "validate", "--config", temp_path, "--adapter", "caddyfile"],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise ProxyError(f"Caddy validation failed:\n{result.stderr}")
    except FileNotFoundError as e:
        raise ProxyError("caddy not found in PATH") from e
    finally:
        Path(temp_path).unlink(missing_ok=True)


def reload_caddy() -> None:
    """Reload Caddy service via systemctl.

    Raises:
        ProxyError: If reload fails.
    """
    try:
        subprocess.run(
            ["sudo", "systemctl", "reload", "caddy"],
            check=True,
        )
    except subprocess.CalledProcessError as e:
        raise ProxyError(f"Failed to reload caddy: {e}") from e
