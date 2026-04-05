# src/rots/deploy/reporting.py
"""Shared reporting utilities for deployment workflows.

Provides consistent formatting for deployment plans and results across
CLI commands and standalone scripts.

Example:
    from rots.deploy import create_plan, execute
    from rots.deploy.reporting import display_plan, format_results, determine_exit_code

    plan = create_plan(hosts, tag)
    print(display_plan(plan, format="text"))

    results = list(execute(plan))
    print(format_results(results, plan, format="json"))
    sys.exit(determine_exit_code(results))
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from .orchestrator import DeployPlan, StepResult

# Exit codes (matching rots convention in commands/common.py)
EXIT_SUCCESS = 0
EXIT_FAILURE = 1
EXIT_PARTIAL = 2


def result_to_dict(result: StepResult) -> dict:
    """Convert StepResult to JSON-serializable dict.

    Args:
        result: The step result to convert.

    Returns:
        Dictionary with host_id, command, success, error, duration_ms.
    """
    return {
        "host_id": result.host_id,
        "command": result.step.command,
        "success": result.success,
        "error": result.error,
        "duration_ms": result.duration_ms,
    }


def display_plan(
    plan: DeployPlan,
    *,
    format: Literal["text", "json"] = "text",
) -> str:
    """Format a deployment plan for display.

    Used by --dry-run to show what would be executed.

    Args:
        plan: The deployment plan to format.
        format: Output format ("text" or "json").

    Returns:
        Formatted string representation of the plan.
    """
    if format == "json":
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
        return json.dumps(output, indent=2)

    # Text format
    lines = [
        f"Deployment plan for {plan.image_tag}:",
        f"  Hosts: {plan.host_count}",
        f"  Steps: {plan.step_count}",
        f"  Failure mode: {plan.failure_mode.value}",
        f"  Delay: {plan.delay:.1f}s",
        "",
    ]
    for i, step in enumerate(plan.steps, 1):
        lines.append(f"  [{i}] {step.host_id}: {step.description}")

    return "\n".join(lines)


def format_results(
    results: list[StepResult],
    plan: DeployPlan,
    *,
    format: Literal["text", "json"] = "text",
    action: str = "deploy",
) -> str:
    """Format execution results for display.

    Args:
        results: List of step results from execution.
        plan: The original deployment plan (for host/step counts).
        format: Output format ("text" or "json").
        action: Action name for JSON output (e.g., "deploy", "trigger").

    Returns:
        Formatted string with summary statistics.
    """
    succeeded = sum(1 for r in results if r.success)
    failed = len(results) - succeeded
    total_duration_ms = sum(r.duration_ms for r in results)

    if format == "json":
        output = {
            "action": action,
            "image_tag": plan.image_tag,
            "total_hosts": plan.host_count,
            "total_steps": plan.step_count,
            "executed_steps": len(results),
            "succeeded": succeeded,
            "failed": failed,
            "total_duration_ms": total_duration_ms,
            "results": [result_to_dict(r) for r in results],
        }
        return json.dumps(output, indent=2)

    # Text format - summary only (step-by-step output is streamed during execution)
    return f"Summary: {succeeded}/{len(results)} steps succeeded, {failed} failed"


def determine_exit_code(results: list[StepResult]) -> int:
    """Determine the appropriate exit code based on results.

    Exit codes follow the rots convention:
    - EXIT_SUCCESS (0): All steps succeeded
    - EXIT_PARTIAL (2): Some steps succeeded, some failed
    - EXIT_FAILURE (1): All steps failed (or no steps executed)

    Args:
        results: List of step results from execution.

    Returns:
        Exit code (0, 1, or 2).
    """
    if not results:
        return EXIT_FAILURE

    succeeded = sum(1 for r in results if r.success)
    failed = len(results) - succeeded

    if failed == 0:
        return EXIT_SUCCESS
    elif succeeded > 0:
        return EXIT_PARTIAL
    else:
        return EXIT_FAILURE
