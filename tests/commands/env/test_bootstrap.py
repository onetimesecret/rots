# tests/commands/env/test_bootstrap.py

"""Tests for the ``rots env bootstrap`` operator command (issue #55).

Command contract (from :mod:`rots.commands.env.bootstrap`)::

    rots env bootstrap --env <env> [--rotate] [--dry-run] [--json]

Orchestrates, against ``db.host_id`` from the ``.otsinfra.yaml`` marker
(``envs.<env>`` block):

1. ``postgres.bootstrap_app`` (or ``postgres.rotate_password`` under
   ``--rotate``)
2. ``valkey.create_acl_user``
3. ``postgres.add_hba``
4. ``backup.install`` when a ``backup`` block is present under the env.

Fails fast on the first step that fails. Exit codes follow the project
convention:

* ``0`` -- every step ok (or ``--dry-run`` printed its plan).
* ``1`` (``EXIT_FAILURE``) -- an RPC failed, timed out, or the remote
  returned an error.
* ``3`` (``EXIT_PRECOND``) -- ``.otsinfra.yaml`` missing or
  schema-invalid.

The RPC client factory :func:`rots.commands.env.bootstrap._get_rpc_client`
is the test seam.

Import strategy
---------------

``rots.infra_marker`` and ``rots.commands.env.bootstrap`` land in parallel
with this file. ``pytest.importorskip`` at module scope keeps collection
green if either module is absent when the suite runs.
"""

from __future__ import annotations

import json
import os
import subprocess
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

# Modules under test — imported via importorskip so collection passes
# before the impl agent has landed them.
infra_marker = pytest.importorskip(
    "rots.infra_marker",
    reason="rots.infra_marker module not yet available (impl in flight)",
)
bootstrap_mod = pytest.importorskip(
    "rots.commands.env.bootstrap",
    reason="rots.commands.env.bootstrap module not yet available (impl in flight)",
)

# Handler modules (already landed) — safe to import directly.
import rots.commands.sidecar.handlers.backup as backup_mod  # noqa: E402
import rots.commands.sidecar.handlers.postgres as pg_mod  # noqa: E402
import rots.commands.sidecar.handlers.secrets as secrets_mod  # noqa: E402
import rots.commands.sidecar.handlers.valkey as valkey_mod  # noqa: E402
from rots.commands.common import EXIT_FAILURE, EXIT_PRECOND, EXIT_SUCCESS  # noqa: E402
from rots.sidecar.commands import CommandResult  # noqa: E402

# =========================================================================
# Marker fixture helpers
# =========================================================================


def _valid_marker_yaml(
    *,
    env_name: str = "eu-demo",
    db_host_id: str = "db-host",
    web_host_id: str = "web-host",
    web_ip: str = "10.0.0.5",
    app_name: str = "myapp",
    owner_role: str = "myapp",
    valkey_rules: tuple[str, ...] = ("on", "~*", "+@all"),
    backup_block: bool = False,
) -> str:
    """Return the YAML body for a valid ``.otsinfra.yaml`` marker.

    The impl expects an ``envs.<env>`` block shape (see
    :func:`rots.infra_marker.load_env_config_from_file`)::

        envs:
          eu-demo:
            db: {...}
            web: {...}
            app: {...}
            valkey: {...}
            backup: {...}   # optional
    """
    rules_block = "\n".join(f"      - {r!r}" for r in valkey_rules)
    backup_section = (
        "\n    backup:\n"
        "      profile: db-daily\n"
        "      target: b2-ots:/eu-demo/db\n"
        "      schedule: daily\n"
        if backup_block
        else ""
    )
    return (
        "envs:\n"
        f"  {env_name}:\n"
        "    db:\n"
        f"      host_id: {db_host_id}\n"
        "    web:\n"
        f"      host_id: {web_host_id}\n"
        f"      ip: {web_ip}\n"
        "    app:\n"
        f"      name: {app_name}\n"
        f"      owner_role: {owner_role}\n"
        "    valkey:\n"
        "      rules:\n"
        f"{rules_block}\n"
        f"{backup_section}"
    )


def _write_marker(tmp_path: Path, body: str) -> Path:
    marker = tmp_path / ".otsinfra.yaml"
    marker.write_text(body, encoding="utf-8")
    return marker


# =========================================================================
# EnvConfig / marker parsing
# =========================================================================


