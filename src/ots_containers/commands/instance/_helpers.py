# src/ots_containers/commands/instance/_helpers.py

"""Internal helper functions for instance commands."""

from __future__ import annotations

import contextlib
import fcntl
import logging
import os
import shlex
import socket
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from ots_shared.ssh import is_remote as _is_remote

from ots_containers import systemd
from ots_containers.environment_file import get_secrets_from_env_file
from ots_containers.systemd import SystemctlError

from .annotations import InstanceType

if TYPE_CHECKING:
    from ots_shared.ssh.executor import Executor

logger = logging.getLogger(__name__)

#: Default lock file path for serialising concurrent deploy/redeploy operations.
DEPLOY_LOCK_PATH = Path("/var/lib/onetimesecret/deploy.lock")


@contextlib.contextmanager
def deploy_lock(
    lock_path: Path = DEPLOY_LOCK_PATH,
    *,
    executor: Executor | None = None,
):
    """Exclusive advisory lock to serialise concurrent deploy/redeploy operations.

    **Local deploys** use ``fcntl.LOCK_EX | fcntl.LOCK_NB`` so a second caller
    gets an immediate ``BlockingIOError`` rather than hanging indefinitely.

    **Remote deploys** (via SSHExecutor) use an advisory lock file on the remote
    host.  The file is created atomically with shell ``noclobber`` (``set -C``),
    preventing TOCTOU races between operators.  A staleness window (default 30
    minutes) auto-expires abandoned locks from crashed sessions.

    Args:
        lock_path: Path to the lock file.  Created (including parent dirs) if
                   it does not exist.  Falls back to a tempfile when the
                   standard path is not writable (e.g. macOS dev environment).
        executor: Optional executor for remote command dispatch.

    Raises:
        SystemExit(1): If the lock is already held by another process/operator.

    Usage::

        with deploy_lock(executor=executor):
            systemd.start(unit)
    """
    if _is_remote(executor):
        _remote_lock_acquire(lock_path, executor)  # type: ignore[arg-type]
        try:
            yield
        finally:
            _remote_lock_release(lock_path, executor)  # type: ignore[arg-type]
        return

    # --- Local locking (fcntl) ---
    resolved = _resolve_lock_path(lock_path)

    try:
        resolved.parent.mkdir(parents=True, exist_ok=True)
        fh = resolved.open("a")  # "a" so we never truncate an existing file
    except OSError as exc:
        # Unable to create/open the lock file — non-fatal on dev machines
        print(
            f"Warning: cannot open deploy lock file {resolved}: {exc}",
            file=sys.stderr,
        )
        yield
        return

    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        fh.close()
        print(
            "Error: another deploy is already in progress "
            f"(lock held: {resolved}).\n"
            "Wait for it to finish or remove the lock file manually if it is stale.",
            file=sys.stderr,
        )
        raise SystemExit(1)

    try:
        yield
    finally:
        fcntl.flock(fh, fcntl.LOCK_UN)
        fh.close()


#: Seconds before a remote lock file is considered stale and can be broken.
REMOTE_LOCK_STALE_SECONDS = 1800  # 30 minutes


def _remote_lock_acquire(
    lock_path: Path,
    executor: Executor,
    *,
    stale_seconds: int = REMOTE_LOCK_STALE_SECONDS,
) -> None:
    """Acquire an advisory deploy lock on a remote host.

    Uses shell ``noclobber`` (``set -C``) for atomic file creation to prevent
    TOCTOU races between concurrent operators.

    If the lock file already exists and is older than *stale_seconds*, it is
    automatically broken (with a warning) on the assumption that the holder
    crashed without cleanup.
    """
    lock_str = shlex.quote(str(lock_path))
    identity = f"{socket.gethostname()}:{os.getpid()}"

    # Ensure parent directory exists
    executor.run(["mkdir", "-p", str(lock_path.parent)], sudo=True, timeout=15)

    # Atomic create via noclobber — fails if the file already exists
    result = executor.run(
        ["/bin/sh", "-c", f"set -C; echo {shlex.quote(identity)} > {lock_str}"],
        sudo=True,
    )

    if result.ok:
        logger.debug("Remote deploy lock acquired: %s", lock_path)
        return

    # Lock file exists — check staleness
    stat_result = executor.run(
        ["/bin/sh", "-c", f"stat -c %Y {lock_str} 2>/dev/null || echo 0"],
        sudo=True,
    )
    try:
        mtime = int(stat_result.stdout.strip())
    except ValueError:
        mtime = 0

    if mtime > 0:
        age_result = executor.run(["date", "+%s"])
        try:
            now = int(age_result.stdout.strip())
        except ValueError:
            now = 0
        age = now - mtime if now > mtime else 0

        if age > stale_seconds:
            # Stale lock — break it
            cat = executor.run(["cat", str(lock_path)], sudo=True)
            holder = cat.stdout.strip() if cat.ok else "unknown"
            logger.warning(
                "Breaking stale remote deploy lock (held by %s, age %ds > %ds)",
                holder,
                age,
                stale_seconds,
            )
            executor.run(["rm", "-f", str(lock_path)], sudo=True)
            # Retry once
            retry = executor.run(
                ["/bin/sh", "-c", f"set -C; echo {shlex.quote(identity)} > {lock_str}"],
                sudo=True,
            )
            if retry.ok:
                return

    # Lock is actively held — report and exit
    cat = executor.run(["cat", str(lock_path)], sudo=True)
    holder = cat.stdout.strip() if cat.ok else "unknown"
    print(
        f"Error: another deploy is already in progress on the remote host.\n"
        f"  Lock file: {lock_path}\n"
        f"  Held by: {holder}\n"
        "Wait for it to finish, or remove the lock if it is stale:\n"
        f"  ssh <host> sudo rm {lock_path}",
        file=sys.stderr,
    )
    raise SystemExit(1)


