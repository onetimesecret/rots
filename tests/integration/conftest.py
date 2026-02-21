# tests/integration/conftest.py
"""Fixtures for SSH integration tests.

Provides a fake SSH server (paramiko server mode) that accepts key-based
authentication and returns scripted responses to commands. This allows
testing the full SSHExecutor -> Paramiko -> transport path without
requiring a real remote host.

Usage in tests:

    def test_something(fake_ssh_server):
        client = fake_ssh_server.connect()
        executor = SSHExecutor(client)
        result = executor.run(["systemctl", "is-active", "myunit"])
        assert result.stdout.strip() == "active"
"""

from __future__ import annotations

import logging
import os
import socket
import threading
from dataclasses import dataclass, field
from pathlib import Path

import paramiko
import pytest

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Key generation helper
# ---------------------------------------------------------------------------

_HOST_KEY: paramiko.RSAKey | None = None


def _get_host_key() -> paramiko.RSAKey:
    """Return a reusable RSA host key (generated once per process)."""
    global _HOST_KEY
    if _HOST_KEY is None:
        _HOST_KEY = paramiko.RSAKey.generate(2048)
    return _HOST_KEY


def _generate_client_key(tmp_path: Path) -> tuple[paramiko.RSAKey, Path]:
    """Generate a client RSA key pair and write the private key to disk.

    Returns (key_object, private_key_path).
    """
    key = paramiko.RSAKey.generate(2048)
    key_path = tmp_path / "id_rsa_test"
    key.write_private_key_file(str(key_path))
    os.chmod(key_path, 0o600)
    return key, key_path


# ---------------------------------------------------------------------------
# Scripted command handler
# ---------------------------------------------------------------------------


@dataclass
class ScriptedResponse:
    """A canned response for a command pattern."""

    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0


# Default responses for common commands used by ots-containers
DEFAULT_RESPONSES: dict[str, ScriptedResponse] = {
    "systemctl is-active": ScriptedResponse(stdout="active\n"),
    "systemctl start": ScriptedResponse(),
    "systemctl stop": ScriptedResponse(),
    "systemctl restart": ScriptedResponse(),
    "systemctl daemon-reload": ScriptedResponse(),
    "systemctl show": ScriptedResponse(stdout="ActiveState=active\nSubState=running\n"),
    "journalctl": ScriptedResponse(stdout="-- No entries --\n"),
    "podman ps": ScriptedResponse(
        stdout="CONTAINER ID  IMAGE  COMMAND  CREATED  STATUS  PORTS  NAMES\n"
    ),
    "podman stats": ScriptedResponse(stdout="[]\n"),
    "sqlite3": ScriptedResponse(),
    "echo": ScriptedResponse(stdout="ok\n"),
}


def _match_response(
    command: str,
    responses: dict[str, ScriptedResponse],
) -> ScriptedResponse:
    """Find the best matching scripted response for a command.

    Tries exact match first, then prefix match (longest prefix wins).
    Falls back to a generic success response.
    """
    # Exact match
    if command in responses:
        return responses[command]

    # Prefix match (longest first)
    best_match = ""
    for pattern in responses:
        if command.startswith(pattern) and len(pattern) > len(best_match):
            best_match = pattern
    if best_match:
        return responses[best_match]

    # Generic fallback
    return ScriptedResponse()


# ---------------------------------------------------------------------------
# Paramiko server implementation
# ---------------------------------------------------------------------------


class _FakeServerInterface(paramiko.ServerInterface):
    """Paramiko server interface that accepts key auth and runs scripted commands."""

    def __init__(self, client_pubkey: paramiko.PKeyBase) -> None:
        self._client_pubkey = client_pubkey
        self.event = threading.Event()

    def check_auth_publickey(self, username: str, key: paramiko.PKeyBase) -> int:
        if key == self._client_pubkey:
            return paramiko.AUTH_SUCCESSFUL
        return paramiko.AUTH_FAILED

    def check_channel_request(self, kind: str, chanid: int) -> int:
        if kind == "session":
            return paramiko.OPEN_SUCCEEDED
        return paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED

    def check_channel_exec_request(self, channel: paramiko.Channel, command: bytes) -> bool:
        return True

    def check_channel_pty_request(
        self,
        channel: paramiko.Channel,
        term: bytes,
        width: int,
        height: int,
        pixelwidth: int,
        pixelheight: int,
        modes: bytes,
    ) -> bool:
        return True

    def check_channel_shell_request(self, channel: paramiko.Channel) -> bool:
        return True

    def check_channel_window_change_request(
        self,
        channel: paramiko.Channel,
        width: int,
        height: int,
        pixelwidth: int,
        pixelheight: int,
    ) -> bool:
        return True

    def get_allowed_auths(self, username: str) -> str:
        return "publickey"


