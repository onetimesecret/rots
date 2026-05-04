# src/rots/quadlet.py

"""
Quadlet template generation for OneTimeSecret containers.

The quadlet template is a systemd unit file that defines how to run
the container. Environment variables are loaded from a drop-in directory
(/etc/default/onetimesecret.d/*.conf) where lexical order determines
precedence (00-baseline.conf, 50-host.conf, etc.).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from ots_shared.ssh import is_remote as _is_remote

from . import systemd
from .config import CONFIG_FILES, Config, join_image_tag

if TYPE_CHECKING:
    from ots_shared.ssh import Executor

logger = logging.getLogger(__name__)


# The quadlet templates use a drop-in directory pattern for runtime:
#   EnvironmentFile=/etc/default/onetimesecret.d/*.conf
# To simplify back to a single file, change the glob to:
#   EnvironmentFile=/etc/default/onetimesecret
DEFAULT_ENV_FILE = Path("/etc/default/onetimesecret.d/00-baseline.conf")
# Legacy single-file path for reference:
# DEFAULT_ENV_FILE = Path("/etc/default/onetimesecret")

# Quadlet template with {secrets_section} placeholder for dynamic generation
WEB_TEMPLATE = """\
# OneTimeSecret Web Quadlet - Systemd-managed Podman container
# Location: /etc/containers/systemd/onetime-web@.container
#
# PREREQUISITES (one-time setup):
#
# 0. (Private registry only) Pull image with authentication:
#    rots image pull --tag <tag>
#    # Uses credentials from /etc/containers/auth.json
#
# 1. Environment drop-in directory:
#    /etc/default/onetimesecret.d/*.conf
#    Lexical order determines precedence (00-baseline.conf, 50-host.conf).
#    Baseline ships in confext; per-host overrides layer via OverlayFS.
#
# 2. (Optional) Place config overrides in {config_dir}/:
#    config.yaml, auth.yaml, logging.yaml
#    Only files present on host are mounted; others use container defaults.
#
# OPERATIONS:
#   Start:    systemctl start onetime-web@7043
#   Stop:     systemctl stop onetime-web@7043
#   Logs:     journalctl -u onetime-web@7043 -f
#   Status:   systemctl status onetime-web@7043
#
# TROUBLESHOOTING:
#   Container:     podman exec -it onetime-web-7043 /bin/sh

[Unit]
Description=OneTimeSecret Web Container %i
After=local-fs.target network-online.target{valkey_after}
Wants=network-online.target{valkey_wants}

[Service]
Restart=on-failure
RestartSec=5
# Allow in-flight HTTP requests to complete before the container exits.
# On SIGTERM, the Ruby/Puma process stops accepting new connections and
# drains the request queue; this window gives it time to do so.
# Caddy upstream health checks (HealthInterval=30s) will detect the
# removed backend and stop routing new requests within one check cycle.
TimeoutStopSec=30
{resource_limits_section}
[Container]
ContainerName=onetime-web-%i
Image={image}
Network=host

# Syslog tag for per-instance log filtering: journalctl -t onetime-web-7043 -f
PodmanArgs=--log-opt tag=onetime-web-%i

# Port is derived from instance name: onetime-web@7043 -> PORT=7043
Environment=PORT=%i

