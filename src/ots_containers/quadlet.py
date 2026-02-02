# src/ots_containers/quadlet.py
"""
Quadlet template generation for OneTimeSecret containers.

The quadlet template is a systemd unit file that defines how to run
the container. Secret= lines are generated dynamically based on the
SECRET_VARIABLE_NAMES defined in the environment file.
"""

from pathlib import Path

from . import systemd
from .config import Config
from .environment_file import generate_quadlet_secret_lines, get_secrets_from_env_file

# Default environment file path
DEFAULT_ENV_FILE = Path("/etc/default/onetimesecret")

# Quadlet template with {secrets_section} placeholder for dynamic generation
WEB_TEMPLATE = """\
# OneTimeSecret Web Quadlet - Systemd-managed Podman container
# Location: /etc/containers/systemd/onetime-web@.container
#
# PREREQUISITES (one-time setup):
#
# 0. (Private registry only) Pull image with authentication:
#    ots-containers image pull --tag <tag>
#    # Uses credentials from /etc/containers/auth.json
#
# 1. Process environment file to create podman secrets:
#    ots env process /etc/default/onetimesecret
#    # This reads SECRET_VARIABLE_NAMES from the env file,
#    # creates podman secrets, and updates the file
#
# 2. Place YAML configs in {config_dir}/:
#    config.yaml, auth.yaml, logging.yaml, billing.yaml
#
# OPERATIONS:
#   Start:    systemctl start onetime-web@7043
#   Stop:     systemctl stop onetime-web@7043
#   Logs:     journalctl -u onetime-web@7043 -f
#   Status:   systemctl status onetime-web@7043
#
# SECRET ROTATION:
#   podman secret rm ots_hmac_secret
#   openssl rand -hex 32 | podman secret create ots_hmac_secret -
#   systemctl restart onetime-web@7043
#
# TROUBLESHOOTING:
#   List secrets:  podman secret ls
#   Inspect:       podman secret inspect ots_hmac_secret
#   Container:     podman exec -it systemd-onetime-web_7043 /bin/sh

[Unit]
Description=OneTimeSecret Web Container %i
After=local-fs.target network-online.target
Wants=network-online.target

[Service]
Restart=on-failure
RestartSec=5

[Container]
Image={image}
Network=host

# Syslog tag for per-instance log filtering: journalctl -t onetime-web-7043 -f
PodmanArgs=--log-opt tag=onetime-web-%i

# Port is derived from instance name: onetime-web@7043 -> PORT=7043
Environment=PORT=%i

# Infrastructure config (connection strings, log level)
# Edit this file and restart to apply changes
EnvironmentFile=/etc/default/onetimesecret

{secrets_section}

# Config directory mounted read-only (all YAML configs)
Volume={config_dir}:/app/etc:ro

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


def get_secrets_section(env_file_path: Path | None = None) -> str:
    """Generate the secrets section for the quadlet template.

    Reads SECRET_VARIABLE_NAMES from the environment file and generates
    corresponding Secret= directives. Returns empty comment if no secrets
    are defined.

    Args:
        env_file_path: Path to environment file (defaults to /etc/default/onetimesecret)

    Returns:
        Multi-line string with Secret= directives, or comment if none defined
    """
    env_path = env_file_path or DEFAULT_ENV_FILE

    if not env_path.exists():
        return "# No secrets configured (env file not found)"

    secrets = get_secrets_from_env_file(env_path)
    if not secrets:
        return "# No secrets configured (SECRET_VARIABLE_NAMES not set in env file)"

    return generate_quadlet_secret_lines(secrets)


def write_web_template(cfg: Config, env_file_path: Path | None = None) -> None:
    """Write the web container quadlet template.

    Args:
        cfg: Configuration object with image and paths
        env_file_path: Optional path to environment file for secret discovery
    """
    secrets_section = get_secrets_section(env_file_path)

    content = WEB_TEMPLATE.format(
        image=cfg.resolved_image_with_tag,
        config_dir=cfg.config_dir,
        secrets_section=secrets_section,
    )
    cfg.web_template_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.web_template_path.write_text(content)
    systemd.daemon_reload()


# Worker quadlet template - for background job processing (Sneakers/RabbitMQ)
WORKER_TEMPLATE = """\
# OneTimeSecret Worker Quadlet - Systemd-managed Podman container for background jobs
# Location: /etc/containers/systemd/onetime-worker@.container
#
# PREREQUISITES (one-time setup):
#
# 0. (Private registry only) Pull image with authentication:
#    ots-containers image pull --tag <tag>
#    # Uses credentials from /etc/containers/auth.json
#
# 1. Process environment file to create podman secrets:
#    ots env process /etc/default/onetimesecret
#    # This reads SECRET_VARIABLE_NAMES from the env file,
#    # creates podman secrets, and updates the file
#
# 2. Place YAML configs in {config_dir}/:
#    config.yaml, auth.yaml, logging.yaml, billing.yaml
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
# SECRET ROTATION:
#   podman secret rm ots_hmac_secret
#   openssl rand -hex 32 | podman secret create ots_hmac_secret -
#   systemctl restart onetime-worker@1
#
# TROUBLESHOOTING:
#   List secrets:  podman secret ls
#   Inspect:       podman secret inspect ots_hmac_secret
#   Container:     podman exec -it systemd-onetime-worker@1 /bin/sh

