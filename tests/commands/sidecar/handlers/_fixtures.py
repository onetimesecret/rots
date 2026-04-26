# tests/commands/sidecar/handlers/_fixtures.py

"""Shared fixtures for two-phase provisioning handler tests.

Registered as a pytest plugin from the rootdir ``tests/conftest.py`` via
``pytest_plugins = ["tests.commands.sidecar.handlers._fixtures"]``. Pytest
9 forbids ``pytest_plugins`` declarations outside the rootdir conftest,
so a plain module imported as a plugin is the portable mechanism to
share these fixtures across sibling directories (notably
``tests/commands/env``).

Downstream impl + test agents (postgres, valkey, backup) consume the
fixtures registered here. The contract is stable:

* ``in_process_bus``  — :class:`InProcessRpcClient` with two peers
  (``"db-host"``, ``"web-host"``) both pointing at the local dispatcher.
  Use it to drive cross-host calls (e.g. ``postgres.bootstrap_app`` →
  ``secrets.deliver`` at ``web-host``) without a broker.
* ``fake_env_file``   — ``(path, seed)`` tuple pointing at a throwaway
  env file under ``tmp_path``. ``seed`` is a helper that writes initial
  content.
* ``postgres_service`` / ``valkey_service`` — session-scoped real
  services started via podman. Skipped when podman is not on PATH.
  Per-test isolation comes from sibling fixtures ``postgres_db`` and
  ``valkey_acl_prefix``.

**Test-services approach**: session-scoped ``podman run``. Rationale:

* The project is already podman-centric (production deploys via Quadlet);
  test agents working here are already familiar with the tooling.
* No new Python test dependencies (testcontainers would add a dep and
  couple tests to an extra library).
* Skips gracefully on dev machines without podman (``pytest.skip`` at
  fixture time), matching ``docs/testing.md``'s "don't assume specific
  podman/systemd state" rule.
* CI provisions podman in the runner, so full coverage still runs there.

Alternative approaches considered and rejected:

* **testcontainers-python** — adds a dependency; the marginal ergonomic
  win is small when we already manage container lifecycle elsewhere in
  the codebase.
* **CI-provided socket** — would require coordinating environment
  variables between the CI config and the fixture; rejected because the
  coordination overhead exceeds the benefit and local test runs would
  need a doc trail to stand up the services by hand.

Tests that do NOT need a real service (the ``secrets.deliver`` worked
example is the canonical case) should skip the service fixtures.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import time
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from rots.commands.sidecar.handlers._transport import InProcessRpcClient
from rots.sidecar.commands import CommandResult, _import_handlers, dispatch

# --- env file helper ------------------------------------------------------


@pytest.fixture
def fake_env_file(tmp_path: Path) -> tuple[Path, Callable[[str], Path]]:
    """Return ``(path, seed)`` for a throwaway ``/etc/default/onetimesecret``.

    The path is ``<tmp_path>/etc/default/onetimesecret`` and its parent is
    pre-created. ``seed(content)`` writes ``content`` to the file and
    returns the path.

    Use for tests that need a concrete env file under a writable tmp path.
    The ``secrets.deliver`` allowlist hard-codes
    ``/etc/default/onetimesecret`` — those tests must patch
    ``rots.commands.sidecar.handlers.secrets.ALLOWED_ENV_FILE`` to point
    at this tmp location. See ``test_secrets.py`` for the pattern.
    """
    parent = tmp_path / "etc" / "default"
    parent.mkdir(parents=True, exist_ok=True)
    path = parent / "onetimesecret"

    def seed(content: str) -> Path:
        path.write_text(content, encoding="utf-8")
        return path

    return path, seed


# --- in-process bus -------------------------------------------------------


@pytest.fixture
def in_process_bus() -> InProcessRpcClient:
    """A fresh :class:`InProcessRpcClient` with ``db-host`` + ``web-host`` peers.

    Both peers dispatch through :func:`rots.sidecar.commands.dispatch`.
    Tests that want to intercept only one side can register a custom
    callable via ``bus.register(host_id, my_fn)`` to override.

    Handlers under test must be imported first so the dispatcher knows
    about them. This fixture calls ``_import_handlers()`` with no role
    filter (full surface) to guarantee that.
    """
    _import_handlers()

    def local(command: str, params: dict[str, Any]) -> CommandResult:
        return dispatch(command, params)

    return InProcessRpcClient({"db-host": local, "web-host": local})


# --- service fixtures: postgres ------------------------------------------


def _podman_available() -> bool:
    return shutil.which("podman") is not None


def _wait_for_port(host: str, port: int, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1.0):
                return True
        except OSError:
            time.sleep(0.2)
    return False


def _wait_for_postgres_ready(
    container: str, timeout: float, consecutive: int = 3, interval: float = 0.5
) -> bool:
    """Wait until ``psql -U postgres`` succeeds N consecutive times inside ``container``.

    Some postgres images (e.g. ``postgres:16-alpine``) double-restart during
    init: postgres starts over the Unix socket for internal setup, shuts down,
    then restarts with the final listener config.  A single successful probe
    can land during the first-start window; requiring ``consecutive`` successes
    spaced ``interval`` seconds apart forces the cumulative probe window to
    straddle the restart gap (~1-2 s for alpine), so we only declare ready once
    the final listener is stable.
    """
    deadline = time.monotonic() + timeout
    last_stderr = ""
    streak = 0
    while time.monotonic() < deadline:
        proc = subprocess.run(
            [
                "podman",
                "exec",
                container,
                "psql",
                "-U",
                "postgres",
                "-tA",
                "-c",
                "SELECT 1",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        if proc.returncode == 0 and proc.stdout.strip() == "1":
            streak += 1
            if streak >= consecutive:
                return True
        else:
            streak = 0
            last_stderr = proc.stderr
        time.sleep(interval)
    raise RuntimeError(
        f"postgres in container {container!r} did not become ready "
        f"({consecutive} consecutive successes within {timeout}s): {last_stderr.strip()}"
    )


@pytest.fixture(scope="session")
def postgres_service() -> Iterator[dict[str, Any]]:
    """Session-scoped real postgres via ``podman run``.

    Skipped if ``podman`` is not on PATH. The container is removed at
    session teardown. Returns a dict::

        {"host": "127.0.0.1", "port": <int>, "user": "postgres",
         "password": "postgres", "container": "<name>"}

    Per-test isolation: use the ``postgres_db`` fixture, which creates
    and drops a uniquely-named database inside this shared service.
    Implementations of the postgres handlers under test MUST NOT assume
    they own the default ``postgres`` database.

    Requires podman. CI provisions podman in the runner.
    """
    if not _podman_available():
        pytest.skip("podman not available on PATH — skipping real-postgres fixture")

    name = f"rots-test-postgres-{uuid.uuid4().hex[:8]}"
    port = _pick_port()
    image = os.environ.get("ROTS_TEST_POSTGRES_IMAGE", "docker.io/library/postgres:17-trixie")

    subprocess.run(
        [
            "podman",
            "run",
            "-d",
            "--name",
            name,
            "-e",
            "POSTGRES_PASSWORD=postgres",
            "-e",
            "POSTGRES_USER=postgres",
            "-p",
            f"{port}:5432",
            image,
        ],
        check=True,
        capture_output=True,
    )

    try:
        if not _wait_for_port("127.0.0.1", port, timeout=30.0):
            raise RuntimeError(f"postgres in container {name!r} did not start within 30s")
        # TCP-open is not enough for the alpine image's double-restart init;
        # block until psql actually answers over the in-container socket.
        _wait_for_postgres_ready(name, timeout=30.0)
        yield {
            "host": "127.0.0.1",
            "port": port,
            "user": "postgres",
            "password": "postgres",
            "container": name,
        }
    finally:
        subprocess.run(
            ["podman", "rm", "-f", name],
            check=False,
            capture_output=True,
        )


@pytest.fixture
def postgres_db(postgres_service: dict[str, Any]) -> Iterator[str]:
    """Create and drop a uniquely-named database for one test.

    Yields the database name. Intended use::

        def test_bootstrap(postgres_service, postgres_db):
            # connect to postgres_service + postgres_db
            ...

    Uses ``podman exec`` via ``psql`` to avoid requiring psycopg in the
    test deps.
    """
    db_name = f"rots_{uuid.uuid4().hex[:12]}"
    container = postgres_service["container"]
    subprocess.run(
        [
            "podman",
            "exec",
            container,
            "psql",
            "-U",
            "postgres",
            "-c",
            f'CREATE DATABASE "{db_name}"',
        ],
        check=True,
        capture_output=True,
    )
    try:
        yield db_name
    finally:
        subprocess.run(
            [
                "podman",
                "exec",
                container,
                "psql",
                "-U",
                "postgres",
                "-c",
                f'DROP DATABASE IF EXISTS "{db_name}"',
            ],
            check=False,
            capture_output=True,
        )


# --- service fixtures: valkey --------------------------------------------


@pytest.fixture(scope="session")
def valkey_service() -> Iterator[dict[str, Any]]:
    """Session-scoped real valkey via ``podman run``.

    Skipped if ``podman`` is not on PATH. Returns a dict::

        {"host": "127.0.0.1", "port": <int>, "container": "<name>"}

    Per-test isolation: use the ``valkey_acl_prefix`` fixture to derive a
    unique ACL-user prefix. Tests MUST add+remove their own users —
    there is no state reset between tests.

    Requires podman. CI provisions podman in the runner.
    """
    if not _podman_available():
        pytest.skip("podman not available on PATH — skipping real-valkey fixture")

    name = f"rots-test-valkey-{uuid.uuid4().hex[:8]}"
    port = _pick_port()
    image = os.environ.get("ROTS_TEST_VALKEY_IMAGE", "docker.io/valkey/valkey:8-alpine")

    # Start valkey-server with ``--aclfile`` set so ``ACL SAVE`` / ``ACL LOAD``
    # work. Valkey 8.x treats ``aclfile`` as immutable at runtime — it cannot
    # be enabled via ``CONFIG SET`` after startup. Valkey refuses to start
    # when the aclfile is missing, so touch it first via a wrapping ``sh``
    # entrypoint. ``/data`` is the image's default working dir and is
    # writable by the valkey user.
    subprocess.run(
        [
            "podman",
            "run",
            "-d",
            "--name",
            name,
            "-p",
            f"{port}:6379",
            "--entrypoint",
            "sh",
            image,
            "-c",
            "touch /data/users.acl && exec valkey-server --aclfile /data/users.acl",
        ],
        check=True,
        capture_output=True,
    )

    try:
        if not _wait_for_port("127.0.0.1", port, timeout=30.0):
            raise RuntimeError(f"valkey in container {name!r} did not start within 30s")
        yield {
            "host": "127.0.0.1",
            "port": port,
            "container": name,
        }
    finally:
        subprocess.run(
            ["podman", "rm", "-f", name],
            check=False,
            capture_output=True,
        )


@pytest.fixture
def valkey_acl_prefix() -> str:
    """Return a unique ACL-user prefix for a single test.

    Example: ``acl_f3a9b2c1``. Tests build full names as
    ``f"{prefix}_app"`` etc. and are responsible for cleanup via
    ``ACL DELUSER``.
    """
    return f"acl_{uuid.uuid4().hex[:8]}"


# --- helpers --------------------------------------------------------------


def _pick_port() -> int:
    """Return an ephemeral port by binding to 0 and reading the assignment."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
