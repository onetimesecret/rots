# tests/sidecar/test_socket.py

"""Tests for src/rots/sidecar/socket.py

Covers:
- Message.from_json parsing and validation
- Response.to_json serialization
- SocketHandler dispatch flow (mocked)
- send_command client function
"""

import json
import threading
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from rots.sidecar.commands import CommandResult
from rots.sidecar.socket import (
    DEFAULT_SOCKET_PATH,
    MAX_MESSAGE_SIZE,
    Message,
    Response,
    SocketHandler,
    SocketServer,
    send_command,
)


class TestMessage:
    """Tests for Message dataclass and parsing."""

    def test_from_json_valid_basic(self):
        """Parse valid message with command only."""
        data = b'{"command": "health"}'
        msg = Message.from_json(data)

        assert msg.command == "health"
        assert msg.params == {}
        assert msg.request_id is None

    def test_from_json_valid_with_params(self):
        """Parse valid message with payload."""
        data = b'{"command": "restart.web", "payload": {"port": 7043}}'
        msg = Message.from_json(data)

        assert msg.command == "restart.web"
        assert msg.params == {"port": 7043}
        assert msg.request_id is None

    def test_from_json_valid_with_request_id(self):
        """Parse valid message with request_id."""
        data = b'{"command": "status", "payload": {}, "request_id": "abc-123"}'
        msg = Message.from_json(data)

        assert msg.command == "status"
        assert msg.params == {}
        assert msg.request_id == "abc-123"

    def test_from_json_invalid_json(self):
        """Reject malformed JSON."""
        with pytest.raises(ValueError, match="Invalid JSON"):
            Message.from_json(b"not json")

    def test_from_json_not_object(self):
        """Reject non-object JSON."""
        with pytest.raises(ValueError, match="must be a JSON object"):
            Message.from_json(b'["list", "not", "object"]')

    def test_from_json_missing_command(self):
        """Reject message without command field."""
        with pytest.raises(ValueError, match="Missing or invalid 'command'"):
            Message.from_json(b'{"params": {}}')

    def test_from_json_empty_command(self):
        """Reject message with empty command."""
        with pytest.raises(ValueError, match="Missing or invalid 'command'"):
            Message.from_json(b'{"command": ""}')

    def test_from_json_invalid_command_type(self):
        """Reject message with non-string command."""
        with pytest.raises(ValueError, match="Missing or invalid 'command'"):
            Message.from_json(b'{"command": 123}')

    def test_from_json_invalid_params_type(self):
        """Reject message with non-object payload."""
        with pytest.raises(ValueError, match="'payload' must be an object"):
            Message.from_json(b'{"command": "health", "payload": "string"}')

    def test_from_json_invalid_request_id_type(self):
        """Reject message with non-string request_id."""
        with pytest.raises(ValueError, match="'request_id' must be a string"):
            Message.from_json(b'{"command": "health", "request_id": 123}')

    def test_from_json_unicode(self):
        """Parse message with unicode content."""
        data = b'{"command": "test", "payload": {"name": "cafe"}}'
        msg = Message.from_json(data)

        assert msg.command == "test"
        assert msg.params == {"name": "cafe"}


class TestResponse:
    """Tests for Response dataclass and serialization."""

    def test_to_json_success_minimal(self):
        """Serialize successful response with no extras."""
        resp = Response(success=True)
        data = resp.to_json()
        obj = json.loads(data)

        assert obj == {"success": True}

    def test_to_json_success_with_result(self):
        """Serialize successful response with result data."""
        resp = Response(success=True, result={"count": 42})
        data = resp.to_json()
        obj = json.loads(data)

        assert obj == {"success": True, "result": {"count": 42}}

    def test_to_json_failure_with_error(self):
        """Serialize failed response with error message."""
        resp = Response(success=False, error="Something went wrong")
        data = resp.to_json()
        obj = json.loads(data)

        assert obj == {"success": False, "error": "Something went wrong"}

    def test_to_json_with_request_id(self):
        """Serialize response with request_id echo."""
        resp = Response(success=True, request_id="req-456")
        data = resp.to_json()
        obj = json.loads(data)

        assert obj == {"success": True, "request_id": "req-456"}

    def test_to_json_full(self):
        """Serialize response with all fields."""
        resp = Response(
            success=False,
            result={"partial": "data"},
            error="Timeout occurred",
            request_id="full-test",
        )
        data = resp.to_json()
        obj = json.loads(data)

        assert obj == {
            "success": False,
            "result": {"partial": "data"},
            "error": "Timeout occurred",
            "request_id": "full-test",
        }


