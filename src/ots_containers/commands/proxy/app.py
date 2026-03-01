# src/ots_containers/commands/proxy/app.py

"""Proxy management commands for OTS containers.

These commands manage the reverse proxy (Caddy) configuration using HOST
environment variables via envsubst. This is intentionally separate from
container .env files to avoid mixing host and container configurations.

All commands support remote execution via the global ``--host`` flag.
"""

from pathlib import Path
from typing import Annotated

import cyclopts

from ots_containers import context
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
    On remote hosts, envsubst uses the remote host's environment.

    Examples:
        ots proxy render
        ots proxy render --dry-run
        ots --host eu-web-01 proxy render
    """
    cfg = Config()
    ex = cfg.get_executor(host=context.host_var.get(None))
    tpl = template or cfg.proxy_template
    out = output or cfg.proxy_config

    try:
        rendered = render_template(tpl, executor=ex)

        if dry_run:
            print(rendered)
            return

        # Validate before writing
        validate_caddy_config(rendered, executor=ex)

        # Write to output path
        from ots_shared.ssh import is_remote

        if is_remote(ex):
            ex.run(["mkdir", "-p", str(out.parent)], timeout=15)
            result = ex.run(["tee", str(out)], input=rendered, timeout=15)
            if not result.ok:
                raise ProxyError(f"Failed to write {out}: {result.stderr}")
        else:
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(rendered)

        print(f"[ok] Rendered {tpl} -> {out}")

    except ProxyError as e:
        raise SystemExit(str(e)) from e


@app.command
def push(
    template_file: Annotated[
        Path,
        cyclopts.Parameter(
            help="Local Caddyfile.template to push to the remote host",
        ),
    ],
    output: Output = None,
    dry_run: DryRun = False,
) -> None:
    """Push a local Caddyfile.template, render it, and reload Caddy.

    Combines three steps into one:
      1. Push local template to remote /etc/onetimesecret/Caddyfile.template
      2. Render with envsubst using HOST environment
      3. Reload Caddy to apply

    Requires --host (pushing to localhost is not useful).

    Examples:
        ots --host eu-web-01 proxy push Caddyfile.template
        ots --host eu-web-01 proxy push Caddyfile.template --dry-run
    """
    from ots_shared.ssh import is_remote

    cfg = Config()
    ex = cfg.get_executor(host=context.host_var.get(None))

    if not is_remote(ex):
        raise SystemExit("proxy push requires a remote host. Use --host to specify one.")

    if not template_file.exists():
        raise SystemExit(f"Local template not found: {template_file}")

    tpl_dest = cfg.proxy_template
    out = output or cfg.proxy_config

    try:
        content = template_file.read_text()

        if dry_run:
            print(f"Would push: {template_file} -> {tpl_dest}")
            print(f"Would render: {tpl_dest} -> {out}")
            print("Would reload Caddy")
            return

        # Step 1: Push template to remote
        result = ex.run(["mkdir", "-p", str(tpl_dest.parent)], timeout=15)
        result = ex.run(["tee", str(tpl_dest)], input=content, timeout=15)
        if not result.ok:
            raise ProxyError(f"Failed to write {tpl_dest}: {result.stderr}")
        print(f"[ok] Pushed {template_file} -> {tpl_dest}")

        # Step 2: Render template on remote
        rendered = render_template(tpl_dest, executor=ex)
        validate_caddy_config(rendered, executor=ex)
        result = ex.run(["mkdir", "-p", str(out.parent)], timeout=15)
        result = ex.run(["tee", str(out)], input=rendered, timeout=15)
        if not result.ok:
            raise ProxyError(f"Failed to write {out}: {result.stderr}")
        print(f"[ok] Rendered {tpl_dest} -> {out}")

        # Step 3: Reload Caddy
        reload_caddy(executor=ex)
        print("[ok] Caddy reloaded")

    except ProxyError as e:
        raise SystemExit(str(e)) from e


@app.command
def reload() -> None:
    """Reload the Caddy service.

    Runs 'systemctl reload caddy' to apply configuration changes.

    Examples:
        ots proxy reload
        ots --host eu-web-01 proxy reload
    """
    cfg = Config()
    ex = cfg.get_executor(host=context.host_var.get(None))
    try:
        reload_caddy(executor=ex)
        print("[ok] Caddy reloaded")
    except ProxyError as e:
        raise SystemExit(str(e)) from e


@app.command
def status() -> None:
    """Show Caddy service status.

    Displays the current systemd status of the Caddy service.

    Examples:
        ots proxy status
        ots --host eu-web-01 proxy status
    """
    cfg = Config()
    ex = cfg.get_executor(host=context.host_var.get(None))
    try:
        result = ex.run(
            ["systemctl", "status", "caddy", "--no-pager"],
            timeout=15,
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
        ots --host eu-web-01 proxy validate
    """
    cfg = Config()
    ex = cfg.get_executor(host=context.host_var.get(None))
    file_path = config_file or cfg.proxy_config

    from ots_shared.ssh import is_remote

    # Check file existence
    if is_remote(ex):
        result = ex.run(["test", "-f", str(file_path)])
        if not result.ok:
            raise SystemExit(f"Config file not found: {file_path}")
    else:
        if not file_path.exists():
            raise SystemExit(f"Config file not found: {file_path}")

    try:
        # Read the file content, then validate via the helper
        if is_remote(ex):
            result = ex.run(["cat", str(file_path)])
            if not result.ok:
                raise SystemExit(f"Failed to read {file_path}: {result.stderr}")
            content = result.stdout
        else:
            content = file_path.read_text()

        validate_caddy_config(content, executor=ex)
        print(f"[ok] {file_path} is valid")

    except ProxyError as e:
        print(f"Validation failed for {file_path}")
        raise SystemExit(str(e)) from e
    except FileNotFoundError as e:
        raise SystemExit("caddy not found in PATH") from e
