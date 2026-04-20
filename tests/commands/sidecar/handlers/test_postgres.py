# tests/commands/sidecar/handlers/test_postgres.py

"""Tests for the ``postgres.*`` handlers (issue #55).

Structure mirrors ``test_secrets.py``: one class per concern. Validation
tests (which must reject before issuing any SQL) carry no service fixture
and run under the ``quick`` marker. State-mutating tests take the
session-scoped ``postgres_service`` fixture plus the per-test
``postgres_db`` fixture, and are marked ``integration`` — CI provisions
podman, dev machines without podman will skip.

Key conventions:

* ``PSQL_PEER_CMD`` is swapped in each integration test so the handler
  talks to the test container instead of the production peer-auth UNIX
  socket. Requires the impl to read ``PSQL_PEER_CMD`` per-call; see the
  report for the coordination note.
* Role names are derived from a per-test uuid because
  ``postgres_service`` is session-scoped and roles are cluster-global
  (unlike databases, which are per-test via ``postgres_db``).
* ``in_process_bus`` routes ``secrets.deliver`` to the in-process
  dispatcher. End-to-end tests point :data:`secrets.ALLOWED_ENV_FILE`
  at the ``fake_env_file`` tmp path.
"""

from __future__ import annotations

import os
import subprocess
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

import rots.commands.sidecar.handlers.postgres as pg
import rots.commands.sidecar.handlers.secrets as secrets_mod
from rots.sidecar.commands import CommandResult

# --- helpers --------------------------------------------------------------


def _psql_exec(container: str, db: str, sql: str) -> subprocess.CompletedProcess[str]:
    """Run a SQL statement against the test container and return the result."""
    return subprocess.run(
        [
            "podman",
            "exec",
            container,
            "psql",
            "-U",
            "postgres",
            "-d",
            db,
            "-tA",
            "-c",
            sql,
        ],
        check=False,
        capture_output=True,
        text=True,
    )


def _role_exists(container: str, role: str) -> bool:
    """Return True iff ``role`` is present in ``pg_roles``."""
    result = _psql_exec(
        container,
        "postgres",
        f"SELECT 1 FROM pg_roles WHERE rolname = '{role}'",
    )
    return result.stdout.strip() == "1"


def _database_exists(container: str, db: str) -> bool:
    """Return True iff ``db`` is present in ``pg_database``."""
    result = _psql_exec(
        container,
        "postgres",
        f"SELECT 1 FROM pg_database WHERE datname = '{db}'",
    )
    return result.stdout.strip() == "1"


def _role_password_is_null(container: str, role: str) -> bool:
    """Return True iff the role's stored password is NULL.

    Reads ``pg_authid`` which requires superuser; the test container
    runs psql as the postgres superuser so this works.
    """
    result = _psql_exec(
        container,
        "postgres",
        f"SELECT rolpassword IS NULL FROM pg_authid WHERE rolname = '{role}'",
    )
    return result.stdout.strip() == "t"


def _drop_role_best_effort(container: str, role: str) -> None:
    """Drop ``role`` ignoring errors. For fixture teardown only."""
    _psql_exec(container, "postgres", f'DROP OWNED BY "{role}" CASCADE')
    _psql_exec(container, "postgres", f'DROP ROLE IF EXISTS "{role}"')


def _drop_database_best_effort(container: str, db: str) -> None:
    """Drop ``db`` ignoring errors. For fixture teardown only."""
    _psql_exec(container, "postgres", f'DROP DATABASE IF EXISTS "{db}"')


# --- fixtures -------------------------------------------------------------


@pytest.fixture
def unique_role() -> str:
    """Return a cluster-unique role name for one test."""
    return f"app_{uuid.uuid4().hex[:12]}"


@pytest.fixture
def patched_psql(
    monkeypatch: pytest.MonkeyPatch,
    postgres_service: dict[str, Any],
) -> Iterator[str]:
    """Redirect ``PSQL_PEER_CMD`` at the test container.

    Production uses ``sudo -u postgres psql`` over the local UNIX socket
    (peer auth). Neither applies inside the test container, so we swap
    the command so the handler speaks to the podman-hosted postgres via
    ``podman exec``.
    """
    container = postgres_service["container"]
    monkeypatch.setattr(
        pg,
        "PSQL_PEER_CMD",
        (
            "podman",
            "exec",
            "-i",
            container,
            "psql",
            "-U",
            "postgres",
            "-v",
            "ON_ERROR_STOP=1",
        ),
    )
    yield container


