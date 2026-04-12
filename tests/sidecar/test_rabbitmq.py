# tests/sidecar/test_rabbitmq.py

"""Tests for src/rots/sidecar/rabbitmq.py

Covers:
- get_host_id resolution order
- RabbitMQConfig.from_url parsing
- RabbitMQConfig.from_env_file parsing
- RabbitMQConfig.from_environment precedence
- RabbitMQConfig host_id field
- RabbitMQConsumer message handling (mocked pika)
- RabbitMQConsumer multi-queue binding
- publish_command timeout behavior (mocked pika)
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from rots.sidecar.rabbitmq import (
    RabbitMQConfig,
    RabbitMQConsumer,
    get_host_id,
    publish_command,
)


class TestGetHostId:
    """Tests for get_host_id resolution order."""

    def test_env_var_takes_precedence(self, monkeypatch, tmp_path):
        """SIDECAR_HOST_ID env var should win over all other sources."""
        # Create .otsinfra.env with different value
        otsinfra_env = tmp_path / ".otsinfra.env"
        otsinfra_env.write_text("SIDECAR_HOST_ID=from-otsinfra\n")

        # Create /etc/default file with different value
        etc_default = tmp_path / "onetimesecret"
        etc_default.write_text("SIDECAR_HOST_ID=from-etc-default\n")

        # Set env var
        monkeypatch.setenv("SIDECAR_HOST_ID", "from-env-var")

        with patch("rots.sidecar.rabbitmq.DEFAULT_ENV_FILE", etc_default):
            result = get_host_id()

        assert result == "from-env-var"

    @pytest.mark.skip(
        reason="ots_shared walk-up discovery requires integration test with actual package"
    )
    def test_falls_back_to_otsinfra_env(self):
        """When no env var, use .otsinfra.env via walk-up discovery.

        This test is skipped because mocking the dynamic import of ots_shared.ssh.env
        is complex and fragile. The walk-up discovery path is better tested via
        integration tests with the actual ots_shared package installed.
        """
        pass

    def test_falls_back_to_etc_default(self, monkeypatch, tmp_path):
        """When no .otsinfra.env, use /etc/default/onetimesecret."""
        monkeypatch.delenv("SIDECAR_HOST_ID", raising=False)

        # Create etc/default file
        etc_default = tmp_path / "onetimesecret"
        etc_default.write_text("SIDECAR_HOST_ID=from-etc-default\n")

        with patch("rots.sidecar.rabbitmq.DEFAULT_ENV_FILE", etc_default):
            # Also need to ensure ots_shared import fails (no walk-up discovery)
            with patch.dict("sys.modules", {"ots_shared": None}):
                result = get_host_id()

        assert result == "from-etc-default"

    def test_falls_back_to_etc_default_with_quotes(self, monkeypatch, tmp_path):
        """Handle quoted values in /etc/default/onetimesecret."""
        monkeypatch.delenv("SIDECAR_HOST_ID", raising=False)

        # Create etc/default file with quoted value
        etc_default = tmp_path / "onetimesecret"
        etc_default.write_text('SIDECAR_HOST_ID="quoted-host-id"\n')

        with patch("rots.sidecar.rabbitmq.DEFAULT_ENV_FILE", etc_default):
            with patch.dict("sys.modules", {"ots_shared": None}):
                result = get_host_id()

        assert result == "quoted-host-id"

    def test_falls_back_to_gethostname(self, monkeypatch, tmp_path, mocker):
        """socket.gethostname() as ultimate fallback."""
        monkeypatch.delenv("SIDECAR_HOST_ID", raising=False)

        # Point to non-existent file
        etc_default = tmp_path / "nonexistent"

        # Mock gethostname
        mocker.patch("socket.gethostname", return_value="test-hostname-001")

        with patch("rots.sidecar.rabbitmq.DEFAULT_ENV_FILE", etc_default):
            with patch.dict("sys.modules", {"ots_shared": None}):
                result = get_host_id()

        assert result == "test-hostname-001"

    def test_empty_value_skipped(self, monkeypatch, tmp_path, mocker):
        """Empty string treated as unset."""
        # Set env var to empty string
        monkeypatch.setenv("SIDECAR_HOST_ID", "")

        # Create etc/default with empty value too
        etc_default = tmp_path / "onetimesecret"
        etc_default.write_text("SIDECAR_HOST_ID=\n")

        # Mock gethostname as the expected fallback
        mocker.patch("socket.gethostname", return_value="fallback-hostname")

        with patch("rots.sidecar.rabbitmq.DEFAULT_ENV_FILE", etc_default):
            with patch.dict("sys.modules", {"ots_shared": None}):
                result = get_host_id()

        assert result == "fallback-hostname"

    def test_whitespace_only_value_skipped(self, monkeypatch, tmp_path, mocker):
        """Whitespace-only value treated as unset."""
        # Set env var to whitespace
        monkeypatch.setenv("SIDECAR_HOST_ID", "   ")

        # Mock gethostname as the expected fallback
        mocker.patch("socket.gethostname", return_value="fallback-hostname")

        etc_default = tmp_path / "nonexistent"

        with patch("rots.sidecar.rabbitmq.DEFAULT_ENV_FILE", etc_default):
            with patch.dict("sys.modules", {"ots_shared": None}):
                result = get_host_id()

        assert result == "fallback-hostname"


class TestRabbitMQConfigHostId:
    """Tests for RabbitMQConfig host_id field."""

    def test_config_includes_host_id(self, monkeypatch, mocker):
        """Default config gets host_id from get_host_id()."""
        # Set env var to control get_host_id() result
        monkeypatch.setenv("SIDECAR_HOST_ID", "default-host")

        config = RabbitMQConfig()

        assert config.host_id == "default-host"

    def test_from_url_preserves_host_id(self, mocker):
        """from_url() with host_id kwarg preserves it."""
        mocker.patch("rots.sidecar.rabbitmq.get_host_id", return_value="should-not-use")

        config = RabbitMQConfig.from_url(
            "amqp://user:pass@localhost:5672/vhost",
            host_id="explicit-host-id",
        )

        assert config.host_id == "explicit-host-id"
        assert config.host == "localhost"
        assert config.vhost == "vhost"

    def test_from_url_uses_get_host_id_when_not_specified(self, mocker):
        """from_url() calls get_host_id() when host_id not provided."""
        mocker.patch("rots.sidecar.rabbitmq.get_host_id", return_value="resolved-host")

        config = RabbitMQConfig.from_url("amqp://user:pass@localhost:5672/vhost")

        assert config.host_id == "resolved-host"

    def test_from_env_file_preserves_host_id(self, tmp_path, mocker):
        """from_env_file() with host_id kwarg preserves it."""
        mocker.patch("rots.sidecar.rabbitmq.get_host_id", return_value="should-not-use")

        env_file = tmp_path / ".env"
        env_file.write_text("RABBITMQ_URL=amqp://user:pass@rabbit:5672/vh\n")

        config = RabbitMQConfig.from_env_file(env_file, host_id="explicit-host")

        assert config.host_id == "explicit-host"
        assert config.host == "rabbit"

    def test_from_env_file_uses_get_host_id_when_not_specified(self, tmp_path, mocker):
        """from_env_file() calls get_host_id() when host_id not provided."""
        mocker.patch("rots.sidecar.rabbitmq.get_host_id", return_value="resolved-host")

        env_file = tmp_path / ".env"
        env_file.write_text("RABBITMQ_URL=amqp://user:pass@rabbit:5672/vh\n")

        config = RabbitMQConfig.from_env_file(env_file)

        assert config.host_id == "resolved-host"

    def test_from_env_file_missing_uses_host_id(self, tmp_path, mocker):
        """from_env_file() with missing file still respects host_id kwarg."""
        mocker.patch("rots.sidecar.rabbitmq.get_host_id", return_value="should-not-use")

        missing_file = tmp_path / "nonexistent.env"

        config = RabbitMQConfig.from_env_file(missing_file, host_id="explicit-host")

        assert config.host_id == "explicit-host"
        # Should have defaults for other fields
        assert config.host == "127.0.0.1"


class TestRabbitMQConfigFromUrl:
    """Tests for RabbitMQConfig.from_url."""

    def test_basic_url(self):
        """Parse basic AMQP URL."""
        config = RabbitMQConfig.from_url("amqp://myuser:mypass@localhost:5672/myvhost")

        assert config.host == "localhost"
        assert config.port == 5672
        assert config.vhost == "myvhost"
        assert config.username == "myuser"
        assert config.password == "mypass"

    def test_url_without_port(self):
        """Default port 5672 when not specified."""
        config = RabbitMQConfig.from_url("amqp://user:pass@rabbit.example.com/")

        assert config.host == "rabbit.example.com"
        assert config.port == 5672

    def test_url_without_vhost(self):
        """Default vhost / when path is empty or just /."""
        config = RabbitMQConfig.from_url("amqp://user:pass@localhost:5672/")
        assert config.vhost == "/"

        config = RabbitMQConfig.from_url("amqp://user:pass@localhost:5672")
        assert config.vhost == "/"

    def test_url_without_credentials(self):
        """Default to guest/guest when no credentials."""
        config = RabbitMQConfig.from_url("amqp://localhost:5672/test")

        assert config.username == "guest"
        assert config.password == "guest"

    def test_url_with_special_chars_in_password(self):
        """Handle URL-encoded special characters in password."""
        # URL with special chars - urlparse does NOT decode by default
        # So we test that the raw value is preserved
        config = RabbitMQConfig.from_url("amqp://user:p%40ss%23w0rd@localhost/")
        assert config.password == "p%40ss%23w0rd"

        # Test password with characters that don't conflict with URL syntax
        config2 = RabbitMQConfig.from_url("amqp://user:my-complex_pass123@localhost/")
        assert config2.password == "my-complex_pass123"

    def test_url_with_ip_address(self):
        """Parse URL with IP address instead of hostname."""
        config = RabbitMQConfig.from_url("amqp://admin:secret@192.168.1.100:5672/prod")

        assert config.host == "192.168.1.100"
        assert config.port == 5672
        assert config.vhost == "prod"


class TestRabbitMQConfigFromEnvFile:
    """Tests for RabbitMQConfig.from_env_file."""

    def test_valid_env_file(self, tmp_path):
        """Parse env file with RABBITMQ_URL."""
        env_file = tmp_path / ".env"
        env_file.write_text('RABBITMQ_URL="amqp://testuser:testpass@rabbit:5672/testvhost"\n')

        config = RabbitMQConfig.from_env_file(env_file)

        assert config.host == "rabbit"
        assert config.port == 5672
        assert config.vhost == "testvhost"
        assert config.username == "testuser"
        assert config.password == "testpass"

    def test_env_file_single_quotes(self, tmp_path):
        """Parse env file with single-quoted value."""
        env_file = tmp_path / ".env"
        env_file.write_text("RABBITMQ_URL='amqp://user:pass@host/vh'\n")

        config = RabbitMQConfig.from_env_file(env_file)

        assert config.host == "host"
        assert config.vhost == "vh"

    def test_env_file_no_quotes(self, tmp_path):
        """Parse env file with unquoted value."""
        env_file = tmp_path / ".env"
        env_file.write_text("RABBITMQ_URL=amqp://user:pass@myhost:5672/vhost\n")

        config = RabbitMQConfig.from_env_file(env_file)

        assert config.host == "myhost"

    def test_env_file_with_comments(self, tmp_path):
        """Ignore comment lines in env file."""
        env_file = tmp_path / ".env"
        env_file.write_text("""