[Unit]
Description=OneTimeSecret Worker %i
After=local-fs.target network-online.target
Wants=network-online.target

[Service]
Restart=on-failure
RestartSec=5
# Allow time for graceful job completion on stop
TimeoutStopSec=90

[Container]
Image={image}
Network=host

# Syslog tag for per-instance log filtering: journalctl -t onetime-worker-1 -f
PodmanArgs=--log-opt tag=onetime-worker-%i

# Worker ID is derived from instance name: onetime-worker@1 -> WORKER_ID=1
Environment=WORKER_ID=%i

# Infrastructure config (connection strings, log level)
# Edit this file and restart to apply changes
EnvironmentFile=/etc/default/onetimesecret

{secrets_section}

# Config directory mounted read-only (all YAML configs)
Volume={config_dir}:/app/etc:ro

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


def write_worker_template(cfg: Config, env_file_path: Path | None = None) -> None:
    """Write the worker container quadlet template.

    Args:
        cfg: Configuration object with image and paths
        env_file_path: Optional path to environment file for secret discovery
    """
    secrets_section = get_secrets_section(env_file_path)

    content = WORKER_TEMPLATE.format(
        image=cfg.resolved_image_with_tag,
        config_dir=cfg.config_dir,
        secrets_section=secrets_section,
    )
    cfg.worker_template_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.worker_template_path.write_text(content)
    systemd.daemon_reload()


# Scheduler quadlet template - for cron-like job scheduling
SCHEDULER_TEMPLATE = """\
# OneTimeSecret Scheduler Quadlet - Systemd-managed Podman container for job scheduling
# Location: /etc/containers/systemd/onetime-scheduler@.container
#
# PREREQUISITES (one-time setup):
#
# 0. (Private registry only) Pull image with authentication:
#    ots-containers image pull --tag <tag>
#    # Uses credentials from /etc/containers/auth.json
#
# 1. Process environment file to create podman secrets:
#    ots env process /etc/default/onetimesecret
#    # This reads SECRET_VARIABLE_NAMES from the env file,
#    # creates podman secrets, and updates the file
#
# 2. Place YAML configs in {config_dir}/:
#    config.yaml, auth.yaml, logging.yaml, billing.yaml
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
# SECRET ROTATION:
#   podman secret rm ots_hmac_secret
#   openssl rand -hex 32 | podman secret create ots_hmac_secret -
#   systemctl restart onetime-scheduler@main
#
# TROUBLESHOOTING:
#   List secrets:  podman secret ls
#   Inspect:       podman secret inspect ots_hmac_secret
#   Container:     podman exec -it systemd-onetime-scheduler_main /bin/sh

[Unit]
Description=OneTimeSecret Scheduler %i
After=local-fs.target network-online.target
Wants=network-online.target

[Service]
Restart=on-failure
RestartSec=5
# Allow time for graceful job completion on stop
TimeoutStopSec=60

[Container]
Image={image}
Network=host

# Syslog tag for per-instance log filtering: journalctl -t onetime-scheduler-main -f
PodmanArgs=--log-opt tag=onetime-scheduler-%i

# Scheduler ID is derived from instance name: onetime-scheduler@main -> SCHEDULER_ID=main
Environment=SCHEDULER_ID=%i

# Infrastructure config (connection strings, log level)
# Edit this file and restart to apply changes
EnvironmentFile=/etc/default/onetimesecret

{secrets_section}

# Config directory mounted read-only (all YAML configs)
Volume={config_dir}:/app/etc:ro

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


def write_scheduler_template(cfg: Config, env_file_path: Path | None = None) -> None:
    """Write the scheduler container quadlet template.

    Args:
        cfg: Configuration object with image and paths
        env_file_path: Optional path to environment file for secret discovery
    """
    secrets_section = get_secrets_section(env_file_path)

    content = SCHEDULER_TEMPLATE.format(
        image=cfg.resolved_image_with_tag,
        config_dir=cfg.config_dir,
        secrets_section=secrets_section,
    )
    cfg.scheduler_template_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.scheduler_template_path.write_text(content)
    systemd.daemon_reload()
