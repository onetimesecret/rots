# src/rots/commands/sidecar/handlers/postgres.py

"""Postgres provisioning handlers (``postgres.*``).

These handlers run on the **db sidecar** (``roles={"db"}``). They own the
second phase of provisioning: generating application passwords, creating
roles / databases, managing ``pg_hba.d/`` drop-ins, and rotating
credentials. Cloud-init (``lots``) no longer touches any of this.

Auth model
----------
The sidecar runs as root on the db host. It invokes ``sudo -u postgres
psql`` over the local UNIX socket, relying on postgres peer authentication.
Peer auth means presence-on-the-host is the credential — there is no
secret to read at sidecar startup. Implementations MUST use this channel
(do not open a TCP connection, do not store a ``.pgpass``).

Cross-host delivery
-------------------
Generated passwords travel to the web peer via ``secrets.deliver`` on the
``peer_id`` sidecar. Handlers obtain an :class:`RpcClient` from module-level
factory (``_get_rpc_client()``) so tests can swap in
:class:`InProcessRpcClient`. Implementations MUST deliver the password
**before** returning success — a dropped delivery is a provisioning
failure, not a warning.

Status
------
This module is a scaffold. All handlers raise :class:`NotImplementedError`.
Impl and test agents write against the spec below.
"""

from __future__ import annotations

import logging
import os
import re
import secrets as _stdlib_secrets
import stat
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from rots.sidecar.commands import Command, CommandResult, register_handler

from ._transport import RpcClient
from ._types import (
    PostgresAddHbaData,
    PostgresBootstrapAppData,
    PostgresRotatePasswordData,
)

__all__ = [
    "PG_HBA_D",
    "PSQL_PEER_CMD",
    "PostgresAddHbaData",
    "PostgresBootstrapAppData",
    "PostgresRotatePasswordData",
    "RpcClient",
    "handle_add_hba",
    "handle_bootstrap_app",
    "handle_ping",
    "handle_rotate_password",
]

logger = logging.getLogger(__name__)

# The directory under which pg_hba.d drop-ins live on Debian/Ubuntu.
# Implementations MUST NOT write outside this directory. The ``name`` parameter
# of :func:`handle_add_hba` is joined into this path after basename validation
# (no slashes, no ``..``, ``.conf`` suffix enforced).
PG_HBA_D: str = "/etc/postgresql/pg_hba.d"

# Wrapper command to invoke psql as the postgres OS user over the local socket.
# Implementations MUST use this form — do not pass `-h hostname` (that forces
# TCP and breaks peer auth).
PSQL_PEER_CMD: tuple[str, ...] = ("sudo", "-u", "postgres", "psql", "-v", "ON_ERROR_STOP=1")

# Identifier regex used for role / database / app names. Anchored, lowercase,
# starts with a letter, up to 63 characters total (postgres NAMEDATALEN cap).
# Chosen narrow enough that a validated identifier is safe to interpolate into
# a double-quoted SQL identifier without further escaping.
_IDENT_RE = re.compile(r"^[a-z][a-z0-9_]{0,62}$")

# Valid first-token keywords for a pg_hba.d/ drop-in line.
_HBA_PREFIXES: frozenset[str] = frozenset(
    {"host", "hostssl", "hostnossl", "local", "hostgssenc", "hostnogssenc"}
)

# File mode for a pg_hba.d drop-in — readable by owner and by postgres group.
_HBA_FILE_MODE = 0o640

# Maximum length for an hba drop-in name *before* the .conf suffix is appended.
_HBA_NAME_MAX = 64


def _get_rpc_client() -> RpcClient:
    """Return an :class:`RpcClient` bound to the active broker.

    Implementations (and tests) MUST route cross-host calls through this
    seam so the in-process test client can be swapped in.

    Default implementation returns a :class:`RabbitMqRpcClient` using
    :func:`RabbitMQConfig.from_environment`. Tests override via::

        import rots.commands.sidecar.handlers.postgres as pg
        monkeypatch.setattr(pg, "_get_rpc_client", lambda: test_bus)
    """
    from ._transport import RabbitMqRpcClient

    return RabbitMqRpcClient()


# --- helpers -------------------------------------------------------------


def _valid_ident(name: str) -> bool:
    """Return ``True`` when ``name`` matches the safe-identifier pattern."""
    return bool(_IDENT_RE.match(name))