class TestSocketHandler:
    """Tests for SocketHandler dispatch logic."""

    def test_dispatch_success(self):
        """Successful command dispatch returns result."""
        mock_dispatcher = MagicMock(return_value=CommandResult.ok({"status": "running"}))

        # Create mock request object
        mock_request = MagicMock()
        mock_request.recv.return_value = b'{"command": "status"}'
        sent_data = []
        mock_request.sendall = lambda d: sent_data.append(d)

        # Create mock server with dispatcher
        mock_server = MagicMock()
        mock_server.dispatcher = mock_dispatcher

        # Create handler with mocked socket
        SocketHandler(mock_request, ("", 0), mock_server)

        # Verify dispatcher was called
        mock_dispatcher.assert_called_once_with("status", {})

        # Verify response was sent
        assert len(sent_data) == 1
        response = json.loads(sent_data[0])
        assert response["success"] is True
        assert response["result"] == {"status": "running"}

    def test_dispatch_failure(self):
        """Failed command dispatch returns error."""
        mock_dispatcher = MagicMock(return_value=CommandResult.fail("Not found"))

        mock_request = MagicMock()
        mock_request.recv.return_value = b'{"command": "unknown"}'
        sent_data = []
        mock_request.sendall = lambda d: sent_data.append(d)

        mock_server = MagicMock()
        mock_server.dispatcher = mock_dispatcher

        SocketHandler(mock_request, ("", 0), mock_server)

        assert len(sent_data) == 1
        response = json.loads(sent_data[0])
        assert response["success"] is False
        assert response["error"] == "Not found"

    def test_dispatch_exception(self):
        """Exception in handler returns error response."""
        mock_dispatcher = MagicMock(side_effect=RuntimeError("Boom"))

        mock_request = MagicMock()
        mock_request.recv.return_value = b'{"command": "explode"}'
        sent_data = []
        mock_request.sendall = lambda d: sent_data.append(d)

        mock_server = MagicMock()
        mock_server.dispatcher = mock_dispatcher

        SocketHandler(mock_request, ("", 0), mock_server)

        assert len(sent_data) == 1
        response = json.loads(sent_data[0])
        assert response["success"] is False
        assert "Internal error" in response["error"]
        assert "Boom" in response["error"]

    def test_dispatch_invalid_json(self):
        """Invalid JSON returns error response."""
        mock_request = MagicMock()
        mock_request.recv.return_value = b"not json"
        sent_data = []
        mock_request.sendall = lambda d: sent_data.append(d)

        SocketHandler(mock_request, ("", 0), MagicMock())

        assert len(sent_data) == 1
        response = json.loads(sent_data[0])
        assert response["success"] is False
        assert "Invalid JSON" in response["error"]

    def test_dispatch_empty_message(self):
        """Empty message closes connection without response."""
        mock_request = MagicMock()
        mock_request.recv.return_value = b""
        sent_data = []
        mock_request.sendall = lambda d: sent_data.append(d)

        SocketHandler(mock_request, ("", 0), MagicMock())

        # No response sent for empty message
        assert len(sent_data) == 0

    def test_dispatch_no_dispatcher(self):
        """Missing dispatcher returns error."""
        mock_request = MagicMock()
        mock_request.recv.return_value = b'{"command": "test"}'
        sent_data = []
        mock_request.sendall = lambda d: sent_data.append(d)

        # Create server without dispatcher attribute
        mock_server = MagicMock(spec=[])

        SocketHandler(mock_request, ("", 0), mock_server)

        assert len(sent_data) == 1
        response = json.loads(sent_data[0])
        assert response["success"] is False
        assert "No dispatcher configured" in response["error"]

    def test_request_id_echoed(self):
        """Request ID is echoed in response."""
        mock_dispatcher = MagicMock(return_value=CommandResult.ok())

        mock_request = MagicMock()
        mock_request.recv.return_value = b'{"command": "test", "request_id": "echo-me"}'
        sent_data = []
        mock_request.sendall = lambda d: sent_data.append(d)

        mock_server = MagicMock()
        mock_server.dispatcher = mock_dispatcher

        SocketHandler(mock_request, ("", 0), mock_server)

        response = json.loads(sent_data[0])
        assert response["request_id"] == "echo-me"