class TestEnvConfig:
    """Coverage for ``rots.infra_marker`` marker loading + ``EnvConfig``.

    The loader is :func:`rots.infra_marker.load_env_config`:

        load_env_config(env, *, start=None) -> EnvConfig

    ``EnvConfig`` is a frozen dataclass exposing:

    * ``env: str``
    * ``db.host_id``
    * ``web.host_id`` / ``web.ip``
    * ``app.name`` / ``app.owner_role``
    * ``valkey.rules`` (tuple[str, ...])
    * ``backup`` — ``None`` or a struct with ``profile`` / ``target`` /
      ``schedule``.
    * ``source: Path | None`` — path to the marker file.

    All schema violations raise
    :class:`rots.infra_marker.InfraMarkerError`.
    """

    pytestmark = pytest.mark.quick

    @pytest.fixture
    def marker_path(self, tmp_path: Path) -> Path:
        return _write_marker(tmp_path, _valid_marker_yaml())

    def test_valid_marker_parses_required_fields(
        self,
        marker_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """All required dotted keys populate ``EnvConfig``."""
        monkeypatch.chdir(marker_path.parent)
        cfg = infra_marker.load_env_config("eu-demo")

        assert cfg.env == "eu-demo"
        assert cfg.db.host_id == "db-host"
        assert cfg.web.host_id == "web-host"
        assert cfg.web.ip == "10.0.0.5"
        assert cfg.app.name == "myapp"
        assert cfg.app.owner_role == "myapp"
        assert cfg.valkey.rules == ("on", "~*", "+@all")
        assert cfg.source == marker_path

    def test_backup_absent_leaves_field_none(
        self,
        marker_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        monkeypatch.chdir(marker_path.parent)
        cfg = infra_marker.load_env_config("eu-demo")
        assert cfg.backup is None

    def test_backup_present_populates_struct(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        _write_marker(tmp_path, _valid_marker_yaml(backup_block=True))
        monkeypatch.chdir(tmp_path)

        cfg = infra_marker.load_env_config("eu-demo")
        assert cfg.backup is not None
        assert cfg.backup.profile == "db-daily"
        assert cfg.backup.target == "b2-ots:/eu-demo/db"
        assert cfg.backup.schedule == "daily"

    @pytest.mark.parametrize(
        "missing_dotted",
        [
            "db.host_id",
            "web.host_id",
            "web.ip",
            "app.name",
            "app.owner_role",
            "valkey.rules",
        ],
    )
    def test_missing_required_key_raises_descriptive_error(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        missing_dotted: str,
    ):
        """Each missing required key should name itself in the error.

        The marker is built by parsing a valid YAML body and popping the
        target key, then re-emitting. This leaves the parent block as an
        empty mapping so the impl surfaces the *specific* missing leaf
        (``envs.eu-demo.db.host_id is required``) rather than the
        higher-level "envs.eu-demo.db is required".
        """
        import yaml

        parts = missing_dotted.split(".")
        parent, leaf = parts[0], parts[1]
        data = yaml.safe_load(_valid_marker_yaml())
        env_block = data["envs"]["eu-demo"]
        # Pop the leaf key but keep the parent mapping so the loader can
        # traverse to it and report the specific leaf as missing.
        env_block[parent].pop(leaf, None)
        _write_marker(tmp_path, yaml.safe_dump(data, sort_keys=False))
        monkeypatch.chdir(tmp_path)

        with pytest.raises(infra_marker.InfraMarkerError) as exc_info:
            infra_marker.load_env_config("eu-demo")

        # Error surfaces the dotted key path (impl uses ``envs.<env>.<key>``).
        msg = str(exc_info.value)
        assert missing_dotted in msg or leaf in msg, (
            f"Expected {missing_dotted!r} (or its leaf {leaf!r}) in error; got: {msg!r}"
        )

    def test_unknown_env_name_raises_with_available_envs(
        self,
        marker_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        monkeypatch.chdir(marker_path.parent)
        with pytest.raises(infra_marker.InfraMarkerError) as exc_info:
            infra_marker.load_env_config("this-env-does-not-exist")

        msg = str(exc_info.value)
        # Per impl: lists available envs in the message.
        assert "this-env-does-not-exist" in msg or "eu-demo" in msg

    def test_walk_up_discovery_from_nested_subdir(
        self,
        marker_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Walking up from a deep subdirectory finds the marker."""
        nested = marker_path.parent / "a" / "b" / "c"
        nested.mkdir(parents=True, exist_ok=True)
        monkeypatch.chdir(nested)

        cfg = infra_marker.load_env_config("eu-demo")
        assert cfg.db.host_id == "db-host"

    def test_extra_keys_tolerated_forward_compat(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Unknown top-level keys under envs.<env> must not break loading."""
        body = _valid_marker_yaml().rstrip() + "\n    future_key: some-value\n"
        _write_marker(tmp_path, body)
        monkeypatch.chdir(tmp_path)

        cfg = infra_marker.load_env_config("eu-demo")
        assert cfg.db.host_id == "db-host"

    def test_missing_marker_file_raises_infra_marker_error(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """No marker anywhere up the tree -> :class:`InfraMarkerError`."""
        nested = tmp_path / "isolated"
        nested.mkdir()
        # Add a ``.git`` so walk-up does not escape to the ancestor where
        # a real project marker could live.
        (nested / ".git").mkdir()
        monkeypatch.chdir(nested)

        with pytest.raises(infra_marker.InfraMarkerError):
            infra_marker.load_env_config("eu-demo")


# =========================================================================
# Bootstrap orchestration — shared helpers
# =========================================================================


def _psql_exec(container: str, db: str, sql: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["podman", "exec", container, "psql", "-U", "postgres", "-d", db, "-tA", "-c", sql],
        check=False,
        capture_output=True,
        text=True,
    )


def _role_exists_in_container(container: str, role: str) -> bool:
    result = _psql_exec(
        container,
        "postgres",
        f"SELECT 1 FROM pg_roles WHERE rolname = '{role}'",
    )
    return result.stdout.strip() == "1"


def _database_exists_in_container(container: str, db: str) -> bool:
    result = _psql_exec(
        container,
        "postgres",
        f"SELECT 1 FROM pg_database WHERE datname = '{db}'",
    )
    return result.stdout.strip() == "1"


def _drop_role_best_effort(container: str, role: str) -> None:
    _psql_exec(container, "postgres", f'DROP OWNED BY "{role}" CASCADE')
    _psql_exec(container, "postgres", f'DROP ROLE IF EXISTS "{role}"')


def _drop_database_best_effort(container: str, db: str) -> None:
    _psql_exec(container, "postgres", f'DROP DATABASE IF EXISTS "{db}"')


def _valkey_user_exists(valkey_container: str, name: str) -> bool:
    proc = subprocess.run(
        ["podman", "exec", valkey_container, "valkey-cli", "ACL", "GETUSER", name],
        check=False,
        capture_output=True,
        text=True,
    )
    return proc.returncode == 0 and bool(proc.stdout.strip())


def _acl_deluser_best_effort(valkey_container: str, name: str) -> None:
    subprocess.run(
        ["podman", "exec", valkey_container, "valkey-cli", "ACL", "DELUSER", name],
        check=False,
        capture_output=True,
        text=True,
    )


def _call_bootstrap(**kwargs: Any) -> int:
    """Invoke ``bootstrap`` and return its exit code.

    The command always ends in ``sys.exit(...)``; direct-call pattern
    mirrors ``tests/commands/test_env.py`` and
    ``tests/commands/workflow/test_trigger.py``.
    """
    try:
        bootstrap_mod.bootstrap(**kwargs)
    except SystemExit as exc:
        code = exc.code
        if code is None or code is True:
            return 0
        if isinstance(code, int):
            return code
        return 1
    return 0


# =========================================================================
# Bootstrap orchestration — fixtures
# =========================================================================


@pytest.fixture
def unique_app_name() -> str:
    """Return a cluster-unique identifier for role/database/ACL user.

    Must satisfy the handlers' identifier regex
    (``^[a-z][a-z0-9_]{0,62}$``) because postgres validates ``app`` and
    ``owner_role`` against it. ``uuid.uuid4().hex`` is lowercase alnum.
    """
    return f"app_{uuid.uuid4().hex[:10]}"


@pytest.fixture
def patched_psql(
    monkeypatch: pytest.MonkeyPatch,
    postgres_service: dict[str, Any],
) -> str:
    """Redirect ``PSQL_PEER_CMD`` at the test container (mirrors test_postgres.py)."""
    container = postgres_service["container"]
    monkeypatch.setattr(
        pg_mod,
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
    return container


@pytest.fixture
def patched_valkey_cli(
    monkeypatch: pytest.MonkeyPatch,
    valkey_service: dict[str, Any],
) -> str:
    """Redirect ``VALKEY_CLI`` at the test container (mirrors test_valkey.py)."""
    container = valkey_service["container"]
    monkeypatch.setattr(
        valkey_mod,
        "VALKEY_CLI",
        ("podman", "exec", container, "valkey-cli"),
    )
    return container


@pytest.fixture
def patched_env_file(
    monkeypatch: pytest.MonkeyPatch,
    fake_env_file: tuple[Path, Callable[[str], Path]],
) -> Path:
    """Point :data:`secrets.ALLOWED_ENV_FILE` at a tmp env file; seed empty."""
    path, seed = fake_env_file
    seed("")
    monkeypatch.setattr(secrets_mod, "ALLOWED_ENV_FILE", str(path))
    return path


@pytest.fixture
def patched_hba_dir(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> Path:
    d = tmp_path / "pg_hba.d"
    d.mkdir()
    monkeypatch.setattr(pg_mod, "PG_HBA_D", str(d))
    return d


@pytest.fixture
def patched_backup_dirs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> dict[str, Path]:
    """Redirect backup artefact directories at tmp_path."""
    systemd_dir = tmp_path / "systemd"
    systemd_dir.mkdir()
    rclone_dir = tmp_path / "rclone"
    rclone_dir.mkdir()
    monkeypatch.setattr(backup_mod, "SYSTEMD_DIR", str(systemd_dir))
    monkeypatch.setattr(backup_mod, "RCLONE_FRAGMENTS_DIR", str(rclone_dir))
    return {"systemd": systemd_dir, "rclone": rclone_dir}


@pytest.fixture
def bound_bus(
    monkeypatch: pytest.MonkeyPatch,
    in_process_bus: Any,
) -> Any:
    """Wire the in-process bus into the operator command + handler seams.

    * ``rots.commands.env.bootstrap._get_rpc_client`` drives the command's
      publishes to ``db.host_id``.
    * ``pg._get_rpc_client`` / ``valkey._get_rpc_client`` drive the
      handler-internal ``secrets.deliver`` hop to the web peer.
    """
    monkeypatch.setattr(bootstrap_mod, "_get_rpc_client", lambda: in_process_bus)
    monkeypatch.setattr(pg_mod, "_get_rpc_client", lambda: in_process_bus)
    monkeypatch.setattr(valkey_mod, "_get_rpc_client", lambda: in_process_bus)
    return in_process_bus


@pytest.fixture
def marker_at_cwd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    unique_app_name: str,
    request: pytest.FixtureRequest,
) -> tuple[Path, str]:
    """Place a valid ``.otsinfra.yaml`` under ``tmp_path`` and ``chdir``.

    Returns ``(marker_path, env_name)``. Pass ``indirect=True`` with a
    ``True`` param to include a ``backup`` block.
    """
    backup_block = bool(getattr(request, "param", False))
    body = _valid_marker_yaml(
        app_name=unique_app_name,
        owner_role=unique_app_name,
        backup_block=backup_block,
    )
    marker = _write_marker(tmp_path, body)
    monkeypatch.chdir(tmp_path)
    return marker, "eu-demo"


# =========================================================================
# Bootstrap orchestration — test class
# =========================================================================


class TestBootstrapOrchestration:
    """End-to-end coverage for ``rots env bootstrap``.

    Happy-path + failure-mode tests that mutate real services carry
    ``pytest.mark.integration``; pure validation / dry-run cases carry
    ``pytest.mark.quick``.
    """

    # ---- happy path -----------------------------------------------------

    @pytest.mark.integration
    def test_full_bootstrap_creates_role_db_acl_hba_and_delivers_secrets(
        self,
        patched_psql: str,
        patched_valkey_cli: str,
        patched_env_file: Path,
        patched_hba_dir: Path,
        patched_backup_dirs: dict[str, Path],
        bound_bus: Any,
        marker_at_cwd: tuple[Path, str],
        unique_app_name: str,
    ):
        """The happy path mutates every expected step.

        Postgres role + database present; valkey ACL user present;
        ``pg_hba.d/10-web.conf`` written; env file has both
        ``PG_PASSWORD`` and ``VALKEY_PASSWORD``.
        """
        pg_container = patched_psql
        valkey_container = patched_valkey_cli
        _, env_name = marker_at_cwd

        try:
            code = _call_bootstrap(env=env_name)
            assert code == EXIT_SUCCESS

            # Postgres state.
            assert _role_exists_in_container(pg_container, unique_app_name)
            assert _database_exists_in_container(pg_container, unique_app_name)

            # Valkey state: the ACL user exists.
            assert _valkey_user_exists(valkey_container, unique_app_name)

            # pg_hba drop-in — impl hard-codes name ``10-web``.
            target = patched_hba_dir / "10-web.conf"
            assert target.exists(), f"add_hba did not write {target}"
            # Content should mention the web IP and the role.
            body = target.read_text()
            assert "10.0.0.5" in body
            assert unique_app_name in body

            # Env file received both secrets.
            env_body = patched_env_file.read_text()
            assert "PG_PASSWORD=" in env_body, f"PG_PASSWORD missing; body={env_body!r}"
            assert "VALKEY_PASSWORD=" in env_body, f"VALKEY_PASSWORD missing; body={env_body!r}"

            # Backup absent from marker -> no backup artefacts.
            assert list(patched_backup_dirs["systemd"].iterdir()) == []
            assert list(patched_backup_dirs["rclone"].iterdir()) == []
        finally:
            _drop_database_best_effort(pg_container, unique_app_name)
            _drop_role_best_effort(pg_container, unique_app_name)
            _acl_deluser_best_effort(valkey_container, unique_app_name)

    @pytest.mark.integration
    @pytest.mark.parametrize("marker_at_cwd", [True], indirect=True)
    def test_happy_path_with_backup_block_installs_backup(
        self,
        patched_psql: str,
        patched_valkey_cli: str,
        patched_env_file: Path,
        patched_hba_dir: Path,
        patched_backup_dirs: dict[str, Path],
        bound_bus: Any,
        marker_at_cwd: tuple[Path, str],
        unique_app_name: str,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """With a ``backup`` block in the marker, the backup handler runs.

        ``systemctl`` / ``systemd-analyze`` are stubbed to succeed
        deterministically -- the handler fixtures only provide postgres +
        valkey containers; the host may not have a live systemd.
        """
        pg_container = patched_psql
        valkey_container = patched_valkey_cli
        _, env_name = marker_at_cwd

        real_run = subprocess.run

        def fake_run(cmd: Any, *args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
            if (
                isinstance(cmd, list | tuple)
                and cmd
                and cmd[0]
                in (
                    "systemctl",
                    "systemd-analyze",
                )
            ):
                return subprocess.CompletedProcess(list(cmd), 0, "", "")
            return real_run(cmd, *args, **kwargs)

        monkeypatch.setattr(backup_mod.subprocess, "run", fake_run)

        try:
            code = _call_bootstrap(env=env_name)
            assert code == EXIT_SUCCESS, "full bootstrap with backup block should succeed"

            systemd_files = sorted(p.name for p in patched_backup_dirs["systemd"].iterdir())
            assert any(f.endswith(".service") for f in systemd_files), systemd_files
            assert any(f.endswith(".timer") for f in systemd_files), systemd_files
        finally:
            _drop_database_best_effort(pg_container, unique_app_name)
            _drop_role_best_effort(pg_container, unique_app_name)
            _acl_deluser_best_effort(valkey_container, unique_app_name)

    @pytest.mark.integration
    def test_happy_path_stdout_receipts_include_changed_markers(
        self,
        patched_psql: str,
        patched_valkey_cli: str,
        patched_env_file: Path,
        patched_hba_dir: Path,
        patched_backup_dirs: dict[str, Path],
        bound_bus: Any,
        marker_at_cwd: tuple[Path, str],
        unique_app_name: str,
        capsys: pytest.CaptureFixture[str],
    ):
        """Human-readable output surfaces a ``CHANGED`` marker per step.

        Per impl's ``_print_results_text``: one ``[CHANGED]`` line per
        mutating step (postgres, valkey, add_hba). Test asserts at least
        two CHANGED markers — tolerates implementation wiggle (e.g. an
        early step being non-mutating on an unusual path).
        """
        pg_container = patched_psql
        valkey_container = patched_valkey_cli
        _, env_name = marker_at_cwd

        try:
            code = _call_bootstrap(env=env_name)
            assert code == EXIT_SUCCESS
            captured = capsys.readouterr()
            combined = captured.out + captured.err
            # Impl emits "[CHANGED]" for mutating steps.
            assert combined.count("CHANGED") >= 2 or combined.lower().count("changed") >= 2, (
                f"Expected multiple change markers in output; got:\n{combined}"
            )
        finally:
            _drop_database_best_effort(pg_container, unique_app_name)
            _drop_role_best_effort(pg_container, unique_app_name)
            _acl_deluser_best_effort(valkey_container, unique_app_name)

    # ---- idempotency ----------------------------------------------------

    @pytest.mark.integration
    def test_second_run_is_idempotent_noop(
        self,
        patched_psql: str,
        patched_valkey_cli: str,
        patched_env_file: Path,
        patched_hba_dir: Path,
        patched_backup_dirs: dict[str, Path],
        bound_bus: Any,
        marker_at_cwd: tuple[Path, str],
        unique_app_name: str,
    ):
        """Re-running bootstrap leaves state + env file unchanged.

        * ``postgres.bootstrap_app`` short-circuits with ``changed=False``
          when the owner role already exists (cannot re-derive the password).
        * ``valkey.create_acl_user`` is idempotent on identical rules
          (no token rotation).
        * ``postgres.add_hba`` is byte-for-byte no-op on re-apply.

        Env file mtime must not advance.
        """
        pg_container = patched_psql
        valkey_container = patched_valkey_cli
        _, env_name = marker_at_cwd

        try:
            assert _call_bootstrap(env=env_name) == EXIT_SUCCESS
            env_mtime_first = os.stat(patched_env_file).st_mtime_ns
            env_body_first = patched_env_file.read_text()

            assert _call_bootstrap(env=env_name) == EXIT_SUCCESS
            assert os.stat(patched_env_file).st_mtime_ns == env_mtime_first
            assert patched_env_file.read_text() == env_body_first
        finally:
            _drop_database_best_effort(pg_container, unique_app_name)
            _drop_role_best_effort(pg_container, unique_app_name)
            _acl_deluser_best_effort(valkey_container, unique_app_name)

    # ---- --dry-run ------------------------------------------------------

    @pytest.mark.quick
    def test_dry_run_publishes_nothing_and_mutates_nothing(
        self,
        patched_env_file: Path,
        patched_hba_dir: Path,
        patched_backup_dirs: dict[str, Path],
        in_process_bus: Any,
        marker_at_cwd: tuple[Path, str],
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ):
        """``--dry-run`` prints the plan without publishing any RPC.

        The bus is replaced with one that raises on publish — if the
        command publishes anything under dry-run, the test fails.
        """
        _, env_name = marker_at_cwd

        def refusing_publish(*args: Any, **kwargs: Any) -> CommandResult:
            raise AssertionError(
                f"RPC publish must not happen under --dry-run; args={args} kwargs={kwargs}"
            )

        monkeypatch.setattr(in_process_bus, "publish", refusing_publish)
        monkeypatch.setattr(bootstrap_mod, "_get_rpc_client", lambda: in_process_bus)
        monkeypatch.setattr(pg_mod, "_get_rpc_client", lambda: in_process_bus)
        monkeypatch.setattr(valkey_mod, "_get_rpc_client", lambda: in_process_bus)

        env_mtime_before = (
            os.stat(patched_env_file).st_mtime_ns if patched_env_file.exists() else None
        )

        code = _call_bootstrap(env=env_name, dry_run=True)
        assert code == EXIT_SUCCESS

        if env_mtime_before is None:
            assert not patched_env_file.exists() or patched_env_file.read_text() == ""
        else:
            assert os.stat(patched_env_file).st_mtime_ns == env_mtime_before
        assert list(patched_hba_dir.iterdir()) == []
        assert list(patched_backup_dirs["systemd"].iterdir()) == []
        assert list(patched_backup_dirs["rclone"].iterdir()) == []

        captured = capsys.readouterr()
        combined = (captured.out + captured.err).lower()
        # Impl emits ``Dry-run plan for env=...`` and lists each step's
        # command name. Each step keyword should be visible.
        assert "dry-run" in combined or "plan" in combined
        for keyword in ("postgres", "valkey"):
            assert keyword in combined, (
                f"Dry-run output missing keyword {keyword!r}; output was:\n{combined}"
            )

    # ---- --json ---------------------------------------------------------

    @pytest.mark.integration
    def test_json_output_is_parseable_with_per_step_receipts(
        self,
        patched_psql: str,
        patched_valkey_cli: str,
        patched_env_file: Path,
        patched_hba_dir: Path,
        patched_backup_dirs: dict[str, Path],
        bound_bus: Any,
        marker_at_cwd: tuple[Path, str],
        unique_app_name: str,
        capsys: pytest.CaptureFixture[str],
    ):
        """``--json`` prints a single JSON object covering every step."""
        pg_container = patched_psql
        valkey_container = patched_valkey_cli
        _, env_name = marker_at_cwd

        try:
            code = _call_bootstrap(env=env_name, json_output=True)
            assert code == EXIT_SUCCESS
            captured = capsys.readouterr()
            out = captured.out.strip()
            assert out, "expected JSON payload on stdout"

            data = json.loads(out)
            assert isinstance(data, dict), f"expected object at top level; got {type(data)}"

            # Impl shape (from ``_print_results_json``): keys include
            # ``env``, ``rotate``, ``dry_run``, ``steps``, ``success``.
            assert data.get("env") == env_name
            assert data.get("dry_run") is False
            assert data.get("success") is True
            steps = data.get("steps")
            assert isinstance(steps, list) and steps, (
                f"expected non-empty steps list; got {steps!r}"
            )
            step_names = [s.get("name") for s in steps]
            assert "postgres.bootstrap_app" in step_names
            assert "valkey.create_acl_user" in step_names
            assert "postgres.add_hba" in step_names
            # Each step carries changed / success.
            for step in steps:
                assert "changed" in step
                assert "success" in step
        finally:
            _drop_database_best_effort(pg_container, unique_app_name)
            _drop_role_best_effort(pg_container, unique_app_name)
            _acl_deluser_best_effort(valkey_container, unique_app_name)

    # ---- --rotate -------------------------------------------------------

    @pytest.mark.integration
    def test_rotate_delivers_new_pg_password(
        self,
        patched_psql: str,
        patched_valkey_cli: str,
        patched_env_file: Path,
        patched_hba_dir: Path,
        patched_backup_dirs: dict[str, Path],
        bound_bus: Any,
        marker_at_cwd: tuple[Path, str],
        unique_app_name: str,
    ):
        """After a base bootstrap, ``--rotate`` mints a new PG_PASSWORD.

        Per impl: ``rotate`` swaps ``postgres.bootstrap_app`` for
        ``postgres.rotate_password``. ``rotate_password`` requires the
        role to already exist (ensured by the prior base run).
        """
        pg_container = patched_psql
        valkey_container = patched_valkey_cli
        _, env_name = marker_at_cwd

        def _read_pg_password(env_file: Path) -> str | None:
            for line in env_file.read_text().splitlines():
                if line.startswith("PG_PASSWORD="):
                    return line.split("=", 1)[1].strip().strip('"')
            return None

        try:
            assert _call_bootstrap(env=env_name) == EXIT_SUCCESS
            first_pw = _read_pg_password(patched_env_file)
            assert first_pw, "base run must land a PG_PASSWORD"

            assert _call_bootstrap(env=env_name, rotate=True) == EXIT_SUCCESS
            second_pw = _read_pg_password(patched_env_file)
            assert second_pw, "rotate run must land a PG_PASSWORD"
            assert second_pw != first_pw, "rotate must produce a different PG_PASSWORD"
        finally:
            _drop_database_best_effort(pg_container, unique_app_name)
            _drop_role_best_effort(pg_container, unique_app_name)
            _acl_deluser_best_effort(valkey_container, unique_app_name)

    # ---- failure short-circuit ------------------------------------------

    @pytest.mark.integration
    def test_postgres_delivery_failure_short_circuits_before_valkey(
        self,
        patched_psql: str,
        patched_valkey_cli: str,
        patched_env_file: Path,
        patched_hba_dir: Path,
        patched_backup_dirs: dict[str, Path],
        in_process_bus: Any,
        monkeypatch: pytest.MonkeyPatch,
        marker_at_cwd: tuple[Path, str],
        unique_app_name: str,
    ):
        """A failed postgres step stops the orchestrator before valkey runs.

        The web-host peer is overridden to refuse ``secrets.deliver``. The
        postgres handler's rollback drops the freshly-created role; the
        operator command sees ``success=False`` from the first step and
        exits ``EXIT_FAILURE`` before publishing the valkey step.
        """
        pg_container = patched_psql
        valkey_container = patched_valkey_cli
        _, env_name = marker_at_cwd

        # Only web-host peer fails on secrets.deliver. db-host continues
        # through the real local dispatcher so the operator command's
        # db-host publishes reach real handlers; the handler-internal hop
        # to web-host is what fails.
        in_process_bus.register(
            "web-host",
            lambda cmd, p: CommandResult.fail("delivery failed: broker down"),
        )
        monkeypatch.setattr(bootstrap_mod, "_get_rpc_client", lambda: in_process_bus)
        monkeypatch.setattr(pg_mod, "_get_rpc_client", lambda: in_process_bus)
        monkeypatch.setattr(valkey_mod, "_get_rpc_client", lambda: in_process_bus)

        try:
            code = _call_bootstrap(env=env_name)
            assert code == EXIT_FAILURE, f"bootstrap must fail with EXIT_FAILURE; got {code}"

            # Postgres rollback: role absent.
            assert not _role_exists_in_container(pg_container, unique_app_name)

            # Valkey step did NOT fire — no ACL user created on the real
            # valkey container. This is the test of orchestrator
            # short-circuit semantics.
            assert not _valkey_user_exists(valkey_container, unique_app_name)
        finally:
            _drop_database_best_effort(pg_container, unique_app_name)
            _drop_role_best_effort(pg_container, unique_app_name)
            _acl_deluser_best_effort(valkey_container, unique_app_name)

    @pytest.mark.integration
    def test_timeout_on_deliver_surfaces_failing_step_name(
        self,
        patched_psql: str,
        patched_valkey_cli: str,
        patched_env_file: Path,
        patched_hba_dir: Path,
        patched_backup_dirs: dict[str, Path],
        in_process_bus: Any,
        monkeypatch: pytest.MonkeyPatch,
        marker_at_cwd: tuple[Path, str],
        unique_app_name: str,
        capsys: pytest.CaptureFixture[str],
    ):
        """Delivery-side ``TimeoutError`` yields EXIT_FAILURE; step name visible."""
        pg_container = patched_psql
        valkey_container = patched_valkey_cli
        _, env_name = marker_at_cwd

        def timeout_on_deliver(cmd: str, params: dict[str, Any]) -> CommandResult:
            if cmd == "secrets.deliver":
                raise TimeoutError("simulated broker timeout")
            from rots.sidecar.commands import dispatch

            return dispatch(cmd, params)

        in_process_bus.register("web-host", timeout_on_deliver)
        monkeypatch.setattr(bootstrap_mod, "_get_rpc_client", lambda: in_process_bus)
        monkeypatch.setattr(pg_mod, "_get_rpc_client", lambda: in_process_bus)
        monkeypatch.setattr(valkey_mod, "_get_rpc_client", lambda: in_process_bus)

        try:
            code = _call_bootstrap(env=env_name)
            assert code == EXIT_FAILURE
            captured = capsys.readouterr()
            combined = (captured.out + captured.err).lower()
            # Step name (``postgres.bootstrap_app``) should be in the
            # receipt output.
            assert "postgres.bootstrap_app" in combined or "postgres" in combined, (
                f"Expected step name in error output; got:\n{combined}"
            )
        finally:
            _drop_database_best_effort(pg_container, unique_app_name)
            _drop_role_best_effort(pg_container, unique_app_name)
            _acl_deluser_best_effort(valkey_container, unique_app_name)

    # ---- invalid invocation --------------------------------------------

    @pytest.mark.quick
    def test_missing_env_kwarg_raises_type_error(self):
        """Calling the function without ``env`` raises TypeError.

        Cyclopts owns the CLI-layer missing-arg error (exit 2 conventional)
        but direct-function call produces a plain ``TypeError`` because
        ``env`` is a required keyword parameter on the impl.
        """
        with pytest.raises(TypeError):
            bootstrap_mod.bootstrap()

    @pytest.mark.quick
    def test_nonexistent_env_name_exits_precond(
        self,
        marker_at_cwd: tuple[Path, str],
        caplog: pytest.LogCaptureFixture,
        capsys: pytest.CaptureFixture[str],
    ):
        """Unknown env name exits ``EXIT_PRECOND`` with available-envs hint.

        The impl emits the precond error via ``logger.error`` (text mode)
        or via a JSON payload on stdout (json mode). In text mode the
        operator sees the message on stderr once the logging handler is
        attached; ``caplog`` captures the record directly.
        """
        import logging

        caplog.set_level(logging.ERROR, logger="rots.commands.env.bootstrap")
        code = _call_bootstrap(env="not-a-real-env")
        assert code == EXIT_PRECOND, (
            f"expected EXIT_PRECOND ({EXIT_PRECOND}) for unknown env; got {code}"
        )

        captured = capsys.readouterr()
        combined = ((captured.out + captured.err) + caplog.text).lower()
        assert "not-a-real-env" in combined or "eu-demo" in combined, (
            f"Expected attempted-env or available-envs in error; got:\n{combined}"
        )

    @pytest.mark.quick
    def test_missing_marker_file_exits_precond(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
        capsys: pytest.CaptureFixture[str],
    ):
        """No ``.otsinfra.yaml`` anywhere up the tree -> EXIT_PRECOND."""
        import logging

        nested = tmp_path / "no-marker"
        nested.mkdir()
        (nested / ".git").mkdir()
        monkeypatch.chdir(nested)

        caplog.set_level(logging.ERROR, logger="rots.commands.env.bootstrap")
        code = _call_bootstrap(env="eu-demo")
        assert code == EXIT_PRECOND

        captured = capsys.readouterr()
        combined = ((captured.out + captured.err) + caplog.text).lower()
        assert ".otsinfra.yaml" in combined or "marker" in combined or "not found" in combined
