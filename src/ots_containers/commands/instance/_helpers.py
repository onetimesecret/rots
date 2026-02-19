# src/ots_containers/commands/instance/_helpers.py

"""Internal helper functions for instance commands."""

import contextlib
import fcntl
import shlex
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from pathlib import Path

from ots_containers import systemd
from ots_containers.environment_file import get_secrets_from_env_file
from ots_containers.systemd import SystemctlError

from .annotations import InstanceType

#: Default lock file path for serialising concurrent deploy/redeploy operations.
DEPLOY_LOCK_PATH = Path("/var/lib/onetimesecret/deploy.lock")


@contextlib.contextmanager
def deploy_lock(lock_path: Path = DEPLOY_LOCK_PATH):
    """Exclusive advisory lock to serialise concurrent deploy/redeploy operations.

    Uses ``fcntl.LOCK_EX | fcntl.LOCK_NB`` so a second caller gets an
    immediate ``BlockingIOError`` rather than hanging indefinitely.

    Args:
        lock_path: Path to the lock file.  Created (including parent dirs) if
                   it does not exist.  Falls back to a tempfile when the
                   standard path is not writable (e.g. macOS dev environment).

    Raises:
        SystemExit(1): If the lock is already held by another process.

    Usage::

        with deploy_lock():
            systemd.start(unit)
    """
    # Resolve the actual lock path — fall back when system path is not writable
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


def build_secret_args(env_file: Path) -> list[str]:
    """Build podman --secret arguments from environment file.

    Reads SECRET_VARIABLE_NAMES from the env file and generates
    corresponding --secret flags for podman run.

    Args:
        env_file: Path to environment file (e.g., /etc/default/onetimesecret)

    Returns:
        List of command arguments: ["--secret", "name,type=env,target=VAR", ...]
    """
    if not env_file.exists():
        return []

    secret_specs = get_secrets_from_env_file(env_file)
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
            ports = systemd.discover_web_instances(running_only=running_only)
            if ports:
                result[InstanceType.WEB] = [str(p) for p in ports]
        elif instance_type == InstanceType.WORKER:
            workers = systemd.discover_worker_instances(running_only=running_only)
            if workers:
                result[InstanceType.WORKER] = workers
        elif instance_type == InstanceType.SCHEDULER:
            schedulers = systemd.discover_scheduler_instances(running_only=running_only)
            if schedulers:
                result[InstanceType.SCHEDULER] = schedulers
        return result

    # No type: discover ALL types
    ports = systemd.discover_web_instances(running_only=running_only)
    if ports:
        result[InstanceType.WEB] = [str(p) for p in ports]

    workers = systemd.discover_worker_instances(running_only=running_only)
    if workers:
        result[InstanceType.WORKER] = workers

    schedulers = systemd.discover_scheduler_instances(running_only=running_only)
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


def run_hook(hook_cmd: str, stage: str, quiet: bool = False) -> None:
    """Execute a pre- or post-deploy hook command.

    The command is run via the shell (``/bin/sh -c``).  If the command exits
    non-zero, ``SystemExit(1)`` is raised with a descriptive message so that
    the caller can abort the deployment.

    Args:
        hook_cmd: Shell command string to execute (e.g. ``"./scripts/scan.sh"``).
        stage: Label for log/error messages (e.g. ``"pre-hook"`` or ``"post-hook"``).
        quiet: Suppress progress output when True.

    Raises:
        SystemExit(1): If the hook exits non-zero.
    """
    if not quiet:
        print(f"Running {stage}: {hook_cmd}")
    result = subprocess.run(
        hook_cmd,
        shell=True,  # noqa: S602
        text=True,
    )
    if result.returncode != 0:
        print(f"  ERROR: {stage} failed (exit {result.returncode}): {hook_cmd}", file=sys.stderr)
        raise SystemExit(1)
    if not quiet:
        print(f"  {stage} passed")