class TestSendCommand:
    """Tests for send_command client helper."""

    def test_socket_not_found(self, tmp_path):
        """Raise ConnectionError if socket doesn't exist."""
        fake_socket = tmp_path / "nonexistent.sock"

        with pytest.raises(ConnectionError, match="Socket not found"):
            send_command("test", socket_path=fake_socket)

    def test_successful_round_trip(self):
        """Full round-trip with mock server."""
        # Use /tmp directly to avoid AF_UNIX path length limit (108 chars)
        import uuid

        socket_path = Path(f"/tmp/test-{uuid.uuid4().hex[:8]}.sock")

        # Create a simple echo server
        def server_thread():
            import socketserver

            class EchoHandler(socketserver.BaseRequestHandler):
                def handle(self):
                    data = self.request.recv(MAX_MESSAGE_SIZE)
                    msg = json.loads(data)
                    response = {
                        "success": True,
                        "result": {"echoed": msg["command"]},
                        "request_id": msg.get("request_id"),
                    }
                    self.request.sendall(json.dumps(response).encode())

            with socketserver.UnixStreamServer(str(socket_path), EchoHandler) as srv:
                srv.handle_request()

        thread = threading.Thread(target=server_thread)
        thread.start()

        # Wait for server to start
        import time

        for _ in range(50):  # 5 second timeout
            if socket_path.exists():
                break
            time.sleep(0.1)

        try:
            response = send_command(
                "test.echo",
                params={"key": "value"},
                socket_path=socket_path,
                request_id="round-trip-1",
            )

            assert response.success is True
            assert response.result == {"echoed": "test.echo"}
            assert response.request_id == "round-trip-1"
        finally:
            thread.join(timeout=5)
            # Clean up socket file
            if socket_path.exists():
                socket_path.unlink()


class TestSocketServer:
    """Tests for SocketServer lifecycle."""

    def test_init_default_path(self):
        """Default socket path is set."""
        server = SocketServer(dispatcher=MagicMock())
        assert server.socket_path == DEFAULT_SOCKET_PATH

    def test_init_custom_path(self, tmp_path):
        """Custom socket path can be provided."""
        custom = tmp_path / "custom.sock"
        server = SocketServer(dispatcher=MagicMock(), socket_path=custom)
        assert server.socket_path == custom

    def test_shutdown_cleans_up(self, tmp_path):
        """Shutdown removes socket file if it exists."""
        socket_path = tmp_path / "cleanup.sock"
        socket_path.touch()  # Create dummy file

        server = SocketServer(dispatcher=MagicMock(), socket_path=socket_path)
        server._server = MagicMock()

        server.shutdown()

        assert not socket_path.exists()


class TestMultiInstance:
    """Tests verifying multi-instance safety (no class variable pollution)."""

    def test_handlers_use_own_server_dispatcher(self):
        """Each handler uses its own server's dispatcher, not a shared class var."""
        # Create two different dispatchers
        dispatcher_a = MagicMock(return_value=CommandResult.ok({"source": "A"}))
        dispatcher_b = MagicMock(return_value=CommandResult.ok({"source": "B"}))

        # Create two mock servers with different dispatchers
        server_a = MagicMock()
        server_a.dispatcher = dispatcher_a
        server_b = MagicMock()
        server_b.dispatcher = dispatcher_b

        # Process request through server A
        mock_request_a = MagicMock()
        mock_request_a.recv.return_value = b'{"command": "test"}'
        sent_a = []
        mock_request_a.sendall = lambda d: sent_a.append(d)
        SocketHandler(mock_request_a, ("", 0), server_a)

        # Process request through server B
        mock_request_b = MagicMock()
        mock_request_b.recv.return_value = b'{"command": "test"}'
        sent_b = []
        mock_request_b.sendall = lambda d: sent_b.append(d)
        SocketHandler(mock_request_b, ("", 0), server_b)

        # Verify each handler used its own server's dispatcher
        dispatcher_a.assert_called_once_with("test", {})
        dispatcher_b.assert_called_once_with("test", {})

        # Verify responses came from correct dispatchers
        response_a = json.loads(sent_a[0])
        response_b = json.loads(sent_b[0])
        assert response_a["result"] == {"source": "A"}
        assert response_b["result"] == {"source": "B"}
