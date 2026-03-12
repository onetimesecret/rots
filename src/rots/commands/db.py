# src/rots/commands/db.py
"""Deployment database backup and restore commands."""

import logging
import sqlite3
from pathlib import Path
from typing import Annotated

import cyclopts

from rots.config import Config, join_image_tag

from .common import JsonOutput

logger = logging.getLogger(__name__)

app = cyclopts.App(
    name="db",
    help="Manage the deployment tracking database.",
)


def _default_backup_path(db_path: Path) -> Path:
    """Generate a timestamped backup path next to the source DB."""
    import datetime

    ts = datetime.datetime.now(datetime.UTC).strftime("%Y%m%dT%H%M%SZ")
    return db_path.parent / f"{db_path.stem}.{ts}.bak"


def _get_executor_and_db():
    """Resolve executor from context and return (executor, db_path)."""
    from rots import context

    cfg = Config()
    ex = cfg.get_executor(host=context.host_var.get(None))
    db_path = cfg.get_db_path(ex)
    return ex, db_path


def _db_exists(db_path: Path, executor) -> bool:
    """Check whether the database file exists (local or remote)."""
    from ots_shared.ssh import is_remote

    if is_remote(executor):
        result = executor.run(["test", "-f", str(db_path)])
        return result.ok
    return db_path.exists()


def _db_size(db_path: Path, executor) -> int:
    """Get database file size in bytes (local or remote)."""
    from ots_shared.ssh import is_remote

    if is_remote(executor):
        result = executor.run(["stat", "--printf=%s", str(db_path)])
        if result.ok:
            return int(result.stdout.strip())
        return 0
    return db_path.stat().st_size


@app.command
def backup(
    dest: Annotated[
        Path | None,
        cyclopts.Parameter(
            help=(
                "Destination path for the backup file. "
                "Defaults to a timestamped copy next to the source DB."
            ),
        ),
    ] = None,
    json_output: JsonOutput = False,
):
    """Back up the deployment database to a file.

    Creates a safe SQLite backup using the API (not a raw file copy) so the
    backup is always consistent even if a write is in progress.

    For remote hosts, the backup is created on the remote host using the
    sqlite3 CLI ``.backup`` command.

    Examples:
        ots db backup
        ots db backup /var/backups/deployments.db
        ots db backup --json
    """
    import json as json_mod

    from ots_shared.ssh import is_remote

    ex, db_path = _get_executor_and_db()

    if not _db_exists(db_path, ex):
        msg = f"Database not found: {db_path}"
        if json_output:
            print(json_mod.dumps({"success": False, "error": msg}))
        else:
            logger.error(f"{msg}")
        raise SystemExit(1)

    target = dest or _default_backup_path(db_path)

    if is_remote(ex):
        # Remote: use sqlite3 CLI .backup command on the remote host
        result = ex.run(
            ["sqlite3", str(db_path), f".backup {target}"],
            timeout=30,
        )
        if not result.ok:
            msg = f"Backup failed: {result.stderr.strip()}"
            if json_output:
                print(json_mod.dumps({"success": False, "error": msg}))
            else:
                logger.error(f"{msg}")
            raise SystemExit(1)

        size = _db_size(target, ex)
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        # Use SQLite backup API for a consistent snapshot
        try:
            src_conn = sqlite3.connect(db_path)
            dst_conn = sqlite3.connect(target)
            src_conn.backup(dst_conn)
            src_conn.close()
            dst_conn.close()
        except sqlite3.Error as exc:
            msg = f"Backup failed: {exc}"
            if json_output:
                print(json_mod.dumps({"success": False, "error": msg}))
            else:
                logger.error(f"{msg}")
            raise SystemExit(1)

        size = target.stat().st_size

    result_data = {
        "success": True,
        "source": str(db_path),
        "destination": str(target),
        "size_bytes": size,
    }
    if json_output:
        print(json_mod.dumps(result_data, indent=2))
    else:
        logger.info(f"Backup created: {target}")
        logger.info(f"  Source:  {db_path}")
        logger.info(f"  Size:    {f'{size:,}'} bytes")


