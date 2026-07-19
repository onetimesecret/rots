# tests/commands/instance/conftest.py

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


@pytest.fixture(autouse=True)
def _isolate_deploy_lock(tmp_path):
    """Point the deploy lock at a per-test path.

    On dev machines ``/var/lib/onetimesecret`` is not writable, so
    ``_resolve_lock_path`` falls back to a single fixed file
    (``$TMPDIR/ots-deploy.lock``).  Integration deploy tests that enter the
    real ``deploy_lock()`` therefore share one advisory lock and collide under
    parallel (``pytest -n auto``) execution.  Rebinding the default lock_path
    to a unique tmp_path isolates each test.  Tests that pass an explicit
    lock_path (test_helpers) or mock ``deploy_lock`` are unaffected, since
    neither uses this default.
    """
    from rots.commands.instance import _helpers

    # deploy_lock is @contextmanager-decorated, so its real default lives on
    # the wrapped generator function, not the wrapper.
    wrapped = _helpers.deploy_lock.__wrapped__
    original = wrapped.__defaults__
    wrapped.__defaults__ = (tmp_path / "deploy.lock",)
    try:
        yield
    finally:
        wrapped.__defaults__ = original