def _psql(
    args: tuple[str, ...], *, input_sql: str | None = None
) -> subprocess.CompletedProcess[str]:
    """Run a psql command with peer auth and ``ON_ERROR_STOP=1``.

    Args:
        args: Extra CLI args appended to :data:`PSQL_PEER_CMD`.
        input_sql: Optional SQL to feed via stdin. Passing SQL via stdin (instead
            of ``-c``) keeps secrets out of ``/proc/<pid>/cmdline`` and the
            process table. Used for ``CREATE ROLE ... PASSWORD ...`` etc.

    Returns:
        The ``CompletedProcess`` from :func:`subprocess.run` on success.

    Raises:
        subprocess.CalledProcessError: psql returned non-zero.
    """
    cmd = PSQL_PEER_CMD + args
    return subprocess.run(
        cmd,
        input=input_sql,
        check=True,
        capture_output=True,
        text=True,
    )


def _role_exists(role: str) -> bool:
    """Return ``True`` if the role exists. Caller validates ``role``."""
    # -tA => tuples-only, unaligned; empty stdout means no row, "1" means present.
    proc = _psql(
        ("-tA", "-c", f"SELECT 1 FROM pg_roles WHERE rolname = '{role}'"),
    )
    return proc.stdout.strip() == "1"


def _database_exists(database: str) -> bool:
    """Return ``True`` if the database exists. Caller validates ``database``."""
    proc = _psql(
        ("-tA", "-c", f"SELECT 1 FROM pg_database WHERE datname = '{database}'"),
    )
    return proc.stdout.strip() == "1"


def _create_role_with_password(role: str, password: str) -> None:
    """Create a LOGIN role with the given password via stdin-piped SQL.

    ``role`` MUST already be identifier-validated. ``password`` is passed
    through stdin so it never appears in the process argv.
    """
    # token_urlsafe() produces only [A-Za-z0-9_-]; no need to escape for SQL
    # string literal, but we still double single-quotes defensively.
    safe_pw = password.replace("'", "''")
    sql = f"CREATE ROLE \"{role}\" LOGIN PASSWORD '{safe_pw}';\n"
    _psql((), input_sql=sql)


def _drop_role(role: str) -> None:
    """Best-effort ``DROP ROLE`` used to roll back a failed delivery."""
    _psql(("-c", f'DROP ROLE IF EXISTS "{role}"'))


def _alter_role_password(role: str, password: str) -> None:
    """Change the password on an existing role via stdin-piped SQL."""
    safe_pw = password.replace("'", "''")
    sql = f"ALTER ROLE \"{role}\" WITH PASSWORD '{safe_pw}';\n"
    _psql((), input_sql=sql)


def _revoke_role_password(role: str) -> None:
    """Null-out the password on a role. Used to strand an undelivered rotation."""
    _psql(("-c", f'ALTER ROLE "{role}" WITH PASSWORD NULL'))


def _create_database(database: str, owner: str) -> None:
    """Create a database owned by ``owner``. Both names identifier-validated."""
    _psql(("-c", f'CREATE DATABASE "{database}" OWNER "{owner}"'))


def _reload_conf() -> bool:
    """Call ``pg_reload_conf()``. Return ``True`` when postgres returned ``t``."""
    proc = _psql(("-tA", "-c", "SELECT pg_reload_conf();"))
    return proc.stdout.strip() == "t"


def _chown_postgres(path: str) -> None:
    """Best-effort ``chown postgres:postgres``. Silent on failure."""
    try:
        import pwd

        entry = pwd.getpwnam("postgres")
        os.chown(path, entry.pw_uid, entry.pw_gid)
    except (KeyError, OSError, PermissionError) as exc:
        logger.debug("chown postgres:postgres on %s skipped: %s", path, exc)


def _deliver_password(
    client: RpcClient,
    peer_id: str,
    value: str,
    *,
    name: str = "PG_PASSWORD",
) -> CommandResult:
    """Publish ``secrets.deliver`` at ``peer_id`` with the given value.

    Intentionally omits ``env_file`` — the secrets.deliver handler defaults
    to :data:`rots.commands.sidecar.handlers.secrets.ALLOWED_ENV_FILE`, and
    passing a path at call time would bypass the allowlist invariant (and
    break tests that redirect the allowlist at a tmp_path).
    """
    return client.publish(
        peer_id,
        "secrets.deliver",
        {
            "name": name,
            "value": value,
        },
        timeout=10.0,
    )


# --- handlers ------------------------------------------------------------


