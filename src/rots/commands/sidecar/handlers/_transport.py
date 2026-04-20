# src/rots/commands/sidecar/handlers/_transport.py

"""Transport abstraction for sidecar-to-sidecar RPC.

The operator command (``rots env bootstrap``) addresses sidecars by host_id
and expects a ``CommandResult`` back. Production flows through RabbitMQ via
the existing :func:`rots.sidecar.rabbitmq.publish_command`. Tests need the
same call surface without a broker, so they use :class:`InProcessRpcClient`
to route publishes to in-memory dispatchers.

Both clients implement the :class:`RpcClient` protocol so downstream impl
agents (postgres / valkey / backup handlers) program against the protocol,
not against a concrete transport, and tests can swap in the in-process
version without touching handler code.

Example (production)::

    from rots.sidecar.rabbitmq import RabbitMQConfig
    from rots.commands.sidecar.handlers._transport import RabbitMqRpcClient

    client = RabbitMqRpcClient(RabbitMQConfig.from_environment())
    result = client.publish(
        host_id="eu-demo-web",
        command="secrets.deliver",
        params={"name": "PG_PASSWORD", "value": "..."},
        timeout=30.0,
    )
    assert result.success

Example (test)::

    from rots.commands.sidecar.handlers._transport import InProcessRpcClient
    from rots.sidecar.commands import dispatch

    bus = InProcessRpcClient({"web-host": dispatch, "db-host": dispatch})
    result = bus.publish("web-host", "secrets.deliver", {...}, timeout=5.0)
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from rots.sidecar.commands import CommandResult


@runtime_checkable
class RpcClient(Protocol):
    """Protocol for delivering commands to a target sidecar by host_id.

    Implementations MUST:

    * Accept an arbitrary ``command`` string. The target dispatcher owns
      validation — unknown commands come back as ``CommandResult.fail(...)``.
    * Return a :class:`CommandResult` constructed from the remote response.
    * Raise :class:`TimeoutError` if ``timeout`` seconds pass without a reply.

    Implementations SHOULD:

    * Treat ``host_id`` as an opaque routing key. Do not parse or interpret.
    * Be thread-safe enough for the operator command's sequential use (the
      command does not parallelise publishes).
    """

    def publish(
        self,
        host_id: str,
        command: str,
        params: dict[str, Any],
        *,
        timeout: float = 30.0,
    ) -> CommandResult:
        """Send ``command`` with ``params`` to the sidecar on ``host_id``.

        Args:
            host_id: Routing target. Matches the target sidecar's
                ``SIDECAR_HOST_ID`` / hostname.
            command: Dotted command name, e.g. ``"secrets.deliver"``.
            params: JSON-serialisable parameters for the remote handler.
            timeout: Seconds to wait for a reply before raising
                :class:`TimeoutError`. Default 30 s.

        Returns:
            :class:`CommandResult` reconstructed from the remote response.
            ``result.success`` is the remote handler's ``success``;
            ``result.data`` is the remote handler's ``data``; ``result.error``
            is the remote handler's ``error``; warnings propagate too.

        Raises:
            TimeoutError: No reply within ``timeout`` seconds.
            RuntimeError: Transport-level failure (broker down, etc.).
        """
        ...


class RabbitMqRpcClient:
    """Production RPC client backed by :mod:`rots.sidecar.rabbitmq`.

    Thin adapter around :func:`publish_command` that reshapes the ``dict``
    response into a :class:`CommandResult`. Does not cache the connection —
    each ``publish`` opens and closes a blocking connection, matching
    the existing CLI behaviour (``rots sidecar publish``).
    """

    def __init__(self, config: Any | None = None) -> None:
        """Create a client.

        Args:
            config: Optional ``RabbitMQConfig``. When ``None`` the helper
                loads it via ``RabbitMQConfig.from_environment()`` on each
                publish. Kept as ``Any`` here to avoid importing pika at
                module load time.
        """
        self._config = config

    def publish(
        self,
        host_id: str,
        command: str,
        params: dict[str, Any],
        *,
        timeout: float = 30.0,
    ) -> CommandResult:
        # Deferred import — pika is not on the test path for unit tests and
        # we want this module safe to import without it.
        from rots.sidecar.rabbitmq import RabbitMQConfig, publish_command

        config = self._config or RabbitMQConfig.from_environment()
        response = publish_command(
            command,
            params,
            config=config,
            timeout=timeout,
            target_host=host_id,
        )
        return _result_from_response(response)


@dataclass
class _Peer:
    """In-process peer registration for :class:`InProcessRpcClient`."""

    host_id: str
    dispatch: Callable[[str, dict[str, Any]], CommandResult]


class InProcessRpcClient:
    """In-memory RPC client for tests — routes ``publish`` to local dispatchers.

    Intended for:

    * Integration-style tests that cover operator-command -> handler loops
      without running RabbitMQ.
    * The ``in_process_bus`` pytest fixture (see
      ``tests/commands/sidecar/handlers/conftest.py``).

    Not intended for production. The class does not serialise/deserialise
    its params — handlers see the exact dict the caller passed.

    Example::

        from rots.sidecar.commands import dispatch as local_dispatch

        bus = InProcessRpcClient({
            "db-host": local_dispatch,
            "web-host": local_dispatch,
        })
        result = bus.publish(
            "web-host",
            "secrets.deliver",
            {"name": "PG_PASSWORD", "value": "abc123"},
            timeout=5.0,
        )
    """

    def __init__(
        self,
        peers: dict[str, Callable[[str, dict[str, Any]], CommandResult]] | None = None,
    ) -> None:
        """Create a bus with the given peers.

        Args:
            peers: Mapping of ``host_id`` to dispatcher callable. The
                callable must accept ``(command, params)`` and return a
                :class:`CommandResult` — the same signature as
                :func:`rots.sidecar.commands.dispatch`. Omit to start empty
                and use :meth:`register`.
        """
        self._peers: dict[str, _Peer] = {}
        if peers:
            for host_id, dispatcher in peers.items():
                self.register(host_id, dispatcher)

    def register(
        self,
        host_id: str,
        dispatcher: Callable[[str, dict[str, Any]], CommandResult],
    ) -> None:
        """Add (or replace) a peer.

        Args:
            host_id: Routing key.
            dispatcher: ``(command, params) -> CommandResult`` callable.
        """
        self._peers[host_id] = _Peer(host_id=host_id, dispatch=dispatcher)

    def peers(self) -> list[str]:
        """Return the list of registered host_ids (test helper)."""
        return sorted(self._peers.keys())

    def publish(
        self,
        host_id: str,
        command: str,
        params: dict[str, Any],
        *,
        timeout: float = 30.0,
    ) -> CommandResult:
        """Deliver ``command`` to the peer registered for ``host_id``.

        ``timeout`` is accepted for protocol-compatibility but ignored —
        the dispatch is synchronous.

        Raises:
            KeyError: if no peer is registered for ``host_id``.
        """
        del timeout  # synchronous — kept for Protocol compatibility
        try:
            peer = self._peers[host_id]
        except KeyError as exc:
            raise KeyError(
                f"No in-process peer registered for host_id={host_id!r}. "
                f"Known peers: {sorted(self._peers.keys())}"
            ) from exc
        return peer.dispatch(command, params)


def _result_from_response(response: dict[str, Any]) -> CommandResult:
    """Reshape a wire-format response dict into a :class:`CommandResult`.

    :func:`rots.sidecar.rabbitmq.publish_command` returns the structure
    written by :meth:`RabbitMQConsumer._on_message`:

        {"success": bool, "result": data, "error": str | None, "warnings": [...]}

    This function is deliberately forgiving — missing keys default to
    sensible values so a partial response does not crash the caller.
    """
    return CommandResult(
        success=bool(response.get("success")),
        data=response.get("result"),
        error=response.get("error"),
        warnings=list(response.get("warnings") or []),
    )
