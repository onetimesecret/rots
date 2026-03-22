# src/rots/sidecar/rabbitmq.py

"""RabbitMQ queue consumer for sidecar commands.

Connects to RabbitMQ and consumes from ots.sidecar.commands queue,
publishes responses via reply_to.

RABBITMQ_URL resolution order:
1. RABBITMQ_URL environment variable
2. RABBITMQ_URL from .otsinfra.env (walk-up discovery)
3. RABBITMQ_URL from /etc/default/onetimesecret
4. Default localhost config (guest:guest@127.0.0.1:5672)
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

if TYPE_CHECKING:
    import pika
    from pika.adapters.blocking_connection import BlockingChannel
    from pika.spec import Basic, BasicProperties

    from .commands import CommandResult

logger = logging.getLogger(__name__)

# Default paths and queue names
DEFAULT_ENV_FILE = Path("/etc/default/onetimesecret")
COMMAND_QUEUE = "ots.sidecar.commands"
COMMAND_EXCHANGE = "ots.sidecar"


@dataclass
class RabbitMQConfig:
    """RabbitMQ connection configuration."""

    host: str = "127.0.0.1"
    port: int = 5672
    vhost: str = "/"
    username: str = "guest"
    password: str = "guest"

    @classmethod
    def from_url(cls, url: str) -> RabbitMQConfig:
        """Parse AMQP URL into config.

        Supports: amqp://user:pass@host:port/vhost
        """
        parsed = urlparse(url)
        return cls(
            host=parsed.hostname or "127.0.0.1",
            port=parsed.port or 5672,
            vhost=parsed.path.lstrip("/") or "/",
            username=parsed.username or "guest",
            password=parsed.password or "guest",
        )

    @classmethod
    def from_env_file(cls, path: Path = DEFAULT_ENV_FILE) -> RabbitMQConfig:
        """Load config from environment file.

        Parses shell-style env file looking for RABBITMQ_URL.
        Falls back to defaults if file missing or URL not found.
        """
        if not path.exists():
            logger.warning("Env file %s not found, using defaults", path)
            return cls()

        try:
            content = path.read_text()
            for line in content.splitlines():
                line = line.strip()
                # Skip comments and empty lines
                if not line or line.startswith("#"):
                    continue
                # Parse key=value
                if "=" in line:
                    key, _, value = line.partition("=")
                    key = key.strip()
                    value = value.strip()
                    # Remove quotes if present
                    if (value.startswith('"') and value.endswith('"')) or (
                        value.startswith("'") and value.endswith("'")
                    ):
                        value = value[1:-1]
                    if key == "RABBITMQ_URL":
                        return cls.from_url(value)
        except Exception as e:
            logger.warning("Failed to parse %s: %s", path, e)

        logger.warning("RABBITMQ_URL not found in %s, using defaults", path)
        return cls()

    @classmethod
    def from_environment(cls) -> RabbitMQConfig:
        """Load config from environment variable, .otsinfra.env, or server env file.

        Resolution order:
        1. RABBITMQ_URL environment variable
        2. RABBITMQ_URL from .otsinfra.env (walk-up discovery)
        3. RABBITMQ_URL from /etc/default/onetimesecret
        4. Default localhost config
        """
        # 1. Environment variable
        url = os.environ.get("RABBITMQ_URL")
        if url:
            return cls.from_url(url)

        # 2. Walk-up .otsinfra.env discovery
        try:
            from ots_shared.ssh.env import find_env_file, load_env_file

            env_file = find_env_file()
            if env_file:
                env_data = load_env_file(env_file)
                url = env_data.get("RABBITMQ_URL")
                if url:
                    logger.debug("Using RABBITMQ_URL from %s", env_file)
                    return cls.from_url(url)
        except ImportError:
            pass  # ots_shared not available, skip walk-up discovery

        # 3. Server env file
        return cls.from_env_file()


class RabbitMQConsumer:
    """Consumes commands from RabbitMQ and dispatches to handlers.

    Usage:
        def handler(command: str, payload: dict) -> dict:
            return {"status": "ok", "result": ...}

        consumer = RabbitMQConsumer(handler)
        consumer.start()  # Blocks, processing messages
    """

    def __init__(
        self,
        handler: Callable[[str, dict[str, Any]], CommandResult],
        config: RabbitMQConfig | None = None,
        queue: str = COMMAND_QUEUE,
        exchange: str = COMMAND_EXCHANGE,
    ):
        """Initialize consumer.

        Args:
            handler: Callback receiving (command, payload) and returning CommandResult
            config: RabbitMQ connection config (defaults to loading from env)
            queue: Queue name to consume from
            exchange: Exchange name for routing
        """
        self.handler = handler
        self.config = config or RabbitMQConfig.from_environment()
        self.queue = queue
        self.exchange = exchange
        self._connection: pika.BlockingConnection | None = None
        self._channel: BlockingChannel | None = None
        self._running = False

    def _connect(self) -> None:
        """Establish connection to RabbitMQ."""
        import pika

        credentials = pika.PlainCredentials(self.config.username, self.config.password)
        parameters = pika.ConnectionParameters(
            host=self.config.host,
            port=self.config.port,
            virtual_host=self.config.vhost,
            credentials=credentials,
            heartbeat=600,
            blocked_connection_timeout=300,
        )

        logger.info(
            "Connecting to RabbitMQ at %s:%d/%s",
            self.config.host,
            self.config.port,
            self.config.vhost,
        )
        self._connection = pika.BlockingConnection(parameters)
        self._channel = self._connection.channel()

        # Declare exchange and queue
        self._channel.exchange_declare(
            exchange=self.exchange,
            exchange_type="direct",
            durable=True,
        )
        self._channel.queue_declare(
            queue=self.queue,
            durable=True,
        )
        self._channel.queue_bind(
            queue=self.queue,
            exchange=self.exchange,
            routing_key=self.queue,
        )

        # Prefetch 1 message at a time for fair dispatch
        self._channel.basic_qos(prefetch_count=1)

        logger.info("Connected, consuming from queue: %s", self.queue)

    def _on_message(
        self,
        channel: BlockingChannel,
        method: Basic.Deliver,
        properties: BasicProperties,
        body: bytes,
    ) -> None:
        """Handle incoming message.

        Parses JSON body, dispatches to handler, publishes response if reply_to set.
        """
        correlation_id = properties.correlation_id or "unknown"
        reply_to = properties.reply_to

        logger.debug("Received message: correlation_id=%s", correlation_id)

        try:
            # Parse message body
            message = json.loads(body.decode("utf-8"))
            command = message.get("command", "")
            payload = message.get("payload", {})

            if not command:
                response = {"success": False, "error": "Missing 'command' field"}
            else:
                # Dispatch to handler and convert CommandResult to dict
                result = self.handler(command, payload)
                response = {
                    "success": result.success,
                    "result": result.data,
                    "error": result.error,
                }
                if result.warnings:
                    response["warnings"] = result.warnings

        except json.JSONDecodeError as e:
            logger.warning("Invalid JSON in message: %s", e)
            response = {"success": False, "error": f"Invalid JSON: {e}"}
        except Exception as e:
            logger.exception("Handler error for correlation_id=%s", correlation_id)
            response = {"success": False, "error": str(e)}

        # Send response if reply_to specified
        if reply_to:
            import pika

            response_body = json.dumps(response).encode("utf-8")
            channel.basic_publish(
                exchange="",
                routing_key=reply_to,
                body=response_body,
                properties=pika.BasicProperties(
                    correlation_id=correlation_id,
                    content_type="application/json",
                ),
            )
            logger.debug("Sent response to %s: correlation_id=%s", reply_to, correlation_id)

        # Acknowledge message
        channel.basic_ack(delivery_tag=method.delivery_tag)

    def start(self) -> None:
        """Start consuming messages (blocking).

        Reconnects automatically on connection loss.
        Call stop() from another thread to exit.
        """
        self._running = True

        while self._running:
            try:
                self._connect()
                if self._channel:
                    self._channel.basic_consume(
                        queue=self.queue,
                        on_message_callback=self._on_message,
                    )
                    self._channel.start_consuming()
            except KeyboardInterrupt:
                logger.info("Interrupted, stopping consumer")
                self.stop()
                break
            except Exception as e:
                if not self._running:
                    break
                logger.warning("Connection lost: %s, reconnecting in 5s...", e)
                import time

                time.sleep(5)

    def stop(self) -> None:
        """Stop consuming and close connection."""
        self._running = False
        if self._channel:
            try:
                self._channel.stop_consuming()
            except Exception:
                pass
        if self._connection:
            try:
                self._connection.close()
            except Exception:
                pass
        logger.info("Consumer stopped")


def publish_command(
    command: str,
    payload: dict[str, Any] | None = None,
    config: RabbitMQConfig | None = None,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Publish a command and wait for response.

    This is a utility for clients to send commands to the sidecar.

    Args:
        command: Command name to execute
        payload: Command parameters
        config: RabbitMQ config (defaults to loading from env)
        timeout: Seconds to wait for response

    Returns:
        Response dict from handler

    Raises:
        TimeoutError: If no response within timeout
    """
    import uuid

    import pika

    config = config or RabbitMQConfig.from_environment()
    credentials = pika.PlainCredentials(config.username, config.password)
    parameters = pika.ConnectionParameters(
        host=config.host,
        port=config.port,
        virtual_host=config.vhost,
        credentials=credentials,
    )

    connection = pika.BlockingConnection(parameters)
    channel = connection.channel()

    # Declare callback queue
    result = channel.queue_declare(queue="", exclusive=True)
    callback_queue = result.method.queue

    correlation_id = str(uuid.uuid4())
    response: dict[str, Any] | None = None

    def on_response(
        ch: BlockingChannel,
        method: Basic.Deliver,
        props: BasicProperties,
        body: bytes,
    ) -> None:
        nonlocal response
        if props.correlation_id == correlation_id:
            response = json.loads(body.decode("utf-8"))

    channel.basic_consume(
        queue=callback_queue,
        on_message_callback=on_response,
        auto_ack=True,
    )

    # Publish command
    message = {"command": command, "payload": payload or {}}
    channel.basic_publish(
        exchange=COMMAND_EXCHANGE,
        routing_key=COMMAND_QUEUE,
        body=json.dumps(message).encode("utf-8"),
        properties=pika.BasicProperties(
            reply_to=callback_queue,
            correlation_id=correlation_id,
            content_type="application/json",
        ),
    )

    # Wait for response
    deadline = __import__("time").time() + timeout
    while response is None:
        connection.process_data_events(time_limit=1)
        if __import__("time").time() > deadline:
            connection.close()
            raise TimeoutError(f"No response within {timeout}s")

    connection.close()
    return response
