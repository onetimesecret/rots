# src/rots/commands/sidecar/app.py

"""Sidecar daemon management commands.

The sidecar daemon provides remote control of OTS instances via RabbitMQ
and local control via Unix socket.
"""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any

import cyclopts

from ..common import DryRun, Follow, JsonOutput, Lines, Yes

if TYPE_CHECKING:
    from ots_shared.ssh.executor import Executor

logger = logging.getLogger(__name__)


def _parse_key_value_args(args: tuple[str, ...]) -> dict[str, Any]:
    """Parse key=value arguments into a dictionary.

    Repeated keys are collected into a list of values.

    Args:
        args: Tuple of "key=value" strings.

    Returns:
        Dictionary with parsed key-value pairs.
    """
    payload: dict[str, Any] = {}
    for arg in args:
        if "=" in arg:
            key, value = arg.split("=", 1)
            if key in payload:
                existing = payload[key]
                if isinstance(existing, list):
                    existing.append(value)
                else:
                    payload[key] = [existing, value]
            else:
                payload[key] = value
        else:
            print(f"Warning: Ignoring invalid argument (no =): {arg}")
    return payload


app = cyclopts.App(
    name="sidecar",
    help="Manage the OTS sidecar daemon (remote control)",
)

# Paths
SIDECAR_UNIT = "onetime-sidecar.service"
SIDECAR_SOCKET = Path("/run/onetime-sidecar.sock")
SIDECAR_UNIT_PATH = Path("/etc/systemd/system/onetime-sidecar.service")
DEFAULT_ROTS_PATH = "/usr/local/bin/rots"

# Systemd unit template (use {rots_path} placeholder)
SYSTEMD_UNIT_TEMPLATE = """\
[Unit]
Description=OneTimeSecret Sidecar Daemon
After=network.target rabbitmq-server.service
Wants=rabbitmq-server.service

[Service]
Type=simple

# Ensure required directories exist before ReadWritePaths takes effect
# (ProtectSystem=strict makes /etc read-only unless path already exists)
ExecStartPre=/usr/bin/mkdir -p /etc/onetimesecret /var/lib/onetimesecret

ExecStart={rots_path} sidecar run
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

# Security hardening
NoNewPrivileges=yes
ProtectSystem=strict
# ProtectHome=no: Required when rots is installed via pipx (~/.local/bin).
# For hardened deployments, install rots to /usr/local/bin and set ProtectHome=yes.
ProtectHome=no
PrivateTmp=yes
ReadWritePaths=/run /var/lib/onetimesecret /etc/onetimesecret

[Install]
WantedBy=multi-user.target
"""


def _resolve_rots_path(explicit_path: str | None = None) -> str:
    """Resolve the rots binary path.

    Priority: explicit flag > auto-detect via shutil.which > default fallback.

    Args:
        explicit_path: Explicitly provided path (from --rots-path flag).

    Returns:
        Resolved path to the rots binary.
    """
    if explicit_path:
        return explicit_path

    detected = shutil.which("rots")
    if detected:
        logger.debug("Auto-detected rots path: %s", detected)
        return detected

    logger.debug("Using default rots path: %s", DEFAULT_ROTS_PATH)
    return DEFAULT_ROTS_PATH


def _get_executor() -> Executor | None:
    """Resolve executor from context. Returns None for local."""
    from rots import context
    from rots.config import Config

    cfg = Config()
    host = context.host_var.get(None)
    if host is None:
        return None
    return cfg.get_executor(host=host)


def _run_systemctl(
    action: str,
    *args: str,
    executor: Executor | None = None,
    check: bool = True,
):
    """Run a systemctl command."""
    from ots_shared.ssh import LocalExecutor

    ex = executor or LocalExecutor()
    cmd = ["systemctl", action, *args]
    logger.debug("Running: %s", " ".join(cmd))
    result = ex.run(cmd, sudo=True, timeout=30)
    if check and not result.ok:
        raise RuntimeError(f"systemctl {action} failed: {result.stderr}")
    return result


