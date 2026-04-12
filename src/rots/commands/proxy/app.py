# src/rots/commands/proxy/app.py

"""Proxy management commands for OTS containers.

These commands manage the reverse proxy (Caddy) configuration using HOST
environment variables via envsubst. This is intentionally separate from
container .env files to avoid mixing host and container configurations.

All commands support remote execution via the global ``--host`` flag.
"""

from __future__ import annotations

import contextlib
import json
import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import cyclopts

from rots import context
from rots.config import Config

if TYPE_CHECKING:
    from ots_shared.ssh import Executor

from ..common import DryRun, JsonOutput
from ._helpers import (
    ProbeResult,
    ProxyError,
    adapt_to_json,
    collect_local_files,
    evaluate_assertions,
    find_free_port,
    find_template_in_dir,
    parse_trace_url,
    patch_caddy_json,
    push_files_to_remote,
    reload_caddy,
    render_template,
    run_caddy,
    run_echo_server,
    run_probe,
    validate_caddy_config,
)

logger = logging.getLogger(__name__)

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

        # Validate before writing — pass source_dir so relative imports resolve
        from ots_shared.ssh import is_remote

        source_dir = tpl.parent
        validate_caddy_config(rendered, executor=ex, source_dir=source_dir)

        # Write to output path

        if is_remote(ex):
            ex.run(["mkdir", "-p", str(out.parent)], timeout=15)
            result = ex.run(["tee", str(out)], input=rendered, timeout=15)
            if not result.ok:
                raise ProxyError(f"Failed to write {out}: {result.stderr}")
        else:
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(rendered)

        logger.info(f"[ok] Rendered {tpl} -> {out}")

    except ProxyError as e:
        raise SystemExit(str(e)) from e


@app.command
def push(
    source: Annotated[
        Path,
        cyclopts.Parameter(
            help="Local file or directory to push to the remote host",
        ),
    ],
    output: Output = None,
    dry_run: DryRun = False,
    remote_dir: Annotated[
        Path | None,
        cyclopts.Parameter(
            name="--remote-dir",
            help="Override remote destination directory",
        ),
    ] = None,
    template: Annotated[
        str | None,
        cyclopts.Parameter(
            name="--template",
            help="Template file within directory to render (auto-detected from *.template)",
        ),
    ] = None,
    no_render: Annotated[
        bool,
        cyclopts.Parameter(
            name="--no-render",
            negative=[],
            help="Skip render/validate/reload after pushing",
        ),
    ] = False,
) -> None:
    """Push a local file or directory, render template, and reload Caddy.

    When *source* is a single file, pushes it to the remote template path,
    renders with envsubst, validates, and reloads Caddy.

    When *source* is a directory, pushes all files (recursively, skipping
    hidden files) to the remote destination, then optionally renders a
    ``*.template`` file found within and reloads Caddy.

    Requires --host (pushing to localhost is not useful).

    Examples:
        ots --host eu-web-01 proxy push Caddyfile.template
        ots --host eu-web-01 proxy push caddy/ --remote-dir /etc/onetimesecret/
        ots --host eu-web-01 proxy push caddy/ --template Caddyfile.template
        ots --host eu-web-01 proxy push caddy/ --no-render
    """
    from ots_shared.ssh import is_remote

    cfg = Config()
    ex = cfg.get_executor(host=context.host_var.get(None))

    if not is_remote(ex):
        raise SystemExit("proxy push requires a remote host. Use --host to specify one.")

    if not source.exists():
        raise SystemExit(f"Local source not found: {source}")

    try:
        if source.is_dir():
            _push_directory(
                source,
                cfg=cfg,
                executor=ex,
                output=output,
                dry_run=dry_run,
                remote_dir=remote_dir,
                template_name=template,
                no_render=no_render,
            )
        else:
            _push_file(source, cfg=cfg, executor=ex, output=output, dry_run=dry_run)
    except ProxyError as e:
        raise SystemExit(str(e)) from e


def _push_file(
    source: Path,
    *,
    cfg: Config,
    executor: Executor,
    output: Path | None,
    dry_run: bool,
) -> None:
    """Push a single template file, render, validate, and reload."""
    tpl_dest = cfg.proxy_template
    out = output or cfg.proxy_config
    content = source.read_text()

    if dry_run:
        logger.info(f"Would push: {source} -> {tpl_dest}")
        logger.info(f"Would render: {tpl_dest} -> {out}")
        logger.info("Would reload Caddy")
        return

    # Step 1: Push template to remote
    result = executor.run(["mkdir", "-p", str(tpl_dest.parent)], timeout=15)
    result = executor.run(["tee", str(tpl_dest)], input=content, timeout=15)
    if not result.ok:
        raise ProxyError(f"Failed to write {tpl_dest}: {result.stderr}")
    logger.info(f"[ok] Pushed {source} -> {tpl_dest}")

    # Step 2: Render template on remote
    rendered = render_template(tpl_dest, executor=executor)
    validate_caddy_config(rendered, executor=executor, source_dir=tpl_dest.parent)
    result = executor.run(["mkdir", "-p", str(out.parent)], timeout=15)
    result = executor.run(["tee", str(out)], input=rendered, timeout=15)
    if not result.ok:
        raise ProxyError(f"Failed to write {out}: {result.stderr}")
    logger.info(f"[ok] Rendered {tpl_dest} -> {out}")

    # Step 3: Reload Caddy
    reload_caddy(executor=executor)
    logger.info("[ok] Caddy reloaded")