def _handle_session(
    transport: paramiko.Transport,
    responses: dict[str, ScriptedResponse],
) -> None:
    """Handle a single SSH session: wait for a channel, respond to exec requests."""
    try:
        channel = transport.accept(timeout=10)
        if channel is None:
            return

        # Wait for exec_command event (set by check_channel_exec_request)
        # The command is delivered via the exec subsystem
        # We need to read it from the transport event
        channel.event.wait(timeout=10)

        # Get the command that was requested
        # In paramiko server mode, the command comes through the exec request
        # which we capture via a custom handler
        command = getattr(channel, "_exec_command", None)
        if command is None:
            # Try to read from channel as a shell session
            command = ""

        if isinstance(command, bytes):
            command = command.decode("utf-8", errors="replace")

        response = _match_response(command, responses)

        if response.stdout:
            channel.sendall(response.stdout.encode())
        if response.stderr:
            channel.sendall_stderr(response.stderr.encode())

        channel.send_exit_status(response.exit_code)
        channel.close()
    except Exception:
        logger.debug("Session handler error", exc_info=True)
    finally:
        try:
            transport.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Fake SSH server
# ---------------------------------------------------------------------------


@dataclass
class FakeSSHServer:
    """An in-process SSH server for integration testing.

    Attributes:
        host: Bind address (localhost).
        port: TCP port the server listens on.
        client_key_path: Path to the private key file for client auth.
        responses: Mutable dict of command -> ScriptedResponse.
    """

    host: str
    port: int
    client_key_path: Path
    responses: dict[str, ScriptedResponse] = field(default_factory=dict)
    _server_socket: socket.socket | None = field(default=None, repr=False)
    _accept_thread: threading.Thread | None = field(default=None, repr=False)
    _running: bool = field(default=False, repr=False)
    _client_pubkey: paramiko.PKeyBase | None = field(default=None, repr=False)

    def connect(self) -> paramiko.SSHClient:
        """Create a paramiko SSHClient connected to this fake server.

        Returns a connected client ready for SSHExecutor.
        """
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(
            hostname=self.host,
            port=self.port,
            username="testuser",
            key_filename=str(self.client_key_path),
            look_for_keys=False,
            allow_agent=False,
            timeout=5,
        )
        return client

    def add_response(
        self, pattern: str, stdout: str = "", stderr: str = "", exit_code: int = 0
    ) -> None:
        """Add or override a scripted response for a command pattern."""
        self.responses[pattern] = ScriptedResponse(
            stdout=stdout, stderr=stderr, exit_code=exit_code
        )

    def _accept_loop(self) -> None:
        """Accept connections in a background thread."""
        while self._running:
            try:
                self._server_socket.settimeout(0.5)
                conn, addr = self._server_socket.accept()
            except TimeoutError:
                continue
            except OSError:
                break

            try:
                transport = paramiko.Transport(conn)
                transport.add_server_key(_get_host_key())

                server_interface = _FakeServerInterface(self._client_pubkey)

                # Monkey-patch the transport to capture exec commands
                original_check = server_interface.check_channel_exec_request

                def _patched_check(channel, command, _orig=original_check):
                    channel._exec_command = command
                    channel.event.set()
                    return _orig(channel, command)

                server_interface.check_channel_exec_request = _patched_check

                transport.start_server(server=server_interface)

                # Handle session in a separate thread
                t = threading.Thread(
                    target=_handle_session,
                    args=(transport, self.responses),
                    daemon=True,
                )
                t.start()
            except Exception:
                logger.debug("Connection handler error", exc_info=True)
                try:
                    conn.close()
                except Exception:
                    pass

    def start(self) -> None:
        """Start the server (called by the fixture)."""
        self._running = True
        self._accept_thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._accept_thread.start()

    def stop(self) -> None:
        """Stop the server and close the socket."""
        self._running = False
        if self._server_socket:
            try:
                self._server_socket.close()
            except Exception:
                pass
        if self._accept_thread:
            self._accept_thread.join(timeout=2)


# ---------------------------------------------------------------------------
# Pytest fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_ssh_server(tmp_path):
    """Start a fake SSH server for integration tests.

    Yields a FakeSSHServer instance. The server accepts key-based auth
    and returns scripted responses to commands.

    Example:
        def test_ssh_executor(fake_ssh_server):
            fake_ssh_server.add_response(
                "systemctl is-active myunit",
                stdout="active\\n",
            )
            client = fake_ssh_server.connect()
            executor = SSHExecutor(client)
            result = executor.run(["systemctl", "is-active", "myunit"])
            assert result.stdout.strip() == "active"
    """
    # Generate client key pair
    client_key, client_key_path = _generate_client_key(tmp_path)

    # Bind to a random available port
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_socket.bind(("127.0.0.1", 0))
    server_socket.listen(5)
    _, port = server_socket.getsockname()

    server = FakeSSHServer(
        host="127.0.0.1",
        port=port,
        client_key_path=client_key_path,
        responses=dict(DEFAULT_RESPONSES),
        _server_socket=server_socket,
        _client_pubkey=client_key,
    )
    server.start()

    yield server

    server.stop()