@app.command
def install(
    dry_run: DryRun = False,
    force: Annotated[
        bool,
        cyclopts.Parameter(
            name=["--force", "-f"],
            help="Overwrite existing unit file",
        ),
    ] = False,
    rots_path: Annotated[
        str | None,
        cyclopts.Parameter(
            name="--rots-path",
            help="Explicit path to rots binary (default: auto-detect or /usr/local/bin/rots)",
        ),
    ] = None,
):
    """Install the sidecar systemd unit.

    Writes the systemd unit file and enables the service.
    Does not start the service automatically.

    The rots binary path in the unit file is determined by:
    1. --rots-path flag (if provided)
    2. Auto-detection via shutil.which("rots")
    3. Default fallback: /usr/local/bin/rots

    Examples:
        rots sidecar install
        rots sidecar install --force
        rots sidecar install --rots-path ~/.local/bin/rots
    """
    ex = _get_executor()

    # Resolve rots path
    resolved_rots_path = _resolve_rots_path(rots_path)
    unit_content = SYSTEMD_UNIT_TEMPLATE.format(rots_path=resolved_rots_path)

    # Check if unit exists
    if ex is None:
        exists = SIDECAR_UNIT_PATH.exists()
    else:
        result = ex.run(["test", "-f", str(SIDECAR_UNIT_PATH)], timeout=10)
        exists = result.ok

    if exists and not force:
        print(f"Unit file already exists: {SIDECAR_UNIT_PATH}")
        print("Use --force to overwrite")
        return

    if dry_run:
        print(f"Would write: {SIDECAR_UNIT_PATH}")
        print(f"Using rots path: {resolved_rots_path}")
        print("---")
        print(unit_content)
        print("---")
        print("Would run: systemctl daemon-reload")
        print("Would run: systemctl enable onetime-sidecar.service")
        return

    # Write unit file
    if ex is None:
        import subprocess

        # Use sudo tee to write with elevated privileges
        proc = subprocess.run(
            ["sudo", "tee", str(SIDECAR_UNIT_PATH)],
            input=unit_content,
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"Failed to write unit file: {proc.stderr}")
    else:
        # Remote: write via heredoc
        result = ex.run(
            ["sh", "-c", f"cat > {SIDECAR_UNIT_PATH}"],
            sudo=True,
            input=unit_content,
            timeout=30,
        )
        if not result.ok:
            raise RuntimeError(f"Failed to write unit file: {result.stderr}")

    print(f"Wrote: {SIDECAR_UNIT_PATH}")
    print(f"Using rots path: {resolved_rots_path}")

    # Reload systemd
    _run_systemctl("daemon-reload", executor=ex)
    print("Reloaded systemd daemon")

    # Enable service
    _run_systemctl("enable", SIDECAR_UNIT, executor=ex)
    print(f"Enabled: {SIDECAR_UNIT}")
    print()
    print("Start with: rots sidecar start")


@app.command
def uninstall(
    dry_run: DryRun = False,
    yes: Yes = False,
):
    """Uninstall the sidecar systemd unit.

    Stops and disables the service, then removes the unit file.

    Examples:
        rots sidecar uninstall
        rots sidecar uninstall --yes
    """
    ex = _get_executor()

    if not yes:
        confirm = input("Uninstall sidecar service? [y/N] ")
        if confirm.lower() != "y":
            print("Cancelled")
            return

    if dry_run:
        print("Would run: systemctl stop onetime-sidecar.service")
        print("Would run: systemctl disable onetime-sidecar.service")
        print(f"Would remove: {SIDECAR_UNIT_PATH}")
        print("Would run: systemctl daemon-reload")
        return

    # Stop if running
    _run_systemctl("stop", SIDECAR_UNIT, executor=ex, check=False)
    print(f"Stopped: {SIDECAR_UNIT}")

    # Disable
    _run_systemctl("disable", SIDECAR_UNIT, executor=ex, check=False)
    print(f"Disabled: {SIDECAR_UNIT}")

    # Remove unit file
    if ex is None:
        import subprocess

        subprocess.run(["sudo", "rm", "-f", str(SIDECAR_UNIT_PATH)], check=True)
    else:
        ex.run(["rm", "-f", str(SIDECAR_UNIT_PATH)], sudo=True, timeout=10)

    print(f"Removed: {SIDECAR_UNIT_PATH}")

    # Reload systemd
    _run_systemctl("daemon-reload", executor=ex)
    print("Reloaded systemd daemon")


