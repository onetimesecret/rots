# src/ots_containers/commands/host/app.py

"""Host configuration management commands.

Push, diff, and pull config files between a local config directory
(e.g. ops-jurisdictions/<env>/) and a remote OTS host via rsync/SSH.

Uses a manifest.conf to map local filenames to remote target paths.
When no manifest.conf exists, uses convention-based defaults matching
CONFIG_FILES in config.py.

Both config_dir and ssh_host can be resolved automatically from a
.otsinfra.env file (walk-up discovery) when not supplied explicitly:
  - config_dir: derived from OTS_TAG -> config-v{major}.{minor}/
  - ssh_host: from global --host flag, OTS_HOST env var, or .otsinfra.env
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Annotated

import cyclopts

from ..common import Quiet
from ._manifest import ManifestEntry, resolve_manifest
from ._rsync import build_rsync_file_cmd, detect_rsync, run_rsync, warn_if_macos_rsync

app = cyclopts.App(
    name="host",
    help="Push, diff, and pull config files to/from remote OTS hosts.",
)

# Host commands default to dry-run (safe). --apply opts in to real writes.
Apply = Annotated[
    bool,
    cyclopts.Parameter(
        name=["--apply"],
        help="Apply changes (default is dry-run)",
        negative=[],
    ),
]

ConfigDir = Annotated[
    Path | None,
    cyclopts.Parameter(
        help=(
            "Local config directory containing files to push. "
            "Resolved from OTS_TAG in .otsinfra.env when omitted."
        ),
    ),
]


def _resolve_ssh_host() -> str:
    """Resolve the SSH host from context (global --host) or .otsinfra.env.

    Uses the same priority chain as ots_shared.ssh.env.resolve_host():
      1. Global --host flag (via context.host_var)
      2. OTS_HOST environment variable
      3. OTS_HOST from .otsinfra.env (walk-up discovery)

    Raises SystemExit if no host can be resolved.
    """
    from ots_shared.ssh.env import resolve_host

    from ... import context

    host = resolve_host(host_flag=context.host_var.get(None))
    if host is None:
        print(
            "Error: no target host. Use --host, set OTS_HOST, or add OTS_HOST to .otsinfra.env",
            file=sys.stderr,
        )
        raise SystemExit(1)
    return host


def _get_executor():
    """Return an Executor for the resolved host.

    Uses Config.get_executor() with the host from context.host_var,
    routing through resolve_host() for .otsinfra.env support.
    """
    from ... import context
    from ...config import Config

    cfg = Config()
    return cfg.get_executor(host=context.host_var.get(None))


def _resolve_config_dir(config_dir: Path | None) -> Path:
    """Resolve config directory: explicit arg, or from .otsinfra.env OTS_TAG.

    Raises SystemExit if no config directory can be resolved.
    """
    if config_dir is not None:
        resolved = config_dir.resolve()
        if not resolved.is_dir():
            print(f"Error: {resolved} is not a directory", file=sys.stderr)
            raise SystemExit(1)
        return resolved

    from ots_shared.ssh.env import resolve_config_dir as _resolve_from_env

    resolved = _resolve_from_env()
    if resolved is None:
        print(
            "Error: no config directory. Pass a path or add OTS_TAG to .otsinfra.env "
            "(e.g. OTS_TAG=v0.24 -> config-v0.24/)",
            file=sys.stderr,
        )
        raise SystemExit(1)
    return resolved


def _resolve_files(
    config_dir: Path,
    manifest: list[ManifestEntry],
) -> list[tuple[Path, ManifestEntry]]:
    """Resolve local files against the manifest.

    Returns (local_path, entry) pairs for files that exist locally.
    Prints warnings for missing files.
    """
    found: list[tuple[Path, ManifestEntry]] = []
    for entry in manifest:
        local_path = config_dir / entry.local_name
        if local_path.exists():
            found.append((local_path, entry))
        else:
            print(f"  [skip] {entry.local_name} (not in {config_dir})", file=sys.stderr)
    return found


@app.command
def push(
    config_dir: ConfigDir = None,
    apply: Apply = False,
    quiet: Quiet = False,
) -> None:
    """Push local config files to a remote host via rsync.

    Reads manifest.conf from the config directory to determine which files
    to push and where they go on the remote host. If no manifest.conf
    exists, uses convention-based defaults (config.yaml, auth.yaml, etc.
    to /etc/onetimesecret/, .env to /etc/default/onetimesecret).

    Both the config directory and target host can be resolved from
    .otsinfra.env when you run this from within a jurisdiction directory.

    Dry-run is the default. Pass --apply to write changes.

    rsync creates timestamped backups of overwritten files on the remote.

    Examples:
        cd ops-jurisdictions/ca/ && ots host push
        ots host push ops-jurisdictions/ca/config-v0.24/
        ots --host ca-tor-web-01 host push
    """
    dry_run = not apply
    ssh_host = _resolve_ssh_host()
    resolved_dir = _resolve_config_dir(config_dir)

    rsync_info = detect_rsync()
    warn_if_macos_rsync(rsync_info)

    manifest = resolve_manifest(resolved_dir)
    files = _resolve_files(resolved_dir, manifest)

    if not files:
        print("No config files found to push.", file=sys.stderr)
        raise SystemExit(1)

    if not quiet:
        mode = "DRY RUN" if dry_run else "PUSH"
        print(f"[{mode}] {resolved_dir} -> {ssh_host}")
        print(f"  rsync: {rsync_info.path} ({rsync_info.version})")
        print(f"  files: {len(files)}")
        print()

    any_failed = False
    for local_path, entry in files:
        cmd = build_rsync_file_cmd(
            rsync_info=rsync_info,
            local_file=local_path,
            ssh_host=ssh_host,
            remote_path=str(entry.remote_path),
            dry_run=dry_run,
            backup=True,
        )
        if not quiet:
            print(f"  {entry.local_name} -> {entry.remote_path}")

        result = run_rsync(cmd, quiet=quiet)
        if result.returncode != 0:
            any_failed = True
            print(f"  [FAIL] {entry.local_name} (exit {result.returncode})", file=sys.stderr)

    if not quiet:
        print()
        if dry_run:
            print("Dry-run complete. Pass --apply to push changes.")
        elif any_failed:
            print("Some files failed to push. Check errors above.")
        else:
            print("Push complete.")

    if any_failed:
        raise SystemExit(1)


@app.command
def diff(
    config_dir: ConfigDir = None,
    quiet: Quiet = False,
) -> None:
    """Show differences between local config and remote host.

    For each file in the manifest, fetches the remote version via
    'ssh <host> cat <path>' and runs a unified diff against the local file.
    Files that don't exist on the remote are shown as entirely new.

    Examples:
        cd ops-jurisdictions/ca/ && ots host diff
        ots --host ca-tor-web-01 host diff ops-jurisdictions/ca/config-v0.24/
    """
    ssh_host = _resolve_ssh_host()
    ex = _get_executor()
    resolved_dir = _resolve_config_dir(config_dir)

    manifest = resolve_manifest(resolved_dir)
    files = _resolve_files(resolved_dir, manifest)

    if not files:
        print("No config files found to diff.", file=sys.stderr)
        raise SystemExit(1)

    if not quiet:
        print(f"Comparing {resolved_dir} <-> {ssh_host}")
        print()

    any_diff = False
    for local_path, entry in files:
        local_content = local_path.read_text()

        # Fetch remote file content via executor
        result = ex.run(["cat", str(entry.remote_path)], timeout=30)

        if result.returncode != 0:
            # File doesn't exist on remote — show as entirely new
            print(f"--- {ssh_host}:{entry.remote_path} (not found)")
            print(f"+++ {local_path}")
            for line in local_content.splitlines():
                print(f"+{line}")
            print()
            any_diff = True
            continue

        remote_content = result.stdout

        if local_content == remote_content:
            if not quiet:
                print(f"  [identical] {entry.local_name}")
            continue

        # Unified diff
        import difflib

        diff_lines = difflib.unified_diff(
            remote_content.splitlines(keepends=True),
            local_content.splitlines(keepends=True),
            fromfile=f"{ssh_host}:{entry.remote_path}",
            tofile=str(local_path),
        )
        diff_text = "".join(diff_lines)
        if diff_text:
            any_diff = True
            print(diff_text)

    if not any_diff and not quiet:
        print("No differences found.")


@app.command
def pull(
    config_dir: ConfigDir = None,
    apply: Apply = False,
    quiet: Quiet = False,
) -> None:
    """Pull config files from a remote host to a local directory.

    For each file in the manifest, downloads the remote version via
    'ssh <host> cat <path>' and writes it to the local config directory.

    Dry-run is the default. Pass --apply to write changes.

    Examples:
        cd ops-jurisdictions/ca/ && ots host pull
        ots --host ca-tor-web-01 host pull --apply
    """
    dry_run = not apply
    ssh_host = _resolve_ssh_host()
    ex = _get_executor()
    resolved_dir = _resolve_config_dir(config_dir)

    manifest = resolve_manifest(resolved_dir)

    if not quiet:
        mode = "DRY RUN" if dry_run else "PULL"
        print(f"[{mode}] {ssh_host} -> {resolved_dir}")
        print()

    any_failed = False
    pulled = 0
    for entry in manifest:
        local_path = resolved_dir / entry.local_name

        # Fetch remote file via executor
        result = ex.run(["cat", str(entry.remote_path)], timeout=30)

        if result.returncode != 0:
            if not quiet:
                print(f"  [skip] {entry.remote_path} (not on remote)")
            continue

        remote_content = result.stdout

        if local_path.exists() and local_path.read_text() == remote_content:
            if not quiet:
                print(f"  [identical] {entry.local_name}")
            continue

        if dry_run:
            action = "would overwrite" if local_path.exists() else "would create"
            print(f"  [{action}] {entry.local_name} <- {entry.remote_path}")
        else:
            local_path.write_text(remote_content)
            action = "updated" if local_path.exists() else "created"
            if not quiet:
                print(f"  [{action}] {entry.local_name} <- {entry.remote_path}")
        pulled += 1

    if not quiet:
        print()
        if dry_run:
            print(f"Dry-run: {pulled} file(s) would be written. Pass --apply to apply.")
        else:
            print(f"Pulled {pulled} file(s).")

    if any_failed:
        raise SystemExit(1)


@app.command
def status(
    config_dir: ConfigDir = None,
) -> None:
    """Show which config files exist locally and on the remote.

    Quick overview of file presence without fetching content.

    Examples:
        cd ops-jurisdictions/ca/ && ots host status
        ots --host ca-tor-web-01 host status
    """
    ssh_host = _resolve_ssh_host()
    ex = _get_executor()
    resolved_dir = _resolve_config_dir(config_dir)

    manifest = resolve_manifest(resolved_dir)

    print(f"Config status: {resolved_dir} <-> {ssh_host}")
    print()
    print(f"  {'File':<30} {'Local':<10} {'Remote':<10}")
    print(f"  {'-' * 30} {'-' * 10} {'-' * 10}")

    for entry in manifest:
        local_path = resolved_dir / entry.local_name
        local_ok = local_path.exists()

        # Check remote existence via executor
        result = ex.run(["test", "-f", str(entry.remote_path)], timeout=10)
        remote_ok = result.returncode == 0

        local_status = "yes" if local_ok else "no"
        remote_status = "yes" if remote_ok else "no"
        print(f"  {entry.local_name:<30} {local_status:<10} {remote_status:<10}")


@app.command(name="init")
def init_env(
    directory: Annotated[
        Path | None,
        cyclopts.Parameter(
            help="Directory to create .otsinfra.env in (default: current directory)",
        ),
    ] = None,
    host: Annotated[
        str,
        cyclopts.Parameter(
            name=["--host"],
            help="SSH host alias for OTS_HOST",
        ),
    ] = "",
    tag: Annotated[
        str,
        cyclopts.Parameter(
            name=["--tag"],
            help="Release tag for OTS_TAG (e.g. v0.24)",
        ),
    ] = "",
    force: Annotated[
        bool,
        cyclopts.Parameter(
            name=["--force"],
            help="Overwrite existing .otsinfra.env",
            negative=[],
        ),
    ] = False,
) -> None:
    """Create a .otsinfra.env file for host targeting.

    Scaffolds a .otsinfra.env template so that push/diff/pull/status
    commands can auto-resolve the target host and config directory
    without --host flags.

    Examples:
        cd ops-jurisdictions/ca/ && ots host init --host ca-tor-web-01 --tag v0.24
        ots host init /path/to/jurisdiction --host eu-hel-web-01
    """
    from ots_shared.ssh.env import ENV_FILENAME, generate_env_template

    target_dir = (directory or Path.cwd()).resolve()
    if not target_dir.is_dir():
        print(f"Error: {target_dir} is not a directory", file=sys.stderr)
        raise SystemExit(1)

    env_path = target_dir / ENV_FILENAME
    if env_path.exists() and not force:
        print(f"Error: {env_path} already exists. Use --force to overwrite.", file=sys.stderr)
        raise SystemExit(1)

    content = generate_env_template(host=host, tag=tag)
    env_path.write_text(content)
    print(f"Created {env_path}")
    if not host:
        print("  Hint: edit OTS_HOST to set the target SSH host alias")
    if not tag:
        print("  Hint: edit OTS_TAG to set the release version (e.g. v0.24)")
