# src/ots_containers/commands/common.py

"""Shared CLI annotations and constants for consistency across commands.

All common flags use long+short forms for consistency:
  --quiet, -q
  --dry-run, -n
  --yes, -y
  --follow, -f
  --lines, -l
  --json, -j

Exit code convention
--------------------
All commands use this scheme so that CI pipelines and shell scripts can
distinguish between different failure modes:

  EXIT_SUCCESS  (0)  Command completed successfully; all operations applied.
  EXIT_FAILURE  (1)  General command failure (unexpected error, bad arguments).
  EXIT_PARTIAL  (2)  Partial success: at least one operation succeeded and at
                     least one failed.  Check the output for details.
  EXIT_PRECOND  (3)  Precondition not met: required configuration is absent
                     (e.g. missing env file, missing Podman secrets, image not
                     pulled).  No destructive action was attempted.
"""

from typing import Annotated

import cyclopts

# ------------------------------------------------------------------
# Exit code constants
# ------------------------------------------------------------------

EXIT_SUCCESS: int = 0
"""Command completed successfully; all operations applied."""

EXIT_FAILURE: int = 1
"""General command failure (unexpected error, bad arguments, etc.)."""

EXIT_PARTIAL: int = 2
"""Partial success: at least one operation succeeded and at least one failed."""

EXIT_PRECOND: int = 3
"""Precondition not met: required configuration is absent."""

# Output control
Quiet = Annotated[
    bool,
    cyclopts.Parameter(
        name=["--quiet", "-q"],
        help="Suppress output",
    ),
]

DryRun = Annotated[
    bool,
    cyclopts.Parameter(
        name=["--dry-run", "-n"],
        help="Show what would be done without doing it",
        negative=[],  # Disable --no-dry-run generation
    ),
]


# Confirmation
Yes = Annotated[
    bool,
    cyclopts.Parameter(
        name=["--yes", "-y"],
        help="Skip confirmation prompts",
    ),
]


# Log viewing
Follow = Annotated[
    bool,
    cyclopts.Parameter(
        name=["--follow", "-f"],
        help="Follow log output",
    ),
]

Lines = Annotated[
    int,
    cyclopts.Parameter(
        name=["--lines", "-l"],
        help="Number of lines to show",
    ),
]


# JSON output
JsonOutput = Annotated[
    bool,
    cyclopts.Parameter(
        name=["--json", "-j"],
        help="Output as JSON",
    ),
]


# Image reference annotations
ImageRef = Annotated[
    str | None,
    cyclopts.Parameter(
        help=(
            "Image reference (e.g. ghcr.io/org/image:tag). "
            "Overrides IMAGE/TAG env vars when provided."
        ),
        show_default=False,
    ),
]

TagFlag = Annotated[
    str | None,
    cyclopts.Parameter(
        name=["--tag", "-t"],
        help="Image tag to use (default: from TAG env or '@current' alias)",
    ),
]