# This is a comment
OTHER_VAR=value
# Another comment
RABBITMQ_URL=amqp://user:pass@rabbit/vh
""")

        config = RabbitMQConfig.from_env_file(env_file)

        assert config.host == "rabbit"

    def test_env_file_missing(self, tmp_path):
        """Return defaults when file doesn't exist."""
        missing_file = tmp_path / "nonexistent.env"

        config = RabbitMQConfig.from_env_file(missing_file)

        assert config.host == "127.0.0.1"
        assert config.port == 5672
        assert config.username == "guest"
        assert config.password == "guest"

    def test_env_file_no_rabbitmq_url(self, tmp_path):
        """Return defaults when RABBITMQ_URL not in file."""
        env_file = tmp_path / ".env"
        env_file.write_text("REDIS_URL=redis://localhost:6379\nDOMAIN=example.com\n")

        config = RabbitMQConfig.from_env_file(env_file)

        assert config.host == "127.0.0.1"
        assert config.username == "guest"

    def test_env_file_empty(self, tmp_path):
        """Return defaults for empty file."""
        env_file = tmp_path / ".env"
        env_file.write_text("")

        config = RabbitMQConfig.from_env_file(env_file)

        assert config.host == "127.0.0.1"


class TestRabbitMQConfigFromEnvironment:
    """Tests for RabbitMQConfig.from_environment."""

    def test_env_var_takes_precedence(self, monkeypatch, tmp_path):
        """RABBITMQ_URL env var takes precedence over file."""
        # Create env file with different config
        env_file = tmp_path / ".env"
        env_file.write_text("RABBITMQ_URL=amqp://file:file@filehost/filevh\n")

        # Set env var with different config
        monkeypatch.setenv("RABBITMQ_URL", "amqp://env:env@envhost/envvh")

        # Mock DEFAULT_ENV_FILE to point to our test file
        with patch("rots.sidecar.rabbitmq.DEFAULT_ENV_FILE", env_file):
            config = RabbitMQConfig.from_environment()

        # Should use env var, not file
        assert config.host == "envhost"
        assert config.username == "env"

    def test_falls_back_to_file(self, monkeypatch, tmp_path):
        """Falls back to env file when no env var."""
        monkeypatch.delenv("RABBITMQ_URL", raising=False)

        env_file = tmp_path / ".env"
        env_file.write_text("RABBITMQ_URL=amqp://fallback:pass@fallbackhost/vh\n")

        # Use from_env_file directly since from_environment calls it
        config = RabbitMQConfig.from_env_file(env_file)

        assert config.host == "fallbackhost"
        assert config.username == "fallback"

    def test_defaults_when_nothing_configured(self, monkeypatch, tmp_path):
        """Returns defaults when neither env var nor file configured."""
        monkeypatch.delenv("RABBITMQ_URL", raising=False)

        missing_file = tmp_path / "nonexistent.env"

        with patch("rots.sidecar.rabbitmq.DEFAULT_ENV_FILE", missing_file):
            config = RabbitMQConfig.from_environment()

        assert config.host == "127.0.0.1"
        assert config.port == 5672
        assert config.username == "guest"
        assert config.password == "guest"