def _remote_lock_release(lock_path: Path, executor: Executor) -> None:
    """Release advisory deploy lock on a remote host."""
    result = executor.run(["rm", "-f", str(lock_path)], sudo=True)
    if not result.ok:
        logger.warning("Failed to remove remote deploy lock: %s", result.stderr)
    else:
        logger.debug("Remote deploy lock released: %s", lock_path)


def _resolve_lock_path(lock_path: Path) -> Path:
    """Return *lock_path* if its parent is writable, otherwise a temp-dir path."""
    import tempfile

    # Try the requested path
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        # Test writeability without creating the file permanently
        test = lock_path.parent / ".ots_lock_probe"
        test.touch()
        test.unlink()
        return lock_path
    except OSError:
        pass

    # Fall back to a user-writable temp location
    tmp = Path(tempfile.gettempdir()) / "ots-deploy.lock"
    return tmp


def format_command(cmd: Sequence[str]) -> str:
    """Format command list as a copy-pasteable shell string.

    Arguments containing spaces, special characters, or that are empty
    will be properly quoted using shlex.quote.
    """
    return " ".join(shlex.quote(arg) for arg in cmd)


def format_journalctl_hint(instances: dict[InstanceType, list[str]]) -> str:
    """Generate a journalctl command to view logs for the given instances.

    Args:
        instances: Dict mapping InstanceType to list of identifiers

    Returns:
        A journalctl command string with -t flags for each instance.
    """
    tags = []
    for itype, ids in instances.items():
        for id_ in ids:
            tags.append(f"onetime-{itype.value}-{id_}")

    if not tags:
        return ""

    tag_args = " ".join(f"-t {shlex.quote(tag)}" for tag in tags)
    return f"journalctl {tag_args} -f"


def build_secret_args(env_file: Path, *, executor: Executor | None = None) -> list[str]:
    """Build podman --secret arguments from environment file.

    Reads SECRET_VARIABLE_NAMES from the env file and generates
    corresponding --secret flags for podman run.

    Args:
        env_file: Path to environment file (e.g., /etc/default/onetimesecret)
        executor: Optional executor for remote file access

    Returns:
        List of command arguments: ["--secret", "name,type=env,target=VAR", ...]
    """
    if _is_remote(executor):
        result = executor.run(["test", "-f", str(env_file)])  # type: ignore[union-attr]
        if not result.ok:
            return []
    elif not env_file.exists():
        return []

    secret_specs = get_secrets_from_env_file(env_file, executor=executor)
    args: list[str] = []
    for spec in secret_specs:
        args.extend(
            [
                "--secret",
                f"{spec.secret_name},type=env,target={spec.env_var_name}",
            ]
        )
    return args