@app.command
def start():
    """Start the sidecar daemon.

    Starts the systemd service.

    Examples:
        rots sidecar start
    """
    ex = _get_executor()
    _run_systemctl("start", SIDECAR_UNIT, executor=ex)
    print(f"Started: {SIDECAR_UNIT}")


@app.command
def stop():
    """Stop the sidecar daemon.

    Stops the systemd service.

    Examples:
        rots sidecar stop
    """
    ex = _get_executor()
    _run_systemctl("stop", SIDECAR_UNIT, executor=ex)
    print(f"Stopped: {SIDECAR_UNIT}")


@app.command
def restart():
    """Restart the sidecar daemon.

    Examples:
        rots sidecar restart
    """
    ex = _get_executor()
    _run_systemctl("restart", SIDECAR_UNIT, executor=ex)
    print(f"Restarted: {SIDECAR_UNIT}")


@app.command
def status(json_output: JsonOutput = False):
    """Show sidecar daemon status.

    Examples:
        rots sidecar status
        rots sidecar status --json
    """
    ex = _get_executor()

    result = _run_systemctl("status", SIDECAR_UNIT, executor=ex, check=False)

    if json_output:
        import json

        # Parse status
        is_active = "Active: active" in result.stdout
        is_running = "running" in result.stdout.lower()

        data = {
            "unit": SIDECAR_UNIT,
            "active": is_active,
            "running": is_running,
            "socket": str(SIDECAR_SOCKET),
        }
        print(json.dumps(data, indent=2))
    else:
        print(result.stdout)
        if result.stderr:
            print(result.stderr)


@app.command
def logs(
    lines: Lines = 50,
    follow: Follow = False,
):
    """Show sidecar daemon logs.

    Examples:
        rots sidecar logs
        rots sidecar logs -n 100
        rots sidecar logs --follow
    """
    from ots_shared.ssh import LocalExecutor

    ex = _get_executor() or LocalExecutor()

    cmd = ["journalctl", "-u", SIDECAR_UNIT, "-n", str(lines), "--no-pager"]
    if follow:
        cmd.append("-f")

    logger.debug("Running: %s", " ".join(cmd))

    if follow:
        # Interactive follow mode
        import subprocess

        subprocess.run(["sudo"] + cmd)
    else:
        result = ex.run(cmd, sudo=True, timeout=30)
        print(result.stdout)
        if result.stderr:
            print(result.stderr)


