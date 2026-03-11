# src/rots/commands/init.py

"""Init command for idempotent setup of rots.

Creates required directories and initializes the deployment database.
Safe to run on new installs and existing systems.  Supports remote
execution via the --host flag and the executor abstraction.
"""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import cyclopts

if TYPE_CHECKING:
    from ots_shared.ssh import Executor

from rots import db
from rots.commands.instance._helpers import apply_quiet
from rots.config import CONFIG_FILES, Config
from rots.environment_file import ENV_FILE_TEMPLATE
from rots.quadlet import DEFAULT_ENV_FILE

logger = logging.getLogger(__name__)

app = cyclopts.App(
    name="init",
    help="Initialize rots directories and database.",
)


def _get_owner_group() -> tuple[int, int]:
    """Get appropriate owner UID and GID for files.

    Returns root:root if running as root, otherwise current user.
    """
    if os.geteuid() == 0:
        return (0, 0)  # root:root
    return (os.getuid(), os.getgid())


def _path_exists(path: Path, executor) -> bool:
    """Check whether *path* exists (local or remote)."""
    from ots_shared.ssh import is_remote

    if is_remote(executor):
        result = executor.run(["test", "-e", str(path)])
        return result.ok
    return path.exists()


def _is_dir(path: Path, executor) -> bool:
    """Check whether *path* is a directory (local or remote)."""
    from ots_shared.ssh import is_remote

    if is_remote(executor):
        result = executor.run(["test", "-d", str(path)])
        return result.ok
    return path.is_dir()


def _create_directory(
    path: Path, *, mode: int = 0o755, quiet: bool = False, executor: Executor | None = None
) -> bool | None:
    """Create directory with mode.

    Returns:
        True if created, False if existed, None if permission denied.
    """
    from ots_shared.ssh import is_remote

    if _path_exists(path, executor):
        if not quiet:
            logger.info(f"  [ok] {path}")
        return False

    if is_remote(executor):
        assert executor is not None
        result = executor.run(["mkdir", "-p", str(path)], sudo=True)
        if not result.ok:
            logger.error(f"  [denied] {path} - remote mkdir failed: {result.stderr.strip()}")
            return None
        executor.run(["chmod", f"{mode:o}", str(path)], sudo=True)
    else:
        try:
            path.mkdir(parents=True, mode=mode)
            uid, gid = _get_owner_group()
            os.chown(path, uid, gid)
        except PermissionError:
            logger.error(f"  [denied] {path} - permission denied (run with sudo?)")
            return None

    if not quiet:
        logger.info(f"  [created] {path}")
    return True


def _copy_template(
    src: Path, dest: Path, *, quiet: bool = False, executor: Executor | None = None
) -> bool | None:
    """Copy template file if destination doesn't exist.

    Both source and destination are resolved via the executor — when remote,
    source is read from the remote filesystem and copied to dest on the same
    host using ``cp -p``.

    Returns:
        True if copied, False if existed or source missing, None if permission denied.
    """
    from ots_shared.ssh import is_remote

    if _path_exists(dest, executor):
        if not quiet:
            logger.info(f"  [ok] {dest}")
        return False

    if not _path_exists(src, executor):
        if not quiet:
            logger.info(f"  [skip] {dest} (source {src} not found)")
        return False

    if is_remote(executor):
        assert executor is not None
        result = executor.run(["cp", "-p", str(src), str(dest)], sudo=True)
        if not result.ok:
            logger.error(f"  [denied] {dest} - remote copy failed: {result.stderr.strip()}")
            return None
    else:
        try:
            shutil.copy2(src, dest)
            uid, gid = _get_owner_group()
            os.chown(dest, uid, gid)
        except PermissionError:
            logger.error(f"  [denied] {dest} - permission denied (run with sudo?)")
            return None

    if not quiet:
        logger.info(f"  [copied] {src} -> {dest}")
    return True


def _write_file(
    path: Path, content: str, *, quiet: bool = False, executor: Executor | None = None
) -> bool | None:
    """Write content to a file if it doesn't exist.

    Returns:
        True if written, False if existed, None if permission denied.
    """
    from ots_shared.ssh import is_remote

    if _path_exists(path, executor):
        if not quiet:
            logger.info(f"  [ok] {path}")
        return False

    if is_remote(executor):
        assert executor is not None
        # Ensure parent dir exists
        executor.run(["mkdir", "-p", str(path.parent)], sudo=True)
        result = executor.run(["tee", str(path)], input=content, sudo=True)
        if not result.ok:
            logger.error(f"  [denied] {path} - remote write failed: {result.stderr.strip()}")
            return None
    else:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
            uid, gid = _get_owner_group()
            os.chown(path, uid, gid)
        except PermissionError:
            logger.error(f"  [denied] {path} - permission denied (run with sudo?)")
            return None

    if not quiet:
        logger.info(f"  [created] {path}")
    return True


