# src/rots/commands/proxy/_helpers.py

"""Helper functions for proxy commands.

IMPORTANT: These functions use HOST environment variables via envsubst,
NOT container .env files. This separation is intentional to keep
reverse proxy configuration independent from container runtime config.

All functions accept an optional ``executor`` parameter for remote
execution via SSH.  When None, they operate locally via subprocess.
"""

from __future__ import annotations

import contextlib
import copy
import dataclasses
import json
import socket
import subprocess
import tempfile
import threading
import time
import urllib.parse
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import TYPE_CHECKING

from ots_shared.ssh import is_remote as _is_remote

if TYPE_CHECKING:
    from collections.abc import Generator

    from ots_shared.ssh import Executor


class ProxyError(Exception):
    """Error during proxy configuration."""


@dataclasses.dataclass
class ProbeResult:
    """Result of probing a URL with curl."""

    url: str
    http_code: int
    ssl_verify_result: int  # 0 = valid chain
    ssl_verify_ok: bool
    cert_issuer: str
    cert_subject: str
    cert_expiry: str
    http_version: str
    time_namelookup: float  # seconds
    time_connect: float
    time_appconnect: float  # TLS handshake complete
    time_starttransfer: float  # TTFB
    time_total: float
    response_headers: dict[str, list[str]]
    curl_json: dict  # raw write-out for --json passthrough


def parse_trace_url(url: str) -> urllib.parse.ParseResult:
    """Normalise and validate a URL for ``proxy trace``.

    Accepts both full URLs (``https://host/path``) and bare
    ``host/path`` shorthand.  Returns the stdlib ``ParseResult``
    so callers can access ``.hostname``, ``.path``, ``.query``,
    ``.scheme``, etc.

    Raises:
        ProxyError: When the URL has no hostname after parsing.
    """
    if "://" not in url:
        url = f"https://{url}"
    parsed = urllib.parse.urlparse(url)
    if not parsed.hostname:
        raise ProxyError(f"Invalid URL (no hostname): {url}")
    return parsed


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


