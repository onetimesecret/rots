# src/ots_containers/commands/proxy/app.py

"""Proxy management commands for OTS containers.

These commands manage the reverse proxy (Caddy) configuration using HOST
environment variables via envsubst. This is intentionally separate from
container .env files to avoid mixing host and container configurations.
"""

import subprocess
from pathlib import Path
from typing import Annotated

import cyclopts

from ots_containers.config import Config

from ..common import DryRun
from ._helpers import (
    ProxyError,
    reload_caddy,
    render_template,
    validate_caddy_config,
)

app = cyclopts.App(
    name="proxy",
    help="Manage reverse proxy (Caddy) configuration",
)

Template = Annotated[
    Path | None,
    cyclopts.Parameter(
        name=["--template", "-t"],
        help="Template file path (e.g. /etc/onetimesecret/Caddyfile.template)",
    ),
]

Output = Annotated[
    Path | None,
    cyclopts.Parameter(
        name=["--output", "-o"],
        help="Output file path (default: /etc/caddy/Caddyfile)",
    ),
]


@app.command
def render(
    template: Template = None,
    output: Output = None,
    dry_run: DryRun = False,
) -> None:
    """Render proxy config from template using HOST environment.

    Uses envsubst to substitute environment variables in the template.
    Validates the result with 'caddy validate' before writing.

    Note: Uses HOST environment variables, not container .env files.

    Examples:
        ots proxy render
        ots proxy render --dry-run
        ots proxy render -t /path/to/template.Caddyfile -o /etc/caddy/Caddyfile
    """
    cfg = Config()
    tpl = template or cfg.proxy_template
    out = output or cfg.proxy_config

    try:
        rendered = render_template(tpl)

        if dry_run:
            print(rendered)
            return

        # Validate before writing
        validate_caddy_config(rendered)

        # Write to output path (may need sudo)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(rendered)
        print(f"[ok] Rendered {tpl} -> {out}")

    except ProxyError as e:
        raise SystemExit(str(e)) from e


@app.command
def reload() -> None:
    """Reload the Caddy service.

    Runs 'systemctl reload caddy' to apply configuration changes.

    Examples:
        ots proxy reload
    """
    try:
        reload_caddy()
        print("[ok] Caddy reloaded")
    except ProxyError as e:
        raise SystemExit(str(e)) from e


@app.command
def status() -> None:
    """Show Caddy service status.

    Displays the current systemd status of the Caddy service.

    Examples:
        ots proxy status
    """
    try:
        result = subprocess.run(
            ["systemctl", "status", "caddy", "--no-pager"],
            capture_output=True,
            text=True,
        )
        print(result.stdout)
        if result.stderr:
            print(result.stderr)
    except Exception as e:
        raise SystemExit(f"Failed to get Caddy status: {e}") from e


@app.command
def validate(
    config_file: Annotated[
        Path | None,
        cyclopts.Parameter(
            name=["--file", "-f"],
            help="Caddyfile to validate (default: /etc/caddy/Caddyfile)",
        ),
    ] = None,
) -> None:
    """Validate Caddy configuration file.

    Runs 'caddy validate' on the specified configuration file.

    Examples:
        ots proxy validate
        ots proxy validate -f /path/to/Caddyfile
    """
    cfg = Config()
    file_path = config_file or cfg.proxy_config

    if not file_path.exists():
        raise SystemExit(f"Config file not found: {file_path}")

    try:
        result = subprocess.run(
            ["caddy", "validate", "--config", str(file_path)],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            print(f"[ok] {file_path} is valid")
            if result.stdout.strip():
                print(result.stdout)
        else:
            print(f"Validation failed for {file_path}")
            print(result.stderr)
            raise SystemExit(1)
    except FileNotFoundError as e:
        raise SystemExit("caddy not found in PATH") from e
