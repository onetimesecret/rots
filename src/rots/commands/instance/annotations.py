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

Usage:
    --web 7043              # Single web instance
    --web 7043,7044         # Multiple web instances
    --web                   # All web instances (no identifiers)
    --type web              # All web instances (equivalent)
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

# Instance type selector (targets all instances of a type)
TypeSelector = Annotated[
    InstanceType | None,
    cyclopts.Parameter(
        name=["--type", "-t"],
        help="Instance type: web, worker, or scheduler",
    ),
]

# Type+identifier flags: comma-separated instance identifiers
# --web 7043,7044  or  --web (all web instances)
WebFlag = Annotated[
    str | None,
    cyclopts.Parameter(
        name=["--web"],
        help="Target web instances (comma-separated ports, e.g. 7043,7044)",
    ),
]

WorkerFlag = Annotated[
    str | None,
    cyclopts.Parameter(
        name=["--worker"],
        help="Target worker instances (comma-separated IDs, e.g. 1,2,billing)",
    ),
]

SchedulerFlag = Annotated[
    str | None,
    cyclopts.Parameter(
        name=["--scheduler"],
        help="Target scheduler instances (comma-separated IDs, e.g. main,1)",
    ),
]


def resolve_instance_type(
    type_: InstanceType | None,
    web: str | None,
    worker: str | None,
    scheduler: str | None,
) -> tuple[InstanceType | None, tuple[str, ...]]:
    """Resolve instance type and identifiers from flags.

    Returns (instance_type, identifiers) where identifiers is a tuple
    of comma-split values from the flag, or empty tuple for "all".
    """
    shorthand_count = sum(x is not None for x in [web, worker, scheduler])

    if shorthand_count > 1:
        raise SystemExit("Only one of --web, --worker, or --scheduler can be specified")

    if type_ is not None:
        if shorthand_count > 0:
            raise SystemExit("Cannot use --type with --web, --worker, or --scheduler")
        return (type_, ())

    if web is not None:
        ids = tuple(x for x in web.split(",") if x) if web else ()
        return (InstanceType.WEB, ids)
    if worker is not None:
        ids = tuple(x for x in worker.split(",") if x) if worker else ()
        return (InstanceType.WORKER, ids)
    if scheduler is not None:
        ids = tuple(x for x in scheduler.split(",") if x) if scheduler else ()
        return (InstanceType.SCHEDULER, ids)

    return (None, ())  # All types