def _glob_env_files(var_dir: Path, executor) -> list[str]:
    """List .env-* files in var_dir (local or remote)."""
    from ots_shared.ssh import is_remote

    if is_remote(executor):
        result = executor.run(["sh", "-c", f"ls -1 {var_dir}/.env-* 2>/dev/null"])
        if result.ok and result.stdout.strip():
            return sorted(result.stdout.strip().splitlines())
        return []
    return sorted(str(p) for p in var_dir.glob(".env-*"))


def _init_db(db_path: Path, *, executor=None) -> bool:
    """Initialize the deployment database (local or remote).

    Returns True on success, False on failure.
    """
    try:
        db.init_db(db_path, executor=executor)
        return True
    except Exception:
        return False


@app.default
def init(
    source_dir: Annotated[
        Path | None,
        cyclopts.Parameter(
            name=["--source", "-s"],
            help="Source directory containing config.yaml and .env templates to copy",
        ),
    ] = None,
    quiet: Annotated[
        bool,
        cyclopts.Parameter(name=["--quiet", "-q"], help="Suppress output"),
    ] = False,
    check: Annotated[
        bool,
        cyclopts.Parameter(help="Check status only, don't create anything"),
    ] = False,
):
    """Initialize rots directories and database.

    Creates FHS-compliant directory structure:
      /etc/onetimesecret/          - System configuration
      /var/lib/onetimesecret/      - Variable runtime data

    Initializes the SQLite deployment database for tracking.

    This command is idempotent - safe to run multiple times.

    When --host is set (global flag), runs initialization on the remote host.
    """
    from ots_shared.ssh import is_remote

    from rots import context

    cfg = Config()
    ex = cfg.get_executor(host=context.host_var.get(None))
    remote = is_remote(ex)

    # Apply quiet only when not in check mode (check always shows output)
    apply_quiet(quiet and not check)

    all_ok = True

    # Detect re-initialization (like git init)
    is_reinit = _path_exists(cfg.db_path, ex) or _path_exists(cfg.var_dir, ex)

    if check:
        logger.info("Checking rots setup...")
    else:
        prefix = "Re-initializing" if is_reinit else "Initializing"
        target = f" on {context.host_var.get('local')}" if remote else ""
        logger.info(f"{prefix} rots{target}...")

    # 1. App Configuration - user-managed config files (all optional)
    logger.info("App Configuration:")
    if check:
        for fname in CONFIG_FILES:
            fpath = cfg.config_dir / fname
            if _path_exists(fpath, ex):
                logger.info(f"  [ok] {fpath}")
            else:
                logger.info(f"  [optional] {fpath}")
    else:
        result = _create_directory(cfg.config_dir, mode=0o755, quiet=True, executor=ex)
        if result is None:
            all_ok = False
        else:
            # Directory exists or was created - now handle config files
            if source_dir:
                src = Path(source_dir)
                for fname in CONFIG_FILES:
                    if (
                        _copy_template(
                            src / fname, cfg.config_dir / fname, quiet=quiet, executor=ex
                        )
                        is None
                    ):
                        all_ok = False
            else:
                for fname in CONFIG_FILES:
                    fpath = cfg.config_dir / fname
                    if _path_exists(fpath, ex):
                        logger.info(f"  [ok] {fpath}")
                    else:
                        logger.info(f"  [optional] {fpath}")

    # 2. System Configuration - quadlet files
    logger.info("System Configuration:")
    quadlet_dir = cfg.web_template_path.parent
    users_dir = quadlet_dir / "users"
    template_paths = [
        cfg.web_template_path,
        cfg.worker_template_path,
        cfg.scheduler_template_path,
    ]
    if check:
        for template_path in template_paths:
            if _path_exists(template_path, ex):
                logger.info(f"  [ok] {template_path}")
            else:
                logger.info(f"  [missing] {template_path}")
                all_ok = False
        if _is_dir(users_dir, ex):
            if remote:
                result = ex.run(["ls", str(users_dir)])
                has_content = result.ok and result.stdout.strip()
            else:
                has_content = any(users_dir.iterdir())
            if has_content:
                logger.info(f"  [ok] {users_dir}")
            else:
                logger.info(f"  [empty] {users_dir}")
        else:
            logger.info(f"  [missing] {users_dir}")
    else:
        if _create_directory(quadlet_dir, mode=0o755, quiet=True, executor=ex) is None:
            all_ok = False
        for template_path in template_paths:
            if _path_exists(template_path, ex):
                logger.info(f"  [ok] {template_path}")
            else:
                logger.info(f"  [missing] {template_path}")
        if _is_dir(users_dir, ex):
            if remote:
                result = ex.run(["ls", str(users_dir)])
                has_content = result.ok and result.stdout.strip()
            else:
                has_content = any(users_dir.iterdir())
            if has_content:
                logger.info(f"  [ok] {users_dir}")
            else:
                logger.info(f"  [empty] {users_dir}")

    # 3. Variable data - runtime files
    logger.info("Variable Data:")
    if check:
        if _path_exists(cfg.var_dir, ex):
            env_files = _glob_env_files(cfg.var_dir, ex)
            for env_file in env_files:
                logger.info(f"  [ok] {env_file}")
            if _path_exists(cfg.db_path, ex):
                logger.info(f"  [ok] {cfg.db_path}")
            else:
                logger.info(f"  [missing] {cfg.db_path}")
                all_ok = False
            if not env_files and not _path_exists(cfg.db_path, ex):
                logger.info(f"  [empty] {cfg.var_dir}")
        else:
            logger.info(f"  [missing] {cfg.var_dir}")
            all_ok = False
    else:
        if _create_directory(cfg.var_dir, mode=0o755, quiet=True, executor=ex) is None:
            all_ok = False
        else:
            env_files = _glob_env_files(cfg.var_dir, ex)
            for env_file in env_files:
                logger.info(f"  [ok] {env_file}")
            # Handle database
            if _path_exists(cfg.db_path, ex):
                logger.info(f"  [ok] {cfg.db_path}")
            else:
                if _init_db(cfg.db_path, executor=ex):
                    if not remote:
                        try:
                            uid, gid = _get_owner_group()
                            os.chown(cfg.db_path, uid, gid)
                        except (PermissionError, OSError):
                            pass
                    logger.info(f"  [created] {cfg.db_path}")
                else:
                    suffix = " (run with sudo?)" if not remote else ""
                    logger.error(f"  [denied] {cfg.db_path} - database init failed{suffix}")
                    all_ok = False

    # 4. Infrastructure environment file (required before deploy)
    logger.info("Infrastructure Configuration:")
    if check:
        if _path_exists(DEFAULT_ENV_FILE, ex):
            logger.info(f"  [ok] {DEFAULT_ENV_FILE}")
        else:
            logger.info(f"  [missing] {DEFAULT_ENV_FILE}")
            logger.info("  Run 'ots init' to scaffold this file, then configure it.")
            all_ok = False
    else:
        if _path_exists(DEFAULT_ENV_FILE, ex):
            logger.info(f"  [ok] {DEFAULT_ENV_FILE}")
        else:
            result = _write_file(DEFAULT_ENV_FILE, ENV_FILE_TEMPLATE, quiet=quiet, executor=ex)
            if result is None:
                all_ok = False
            elif result:
                logger.info("    Edit it to add your connection strings and secret values,")
                logger.info("    then run: sudo ots env process")

    # Summary
    if check:
        if all_ok:
            logger.info("Status: All components present")
        else:
            logger.info("Status: Missing components (run 'ots init' to create)")
        return 0 if all_ok else 1

    if all_ok:
        logger.info("Initialization complete.")
    else:
        logger.warning("Initialization incomplete - some operations failed.")
        if remote:
            logger.warning("Try running with elevated privileges on the remote host.")
        else:
            logger.warning("Try running with elevated privileges: sudo ots init")
    logger.info("Next steps:")
    logger.info(f"  1. (Optional) Place config overrides in {cfg.config_dir}/")
    logger.info(f"  2. Edit {DEFAULT_ENV_FILE} with infrastructure env vars and secret values")
    logger.info("  3. Run 'sudo ots env process' to move secret values into podman secret store")
    logger.info("  4. Run 'ots image pull --tag <version>' to pull an image")
    logger.info("  5. Run 'ots instance deploy <port>' to start an instance")

    return 0 if all_ok else 1