def _push_directory(
    source: Path,
    *,
    cfg: Config,
    executor: Executor,
    output: Path | None,
    dry_run: bool,
    remote_dir: Path | None,
    template_name: str | None,
    no_render: bool,
) -> None:
    """Push a directory of files, optionally render and reload."""
    dest = remote_dir or cfg.proxy_template.parent
    out = output or cfg.proxy_config

    files = collect_local_files(source)
    if not files:
        raise ProxyError(f"No files found in {source}")

    # Determine template file once for both dry_run and real execution
    tpl_name: str | None = None
    if not no_render:
        if template_name:
            tpl_name = template_name
        else:
            found = find_template_in_dir(source)
            if found:
                tpl_name = found.name

    if dry_run:
        logger.info(f"Would push {len(files)} file(s) to {dest}:")
        push_files_to_remote(source, dest, executor=executor, dry_run=True)
        if tpl_name:
            logger.info(f"Would render: {dest / tpl_name} -> {out}")
            logger.info("Would reload Caddy")
        elif not no_render:
            logger.info("No template found; skipping render/reload")
        return

    # Push all files
    logger.info(f"Pushing {len(files)} file(s) to {dest}:")
    push_files_to_remote(source, dest, executor=executor)
    logger.info(f"[ok] Pushed {len(files)} file(s)")

    if not tpl_name:
        if not no_render:
            logger.info("No template found; skipping render/reload")
        return

    tpl_path = dest / tpl_name

    # Render, validate, reload
    rendered = render_template(tpl_path, executor=executor)
    validate_caddy_config(rendered, executor=executor, source_dir=dest)
    result = executor.run(["mkdir", "-p", str(out.parent)], timeout=15)
    result = executor.run(["tee", str(out)], input=rendered, timeout=15)
    if not result.ok:
        raise ProxyError(f"Failed to write {out}: {result.stderr}")
    logger.info(f"[ok] Rendered {tpl_path} -> {out}")

    reload_caddy(executor=executor)
    logger.info("[ok] Caddy reloaded")


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
        logger.info("[ok] Caddy reloaded")
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
        logger.info(f"[ok] {file_path} is valid")

    except ProxyError as e:
        logger.error(f"Validation failed for {file_path}")
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
        logger.info("[ok] Configs are equivalent")
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
            if received and resp_body and logger.isEnabledFor(logging.DEBUG):
                try:
                    echo_data = json.loads(resp_body)
                    print(f"\necho: {json.dumps(echo_data, indent=2)}")
                except json.JSONDecodeError:
                    print(f"\necho: {resp_body}")

    except ProxyError as e:
        raise SystemExit(str(e)) from e