@pytest.fixture
def patched_env_file(
    monkeypatch: pytest.MonkeyPatch,
    fake_env_file: tuple[Path, Callable[[str], Path]],
) -> Path:
    """Point :data:`secrets.ALLOWED_ENV_FILE` at a tmp env file.

    End-to-end tests call ``bootstrap_app`` which publishes
    ``secrets.deliver`` at web-host via ``in_process_bus``. That handler
    checks the target against ``ALLOWED_ENV_FILE`` — hard-coded to
    ``/etc/default/onetimesecret`` in production. Repoint at tmp_path so
    the end-to-end test is self-contained.
    """
    path, seed = fake_env_file
    seed("")
    monkeypatch.setattr(secrets_mod, "ALLOWED_ENV_FILE", str(path))
    return path


@pytest.fixture
def bound_bus(
    monkeypatch: pytest.MonkeyPatch,
    in_process_bus: Any,
) -> Any:
    """Wire the in-process bus into the postgres handler factory.

    The handler obtains its RPC client via ``_get_rpc_client``. Swap that
    seam so cross-host calls (``secrets.deliver`` at ``peer_id``) route
    to the in-process dispatcher.
    """
    monkeypatch.setattr(pg, "_get_rpc_client", lambda: in_process_bus)
    return in_process_bus


# =========================================================================
# handle_bootstrap_app — validation (no SQL, no podman needed)
# =========================================================================


