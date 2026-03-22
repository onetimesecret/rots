# src/rots/commands/sidecar/app.py

"""Sidecar daemon management commands.

The sidecar daemon provides remote control of OTS instances via RabbitMQ
and local control via Unix socket.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import cyclopts

from ..common import DryRun, Follow, JsonOutput, Lines, Yes

if TYPE_CHECKING:
    from ots_shared.ssh.executor import Executor

logger = logging.getLogger(__name__)

app = cyclopts.App(
    name="sidecar",
    help="Manage the OTS sidecar daemon (remote control)",
)

# Paths
SIDECAR_UNIT = "onetime-sidecar.service"
SIDECAR_SOCKET = Path("/run/onetime-sidecar.sock")
SIDECAR_UNIT_PATH = Path("/etc/systemd/system/onetime-sidecar.service")

# Systemd unit template
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

ExecStart=/usr/local/bin/rots sidecar run
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

# Security hardening
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
ReadWritePaths=/run /var/lib/onetimesecret /etc/onetimesecret

[Install]
WantedBy=multi-user.target
"""


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
):
    """Install the sidecar systemd unit.

    Writes the systemd unit file and enables the service.
    Does not start the service automatically.

    Examples:
        rots sidecar install
        rots sidecar install --force
    """
    ex = _get_executor()

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
        print("---")
        print(SYSTEMD_UNIT_TEMPLATE)
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
            input=SYSTEMD_UNIT_TEMPLATE,
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
            input=SYSTEMD_UNIT_TEMPLATE,
            timeout=30,
        )
        if not result.ok:
            raise RuntimeError(f"Failed to write unit file: {result.stderr}")

    print(f"Wrote: {SIDECAR_UNIT_PATH}")

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
    from rots.sidecar.rabbitmq import RabbitMQConsumer
    from rots.sidecar.socket import SocketServer

    # Register all command handlers before creating servers
    _import_handlers()

    print(f"Starting sidecar daemon (PID: {os.getpid()})")
    print(f"Socket: {socket}")
    print(f"RabbitMQ: {'disabled' if no_rabbitmq else 'enabled'}")

    # Create servers
    socket_server = SocketServer(dispatch, socket_path=Path(socket))
    rabbitmq_consumer = None if no_rabbitmq else RabbitMQConsumer(dispatch)

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
):
    """Send a command to the sidecar via Unix socket.

    Useful for testing and debugging.

    Examples:
        rots sidecar send health
        rots sidecar send status
        rots sidecar send restart.web identifier=7043
    """
    import json
    import socket as sock

    # Parse args into payload
    payload: dict[str, str] = {}
    for arg in args:
        if "=" in arg:
            key, value = arg.split("=", 1)
            payload[key] = value
        else:
            print(f"Warning: Ignoring invalid argument (no =): {arg}")

    # Build message
    message = {"command": command, "payload": payload}
    message_bytes = json.dumps(message).encode("utf-8")

    # Connect to socket
    try:
        client = sock.socket(sock.AF_UNIX, sock.SOCK_STREAM)
        client.settimeout(timeout)
        client.connect(str(SIDECAR_SOCKET))

        # Send message
        client.sendall(message_bytes)
        client.shutdown(sock.SHUT_WR)

        # Receive response
        response_data = b""
        while True:
            chunk = client.recv(4096)
            if not chunk:
                break
            response_data += chunk

        client.close()

        # Parse and print response
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
