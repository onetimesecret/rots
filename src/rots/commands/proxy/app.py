# src/rots/commands/proxy/app.py

"""Proxy management commands for OTS containers.

These commands manage the reverse proxy (Caddy) configuration using HOST
environment variables via envsubst. This is intentionally separate from
container .env files to avoid mixing host and container configurations.

All commands support remote execution via the global ``--host`` flag.
"""

import contextlib
import logging
from pathlib import Path
from typing import Annotated

import cyclopts

from rots import context
from rots.config import Config

from ..common import DryRun
from ._helpers import (
    ProxyError,
    adapt_to_json,
    find_free_port,
    parse_trace_url,
    patch_caddy_json,
    reload_caddy,
    render_template,
    run_caddy,
    run_echo_server,
    validate_caddy_config,
)

log = logging.getLogger(__name__)

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
            source_dir = None
        else:
            content = file_path.read_text()
            source_dir = file_path.parent

        validate_caddy_config(content, executor=ex, source_dir=source_dir)
        print(f"[ok] {file_path} is valid")

    except ProxyError as e:
        print(f"Validation failed for {file_path}")
        raise SystemExit(str(e)) from e
    except FileNotFoundError as e:
        raise SystemExit("caddy not found in PATH") from e


@app.command
def diff(
    old: Annotated[
        Path,
        cyclopts.Parameter(help="Original Caddyfile (e.g. monolithic config)"),
    ],
    new: Annotated[
        Path,
        cyclopts.Parameter(help="New Caddyfile (e.g. snippet-based config)"),
    ],
) -> None:
    """Diff two Caddyfiles by their adapted JSON representation.

    Runs 'caddy adapt' on both files, sorts the resulting JSON, and
    prints a unified diff.  Useful for verifying that a refactored
    Caddyfile (e.g. snippet-based) produces the same effective config
    as the original monolith.

    Exit codes:
      0  configs are equivalent
      1  configs differ (or an error occurred)

    Examples:
        rots proxy diff /etc/caddy/Caddyfile.old /etc/caddy/Caddyfile
        rots --host eu-web-01 proxy diff /tmp/monolith.conf /tmp/snippets.conf
    """
    import difflib

    cfg = Config()
    ex = cfg.get_executor(host=context.host_var.get(None))

    try:
        old_json = adapt_to_json(old, executor=ex)
        new_json = adapt_to_json(new, executor=ex)
    except ProxyError as e:
        raise SystemExit(str(e)) from e

    if old_json == new_json:
        print("[ok] Configs are equivalent")
        return

    result = difflib.unified_diff(
        old_json.splitlines(keepends=True),
        new_json.splitlines(keepends=True),
        fromfile=str(old),
        tofile=str(new),
    )
    print("".join(result), end="")
    raise SystemExit(1)


