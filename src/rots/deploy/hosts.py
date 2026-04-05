# src/rots/deploy/hosts.py
"""Host discovery and resolution for fleet deployments.

Provides walk-up discovery for .otsinfra-hosts.txt files, following the
same pattern as .otsinfra.env discovery in ots_shared.ssh.env.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

HOSTS_FILENAME = ".otsinfra-hosts.txt"


def find_hosts_file(start: Path | None = None) -> Path | None:
    """Walk up from *start* looking for a .otsinfra-hosts.txt file.

    Stops at the first directory containing .git or at the user's home
    directory — whichever is reached first. Returns None if not found.

    This mirrors the walk-up discovery pattern used for .otsinfra.env.
    """
    current = (start or Path.cwd()).resolve()
    home = Path.home().resolve()

    while True:
        candidate = current / HOSTS_FILENAME
        if candidate.is_file():
            logger.debug("Found hosts file: %s", candidate)
            return candidate

        # Stop at .git boundary
        if (current / ".git").exists():
            logger.debug("Reached .git boundary at %s, no hosts file found", current)
            return None

        # Stop at home directory ceiling
        if current == home:
            logger.debug("Reached home directory, no hosts file found")
            return None

        parent = current.parent
        # Filesystem root — stop
        if parent == current:
            return None

        current = parent


def load_hosts_file(path: Path) -> list[str]:
    """Load host IDs from a file.

    Format: One host per line. Blank lines and lines starting with # are
    ignored. Whitespace is stripped.

    Args:
        path: Path to the hosts file.

    Returns:
        List of host IDs in file order.
    """
    hosts: list[str] = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        hosts.append(line)
    return hosts


def resolve_hosts(
    hosts: tuple[str, ...] | list[str] = (),
    *,
    hosts_file: Path | None = None,
) -> list[str]:
    """Resolve host IDs from various sources.

    Resolution order:
    1. Explicit hosts from positional argument
    2. --hosts-file if provided
    3. Walk-up discovery for .otsinfra-hosts.txt

    Deduplicates while preserving order.

    Args:
        hosts: Explicit host IDs from CLI.
        hosts_file: Explicit path to hosts file.

    Returns:
        List of unique host IDs.

    Raises:
        ValueError: If no hosts could be resolved from any source.
    """
    result: list[str] = []
    seen: set[str] = set()

    def add_hosts(host_list: list[str], source: str) -> None:
        for h in host_list:
            if h not in seen:
                seen.add(h)
                result.append(h)
                logger.debug("Added host %r from %s", h, source)

    # 1. Explicit hosts from CLI
    if hosts:
        add_hosts(list(hosts), "CLI arguments")

    # 2. Explicit hosts file
    if hosts_file is not None:
        if not hosts_file.is_file():
            raise ValueError(f"Hosts file not found: {hosts_file}")
        add_hosts(load_hosts_file(hosts_file), f"--hosts-file {hosts_file}")

    # 3. Walk-up discovery (only if no explicit sources provided results)
    if not result:
        discovered = find_hosts_file()
        if discovered:
            add_hosts(load_hosts_file(discovered), f"discovered {discovered}")

    if not result:
        raise ValueError(
            "No hosts specified. Provide hosts as arguments, "
            "use --hosts-file, or create a .otsinfra-hosts.txt file."
        )

    return result
