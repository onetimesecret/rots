# src/rots/db.py
"""SQLite database for deployment timeline and image alias tracking.

The deployment timeline is an append-only audit trail that records all
deployment actions. It does NOT rely on environment variables for determining
previous tags - instead it queries the timeline history.

This ensures:
- Consecutive rollbacks work correctly (history moves forward, not toggling)
- Full audit trail of all deployments
- CURRENT and ROLLBACK aliases are tracked in the database

Remote execution: When an executor (SSHExecutor) is provided, database
operations are dispatched via the ``sqlite3`` CLI on the remote host instead
of using the Python sqlite3 module.  The remote ``sqlite3`` must be >= 3.33
(``-json`` output flag).  Debian 12 ships 3.40, Ubuntu 22.04 ships 3.37.
"""

from __future__ import annotations

import json as _json
import logging
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from ots_shared.ssh import is_remote as _is_remote

if TYPE_CHECKING:
    from ots_shared.ssh.executor import Executor

logger = logging.getLogger(__name__)


@dataclass
class Deployment:
    """A deployment record from the timeline."""

    id: int
    timestamp: str
    port: int | None
    image: str
    tag: str
    action: str  # deploy, redeploy, undeploy, rollback, set-current
    success: bool
    notes: str | None = None


@dataclass
class ImageAlias:
    """An image alias (CURRENT, ROLLBACK, etc.)."""

    alias: str
    image: str
    tag: str
    set_at: str


SCHEMA = """
CREATE TABLE IF NOT EXISTS deployments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL DEFAULT (datetime('now')),
    port INTEGER,
    image TEXT NOT NULL,
    tag TEXT NOT NULL,
    action TEXT NOT NULL,
    success INTEGER NOT NULL DEFAULT 1,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS image_aliases (
    alias TEXT PRIMARY KEY,
    image TEXT NOT NULL,
    tag TEXT NOT NULL,
    set_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_deployments_timestamp ON deployments(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_deployments_port ON deployments(port);
CREATE INDEX IF NOT EXISTS idx_deployments_tag ON deployments(tag);

-- Service instances (systemd template services like valkey-server@)
CREATE TABLE IF NOT EXISTS service_instances (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    package TEXT NOT NULL,          -- e.g., "valkey", "redis"
    instance TEXT NOT NULL,         -- e.g., "6379" (port-based)
    config_file TEXT NOT NULL,      -- /etc/valkey/instances/6379.conf
    data_dir TEXT NOT NULL,         -- /var/lib/valkey/6379
    port INTEGER,                   -- Actual port number
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    notes TEXT,
    UNIQUE(package, instance)
);

-- Service action audit trail
CREATE TABLE IF NOT EXISTS service_actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL DEFAULT (datetime('now')),
    package TEXT NOT NULL,
    instance TEXT NOT NULL,
    action TEXT NOT NULL,           -- init, enable, disable, start, stop, restart, secret-set, etc.
    success INTEGER NOT NULL DEFAULT 1,
    notes TEXT
);

CREATE INDEX IF NOT EXISTS idx_service_instances_package ON service_instances(package);
CREATE INDEX IF NOT EXISTS idx_service_actions_timestamp ON service_actions(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_service_actions_pkg_inst
    ON service_actions(package, instance);

-- DNS record audit trail
CREATE TABLE IF NOT EXISTS dns_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL DEFAULT (datetime('now')),
    hostname TEXT NOT NULL,
    record_type TEXT NOT NULL,
    value TEXT NOT NULL,
    ttl INTEGER,
    provider TEXT NOT NULL,
    action TEXT NOT NULL,
    success INTEGER NOT NULL DEFAULT 1,
    notes TEXT
);

-- DNS current state (last-known record per hostname)
CREATE TABLE IF NOT EXISTS dns_current (
    hostname TEXT PRIMARY KEY,
    record_type TEXT NOT NULL,
    value TEXT NOT NULL,
    ttl INTEGER,
    provider TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_dns_records_timestamp ON dns_records(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_dns_records_hostname ON dns_records(hostname);
CREATE INDEX IF NOT EXISTS idx_dns_current_provider ON dns_current(provider);
"""


