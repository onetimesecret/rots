# src/rots/commands/service/packages.py

"""Service package registry for systemd template services.

Defines ServicePackage and SecretConfig dataclasses that describe how to
manage different systemd template services (valkey-server@, redis-server@, etc.).
"""

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class SecretConfig:
    """Configuration for secret handling in service configs.

    Supports three tiers:
    - inline: Secrets in main config file (mode 0600)
    - separate: Main .conf + separate .secrets file (default)
    - credential: systemd LoadCredential= directive
    """

    # Keys that contain secrets (e.g., ["requirepass", "masterauth"])
    secret_keys: tuple[str, ...] = field(default_factory=tuple)

    # Pattern for secrets file, e.g., "{instance}.secrets"
    # None means inline secrets in main config
    secrets_file_pattern: str | None = "{instance}.secrets"

    # Include directive to add to main config, e.g., "include {secrets_path}"
    # None if using inline or credential strategy
    include_directive: str | None = "include {secrets_path}"

    # File mode for config when it contains secrets (inline mode)
    config_with_secrets_mode: int = 0o600

    # File mode for separate secrets file
    secrets_file_mode: int = 0o600

    # Whether secrets file should be owned by service user
    secrets_owned_by_service: bool = True


@dataclass(frozen=True)
class ServicePackage:
    """Definition of a systemd template service package.

    Describes the paths, patterns, and behaviors for managing a specific
    package's template service (e.g., valkey-server@.service).
    """

    # Package name (e.g., "valkey", "redis")
    name: str

    # systemd template unit name without .service suffix (e.g., "valkey-server@")
    template: str

    # Base config directory (e.g., /etc/valkey)
    config_dir: Path

    # Base data directory (e.g., /var/lib/valkey)
    data_dir: Path

    # Pattern for instance config files
    # e.g., "{instance}.conf" -> /etc/redis/instances/6379.conf
    # or "valkey-{instance}.conf" -> /etc/valkey/valkey-6379.conf
    config_file_pattern: str = "{instance}.conf"

    # Whether to place instance configs in instances/ subdirectory
    # True: /etc/valkey/instances/6379.conf (custom convention)
    # False: /etc/valkey/valkey-6379.conf (Debian package convention)
    use_instances_subdir: bool = True

    # Default (non-template) service if any (e.g., "valkey-server.service")
    default_service: str | None = None

    # Path to default config file that ships with package
    # Used as reference/base for copy-on-write
    default_config: Path | None = None

    # Secret handling configuration
    secrets: SecretConfig | None = None

    # User the service runs as
    service_user: str | None = None

    # Group the service runs as
    service_group: str | None = None

    # Default port for the service
    default_port: int | None = None

    # Port config key in the config file (e.g., "port")
    port_config_key: str = "port"

    # Bind address config key (e.g., "bind")
    bind_config_key: str = "bind"

    # Config line format: "key value" or "key=value"
    config_format: str = "space"  # "space" or "equals"

    # Comment prefix in config files
    comment_prefix: str = "#"

    @property
    def instances_dir(self) -> Path:
        """Directory for instance-specific config files."""
        return self.config_dir / "instances"

    @property
    def template_unit(self) -> str:
        """Full template unit name with .service suffix."""
        return f"{self.template}.service"

    def instance_unit(self, instance: str) -> str:
        """Get full unit name for a specific instance."""
        return f"{self.template}{instance}.service"

    def config_file(self, instance: str) -> Path:
        """Get config file path for a specific instance."""
        config_name = self.config_file_pattern.format(instance=instance)
        if self.use_instances_subdir:
            return self.instances_dir / config_name
        return self.config_dir / config_name

    def secrets_file(self, instance: str) -> Path | None:
        """Get secrets file path for a specific instance, if using separate secrets."""
        if self.secrets and self.secrets.secrets_file_pattern:
            secrets_name = self.secrets.secrets_file_pattern.format(instance=instance)
            if self.use_instances_subdir:
                return self.instances_dir / secrets_name
            return self.config_dir / secrets_name
        return None

    def data_path(self, instance: str) -> Path:
        """Get data directory for a specific instance."""
        return self.data_dir / instance


# =============================================================================
# Package Registry
# =============================================================================

VALKEY_SECRETS = SecretConfig(
    secret_keys=("requirepass", "masterauth"),
    secrets_file_pattern="valkey-{instance}.secrets",
    include_directive="include {secrets_path}",
)

REDIS_SECRETS = SecretConfig(
    secret_keys=("requirepass", "masterauth"),
    secrets_file_pattern="{instance}.secrets",
    include_directive="include {secrets_path}",
)

VALKEY = ServicePackage(
    name="valkey",
    template="valkey-server@",
    config_dir=Path("/etc/valkey"),
    data_dir=Path("/var/lib/valkey"),
    config_file_pattern="valkey-{instance}.conf",
    use_instances_subdir=False,  # Debian convention: /etc/valkey/valkey-{port}.conf
    default_service="valkey-server.service",
    default_config=Path("/etc/valkey/valkey.conf"),
    secrets=VALKEY_SECRETS,
    service_user="valkey",
    service_group="valkey",
    default_port=6379,
    port_config_key="port",
    bind_config_key="bind",
    config_format="space",
)

REDIS = ServicePackage(
    name="redis",
    template="redis-server@",
    config_dir=Path("/etc/redis"),
    data_dir=Path("/var/lib/redis"),
    config_file_pattern="{instance}.conf",
    default_service="redis-server.service",
    default_config=Path("/etc/redis/redis.conf"),
    secrets=REDIS_SECRETS,
    service_user="redis",
    service_group="redis",
    default_port=6379,
    port_config_key="port",
    bind_config_key="bind",
    config_format="space",
)

# Registry of all known packages
PACKAGES: dict[str, ServicePackage] = {
    "valkey": VALKEY,
    "redis": REDIS,
}


def get_package(name: str) -> ServicePackage:
    """Get a service package by name.

    Args:
        name: Package name (e.g., "valkey", "redis")

    Returns:
        ServicePackage definition

    Raises:
        SystemExit: If the package name is not registered, with available names listed.
    """
    if name not in PACKAGES:
        available = ", ".join(sorted(PACKAGES.keys()))
        raise SystemExit(f"Unknown service package '{name}'. Available packages: {available}")
    return PACKAGES[name]


def list_packages() -> list[str]:
    """List all registered package names."""
    return sorted(PACKAGES.keys())
