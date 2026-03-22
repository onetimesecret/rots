# src/rots/commands/self.py

"""Self-management commands for rots.

Provides upgrade and version management for the rots CLI tool itself.
Designed for use with pipx installations.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from typing import Annotated

import cyclopts

from rots import __version__

app = cyclopts.App(
    name="self",
    help="Manage the rots installation itself.",
)


def _find_pipx() -> str | None:
    """Find the pipx binary path."""
    return shutil.which("pipx")


def _find_pip() -> str | None:
    """Find pip in the current Python environment."""
    return shutil.which("pip") or shutil.which("pip3")


def _get_installed_version() -> str:
    """Get the currently installed version."""
    return __version__


def _get_pypi_version(package: str = "rots") -> str | None:
    """Query PyPI for the latest version of a package.

    Returns None if the query fails.
    """
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "index", "versions", package],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            # Output format varies by pip version:
            # - "rots (0.24.0)" format
            # - "Available versions: 0.24.0, 0.23.0, ..." format
            output = result.stdout.strip()
            if "(" in output and ")" in output:
                return output.split("(")[1].split(")")[0]
            if "Available versions:" in output:
                # Extract first (latest) version from comma-separated list
                versions_part = output.split("Available versions:")[-1]
                first_version = versions_part.split(",")[0].strip()
                if first_version:
                    return first_version
        return None
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None


@app.default
def self_help():
    """Show self-management commands."""
    app.help_print([])


@app.command
def upgrade(
    version: Annotated[
        str | None,
        cyclopts.Parameter(
            name=["--version", "-v"],
            help="Specific version to install (default: latest)",
        ),
    ] = None,
    force: Annotated[
        bool,
        cyclopts.Parameter(
            name=["--force", "-f"],
            help="Force reinstall even if already at target version",
        ),
    ] = False,
    dry_run: Annotated[
        bool,
        cyclopts.Parameter(
            name=["--dry-run", "-n"],
            help="Show what would be done without executing",
        ),
    ] = False,
):
    """Upgrade rots to the latest version (or a specific version).

    Requires pipx. If rots was installed with pip, migrate first:

        pip uninstall rots
        pipx install rots

    Examples:
        rots self upgrade              # upgrade to latest
        rots self upgrade -v 0.24.0    # upgrade to specific version
        rots self upgrade --dry-run    # show what would happen
    """
    pipx = _find_pipx()
    current = _get_installed_version()

    if not pipx:
        print("pipx not found.")
        print()
        print("rots self upgrade requires pipx. To migrate from pip:")
        print()
        print("  pip uninstall rots")
        print("  pip install pipx")
        print("  pipx install rots")
        print()
        print("See: https://pipx.pypa.io/")
        raise SystemExit(1)

    # Build the command
    if version:
        package_spec = f"rots=={version}"
        target = version
    else:
        package_spec = "rots"
        target = "latest"

    # Check if upgrade is needed (unless forcing)
    if not force and version and version == current:
        print(f"rots is already at version {current}")
        return

    print(f"Current version: {current}")
    print(f"Target version:  {target}")

    if dry_run:
        print()
        print("Would run:")
        if version:
            print(f"  pipx install --force {package_spec}")
        else:
            print(f"  pipx upgrade {package_spec}")
        return

    print()

    # Execute upgrade
    try:
        if version:
            # pipx upgrade doesn't support pinning, use install --force
            cmd = [pipx, "install", "--force", package_spec]
        else:
            cmd = [pipx, "upgrade", package_spec]

        print(f"Running: {' '.join(cmd)}")
        result = subprocess.run(cmd, check=False)

        if result.returncode == 0:
            print()
            print("Upgrade complete. Run 'rots version' to verify.")
        else:
            print()
            print(f"Upgrade failed with exit code {result.returncode}")
            raise SystemExit(result.returncode)

    except FileNotFoundError:
        print(f"Failed to execute pipx at: {pipx}")
        raise SystemExit(1)


@app.command
def check():
    """Check if a newer version of rots is available."""
    current = _get_installed_version()
    print(f"Installed: {current}")

    latest = _get_pypi_version("rots")
    if latest:
        print(f"Latest:    {latest}")
        if latest != current:
            print()
            print("To upgrade: rots self upgrade")
    else:
        print("Latest:    (unable to query PyPI)")