def resolve_identifiers(
    identifiers: tuple[str, ...],
    instance_type: InstanceType | None,
    running_only: bool = False,
    *,
    executor: Executor | None = None,
) -> dict[InstanceType, list[str]]:
    """Resolve instance identifiers from explicit args or auto-discovery.

    Args:
        identifiers: Explicitly provided identifiers. If non-empty, requires instance_type.
        instance_type: Required when identifiers provided. If None with no identifiers,
                      discovers all types.
        running_only: If True, only discover running instances.

    Returns:
        Dict mapping InstanceType to list of identifiers (as strings).

    Raises:
        SystemExit: If identifiers provided without instance_type.
    """
    # If identifiers provided, type is required
    if identifiers:
        if instance_type is None:
            raise SystemExit(
                "Instance type required when identifiers are specified. "
                "Use --web, --worker, or --scheduler."
            )
        # Validate web instance identifiers are valid ports
        if instance_type == InstanceType.WEB:
            for id_ in identifiers:
                try:
                    port = int(id_)
                    if not (1 <= port <= 65535):
                        raise SystemExit(f"Invalid port number: {id_} (must be 1-65535)")
                except ValueError:
                    raise SystemExit(f"Invalid port for web instance: {id_!r} (must be numeric)")
        return {instance_type: list(identifiers)}

    # No identifiers: discover based on type filter
    result: dict[InstanceType, list[str]] = {}

    # If type specified, only discover that type
    if instance_type is not None:
        if instance_type == InstanceType.WEB:
            ports = systemd.discover_web_instances(running_only=running_only, executor=executor)
            if ports:
                result[InstanceType.WEB] = [str(p) for p in ports]
        elif instance_type == InstanceType.WORKER:
            workers = systemd.discover_worker_instances(
                running_only=running_only, executor=executor
            )
            if workers:
                result[InstanceType.WORKER] = workers
        elif instance_type == InstanceType.SCHEDULER:
            schedulers = systemd.discover_scheduler_instances(
                running_only=running_only, executor=executor
            )
            if schedulers:
                result[InstanceType.SCHEDULER] = schedulers
        return result

    # No type: discover ALL types
    ports = systemd.discover_web_instances(running_only=running_only, executor=executor)
    if ports:
        result[InstanceType.WEB] = [str(p) for p in ports]

    workers = systemd.discover_worker_instances(running_only=running_only, executor=executor)
    if workers:
        result[InstanceType.WORKER] = workers

    schedulers = systemd.discover_scheduler_instances(running_only=running_only, executor=executor)
    if schedulers:
        result[InstanceType.SCHEDULER] = schedulers

    return result


def for_each_instance(
    instances: dict[InstanceType, list[str]],
    delay: int,
    action: Callable[[InstanceType, str], None],
    verb: str,
    *,
    show_logs_hint: bool = False,
) -> int:
    """Run action for each instance with delay between.

    Args:
        instances: Dict mapping InstanceType to list of identifiers
        delay: Seconds to wait between operations
        action: Callable taking (instance_type, identifier)
        verb: Present participle for logging (e.g., "Restarting")
        show_logs_hint: If True, print journalctl command to view logs

    Returns:
        Total number of instances processed.
    """
    # Flatten to list of (type, id) tuples
    items: list[tuple[InstanceType, str]] = []
    for itype, ids in instances.items():
        for id_ in ids:
            items.append((itype, id_))

    total = len(items)
    if total == 0:
        print("No instances found to operate on.")
        return 0

    for i, (itype, id_) in enumerate(items, 1):
        unit = systemd.unit_name(itype.value, id_)
        print(f"[{i}/{total}] {verb} {unit}...")
        try:
            action(itype, id_)
        except SystemctlError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            if exc.journal:
                print(f"\nRecent journal output for {exc.unit}:", file=sys.stderr)
                for line in exc.journal.splitlines():
                    print(f"  {line}", file=sys.stderr)
            raise SystemExit(1) from None
        if i < total and delay > 0:
            print(f"Waiting {delay}s...")
            time.sleep(delay)

    print(f"Processed {total} instance(s)")

    if show_logs_hint:
        hint = format_journalctl_hint(instances)
        if hint:
            print(f"\nView logs: {hint}")

    return total


def run_hook(
    hook_cmd: str,
    stage: str,
    quiet: bool = False,
    *,
    executor: Executor | None = None,
) -> None:
    """Execute a pre- or post-deploy hook command **locally**.

    Hooks always run on the local machine regardless of whether *executor*
    points to a remote host.  This is a deliberate safety constraint: hooks
    are operator-supplied scripts that should execute in the local
    environment where the CLI is invoked, not on the remote target.

    The command is run via the shell (``/bin/sh -c``).  If the command exits
    non-zero, ``SystemExit(1)`` is raised with a descriptive message so that
    the caller can abort the deployment.

    Args:
        hook_cmd: Shell command string to execute (e.g. ``"./scripts/scan.sh"``).
        stage: Label for log/error messages (e.g. ``"pre-hook"`` or ``"post-hook"``).
        quiet: Suppress progress output when True.
        executor: Accepted for call-site compatibility but ignored; hooks
            are never forwarded to a remote executor.

    Raises:
        SystemExit(1): If the hook exits non-zero.
    """
    if not quiet:
        print(f"Running {stage}: {hook_cmd}")

    proc = subprocess.run(
        hook_cmd,
        shell=True,  # noqa: S602
        text=True,
    )

    if proc.returncode != 0:
        print(f"  ERROR: {stage} failed (exit {proc.returncode}): {hook_cmd}", file=sys.stderr)
        raise SystemExit(1)
    if not quiet:
        print(f"  {stage} passed")