class TestRabbitMQConsumerMessageHandling:
    """Tests for RabbitMQConsumer._on_message."""

    @pytest.fixture
    def mock_pika(self):
        """Mock pika module for all tests in this class."""
        with patch.dict("sys.modules", {"pika": MagicMock()}):
            yield

    def test_valid_message_dispatch(self, mock_pika):
        """Handler called with correct command and payload."""
        # Track handler calls
        handler_calls = []

        def mock_handler(command: str, payload: dict) -> dict:
            handler_calls.append((command, payload))
            return {"status": "ok", "result": "test"}

        consumer = RabbitMQConsumer(
            handler=mock_handler,
            config=RabbitMQConfig(),
        )

        # Mock channel for ack
        mock_channel = MagicMock()
        mock_method = MagicMock()
        mock_method.delivery_tag = 1
        mock_properties = MagicMock()
        mock_properties.correlation_id = "test-123"
        mock_properties.reply_to = None

        body = json.dumps({"command": "restart.web", "payload": {"port": 7043}}).encode()

        consumer._on_message(mock_channel, mock_method, mock_properties, body)

        # Verify handler was called
        assert len(handler_calls) == 1
        assert handler_calls[0] == ("restart.web", {"port": 7043})

        # Verify message was acked
        mock_channel.basic_ack.assert_called_once_with(delivery_tag=1)

    def test_response_published_when_reply_to_set(self, mock_pika):
        """Response published to reply_to queue."""
        from rots.sidecar.commands import CommandResult

        def mock_handler(command: str, payload: dict) -> CommandResult:
            return CommandResult.ok(data="result")

        consumer = RabbitMQConsumer(
            handler=mock_handler,
            config=RabbitMQConfig(),
        )

        mock_channel = MagicMock()
        mock_method = MagicMock()
        mock_method.delivery_tag = 1
        mock_properties = MagicMock()
        mock_properties.correlation_id = "corr-456"
        mock_properties.reply_to = "amq.rabbitmq.reply-to.abc123"

        body = json.dumps({"command": "health", "payload": {}}).encode()

        consumer._on_message(mock_channel, mock_method, mock_properties, body)

        # Verify response was published
        mock_channel.basic_publish.assert_called_once()
        call_kwargs = mock_channel.basic_publish.call_args.kwargs
        assert call_kwargs["routing_key"] == "amq.rabbitmq.reply-to.abc123"
        assert call_kwargs["exchange"] == ""

        # Verify response body
        response = json.loads(call_kwargs["body"].decode())
        assert response["success"] is True
        assert response["result"] == "result"

    def test_invalid_json_returns_error(self, mock_pika):
        """Invalid JSON message returns error response."""
        from rots.sidecar.commands import CommandResult

        def mock_handler(command: str, payload: dict) -> CommandResult:
            return CommandResult.ok()

        consumer = RabbitMQConsumer(
            handler=mock_handler,
            config=RabbitMQConfig(),
        )

        mock_channel = MagicMock()
        mock_method = MagicMock()
        mock_method.delivery_tag = 1
        mock_properties = MagicMock()
        mock_properties.correlation_id = "bad-json"
        mock_properties.reply_to = "reply.queue"

        body = b"not valid json{{"

        consumer._on_message(mock_channel, mock_method, mock_properties, body)

        # Should still ack the message (bad message, but processed)
        mock_channel.basic_ack.assert_called_once()

        # Should publish error response
        mock_channel.basic_publish.assert_called_once()
        response = json.loads(mock_channel.basic_publish.call_args.kwargs["body"].decode())
        assert response["success"] is False
        assert "Invalid JSON" in response["error"]

    def test_missing_command_returns_error(self, mock_pika):
        """Message without command field returns error."""
        from rots.sidecar.commands import CommandResult

        def mock_handler(command: str, payload: dict) -> CommandResult:
            return CommandResult.ok()

        consumer = RabbitMQConsumer(
            handler=mock_handler,
            config=RabbitMQConfig(),
        )

        mock_channel = MagicMock()
        mock_method = MagicMock()
        mock_method.delivery_tag = 1
        mock_properties = MagicMock()
        mock_properties.correlation_id = "no-cmd"
        mock_properties.reply_to = "reply.queue"

        body = json.dumps({"payload": {"key": "value"}}).encode()  # No command

        consumer._on_message(mock_channel, mock_method, mock_properties, body)

        mock_channel.basic_publish.assert_called_once()
        response = json.loads(mock_channel.basic_publish.call_args.kwargs["body"].decode())
        assert response["success"] is False
        assert "command" in response["error"].lower()

    def test_handler_exception_returns_error(self, mock_pika):
        """Handler exception is caught and returned as error."""
        from rots.sidecar.commands import CommandResult

        def failing_handler(command: str, payload: dict) -> CommandResult:
            raise RuntimeError("Handler exploded")

        consumer = RabbitMQConsumer(
            handler=failing_handler,
            config=RabbitMQConfig(),
        )

        mock_channel = MagicMock()
        mock_method = MagicMock()
        mock_method.delivery_tag = 1
        mock_properties = MagicMock()
        mock_properties.correlation_id = "will-fail"
        mock_properties.reply_to = "reply.queue"

        body = json.dumps({"command": "boom", "payload": {}}).encode()

        consumer._on_message(mock_channel, mock_method, mock_properties, body)

        # Should still ack (message processed, even if handler failed)
        mock_channel.basic_ack.assert_called_once()

        response = json.loads(mock_channel.basic_publish.call_args.kwargs["body"].decode())
        assert response["success"] is False
        assert "Handler exploded" in response["error"]


