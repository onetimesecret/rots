# src/rots/sidecar/socket.py

"""Unix socket server for local sidecar communication.

Listens on /run/onetime-sidecar.sock for JSON-formatted command messages.
Each message is dispatched to the appropriate handler and a JSON response
is returned.

Socket permissions are root-only (mode 0600) for security.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import socketserver
import stat
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .commands import CommandResult

logger = logging.getLogger(__name__)

# Default socket path (can be overridden for testing)
DEFAULT_SOCKET_PATH = Path("/run/onetime-sidecar.sock")

# Maximum message size (64KB should be plenty for JSON commands)
MAX_MESSAGE_SIZE = 65536

# Socket permissions: owner read/write only (root)
SOCKET_MODE = stat.S_IRUSR | stat.S_IWUSR  # 0600


@dataclass
class Message:
    """Parsed incoming message from socket client."""

    command: str
    params: dict[str, Any]
    request_id: str | None = None

    @classmethod
    def from_json(cls, data: bytes) -> Message:
        """Parse JSON bytes into a Message.

        Expected format:
        {
            "command": "restart.web",
            "payload": {"port": 7043},
            "request_id": "optional-correlation-id"
        }

        Raises:
            ValueError: If JSON is invalid or missing required fields.
        """
        try:
            obj = json.loads(data.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            raise ValueError(f"Invalid JSON: {e}") from e

        if not isinstance(obj, dict):
            raise ValueError("Message must be a JSON object")

        command = obj.get("command")
        if not command or not isinstance(command, str):
            raise ValueError("Missing or invalid 'command' field")

        payload = obj.get("payload", {})
        if not isinstance(payload, dict):
            raise ValueError("'payload' must be an object if provided")

        request_id = obj.get("request_id")
        if request_id is not None and not isinstance(request_id, str):
            raise ValueError("'request_id' must be a string if provided")

        return cls(command=command, params=payload, request_id=request_id)


@dataclass
class Response:
    """Response to send back to socket client."""

    success: bool
    result: Any = None
    error: str | None = None
    request_id: str | None = None

    def to_json(self) -> bytes:
        """Serialize response to JSON bytes."""
        obj: dict[str, Any] = {"success": self.success}
        if self.result is not None:
            obj["result"] = self.result
        if self.error is not None:
            obj["error"] = self.error
        if self.request_id is not None:
            obj["request_id"] = self.request_id
        return json.dumps(obj).encode("utf-8")


# Type alias for dispatcher function
Dispatcher = Callable[[str, dict[str, Any]], "CommandResult"]


class SidecarUnixServer(socketserver.UnixStreamServer):
    """Unix stream server that holds the dispatcher for handlers.

    This subclass stores the dispatcher as an instance attribute rather than
    using a class variable on the handler. Each handler accesses the dispatcher
    via self.server.dispatcher, making it safe for multi-instance scenarios.
    """

    def __init__(
        self,
        server_address: str,
        RequestHandlerClass: type[socketserver.BaseRequestHandler],
        dispatcher: Dispatcher,
    ) -> None:
        self.dispatcher = dispatcher
        super().__init__(server_address, RequestHandlerClass)


class SocketHandler(socketserver.BaseRequestHandler):
    """Handle individual socket connections.

    Each connection can send one message and receive one response.
    The handler reads the JSON message, dispatches to the command handler,
    and sends back the JSON response.

    The dispatcher is accessed via self.server.dispatcher, which is set
    by the SidecarUnixServer that creates this handler.
    """

    # Type hint for server (set by socketserver framework)
    server: SidecarUnixServer

    def handle(self) -> None:
        """Process a single client connection."""
        try:
            # Read message from client
            data = self.request.recv(MAX_MESSAGE_SIZE)
            if not data:
                logger.debug("Empty message received, closing connection")
                return

            logger.debug("Received %d bytes from socket client", len(data))

            # Parse the message
            try:
                message = Message.from_json(data)
            except ValueError as e:
                logger.warning("Invalid message: %s", e)
                response = Response(
                    success=False,
                    error=str(e),
                )
                self.request.sendall(response.to_json())
                return

            logger.info("Dispatching command: %s", message.command)

            # Get dispatcher from server instance (not class variable)
            dispatcher = getattr(self.server, "dispatcher", None)

            # Dispatch to handler
            if dispatcher is None:
                response = Response(
                    success=False,
                    error="No dispatcher configured",
                    request_id=message.request_id,
                )
            else:
                try:
                    result = dispatcher(message.command, message.params)
                    response = Response(
                        success=result.success,
                        result=result.data,
                        error=result.error,
                        request_id=message.request_id,
                    )
                except Exception as e:
                    logger.exception("Command handler raised exception")
                    response = Response(
                        success=False,
                        error=f"Internal error: {e}",
                        request_id=message.request_id,
                    )

            # Send response
            self.request.sendall(response.to_json())

        except OSError as e:
            logger.error("Socket error: %s", e)


class SocketServer:
    """Unix socket server for sidecar commands.

    Usage:
        server = SocketServer(dispatcher=my_dispatcher_func)
        server.start()  # blocks until shutdown
    """

    def __init__(
        self,
        dispatcher: Dispatcher,
        socket_path: Path | None = None,
    ) -> None:
        """Initialize the socket server.

        Args:
            dispatcher: Function to dispatch commands to handlers.
                        Takes (command: str, params: dict) and returns CommandResult.
            socket_path: Path for the Unix socket. Defaults to /run/onetime-sidecar.sock
        """
        self.socket_path = socket_path or DEFAULT_SOCKET_PATH
        self.dispatcher = dispatcher
        self._server: SidecarUnixServer | None = None

    def start(self) -> None:
        """Start the socket server (blocks until shutdown).

        Creates the socket file with root-only permissions.
        Removes any existing socket file first.
        """
        # Remove stale socket file if it exists
        if self.socket_path.exists():
            logger.info("Removing stale socket file: %s", self.socket_path)
            self.socket_path.unlink()

        # Ensure parent directory exists
        self.socket_path.parent.mkdir(parents=True, exist_ok=True)

        # Create server with dispatcher bound to the server instance
        self._server = SidecarUnixServer(
            str(self.socket_path),
            SocketHandler,
            dispatcher=self.dispatcher,
        )

        # Set socket permissions to root-only (0600)
        os.chmod(self.socket_path, SOCKET_MODE)
        logger.info(
            "Socket server listening on %s (mode %o)",
            self.socket_path,
            SOCKET_MODE,
        )

        try:
            self._server.serve_forever()
        finally:
            self.shutdown()

    def shutdown(self) -> None:
        """Stop the server and clean up socket file."""
        if self._server is not None:
            logger.info("Shutting down socket server")
            self._server.shutdown()
            self._server = None

        # Clean up socket file
        if self.socket_path.exists():
            try:
                self.socket_path.unlink()
                logger.debug("Removed socket file: %s", self.socket_path)
            except OSError as e:
                logger.warning("Failed to remove socket file: %s", e)


def send_command(
    command: str,
    params: dict[str, Any] | None = None,
    *,
    socket_path: Path | None = None,
    request_id: str | None = None,
    timeout: float = 30.0,
) -> Response:
    """Send a command to the sidecar socket and return the response.

    This is a client helper function for CLI commands to communicate
    with the running sidecar daemon.

    Args:
        command: Command name (e.g., "restart.web", "config.stage")
        params: Command parameters as a dict
        socket_path: Override default socket path
        request_id: Optional correlation ID
        timeout: Socket timeout in seconds

    Returns:
        Response object with success, result, and error fields

    Raises:
        ConnectionError: If unable to connect to the socket
        TimeoutError: If the operation times out
    """
    socket_path = socket_path or DEFAULT_SOCKET_PATH

    if not socket_path.exists():
        raise ConnectionError(f"Socket not found: {socket_path}")

    message = {
        "command": command,
        "payload": params or {},
    }
    if request_id is not None:
        message["request_id"] = request_id

    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        try:
            sock.connect(str(socket_path))
        except OSError as e:
            raise ConnectionError(f"Cannot connect to socket: {e}") from e

        # Send command
        sock.sendall(json.dumps(message).encode("utf-8"))

        # Receive response
        data = sock.recv(MAX_MESSAGE_SIZE)
        if not data:
            raise ConnectionError("Empty response from sidecar")

        try:
            obj = json.loads(data.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            raise ConnectionError(f"Invalid response from sidecar: {e}") from e

        return Response(
            success=obj.get("success", False),
            result=obj.get("result"),
            error=obj.get("error"),
            request_id=obj.get("request_id"),
        )
