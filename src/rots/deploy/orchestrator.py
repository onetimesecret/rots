# packages/rots/src/rots/deploy/orchestrator.py

"""Fleet deployment orchestration.

Provides the core orchestration logic for deploying container images
across multiple hosts via the sidecar RabbitMQ messaging system.

This module uses a generator-based execution model for:
- Real-time progress streaming to console
- Clean mapping to Hatchet step-by-step execution
- Early termination on STOP_ON_FIRST failure

Example:
    plan = create_plan(["host1", "host2"], "v0.24.1")
    for result in execute(plan):
        print(f"{result.step.host_id}: {'OK' if result.success else result.error}")
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from rots.sidecar.rabbitmq import RabbitMQConfig

logger = logging.getLogger(__name__)


class FailureMode(StrEnum):
    """How to handle failures during deployment."""

    STOP_ON_FIRST = "stop"  # Default: halt on first error (safe for production)
    CONTINUE_ALL = "continue"  # Attempt all hosts regardless of failures


@dataclass
class DeployStep:
    """A single step in a deployment plan."""

    host_id: str
    command: str  # e.g., "rots.image.pull", "rots.instance.redeploy"
    payload: dict[str, Any] = field(default_factory=dict)
    timeout: float = 120.0

    @property
    def description(self) -> str:
        """Human-readable description of this step."""
        # Extract meaningful info from command
        cmd_short = self.command.replace("rots.", "")
        args = self.payload.get("args", [])
        if args:
            return f"{cmd_short} {' '.join(str(a) for a in args)}"
        return cmd_short


@dataclass
class StepResult:
    """Result of executing a single deploy step."""

    step: DeployStep
    success: bool
    response: dict[str, Any] | None = None
    error: str | None = None
    duration_ms: int = 0

    @property
    def host_id(self) -> str:
        """Convenience accessor for step.host_id."""
        return self.step.host_id


@dataclass
class DeployPlan:
    """A complete deployment plan."""

    image_tag: str
    steps: list[DeployStep] = field(default_factory=list)
    failure_mode: FailureMode = FailureMode.STOP_ON_FIRST
    delay: float = 5.0  # Seconds between steps

    @property
    def host_count(self) -> int:
        """Number of unique hosts in this plan."""
        return len({s.host_id for s in self.steps})

    @property
    def step_count(self) -> int:
        """Total number of steps."""
        return len(self.steps)


def create_plan(
    hosts: list[str],
    image_tag: str,
    *,
    port: int = 7043,
    failure_mode: FailureMode = FailureMode.STOP_ON_FIRST,
    delay: float = 5.0,
    pull_timeout: float = 120.0,
    redeploy_timeout: float = 60.0,
) -> DeployPlan:
    """Build a deployment plan.

    Creates 2 steps per host:
    1. rots.image.pull --tag {tag} --current
    2. rots.instance.redeploy {port}

    Args:
        hosts: List of target host IDs.
        image_tag: Image tag to deploy.
        port: Instance port to redeploy (default: 7043).
        failure_mode: How to handle failures.
        delay: Seconds between steps.
        pull_timeout: Timeout for image pull (default: 120s).
        redeploy_timeout: Timeout for redeploy (default: 60s).

    Returns:
        DeployPlan ready for execution.
    """
    steps: list[DeployStep] = []

    for host_id in hosts:
        # Step 1: Pull image
        steps.append(
            DeployStep(
                host_id=host_id,
                command="rots.image.pull",
                payload={"args": ["--tag", image_tag, "--current"]},
                timeout=pull_timeout,
            )
        )
        # Step 2: Redeploy instance
        steps.append(
            DeployStep(
                host_id=host_id,
                command="rots.instance.redeploy",
                payload={"args": [str(port)]},
                timeout=redeploy_timeout,
            )
        )

    return DeployPlan(
        image_tag=image_tag,
        steps=steps,
        failure_mode=failure_mode,
        delay=delay,
    )


def publish_and_wait(
    host_id: str,
    command: str,
    *,
    args: list[str] | None = None,
    payload: dict[str, Any] | None = None,
    timeout: float = 60.0,
    config: RabbitMQConfig | None = None,
) -> dict[str, Any]:
    """Send a command to a specific host via RabbitMQ and wait for response.

    This is a thin wrapper around rabbitmq.publish_command() that provides
    a more convenient interface for CLI-style arguments.

    Args:
        host_id: Target host identifier for routing.
        command: Command name (e.g., "rots.image.pull").
        args: CLI-style arguments (converted to payload["args"]).
        payload: Full payload dict (overrides args if both provided).
        timeout: Seconds to wait for response.
        config: RabbitMQ configuration (defaults to from_environment()).

    Returns:
        Response dict from the sidecar handler.

    Raises:
        TimeoutError: If no response within timeout.
    """
    from rots.sidecar.rabbitmq import RabbitMQConfig, publish_command

    # Build payload (copy to avoid mutating caller's dict)
    if payload is None:
        payload = {}
    else:
        payload = dict(payload)
    if args is not None and "args" not in payload:
        payload["args"] = args

    # Resolve config
    if config is None:
        config = RabbitMQConfig.from_environment()

    logger.debug(
        "Publishing command %r to host %r with payload %r",
        command,
        host_id,
        payload,
    )

    return publish_command(
        command=command,
        payload=payload,
        config=config,
        timeout=timeout,
        target_host=host_id,
    )


def execute(
    plan: DeployPlan,
    config: RabbitMQConfig | None = None,
) -> Iterator[StepResult]:
    """Execute a deployment plan, yielding results for each step.

    Generator-based execution for:
    - Real-time progress output to console
    - Clean mapping to Hatchet step-by-step model
    - Early termination on STOP_ON_FIRST failure

    Args:
        plan: The deployment plan to execute.
        config: RabbitMQ configuration (defaults to from_environment()).

    Yields:
        StepResult for each step as it completes.

    Note:
        When failure_mode is STOP_ON_FIRST, iteration stops after the
        first failed step. Remaining steps are not executed.
    """
    from rots.sidecar.rabbitmq import RabbitMQConfig

    if config is None:
        config = RabbitMQConfig.from_environment()

    for i, step in enumerate(plan.steps):
        # Apply delay between steps (not before first)
        if i > 0 and plan.delay > 0:
            logger.debug("Waiting %.1fs before next step", plan.delay)
            time.sleep(plan.delay)

        logger.info(
            "[%d/%d] %s: %s",
            i + 1,
            plan.step_count,
            step.host_id,
            step.description,
        )

        start_ms = int(time.time() * 1000)

        try:
            response = publish_and_wait(
                host_id=step.host_id,
                command=step.command,
                payload=step.payload,
                timeout=step.timeout,
                config=config,
            )

            duration_ms = int(time.time() * 1000) - start_ms

            # Check response success
            # Response format: {"success": bool, "result": ..., "error": ...}
            # OR from rots handler: {"status": "ok"|"error", ...}
            success = response.get("success", response.get("status") == "ok")
            error = response.get("error") if not success else None

            result = StepResult(
                step=step,
                success=success,
                response=response,
                error=error,
                duration_ms=duration_ms,
            )

        except TimeoutError as e:
            duration_ms = int(time.time() * 1000) - start_ms
            result = StepResult(
                step=step,
                success=False,
                error=f"Timeout: {e}",
                duration_ms=duration_ms,
            )

        except Exception as e:
            duration_ms = int(time.time() * 1000) - start_ms
            logger.exception("Unexpected error executing step: %s", step.description)
            result = StepResult(
                step=step,
                success=False,
                error=f"Unexpected error: {e}",
                duration_ms=duration_ms,
            )

        yield result

        # Early termination on failure
        if not result.success and plan.failure_mode == FailureMode.STOP_ON_FIRST:
            logger.warning("Stopping deployment: step failed and failure_mode is STOP_ON_FIRST")
            return
