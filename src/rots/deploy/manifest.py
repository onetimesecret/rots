# src/rots/deploy/manifest.py
"""Deployment manifest schema and discovery.

Provides YAML-based deployment manifests for specifying target hosts
and deployment parameters. Follows the same walk-up discovery pattern
as .otsinfra-hosts.txt and .otsinfra.env.

Example .ots-deploy.yaml:
    hosts:
      - acme-prod-1
      - acme-prod-2
    port: 7043
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

MANIFEST_FILENAME = ".ots-deploy.yaml"

# Allowed keys in manifest (strict schema)
ALLOWED_KEYS = frozenset({"hosts", "port"})


class ManifestError(Exception):
    """Error loading or parsing a deployment manifest."""


@dataclass
class DeployManifest:
    """Deployment manifest specifying target hosts and parameters.

    Attributes:
        hosts: List of target host IDs.
        port: Instance port to redeploy (default: 7043).
        source: Path to the manifest file (for logging/debugging).
    """

    hosts: list[str]
    port: int = 7043
    source: Path | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        """Validate manifest after initialization."""
        if not self.hosts:
            raise ManifestError("Manifest must specify at least one host")
        for host in self.hosts:
            if not isinstance(host, str) or not host.strip():
                raise ManifestError(f"Invalid host entry: {host!r}")

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, source: Path | None = None) -> DeployManifest:
        """Create manifest from a dictionary.

        Args:
            data: Parsed YAML/dict data.
            source: Optional path for error messages.

        Returns:
            DeployManifest instance.

        Raises:
            ManifestError: If data is invalid or contains unknown keys.
        """
        if not isinstance(data, dict):
            raise ManifestError(f"Manifest must be a YAML mapping, got {type(data).__name__}")

        # Strict schema: reject unknown keys
        unknown = set(data.keys()) - ALLOWED_KEYS
        if unknown:
            raise ManifestError(f"Unknown keys in manifest: {sorted(unknown)}")

        # Extract hosts (required)
        hosts = data.get("hosts")
        if hosts is None:
            raise ManifestError("Manifest missing required 'hosts' key")
        if not isinstance(hosts, list):
            raise ManifestError(f"'hosts' must be a list, got {type(hosts).__name__}")

        # Extract port (optional)
        port = data.get("port", 7043)
        if not isinstance(port, int):
            raise ManifestError(f"'port' must be an integer, got {type(port).__name__}")

        return cls(hosts=hosts, port=port, source=source)

    @classmethod
    def from_file(cls, path: Path) -> DeployManifest:
        """Load manifest from a YAML file.

        Args:
            path: Path to the manifest file.

        Returns:
            DeployManifest instance.

        Raises:
            ManifestError: If file cannot be read or parsed.
            FileNotFoundError: If file does not exist.
        """
        # Import yaml lazily to avoid hard dependency
        try:
            import yaml
        except ImportError as e:
            raise ManifestError(
                "PyYAML is required to load manifests. Install with: pip install pyyaml"
            ) from e

        if not path.is_file():
            raise FileNotFoundError(f"Manifest file not found: {path}")

        try:
            text = path.read_text()
        except OSError as e:
            raise ManifestError(f"Cannot read manifest file: {e}") from e

        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError as e:
            raise ManifestError(f"Invalid YAML in manifest: {e}") from e

        return cls.from_dict(data, source=path)

    @classmethod
    def discover(cls, start: Path | None = None) -> DeployManifest | None:
        """Walk up from *start* looking for a .ots-deploy.yaml file.

        Stops at the first directory containing .git or at the user's home
        directory - whichever is reached first. Returns None if not found.

        This mirrors the walk-up discovery pattern used for .otsinfra.env
        and .otsinfra-hosts.txt.

        Args:
            start: Starting directory (defaults to cwd).

        Returns:
            DeployManifest if found and valid, None otherwise.

        Raises:
            ManifestError: If file is found but invalid.
        """
        path = find_manifest_file(start)
        if path is None:
            return None
        return cls.from_file(path)


def find_manifest_file(start: Path | None = None) -> Path | None:
    """Walk up from *start* looking for a .ots-deploy.yaml file.

    Stops at the first directory containing .git or at the user's home
    directory - whichever is reached first. Returns None if not found.

    This mirrors the walk-up discovery pattern used for .otsinfra.env
    and .otsinfra-hosts.txt.
    """
    current = (start or Path.cwd()).resolve()
    home = Path.home().resolve()

    while True:
        candidate = current / MANIFEST_FILENAME
        if candidate.is_file():
            logger.debug("Found manifest file: %s", candidate)
            return candidate

        # Stop at .git boundary
        if (current / ".git").exists():
            logger.debug("Reached .git boundary at %s, no manifest found", current)
            return None

        # Stop at home directory ceiling
        if current == home:
            logger.debug("Reached home directory, no manifest found")
            return None

        parent = current.parent
        # Filesystem root - stop
        if parent == current:
            return None

        current = parent