@register_handler(Command.POSTGRES_BOOTSTRAP_APP, roles={"db"})
def handle_bootstrap_app(params: dict[str, Any]) -> CommandResult:
    """Create an application role + database, generate a password, deliver it.

    See module docstring and issue #55 for the contract. Implementation notes:

    * When the owner role already exists, this is a full short-circuit:
      step 4 (``secrets.deliver``) and step 5 (database existence check +
      CREATE DATABASE) are both skipped. Returns ``changed=False`` with
      ``password_delivered_to=peer_id`` as an informational echo. A re-run
      is thus a no-op end-to-end, matching operator intuition — if the
      role is here, the full bootstrap happened on a prior call and we do
      not know the password we would need to re-deliver.
    * ``changed`` is ``True`` only on the first run where the role was
      created. If the role was absent but the database already existed
      (an unusual state, e.g. after a manual restore), ``changed`` still
      reports the role creation + password delivery truthfully.
    """
    app = params.get("app")
    owner_role = params.get("owner_role")
    peer_ip = params.get("peer_ip")
    peer_id = params.get("peer_id")

    for key, val in (
        ("app", app),
        ("owner_role", owner_role),
        ("peer_ip", peer_ip),
        ("peer_id", peer_id),
    ):
        if not isinstance(val, str) or not val:
            return CommandResult.fail(f"Missing or empty '{key}' parameter")

    # Narrow types for pyright (the guard above proves these are non-empty strs).
    assert isinstance(app, str)
    assert isinstance(owner_role, str)
    assert isinstance(peer_ip, str)
    assert isinstance(peer_id, str)

    if not _valid_ident(app):
        return CommandResult.fail(f"Invalid app name: {app!r}")
    if not _valid_ident(owner_role):
        return CommandResult.fail(f"Invalid owner_role name: {owner_role!r}")

    # peer_ip is not interpolated into SQL in this handler; its presence is
    # required but full validation happens in add_hba. Still, reject obvious
    # junk so we don't surface a confused success receipt.
    if not peer_ip.strip():
        return CommandResult.fail("Invalid peer_ip: empty string")

    role_changed = False
    db_changed = False

    # --- role branch -----------------------------------------------------
    try:
        role_is_present = _role_exists(owner_role)
    except subprocess.CalledProcessError as exc:
        return CommandResult.fail(f"psql failed: {exc.stderr.strip() or exc}")

    if role_is_present:
        # Full short-circuit. We cannot re-deliver a password we do not
        # know, so echo the receipt and return changed=False.
        logger.info(
            "postgres.bootstrap_app: role %s already exists; no-op (changed=False)",
            owner_role,
        )
        existing_data: PostgresBootstrapAppData = {
            "role": owner_role,
            "database": app,
            "password_delivered_to": peer_id,
            "changed": False,
        }
        return CommandResult.ok(existing_data)

    # --- role does not exist: create + deliver -------------------------
    password = _stdlib_secrets.token_urlsafe(32)
    try:
        _create_role_with_password(owner_role, password)
    except subprocess.CalledProcessError as exc:
        return CommandResult.fail(f"psql failed: {exc.stderr.strip() or exc}")
    role_changed = True
    logger.info("postgres.bootstrap_app created role %s", owner_role)

    # Deliver before returning success. On any delivery failure, roll back.
    client = _get_rpc_client()
    try:
        delivery = _deliver_password(client, peer_id, password)
    except TimeoutError:
        try:
            _drop_role(owner_role)
        except subprocess.CalledProcessError as drop_exc:
            logger.warning(
                "postgres.bootstrap_app rollback DROP ROLE %s failed after delivery timeout: %s",
                owner_role,
                drop_exc.stderr.strip() or drop_exc,
            )
        return CommandResult.fail("secrets.deliver timed out")
    except Exception as exc:  # noqa: BLE001 — transport-level surface
        try:
            _drop_role(owner_role)
        except subprocess.CalledProcessError as drop_exc:
            logger.warning(
                "postgres.bootstrap_app rollback DROP ROLE %s failed: %s",
                owner_role,
                drop_exc.stderr.strip() or drop_exc,
            )
        return CommandResult.fail(f"secrets.deliver transport error: {exc}")

    if not delivery.success:
        try:
            _drop_role(owner_role)
        except subprocess.CalledProcessError as drop_exc:
            logger.warning(
                "postgres.bootstrap_app rollback DROP ROLE %s failed after delivery error: %s",
                owner_role,
                drop_exc.stderr.strip() or drop_exc,
            )
        return CommandResult.fail(f"secrets.deliver failed: {delivery.error or 'unknown error'}")

    # --- database branch -------------------------------------------------
    try:
        database_is_present = _database_exists(app)
    except subprocess.CalledProcessError as exc:
        return CommandResult.fail(f"psql failed: {exc.stderr.strip() or exc}")

    if not database_is_present:
        try:
            _create_database(app, owner_role)
        except subprocess.CalledProcessError as exc:
            return CommandResult.fail(f"psql failed: {exc.stderr.strip() or exc}")
        db_changed = True
        logger.info("postgres.bootstrap_app created database %s owner=%s", app, owner_role)

    data: PostgresBootstrapAppData = {
        "role": owner_role,
        "database": app,
        "password_delivered_to": peer_id,
        "changed": role_changed or db_changed,
    }
    return CommandResult.ok(data)