def validate_caddy_config(
    content: str,
    *,
    executor: Executor | None = None,
    source_dir: Path | None = None,
) -> None:
    """Validate Caddy configuration syntax.

    Args:
        content: Caddyfile content to validate.
        executor: Executor for command dispatch.
        source_dir: Directory to create the temp file in, so Caddy can
            resolve relative ``import`` paths.  When *None*, uses the
            system temp directory (imports will fail if the Caddyfile
            uses relative paths).

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

    # Local execution — write temp file into source_dir so relative imports resolve
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".Caddyfile", dir=source_dir, delete=False
    ) as f:
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


def adapt_to_json(config_path: Path, *, executor: Executor | None = None) -> str:
    """Run ``caddy adapt`` on a Caddyfile and return sorted JSON.

    Args:
        config_path: Path to the Caddyfile.
        executor: Executor for command dispatch.

    Returns:
        Sorted JSON string of the adapted config.

    Raises:
        ProxyError: If caddy adapt fails or the file is not found.
    """
    cmd = ["caddy", "adapt", "--config", str(config_path), "--adapter", "caddyfile"]

    if _is_remote(executor):
        result = executor.run(["test", "-f", str(config_path)])  # type: ignore[union-attr]
        if not result.ok:
            raise ProxyError(f"Config file not found: {config_path}")
        result = executor.run(cmd, timeout=30)  # type: ignore[union-attr]
        if not result.ok:
            raise ProxyError(f"caddy adapt failed for {config_path}:\n{result.stderr}")
        raw = result.stdout
    else:
        if not config_path.exists():
            raise ProxyError(f"Config file not found: {config_path}")
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True)
            if proc.returncode != 0:
                raise ProxyError(f"caddy adapt failed for {config_path}:\n{proc.stderr}")
            raw = proc.stdout
        except FileNotFoundError as e:
            raise ProxyError("caddy not found in PATH") from e

    import json

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ProxyError(f"caddy adapt produced invalid JSON: {e}") from e

    return json.dumps(parsed, indent=2, sort_keys=True) + "\n"


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


# ---------------------------------------------------------------------------
# Trace helpers
# ---------------------------------------------------------------------------


def find_free_port() -> int:
    """Return an available ephemeral port on 127.0.0.1.

    There is a small TOCTOU window between closing this socket and a
    consumer binding to the port — acceptable for local dev tooling.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def patch_caddy_json(
    config: dict,
    *,
    caddy_port: int,
    echo_addr: str | None = None,
) -> dict:
    """Deep-copy *config* and patch it for local trace use.

    Modifications:
    - Merges all server routes into one server on ``127.0.0.1:{caddy_port}``
    - ``automatic_https`` → ``{"disable": true}``
    - ``tls_connection_policies`` removed
    - When *echo_addr* is set, all ``reverse_proxy`` upstreams → *echo_addr*
    - When *echo_addr* is ``None`` (live mode), upstreams are left as-is
    - ``apps.tls`` removed (avoids provisioning DNS providers, etc.)
    - ``admin`` → ``{"disabled": true}``
    - Each route gets an ``X-Trace-Route`` response header identifying
      its origin server block and host matchers

    Raises:
        ProxyError: When ``apps.http.servers`` is missing or empty.
    """
    patched = copy.deepcopy(config)

    servers = patched.get("apps", {}).get("http", {}).get("servers")
    if not servers:
        raise ProxyError("No apps.http.servers in Caddy config")

    # Merge all servers into a single server to avoid "listener address
    # repeated" errors — production configs often have multiple servers
    # (Cloudflare-proxied, direct, on-demand TLS) each on :443.
    merged_routes: list[dict] = []
    for srv in servers.values():
        listen = srv.get("listen", [])
        for route in srv.get("routes", []):
            if echo_addr is not None:
                _patch_handler_upstreams(route.get("handle", []), echo_addr)
            _inject_trace_header(route, listen)
            merged_routes.append(route)

    merged = {
        "listen": [f"127.0.0.1:{caddy_port}"],
        "automatic_https": {"disable": True},
        "routes": merged_routes,
    }
    patched["apps"]["http"]["servers"] = {"srv0": merged}

    # Remove the TLS app entirely — local trace doesn't need certificates
    # and provisioning may fail (e.g. Cloudflare DNS token not set).
    patched.get("apps", {}).pop("tls", None)

    patched.setdefault("admin", {})["disabled"] = True
    return patched


def _patch_handler_upstreams(handlers: list[dict], echo_addr: str) -> None:
    """Recursively replace ``reverse_proxy`` upstreams with *echo_addr*."""
    for handler in handlers:
        if handler.get("handler") == "reverse_proxy":
            for upstream in handler.get("upstreams", []):
                upstream["dial"] = echo_addr
        # Recurse into subroutes
        for route in handler.get("routes", []):
            _patch_handler_upstreams(route.get("handle", []), echo_addr)


def _inject_trace_header(route: dict, listen: list[str]) -> None:
    """Prepend a ``headers`` handler that stamps ``X-Trace-Route``.

    The label uses the server's listen address and the route's host
    matchers to produce something recognisable from the Caddyfile,
    e.g. ``:443 us.example.com, eu.example.com`` or ``:80 *``.
    """
    hosts: list[str] = []
    for m in route.get("match", []):
        hosts.extend(m.get("host", []))

    port = listen[0] if listen else ":?"
    label = f"{port} {', '.join(hosts)}" if hosts else f"{port} *"

    marker: dict = {
        "handler": "headers",
        "response": {"set": {"X-Trace-Route": [label]}},
    }
    route.setdefault("handle", []).insert(0, marker)


