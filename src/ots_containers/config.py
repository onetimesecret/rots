# src/ots_containers/config.py

from __future__ import annotations

import atexit
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ots_shared.ssh.executor import Executor

# Default image registry (public)
DEFAULT_IMAGE = "ghcr.io/onetimesecret/onetimesecret"

# Sentinel value for the default tag.  The leading '@' makes it an invalid
# OCI registry tag so it can never be confused with a real tag on the registry.
# When TAG env var is not set, the tool resolves the CURRENT DB alias.
# This prevents accidental pulls of a registry tag literally named "current".
DEFAULT_TAG = "@current"

# Default environment file path (infrastructure env vars for OTS containers)
DEFAULT_ENV_FILE = Path("/etc/default/onetimesecret")

# Config files that ship as defaults in the container image (etc/defaults/*.defaults.yaml).
# Only files present on the host override the container's built-in defaults.
CONFIG_FILES: tuple[str, ...] = (
    "config.yaml",
    "auth.yaml",
    "logging.yaml",
    "billing.yaml",
)


# Session-scoped SSH connection cache: hostname -> paramiko.SSHClient.
# Avoids creating a new connection per get_executor() call within one CLI
# invocation.  Connections are closed automatically at interpreter exit.
_ssh_cache: dict[str, object] = {}


def _close_ssh_cache() -> None:
    """Close all cached SSH connections. Registered via atexit."""
    for hostname, client in list(_ssh_cache.items()):
        try:
            client.close()  # type: ignore[union-attr]
        except Exception:
            pass
    _ssh_cache.clear()


atexit.register(_close_ssh_cache)


