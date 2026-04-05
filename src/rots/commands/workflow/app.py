# src/rots/commands/workflow/app.py
"""Fleet orchestration workflow CLI commands.

Provides multi-host deployment workflows via the sidecar RabbitMQ
messaging system. Executes sequentially with STOP_ON_FIRST failure
mode by default (safe for production).

Example:
    rots workflow deploy acme-prod-1 acme-prod-2 --tag v0.24.1
    rots workflow deploy --hosts-file hosts.txt --tag v0.24.1
    rots workflow deploy --tag v0.24.1  # uses .otsinfra-hosts.txt discovery
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Annotated

import cyclopts

from rots.commands.common import (
    EXIT_FAILURE,
    EXIT_PARTIAL,
    EXIT_PRECOND,
    EXIT_SUCCESS,
    DryRun,
    JsonOutput,
)
from rots.deploy import FailureMode, create_plan, execute, resolve_hosts

logger = logging.getLogger(__name__)

app = cyclopts.App(
    name="workflow",
    help="Fleet orchestration workflows (deploy, status).",
)


@app.command
def deploy(
    hosts: Annotated[
        tuple[str, ...],
        cyclopts.Parameter(
            help="Target host IDs. If omitted, uses --hosts-file or discovery.",
        ),
    ] = (),
    *,
    tag: Annotated[
        str,
        cyclopts.Parameter(
            name=["--tag", "-t"],
            help="Image tag to deploy (required).",
        ),
    ],
    port: Annotated[
        int,
        cyclopts.Parameter(
            name=["--port", "-p"],
            help="Instance port to redeploy.",
        ),
    ] = 7043,
    hosts_file: Annotated[
        Path | None,
        cyclopts.Parameter(
            name="--hosts-file",
            help="File with host IDs (one per line).",
        ),
    ] = None,
    delay: Annotated[
        float,
        cyclopts.Parameter(
            name="--delay",
            help="Seconds between steps.",
        ),
    ] = 5.0,
    continue_on_failure: Annotated[
        bool,
        cyclopts.Parameter(
            name="--continue-on-failure",
            help="Continue deployment even if a host fails.",
            negative=[],  # Disable --no-continue-on-failure
        ),
    ] = False,
    timeout: Annotated[
        int,
        cyclopts.Parameter(
            name="--timeout",
            help="Per-step timeout in seconds.",
        ),
    ] = 120,
    dry_run: DryRun = False,
    json_output: JsonOutput = False,
) -> None:
    """Deploy image to fleet hosts via sidecar.

    Executes sequentially. Stops on first failure unless --continue-on-failure.

    Examples:
        rots workflow deploy acme-prod-1 acme-prod-2 --tag v0.24.1
        rots workflow deploy --hosts-file hosts.txt --tag v0.24.1 --port 7043
        rots workflow deploy acme-prod-1 --tag v0.24.1 --continue-on-failure
        rots workflow deploy --tag v0.24.1 --dry-run
    """
    # Resolve hosts
    try:
        resolved_hosts = resolve_hosts(hosts, hosts_file=hosts_file)
    except ValueError as e:
        if json_output:
            print(json.dumps({"error": str(e), "exit_code": EXIT_PRECOND}))
        else:
            logger.error("%s", e)
        sys.exit(EXIT_PRECOND)

    # Determine failure mode
    failure_mode = FailureMode.CONTINUE_ALL if continue_on_failure else FailureMode.STOP_ON_FIRST

    # Create plan
    plan = create_plan(
        hosts=resolved_hosts,
        image_tag=tag,
        port=port,
        failure_mode=failure_mode,
        delay=delay,
        pull_timeout=float(timeout),
        redeploy_timeout=float(timeout),
    )

    # Dry run: show plan and exit
    if dry_run:
        _show_plan(plan, json_output)
        sys.exit(EXIT_SUCCESS)

    # Execute plan
    if not json_output:
        logger.info(
            "Deploying %s to %d host(s) (failure_mode=%s, delay=%.1fs)",
            tag,
            plan.host_count,
            failure_mode.value,
            delay,
        )

    results = []
    succeeded = 0
    failed = 0

    try:
        for result in execute(plan):
            results.append(result)
            if result.success:
                succeeded += 1
                if not json_output:
                    logger.info(
                        "  %s: %s ... OK (%.1fs)",
                        result.host_id,
                        result.step.description,
                        result.duration_ms / 1000,
                    )
            else:
                failed += 1
                if not json_output:
                    logger.error(
                        "  %s: %s ... FAILED: %s",
                        result.host_id,
                        result.step.description,
                        result.error or "unknown error",
                    )
    except Exception as e:
        logger.exception("Unexpected error during deployment")
        if json_output:
            print(
                json.dumps(
                    {
                        "error": str(e),
                        "results": [_result_to_dict(r) for r in results],
                        "exit_code": EXIT_FAILURE,
                    }
                )
            )
        sys.exit(EXIT_FAILURE)

    # Output results
    if json_output:
        output = {
            "action": "deploy",
            "image_tag": tag,
            "total_hosts": plan.host_count,
            "total_steps": plan.step_count,
            "executed_steps": len(results),
            "succeeded": succeeded,
            "failed": failed,
            "results": [_result_to_dict(r) for r in results],
        }
        print(json.dumps(output, indent=2))
    else:
        logger.info("")
        logger.info(
            "Summary: %d/%d steps succeeded, %d failed",
            succeeded,
            len(results),
            failed,
        )

    # Exit code
    if failed == 0:
        sys.exit(EXIT_SUCCESS)
    elif succeeded > 0:
        sys.exit(EXIT_PARTIAL)
    else:
        sys.exit(EXIT_FAILURE)


def _show_plan(plan, json_output: bool) -> None:
    """Display the deployment plan without executing."""
    if json_output:
        output = {
            "action": "plan",
            "image_tag": plan.image_tag,
            "host_count": plan.host_count,
            "step_count": plan.step_count,
            "failure_mode": plan.failure_mode.value,
            "delay": plan.delay,
            "steps": [
                {
                    "host_id": s.host_id,
                    "command": s.command,
                    "args": s.payload.get("args", []),
                    "timeout": s.timeout,
                }
                for s in plan.steps
            ],
        }
        print(json.dumps(output, indent=2))
    else:
        logger.info("Deployment plan for %s:", plan.image_tag)
        logger.info("  Hosts: %d", plan.host_count)
        logger.info("  Steps: %d", plan.step_count)
        logger.info("  Failure mode: %s", plan.failure_mode.value)
        logger.info("  Delay: %.1fs", plan.delay)
        logger.info("")
        for i, step in enumerate(plan.steps, 1):
            logger.info("  [%d] %s: %s", i, step.host_id, step.description)


def _result_to_dict(result) -> dict:
    """Convert StepResult to JSON-serializable dict."""
    return {
        "host_id": result.host_id,
        "command": result.step.command,
        "success": result.success,
        "error": result.error,
        "duration_ms": result.duration_ms,
    }