def _escape_sql_string(value: str) -> str:
    """Escape a string for use in a sqlite3 CLI SQL literal.

    SQLite uses doubled single-quotes for escaping:  O'Brien -> 'O''Brien'
    """
    return value.replace("'", "''")


def _remote_query(
    db_path: Path,
    sql: str,
    params: tuple = (),
    *,
    executor: Executor,
) -> list[dict]:
    """Execute a SELECT query on a remote host via the sqlite3 CLI.

    Uses ``sqlite3 -json`` (requires sqlite3 >= 3.33) for structured output.
    Returns a list of dicts (one per row).
    """
    formatted_sql = _interpolate_params(sql, params)
    result = executor.run(
        ["sqlite3", "-json", str(db_path), formatted_sql],
        timeout=15,
    )
    if not result.ok:
        logger.warning("Remote sqlite3 query failed: %s", result.stderr.strip())
        return []
    stdout = result.stdout.strip()
    if not stdout:
        return []
    return _json.loads(stdout)


def _remote_execute(
    db_path: Path,
    sql: str,
    params: tuple = (),
    *,
    executor: Executor,
) -> None:
    """Execute a write (INSERT/UPDATE/DELETE) on a remote host via sqlite3 CLI."""
    formatted_sql = _interpolate_params(sql, params)
    result = executor.run(
        ["sqlite3", str(db_path), formatted_sql],
        timeout=15,
    )
    if not result.ok:
        logger.warning("Remote sqlite3 execute failed: %s", result.stderr.strip())


def _interpolate_params(sql: str, params: tuple) -> str:
    """Replace ``?`` placeholders with properly escaped literal values.

    Only supports int, str, and None — which covers all our query parameters.
    """
    parts: list[str] = []
    param_idx = 0
    for char in sql:
        if char == "?" and param_idx < len(params):
            val = params[param_idx]
            if val is None:
                parts.append("NULL")
            elif isinstance(val, int):
                parts.append(str(val))
            elif isinstance(val, str):
                parts.append(f"'{_escape_sql_string(val)}'")
            else:
                parts.append(f"'{_escape_sql_string(str(val))}'")
            param_idx += 1
        else:
            parts.append(char)
    return "".join(parts)


def _remote_init_db(db_path: Path, *, executor: Executor) -> None:
    """Ensure the remote database exists and has the schema applied."""
    # Use a single sqlite3 invocation with the full schema
    result = executor.run(
        ["sqlite3", str(db_path), SCHEMA],
        timeout=15,
    )
    if not result.ok:
        logger.warning("Remote init_db failed: %s", result.stderr.strip())


def init_db(db_path: Path, *, executor: Executor | None = None) -> None:
    """Initialize the database with schema. Idempotent."""
    if _is_remote(executor):
        _remote_init_db(db_path, executor=executor)  # type: ignore[arg-type]
        return
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.executescript(SCHEMA)