# Infrastructure config via drop-in directory (lexical order: 00-baseline, 50-host, etc.)
# Baseline ships in confext; per-host overrides layer on top via OverlayFS merge
EnvironmentFile=/etc/default/onetimesecret.d/*.conf

{secrets_section}

# Host config overrides (per-file, only what exists on host)
{config_volumes_section}

# Static assets extracted from container image
Volume=static_assets:/app/public:ro

# Health check endpoint
HealthCmd=curl -sf http://localhost:%i/health || exit 1
HealthInterval=30s
HealthRetries=3
HealthStartPeriod=10s

[Install]
WantedBy=multi-user.target
"""


def get_secrets_section(
    env_file_path: Path | None = None,  # noqa: ARG001
    *,
    force: bool = False,  # noqa: ARG001
    executor: Executor | None = None,  # noqa: ARG001
    render_mode: bool = False,  # noqa: ARG001
) -> str:
    """Generate the secrets section for the quadlet template.

    Secrets are now managed via the drop-in environment directory
    (/etc/default/onetimesecret.d/*.conf) rather than probed from
    SECRET_VARIABLE_NAMES. This function returns a placeholder comment.

    The signature is preserved for API compatibility with existing callers.
    """
    return "# Secrets loaded via environment drop-in directory"


def get_config_volumes_section(
    cfg: Config,
    *,
    executor: Executor | None = None,
    config_source: Path | None = None,
) -> str:
    """Generate per-file Volume directives for host config overrides.

    Only mounts files that exist on the host. Missing files use container defaults.

    When *config_source* is set (render mode), probe the local directory for the
    canonical CONFIG_FILES instead of the host. The Volume= left-side path is
    still the host path (cfg.config_dir / fname) since the rendered quadlet is
    intended for deployment to a real host.
    """
    if config_source is not None:
        # Render mode: probe the local config_source directory, but keep host
        # paths on the left side of Volume= so the unit deploys correctly.
        lines = []
        for fname in CONFIG_FILES:
            if (config_source / fname).exists():
                host_path = cfg.config_dir / fname
                lines.append(f"Volume={host_path}:/app/etc/{fname}:ro")
        if not lines:
            return "# No host config overrides (using container built-in defaults)"
        return "\n".join(lines)

    files = cfg.get_existing_config_files(executor=executor)

    if not files:
        return "# No host config overrides (using container built-in defaults)"
    lines = []
    for f in files:
        lines.append(f"Volume={f}:/app/etc/{f.name}:ro")
    return "\n".join(lines)


def get_resource_limits_section(cfg: Config) -> str:
    """Generate resource limit directives for the [Service] section.

    Returns the MemoryMax= and CPUQuota= lines (with a trailing newline so
    the placeholder can be placed inside the section without extra spacing)
    when the corresponding Config fields are set.  Returns an empty string
    when neither is configured so the template does not gain spurious blank
    lines.

    Args:
        cfg: Configuration object (reads ``memory_max`` and ``cpu_quota``).

    Returns:
        Multi-line string to substitute for ``{resource_limits_section}``
        in a quadlet template.

    Example output when both are set::

        MemoryMax=1G
        CPUQuota=80%
    """
    lines = []
    if cfg.memory_max:
        from rots.config import MEMORY_MAX_RE

        if not MEMORY_MAX_RE.match(cfg.memory_max):
            raise ValueError(
                f"Invalid MEMORY_MAX: {cfg.memory_max!r}. "
                "Must be a systemd memory value (e.g. 512M, 1G, infinity)."
            )
        lines.append(f"MemoryMax={cfg.memory_max}")
    if cfg.cpu_quota:
        from rots.config import CPU_QUOTA_RE

        if not CPU_QUOTA_RE.match(cfg.cpu_quota):
            raise ValueError(
                f"Invalid CPU_QUOTA: {cfg.cpu_quota!r}. Must be a percentage (e.g. 80%, 150%)."
            )
        lines.append(f"CPUQuota={cfg.cpu_quota}")
    return "\n".join(lines) + "\n" if lines else ""


def _get_valkey_unit_dependencies(cfg: Config) -> tuple[str, str]:
    """Return (after_fragment, wants_fragment) for the [Unit] section.

    When cfg.valkey_service is set (e.g. "valkey-server@6379.service") the
    web quadlet declares ordering and a soft dependency on that unit so that
    on reboot the data store is started before the OTS container.

    Returns:
        A tuple of two strings to be appended after the existing After= and
        Wants= values respectively.  Empty strings when no service is configured.
    """
    if not cfg.valkey_service:
        return "", ""
    from rots.config import SYSTEMD_UNIT_RE

    if not SYSTEMD_UNIT_RE.match(cfg.valkey_service):
        raise ValueError(
            f"Invalid valkey_service: {cfg.valkey_service!r}. "
            "Must be a valid systemd unit name (e.g. valkey-server@6379.service)."
        )
    return f" {cfg.valkey_service}", f"\nWants={cfg.valkey_service}"


def _build_fmt_vars(
    cfg: Config,
    env_file_path: Path | None,
    *,
    force: bool,
    extra_vars: dict | None = None,
    executor: Executor | None = None,
    config_source: Path | None = None,
    render_mode: bool = False,
) -> dict:
    """Build the format variables dict for a quadlet template.

    Shared by both ``_write_template`` (which writes to disk) and
    ``render_template`` (dry-run, no disk I/O).

    Args:
        config_source: When set, probe this local directory for config files
            instead of the host (used by ``--render``).
        render_mode: When True, suppress Secret= lines, probe ``config_source``
            instead of the host filesystem for config volumes, and resolve the
            ``Image=`` value without touching the deployment database. Render
            mode rejects alias tags (``@current``, ``@rollback``, ``current``,
            ``rollback``) since their resolution requires a DB lookup, which
            would create ``deployments.db`` on the caller's host — violating
            the no-host-I/O contract of ``--render``.
    """
    secrets_section = get_secrets_section(
        env_file_path, force=force, executor=executor, render_mode=render_mode
    )
    config_volumes_section = get_config_volumes_section(
        cfg, executor=executor, config_source=config_source
    )

    if cfg.registry:
        image = "onetime.image"
    elif render_mode:
        # Render mode contract: no host I/O, no DB writes. resolve_image_tag
        # would call db.get_alias -> get_connection -> init_db for alias tags,
        # creating deployments.db on the caller's filesystem. Reject aliases
        # explicitly so the user sees a clear error rather than a silent DB
        # file appearing in their checkout.
        tag_key = cfg.tag.lstrip("@").lower()
        if tag_key in ("current", "rollback"):
            raise SystemExit(
                f"render: tag {cfg.tag!r} is an alias and cannot be resolved without "
                "consulting the deployment database (which render mode forbids).\n"
                "Pass a concrete tag or digest with --tag (e.g. --tag v0.24.0) or via "
                "an explicit image reference (e.g. ghcr.io/org/image:v0.24.0)."
            )
        image = join_image_tag(cfg.effective_image, cfg.tag)
    else:
        image = cfg.resolved_image_with_tag(executor=executor)

    fmt_vars: dict = {
        "image": image,
        "config_dir": cfg.config_dir,
        "secrets_section": secrets_section,
        "config_volumes_section": config_volumes_section,
        "resource_limits_section": get_resource_limits_section(cfg),
    }
    if extra_vars:
        fmt_vars.update(extra_vars)
    return fmt_vars


def render_web_template(
    cfg: Config,
    env_file_path: Path | None = None,
    *,
    force: bool = False,
    executor: Executor | None = None,
    config_source: Path | None = None,
    render_mode: bool = False,
) -> str:
    """Render the web quadlet template content without writing to disk.

    Used by dry-run to preview what would be written.
    """
    valkey_after, valkey_wants = _get_valkey_unit_dependencies(cfg)
    fmt_vars = _build_fmt_vars(
        cfg,
        env_file_path,
        force=force,
        extra_vars={"valkey_after": valkey_after, "valkey_wants": valkey_wants},
        executor=executor,
        config_source=config_source,
        render_mode=render_mode,
    )
    return WEB_TEMPLATE.format(**fmt_vars)


def render_worker_template(
    cfg: Config,
    env_file_path: Path | None = None,
    *,
    force: bool = False,
    executor: Executor | None = None,
    config_source: Path | None = None,
    render_mode: bool = False,
) -> str:
    """Render the worker quadlet template content without writing to disk."""
    fmt_vars = _build_fmt_vars(
        cfg,
        env_file_path,
        force=force,
        executor=executor,
        config_source=config_source,
        render_mode=render_mode,
    )
    return WORKER_TEMPLATE.format(**fmt_vars)


def render_scheduler_template(
    cfg: Config,
    env_file_path: Path | None = None,
    *,
    force: bool = False,
    executor: Executor | None = None,
    config_source: Path | None = None,
    render_mode: bool = False,
) -> str:
    """Render the scheduler quadlet template content without writing to disk."""
    fmt_vars = _build_fmt_vars(
        cfg,
        env_file_path,
        force=force,
        executor=executor,
        config_source=config_source,
        render_mode=render_mode,
    )
    return SCHEDULER_TEMPLATE.format(**fmt_vars)


def _write_template(
    template: str,
    path: Path,
    cfg: Config,
    env_file_path: Path | None,
    *,
    force: bool,
    extra_vars: dict | None = None,
    executor: Executor | None = None,
) -> None:
    """Shared implementation for writing a quadlet template to disk.

    Resolves the secrets section and config volumes section, formats the
    template string, writes the file, and triggers a systemd daemon-reload.

    Args:
        template: Template string containing ``{image}``, ``{config_dir}``,
                  ``{secrets_section}``, ``{config_volumes_section}``, and
                  any keys in *extra_vars*.
        path: Destination file path (parent dirs created if absent).
        cfg: Configuration object.
        env_file_path: Optional override for the environment file location.
        force: Pass ``force=True`` to ``get_secrets_section`` to allow deploy
               without secrets.
        extra_vars: Additional ``str.format`` keyword arguments (e.g.
                    ``valkey_after``, ``valkey_wants`` for the web template).
        executor: Optional executor for remote file writes.
    """
    fmt_vars = _build_fmt_vars(
        cfg, env_file_path, force=force, extra_vars=extra_vars, executor=executor
    )
    content = template.format(**fmt_vars)
    if executor is not None and _is_remote(executor):
        executor.run(["mkdir", "-p", str(path.parent)])
        executor.run(["tee", str(path)], input=content)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    systemd.daemon_reload(executor=executor)


def write_web_template(
    cfg: Config,
    env_file_path: Path | None = None,
    *,
    force: bool = False,
    executor: Executor | None = None,
) -> None:
    """Write the web container quadlet template.

    Args:
        cfg: Configuration object with image and paths
        env_file_path: Optional path to environment file for secret discovery
        force: If True, allow deployment even when secrets are not configured.
        executor: Optional executor for remote writes.
    """
    if cfg.registry:
        write_image_template(cfg, executor=executor)
    valkey_after, valkey_wants = _get_valkey_unit_dependencies(cfg)
    _write_template(
        WEB_TEMPLATE,
        cfg.web_template_path,
        cfg,
        env_file_path,
        force=force,
        extra_vars={"valkey_after": valkey_after, "valkey_wants": valkey_wants},
        executor=executor,
    )


# Worker quadlet template - for background job processing (Sneakers/RabbitMQ)
WORKER_TEMPLATE = """\
# OneTimeSecret Worker Quadlet - Systemd-managed Podman container for background jobs
# Location: /etc/containers/systemd/onetime-worker@.container
#
# PREREQUISITES (one-time setup):
#
# 0. (Private registry only) Pull image with authentication:
#    rots image pull --tag <tag>
#    # Uses credentials from /etc/containers/auth.json
#
# 1. Environment drop-in directory:
#    /etc/default/onetimesecret.d/*.conf
#    Lexical order determines precedence (00-baseline.conf, 50-host.conf).
#    Baseline ships in confext; per-host overrides layer via OverlayFS.
#
# 2. (Optional) Place config overrides in {config_dir}/:
#    config.yaml, auth.yaml, logging.yaml
#    Only files present on host are mounted; others use container defaults.
#
# OPERATIONS:
#   Start:    systemctl start onetime-worker@1
#   Stop:     systemctl stop onetime-worker@1
#   Logs:     journalctl -u onetime-worker@1 -f
#   Status:   systemctl status onetime-worker@1
#
# WORKER INSTANCES:
#   Numeric: onetime-worker@1, onetime-worker@2
#   Named:   onetime-worker@billing, onetime-worker@emails
#
# TROUBLESHOOTING:
#   Container:     podman exec -it onetime-worker-1 /bin/sh

[Unit]
Description=OneTimeSecret Worker %i
After=local-fs.target network-online.target
Wants=network-online.target

[Service]
Restart=on-failure
RestartSec=5
# Allow time for graceful job completion on stop
TimeoutStopSec=90
{resource_limits_section}
[Container]
ContainerName=onetime-worker-%i
Image={image}
Network=host

# Syslog tag for per-instance log filtering: journalctl -t onetime-worker-1 -f
PodmanArgs=--log-opt tag=onetime-worker-%i

# Worker ID is derived from instance name: onetime-worker@1 -> WORKER_ID=1
Environment=WORKER_ID=%i

# Infrastructure config via drop-in directory (lexical order: 00-baseline, 50-host, etc.)
# Baseline ships in confext; per-host overrides layer on top via OverlayFS merge
EnvironmentFile=/etc/default/onetimesecret.d/*.conf

{secrets_section}

# Host config overrides (per-file, only what exists on host)
{config_volumes_section}

# Worker entry point - runs Sneakers job processor
Exec=bin/entrypoint.sh bin/ots worker

# Health check - verify sneakers process is running
HealthCmd=pgrep -f "sneakers" || exit 1
HealthInterval=30s
HealthRetries=3
HealthStartPeriod=15s

[Install]
WantedBy=multi-user.target
"""


def write_worker_template(
    cfg: Config,
    env_file_path: Path | None = None,
    *,
    force: bool = False,
    executor: Executor | None = None,
) -> None:
    """Write the worker container quadlet template.

    Args:
        cfg: Configuration object with image and paths
        env_file_path: Optional path to environment file for secret discovery
        force: If True, allow deployment even when secrets are not configured.
        executor: Optional executor for remote writes.
    """
    if cfg.registry:
        write_image_template(cfg, executor=executor)
    _write_template(
        WORKER_TEMPLATE,
        cfg.worker_template_path,
        cfg,
        env_file_path,
        force=force,
        executor=executor,
    )


# Scheduler quadlet template - for cron-like job scheduling
SCHEDULER_TEMPLATE = """\
# OneTimeSecret Scheduler Quadlet - Systemd-managed Podman container for job scheduling
# Location: /etc/containers/systemd/onetime-scheduler@.container
#
# PREREQUISITES (one-time setup):
#
# 0. (Private registry only) Pull image with authentication:
#    rots image pull --tag <tag>
#    # Uses credentials from /etc/containers/auth.json
#
# 1. Environment drop-in directory:
#    /etc/default/onetimesecret.d/*.conf
#    Lexical order determines precedence (00-baseline.conf, 50-host.conf).
#    Baseline ships in confext; per-host overrides layer via OverlayFS.
#
# 2. (Optional) Place config overrides in {config_dir}/:
#    config.yaml, auth.yaml, logging.yaml
#    Only files present on host are mounted; others use container defaults.
#
# OPERATIONS:
#   Start:    systemctl start onetime-scheduler@main
#   Stop:     systemctl stop onetime-scheduler@main
#   Logs:     journalctl -u onetime-scheduler@main -f
#   Status:   systemctl status onetime-scheduler@main
#
# SCHEDULER INSTANCES:
#   Named:   onetime-scheduler@main, onetime-scheduler@cron
#   Numeric: onetime-scheduler@1
#
# TROUBLESHOOTING:
#   Container:     podman exec -it onetime-scheduler-main /bin/sh

[Unit]
Description=OneTimeSecret Scheduler %i
After=local-fs.target network-online.target
Wants=network-online.target

[Service]
Restart=on-failure
RestartSec=5
# Allow time for graceful job completion on stop
TimeoutStopSec=60
{resource_limits_section}
[Container]
ContainerName=onetime-scheduler-%i
Image={image}
Network=host

# Syslog tag for per-instance log filtering: journalctl -t onetime-scheduler-main -f
PodmanArgs=--log-opt tag=onetime-scheduler-%i

# Scheduler ID is derived from instance name: onetime-scheduler@main -> SCHEDULER_ID=main
Environment=SCHEDULER_ID=%i

# Infrastructure config via drop-in directory (lexical order: 00-baseline, 50-host, etc.)
# Baseline ships in confext; per-host overrides layer on top via OverlayFS merge
EnvironmentFile=/etc/default/onetimesecret.d/*.conf

{secrets_section}

# Host config overrides (per-file, only what exists on host)
{config_volumes_section}

# Scheduler entry point - runs scheduled job processor
Exec=bin/entrypoint.sh bin/ots scheduler

# Health check - verify scheduler process is running
HealthCmd=pgrep -f "bin/ots scheduler" || exit 1
HealthInterval=30s
HealthRetries=3
HealthStartPeriod=15s

[Install]
WantedBy=multi-user.target
"""


IMAGE_TEMPLATE = """\
# OneTimeSecret Image Unit - pulls container image with registry authentication
# Location: /etc/containers/systemd/onetime.image
#
# This file is generated when OTS_REGISTRY is set. It ensures the image is
# pulled from the private registry with authentication before any container
# starts. Quadlet auto-creates a dependency: each .container referencing
# Image=onetime.image will wait for onetime-image.service to complete.
#
# OPERATIONS:
#   Pull now:  systemctl start onetime-image.service
#   Status:    systemctl status onetime-image.service

[Image]
Image={image}
AuthFile={auth_file}
"""


def render_image_template(
    cfg: Config,
    *,
    executor: Executor | None = None,
) -> str:
    """Render the image quadlet unit content without writing to disk.

    Used by dry-run to preview what would be written. Only meaningful
    when cfg.registry is set.
    """
    return IMAGE_TEMPLATE.format(
        image=cfg.resolved_image_with_tag(executor=executor),
        auth_file=cfg.get_registry_auth_file(executor=executor),
    )


def write_image_template(
    cfg: Config,
    *,
    executor: Executor | None = None,
) -> None:
    """Write the image quadlet unit for private registry authentication.

    Only called when cfg.registry is set. The generated onetime.image unit
    pulls the image with AuthFile= credentials. All .container units that
    reference Image=onetime.image will auto-depend on onetime-image.service.
    """
    content = render_image_template(cfg, executor=executor)
    path = cfg.image_template_path
    if executor is not None and _is_remote(executor):
        executor.run(["mkdir", "-p", str(path.parent)])
        executor.run(["tee", str(path)], input=content)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    systemd.daemon_reload(executor=executor)


def write_scheduler_template(
    cfg: Config,
    env_file_path: Path | None = None,
    *,
    force: bool = False,
    executor: Executor | None = None,
) -> None:
    """Write the scheduler container quadlet template.

    Args:
        cfg: Configuration object with image and paths
        env_file_path: Optional path to environment file for secret discovery
        force: If True, allow deployment even when secrets are not configured.
        executor: Optional executor for remote writes.
    """
    if cfg.registry:
        write_image_template(cfg, executor=executor)
    _write_template(
        SCHEDULER_TEMPLATE,
        cfg.scheduler_template_path,
        cfg,
        env_file_path,
        force=force,
        executor=executor,
    )
