# src/rots/deploy/__init__.py
"""Fleet deployment orchestration library.

This module provides orchestration logic for deploying container images
across multiple hosts via the sidecar RabbitMQ messaging system.

The library is designed to be importable by Hatchet workers for durable
workflow execution, separate from the CLI machinery.

Example:
    from rots.deploy.orchestrator import create_plan, execute, FailureMode
    from rots.deploy.hosts import resolve_hosts

    hosts = resolve_hosts(("prod-1", "prod-2"))
    plan = create_plan(hosts, "v0.24.1")
    for result in execute(plan):
        print(f"{result.step.host_id}: {result.success}")
"""

from .hosts import find_hosts_file, load_hosts_file, resolve_hosts
from .manifest import DeployManifest, ManifestError, find_manifest_file
from .orchestrator import (
    DeployPlan,
    DeployStep,
    FailureMode,
    StepResult,
    create_plan,
    execute,
    publish_and_wait,
)
from .reporting import (
    EXIT_FAILURE,
    EXIT_PARTIAL,
    EXIT_SUCCESS,
    determine_exit_code,
    display_plan,
    format_results,
    result_to_dict,
    run_plan_with_progress,
)

__all__ = [
    # orchestrator
    "FailureMode",
    "DeployStep",
    "StepResult",
    "DeployPlan",
    "create_plan",
    "execute",
    "publish_and_wait",
    # hosts
    "resolve_hosts",
    "find_hosts_file",
    "load_hosts_file",
    # manifest
    "DeployManifest",
    "ManifestError",
    "find_manifest_file",
    # reporting
    "EXIT_SUCCESS",
    "EXIT_FAILURE",
    "EXIT_PARTIAL",
    "display_plan",
    "format_results",
    "determine_exit_code",
    "result_to_dict",
    "run_plan_with_progress",
]