@register_handler(Command.POSTGRES_ADD_HBA, roles={"db"})
def handle_add_hba(params: dict[str, Any]) -> CommandResult:
    """Install (or update) a ``pg_hba.d/`` drop-in and reload postgres.

    See module docstring for the contract.
    """
    name = params.get("name")
    content = params.get("content")

    if not isinstance(name, str) or not name:
        return CommandResult.fail("Missing or empty 'name' parameter")
    if not isinstance(content, str) or content == "":
        return CommandResult.fail("Missing or empty 'content' parameter")

    # --- name validation -------------------------------------------------
    # Enforce bare basename: no "/", no "..", no leading ".".
    # Length cap applies to the pre-suffix form.
    bare_name = name[:-5] if name.endswith(".conf") else name
    if (
        "/" in bare_name
        or bare_name.startswith(".")
        or ".." in bare_name
        or not bare_name
        or len(bare_name) > _HBA_NAME_MAX
    ):
        return CommandResult.fail(f"Invalid hba name: {name!r}")

    # --- content validation ---------------------------------------------
    if not content.endswith("\n"):
        content = content + "\n"

    for idx, line in enumerate(content.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = stripped.split()
        if not parts or parts[0] not in _HBA_PREFIXES:
            return CommandResult.fail(
                f"Invalid hba line {idx}: expected one of {sorted(_HBA_PREFIXES)}, got {parts[0]!r}"
                if parts
                else f"Invalid hba line {idx}: empty"
            )

    # --- path + idempotency ---------------------------------------------
    filename = f"{bare_name}.conf"
    target = Path(PG_HBA_D) / filename

    # Byte-for-byte existence check. Refuse to follow a symlink target.
    try:
        lst = os.lstat(target)
    except FileNotFoundError:
        lst = None
    except OSError as exc:
        return CommandResult.fail(f"Failed to stat {target}: {exc}")

    if lst is not None and stat.S_ISLNK(lst.st_mode):
        return CommandResult.fail(f"Refusing to write: {target} is a symbolic link")

    if lst is not None:
        try:
            fd = os.open(target, os.O_RDONLY | os.O_NOFOLLOW)
        except OSError as exc:
            return CommandResult.fail(f"Failed to open {target}: {exc}")
        try:
            with os.fdopen(fd, "r", encoding="utf-8") as f:
                existing = f.read()
        except OSError as exc:
            return CommandResult.fail(f"Failed to read {target}: {exc}")
        if existing == content:
            logger.info("postgres.add_hba no-op: %s already current", target)
            no_op: PostgresAddHbaData = {"reloaded": False, "changed": False}
            return CommandResult.ok(no_op)

    # --- atomic write ----------------------------------------------------
    parent = target.parent
    try:
        parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return CommandResult.fail(f"Failed to ensure parent dir {parent}: {exc}")

    tmp_fd: int | None = None
    tmp_name: str | None = None
    try:
        tmp_fd, tmp_name = tempfile.mkstemp(
            prefix=f".{target.name}.",
            suffix=".tmp",
            dir=str(parent),
        )
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            tmp_fd = None
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp_name, _HBA_FILE_MODE)
        _chown_postgres(tmp_name)
        os.rename(tmp_name, target)
        tmp_name = None
    except OSError as exc:
        return CommandResult.fail(f"Failed to write {target}: {exc}")
    finally:
        if tmp_fd is not None:
            try:
                os.close(tmp_fd)
            except OSError:
                pass
        if tmp_name is not None:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass

    logger.info("postgres.add_hba wrote %s (mode=0%o)", target, _HBA_FILE_MODE)

    # --- reload ----------------------------------------------------------
    warnings: list[str] = []
    reloaded = False
    try:
        reloaded = _reload_conf()
    except subprocess.CalledProcessError as exc:
        return CommandResult(
            success=False,
            error=(
                f"pg_reload_conf() failed: {exc.stderr.strip() or exc}. "
                f"File {target} was written; run 'systemctl reload postgresql' manually."
            ),
        )

    if not reloaded:
        warnings.append(
            f"pg_reload_conf() returned false (postgres may not be running); "
            f"{target} written but reload did not take effect"
        )
        logger.warning("postgres.add_hba: pg_reload_conf() returned false for %s", target)

    data: PostgresAddHbaData = {"reloaded": reloaded, "changed": True}
    return CommandResult(success=True, data=data, warnings=warnings)


