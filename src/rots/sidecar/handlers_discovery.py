# packages/rots/src/rots/sidecar/handlers_discovery.py

"""Discovery handlers for sidecar presence detection.

The discover.ping command allows sidecars to respond to broadcast
discovery requests with their host_id, enabling operators to enumerate
which hosts have active sidecars.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

from .commands import Command, CommandResult, register_handler
from .rabbitmq import get_host_id

logger = logging.getLogger(__name__)


@register_handler(Command.DISCOVER_PING)
def handle_discover_ping(params: dict[str, Any]) -> CommandResult:
    """Respond to a discovery ping with this sidecar's identity.

    Returns the host_id, PID, and timestamp so the caller can
    enumerate active sidecars and their basic health.
    """
    host_id = get_host_id()
    logger.info("Discovery ping received, responding as host_id=%s", host_id)

    return CommandResult.ok(
        {
            "host_id": host_id,
            "pid": os.getpid(),
            "timestamp": time.time(),
        }
    )