@dataclass
class Config:
    """FHS-compliant configuration paths.

    Directory layout:
        /etc/onetimesecret/          - YAML config overrides (per-file mount, optional)
        /etc/default/onetimesecret   - Infrastructure env vars (REDIS_URL, etc.)
        /var/lib/onetimesecret/      - Variable runtime data (deployments.db)
        /etc/containers/systemd/     - Quadlet unit files

    Legacy path migration (v0.22 -> FHS):
        /opt/onetimesecret/config/.env              -> /etc/default/onetimesecret
        /opt/onetimesecret/config/config.yaml       -> /etc/onetimesecret/config.yaml
        /opt/onetimesecret/config/auth.yaml         -> /etc/onetimesecret/auth.yaml
        /opt/onetimesecret/config/Caddyfile.template -> /etc/onetimesecret/Caddyfile.template

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

    # Valkey/Redis service dependency for quadlet ordering (optional).
    # Set OTS_VALKEY_SERVICE to the systemd unit name, e.g. "valkey-server@6379.service".
    # When set, the web quadlet adds After= and Wants= entries so the OTS container
    # only starts after the data store is ready on reboot.
    valkey_service: str | None = field(default_factory=lambda: os.environ.get("OTS_VALKEY_SERVICE"))

    # Container resource limits (optional, applied to all quadlet templates).
    # Set via environment variables before deploying.
    #
    # MEMORY_MAX: Hard memory limit — container is OOM-killed if exceeded.
    #             Systemd format: "512M", "1G", "2G".  Example: MEMORY_MAX=1G
    # CPU_QUOTA:  CPU time quota as a percentage.  Example: CPU_QUOTA=80%
    memory_max: str | None = field(default_factory=lambda: os.environ.get("MEMORY_MAX"))
    cpu_quota: str | None = field(default_factory=lambda: os.environ.get("CPU_QUOTA"))

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

    def get_registry_auth_file(self, executor: Executor | None = None) -> Path:
        """Remote-aware registry auth file resolution.

        When *executor* is None or a LocalExecutor, delegates to the
        :attr:`registry_auth_file` property (local filesystem probing).
        For remote executors, probes the remote filesystem and uses
        remote-appropriate defaults (always Linux, always root on production).
        """
        from ots_shared.ssh import is_remote

        if not is_remote(executor):
            return self.registry_auth_file

        # Explicit override
        if self._registry_auth_file:
            return self._registry_auth_file

        # Environment variable (local CLI environment, but still useful)
        env_path = os.environ.get("REGISTRY_AUTH_FILE")
        if env_path:
            return Path(env_path)

        # On remote production hosts: check XDG_RUNTIME_DIR pattern
        # then /etc/containers/auth.json (root on Linux)
        for candidate in [
            Path("/run/containers/0/auth.json"),  # rootless podman on systemd
            Path("/etc/containers/auth.json"),  # root on Linux
        ]:
            result = executor.run(["test", "-f", str(candidate)])  # type: ignore[union-attr]
            if result.ok:
                return candidate

        # Default to system path (root on Linux production)
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
    def system_db_path(self) -> Path:
        """System-level database path (always /var/lib/onetimesecret/deployments.db).

        Use this for remote execution where the target host is a production
        Linux server with writable /var/lib/onetimesecret/.  The local
        fallback logic in ``db_path`` probes the *local* filesystem, which
        gives the wrong answer when the CLI runs on macOS but deploys to a
        remote Linux host.
        """
        return self.var_dir / "deployments.db"

    @property
    def db_path(self) -> Path:
        """SQLite database for deployment tracking (local-only probing).

        Uses system path (/var/lib/onetimesecret/) on Linux production,
        falls back to user space (~/.local/share/ots-containers/) when
        system path is not writable (macOS, non-root user).

        .. note::

           This property probes the *local* filesystem.  For remote
           execution, use ``get_db_path(executor)`` or ``system_db_path``
           instead -- see ``get_db_path`` for details.
        """
        system_path = self.system_db_path

        # Check if system path is usable (exists and writable, or parent is writable)
        if system_path.exists() and os.access(system_path, os.W_OK):
            return system_path
        if self.var_dir.exists() and os.access(self.var_dir, os.W_OK):
            return system_path

        # Fall back to XDG_DATA_HOME (~/.local/share/ots-containers/)
        xdg_data = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
        user_dir = xdg_data / "ots-containers"
        return user_dir / "deployments.db"

    def get_db_path(self, executor: Executor | None = None) -> Path:
        """Return the correct database path for the given execution context.

        When *executor* is a remote (SSH) executor, returns ``system_db_path``
        unconditionally -- remote hosts are production Linux servers where
        /var/lib/onetimesecret/ is always writable.

        When *executor* is None or a LocalExecutor, falls back to the
        ``db_path`` property which probes the local filesystem.
        """
        if executor is not None:
            from ots_shared.ssh import LocalExecutor

            if not isinstance(executor, LocalExecutor):
                return self.system_db_path
        return self.db_path

    @property
    def existing_config_files(self) -> list[Path]:
        """Host config files that exist and should be mounted into the container.

        Only files present on the host override the container's built-in defaults.

        Note: This uses local Path.exists(). For remote-aware checks, use
        :meth:`get_existing_config_files` with an executor argument.
        """
        return [self.config_dir / f for f in CONFIG_FILES if (self.config_dir / f).exists()]

    def get_existing_config_files(self, executor: Executor | None = None) -> list[Path]:
        """Remote-aware check for host config files.

        When *executor* is None or a LocalExecutor, delegates to the
        :attr:`existing_config_files` property.  For remote executors,
        probes the remote filesystem via ``test -f``.
        """
        from ots_shared.ssh import is_remote

        if not is_remote(executor):
            return self.existing_config_files

        files: list[Path] = []
        for fname in CONFIG_FILES:
            fpath = self.config_dir / fname
            result = executor.run(["test", "-f", str(fpath)])  # type: ignore[union-attr]
            if result.ok:
                files.append(fpath)
        return files

    @property
    def has_custom_config(self) -> bool:
        """Whether any host config files exist to mount (local check only).

        For remote-aware checks, use ``len(cfg.get_existing_config_files(executor)) > 0``.
        """
        return len(self.existing_config_files) > 0

    def validate(self) -> None:
        """Validate configuration. Config files are optional (container has defaults)."""
        pass

    def get_executor(self, host: str | None = None):
        """Return an Executor for the given host, or LocalExecutor if None.

        Uses the host resolution chain: explicit host > OTS_HOST env >
        .otsinfra.env > None (local). When a host is resolved, connects
        via SSH and returns an SSHExecutor.  SSH connections are cached
        per hostname for the lifetime of the process so repeated calls
        within one CLI invocation reuse the same transport.
        """
        from ots_shared.ssh import LocalExecutor, SSHExecutor, resolve_host, ssh_connect

        resolved = resolve_host(host_flag=host)
        if resolved is None:
            return LocalExecutor()

        if resolved not in _ssh_cache:
            import sys

            print(f"Connecting to {resolved}...", file=sys.stderr, flush=True)
            try:
                _ssh_cache[resolved] = ssh_connect(resolved)
            except ImportError:
                raise SystemExit(
                    "paramiko is required for SSH connections. "
                    "Install it with: pip install ots-shared[ssh]"
                )
            except TimeoutError:
                raise SystemExit(
                    f"SSH to {resolved} failed: Connection timed out. "
                    "Check that the host is reachable and accepting SSH connections."
                )
            except OSError as exc:
                # Catch connection-refused, network-unreachable, etc.
                # paramiko.ssh_exception.NoValidConnectionsError is an OSError subclass.
                raise SystemExit(f"SSH to {resolved} failed: {exc}")
            except Exception as exc:
                # Catch paramiko.AuthenticationException, paramiko.SSHException,
                # and any other paramiko errors without importing paramiko at
                # module level — they all inherit from Exception.
                exc_name = type(exc).__name__
                if "Authentication" in exc_name:
                    raise SystemExit(
                        f"SSH to {resolved} failed: Authentication failed. "
                        "Check your SSH key and that the remote user is correct."
                    )
                if "SSHException" in exc_name or "SSH" in exc_name:
                    raise SystemExit(
                        f"SSH to {resolved} failed: {exc}. "
                        "Check your SSH configuration (~/.ssh/config) and known_hosts."
                    )
                raise
            print("Connected.", file=sys.stderr, flush=True)
        return SSHExecutor(_ssh_cache[resolved])

    def resolve_image_tag(self, *, executor: Executor | None = None) -> tuple[str, str]:
        """Resolve image and tag, checking database aliases if tag is an alias.

        Sentinel tags (@current, @rollback) and bare alias names (current,
        rollback) are looked up in the deployment database. Falls back to the
        literal tag if no alias is found.

        Args:
            executor: Optional executor for remote DB lookups. When None,
                reads from the local database.

        Returns (image, tag) tuple.
        """
        from . import db

        # Normalize: strip the leading '@' from sentinel values so the lookup
        # key is the plain alias name ("current", "rollback").
        tag_key = self.tag.lstrip("@")

        # Check if tag is an alias name (sentinel or bare)
        if tag_key.lower() in ("current", "rollback"):
            alias = db.get_alias(self.db_path, tag_key, executor=executor)
            if alias:
                return (alias.image, alias.tag)

        # Not an alias (or alias not set) — return as-is.
        # Callers that need a real tag (e.g. pull) should check for the
        # sentinel '@current' / '@rollback' and raise an appropriate error.
        return (self.image, self.tag)

    def resolved_image_with_tag(self, *, executor: Executor | None = None) -> str:
        """Image with tag, resolving aliases like 'current' and 'rollback'.

        Args:
            executor: Optional executor for remote DB lookups.
        """
        image, tag = self.resolve_image_tag(executor=executor)
        return f"{image}:{tag}"