@register_handler(Command.POSTGRES_ROTATE_PASSWORD, roles={"db"})
def handle_rotate_password(params: dict[str, Any]) -> CommandResult:
    """Generate a new password for a role and deliver it to ``peer_id``.

    See module docstring for the contract.
    """
    role = params.get("role")
    peer_id = params.get("peer_id")

    for key, val in (("role", role), ("peer_id", peer_id)):
        if not isinstance(val, str) or not val:
            return CommandResult.fail(f"Missing or empty '{key}' parameter")

    assert isinstance(role, str)
    assert isinstance(peer_id, str)

    if not _valid_ident(role):
        return CommandResult.fail(f"Invalid role name: {role!r}")

    try:
        if not _role_exists(role):
            return CommandResult.fail(f"Role does not exist: {role!r}")
    except subprocess.CalledProcessError as exc:
        return CommandResult.fail(f"psql failed: {exc.stderr.strip() or exc}")

    new_password = _stdlib_secrets.token_urlsafe(32)
    try:
        _alter_role_password(role, new_password)
    except subprocess.CalledProcessError as exc:
        return CommandResult.fail(f"psql failed: {exc.stderr.strip() or exc}")
    logger.info("postgres.rotate_password altered role %s", role)

    # Delivery — on any failure, run a best-effort password revoke.
    client = _get_rpc_client()
    warnings: list[str] = []
    try:
        delivery = _deliver_password(client, peer_id, new_password)
    except TimeoutError:
        _best_effort_revoke(role, warnings)
        return CommandResult(
            success=False,
            error="secrets.deliver timed out",
            warnings=warnings,
        )
    except Exception as exc:  # noqa: BLE001 — transport-level surface
        _best_effort_revoke(role, warnings)
        return CommandResult(
            success=False,
            error=f"secrets.deliver transport error: {exc}",
            warnings=warnings,
        )

    if not delivery.success:
        _best_effort_revoke(role, warnings)
        return CommandResult(
            success=False,
            error=f"secrets.deliver failed: {delivery.error or 'unknown error'}",
            warnings=warnings,
        )

    data: PostgresRotatePasswordData = {"delivered_to": peer_id, "changed": True}
    return CommandResult.ok(data)


@register_handler(Command.POSTGRES_PING, roles={"db"})
def handle_ping(params: dict[str, Any]) -> CommandResult:
    """Verify postgres connectivity via a trivial ``SELECT 1`` over peer auth."""
    del params  # unused; dispatcher always passes a dict
    try:
        _psql(("-tAc", "SELECT 1"))
    except subprocess.CalledProcessError as exc:
        return CommandResult.fail(f"postgres.ping failed: {exc.stderr.strip() or exc}")
    return CommandResult.ok({"ok": True, "changed": False})


def _best_effort_revoke(role: str, warnings: list[str]) -> None:
    """Null the password on ``role`` and append any failure to ``warnings``."""
    try:
        _revoke_role_password(role)
    except subprocess.CalledProcessError as exc:
        msg = (
            f"Best-effort password revoke for {role!r} failed after delivery error: "
            f"{exc.stderr.strip() or exc}. Operator must re-run rotate_password."
        )
        warnings.append(msg)
        logger.warning("postgres.rotate_password revoke failed: %s", msg)
    except OSError as exc:
        msg = f"Best-effort password revoke for {role!r} failed: {exc}"
        warnings.append(msg)
        logger.warning("postgres.rotate_password revoke failed: %s", msg)