def _echo_handler_class(received: list[dict]) -> type[BaseHTTPRequestHandler]:
    """Return a request handler that captures requests into *received*."""

    class EchoHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self._handle()

        def do_POST(self) -> None:
            self._handle()

        def do_PUT(self) -> None:
            self._handle()

        def do_DELETE(self) -> None:
            self._handle()

        def do_PATCH(self) -> None:
            self._handle()

        def do_HEAD(self) -> None:
            self._handle()

        def do_OPTIONS(self) -> None:
            self._handle()

        def _handle(self) -> None:
            entry = {
                "method": self.command,
                "path": self.path,
                "headers": {k: v for k, v in self.headers.items()},
            }
            received.append(entry)
            body = json.dumps(entry).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            pass  # suppress stderr

    return EchoHandler


@contextlib.contextmanager
def run_echo_server(port: int) -> Generator[tuple[str, list[dict]], None, None]:
    """Start an HTTP echo server on *port* in a daemon thread.

    Yields ``(addr, received)`` where *addr* is ``"127.0.0.1:{port}"``
    and *received* accumulates captured request dicts.
    """
    received: list[dict] = []
    server = HTTPServer(("127.0.0.1", port), _echo_handler_class(received))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"127.0.0.1:{port}", received
    finally:
        server.shutdown()


@contextlib.contextmanager
def run_caddy(config: dict, port: int) -> Generator[subprocess.Popen, None, None]:  # type: ignore[type-arg]
    """Start ``caddy run`` with a patched JSON config on *port*.

    Writes *config* to a temporary file, launches Caddy, and polls
    until it accepts connections (5 s timeout).  Cleans up on exit.

    Raises:
        ProxyError: When caddy fails to start or accept connections.
    """
    tmp = tempfile.NamedTemporaryFile(  # noqa: SIM115
        mode="w",
        suffix=".json",
        delete=False,
    )
    try:
        json.dump(config, tmp)
        tmp.close()

        # Caddy redirects its logger to stdout and dumps the full
        # config as debug JSON — often hundreds of KB.  Piping either
        # stream fills the OS pipe buffer (64 KB) and stalls Caddy.
        # Write stderr to a temp file so we can read it on failure.
        stderr_tmp = tempfile.NamedTemporaryFile(  # noqa: SIM115
            mode="w",
            suffix=".caddy-stderr",
            delete=False,
        )
        stderr_path = Path(stderr_tmp.name)
        stderr_fd = stderr_tmp

        proc = subprocess.Popen(
            ["caddy", "run", "--config", tmp.name],
            stdout=subprocess.DEVNULL,
            stderr=stderr_fd,
        )
        stderr_fd.close()

        def _read_stderr() -> str:
            return stderr_path.read_text(errors="replace").strip()

        # Wait for Caddy to accept connections.  Connection-refused
        # returns instantly (not after the timeout), so we sleep
        # between attempts to give Caddy time to start.
        deadline = 50  # 50 × 0.1 s = 5 s
        for _ in range(deadline):
            if proc.poll() is not None:
                raise ProxyError(f"Caddy exited early:\n{_read_stderr()}")
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                    break
            except OSError:
                time.sleep(0.1)
        else:
            proc.terminate()
            proc.wait(timeout=3)
            msg = f"Caddy did not accept connections on :{port} within 5 s"
            stderr_text = _read_stderr()
            if stderr_text:
                msg += f"\n{stderr_text}"
            raise ProxyError(msg)

        yield proc
    except FileNotFoundError as e:
        raise ProxyError("caddy not found in PATH") from e
    finally:
        Path(tmp.name).unlink(missing_ok=True)
        if "stderr_path" in locals():
            stderr_path.unlink(missing_ok=True)
        if "proc" in locals():
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=3)


# ---------------------------------------------------------------------------
# Probe helpers
# ---------------------------------------------------------------------------

_CURL_SENTINEL = "%%CURL_JSON%%"