@contextmanager
def get_connection(
    db_path: Path,
    *,
    executor: Executor | None = None,
) -> Iterator[sqlite3.Connection]:
    """Get a local database connection, initializing if needed.

    For remote execution paths, callers should use ``_remote_query`` /
    ``_remote_execute`` directly instead of opening a connection.

    Raises:
        ValueError: If called with a remote executor (the Python sqlite3
            module cannot open a database on a remote host).
    """
    if _is_remote(executor):
        raise ValueError(
            "get_connection() cannot be used with a remote executor. "
            "Use _remote_query() / _remote_execute() directly."
        )
    if not db_path.exists():
        init_db(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def record_deployment(
    db_path: Path,
    image: str,
    tag: str,
    action: str,
    port: int | None = None,
    success: bool = True,
    notes: str | None = None,
    *,
    executor: Executor | None = None,
) -> int:
    """Record a deployment action to the timeline. Returns the deployment ID."""
    sql = """
        INSERT INTO deployments (port, image, tag, action, success, notes)
        VALUES (?, ?, ?, ?, ?, ?)
    """
    params = (port, image, tag, action, 1 if success else 0, notes)
    if _is_remote(executor):
        _remote_execute(db_path, sql, params, executor=executor)  # type: ignore[arg-type]
        return 0  # Remote doesn't return lastrowid
    with get_connection(db_path) as conn:
        cursor = conn.execute(sql, params)
        conn.commit()
        return cursor.lastrowid or 0


def get_deployments(
    db_path: Path,
    limit: int = 50,
    port: int | None = None,
    action_like: str | None = None,
    notes_like: str | None = None,
    *,
    executor: Executor | None = None,
) -> list[Deployment]:
    """Get deployment history, optionally filtered by port, action, or notes.

    Args:
        db_path: Path to the database file.
        limit: Maximum number of records to return.
        port: Filter by exact port number (for web instances).
        action_like: Filter by action pattern using SQL LIKE (e.g., "%-worker").
        notes_like: Filter by notes pattern using SQL LIKE (e.g., "%worker_id=1%").
        executor: Executor for command dispatch. None uses local sqlite3.
    """
    # Build query dynamically based on filters
    conditions: list[str] = []
    params: list[int | str] = []

    if port is not None:
        conditions.append("port = ?")
        params.append(port)
    if action_like is not None:
        conditions.append("action LIKE ?")
        params.append(action_like)
    if notes_like is not None:
        conditions.append("notes LIKE ?")
        params.append(notes_like)

    where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    query = f"""
        SELECT id, timestamp, port, image, tag, action, success, notes
        FROM deployments
        {where_clause}
        ORDER BY timestamp DESC
        LIMIT ?
    """
    params.append(limit)

    if _is_remote(executor):
        rows = _remote_query(db_path, query, tuple(params), executor=executor)  # type: ignore[arg-type]
        return [
            Deployment(
                id=row["id"],
                timestamp=row["timestamp"],
                port=row.get("port"),
                image=row["image"],
                tag=row["tag"],
                action=row["action"],
                success=bool(row["success"]),
                notes=row.get("notes"),
            )
            for row in rows
        ]

    with get_connection(db_path) as conn:
        rows = conn.execute(query, params).fetchall()
        return [
            Deployment(
                id=row["id"],
                timestamp=row["timestamp"],
                port=row["port"],
                image=row["image"],
                tag=row["tag"],
                action=row["action"],
                success=bool(row["success"]),
                notes=row["notes"],
            )
            for row in rows
        ]


def set_alias(
    db_path: Path,
    alias: str,
    image: str,
    tag: str,
    *,
    executor: Executor | None = None,
) -> None:
    """Set an image alias (e.g., CURRENT, ROLLBACK)."""
    sql = """
        INSERT INTO image_aliases (alias, image, tag, set_at)
        VALUES (?, ?, ?, datetime('now'))
        ON CONFLICT(alias) DO UPDATE SET
            image = excluded.image,
            tag = excluded.tag,
            set_at = datetime('now')
    """
    params = (alias.upper(), image, tag)
    if _is_remote(executor):
        _remote_execute(db_path, sql, params, executor=executor)  # type: ignore[arg-type]
        return
    with get_connection(db_path) as conn:
        conn.execute(sql, params)
        conn.commit()


def get_alias(
    db_path: Path,
    alias: str,
    *,
    executor: Executor | None = None,
) -> ImageAlias | None:
    """Get an image alias."""
    sql = "SELECT alias, image, tag, set_at FROM image_aliases WHERE alias = ?"
    params = (alias.upper(),)
    if _is_remote(executor):
        rows = _remote_query(db_path, sql, params, executor=executor)  # type: ignore[arg-type]
        if rows:
            row = rows[0]
            return ImageAlias(
                alias=row["alias"],
                image=row["image"],
                tag=row["tag"],
                set_at=row["set_at"],
            )
        return None
    with get_connection(db_path) as conn:
        row = conn.execute(sql, params).fetchone()
        if row:
            return ImageAlias(
                alias=row["alias"],
                image=row["image"],
                tag=row["tag"],
                set_at=row["set_at"],
            )
        return None


def get_all_aliases(
    db_path: Path,
    *,
    executor: Executor | None = None,
) -> list[ImageAlias]:
    """Get all image aliases."""
    sql = "SELECT alias, image, tag, set_at FROM image_aliases ORDER BY alias"
    if _is_remote(executor):
        rows = _remote_query(db_path, sql, executor=executor)  # type: ignore[arg-type]
        return [
            ImageAlias(
                alias=row["alias"],
                image=row["image"],
                tag=row["tag"],
                set_at=row["set_at"],
            )
            for row in rows
        ]
    with get_connection(db_path) as conn:
        rows = conn.execute(sql).fetchall()
        return [
            ImageAlias(
                alias=row["alias"],
                image=row["image"],
                tag=row["tag"],
                set_at=row["set_at"],
            )
            for row in rows
        ]


def get_current_image(
    db_path: Path,
    *,
    executor: Executor | None = None,
) -> tuple[str, str] | None:
    """Get the current image and tag. Returns (image, tag) or None."""
    alias = get_alias(db_path, "CURRENT", executor=executor)
    if alias:
        return (alias.image, alias.tag)
    return None


def get_rollback_image(
    db_path: Path,
    *,
    executor: Executor | None = None,
) -> tuple[str, str] | None:
    """Get the rollback image and tag. Returns (image, tag) or None."""
    alias = get_alias(db_path, "ROLLBACK", executor=executor)
    if alias:
        return (alias.image, alias.tag)
    return None


def set_current(
    db_path: Path,
    image: str,
    tag: str,
    *,
    executor: Executor | None = None,
) -> str | None:
    """Set CURRENT alias, moving previous CURRENT to ROLLBACK.

    Returns the previous CURRENT tag (now ROLLBACK), or None if no previous.
    """
    # Get current before updating
    previous = get_current_image(db_path, executor=executor)
    previous_tag = None

    if previous:
        prev_image, prev_tag = previous
        # Move current to rollback
        set_alias(db_path, "ROLLBACK", prev_image, prev_tag, executor=executor)
        previous_tag = prev_tag

    # Set new current
    set_alias(db_path, "CURRENT", image, tag, executor=executor)

    # Record the action
    record_deployment(
        db_path,
        image=image,
        tag=tag,
        action="set-current",
        notes=f"Previous: {previous_tag}" if previous_tag else "Initial current",
        executor=executor,
    )

    return previous_tag


def rollback(
    db_path: Path,
    *,
    executor: Executor | None = None,
) -> tuple[str, str] | None:
    """Promote ROLLBACK to CURRENT.

    This queries the deployment timeline to find the previous successful
    deployment, NOT the ROLLBACK alias. This ensures consecutive rollbacks
    work correctly by walking back through history.

    Returns (image, tag) of the new CURRENT, or None if no rollback available.
    """
    sql = """
        SELECT image, tag, MAX(id) as last_id
        FROM deployments
        WHERE success = 1
          AND action IN ('deploy', 'redeploy', 'set-current')
        GROUP BY image, tag
        ORDER BY last_id DESC
        LIMIT 2
    """

    if _is_remote(executor):
        rows = _remote_query(db_path, sql, executor=executor)  # type: ignore[arg-type]
    else:
        with get_connection(db_path) as conn:
            rows = [dict(r) for r in conn.execute(sql).fetchall()]

    if len(rows) < 2:
        return None

    # rows[0] is current (most recent), rows[1] is what we want to roll back to
    rollback_image = rows[1]["image"]
    rollback_tag = rows[1]["tag"]

    # Get what we're rolling back from
    current = get_current_image(db_path, executor=executor)
    current_tag = current[1] if current else "unknown"

    # Update aliases - CURRENT becomes ROLLBACK, then new tag becomes CURRENT
    if current:
        set_alias(db_path, "ROLLBACK", current[0], current[1], executor=executor)

    set_alias(db_path, "CURRENT", rollback_image, rollback_tag, executor=executor)

    # Record the rollback action
    record_deployment(
        db_path,
        image=rollback_image,
        tag=rollback_tag,
        action="rollback",
        notes=f"Rolled back from {current_tag}",
        executor=executor,
    )

    return (rollback_image, rollback_tag)


def get_previous_tags(
    db_path: Path,
    limit: int = 10,
    *,
    executor: Executor | None = None,
) -> list[tuple[str, str, str]]:
    """Get previous distinct (image, tag, timestamp) from deployment history.

    Used for displaying rollback options. Returns list of (image, tag, timestamp).
    """
    sql = """
        SELECT DISTINCT image, tag, MAX(timestamp) as last_used
        FROM deployments
        WHERE success = 1
          AND action IN ('deploy', 'redeploy', 'set-current')
        GROUP BY image, tag
        ORDER BY last_used DESC
        LIMIT ?
    """
    params = (limit,)

    if _is_remote(executor):
        rows = _remote_query(db_path, sql, params, executor=executor)  # type: ignore[arg-type]
        return [(row["image"], row["tag"], row["last_used"]) for row in rows]

    with get_connection(db_path) as conn:
        rows = conn.execute(sql, params).fetchall()

    return [(row["image"], row["tag"], row["last_used"]) for row in rows]


# =============================================================================
# Service Instance Management
# =============================================================================


@dataclass
class ServiceInstance:
    """A managed service instance record."""

    id: int
    package: str
    instance: str
    config_file: str
    data_dir: str
    port: int | None
    created_at: str
    updated_at: str
    notes: str | None = None


@dataclass
class ServiceAction:
    """A service action audit record."""

    id: int
    timestamp: str
    package: str
    instance: str
    action: str
    success: bool
    notes: str | None = None


def record_service_instance(
    db_path: Path,
    package: str,
    instance: str,
    config_file: str,
    data_dir: str,
    port: int | None = None,
    notes: str | None = None,
    *,
    executor: Executor | None = None,
) -> int:
    """Record a new service instance. Returns the instance ID."""
    sql = """
        INSERT INTO service_instances (package, instance, config_file, data_dir, port, notes)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(package, instance) DO UPDATE SET
            config_file = excluded.config_file,
            data_dir = excluded.data_dir,
            port = excluded.port,
            notes = excluded.notes,
            updated_at = datetime('now')
    """
    params = (package, instance, config_file, data_dir, port, notes)
    if _is_remote(executor):
        _remote_execute(db_path, sql, params, executor=executor)  # type: ignore[arg-type]
        return 0  # Remote doesn't return lastrowid
    with get_connection(db_path) as conn:
        cursor = conn.execute(sql, params)
        conn.commit()
        return cursor.lastrowid or 0


def get_service_instance(
    db_path: Path,
    package: str,
    instance: str,
    *,
    executor: Executor | None = None,
) -> ServiceInstance | None:
    """Get a service instance by package and instance name."""
    sql = """
        SELECT id, package, instance, config_file, data_dir, port, created_at, updated_at, notes
        FROM service_instances
        WHERE package = ? AND instance = ?
    """
    params = (package, instance)
    if _is_remote(executor):
        rows = _remote_query(db_path, sql, params, executor=executor)  # type: ignore[arg-type]
        if rows:
            row = rows[0]
            return ServiceInstance(
                id=row["id"],
                package=row["package"],
                instance=row["instance"],
                config_file=row["config_file"],
                data_dir=row["data_dir"],
                port=row.get("port"),
                created_at=row["created_at"],
                updated_at=row["updated_at"],
                notes=row.get("notes"),
            )
        return None
    with get_connection(db_path) as conn:
        row = conn.execute(sql, params).fetchone()
        if row:
            return ServiceInstance(
                id=row["id"],
                package=row["package"],
                instance=row["instance"],
                config_file=row["config_file"],
                data_dir=row["data_dir"],
                port=row["port"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
                notes=row["notes"],
            )
        return None


def get_service_instances(
    db_path: Path,
    package: str | None = None,
    *,
    executor: Executor | None = None,
) -> list[ServiceInstance]:
    """Get all service instances, optionally filtered by package."""
    if package:
        sql = """
            SELECT id, package, instance, config_file, data_dir, port,
                   created_at, updated_at, notes
            FROM service_instances
            WHERE package = ?
            ORDER BY package, instance
        """
        params: tuple = (package,)
    else:
        sql = """
            SELECT id, package, instance, config_file, data_dir, port,
                   created_at, updated_at, notes
            FROM service_instances
            ORDER BY package, instance
        """
        params = ()

    if _is_remote(executor):
        rows = _remote_query(db_path, sql, params, executor=executor)  # type: ignore[arg-type]
        return [
            ServiceInstance(
                id=row["id"],
                package=row["package"],
                instance=row["instance"],
                config_file=row["config_file"],
                data_dir=row["data_dir"],
                port=row.get("port"),
                created_at=row["created_at"],
                updated_at=row["updated_at"],
                notes=row.get("notes"),
            )
            for row in rows
        ]

    with get_connection(db_path) as conn:
        rows = conn.execute(sql, params).fetchall()
        return [
            ServiceInstance(
                id=row["id"],
                package=row["package"],
                instance=row["instance"],
                config_file=row["config_file"],
                data_dir=row["data_dir"],
                port=row["port"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
                notes=row["notes"],
            )
            for row in rows
        ]


def delete_service_instance(
    db_path: Path,
    package: str,
    instance: str,
    *,
    executor: Executor | None = None,
) -> bool:
    """Delete a service instance record. Returns True if deleted."""
    sql = "DELETE FROM service_instances WHERE package = ? AND instance = ?"
    params = (package, instance)
    if _is_remote(executor):
        # Check existence first since remote execute doesn't return rowcount
        check_sql = (
            "SELECT COUNT(*) as cnt FROM service_instances WHERE package = ? AND instance = ?"
        )
        rows = _remote_query(db_path, check_sql, params, executor=executor)  # type: ignore[arg-type]
        if not rows or rows[0].get("cnt", 0) == 0:
            return False
        _remote_execute(db_path, sql, params, executor=executor)  # type: ignore[arg-type]
        return True
    with get_connection(db_path) as conn:
        cursor = conn.execute(sql, params)
        conn.commit()
        return cursor.rowcount > 0


def record_service_action(
    db_path: Path,
    package: str,
    instance: str,
    action: str,
    success: bool = True,
    notes: str | None = None,
    *,
    executor: Executor | None = None,
) -> int:
    """Record a service action to the audit trail. Returns the action ID."""
    sql = """
        INSERT INTO service_actions (package, instance, action, success, notes)
        VALUES (?, ?, ?, ?, ?)
    """
    params = (package, instance, action, 1 if success else 0, notes)
    if _is_remote(executor):
        _remote_execute(db_path, sql, params, executor=executor)  # type: ignore[arg-type]
        return 0  # Remote doesn't return lastrowid
    with get_connection(db_path) as conn:
        cursor = conn.execute(sql, params)
        conn.commit()
        return cursor.lastrowid or 0


def get_service_actions(
    db_path: Path,
    package: str | None = None,
    instance: str | None = None,
    limit: int = 50,
    *,
    executor: Executor | None = None,
) -> list[ServiceAction]:
    """Get service action history, optionally filtered."""
    conditions: list[str] = []
    params: list[int | str] = []

    if package:
        conditions.append("package = ?")
        params.append(package)
    if instance:
        conditions.append("instance = ?")
        params.append(instance)

    where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    sql = f"""
        SELECT id, timestamp, package, instance, action, success, notes
        FROM service_actions
        {where_clause}
        ORDER BY timestamp DESC
        LIMIT ?
    """
    params.append(limit)

    if _is_remote(executor):
        rows = _remote_query(db_path, sql, tuple(params), executor=executor)  # type: ignore[arg-type]
        return [
            ServiceAction(
                id=row["id"],
                timestamp=row["timestamp"],
                package=row["package"],
                instance=row["instance"],
                action=row["action"],
                success=bool(row["success"]),
                notes=row.get("notes"),
            )
            for row in rows
        ]

    with get_connection(db_path) as conn:
        rows = conn.execute(sql, params).fetchall()
        return [
            ServiceAction(
                id=row["id"],
                timestamp=row["timestamp"],
                package=row["package"],
                instance=row["instance"],
                action=row["action"],
                success=bool(row["success"]),
                notes=row["notes"],
            )
            for row in rows
        ]


# =============================================================================
# DNS Record Management
# =============================================================================


@dataclass
class DnsRecord:
    """A DNS action audit record."""

    id: int
    timestamp: str
    hostname: str
    record_type: str
    value: str
    ttl: int | None
    provider: str
    action: str
    success: bool
    notes: str | None = None


@dataclass
class DnsCurrent:
    """Current DNS state for a hostname."""

    hostname: str
    record_type: str
    value: str
    ttl: int | None
    provider: str
    updated_at: str


def record_dns_action(
    db_path: Path,
    hostname: str,
    record_type: str,
    value: str,
    ttl: int | None,
    provider: str,
    action: str,
    success: bool = True,
    notes: str | None = None,
    *,
    executor: Executor | None = None,
) -> int:
    """Record a DNS action to the audit trail. Returns the record ID."""
    sql = """
        INSERT INTO dns_records
            (hostname, record_type, value, ttl, provider, action, success, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """
    params = (hostname, record_type, value, ttl, provider, action, 1 if success else 0, notes)
    if _is_remote(executor):
        _remote_execute(db_path, sql, params, executor=executor)  # type: ignore[arg-type]
        return 0  # Remote doesn't return lastrowid
    with get_connection(db_path) as conn:
        cursor = conn.execute(sql, params)
        conn.commit()
        return cursor.lastrowid or 0


def upsert_dns_current(
    db_path: Path,
    hostname: str,
    record_type: str,
    value: str,
    ttl: int | None,
    provider: str,
    *,
    executor: Executor | None = None,
) -> None:
    """Update or insert the current DNS state for a hostname."""
    sql = """
        INSERT INTO dns_current (hostname, record_type, value, ttl, provider, updated_at)
        VALUES (?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(hostname) DO UPDATE SET
            record_type = excluded.record_type,
            value = excluded.value,
            ttl = excluded.ttl,
            provider = excluded.provider,
            updated_at = datetime('now')
    """
    params = (hostname, record_type, value, ttl, provider)
    if _is_remote(executor):
        _remote_execute(db_path, sql, params, executor=executor)  # type: ignore[arg-type]
        return
    with get_connection(db_path) as conn:
        conn.execute(sql, params)
        conn.commit()


def get_dns_current(
    db_path: Path,
    hostname: str,
    *,
    executor: Executor | None = None,
) -> DnsCurrent | None:
    """Get the current DNS record for a hostname."""
    sql = """
        SELECT hostname, record_type, value, ttl, provider, updated_at
        FROM dns_current
        WHERE hostname = ?
    """
    params = (hostname,)
    if _is_remote(executor):
        rows = _remote_query(db_path, sql, params, executor=executor)  # type: ignore[arg-type]
        if rows:
            row = rows[0]
            return DnsCurrent(
                hostname=row["hostname"],
                record_type=row["record_type"],
                value=row["value"],
                ttl=row.get("ttl"),
                provider=row["provider"],
                updated_at=row["updated_at"],
            )
        return None
    with get_connection(db_path) as conn:
        row = conn.execute(sql, params).fetchone()
        if row:
            return DnsCurrent(
                hostname=row["hostname"],
                record_type=row["record_type"],
                value=row["value"],
                ttl=row["ttl"],
                provider=row["provider"],
                updated_at=row["updated_at"],
            )
        return None


def get_all_dns_current(
    db_path: Path,
    *,
    executor: Executor | None = None,
) -> list[DnsCurrent]:
    """Get all current DNS records."""
    sql = """
        SELECT hostname, record_type, value, ttl, provider, updated_at
        FROM dns_current
        ORDER BY hostname
    """
    if _is_remote(executor):
        rows = _remote_query(db_path, sql, executor=executor)  # type: ignore[arg-type]
        return [
            DnsCurrent(
                hostname=row["hostname"],
                record_type=row["record_type"],
                value=row["value"],
                ttl=row.get("ttl"),
                provider=row["provider"],
                updated_at=row["updated_at"],
            )
            for row in rows
        ]
    with get_connection(db_path) as conn:
        rows = conn.execute(sql).fetchall()
        return [
            DnsCurrent(
                hostname=row["hostname"],
                record_type=row["record_type"],
                value=row["value"],
                ttl=row["ttl"],
                provider=row["provider"],
                updated_at=row["updated_at"],
            )
            for row in rows
        ]


def get_dns_history(
    db_path: Path,
    hostname: str,
    limit: int = 50,
    *,
    executor: Executor | None = None,
) -> list[DnsRecord]:
    """Get DNS action history for a hostname."""
    sql = """
        SELECT id, timestamp, hostname, record_type, value, ttl, provider, action, success, notes
        FROM dns_records
        WHERE hostname = ?
        ORDER BY timestamp DESC
        LIMIT ?
    """
    params = (hostname, limit)
    if _is_remote(executor):
        rows = _remote_query(db_path, sql, params, executor=executor)  # type: ignore[arg-type]
        return [
            DnsRecord(
                id=row["id"],
                timestamp=row["timestamp"],
                hostname=row["hostname"],
                record_type=row["record_type"],
                value=row["value"],
                ttl=row.get("ttl"),
                provider=row["provider"],
                action=row["action"],
                success=bool(row["success"]),
                notes=row.get("notes"),
            )
            for row in rows
        ]
    with get_connection(db_path) as conn:
        rows = conn.execute(sql, params).fetchall()
        return [
            DnsRecord(
                id=row["id"],
                timestamp=row["timestamp"],
                hostname=row["hostname"],
                record_type=row["record_type"],
                value=row["value"],
                ttl=row["ttl"],
                provider=row["provider"],
                action=row["action"],
                success=bool(row["success"]),
                notes=row["notes"],
            )
            for row in rows
        ]


def delete_dns_current(
    db_path: Path,
    hostname: str,
    *,
    executor: Executor | None = None,
) -> bool:
    """Delete a DNS current record. Returns True if deleted."""
    sql = "DELETE FROM dns_current WHERE hostname = ?"
    params = (hostname,)
    if _is_remote(executor):
        _remote_execute(db_path, sql, params, executor=executor)  # type: ignore[arg-type]
        return True
    with get_connection(db_path) as conn:
        cursor = conn.execute(sql, params)
        conn.commit()
        return cursor.rowcount > 0
