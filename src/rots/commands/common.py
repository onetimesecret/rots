# src/rots/commands/common.py

"""Shared CLI annotations and constants for consistency across rots commands.

Common flags and exit codes are defined in ots_shared and re-exported here
so that rots commands import from a single location. Rots-specific type
aliases (ImageRef, TagFlag) are defined locally below.
"""

from typing import Annotated

import cyclopts

# Re-export shared CLI type aliases
from ots_shared.cli import DryRun, Follow, JsonOutput, Lines, Quiet, Yes

# Re-export shared exit codes
from ots_shared.exit_codes import EXIT_FAILURE, EXIT_PARTIAL, EXIT_PRECOND, EXIT_SUCCESS

# Silence F401 for re-exports
__all__ = [
    "DryRun",
    "Follow",
    "JsonOutput",
    "Lines",
    "Quiet",
    "Yes",
    "EXIT_FAILURE",
    "EXIT_PARTIAL",
    "EXIT_PRECOND",
    "EXIT_SUCCESS",
    "ImageRef",
    "TagFlag",
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
