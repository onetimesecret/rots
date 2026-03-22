# src/rots/config.py

from __future__ import annotations

import atexit
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ots_shared.ssh.executor import Executor

logger = logging.getLogger(__name__)

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
    "Caddyfile.template",
    "puma.rb",
)

# --- Input validation patterns ---
# OCI tag: alphanumeric start, then alphanumeric/dot/hyphen/underscore, max 128 chars.
# Also allows the '@current'/'@rollback' sentinel prefix and digest references
# like '@sha256:abc123...' (algorithm:hex format).
TAG_RE = re.compile(r"^(?:@[a-zA-Z0-9]+:[a-fA-F0-9]+|@?[a-zA-Z0-9][a-zA-Z0-9._-]{0,127})$")

# OCI image reference (without tag): registry/path components.
# Each path component: alphanumeric, may contain dots/hyphens/underscores, separated by '/'.
# Colons allowed for registry port numbers (e.g. registry:5000/org/image).
# Minimal validation to reject shell metacharacters and whitespace.
# The negative lookahead rejects '..' path traversal sequences anywhere in the reference.
IMAGE_RE = re.compile(r"^(?!.*\.\.)[a-zA-Z0-9][a-zA-Z0-9._/:-]{0,254}$")

# Systemd resource limit: MemoryMax accepts e.g. "512M", "1G", "2G", "infinity",
# or bare byte counts.  CPUQuota accepts percentages like "80%", "150%".
# This pattern is intentionally strict to prevent newline/directive injection.
MEMORY_MAX_RE = re.compile(r"^(?:infinity|\d+[KMGT]?)$", re.IGNORECASE)
CPU_QUOTA_RE = re.compile(r"^\d{1,5}%$")

# Systemd unit name: alphanumeric, hyphens, dots, underscores, and '@' for template instances.
# Intentionally strict to prevent newline/directive injection into quadlet [Unit] section.
# Examples: "valkey-server@6379.service", "redis.service"
SYSTEMD_UNIT_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._@:-]{0,255}$")

# OCI registry URL: hostname with optional port and path components.
# Examples: "registry.example.com", "registry:5000", "registry.example.com/org"
# The negative lookahead rejects '..' path traversal sequences anywhere in the reference.
# Rejects shell metacharacters, whitespace, and newlines.
REGISTRY_RE = re.compile(r"^(?!.*\.\.)[a-zA-Z0-9][a-zA-Z0-9._/:-]{0,254}$")


def join_image_tag(image: str, tag: str) -> str:
    """Join image and tag using OCI reference syntax.

    Digest tags (starting with '@') use '@' separator;
    named tags use ':' separator.
    """
    if tag.startswith("@"):
        return f"{image}{tag}"
    return f"{image}:{tag}"


def _strip_registry_prefix(image: str) -> str:
    """Strip the registry hostname from an OCI image reference, keeping the path.

    OCI convention: the first ``/``-delimited component is a registry hostname
    when it contains a ``.`` or ``:``.  Otherwise the entire string is an
    image path (e.g. ``library/nginx``).

    Examples::

        _strip_registry_prefix("ghcr.io/onetimesecret/onetimesecret")
        # -> "onetimesecret/onetimesecret"

        _strip_registry_prefix("registry:5000/org/app")
        # -> "org/app"

        _strip_registry_prefix("onetimesecret/onetimesecret")
        # -> "onetimesecret/onetimesecret"  (no registry to strip)

        _strip_registry_prefix("myapp")
        # -> "myapp"
    """
    slash = image.find("/")
    if slash == -1:
        return image
    first_component = image[:slash]
    if "." in first_component or ":" in first_component:
        return image[slash + 1 :]
    return image


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