@app.command
def run(
    socket: Annotated[
        str,
        cyclopts.Parameter(
            name=["--socket", "-s"],
            help="Unix socket path",
        ),
    ] = str(SIDECAR_SOCKET),
    no_rabbitmq: Annotated[
        bool,
        cyclopts.Parameter(
            name="--no-rabbitmq",
            help="Disable RabbitMQ consumer",
        ),
    ] = False,
):
    """Run the sidecar daemon in foreground mode.

    This is primarily for debugging or when running under a process manager.
    For production, use: rots sidecar start

    Examples:
        rots sidecar run
        rots sidecar run --socket /tmp/test.sock --no-rabbitmq
    """
    import signal
    import sys
    import threading

    from rots.sidecar.commands import _import_handlers, dispatch
    from rots.sidecar.rabbitmq import RabbitMQConfig, RabbitMQConsumer
    from rots.sidecar.socket import SocketServer

    # Register all command handlers before creating servers
    _import_handlers()

    # Load RabbitMQ config (resolves host_id) before creating consumer
    rabbitmq_config = None if no_rabbitmq else RabbitMQConfig.from_environment()

    print(f"Starting sidecar daemon (PID: {os.getpid()})")
    print(f"Socket: {socket}")
    print(f"RabbitMQ: {'disabled' if no_rabbitmq else 'enabled'}")
    if rabbitmq_config:
        print(f"Host ID: {rabbitmq_config.host_id}")

    # Create servers
    socket_server = SocketServer(dispatch, socket_path=Path(socket))
    rabbitmq_consumer = None if no_rabbitmq else RabbitMQConsumer(dispatch, config=rabbitmq_config)

    # Start socket server in thread
    socket_thread = threading.Thread(target=socket_server.start, daemon=True)
    socket_thread.start()

    # Handle shutdown
    shutdown_event = threading.Event()

    def handle_signal(signum, frame):
        print(f"\nReceived signal {signum}, shutting down...")
        shutdown_event.set()
        socket_server.shutdown()
        if rabbitmq_consumer:
            rabbitmq_consumer.stop()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    # Run RabbitMQ consumer in main thread (or wait for shutdown)
    if rabbitmq_consumer:
        rabbitmq_thread = threading.Thread(target=rabbitmq_consumer.start, daemon=True)
        rabbitmq_thread.start()

    # Wait for shutdown
    print("Sidecar daemon running. Press Ctrl+C to stop.")
    shutdown_event.wait()

    print("Sidecar daemon stopped.")
    sys.exit(0)


@app.command
def send(
    command: Annotated[str, cyclopts.Parameter(help="Command to send")],
    *args: Annotated[str, cyclopts.Parameter(help="key=value arguments")],
    timeout: Annotated[
        float,
        cyclopts.Parameter(
            name=["--timeout", "-t"],
            help="Response timeout in seconds",
        ),
    ] = 30.0,
    socket: Annotated[
        bool,
        cyclopts.Parameter(
            name=["--socket", "-s"],
            help="Send via Unix socket (default)",
        ),
    ] = False,
    rabbitmq: Annotated[
        bool,
        cyclopts.Parameter(
            name=["--rabbitmq", "-r"],
            help="Send via RabbitMQ message queue",
        ),
    ] = False,
):
    """Send a command to the sidecar.

    Specify transport with --socket (default) or --rabbitmq.

    Examples:
        rots sidecar send health --socket
        rots sidecar send status --rabbitmq
        rots sidecar send restart.web identifier=7043
    """

    if socket and rabbitmq:
        print("Error: Cannot specify both --socket and --rabbitmq")
        raise SystemExit(1)

    payload = _parse_key_value_args(args)

    if rabbitmq:
        _send_via_rabbitmq(command, payload, timeout)
    else:
        _send_via_socket(command, payload, timeout)


def _send_via_socket(command: str, payload: dict, timeout: float) -> None:
    """Send command via Unix socket."""
    import json
    import socket as sock

    message = {"command": command, "payload": payload}
    message_bytes = json.dumps(message).encode("utf-8")

    try:
        client = sock.socket(sock.AF_UNIX, sock.SOCK_STREAM)
        client.settimeout(timeout)
        client.connect(str(SIDECAR_SOCKET))

        client.sendall(message_bytes)
        client.shutdown(sock.SHUT_WR)

        response_data = b""
        while True:
            chunk = client.recv(4096)
            if not chunk:
                break
            response_data += chunk

        client.close()

        response = json.loads(response_data.decode("utf-8"))
        print(json.dumps(response, indent=2))

    except FileNotFoundError:
        print(f"Error: Socket not found: {SIDECAR_SOCKET}")
        print("Is the sidecar daemon running? Try: rots sidecar status")
        raise SystemExit(1)
    except TimeoutError:
        print(f"Error: Timeout waiting for response ({timeout}s)")
        raise SystemExit(1)
    except Exception as e:
        print(f"Error: {e}")
        raise SystemExit(1)