@app.command
def trace(
    config_file: Annotated[
        Path,
        cyclopts.Parameter(help="Caddyfile to test"),
    ],
    url: Annotated[
        str,
        cyclopts.Parameter(
            help="URL to request (e.g. https://us.onetime.co/api/v2/status or us.onetime.co/path)"
        ),
    ],
    header: Annotated[
        tuple[str, ...],
        cyclopts.Parameter(
            name="--header",
            help="Extra request header (repeatable, curl-style 'Key: Value')",
        ),
    ] = (),
    caddy_port: Annotated[
        int | None,
        cyclopts.Parameter(help="Override Caddy listen port (default: ephemeral)"),
    ] = None,
    echo_port: Annotated[
        int | None,
        cyclopts.Parameter(help="Override echo server port (default: ephemeral)"),
    ] = None,
    render: Annotated[
        bool,
        cyclopts.Parameter(
            name="--render",
            help="Run envsubst on the Caddyfile before tracing (like 'rots proxy render')",
        ),
    ] = False,
    live: Annotated[
        bool,
        cyclopts.Parameter(
            name="--live",
            help="Forward to real upstream instead of echo server",
        ),
    ] = False,
) -> None:
    """Smoke-test a Caddyfile against a local echo server.

    Starts a real Caddy process and a lightweight echo backend, sends a
    request through Caddy, and prints exactly what the client received
    and what the upstream (Puma) would have seen.

    With ``--live``, skips the echo server and forwards requests to the
    real upstream backends (as defined in the Caddyfile).  The response
    section shows actual application output; no upstream section is
    printed since there is no echo server to capture it.

    Use ``--render`` when the Caddyfile contains ``$ENV_VAR`` placeholders
    that need envsubst expansion before Caddy can parse it.

    Local only — rejects --host.

    Examples:
        rots proxy trace Caddyfile https://us.onetime.co/api/v2/status
        rots proxy trace Caddyfile us.onetime.co/.env
        rots proxy trace --live Caddyfile https://us.onetime.co/api/v2/status
        rots proxy trace --render Caddyfile.template https://us.onetime.co/api/v2/status
        rots proxy trace Caddyfile https://us.onetime.co/api/v2/secret/conceal \\
            --header "Origin: https://onetimesecret.com"
    """
    import json
    import tempfile
    import urllib.error
    import urllib.request

    from ots_shared.ssh import is_remote

    cfg = Config()
    ex = cfg.get_executor(host=context.host_var.get(None))

    if is_remote(ex):
        raise SystemExit("proxy trace is local-only. Do not use --host.")

    if not config_file.exists():
        raise SystemExit(f"Config file not found: {config_file}")

    try:
        parsed = parse_trace_url(url)
    except ProxyError as e:
        raise SystemExit(str(e)) from e
    assert parsed.hostname  # guaranteed by parse_trace_url

    request_path = parsed.path or "/"
    if parsed.query:
        request_path = f"{request_path}?{parsed.query}"

    # Optionally render env vars via envsubst, then adapt to JSON.
    # The temp file lives in the same directory as the original so
    # Caddy can resolve relative import paths.
    try:
        if render:
            rendered = render_template(config_file, executor=ex)
            with tempfile.NamedTemporaryFile(
                mode="w",
                suffix=".Caddyfile",
                dir=config_file.parent,
                delete=False,
            ) as f:
                f.write(rendered)
                adapt_path = Path(f.name)
            try:
                raw_json = adapt_to_json(adapt_path, executor=ex)
            finally:
                adapt_path.unlink(missing_ok=True)
        else:
            raw_json = adapt_to_json(config_file, executor=ex)
        caddy_config = json.loads(raw_json)
    except ProxyError as e:
        raise SystemExit(str(e)) from e

    c_port = caddy_port or find_free_port()

    if live:
        # Live mode: forward to real upstream, no echo server
        echo_addr = None
    else:
        e_port = echo_port or find_free_port()
        echo_addr = f"127.0.0.1:{e_port}"

    try:
        patched = patch_caddy_json(caddy_config, caddy_port=c_port, echo_addr=echo_addr)
    except ProxyError as e:
        raise SystemExit(str(e)) from e

    try:
        with contextlib.ExitStack() as stack:
            received: list[dict] = []
            if not live:
                _, received = stack.enter_context(run_echo_server(e_port))
            proc = stack.enter_context(run_caddy(patched, c_port))

            # Discard health-check probes that arrived during startup
            received.clear()

            if live:
                print(f"caddy pid={proc.pid} -> live upstream\n")
            else:
                print(f"caddy pid={proc.pid} on 127.0.0.1:{c_port}\n")

            # Build the request
            req_url = f"http://127.0.0.1:{c_port}{request_path}"
            req = urllib.request.Request(req_url)
            req.add_header("Host", parsed.hostname)
            for h in header:
                key, _, value = h.partition(":")
                if key and value:
                    req.add_header(key.strip(), value.strip())

            try:
                resp = urllib.request.urlopen(req)  # noqa: S310
                status_code = resp.status
                resp_headers = dict(resp.headers)
                resp_body = resp.read().decode(errors="replace")
            except urllib.error.HTTPError as e:
                status_code = e.code
                resp_headers = dict(e.headers)
                resp_body = e.read().decode(errors="replace")

            # Output
            print(f"{parsed.hostname}{request_path}")

            if not live and received:
                print("\nforwarded request:")
                up = received[0]
                print(f"  {up['method']} {up['path']}")
                skip_up = {"content-length", "content-type", "accept-encoding", "user-agent"}
                for k, v in sorted(up["headers"].items()):
                    if k.lower() not in skip_up:
                        print(f"  {k}: {v}")

            print(f"\nresponse: {status_code}")
            # Filter noisy headers
            skip = {"server", "date", "content-length", "content-type", "transfer-encoding"}
            for k, v in sorted(resp_headers.items()):
                if k.lower() not in skip:
                    print(f"  {k}: {v}")

            if live:
                if resp_body:
                    print(f"\nbody: {resp_body[:500]}")
            elif not received:
                if resp_body:
                    print(f"\nblocked: {status_code}")
                    print(f"  body: {resp_body[:500]}")
                else:
                    print(f"\nblocked: {status_code} (no body)")

            # Show the echo body that round-tripped through Caddy
            if received and resp_body and log.isEnabledFor(logging.DEBUG):
                try:
                    echo_data = json.loads(resp_body)
                    print(f"\necho: {json.dumps(echo_data, indent=2)}")
                except json.JSONDecodeError:
                    print(f"\necho: {resp_body}")

    except ProxyError as e:
        raise SystemExit(str(e)) from e