def parse_image_reference(ref: str) -> tuple[str, str | None]:
    """Parse an OCI image reference into (image, tag_or_none).

    Uses the last-colon-after-last-slash rule to handle registry ports
    correctly.  Digest references (``@sha256:...``) are recognised and
    returned with the ``@`` prefix so callers can distinguish them from
    plain tags.

    Examples::

        parse_image_reference("registry:5000/org/image:tag")
        # -> ("registry:5000/org/image", "tag")

        parse_image_reference("registry:5000/image")
        # -> ("registry:5000/image", None)

        parse_image_reference("image:tag")
        # -> ("image", "tag")

        parse_image_reference("image@sha256:abc123")
        # -> ("image", "@sha256:abc123")

    Args:
        ref: An OCI image reference string.

    Returns:
        A ``(image, tag_or_none)`` tuple.  *tag_or_none* is ``None``
        when the reference contains no tag or digest.
    """
    if not ref:
        raise ValueError("Image reference must not be empty")

    # Digest reference: split on the first '@'
    at_pos = ref.find("@")
    if at_pos != -1:
        image_part = ref[:at_pos]
        digest_part = ref[at_pos:]  # includes the '@'
        return (image_part, digest_part)

    # Last-colon-after-last-slash rule
    last_slash = ref.rfind("/")
    colon_pos = ref.rfind(":")

    if colon_pos != -1 and colon_pos > last_slash:
        # Colon is after the last slash (or there is no slash) -> it's a tag separator
        return (ref[:colon_pos], ref[colon_pos + 1 :])

    # No tag portion found
    return (ref, None)


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
        ots_hmac_secret              - AUTH_SECRET env var
        ots_secret                   - SECRET env var
        ots_session_secret           - SESSION_SECRET env var
    """

    config_dir: Path = Path("/etc/onetimesecret")
    var_dir: Path = Path("/var/lib/onetimesecret")
    image: str = field(default_factory=lambda: os.environ.get("IMAGE") or DEFAULT_IMAGE)
    tag: str = field(default_factory=lambda: os.environ.get("TAG") or DEFAULT_TAG)
    _image_explicit: bool = field(default=False, repr=False)
    web_template_path: Path = Path("/etc/containers/systemd/onetime-web@.container")
    worker_template_path: Path = Path("/etc/containers/systemd/onetime-worker@.container")
    scheduler_template_path: Path = Path("/etc/containers/systemd/onetime-scheduler@.container")
    image_template_path: Path = Path("/etc/containers/systemd/onetime.image")

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

    def __post_init__(self):
        """Validate fields on construction (and dataclasses.replace)."""
        if not self._image_explicit and os.environ.get("IMAGE"):
            self._image_explicit = True
        self.validate()

    @property
    def image_with_tag(self) -> str:
        return join_image_tag(self.image, self.tag)

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
        image_path = _strip_registry_prefix(self.image)
        return f"{self.registry}/{image_path}"

    @property
    def private_image_with_tag(self) -> str | None:
        """Full image reference for private registry."""
        if not self.private_image:
            return None
        return join_image_tag(self.private_image, self.tag)

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
        falls back to user space (~/.local/share/rots/) when
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

        # Fall back to XDG_DATA_HOME (~/.local/share/rots/)
        xdg_data = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
        user_dir = xdg_data / "rots"
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
        """Validate configuration values for safe use in OCI refs and templates.

        Checks:
        - tag matches OCI tag format (alphanumeric start, max 128 chars,
          no shell metacharacters). Sentinel tags (@current, @rollback) allowed.
        - image matches OCI image reference format (no shell metacharacters,
          no whitespace).
        - memory_max matches systemd MemoryMax format (e.g. 512M, 1G, infinity).
        - cpu_quota matches systemd CPUQuota format (e.g. 80%, 150%).
        - valkey_service matches systemd unit name format (no newlines or injection).
        - registry matches hostname:port/path pattern (no shell metacharacters).

        Config files are optional (container has defaults) and are not checked here.

        Raises:
            ValueError: If tag, image, resource limits, service names, or registry
                contain invalid characters.
        """
        if not TAG_RE.match(self.tag):
            raise ValueError(
                f"Invalid tag: {self.tag!r}. "
                "Tags must start with an alphanumeric character, contain only "
                "alphanumerics, dots, hyphens, and underscores, and be at most "
                "128 characters. Sentinel prefix '@' is allowed."
            )
        if not IMAGE_RE.match(self.image):
            raise ValueError(
                f"Invalid image: {self.image!r}. "
                "Image names must start with an alphanumeric character and contain "
                "only alphanumerics, dots, hyphens, underscores, and forward slashes."
            )
        # Check for embedded tag in image (e.g. IMAGE=ghcr.io/org/app:v1.0)
        last_slash = self.image.rfind("/")
        colon_after = self.image.rfind(":")
        if colon_after != -1 and colon_after > last_slash:
            raise ValueError(
                f"IMAGE should not include a tag (got '{self.image}'). "
                f"Set the tag separately via TAG env var or --tag flag."
            )
        if self.memory_max and not MEMORY_MAX_RE.match(self.memory_max):
            raise ValueError(
                f"Invalid MEMORY_MAX: {self.memory_max!r}. "
                "Must be a systemd memory value: a number with optional K/M/G/T "
                "suffix, or 'infinity'."
            )
        if self.cpu_quota and not CPU_QUOTA_RE.match(self.cpu_quota):
            raise ValueError(
                f"Invalid CPU_QUOTA: {self.cpu_quota!r}. Must be a percentage like '80%' or '150%'."
            )
        if self.valkey_service and not SYSTEMD_UNIT_RE.match(self.valkey_service):
            raise ValueError(
                f"Invalid OTS_VALKEY_SERVICE: {self.valkey_service!r}. "
                "Must be a valid systemd unit name (alphanumeric, hyphens, dots, "
                "underscores, and '@' for template instances)."
            )
        if self.registry and not REGISTRY_RE.match(self.registry):
            raise ValueError(
                f"Invalid OTS_REGISTRY: {self.registry!r}. "
                "Must be a valid registry URL (hostname with optional port and path, "
                "no shell metacharacters or whitespace)."
            )

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
            logger.info(f"Connecting to {resolved}...")
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
                # Catch paramiko-specific exceptions with isinstance() via
                # lazy import, falling back to re-raise if paramiko isn't
                # available (shouldn't happen since we just used it above).
                try:
                    from paramiko import AuthenticationException, SSHException
                except ImportError:
                    raise exc
                if isinstance(exc, AuthenticationException):
                    raise SystemExit(
                        f"SSH to {resolved} failed: Authentication failed. "
                        "Check your SSH key and that the remote user is correct."
                    )
                if isinstance(exc, SSHException):
                    raise SystemExit(
                        f"SSH to {resolved} failed: {exc}. "
                        "Check your SSH configuration (~/.ssh/config) and known_hosts."
                    )
                raise
            logger.info("Connected.")
        return SSHExecutor(_ssh_cache[resolved])

    def resolve_image_tag(self, *, executor: Executor | None = None) -> tuple[str, str]:
        """Resolve image and tag, checking database aliases if tag is an alias.

        Sentinel tags (@current, @rollback) and bare alias names (current,
        rollback) are looked up in the deployment database. Falls back to the
        literal tag if no alias is found.

        When an alias resolves, only the tag is taken from the database.
        The image is only taken from the alias when the user did not
        explicitly set IMAGE (i.e. IMAGE env var is not present).

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
                # Explicit image (env var or CLI positional) takes precedence
                # over the alias image.  The alias only supplies the image
                # when no explicit override was given.
                image = self.image if self._image_explicit else alias.image
                return (image, alias.tag)

        # Not an alias (or alias not set) — return as-is.
        # Callers that need a real tag (e.g. pull) should check for the
        # sentinel '@current' / '@rollback' and raise an appropriate error.
        return (self.image, self.tag)

    def resolved_image_with_tag(self, *, executor: Executor | None = None) -> str:
        """Operational image:tag string for podman pull/run.

        Resolves aliases like 'current' and 'rollback', and prepends
        the private registry prefix when ``OTS_REGISTRY`` is set.

        For the canonical (registry-free) pair, use :meth:`resolve_image_tag`.

        Args:
            executor: Optional executor for remote DB lookups.
        """
        image, tag = self.resolve_image_tag(executor=executor)
        if self.registry:
            image_path = _strip_registry_prefix(image)
            image = f"{self.registry}/{image_path}"
        return join_image_tag(image, tag)

    def podman_auth_args(self, *, executor: Executor | None = None) -> list[str]:
        """Return ``--authfile`` arguments when a private registry is configured.

        Returns an empty list when no registry is set, so callers can
        always do ``cmd.extend(cfg.podman_auth_args(...))``.
        """
        if not self.registry:
            return []
        return ["--authfile", str(self.get_registry_auth_file(executor=executor))]
