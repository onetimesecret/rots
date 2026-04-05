#!/usr/bin/env python3
"""Standalone deployment script for custom OTS images.

This is a runbook artifact that can run from any directory without
requiring the full rots CLI to be installed. It imports the deploy
library directly.

Usage:
    # Explicit hosts
    ./deploy-custom-image.py --tag v0.24.0 --hosts host1,host2

    # From jurisdiction directory (uses .otsinfra-hosts.txt)
    cd ops-jurisdictions/acme/
    /path/to/deploy-custom-image.py --tag v0.24.0

    # With options
    ./deploy-custom-image.py --tag v0.24.0 --hosts host1 --port 7044 --continue-on-failure
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

# Ensure the rots package is importable
# This handles running from the scripts/ directory
_script_dir = Path(__file__).resolve().parent
_src_dir = _script_dir.parent / "src"
if _src_dir.exists() and str(_src_dir) not in sys.path:
    sys.path.insert(0, str(_src_dir))

from rots.deploy import FailureMode, create_plan, execute, resolve_hosts  # noqa: E402

# Exit codes (matching rots convention)
EXIT_SUCCESS = 0
EXIT_FAILURE = 1
EXIT_PARTIAL = 2
EXIT_PRECOND = 3


def setup_logging(verbose: bool = False) -> None:
    """Configure logging for script output."""
    level = logging.DEBUG if verbose else logging.INFO
    fmt = "%(message)s" if not verbose else "%(levelname)s: %(message)s"
    logging.basicConfig(level=level, format=fmt, stream=sys.stderr)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Deploy custom OTS image to fleet hosts via sidecar.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--tag",
        "-t",
        required=True,
        help="Image tag to deploy (e.g., v0.24.0)",
    )
    parser.add_argument(
        "--hosts",
        "-H",
        help="Comma-separated list of host IDs",
    )
    parser.add_argument(
        "--hosts-file",
        type=Path,
        help="File with host IDs (one per line)",
    )
    parser.add_argument(
        "--port",
        "-p",
        type=int,
        default=7043,
        help="Instance port to redeploy (default: 7043)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=5.0,
        help="Seconds between steps (default: 5.0)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=120,
        help="Per-step timeout in seconds (default: 120)",
    )
    parser.add_argument(
        "--continue-on-failure",
        action="store_true",
        help="Continue deployment even if a host fails",
    )
    parser.add_argument(
        "--dry-run",
        "-n",
        action="store_true",
        help="Show plan without executing",
    )
    parser.add_argument(
        "--json",
        "-j",
        action="store_true",
        help="Output as JSON",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable verbose output",
    )

    args = parser.parse_args()
    setup_logging(args.verbose)
    logger = logging.getLogger(__name__)

    # Parse hosts
    explicit_hosts: tuple[str, ...] = ()
    if args.hosts:
        explicit_hosts = tuple(h.strip() for h in args.hosts.split(",") if h.strip())

    # Resolve hosts
    try:
        hosts = resolve_hosts(explicit_hosts, hosts_file=args.hosts_file)
    except ValueError as e:
        if args.json:
            print(json.dumps({"error": str(e), "exit_code": EXIT_PRECOND}))
        else:
            logger.error("Error: %s", e)
        return EXIT_PRECOND

    # Determine failure mode
    failure_mode = (
        FailureMode.CONTINUE_ALL if args.continue_on_failure else FailureMode.STOP_ON_FIRST
    )

    # Create plan
    plan = create_plan(
        hosts=hosts,
        image_tag=args.tag,
        port=args.port,
        failure_mode=failure_mode,
        delay=args.delay,
        pull_timeout=float(args.timeout),
        redeploy_timeout=float(args.timeout),
    )

    # Dry run
    if args.dry_run:
        if args.json:
            output = {
                "action": "plan",
                "image_tag": plan.image_tag,
                "host_count": plan.host_count,
                "step_count": plan.step_count,
                "failure_mode": plan.failure_mode.value,
                "steps": [
                    {
                        "host_id": s.host_id,
                        "command": s.command,
                        "args": s.payload.get("args", []),
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
            for i, step in enumerate(plan.steps, 1):
                logger.info("  [%d] %s: %s", i, step.host_id, step.description)
        return EXIT_SUCCESS

    # Execute
    if not args.json:
        logger.info(
            "Deploying %s to %d host(s)...",
            args.tag,
            plan.host_count,
        )

    results = []
    succeeded = 0
    failed = 0

    try:
        for result in execute(plan):
            results.append(result)
            if result.success:
                succeeded += 1
                if not args.json:
                    logger.info(
                        "  %s: %s ... OK (%.1fs)",
                        result.host_id,
                        result.step.description,
                        result.duration_ms / 1000,
                    )
            else:
                failed += 1
                if not args.json:
                    logger.error(
                        "  %s: %s ... FAILED: %s",
                        result.host_id,
                        result.step.description,
                        result.error,
                    )
    except Exception as e:
        logger.exception("Unexpected error")
        if args.json:
            print(json.dumps({"error": str(e), "exit_code": EXIT_FAILURE}))
        return EXIT_FAILURE

    # Output
    if args.json:
        output = {
            "action": "deploy",
            "image_tag": args.tag,
            "total_hosts": plan.host_count,
            "executed_steps": len(results),
            "succeeded": succeeded,
            "failed": failed,
            "results": [
                {
                    "host_id": r.host_id,
                    "command": r.step.command,
                    "success": r.success,
                    "error": r.error,
                    "duration_ms": r.duration_ms,
                }
                for r in results
            ],
        }
        print(json.dumps(output, indent=2))
    else:
        logger.info("")
        logger.info("Summary: %d/%d succeeded, %d failed", succeeded, len(results), failed)

    # Exit code
    if failed == 0:
        return EXIT_SUCCESS
    elif succeeded > 0:
        return EXIT_PARTIAL
    else:
        return EXIT_FAILURE


if __name__ == "__main__":
    sys.exit(main())