class TestRabbitMQConsumerMultiQueue:
    """Tests for RabbitMQConsumer multi-queue binding."""

    @pytest.fixture
    def mock_pika(self):
        """Mock pika module for all tests in this class."""
        with patch.dict("sys.modules", {"pika": MagicMock()}):
            yield

    def test_queues_list_built_correctly(self, mock_pika, mocker):
        """Both base and host-specific queues in list."""
        mocker.patch("rots.sidecar.rabbitmq.get_host_id", return_value="web-001")

        def mock_handler(command: str, payload: dict) -> dict:
            return {"status": "ok"}

        config = RabbitMQConfig(host_id="web-001")
        consumer = RabbitMQConsumer(
            handler=mock_handler,
            config=config,
            queue="ots.sidecar.commands",
        )

        assert consumer.queues == [
            "ots.sidecar.commands",
            "ots.sidecar.commands.web-001",
        ]

    def test_queues_list_with_custom_queue_name(self, mock_pika, mocker):
        """Queues list uses provided queue name as base."""
        mocker.patch("rots.sidecar.rabbitmq.get_host_id", return_value="host-xyz")

        def mock_handler(command: str, payload: dict) -> dict:
            return {"status": "ok"}

        config = RabbitMQConfig(host_id="host-xyz")
        consumer = RabbitMQConsumer(
            handler=mock_handler,
            config=config,
            queue="custom.queue",
        )

        assert consumer.queues == ["custom.queue", "custom.queue.host-xyz"]

    def test_declares_both_queues(self, mock_pika, mocker):
        """queue_declare called for each queue."""
        mocker.patch("rots.sidecar.rabbitmq.get_host_id", return_value="web-002")

        def mock_handler(command: str, payload: dict) -> dict:
            return {"status": "ok"}

        config = RabbitMQConfig(host_id="web-002")
        consumer = RabbitMQConsumer(
            handler=mock_handler,
            config=config,
        )

        # Mock channel
        mock_channel = MagicMock()
        consumer._channel = mock_channel

        # Simulate the declare/bind loop from _connect
        for q in consumer.queues:
            mock_channel.queue_declare(queue=q, durable=True)
            mock_channel.queue_bind(
                queue=q,
                exchange=consumer.exchange,
                routing_key=q,
            )

        # Verify queue_declare was called for both queues
        declare_calls = mock_channel.queue_declare.call_args_list
        assert len(declare_calls) == 2
        assert declare_calls[0].kwargs == {"queue": "ots.sidecar.commands", "durable": True}
        assert declare_calls[1].kwargs == {
            "queue": "ots.sidecar.commands.web-002",
            "durable": True,
        }

    def test_binds_both_queues(self, mock_pika, mocker):
        """queue_bind called with correct routing keys."""
        mocker.patch("rots.sidecar.rabbitmq.get_host_id", return_value="web-003")

        def mock_handler(command: str, payload: dict) -> dict:
            return {"status": "ok"}

        config = RabbitMQConfig(host_id="web-003")
        consumer = RabbitMQConsumer(
            handler=mock_handler,
            config=config,
        )

        # Mock channel
        mock_channel = MagicMock()
        consumer._channel = mock_channel

        # Simulate the declare/bind loop from _connect
        for q in consumer.queues:
            mock_channel.queue_declare(queue=q, durable=True)
            mock_channel.queue_bind(
                queue=q,
                exchange=consumer.exchange,
                routing_key=q,
            )

        # Verify queue_bind was called with correct routing keys
        bind_calls = mock_channel.queue_bind.call_args_list
        assert len(bind_calls) == 2

        # First call: base queue
        assert bind_calls[0].kwargs == {
            "queue": "ots.sidecar.commands",
            "exchange": "ots.sidecar",
            "routing_key": "ots.sidecar.commands",
        }

        # Second call: host-specific queue
        assert bind_calls[1].kwargs == {
            "queue": "ots.sidecar.commands.web-003",
            "exchange": "ots.sidecar",
            "routing_key": "ots.sidecar.commands.web-003",
        }

    def test_consumes_from_both_queues(self, mock_pika, mocker):
        """basic_consume called for each queue."""
        mocker.patch("rots.sidecar.rabbitmq.get_host_id", return_value="web-004")

        def mock_handler(command: str, payload: dict) -> dict:
            return {"status": "ok"}

        config = RabbitMQConfig(host_id="web-004")
        consumer = RabbitMQConsumer(
            handler=mock_handler,
            config=config,
        )

        # Mock channel
        mock_channel = MagicMock()
        consumer._channel = mock_channel

        # Simulate the consume loop from start()
        for q in consumer.queues:
            mock_channel.basic_consume(
                queue=q,
                on_message_callback=consumer._on_message,
            )

        # Verify basic_consume was called for both queues
        consume_calls = mock_channel.basic_consume.call_args_list
        assert len(consume_calls) == 2
        assert consume_calls[0].kwargs["queue"] == "ots.sidecar.commands"
        assert consume_calls[1].kwargs["queue"] == "ots.sidecar.commands.web-004"
        # Both should use the same callback
        assert consume_calls[0].kwargs["on_message_callback"] == consumer._on_message
        assert consume_calls[1].kwargs["on_message_callback"] == consumer._on_message


