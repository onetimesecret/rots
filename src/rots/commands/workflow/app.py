# packages/rots/src/rots/commands/workflow/app.py

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
    EXIT_PRECOND,
    EXIT_SUCCESS,
    DryRun,
    JsonOutput,
)
from rots.deploy import (
    DeployManifest,
    FailureMode,
    ManifestError,
    create_plan,
    display_plan,
    resolve_hosts,
    run_plan_with_progress,
)

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
        fmt = "json" if json_output else "text"
        output = display_plan(plan, format=fmt)
        if json_output:
            print(output)
        else:
            for line in output.split("\n"):
                logger.info(line)
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

    exit_code = run_plan_with_progress(
        plan, json_output=json_output, action="deploy", logger=logger
    )
    sys.exit(exit_code)


@app.command
def trigger(
    *,
    tag: Annotated[
        str,
        cyclopts.Parameter(
            name=["--tag", "-t"],
            help="Image tag to deploy (required).",
        ),
    ],
    hosts: Annotated[
        tuple[str, ...],
        cyclopts.Parameter(
            name="--hosts",
            help="Target host IDs (comma-separated or repeated).",
        ),
    ] = (),
    manifest: Annotated[
        Path | None,
        cyclopts.Parameter(
            name="--manifest",
            help="Path to .ots-deploy.yaml manifest file.",
        ),
    ] = None,
    port: Annotated[
        int | None,
        cyclopts.Parameter(
            name=["--port", "-p"],
            help="Instance port (overrides manifest).",
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
            negative=[],
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
    """Trigger deployment from post-receive hook or CI.

    Resolves hosts from: --hosts > --manifest > .ots-deploy.yaml > .otsinfra-hosts.txt

    Designed for automation: use --json for structured output suitable for
    parsing in shell scripts or CI pipelines.

    Examples:
        # With manifest file (recommended for CI)
        rots workflow trigger --tag v1.2.3 --json

        # With explicit hosts
        rots workflow trigger --tag v1.2.3 --hosts acme-prod-1 --hosts acme-prod-2

        # Dry run to see plan
        rots workflow trigger --tag v1.2.3 --dry-run

        # From gitolite post-receive hook
        TAG=$(git describe --tags) && rots workflow trigger --tag $TAG --json
    """
    resolved_hosts: list[str] = []
    resolved_port: int = 7043

    # Resolution priority: CLI args > manifest file > discovery > hosts file
    try:
        if hosts:
            # 1. Explicit hosts from CLI
            resolved_hosts = list(hosts)
            resolved_port = port if port is not None else 7043
            logger.debug("Using hosts from CLI: %s", resolved_hosts)

        elif manifest is not None:
            # 2. Explicit manifest file
            try:
                m = DeployManifest.from_file(manifest)
            except FileNotFoundError:
                raise ValueError(f"Manifest file not found: {manifest}")
            resolved_hosts = m.hosts
            resolved_port = port if port is not None else m.port
            logger.debug("Using manifest from %s: %s", manifest, resolved_hosts)

        else:
            # 3. Walk-up discovery for .ots-deploy.yaml
            m = DeployManifest.discover()
            if m is not None:
                resolved_hosts = m.hosts
                resolved_port = port if port is not None else m.port
                logger.debug("Discovered manifest at %s: %s", m.source, resolved_hosts)
            else:
                # 4. Fall back to .otsinfra-hosts.txt
                try:
                    resolved_hosts = resolve_hosts(())
                except ValueError:
                    # Re-raise with trigger-specific message (resolve_hosts mentions
                    # --hosts-file which is not a valid flag for trigger command)
                    raise ValueError(
                        "No hosts found. Provide --hosts, --manifest, or create a "
                        ".ots-deploy.yaml or .otsinfra-hosts.txt file."
                    )
                resolved_port = port if port is not None else 7043
                logger.debug("Using hosts from .otsinfra-hosts.txt: %s", resolved_hosts)

    except (ValueError, ManifestError) as e:
        if json_output:
            print(json.dumps({"error": str(e), "exit_code": EXIT_PRECOND}))
        else:
            logger.error("%s", e)
        sys.exit(EXIT_PRECOND)

    if not resolved_hosts:
        msg = "No hosts specified. Provide --hosts, --manifest, or create a .ots-deploy.yaml file."
        if json_output:
            print(json.dumps({"error": msg, "exit_code": EXIT_PRECOND}))
        else:
            logger.error("%s", msg)
        sys.exit(EXIT_PRECOND)

    # Determine failure mode
    failure_mode = FailureMode.CONTINUE_ALL if continue_on_failure else FailureMode.STOP_ON_FIRST

    # Create plan
    plan = create_plan(
        hosts=resolved_hosts,
        image_tag=tag,
        port=resolved_port,
        failure_mode=failure_mode,
        delay=delay,
        pull_timeout=float(timeout),
        redeploy_timeout=float(timeout),
    )

    # Dry run: show plan and exit
    if dry_run:
        fmt = "json" if json_output else "text"
        output = display_plan(plan, format=fmt)
        if json_output:
            print(output)
        else:
            for line in output.split("\n"):
                logger.info(line)
        sys.exit(EXIT_SUCCESS)

    # Execute plan
    if not json_output:
        logger.info(
            "Triggering deployment of %s to %d host(s)",
            tag,
            plan.host_count,
        )

    exit_code = run_plan_with_progress(
        plan,
        json_output=json_output,
        action="trigger",
        logger=logger,
        extra_json_fields={"port": resolved_port},
    )
    sys.exit(exit_code)
