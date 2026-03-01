# src/rots/commands/instance/annotations.py

"""Type annotations for instance commands.

Instance types:
- web: HTTP server containers (onetime-web@{port})
- worker: Background job processors (onetime-worker@{id})
- scheduler: Cron-like job scheduler (onetime-scheduler@{id})

Identifier patterns:
- Web: numeric ports (7043, 7044)
- Worker: numeric or named (1, 2, billing, emails)
- Scheduler: numeric or named (main, 1)
"""

from enum import StrEnum
from typing import Annotated

import cyclopts


class InstanceType(StrEnum):
    """Type of container instance."""

    WEB = "web"
    WORKER = "worker"
    SCHEDULER = "scheduler"


# Delay between sequential operations
Delay = Annotated[
    int,
    cyclopts.Parameter(
        name=["--delay", "-d"],
        help="Seconds between operations",
    ),
]

# Instance identifiers as positional arguments
# Works for all types: ports for web, IDs for worker/scheduler
Identifiers = Annotated[
    tuple[str, ...],
    cyclopts.Parameter(
        show=False,  # Positional, not shown as named param in help
        help="Instance identifiers (ports for web, IDs for worker/scheduler)",
    ),
]

# Instance type selector
TypeSelector = Annotated[
    InstanceType | None,
    cyclopts.Parameter(
        name=["--type", "-t"],
        help="Instance type: web, worker, or scheduler",
    ),
]

# Shorthand flags for --type
WebFlag = Annotated[
    bool,
    cyclopts.Parameter(
        name=["--web"],
        help="Target web instances (shorthand for --type web)",
    ),
]

WorkerFlag = Annotated[
    bool,
    cyclopts.Parameter(
        name=["--worker"],
        help="Target worker instances (shorthand for --type worker)",
    ),
]

SchedulerFlag = Annotated[
    bool,
    cyclopts.Parameter(
        name=["--scheduler"],
        help="Target scheduler instances (shorthand for --type scheduler)",
    ),
]


def resolve_instance_type(
    type_: InstanceType | None,
    web: bool,
    worker: bool,
    scheduler: bool,
) -> InstanceType | None:
    """Resolve instance type from --type flag or shorthand flags.

    Returns None if no type specified (meaning "all types").
    Raises if multiple shorthand flags are set.
    """
    shorthand_count = sum([web, worker, scheduler])

    if shorthand_count > 1:
        raise SystemExit("Only one of --web, --worker, or --scheduler can be specified")

    if type_ is not None:
        if shorthand_count > 0:
            raise SystemExit("Cannot use --type with --web, --worker, or --scheduler")
        return type_

    if web:
        return InstanceType.WEB
    if worker:
        return InstanceType.WORKER
    if scheduler:
        return InstanceType.SCHEDULER

    return None  # All types