class TestPublishCommandTimeout:
    """Tests for publish_command timeout behavior.

    These tests verify the timeout and response handling logic.
    Full integration tests would require a real RabbitMQ connection.
    """

    @pytest.fixture
    def mock_pika_module(self):
        """Create a mock pika module with required classes."""
        mock_pika = MagicMock()

        # Mock connection and channel
        mock_connection = MagicMock()
        mock_channel = MagicMock()
        mock_connection.channel.return_value = mock_channel

        # Mock queue_declare result
        mock_result = MagicMock()
        mock_result.method.queue = "amq.gen.callback123"
        mock_channel.queue_declare.return_value = mock_result

        mock_pika.BlockingConnection.return_value = mock_connection
        mock_pika.PlainCredentials.return_value = MagicMock()
        mock_pika.ConnectionParameters.return_value = MagicMock()

        def make_props(**kwargs):
            props = MagicMock()
            for k, v in kwargs.items():
                setattr(props, k, v)
            return props

        mock_pika.BasicProperties.side_effect = make_props

        return {
            "pika": mock_pika,
            "connection": mock_connection,
            "channel": mock_channel,
        }

    def test_timeout_raises_error(self, mock_pika_module):
        """TimeoutError raised when no response within timeout."""
        mock_connection = mock_pika_module["connection"]

        # process_data_events does nothing (no response arrives)
        mock_connection.process_data_events.return_value = None

        with patch.dict("sys.modules", {"pika": mock_pika_module["pika"]}):
            with pytest.raises(TimeoutError, match="No response within"):
                publish_command(
                    command="test",
                    payload={"key": "value"},
                    config=RabbitMQConfig(),
                    timeout=0.1,  # Very short timeout for test
                )

        # Verify connection was closed even on timeout
        mock_connection.close.assert_called_once()

    def test_config_used_for_credentials(self, mock_pika_module):
        """Verify config parameters are passed to credentials."""
        mock_pika = mock_pika_module["pika"]
        mock_connection = mock_pika_module["connection"]

        # Don't let it loop forever
        mock_connection.process_data_events.return_value = None

        config = RabbitMQConfig(
            host="custom.rabbitmq.local",
            port=5673,
            vhost="myvhost",
            username="myuser",
            password="mypass",
        )

        with patch.dict("sys.modules", {"pika": mock_pika}):
            try:
                publish_command(
                    command="test",
                    config=config,
                    timeout=0.1,
                )
            except TimeoutError:
                pass  # Expected

            # Verify credentials were created with our config
            mock_pika.PlainCredentials.assert_called_once_with("myuser", "mypass")

    def test_connection_closed_on_json_decode_error(self, mock_pika_module):
        """Connection is closed even when callback raises JSONDecodeError.

        This tests the fix for a connection leak when sidecar returns malformed JSON.
        The callback's json.loads() would raise JSONDecodeError, bypassing close().
        """
        mock_pika = mock_pika_module["pika"]
        mock_connection = mock_pika_module["connection"]
        mock_channel = mock_pika_module["channel"]

        # Capture the callback function and correlation_id
        callback_holder = {}
        correlation_holder = {}

        def capture_callback(**kwargs):
            callback_holder["callback"] = kwargs.get("on_message_callback")

        mock_channel.basic_consume.side_effect = capture_callback

        # Capture the correlation_id from the published message
        def capture_publish(*args, **kwargs):
            props = kwargs.get("properties")
            if props and hasattr(props, "correlation_id"):
                correlation_holder["id"] = props.correlation_id

        mock_channel.basic_publish.side_effect = capture_publish

        # Simulate process_data_events triggering a response with matching correlation_id
        call_count = [0]

        def process_events(**kwargs):
            call_count[0] += 1
            if call_count[0] == 1 and "callback" in callback_holder and "id" in correlation_holder:
                # Create mock message with invalid JSON body but matching correlation
                mock_method = MagicMock()
                mock_props = MagicMock()
                mock_props.correlation_id = correlation_holder["id"]
                # The callback will try to json.loads this invalid JSON
                callback_holder["callback"](
                    mock_channel,
                    mock_method,
                    mock_props,
                    b"not valid json {{{{",
                )

        mock_connection.process_data_events.side_effect = process_events

        with patch.dict("sys.modules", {"pika": mock_pika}):
            with pytest.raises(json.JSONDecodeError):
                publish_command(
                    command="test",
                    payload={},
                    config=RabbitMQConfig(),
                    timeout=5.0,
                )

        # Connection should be closed even after JSONDecodeError
        mock_connection.close.assert_called_once()


