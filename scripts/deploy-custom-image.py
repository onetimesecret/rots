#!/usr/bin/env python3
# packages/rots/scripts/deploy-custom-image.py

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

from rots.deploy import (  # noqa: E402
    EXIT_SUCCESS,
    FailureMode,
    create_plan,
    display_plan,
    resolve_hosts,
    run_plan_with_progress,
)

# Precondition failure (not exported from deploy - script-specific)
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

    # Dry run: show plan and exit
    if args.dry_run:
        fmt = "json" if args.json else "text"
        output = display_plan(plan, format=fmt)
        print(output)
        return EXIT_SUCCESS

    # Execute
    if not args.json:
        logger.info(
            "Deploying %s to %d host(s)...",
            args.tag,
            plan.host_count,
        )

    return run_plan_with_progress(plan, json_output=args.json, action="deploy", logger=logger)


if __name__ == "__main__":
    sys.exit(main())
