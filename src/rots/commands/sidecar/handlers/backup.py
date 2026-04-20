# src/rots/commands/sidecar/handlers/backup.py

"""Backup job provisioning handlers (``backup.*``).

These handlers run on the **db sidecar** (``roles={"db"}``). They install
and uninstall systemd timer-based backup jobs, with rclone as the upload
transport. Profiles are named (``"db-daily"``, ``"valkey-hourly"``, etc.)
and each profile maps to a fixed ``.service`` + ``.timer`` pair and an
rclone config fragment.

System layout
-------------
* Units live in :data:`SYSTEMD_DIR`.
* rclone fragments live in :data:`RCLONE_FRAGMENTS_DIR` (one file per
  profile). The base rclone config is owned by the operator; handlers
  only touch files under this fragments dir.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from rots.sidecar.commands import Command, CommandResult, register_handler

from ._types import BackupInstallData, BackupUninstallData

__all__ = [
    "ALLOWED_PROFILES",
    "BackupInstallData",
    "BackupUninstallData",
    "RCLONE_FRAGMENTS_DIR",
    "SYSTEMD_DIR",
    "handle_install",
    "handle_uninstall",
]

logger = logging.getLogger(__name__)

# Directory where generated systemd unit + timer files are written. Each
# profile produces two files:
#   ots-backup-<profile>.service
#   ots-backup-<profile>.timer
# Implementations MUST NOT write anywhere else.
SYSTEMD_DIR: str = "/etc/systemd/system"

# Per-profile rclone configuration fragments. The rclone binary assembles
# the full config by reading every ``*.conf`` under this directory in
# addition to the primary config. Implementations create / remove a single
# file per profile: ``<profile>.conf``.
RCLONE_FRAGMENTS_DIR: str = "/etc/rclone/conf.d"

# Allowlisted profile names. Narrow on purpose -- expanding this requires
# corresponding templates. Impl agents add entries here AND ship matching
# script content for the ``.service`` unit.
ALLOWED_PROFILES: frozenset[str] = frozenset({"db-daily", "valkey-hourly"})

# Regex for the ``<remote>`` part of a ``<remote>:<path>`` rclone target.
_REMOTE_RE = re.compile(r"^[A-Za-z0-9_-]+$")

# File modes for generated artifacts.
_UNIT_MODE = 0o644
_RCLONE_FRAGMENT_MODE = 0o640


# --- unit rendering -------------------------------------------------------


def _render_service(profile: str, target: str) -> str:
    """Return the content of ``ots-backup-<profile>.service``.

    The ExecStart is a ``/bin/bash -c "..."`` invocation with ``set -euo
    pipefail`` so any non-zero step propagates and systemd records the
    failure.
    """
    if profile == "db-daily":
        description = "OneTimeSecret Postgres daily backup"
        # pg_dumpall streamed through gzip and piped to rclone rcat --
        # avoids staging the dump on disk. set -euo pipefail guarantees the
        # pipeline fails the service on any broken stage.
        script = (
            "set -euo pipefail; "
            'ts="$(date -u +%Y%m%dT%H%M%SZ)"; '
            "sudo -u postgres pg_dumpall "
            "| gzip "
            f'| rclone rcat "{target}/postgres-${{ts}}.sql.gz"'
        )
    elif profile == "valkey-hourly":
        description = "OneTimeSecret Valkey hourly backup"
        # BGSAVE is asynchronous -- poll LASTSAVE until it advances past
        # the value we captured before triggering it. Then copy the RDB
        # snapshot to the remote. redis-cli talks to the local valkey
        # over its default socket / localhost.
        script = (
            "set -euo pipefail; "
            'ts="$(date -u +%Y%m%dT%H%M%SZ)"; '
            'start="$(redis-cli LASTSAVE)"; '
            "redis-cli BGSAVE >/dev/null; "
            'while [ "$(redis-cli LASTSAVE)" = "$start" ]; do sleep 1; done; '
            "rclone copyto /var/lib/valkey/dump.rdb "
            f'"{target}/valkey-${{ts}}.rdb"'
        )
    else:
        # Should have been rejected by the allowlist check upstream.
        raise ValueError(f"Unsupported profile: {profile!r}")

    return (
        "[Unit]\n"
        f"Description={description}\n"
        "After=network-online.target\n"
        "Wants=network-online.target\n"
        "\n"
        "[Service]\n"
        "Type=oneshot\n"
        f"ExecStart=/bin/bash -c {_shell_quote(script)}\n"
    )


def _render_timer(profile: str, schedule: str) -> str:
    """Return the content of ``ots-backup-<profile>.timer``."""
    unit = f"ots-backup-{profile}.service"
    return (
        "[Unit]\n"
        f"Description=Timer for {unit}\n"
        "\n"
        "[Timer]\n"
        f"OnCalendar={schedule}\n"
        "Persistent=true\n"
        f"Unit={unit}\n"
        "\n"
        "[Install]\n"
        "WantedBy=timers.target\n"
    )


def _render_rclone_fragment(profile: str, target: str) -> str:
    """Return the rclone fragment content for ``<profile>.conf``.

    The fragment is a comment-only stub: the ``<remote>`` referenced in the
    ``target`` is expected to exist in the operator-managed primary rclone
    config. Writing a comment-only file lets uninstall track existence
    without committing handler code to a specific remote schema.
    """
    return (
        f"# rclone fragment for backup profile {profile}\n"
        "# The <remote> part of the target must be defined in the\n"
        "# operator-managed rclone config. This file is managed by\n"
        "# rots; do not edit by hand.\n"
    )


def _shell_quote(s: str) -> str:
    """Wrap ``s`` in double quotes for an ``ExecStart=/bin/bash -c "..."``.

    Escapes backslashes and double quotes. Shell ``$`` and backticks are
    left intact -- the rendered script deliberately uses shell expansion.
    """
    escaped = s.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


# --- validation helpers ---------------------------------------------------


def _validate_target(target: str) -> str | None:
    """Return ``None`` if ``target`` is acceptable, else an error string."""
    if not target or target.strip() == "":
        return "target must be non-empty"
    if "\n" in target:
        return "target must not contain newline"
    # Colon-style only. The fragment-style ("[remote]\n...") path listed in
    # the docstring is not exercised by current profiles, so we reject it
    # explicitly rather than silently accepting something the handler does
    # not finish.
    if ":" not in target:
        return "target must be in '<remote>:<path>' form"
    remote, _, path = target.partition(":")
    if not _REMOTE_RE.match(remote):
        return f"invalid remote name {remote!r}: must match ^[A-Za-z0-9_-]+$"
    if "\n" in path:
        return "target path must not contain newline"
    return None


def _validate_schedule(schedule: str) -> str | None:
    """Return ``None`` if ``schedule`` is accepted by ``systemd-analyze``."""
    if not schedule or not schedule.strip():
        return "schedule must be non-empty"
    try:
        subprocess.run(
            ["systemd-analyze", "calendar", schedule],
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return "systemd-analyze not available to validate schedule"
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip() or (exc.stdout or "").strip()
        return f"invalid schedule {schedule!r}: {stderr}"
    return None


# --- atomic write ---------------------------------------------------------


def _read_existing(path: Path) -> bytes | None:
    """Return the bytes at ``path`` or ``None`` if missing.

    Uses ``O_NOFOLLOW`` to refuse to read through a symlink -- a symlink
    at the target location is a hijack vector. The caller treats an
    ``OSError`` as "cannot safely read, proceed with a write anyway" by
    raising; we surface it.
    """
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return None
    with os.fdopen(fd, "rb") as f:
        return f.read()


def _write_if_different(path: Path, content: str, mode: int) -> bool:
    """Write ``content`` to ``path`` atomically iff it differs.

    Returns ``True`` if the file was (re)written, ``False`` if the existing
    content already matched byte-for-byte. Raises :class:`OSError` on any
    filesystem failure -- the caller decides how to surface it.
    """
    encoded = content.encode("utf-8")

    existing = _read_existing(path)
    if existing == encoded:
        return False

    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)

    tmp_fd: int | None = None
    tmp_name: str | None = None
    try:
        tmp_fd, tmp_name = tempfile.mkstemp(
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=str(parent),
        )
        with os.fdopen(tmp_fd, "wb") as f:
            tmp_fd = None  # fdopen owns it now
            f.write(encoded)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp_name, mode)
        os.rename(tmp_name, path)
        tmp_name = None
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

    return True


# --- systemctl wrappers ---------------------------------------------------


def _systemctl(*args: str) -> subprocess.CompletedProcess[str]:
    """Invoke ``systemctl`` and return the completed process.

    Does NOT raise on non-zero exit -- the caller inspects ``returncode``.
    """
    return subprocess.run(
        ["systemctl", *args],
        check=False,
        capture_output=True,
        text=True,
    )


def _systemctl_checked(*args: str) -> None:
    """Invoke ``systemctl`` and raise :class:`subprocess.CalledProcessError` on failure."""
    subprocess.run(
        ["systemctl", *args],
        check=True,
        capture_output=True,
        text=True,
    )


# --- handlers -------------------------------------------------------------


@register_handler(Command.BACKUP_INSTALL, roles={"db"})
def handle_install(params: dict[str, Any]) -> CommandResult:
    """Install a backup profile's ``.service`` + ``.timer`` + rclone fragment.

    See module docstring / spec for the full param + behaviour contract.
    """
    # --- param gates ------------------------------------------------------
    profile = params.get("profile")
    if not isinstance(profile, str) or not profile:
        return CommandResult.fail("Missing or empty 'profile' parameter")
    if profile not in ALLOWED_PROFILES:
        return CommandResult.fail(
            f"Rejected profile: {profile!r}. Allowed profiles: {sorted(ALLOWED_PROFILES)}"
        )

    target = params.get("target")
    if not isinstance(target, str):
        return CommandResult.fail("Missing 'target' parameter")
    target_err = _validate_target(target)
    if target_err is not None:
        # Do not echo ``target`` -- it may carry credentials in a URL.
        return CommandResult.fail(f"Invalid target for profile {profile!r}: {target_err}")

    schedule = params.get("schedule")
    if not isinstance(schedule, str):
        return CommandResult.fail("Missing 'schedule' parameter")
    schedule_err = _validate_schedule(schedule)
    if schedule_err is not None:
        return CommandResult.fail(schedule_err)

    # --- render -----------------------------------------------------------
    service_content = _render_service(profile, target)
    timer_content = _render_timer(profile, schedule)
    rclone_content = _render_rclone_fragment(profile, target)

    service_path = Path(SYSTEMD_DIR) / f"ots-backup-{profile}.service"
    timer_path = Path(SYSTEMD_DIR) / f"ots-backup-{profile}.timer"
    rclone_path = Path(RCLONE_FRAGMENTS_DIR) / f"{profile}.conf"

    # --- write-if-different ----------------------------------------------
    any_changed = False
    try:
        for path, content, mode in (
            (service_path, service_content, _UNIT_MODE),
            (timer_path, timer_content, _UNIT_MODE),
            (rclone_path, rclone_content, _RCLONE_FRAGMENT_MODE),
        ):
            wrote = _write_if_different(path, content, mode)
            if wrote:
                logger.info("backup.install wrote %s for profile %s", path, profile)
                any_changed = True
    except OSError as exc:
        return CommandResult.fail(f"Failed to write backup artifacts for {profile!r}: {exc}")

    # --- daemon-reload (only if something changed) ------------------------
    if any_changed:
        try:
            _systemctl_checked("daemon-reload")
        except subprocess.CalledProcessError as exc:
            stderr = (exc.stderr or "").strip()
            return CommandResult.fail(f"systemctl daemon-reload failed: {stderr}")

    # --- enable --now (idempotent at systemd layer) -----------------------
    timer_unit = f"ots-backup-{profile}.timer"
    try:
        _systemctl_checked("enable", "--now", timer_unit)
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        return CommandResult.fail(f"systemctl enable --now {timer_unit} failed: {stderr}")

    data = BackupInstallData(
        unit=f"ots-backup-{profile}.service",
        timer=timer_unit,
        changed=any_changed,
    )
    return CommandResult.ok(data)


@register_handler(Command.BACKUP_UNINSTALL, roles={"db"})
def handle_uninstall(params: dict[str, Any]) -> CommandResult:
    """Remove a previously-installed backup profile.

    See module docstring / spec for the full param + behaviour contract.
    """
    profile = params.get("profile")
    if not isinstance(profile, str) or not profile:
        return CommandResult.fail("Missing or empty 'profile' parameter")
    if profile not in ALLOWED_PROFILES:
        return CommandResult.fail(
            f"Rejected profile: {profile!r}. Allowed profiles: {sorted(ALLOWED_PROFILES)}"
        )

    timer_unit = f"ots-backup-{profile}.timer"
    service_path = Path(SYSTEMD_DIR) / f"ots-backup-{profile}.service"
    timer_path = Path(SYSTEMD_DIR) / f"ots-backup-{profile}.timer"
    rclone_path = Path(RCLONE_FRAGMENTS_DIR) / f"{profile}.conf"

    # --- disable --now ----------------------------------------------------
    # Run unconditionally. Expected-failure case: unit is not loaded (e.g.
    # the profile was never installed, or already uninstalled). We detect
    # that via stderr and suppress; any other failure surfaces.
    disable_proc = _systemctl(
        "disable",
        "--now",
        timer_unit,
    )
    if disable_proc.returncode != 0:
        stderr = (disable_proc.stderr or "").strip()
        lower = stderr.lower()
        not_loaded = (
            "not loaded" in lower
            or "no such file" in lower
            or "does not exist" in lower
            or "not found" in lower
        )
        if not not_loaded:
            return CommandResult.fail(f"systemctl disable --now {timer_unit} failed: {stderr}")

    # --- unlink the three files ------------------------------------------
    any_removed = False
    try:
        for path in (service_path, timer_path, rclone_path):
            existed = path.exists()
            path.unlink(missing_ok=True)
            if existed:
                logger.info("backup.uninstall removed %s for profile %s", path, profile)
                any_removed = True
    except OSError as exc:
        return CommandResult.fail(f"Failed to remove backup artifacts for {profile!r}: {exc}")

    # --- daemon-reload (only if something was removed) --------------------
    if any_removed:
        try:
            _systemctl_checked("daemon-reload")
        except subprocess.CalledProcessError as exc:
            stderr = (exc.stderr or "").strip()
            return CommandResult.fail(f"systemctl daemon-reload failed: {stderr}")

    data = BackupUninstallData(ok=True, changed=any_removed)
    return CommandResult.ok(data)