@app.command
def restore(
    src: Annotated[
        Path,
        cyclopts.Parameter(
            help="Path to the backup file to restore from.",
        ),
    ],
    yes: Annotated[
        bool,
        cyclopts.Parameter(
            name=["--yes", "-y"],
            help="Skip confirmation prompt.",
        ),
    ] = False,
    json_output: JsonOutput = False,
):
    """Restore the deployment database from a backup file.

    Validates that the source is a valid SQLite database containing the
    required tables before overwriting the live database.

    A pre-restore backup of the current database is automatically created
    alongside the live database before the restore proceeds.

    For remote hosts, the source file must be accessible locally. The
    validated backup is pushed to the remote host via the executor.

    Examples:
        ots db restore /var/backups/deployments.db
        ots db restore /var/backups/deployments.db --yes
    """
    import json as json_mod

    from ots_shared.ssh import is_remote

    if not src.exists():
        msg = f"Backup file not found: {src}"
        if json_output:
            print(json_mod.dumps({"success": False, "error": msg}))
        else:
            logger.error(f"{msg}")
        raise SystemExit(1)

    # Validate the backup is a valid SQLite DB with expected tables
    required_tables = {"deployments", "image_aliases"}
    try:
        conn = sqlite3.connect(src)
        present = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        conn.close()
    except sqlite3.Error as exc:
        msg = f"Cannot read backup file (not a valid SQLite database?): {exc}"
        if json_output:
            print(json_mod.dumps({"success": False, "error": msg}))
        else:
            logger.error(f"{msg}")
        raise SystemExit(1)

    missing = required_tables - present
    if missing:
        msg = f"Backup is missing required tables: {', '.join(sorted(missing))}"
        if json_output:
            print(json_mod.dumps({"success": False, "error": msg}))
        else:
            logger.error(f"{msg}")
        raise SystemExit(1)

    ex, live_db = _get_executor_and_db()

    if not yes and not json_output:
        exists = _db_exists(live_db, ex)
        if exists:
            print(f"This will REPLACE the live database: {live_db}")
        else:
            print(f"This will create the database at: {live_db}")
        print(f"Restoring from: {src}")
        response = input("Continue? [y/N] ")
        if response.lower() not in ("y", "yes"):
            print("Aborted")
            return

    if is_remote(ex):
        # Remote restore: pre-backup on remote, then push local file via SQL dump
        pre_restore_backup: Path | None = None
        if _db_exists(live_db, ex):
            pre_restore_backup = _default_backup_path(live_db)
            ex.run(
                ["sqlite3", str(live_db), f".backup {pre_restore_backup}"],
                timeout=30,
            )

        # Dump local backup to SQL, execute on remote.
        # NOTE: iterdump() loads the entire DB as SQL text in memory. Fine for
        # deployment-metadata-sized databases; for large DBs, consider SFTP
        # transfer of the file followed by a remote .backup instead.
        dump_conn = sqlite3.connect(src)
        sql_dump = "\n".join(dump_conn.iterdump())
        dump_conn.close()

        # Drop existing tables and restore from dump
        result = ex.run(
            ["sqlite3", str(live_db)],
            input=sql_dump,
            timeout=60,
        )
        if not result.ok:
            msg = f"Restore failed: {result.stderr.strip()}"
            if json_output:
                print(json_mod.dumps({"success": False, "error": msg}))
            else:
                logger.error(f"{msg}")
            raise SystemExit(1)
    else:
        # Local restore
        pre_restore_backup = None
        if live_db.exists():
            pre_restore_backup = _default_backup_path(live_db)
            try:
                src_conn = sqlite3.connect(live_db)
                dst_conn = sqlite3.connect(pre_restore_backup)
                src_conn.backup(dst_conn)
                src_conn.close()
                dst_conn.close()
            except sqlite3.Error:
                pre_restore_backup = None  # Non-fatal; proceed with restore

        # Perform the restore using SQLite backup API
        live_db.parent.mkdir(parents=True, exist_ok=True)
        try:
            backup_conn = sqlite3.connect(src)
            live_conn = sqlite3.connect(live_db)
            backup_conn.backup(live_conn)
            backup_conn.close()
            live_conn.close()
        except sqlite3.Error as exc:
            msg = f"Restore failed: {exc}"
            if json_output:
                print(json_mod.dumps({"success": False, "error": msg}))
            else:
                logger.error(f"{msg}")
            raise SystemExit(1)

    result_data = {
        "success": True,
        "source": str(src),
        "destination": str(live_db),
        "pre_restore_backup": str(pre_restore_backup) if pre_restore_backup else None,
    }
    if json_output:
        print(json_mod.dumps(result_data, indent=2))
    else:
        logger.info(f"Restored: {src} -> {live_db}")
        if pre_restore_backup:
            logger.info(f"  Pre-restore backup: {pre_restore_backup}")