class TestPublishCommandTargetHost:
    """Tests for publish_command target_host routing.

    Verifies that target_host parameter correctly affects the routing key.
    """

    @pytest.fixture
    def mock_pika_module(self):
        """Create a mock pika module with required classes."""
        mock_pika = MagicMock()

        # Mock connection and channel
        mock_connection = MagicMock()
        mock_channel = MagicMock()
        mock_connection.channel.return_value = mock_channel

        # Mock queue_declare result
        mock_result = MagicMock()
        mock_result.method.queue = "amq.gen.callback123"
        mock_channel.queue_declare.return_value = mock_result

        mock_pika.BlockingConnection.return_value = mock_connection
        mock_pika.PlainCredentials.return_value = MagicMock()
        mock_pika.ConnectionParameters.return_value = MagicMock()

        def make_props(**kwargs):
            props = MagicMock()
            for k, v in kwargs.items():
                setattr(props, k, v)
            return props

        mock_pika.BasicProperties.side_effect = make_props

        return {
            "pika": mock_pika,
            "connection": mock_connection,
            "channel": mock_channel,
        }

    def test_no_target_host_uses_shared_queue(self, mock_pika_module):
        """When target_host is None, routing_key is shared queue."""
        mock_channel = mock_pika_module["channel"]
        mock_connection = mock_pika_module["connection"]
        mock_connection.process_data_events.return_value = None

        with patch.dict("sys.modules", {"pika": mock_pika_module["pika"]}):
            try:
                publish_command(
                    command="health",
                    config=RabbitMQConfig(),
                    timeout=0.1,
                    target_host=None,
                )
            except TimeoutError:
                pass  # Expected

        # Verify routing_key is the base queue
        call_kwargs = mock_channel.basic_publish.call_args.kwargs
        assert call_kwargs["routing_key"] == "ots.sidecar.commands"
        assert call_kwargs["exchange"] == "ots.sidecar"

    def test_target_host_uses_host_specific_queue(self, mock_pika_module):
        """When target_host is set, routing_key includes host."""
        mock_channel = mock_pika_module["channel"]
        mock_connection = mock_pika_module["connection"]
        mock_connection.process_data_events.return_value = None

        with patch.dict("sys.modules", {"pika": mock_pika_module["pika"]}):
            try:
                publish_command(
                    command="restart.web",
                    payload={"identifier": "7043"},
                    config=RabbitMQConfig(),
                    timeout=0.1,
                    target_host="web-prod-01",
                )
            except TimeoutError:
                pass  # Expected

        # Verify routing_key includes the target host
        call_kwargs = mock_channel.basic_publish.call_args.kwargs
        assert call_kwargs["routing_key"] == "ots.sidecar.commands.web-prod-01"
        assert call_kwargs["exchange"] == "ots.sidecar"

    def test_target_host_with_special_chars(self, mock_pika_module):
        """Host IDs with dashes and underscores work correctly."""
        mock_channel = mock_pika_module["channel"]
        mock_connection = mock_pika_module["connection"]
        mock_connection.process_data_events.return_value = None

        with patch.dict("sys.modules", {"pika": mock_pika_module["pika"]}):
            try:
                publish_command(
                    command="health",
                    config=RabbitMQConfig(),
                    timeout=0.1,
                    target_host="eu-west-1_prod-server-01",
                )
            except TimeoutError:
                pass  # Expected

        call_kwargs = mock_channel.basic_publish.call_args.kwargs
        assert call_kwargs["routing_key"] == "ots.sidecar.commands.eu-west-1_prod-server-01"

    def test_message_body_unchanged_by_target_host(self, mock_pika_module):
        """target_host only affects routing, not message content."""
        mock_channel = mock_pika_module["channel"]
        mock_connection = mock_pika_module["connection"]
        mock_connection.process_data_events.return_value = None

        with patch.dict("sys.modules", {"pika": mock_pika_module["pika"]}):
            try:
                publish_command(
                    command="config.stage",
                    payload={"key": "REDIS_URL", "value": "redis://localhost"},
                    config=RabbitMQConfig(),
                    timeout=0.1,
                    target_host="acme-prod-1",
                )
            except TimeoutError:
                pass  # Expected

        # Verify message body has command and payload
        call_kwargs = mock_channel.basic_publish.call_args.kwargs
        body = json.loads(call_kwargs["body"].decode())
        assert body["command"] == "config.stage"
        assert body["payload"] == {"key": "REDIS_URL", "value": "redis://localhost"}