def build_curl_args(
    url: str,
    *,
    resolve: str | None = None,
    connect_to: str | None = None,
    cacert: Path | None = None,
    cert_status: bool = False,
    extra_headers: tuple[str, ...] = (),
    timeout: int = 30,
    method: str | None = None,
    insecure: bool = False,
    follow_redirects: bool = False,
) -> list[str]:
    """Build the curl command list for probing *url*.

    Returns the full argv list without executing anything — purely
    testable by asserting on the returned list.
    """
    cmd = [
        "curl",
        "-sS",
        "-o",
        "/dev/null",
        "-D",
        "-",
        "-w",
        f"\n{_CURL_SENTINEL}\n%{{json}}",
        "--max-time",
        str(timeout),
    ]

    if resolve is not None:
        cmd.extend(["--resolve", resolve])
    if connect_to is not None:
        cmd.extend(["--connect-to", connect_to])
    if cacert is not None:
        cmd.extend(["--cacert", str(cacert)])
    if cert_status:
        cmd.append("--cert-status")
    for h in extra_headers:
        cmd.extend(["-H", h])
    if method is not None:
        cmd.extend(["-X", method])
    if insecure:
        cmd.append("-k")
    if follow_redirects:
        cmd.append("-L")

    cmd.append("--")
    cmd.append(url)
    return cmd


def parse_curl_output(stdout: str) -> ProbeResult:
    """Parse combined curl output (``-D -`` headers + ``-w '%{json}'``).

    The output is split on the ``%%CURL_JSON%%`` sentinel.  The first
    part contains HTTP response headers; the second is the JSON blob
    from curl's ``--write-out '%{json}'``.

    Raises:
        ProxyError: When the sentinel is missing or JSON is malformed.
    """
    if _CURL_SENTINEL not in stdout:
        raise ProxyError("curl output missing sentinel — unexpected format")

    header_section, json_section = stdout.split(_CURL_SENTINEL, 1)

    # When curl follows redirects (-L), -D - emits multiple header blocks
    # (one per hop) separated by blank lines.  Only parse the final block
    # so assertions and output reflect the terminal response.
    normalized = header_section.replace("\r\n", "\n")
    header_blocks = [b for b in normalized.split("\n\n") if b.strip()]
    final_block = header_blocks[-1] if header_blocks else ""

    response_headers: dict[str, list[str]] = {}
    for line in final_block.splitlines():
        if ":" in line and not line.startswith("HTTP/"):
            key, _, value = line.partition(":")
            key = key.strip()
            response_headers.setdefault(key, []).append(value.strip())

    try:
        curl_json = json.loads(json_section.strip())
    except (json.JSONDecodeError, ValueError) as e:
        raise ProxyError(f"curl JSON output malformed: {e}") from e

    # Extract cert details from the certs string
    certs_str = curl_json.get("certs", "")
    cert_issuer = ""
    cert_subject = ""
    cert_expiry = ""
    for cert_line in certs_str.splitlines():
        stripped = cert_line.strip()
        if stripped.startswith("Issuer:") and not cert_issuer:
            cert_issuer = stripped[len("Issuer:") :].strip()
        elif stripped.startswith("Subject:") and not cert_subject:
            cert_subject = stripped[len("Subject:") :].strip()
        elif stripped.startswith("Expire date:") and not cert_expiry:
            cert_expiry = stripped[len("Expire date:") :].strip()

    ssl_verify = curl_json.get("ssl_verify_result", -1)
    return ProbeResult(
        url=curl_json.get("url_effective", curl_json.get("url", "")),
        http_code=curl_json.get("http_code", 0),
        ssl_verify_result=ssl_verify,
        ssl_verify_ok=ssl_verify == 0,
        cert_issuer=cert_issuer,
        cert_subject=cert_subject,
        cert_expiry=cert_expiry,
        http_version=curl_json.get("http_version", ""),
        time_namelookup=curl_json.get("time_namelookup", 0.0),
        time_connect=curl_json.get("time_connect", 0.0),
        time_appconnect=curl_json.get("time_appconnect", 0.0),
        time_starttransfer=curl_json.get("time_starttransfer", 0.0),
        time_total=curl_json.get("time_total", 0.0),
        response_headers=response_headers,
        curl_json=curl_json,
    )


