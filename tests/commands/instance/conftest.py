"""Shared fixtures for instance command tests."""

import logging
import sys

import pytest


class _LiveStderrHandler(logging.StreamHandler):
    """StreamHandler that resolves sys.stderr at emit time.

    The default StreamHandler captures a reference to sys.stderr at
    creation time.  If pytest's capsys replaces sys.stderr *after* the
    handler is created, the handler writes to the original fd and
    capsys.readouterr() returns empty strings.

    This handler looks up sys.stderr on every emit so it always writes
    to whatever pytest (or anything else) has installed.
    """

    def __init__(self):
        super().__init__()
        # Don't bind to a specific stream at init time
        self.stream = sys.stderr

    def emit(self, record):
        self.stream = sys.stderr
        super().emit(record)


@pytest.fixture(autouse=True)
def _reset_logging_handlers():
    """Ensure logging handlers write to pytest's captured stderr.

    Replaces any existing handlers with a _LiveStderrHandler so that
    capsys.readouterr().err contains logger output.
    """
    root = logging.getLogger()
    old_handlers = root.handlers[:]
    old_level = root.level
    for h in old_handlers:
        root.removeHandler(h)

    from rots.cli import _CLIFormatter

    handler = _LiveStderrHandler()
    handler.setFormatter(_CLIFormatter())
    root.addHandler(handler)
    root.setLevel(logging.INFO)

    yield

    # Restore original handlers
    root.removeHandler(handler)
    for h in old_handlers:
        root.addHandler(h)
    root.setLevel(old_level)
