"""Context variables for threading global state through the call stack.

Uses contextvars to avoid circular imports and function signature changes
across the entire call graph.
"""

from __future__ import annotations

import contextvars

# Target host for remote execution. Set by --host CLI flag.
# None means local execution.
host_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("ots_host", default=None)