@app.command
def probe(
    url: Annotated[str, cyclopts.Parameter(help="URL to probe")],
    resolve: Annotated[
        str | None,
        cyclopts.Parameter(name="--resolve", help="DNS override: host:port:addr"),
    ] = None,
    connect_to: Annotated[
        str | None,
        cyclopts.Parameter(name="--connect-to", help="Reroute: host:port:host2:port2"),
    ] = None,
    cacert: Annotated[
        Path | None,
        cyclopts.Parameter(name="--cacert", help="CA cert for verification"),
    ] = None,
    cert_status: Annotated[
        bool,
        cyclopts.Parameter(name="--cert-status", help="Check OCSP stapling"),
    ] = False,
    method: Annotated[
        str | None,
        cyclopts.Parameter(name="--method", help="HTTP method (e.g., HEAD, OPTIONS)"),
    ] = None,
    insecure: Annotated[
        bool,
        cyclopts.Parameter(name="--insecure", help="Skip TLS verification (-k)"),
    ] = False,
    follow_redirects: Annotated[
        bool,
        cyclopts.Parameter(name="--follow", help="Follow redirects (-L)"),
    ] = False,
    header: Annotated[
        tuple[str, ...],
        cyclopts.Parameter(name="--header", help="Extra header (repeatable)"),
    ] = (),
    expect_status: Annotated[
        int | None,
        cyclopts.Parameter(name="--expect-status", help="Assert HTTP status"),
    ] = None,
    expect_header: Annotated[
        tuple[str, ...],
        cyclopts.Parameter(name="--expect-header", help="Assert header (repeatable)"),
    ] = (),
    expect_cert_days: Annotated[
        int | None,
        cyclopts.Parameter(
            name="--expect-cert-days-remaining", help="Assert minimum days until cert expiry"
        ),
    ] = None,
    json_output: JsonOutput = False,
    retries: Annotated[
        int,
        cyclopts.Parameter(name="--retries", help="Number of retry attempts (0 = no retries)"),
    ] = 0,
    retry_delay: Annotated[
        float,
        cyclopts.Parameter(name="--retry-delay", help="Seconds between retries"),
    ] = 1.0,
) -> None:
    """Probe a live URL with curl and report TLS, headers, and timing.

    Verifies deployed behaviour — does the live endpoint return the right
    TLS cert, security headers, and status code?

    Supports DNS-independent staging tests via --resolve and --connect-to.
    When assertions (--expect-status, --expect-header) are provided, exits
    non-zero on failure for CI use.  Use --retries to wait for a service to
    become ready (retries both connection errors and assertion failures).

    Examples:
        rots proxy probe https://us.onetime.co/api/v2/status
        rots proxy probe https://us.onetime.co/api/v2/status --expect-status 200
        rots proxy probe https://us.onetime.co/api/v2/status \\
            --resolve us.onetime.co:443:10.0.0.5
        rots proxy probe https://us.onetime.co/api/v2/status \\
            --expect-status 200 --retries 5 --retry-delay 2.0
        rots --host eu-web-01 proxy probe https://localhost:7043/health
    """
    cfg = Config()
    ex = cfg.get_executor(host=context.host_var.get(None))

    try:
        parse_trace_url(url)
    except ProxyError as e:
        raise SystemExit(str(e)) from e

    last_result: ProbeResult | None = None
    last_assertions: list[dict] = []

    for attempt in range(retries + 1):
        try:
            last_result = run_probe(
                url,
                resolve=resolve,
                connect_to=connect_to,
                cacert=cacert,
                cert_status=cert_status,
                extra_headers=header,
                method=method,
                insecure=insecure,
                follow_redirects=follow_redirects,
                executor=ex,
            )
        except ProxyError as e:
            if attempt < retries:
                time.sleep(retry_delay)
                continue
            raise SystemExit(str(e)) from e

        last_assertions = evaluate_assertions(
            last_result,
            expect_status=expect_status,
            expect_headers=expect_header,
            expect_cert_days=expect_cert_days,
        )

        all_passed = not last_assertions or all(a["passed"] for a in last_assertions)
        if all_passed or attempt == retries:
            break

        time.sleep(retry_delay)

    assert last_result is not None  # loop always sets or raises

    if json_output:
        _print_probe_json(last_result, last_assertions)
    else:
        _print_probe_human(last_result, last_assertions)

    # Exit code: 0 if no assertions or all pass, 1 if any fail
    if last_assertions and not all(a["passed"] for a in last_assertions):
        raise SystemExit(1)


def _print_probe_human(result: ProbeResult, assertions: list[dict]) -> None:
    """Print human-readable probe output."""
    print(result.url)

    # TLS section
    if result.url.startswith("https"):
        tag = "[ok]" if result.ssl_verify_ok else "[FAIL]"
        label = (
            "verified" if result.ssl_verify_ok else (f"verify failed ({result.ssl_verify_result})")
        )
        print(f"\n  tls: {tag} {label}")
        if result.cert_issuer:
            print(f"       issuer:  {result.cert_issuer}")
        if result.cert_expiry:
            print(f"       expiry:  {result.cert_expiry}")

    # Status
    print(f"\n  status: {result.http_code}")

    # Headers
    if result.response_headers:
        print("\n  headers:")
        for k, vs in sorted(result.response_headers.items()):
            for v in vs:
                print(f"    {k}: {v}")

    # Timing
    print("\n  timing:")
    print(f"    dns:       {result.time_namelookup * 1000:7.1f} ms")
    print(f"    connect:   {result.time_connect * 1000:7.1f} ms")
    print(f"    tls:       {result.time_appconnect * 1000:7.1f} ms")
    print(f"    ttfb:      {result.time_starttransfer * 1000:7.1f} ms")
    print(f"    total:     {result.time_total * 1000:7.1f} ms")

    # Assertions
    if assertions:
        print()
        for a in assertions:
            tag = "[ok]" if a["passed"] else "[FAIL]"
            if a["passed"]:
                print(f"  {tag} {a['check']} {a['expected']}")
            else:
                print(f"  {tag} {a['check']}: expected {a['expected']}, got {a['actual']}")


def _print_probe_json(result: ProbeResult, assertions: list[dict]) -> None:
    """Print JSON probe output."""
    output = {
        "url": result.url,
        "http_code": result.http_code,
        "tls": {
            "verified": result.ssl_verify_ok,
            "verify_result": result.ssl_verify_result,
            "issuer": result.cert_issuer,
            "subject": result.cert_subject,
            "expiry": result.cert_expiry,
        },
        "timing": {
            "dns_ms": round(result.time_namelookup * 1000, 1),
            "connect_ms": round(result.time_connect * 1000, 1),
            "tls_ms": round(result.time_appconnect * 1000, 1),
            "ttfb_ms": round(result.time_starttransfer * 1000, 1),
            "total_ms": round(result.time_total * 1000, 1),
        },
        "headers": result.response_headers,
        "assertions": assertions,
    }
    print(json.dumps(output, indent=2))
