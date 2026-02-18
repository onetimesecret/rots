# src/ots_containers/config.py

import os
from dataclasses import dataclass, field
from pathlib import Path

# Default image registry (public)
DEFAULT_IMAGE = "ghcr.io/onetimesecret/onetimesecret"
DEFAULT_TAG = "current"

# Config files that ship as defaults in the container image (etc/defaults/*.defaults.yaml).
# Only files present on the host override the container's built-in defaults.
CONFIG_FILES: tuple[str, ...] = (
    "config.yaml",
    "auth.yaml",
    "logging.yaml",
    "billing.yaml",
)


@dataclass
class Config:
    """FHS-compliant configuration paths.

    Directory layout:
        /etc/onetimesecret/          - YAML config overrides (per-file mount, optional)
        /etc/default/onetimesecret   - Infrastructure env vars (REDIS_URL, etc.)
        /var/lib/onetimesecret/      - Variable runtime data (deployments.db)
        /etc/containers/systemd/     - Quadlet unit files

    Secrets (via podman secret):
        ots_hmac_secret              - HMAC_SECRET env var
        ots_secret                   - SECRET env var
        ots_session_secret           - SESSION_SECRET env var
    """

    config_dir: Path = Path("/etc/onetimesecret")
    var_dir: Path = Path("/var/lib/onetimesecret")
    image: str = field(default_factory=lambda: os.environ.get("IMAGE", DEFAULT_IMAGE))
    tag: str = field(default_factory=lambda: os.environ.get("TAG", DEFAULT_TAG))
    web_template_path: Path = Path("/etc/containers/systemd/onetime-web@.container")
    worker_template_path: Path = Path("/etc/containers/systemd/onetime-worker@.container")
    scheduler_template_path: Path = Path("/etc/containers/systemd/onetime-scheduler@.container")

    # Private registry configuration (optional, set via OTS_REGISTRY env var)
    registry: str | None = field(default_factory=lambda: os.environ.get("OTS_REGISTRY"))
    _registry_auth_file: Path | None = field(default=None, repr=False)

    # Proxy (Caddy) configuration - uses HOST environment, not container .env
    proxy_template: Path = Path("/etc/onetimesecret/Caddyfile.template")
    proxy_config: Path = Path("/etc/caddy/Caddyfile")

    @property
    def image_with_tag(self) -> str:
        return f"{self.image}:{self.tag}"

    @property
    def registry_auth_file(self) -> Path:
        """Container registry auth file path.

        Resolution order:
        1. Explicit override via _registry_auth_file
        2. REGISTRY_AUTH_FILE env var
        3. XDG_RUNTIME_DIR/containers/auth.json (if exists)
        4. ~/.config/containers/auth.json (non-root user, macOS)
        5. /etc/containers/auth.json (root on Linux only)
        """
        import sys

        # Explicit override
        if self._registry_auth_file:
            return self._registry_auth_file

        # Environment variable
        env_path = os.environ.get("REGISTRY_AUTH_FILE")
        if env_path:
            return Path(env_path)

        # XDG_RUNTIME_DIR (podman's default on Linux with user session)
        xdg_runtime = os.environ.get("XDG_RUNTIME_DIR")
        if xdg_runtime:
            runtime_auth = Path(xdg_runtime) / "containers" / "auth.json"
            if runtime_auth.exists():
                return runtime_auth

        # User config - preferred for non-root users and macOS
        user_auth = Path.home() / ".config" / "containers" / "auth.json"
        is_root = os.geteuid() == 0 if hasattr(os, "geteuid") else False

        if sys.platform == "darwin" or not is_root:
            # Non-root users should use their own config dir
            return user_auth

        # System path (root on Linux only)
        return Path("/etc/containers/auth.json")

    @property
    def private_image(self) -> str | None:
        """Image path for private registry (requires OTS_REGISTRY env var)."""
        if not self.registry:
            return None
        image_basename = self.image.split("/")[-1]
        return f"{self.registry}/{image_basename}"

    @property
    def private_image_with_tag(self) -> str | None:
        """Full image reference for private registry."""
        if not self.private_image:
            return None
        return f"{self.private_image}:{self.tag}"

    @property
    def config_yaml(self) -> Path:
        """Application configuration file."""
        return self.config_dir / "config.yaml"

    @property
    def db_path(self) -> Path:
        """SQLite database for deployment tracking.

        Uses system path (/var/lib/onetimesecret/) on Linux production,
        falls back to user space (~/.local/share/ots-containers/) when
        system path is not writable (macOS, non-root user).
        """
        system_path = self.var_dir / "deployments.db"

        # Check if system path is usable (exists and writable, or parent is writable)
        if system_path.exists() and os.access(system_path, os.W_OK):
            return system_path
        if self.var_dir.exists() and os.access(self.var_dir, os.W_OK):
            return system_path

        # Fall back to XDG_DATA_HOME (~/.local/share/ots-containers/)
        xdg_data = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
        user_dir = xdg_data / "ots-containers"
        return user_dir / "deployments.db"

    @property
    def existing_config_files(self) -> list[Path]:
        """Host config files that exist and should be mounted into the container.

        Only files present on the host override the container's built-in defaults.
        """
        return [self.config_dir / f for f in CONFIG_FILES if (self.config_dir / f).exists()]

    @property
    def has_custom_config(self) -> bool:
        """Whether any host config files exist to mount."""
        return len(self.existing_config_files) > 0

    def validate(self) -> None:
        """Validate configuration. Config files are optional (container has defaults)."""
        pass

    def resolve_image_tag(self) -> tuple[str, str]:
        """Resolve image and tag, checking database aliases if tag is an alias.

        If tag is 'current' or 'rollback', looks up the actual tag from the
        deployment database. Falls back to the literal tag if no alias found.

        Returns (image, tag) tuple.
        """
        from . import db

        # Check if tag is an alias
        if self.tag.lower() in ("current", "rollback"):
            alias = db.get_alias(self.db_path, self.tag)
            if alias:
                return (alias.image, alias.tag)

        return (self.image, self.tag)

    @property
    def resolved_image_with_tag(self) -> str:
        """Image with tag, resolving aliases like 'current' and 'rollback'."""
        image, tag = self.resolve_image_tag()
        return f"{image}:{tag}"
