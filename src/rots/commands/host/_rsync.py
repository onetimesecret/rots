# src/rots/commands/host/_rsync.py

"""Rsync wrapper with version detection and macOS compatibility.

macOS ships openrsync (2.6.9 compat) whose --checksum behaviour is
unreliable.  This module detects the rsync version, warns when < 3.x,
and respects the RSYNC_PATH env var for overriding (e.g. Homebrew rsync
at /opt/homebrew/bin/rsync).
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RsyncInfo:
    """Detected rsync binary and version."""

    path: str
    version: str
    major: int
    minor: int
    is_openrsync: bool

    @property
    def supports_checksum(self) -> bool:
        """Whether --checksum is reliable (rsync >= 3.0)."""
        return self.major >= 3 and not self.is_openrsync


def detect_rsync() -> RsyncInfo:
    """Detect rsync binary and version.

    Resolution order:
    1. RSYNC_PATH env var
    2. rsync on PATH

    Raises:
        SystemExit: If rsync is not found.
    """
    rsync_path = os.environ.get("RSYNC_PATH")
    if rsync_path:
        if not os.path.isfile(rsync_path):
            raise SystemExit(f"RSYNC_PATH={rsync_path} does not exist")
    else:
        rsync_path = shutil.which("rsync")
        if not rsync_path:
            raise SystemExit("rsync not found on PATH. Install rsync or set RSYNC_PATH.")

    try:
        result = subprocess.run(
            [rsync_path, "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        raise SystemExit(f"Failed to run {rsync_path} --version: {exc}") from exc

    output = result.stdout + result.stderr
    is_openrsync = "openrsync" in output.lower()

    # Parse version: "rsync  version 3.2.7  protocol version 31"
    # or openrsync: "openrsync: protocol version 20, rsync version 2.6.9 compat"
    version_match = re.search(r"version\s+(\d+)\.(\d+)\.?(\d*)", output)
    if version_match:
        major = int(version_match.group(1))
        minor = int(version_match.group(2))
        patch = version_match.group(3) or "0"
        version = f"{major}.{minor}.{patch}"
    else:
        major, minor = 0, 0
        version = "unknown"

    return RsyncInfo(
        path=rsync_path,
        version=version,
        major=major,
        minor=minor,
        is_openrsync=is_openrsync,
    )


def warn_if_macos_rsync(info: RsyncInfo) -> None:
    """Print a warning if rsync looks like macOS openrsync with unreliable --checksum."""
    if info.is_openrsync or (info.major > 0 and info.major < 3):
        tag = " (openrsync)" if info.is_openrsync else ""
        logger.warning(
            f"rsync {info.version} detected{tag} — --checksum may be unreliable.\n"
            "  Set RSYNC_PATH to a newer rsync (e.g. /opt/homebrew/bin/rsync) "
            "for reliable checksums."
        )


def build_rsync_push_cmd(
    *,
    rsync_info: RsyncInfo,
    local_files: list[Path],
    ssh_host: str,
    remote_dir: str,
    dry_run: bool = True,
    backup: bool = True,
) -> list[str]:
    """Build an rsync command for pushing config files.

    Returns the command as a list of strings suitable for subprocess.run.
    """
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    cmd = [rsync_info.path, "-avz"]

    if rsync_info.supports_checksum:
        cmd.append("--checksum")

    if backup:
        cmd.extend(["--backup", f"--suffix=.{timestamp}.bak"])

    if dry_run:
        cmd.extend(["--dry-run", "--itemize-changes"])

    cmd.extend(["-e", "ssh"])

    for f in local_files:
        cmd.append(str(f))

    cmd.append(f"{ssh_host}:{remote_dir}/")
    return cmd


def build_rsync_file_cmd(
    *,
    rsync_info: RsyncInfo,
    local_file: Path,
    ssh_host: str,
    remote_path: str,
    dry_run: bool = True,
    backup: bool = True,
) -> list[str]:
    """Build an rsync command for pushing a single file to a specific remote path."""
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    cmd = [rsync_info.path, "-avz"]

    if rsync_info.supports_checksum:
        cmd.append("--checksum")

    if backup:
        cmd.extend(["--backup", f"--suffix=.{timestamp}.bak"])

    if dry_run:
        cmd.extend(["--dry-run", "--itemize-changes"])

    cmd.extend(["-e", "ssh"])
    cmd.append(str(local_file))
    cmd.append(f"{ssh_host}:{remote_path}")
    return cmd


def run_rsync(cmd: list[str], *, quiet: bool = False) -> subprocess.CompletedProcess:
    """Execute an rsync command and return the result.

    Streams output to stdout unless quiet.
    """
    if not quiet:
        result = subprocess.run(cmd, text=True)
    else:
        result = subprocess.run(cmd, capture_output=True, text=True)
    return result