def _send_via_rabbitmq(command: str, payload: dict, timeout: float) -> None:
    """Send command via RabbitMQ."""
    import json

    from rots.sidecar.rabbitmq import RabbitMQConfig, publish_command

    try:
        config = RabbitMQConfig.from_environment()
        print(f"Connecting to RabbitMQ at {config.host}:{config.port}/{config.vhost}")

        response = publish_command(command, payload, config=config, timeout=timeout)
        print(json.dumps(response, indent=2))

    except TimeoutError:
        print(f"Error: Timeout waiting for response ({timeout}s)")
        raise SystemExit(1)
    except ImportError as e:
        print(f"Error: Missing dependency: {e}")
        print("Install with: pipx inject rots pika")
        raise SystemExit(1)
    except Exception as e:
        print(f"Error: {e}")
        raise SystemExit(1)


@app.command
def discover(
    timeout: Annotated[
        float,
        cyclopts.Parameter(
            name=["--timeout", "-t"],
            help="Seconds to wait for sidecar responses",
        ),
    ] = 5.0,
    json_output: JsonOutput = False,
):
    """Discover active sidecars by broadcasting a ping.

    Sends a discover.ping to the shared command queue and collects
    responses within the timeout window. Each running sidecar responds
    with its host_id.

    Examples:
        rots sidecar discover
        rots sidecar discover --timeout 10
        rots sidecar discover --json
    """
    import json
    import time
    import uuid

    logger.info("Broadcasting discover.ping (timeout=%.1fs)", timeout)

    try:
        import pika

        from rots.sidecar.rabbitmq import (
            DISCOVER_EXCHANGE,
            RabbitMQConfig,
        )

        config = RabbitMQConfig.from_environment()
        credentials = pika.PlainCredentials(config.username, config.password)
        parameters = pika.ConnectionParameters(
            host=config.host,
            port=config.port,
            virtual_host=config.vhost,
            credentials=credentials,
        )

        connection = pika.BlockingConnection(parameters)
        channel = connection.channel()
        logger.debug(
            "Connected to RabbitMQ at %s:%d/%s",
            config.host,
            config.port,
            config.vhost,
        )

        # Fanout exchange delivers to every sidecar's exclusive queue
        channel.exchange_declare(
            exchange=DISCOVER_EXCHANGE,
            exchange_type="fanout",
            durable=True,
        )

        # Declare exclusive callback queue for collecting responses
        result = channel.queue_declare(queue="", exclusive=True)
        callback_queue = result.method.queue

        correlation_id = str(uuid.uuid4())
        responses: list[dict[str, Any]] = []

        def on_response(ch, method, props, body):
            if props.correlation_id == correlation_id:
                responses.append(json.loads(body.decode("utf-8")))

        channel.basic_consume(
            queue=callback_queue,
            on_message_callback=on_response,
            auto_ack=True,
        )

        # Publish discover.ping via fanout — all sidecars receive it
        message = {"command": "discover.ping", "payload": {}}
        channel.basic_publish(
            exchange=DISCOVER_EXCHANGE,
            routing_key="",  # fanout ignores routing key
            body=json.dumps(message).encode("utf-8"),
            properties=pika.BasicProperties(
                reply_to=callback_queue,
                correlation_id=correlation_id,
                content_type="application/json",
            ),
        )
        logger.debug(
            "Published discover.ping: correlation_id=%s callback_queue=%s",
            correlation_id,
            callback_queue,
        )

        # Collect responses until timeout
        deadline = time.time() + timeout
        while time.time() < deadline:
            connection.process_data_events(time_limit=1)

        connection.close()
        logger.info(
            "Discovery complete: %d sidecar(s) responded in %.1fs",
            len(responses),
            timeout,
        )

        if json_output:
            print(json.dumps({"sidecars": responses, "count": len(responses)}, indent=2))
        else:
            if not responses:
                print("No sidecars responded within timeout")
            else:
                print(f"Discovered {len(responses)} sidecar(s):")
                for resp in responses:
                    result_data = resp.get("result", resp)
                    host_id = result_data.get("host_id", "unknown")
                    print(f"  - {host_id}")

    except ImportError as e:
        print(f"Error: Missing dependency: {e}")
        print("Install with: pipx inject rots pika")
        raise SystemExit(1)
    except Exception as e:
        logger.error("Discovery failed: %s", e)
        print(f"Error: {e}")
        raise SystemExit(1)