@app.command
def deployments(
    web: Annotated[
        int | None,
        cyclopts.Parameter(
            name=["--web", "-w"],
            help="Filter by web instance port (e.g. 7043). Shows all instances if omitted.",
        ),
    ] = None,
    limit: Annotated[
        int,
        cyclopts.Parameter(
            name=["--limit", "-l"],
            help="Maximum number of records to show.",
        ),
    ] = 50,
    json_output: JsonOutput = False,
):
    """Show deployment history from the tracking database.

    Lists deployment records (image, tag, timestamp, action, status) ordered
    most-recent first. Use --web to narrow results to a specific instance port.

    Examples:
        ots db deployments
        ots db deployments --web 7043
        ots db deployments --web 7043 --limit 10
        ots db deployments --json
    """
    import json as json_mod

    from rots import db as db_module

    ex, db_path = _get_executor_and_db()

    if not _db_exists(db_path, ex):
        msg = f"Database not found: {db_path}"
        if json_output:
            print(json_mod.dumps({"success": False, "error": msg}))
        else:
            logger.error(f"{msg}")
            logger.info("Run 'ots init' or deploy an instance first to create the database.")
        raise SystemExit(1)

    records = db_module.get_deployments(db_path, limit=limit, port=web, executor=ex)

    if json_output:
        output = [
            {
                "id": r.id,
                "timestamp": r.timestamp,
                "port": r.port,
                "image": r.image,
                "tag": r.tag,
                "action": r.action,
                "success": r.success,
                "notes": r.notes,
            }
            for r in records
        ]
        print(json_mod.dumps(output, indent=2))
        return

    if not records:
        if web is not None:
            print(f"No deployment history for port {web}.")
        else:
            print("No deployment history found.")
        return

    col_id = f"{'ID':>5}"
    col_ts = f"{'Timestamp':<20}"
    col_port = f"{'Port':<6}"
    col_image = f"{'Image':<30}"
    col_tag = f"{'Tag':<20}"
    col_action = f"{'Action':<12}"
    header = f"{col_id}  {col_ts}  {col_port}  {col_image}  {col_tag}  {col_action}  OK"
    print(header)
    print("-" * len(header))
    for r in records:
        port_str = str(r.port) if r.port is not None else "-"
        ok_str = "yes" if r.success else "NO"
        # Truncate image if too long
        image_display = r.image if len(r.image) <= 30 else r.image[-29:]
        row = (
            f"{r.id:>5}  {r.timestamp:<20}  {port_str:<6}  "
            f"{image_display:<30}  {r.tag:<20}  {r.action:<12}  {ok_str}"
        )
        print(row)


@app.command
def info(
    json_output: JsonOutput = False,
):
    """Show deployment database location and statistics.

    Examples:
        ots db info
        ots db info --json
    """
    import json as json_mod

    from rots import db as db_module

    ex, db_path = _get_executor_and_db()

    if not _db_exists(db_path, ex):
        result = {"db_path": str(db_path), "exists": False}
        if json_output:
            print(json_mod.dumps(result, indent=2))
        else:
            print(f"Database: {db_path}")
            print("  Status: not found (run 'ots init' to create it)")
        return

    size = _db_size(db_path, ex)
    aliases = db_module.get_all_aliases(db_path, executor=ex)

    # Get total deployment count via the executor-aware path
    from ots_shared.ssh import is_remote

    if is_remote(ex):
        rows = db_module._remote_query(
            db_path,
            "SELECT COUNT(*) as cnt FROM deployments",
            executor=ex,
        )
        total = rows[0]["cnt"] if rows else 0
    else:
        with db_module.get_connection(db_path) as conn:
            total = conn.execute("SELECT COUNT(*) FROM deployments").fetchone()[0]

    result = {
        "db_path": str(db_path),
        "exists": True,
        "size_bytes": size,
        "total_deployments": total,
        "aliases": [
            {"alias": a.alias, "image": a.image, "tag": a.tag, "set_at": a.set_at} for a in aliases
        ],
    }
    if json_output:
        print(json_mod.dumps(result, indent=2))
    else:
        print(f"Database: {db_path}")
        print(f"  Size:        {size:,} bytes")
        print(f"  Deployments: {total}")
        if aliases:
            print("  Aliases:")
            for a in aliases:
                print(f"    {a.alias}: {join_image_tag(a.image, a.tag)} (set {a.set_at})")
        else:
            print("  Aliases:     none (CURRENT/ROLLBACK not set)")
        print()
        print("Recovery note:")
        print("  If the database is lost, re-set aliases with:")
        print("    ots image set-current --tag <your-tag>")
        print("  This re-creates CURRENT and ROLLBACK from scratch.")