def _parse_cert_expiry_days(cert_expiry: str) -> int | None:
    """Parse cert expiry string and return days remaining.

    The format comes from curl's ``%{json}`` output, e.g.,
    ``"Aug 17 23:59:59 2026 GMT"``.

    Returns None on empty string or parse failure.
    """
    if not cert_expiry:
        return None
    try:
        expiry = datetime.strptime(cert_expiry, "%b %d %H:%M:%S %Y %Z")
        expiry = expiry.replace(tzinfo=UTC)
        now = datetime.now(UTC)
        return (expiry - now).days
    except (ValueError, OverflowError):
        return None


def evaluate_assertions(
    result: ProbeResult,
    *,
    expect_status: int | None = None,
    expect_headers: tuple[str, ...] = (),
    expect_cert_days: int | None = None,
) -> list[dict]:
    """Evaluate assertions against a probe result.

    Returns a list of ``{"check": str, "passed": bool, "expected": str,
    "actual": str}`` dicts.  Returns an empty list when no assertions
    are specified.
    """
    checks: list[dict] = []

    if expect_status is not None:
        checks.append(
            {
                "check": "status",
                "passed": result.http_code == expect_status,
                "expected": str(expect_status),
                "actual": str(result.http_code),
            }
        )

    # Build a case-insensitive lookup of response headers
    lower_headers = {k.lower(): (k, vs) for k, vs in result.response_headers.items()}

    for header_spec in expect_headers:
        key, _, expected_value = header_spec.partition(":")
        key = key.strip()
        expected_value = expected_value.strip()

        orig_key, actual_values = lower_headers.get(key.lower(), (key, []))
        checks.append(
            {
                "check": f"header {key}",
                "passed": expected_value in actual_values,
                "expected": f"{key}: {expected_value}",
                "actual": (
                    f"{orig_key}: {', '.join(actual_values)}" if actual_values else "(missing)"
                ),
            }
        )

    if expect_cert_days is not None:
        days = _parse_cert_expiry_days(result.cert_expiry)
        if days is None:
            checks.append(
                {
                    "check": "cert-expiry",
                    "passed": False,
                    "expected": f">= {expect_cert_days} days",
                    "actual": "(no expiry date available)",
                }
            )
        else:
            checks.append(
                {
                    "check": "cert-expiry",
                    "passed": days >= expect_cert_days,
                    "expected": f">= {expect_cert_days} days",
                    "actual": f"{days} days",
                }
            )

    return checks


def run_probe(
    url: str,
    *,
    resolve: str | None = None,
    connect_to: str | None = None,
    cacert: Path | None = None,
    cert_status: bool = False,
    extra_headers: tuple[str, ...] = (),
    timeout: int = 30,
    method: str | None = None,
    insecure: bool = False,
    follow_redirects: bool = False,
    executor: Executor | None = None,
) -> ProbeResult:
    """Execute curl and return parsed probe results.

    Uses *executor* when provided (remote execution via SSH), otherwise
    runs curl locally via subprocess.

    Raises:
        ProxyError: On curl errors (not found, timeout, non-zero exit).
    """
    cmd = build_curl_args(
        url,
        resolve=resolve,
        connect_to=connect_to,
        cacert=cacert,
        cert_status=cert_status,
        extra_headers=extra_headers,
        timeout=timeout,
        method=method,
        insecure=insecure,
        follow_redirects=follow_redirects,
    )

    # Give subprocess a bit more than curl's --max-time to avoid racing
    subprocess_timeout = timeout + 5

    if _is_remote(executor):
        result = executor.run(cmd, timeout=subprocess_timeout)  # type: ignore[union-attr]
        if not result.ok:
            raise ProxyError(f"curl failed (exit {result.returncode}): {result.stderr}")
        return parse_curl_output(result.stdout)

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=subprocess_timeout,
        )
    except FileNotFoundError as e:
        raise ProxyError("curl not found in PATH") from e
    except subprocess.TimeoutExpired as e:
        raise ProxyError("curl timed out") from e

    if proc.returncode != 0:
        raise ProxyError(f"curl failed (exit {proc.returncode}): {proc.stderr}")

    return parse_curl_output(proc.stdout)
