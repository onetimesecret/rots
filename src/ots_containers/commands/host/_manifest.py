# src/ots_containers/commands/host/_manifest.py

"""Manifest parsing for config push operations.

A manifest.conf maps local filenames to remote target paths. Format:

    # Comments start with #
    config.yaml     /etc/onetimesecret/config.yaml
    auth.yaml       /etc/onetimesecret/auth.yaml
    .env            /etc/default/onetimesecret

Fields are whitespace-separated: <local_name> <remote_path>.
Lines with only whitespace or starting with # are skipped.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ots_containers.config import CONFIG_FILES, DEFAULT_ENV_FILE


@dataclass(frozen=True)
class ManifestEntry:
    """A single mapping from a local file to a remote path."""

    local_name: str
    remote_path: Path


def parse_manifest(manifest_path: Path) -> list[ManifestEntry]:
    """Parse a manifest.conf file into a list of ManifestEntry.

    Raises:
        FileNotFoundError: If manifest_path does not exist.
        ValueError: If a line has fewer than 2 fields.
    """
    entries: list[ManifestEntry] = []
    text = manifest_path.read_text()
    for lineno, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 2:
            raise ValueError(
                f"{manifest_path}:{lineno}: expected '<local_name> <remote_path>', "
                f"got: {raw_line!r}"
            )
        entries.append(ManifestEntry(local_name=parts[0], remote_path=Path(parts[1])))
    return entries


def default_manifest() -> list[ManifestEntry]:
    """Return the convention-based manifest when no manifest.conf exists.

    Maps CONFIG_FILES to /etc/onetimesecret/<name>,
    plus .env -> /etc/default/onetimesecret and
    Caddyfile.template -> /etc/onetimesecret/Caddyfile.template.
    """
    entries: list[ManifestEntry] = []
    for fname in CONFIG_FILES:
        entries.append(
            ManifestEntry(local_name=fname, remote_path=Path(f"/etc/onetimesecret/{fname}"))
        )
    entries.append(ManifestEntry(local_name=".env", remote_path=DEFAULT_ENV_FILE))
    entries.append(
        ManifestEntry(
            local_name="Caddyfile.template",
            remote_path=Path("/etc/onetimesecret/Caddyfile.template"),
        )
    )
    return entries


def resolve_manifest(config_dir: Path) -> list[ManifestEntry]:
    """Load manifest.conf from config_dir if it exists, else use defaults."""
    manifest_path = config_dir / "manifest.conf"
    if manifest_path.exists():
        return parse_manifest(manifest_path)
    return default_manifest()