class TestBootstrapAppValidation:
    """Validation rejects bad inputs before any SQL runs.

    The ``no_subprocess`` monkeypatch asserts that ``subprocess.run`` is
    never invoked. Assumes the impl uses ``pg.subprocess.run`` (module
    import style) — the standard pattern.
    """

    pytestmark = pytest.mark.quick

    @pytest.fixture
    def no_subprocess(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def boom(*args: Any, **kwargs: Any) -> Any:
            raise AssertionError("subprocess.run must not be called for validation rejections")

        monkeypatch.setattr(pg.subprocess, "run", boom)

    def _valid(self) -> dict[str, Any]:
        return {
            "app": "myapp",
            "owner_role": "myapp",
            "peer_ip": "10.0.0.5",
            "peer_id": "web-host",
        }

    @pytest.mark.parametrize("missing", ["app", "owner_role", "peer_ip", "peer_id"])
    def test_missing_required_param(self, no_subprocess: None, missing: str):
        params = self._valid()
        del params[missing]
        result = pg.handle_bootstrap_app(params)
        assert result.success is False
        assert result.error is not None

    @pytest.mark.parametrize(
        "bad_app",
        [
            "Uppercase",
            "1leading_digit",
            "has-dash",
            "has space",
            "has;semi",
            "x" * 64,  # over the 63-char tail cap
            "",
            "_underscore_lead",
            "has.dot",
            "..",
        ],
    )
    def test_invalid_app_name(self, no_subprocess: None, bad_app: str):
        params = self._valid()
        params["app"] = bad_app
        result = pg.handle_bootstrap_app(params)
        assert result.success is False
        assert result.error is not None

    @pytest.mark.parametrize(
        "bad_owner",
        ["Uppercase", "1lead", "has-dash", "with;inj", "", ".."],
    )
    def test_invalid_owner_role(self, no_subprocess: None, bad_owner: str):
        params = self._valid()
        params["owner_role"] = bad_owner
        result = pg.handle_bootstrap_app(params)
        assert result.success is False

    @pytest.mark.parametrize("bad_peer_ip", ["", 42, None, "   "])
    def test_invalid_peer_ip(self, no_subprocess: None, bad_peer_ip: Any):
        params = self._valid()
        params["peer_ip"] = bad_peer_ip
        result = pg.handle_bootstrap_app(params)
        assert result.success is False

    @pytest.mark.parametrize("bad_peer_id", ["", 0, None])
    def test_invalid_peer_id(self, no_subprocess: None, bad_peer_id: Any):
        params = self._valid()
        params["peer_id"] = bad_peer_id
        result = pg.handle_bootstrap_app(params)
        assert result.success is False


# =========================================================================
# handle_bootstrap_app — state-mutating (real postgres)
# =========================================================================


class TestBootstrapAppHappyPath:
    """Happy-path and idempotency tests against a real postgres container."""

    pytestmark = pytest.mark.integration

    def test_creates_role_database_and_delivers_password(
        self,
        patched_psql: str,
        patched_env_file: Path,
        bound_bus: Any,
        unique_role: str,
    ):
        container = patched_psql
        try:
            result = pg.handle_bootstrap_app(
                {
                    "app": unique_role,
                    "owner_role": unique_role,
                    "peer_ip": "10.0.0.5",
                    "peer_id": "web-host",
                }
            )

            assert result.success is True, result.error
            assert result.data["role"] == unique_role
            assert result.data["database"] == unique_role
            assert result.data["password_delivered_to"] == "web-host"
            assert result.data["changed"] is True

            # Verify real state in the container.
            assert _role_exists(container, unique_role)
            assert _database_exists(container, unique_role)

            # Password landed in the env file via the in-process bus.
            body = patched_env_file.read_text()
            assert "PG_PASSWORD=" in body
            # The value is non-empty (token_urlsafe(32) is ~43 chars).
            for line in body.splitlines():
                if line.startswith("PG_PASSWORD="):
                    assert len(line) > len("PG_PASSWORD=") + 8
                    break
            else:
                pytest.fail("PG_PASSWORD line not found in env file")
        finally:
            _drop_database_best_effort(container, unique_role)
            _drop_role_best_effort(container, unique_role)

    def test_idempotent_rerun_is_noop(
        self,
        patched_psql: str,
        patched_env_file: Path,
        bound_bus: Any,
        unique_role: str,
    ):
        """Second call with same params returns changed=False and mutates nothing.

        The env file mtime is captured after the first run and compared
        after the second — the docstring specifies "No queries beyond
        the two existence checks run" and no delivery on re-run.
        """
        container = patched_psql
        params = {
            "app": unique_role,
            "owner_role": unique_role,
            "peer_ip": "10.0.0.5",
            "peer_id": "web-host",
        }
        try:
            first = pg.handle_bootstrap_app(params)
            assert first.success is True
            assert first.data["changed"] is True

            env_mtime_after_first = os.stat(patched_env_file).st_mtime_ns
            body_after_first = patched_env_file.read_text()

            second = pg.handle_bootstrap_app(params)
            assert second.success is True, second.error
            assert second.data["changed"] is False
            assert second.data["role"] == unique_role
            assert second.data["database"] == unique_role
            assert second.data["password_delivered_to"] == "web-host"

            # Env file untouched.
            assert os.stat(patched_env_file).st_mtime_ns == env_mtime_after_first
            assert patched_env_file.read_text() == body_after_first

            # State unchanged.
            assert _role_exists(container, unique_role)
            assert _database_exists(container, unique_role)
        finally:
            _drop_database_best_effort(container, unique_role)
            _drop_role_best_effort(container, unique_role)


class TestBootstrapAppExistingRole:
    """When the role already exists, no rotation occurs; receipt is echoed.

    Spec (bootstrap_app step 3, verbatim):

        "In the already-exists case the handler cannot deliver a password
        (it doesn't know one); it MUST still publish a delivery receipt
        so the operator can verify the web peer has what it needs.
        Strategy: when the role exists, skip step 4 and return
        changed=False with password_delivered_to=peer_id as an
        informational echo."

    Interpreted here as: skip step 4 entirely (no bus publish, env file
    untouched) and only echo ``password_delivered_to`` in the data
    payload. Flagged in the report.
    """

    pytestmark = pytest.mark.integration

    def test_existing_role_is_not_rotated(
        self,
        patched_psql: str,
        patched_env_file: Path,
        bound_bus: Any,
        unique_role: str,
    ):
        container = patched_psql
        # Pre-create the role. Test owns cleanup.
        _psql_exec(
            container,
            "postgres",
            f"CREATE ROLE \"{unique_role}\" LOGIN PASSWORD 'preexisting'",
        )
        try:
            env_before = patched_env_file.read_text()

            result = pg.handle_bootstrap_app(
                {
                    "app": unique_role,
                    "owner_role": unique_role,
                    "peer_ip": "10.0.0.5",
                    "peer_id": "web-host",
                }
            )

            assert result.success is True, result.error
            assert result.data["changed"] is False
            assert result.data["role"] == unique_role
            assert result.data["password_delivered_to"] == "web-host"

            # Env file untouched: no rotation happened.
            assert patched_env_file.read_text() == env_before
        finally:
            _drop_database_best_effort(container, unique_role)
            _drop_role_best_effort(container, unique_role)


class TestBootstrapAppDeliveryFailureRollback:
    """If ``secrets.deliver`` fails, the freshly-created role is dropped."""

    pytestmark = pytest.mark.integration

    def test_delivery_failure_drops_role(
        self,
        patched_psql: str,
        patched_env_file: Path,
        in_process_bus: Any,
        monkeypatch: pytest.MonkeyPatch,
        unique_role: str,
    ):
        container = patched_psql
        # Override the web-host peer to return failure; db-host continues
        # to route through the real local dispatcher.
        in_process_bus.register(
            "web-host",
            lambda cmd, p: CommandResult.fail("delivery failed: broker down"),
        )
        monkeypatch.setattr(pg, "_get_rpc_client", lambda: in_process_bus)

        try:
            result = pg.handle_bootstrap_app(
                {
                    "app": unique_role,
                    "owner_role": unique_role,
                    "peer_ip": "10.0.0.5",
                    "peer_id": "web-host",
                }
            )

            assert result.success is False
            assert result.error is not None

            # Rollback: role must not exist, database must not exist
            # (database creation is step 5, after delivery).
            assert not _role_exists(container, unique_role)
            assert not _database_exists(container, unique_role)

            # Env file untouched (delivery failed, nothing was written).
            assert patched_env_file.read_text() == ""
        finally:
            _drop_database_best_effort(container, unique_role)
            _drop_role_best_effort(container, unique_role)


# =========================================================================
# handle_add_hba
# =========================================================================


class TestAddHbaValidation:
    """Name and content validation reject before filesystem work."""

    pytestmark = pytest.mark.quick

    @pytest.fixture
    def tmp_hba_dir(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> Path:
        d = tmp_path / "pg_hba.d"
        d.mkdir()
        monkeypatch.setattr(pg, "PG_HBA_D", str(d))
        return d

    @pytest.fixture
    def no_subprocess(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def boom(*args: Any, **kwargs: Any) -> Any:
            raise AssertionError("subprocess.run must not be called for validation rejections")

        monkeypatch.setattr(pg.subprocess, "run", boom)

    @pytest.mark.parametrize(
        "bad_name",
        [
            "/absolute",
            "../escape",
            "dir/sub",
            ".hidden",
            "",
            "x" * 65,  # > 64 char cap
        ],
    )
    def test_rejects_bad_name(
        self,
        tmp_hba_dir: Path,
        no_subprocess: None,
        bad_name: str,
    ):
        result = pg.handle_add_hba({"name": bad_name, "content": "host all all 10.0.0.0/24 md5\n"})
        assert result.success is False
        # No file was created anywhere.
        assert list(tmp_hba_dir.iterdir()) == []

    def test_rejects_empty_content(
        self,
        tmp_hba_dir: Path,
        no_subprocess: None,
    ):
        result = pg.handle_add_hba({"name": "10-web", "content": ""})
        assert result.success is False
        assert list(tmp_hba_dir.iterdir()) == []

    @pytest.mark.parametrize(
        "bad_content",
        [
            "garbage line\n",
            "DROP DATABASE\n",
            "evil\n",
        ],
    )
    def test_rejects_content_without_hba_verb(
        self,
        tmp_hba_dir: Path,
        no_subprocess: None,
        bad_content: str,
    ):
        result = pg.handle_add_hba({"name": "10-web", "content": bad_content})
        assert result.success is False
        assert list(tmp_hba_dir.iterdir()) == []

    def test_missing_params(self, tmp_hba_dir: Path, no_subprocess: None):
        assert pg.handle_add_hba({"content": "host all all 10/24 md5\n"}).success is False
        assert pg.handle_add_hba({"name": "10-web"}).success is False


class TestAddHbaHappyPath:
    """Write, reload, idempotency, and content-change behaviour."""

    pytestmark = pytest.mark.integration

    @pytest.fixture
    def tmp_hba_dir(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> Path:
        d = tmp_path / "pg_hba.d"
        d.mkdir()
        monkeypatch.setattr(pg, "PG_HBA_D", str(d))
        return d

    def test_writes_file_and_reloads(
        self,
        patched_psql: str,
        tmp_hba_dir: Path,
    ):
        content = "host all all 10.0.0.0/24 md5\n"
        result = pg.handle_add_hba({"name": "10-web", "content": content})

        assert result.success is True, result.error
        assert result.data["changed"] is True
        assert result.data["reloaded"] is True

        target = tmp_hba_dir / "10-web.conf"
        assert target.exists()
        assert target.read_text() == content

    def test_appends_conf_suffix_if_missing(
        self,
        patched_psql: str,
        tmp_hba_dir: Path,
    ):
        pg.handle_add_hba({"name": "20-app", "content": "host all all 10.0.0.0/24 md5\n"})
        assert (tmp_hba_dir / "20-app.conf").exists()

    def test_idempotent_rerun_is_noop(
        self,
        patched_psql: str,
        tmp_hba_dir: Path,
    ):
        content = "host all all 10.0.0.0/24 md5\n"
        first = pg.handle_add_hba({"name": "10-web", "content": content})
        assert first.data["changed"] is True

        target = tmp_hba_dir / "10-web.conf"
        mtime_after_first = os.stat(target).st_mtime_ns

        second = pg.handle_add_hba({"name": "10-web", "content": content})
        assert second.success is True, second.error
        assert second.data["changed"] is False
        assert second.data["reloaded"] is False
        assert os.stat(target).st_mtime_ns == mtime_after_first

    def test_content_change_rewrites(
        self,
        patched_psql: str,
        tmp_hba_dir: Path,
    ):
        pg.handle_add_hba({"name": "10-web", "content": "host all all 10.0.0.0/24 md5\n"})
        result = pg.handle_add_hba(
            {"name": "10-web", "content": "host all all 192.168.0.0/24 md5\n"}
        )
        assert result.success is True, result.error
        assert result.data["changed"] is True
        assert result.data["reloaded"] is True

        target = tmp_hba_dir / "10-web.conf"
        assert "192.168.0.0/24" in target.read_text()
        assert "10.0.0.0/24" not in target.read_text()


class TestAddHbaReloadFailure:
    """If the reload call fails, the file is NOT rolled back.

    Spec (add_hba error mapping, verbatim):

        "the file is already written, but the reload failed. Return
        CommandResult.fail with a warning that the file exists and
        manual systemctl reload postgresql is needed. Do NOT
        auto-rollback the file."

    The "warning" placement is ambiguous — it could live in
    ``result.error`` or ``result.warnings``. This test accepts either.
    Flagged in the report.
    """

    pytestmark = pytest.mark.quick

    def test_reload_failure_leaves_file_in_place(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        hba_dir = tmp_path / "pg_hba.d"
        hba_dir.mkdir()
        monkeypatch.setattr(pg, "PG_HBA_D", str(hba_dir))

        def failing_run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
            raise subprocess.CalledProcessError(
                1,
                list(args[0]) if args else [],
                stderr="pg_reload_conf failed",
            )

        monkeypatch.setattr(pg.subprocess, "run", failing_run)

        result = pg.handle_add_hba({"name": "10-web", "content": "host all all 10.0.0.0/24 md5\n"})

        assert result.success is False

        # File MUST be on disk — no rollback per spec.
        target = hba_dir / "10-web.conf"
        assert target.exists(), "Per spec, the file is NOT rolled back on reload failure"

        # Warning about reload / manual reload surfaced somewhere.
        err_lower = (result.error or "").lower()
        warn_joined = " ".join(result.warnings).lower()
        surfaced = "reload" in err_lower or "reload" in warn_joined
        assert surfaced, (
            f"Expected a reload-related warning in error={result.error!r} "
            f"or warnings={result.warnings!r}"
        )


# =========================================================================
# handle_rotate_password
# =========================================================================


class TestRotatePasswordValidation:
    """Identifier validation rejects before any ALTER ROLE."""

    pytestmark = pytest.mark.quick

    @pytest.fixture
    def no_subprocess(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def boom(*args: Any, **kwargs: Any) -> Any:
            raise AssertionError("subprocess.run must not be called for validation rejections")

        monkeypatch.setattr(pg.subprocess, "run", boom)

    @pytest.mark.parametrize(
        "bad_role",
        [
            "Uppercase",
            "1lead",
            "has-dash",
            "has space",
            "inj;drop",
            "",
            "..",
        ],
    )
    def test_rejects_bad_role(self, no_subprocess: None, bad_role: str):
        result = pg.handle_rotate_password({"role": bad_role, "peer_id": "web-host"})
        assert result.success is False

    @pytest.mark.parametrize("missing", ["role", "peer_id"])
    def test_missing_param(self, no_subprocess: None, missing: str):
        params = {"role": "myapp", "peer_id": "web-host"}
        del params[missing]
        result = pg.handle_rotate_password(params)
        assert result.success is False


class TestRotatePasswordHappyPath:
    """Rotate generates a new password and delivers it via the bus."""

    pytestmark = pytest.mark.integration

    def test_rotate_delivers_new_password(
        self,
        patched_psql: str,
        patched_env_file: Path,
        bound_bus: Any,
        unique_role: str,
    ):
        container = patched_psql
        # Pre-create role. rotate_password is NOT idempotent; spec says
        # the role must already exist.
        _psql_exec(
            container,
            "postgres",
            f"CREATE ROLE \"{unique_role}\" LOGIN PASSWORD 'original'",
        )
        try:
            # Seed the env file with an existing PG_PASSWORD so we can
            # verify it changed.
            patched_env_file.write_text("PG_PASSWORD=original_delivered\n")

            result = pg.handle_rotate_password({"role": unique_role, "peer_id": "web-host"})

            assert result.success is True, result.error
            assert result.data["delivered_to"] == "web-host"
            assert result.data["changed"] is True

            body = patched_env_file.read_text()
            assert "PG_PASSWORD=" in body
            assert "original_delivered" not in body
        finally:
            _drop_role_best_effort(container, unique_role)


class TestRotatePasswordMissingRole:
    """Rotate fails cleanly when the role does not exist."""

    pytestmark = pytest.mark.integration

    def test_missing_role_fails_without_alter(
        self,
        patched_psql: str,
        patched_env_file: Path,
        bound_bus: Any,
        unique_role: str,
    ):
        container = patched_psql
        # Role deliberately NOT created.
        seeded = "PG_PASSWORD=leave_me_alone\n"
        patched_env_file.write_text(seeded)

        result = pg.handle_rotate_password({"role": unique_role, "peer_id": "web-host"})

        assert result.success is False
        assert result.error is not None

        # Env file untouched.
        assert patched_env_file.read_text() == seeded

        # Role still absent.
        assert not _role_exists(container, unique_role)


class TestRotatePasswordDeliveryFailure:
    """Delivery failure triggers a best-effort revoke via ALTER ROLE ... PASSWORD NULL."""

    pytestmark = pytest.mark.integration

    def test_delivery_failure_revokes_password(
        self,
        patched_psql: str,
        patched_env_file: Path,
        in_process_bus: Any,
        monkeypatch: pytest.MonkeyPatch,
        unique_role: str,
    ):
        container = patched_psql
        _psql_exec(
            container,
            "postgres",
            f"CREATE ROLE \"{unique_role}\" LOGIN PASSWORD 'original'",
        )
        try:
            in_process_bus.register(
                "web-host",
                lambda cmd, p: CommandResult.fail("delivery failed: broker down"),
            )
            monkeypatch.setattr(pg, "_get_rpc_client", lambda: in_process_bus)

            result = pg.handle_rotate_password({"role": unique_role, "peer_id": "web-host"})

            assert result.success is False

            # Revoke best-effort: the role's stored password is now NULL.
            # Requires postgres superuser, which the test container uses.
            assert _role_password_is_null(container, unique_role), (
                "Delivery failure must trigger ALTER ROLE ... PASSWORD NULL"
            )

            # Both the delivery failure and any revoke issues surface
            # somewhere — spec says "surface both errors in warnings"
            # but the outer result is fail. Loose check either way.
            err_lower = (result.error or "").lower()
            warn_joined = " ".join(result.warnings).lower()
            assert (
                "deliver" in err_lower
                or "deliver" in warn_joined
                or "broker" in err_lower
                or "broker" in warn_joined
            )
        finally:
            _drop_role_best_effort(container, unique_role)