@app.command
def publish(
    command: Annotated[str, cyclopts.Parameter(help="Command name or JSON message")],
    *args: Annotated[str, cyclopts.Parameter(help="key=value arguments")],
    timeout: Annotated[
        float,
        cyclopts.Parameter(
            name=["--timeout", "-t"],
            help="Response timeout in seconds",
        ),
    ] = 30.0,
    broadcast: Annotated[
        bool,
        cyclopts.Parameter(
            name=["--broadcast", "-b"],
            help="Send to shared queue (any sidecar) instead of targeted host",
        ),
    ] = False,
):
    """Publish a command to a remote sidecar via RabbitMQ with host targeting.

    Target resolution (unless --broadcast):
      1. Global --host flag: rots -H <host> sidecar publish ...
      2. SIDECAR_HOST_ID environment variable
      3. SIDECAR_HOST_ID from .otsinfra.env (walk-up discovery)

    Examples:
        # Explicit host targeting
        rots -H acme-prod-1 sidecar publish restart.web identifier=7043

        # From .otsinfra.env directory (uses SIDECAR_HOST_ID)
        cd ops-jurisdictions/acme && rots sidecar publish restart.web

        # Broadcast to any available sidecar
        rots sidecar publish --broadcast health
    """
    import json

    from rots.sidecar.rabbitmq import RabbitMQConfig, get_host_id, publish_command

    from ... import context

    # Resolve target host
    target_host: str | None = None
    if not broadcast:
        # 1. Check global --host flag first
        host_flag = context.host_var.get(None)
        if host_flag:
            target_host = host_flag
        else:
            # 2. Fall back to get_host_id() for .otsinfra.env discovery
            target_host = get_host_id()

    # Parse command: try JSON first, then key=value args
    command_name: str
    payload: dict[str, Any] = {}

    try:
        # Try parsing as JSON
        parsed = json.loads(command)
        command_name = parsed.get("command", "")
        payload = parsed.get("payload", {})
    except json.JSONDecodeError:
        # Not JSON - treat as command name with key=value args
        command_name = command
        payload = _parse_key_value_args(args)

    if not command_name:
        print("Error: No command specified")
        raise SystemExit(1)

    try:
        config = RabbitMQConfig.from_environment()

        # Show targeting info
        if target_host:
            print(f"Target: {target_host}")
            print(f"Queue: ots.sidecar.commands.{target_host}")
        else:
            print("Target: broadcast (any available sidecar)")
            print("Queue: ots.sidecar.commands")
        print(f"RabbitMQ: {config.host}:{config.port}/{config.vhost}")
        print(f"Command: {command_name}")
        if payload:
            print(f"Payload: {json.dumps(payload)}")
        print()

        response = publish_command(
            command_name,
            payload,
            config=config,
            timeout=timeout,
            target_host=target_host,
        )
        print(json.dumps(response, indent=2))

    except TimeoutError:
        print(f"Error: Timeout waiting for response ({timeout}s)")
        if target_host:
            print(f"Hint: Is the sidecar running on {target_host}?")
        raise SystemExit(1)
    except ImportError as e:
        print(f"Error: Missing dependency: {e}")
        print("Install with: pipx inject rots pika")
        raise SystemExit(1)
    except Exception as e:
        print(f"Error: {e}")
        raise SystemExit(1)
