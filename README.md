# rots - Remote OTS Commander

Service orchestration CLI for [OneTimeSecret](https://github.com/onetimesecret/onetimesecret) infrastructure.

**Dual-purpose management tool:**

- **Container orchestration**: Containerized OTS deployments via Podman Quadlets (systemd integration)
- **Service management**: Native systemd services for dependencies (Valkey, Redis)

## Installation

### With pipx (Recommended)

[pipx](https://pipx.pypa.io/) installs CLI tools in isolated environments, preventing dependency conflicts and enabling clean upgrades.

```bash
# Install pipx if needed
pip install pipx
pipx ensurepath

# Install rots
pipx install rots

# Or from git
pipx install git+https://github.com/onetimesecret/ots-containers.git
```

### Migrating from pip to pipx

If you previously installed with pip:

```bash
pip uninstall rots
pipx install rots
```

### With pip

Not recommended for production. Use pipx instead.

```bash
pip install rots
```

### From source

```bash
git clone https://github.com/onetimesecret/ots-containers.git
cd ots-containers
pipx install .
```

## Upgrading

```bash
# Check for updates
rots self check

# Upgrade to latest
rots self upgrade

# Upgrade to specific version
rots self upgrade --version 0.24.0
```

The `rots self upgrade` command wraps pipx and is safe to invoke remotely via the sidecar.

## Usage

```bash
rots --help
rots --version
```

### Instance Types

Three container types with explicit systemd unit naming:

| Type          | Unit Name                | Identifier  | Use             |
| ------------- | ------------------------ | ----------- | --------------- |
| `--web`       | `onetime-web@{port}`     | Port number | HTTP servers    |
| `--worker`    | `onetime-worker@{id}`    | Name/number | Background jobs |
| `--scheduler` | `onetime-scheduler@{id}` | Name/number | Scheduled tasks |

### Managing OTS Containers

```bash
# List all instances
rots instances
rots instances --json

# List by type
rots instances --web
rots instances --worker
rots instances --scheduler

# Deploy instances
rots instances deploy --web 7043 7044
rots instances deploy --worker billing emails
rots instances deploy --scheduler main

# Redeploy (regenerate quadlet and restart)
rots instances redeploy                    # all running
rots instances redeploy --web 7043         # specific

# Start/stop/restart
rots instances start --web 7043
rots instances stop --scheduler main
rots instances restart                     # all running

# Status and logs
rots instances status
rots instances logs --web 7043 -f
rots instances logs --scheduler main -f

# Enable/disable at boot
rots instances enable --web 7043
rots instances disable --scheduler main -y

# Interactive shell
rots instances exec --web 7043
```

### Managing systemd Services (Valkey, Redis)

```bash
# Initialize new service instance
rots service init valkey 6379
rots service init redis 6380 --bind 0.0.0.0

# Start/stop/restart
rots service start valkey 6379
rots service stop redis 6380
rots service restart valkey 6379

# Status and logs
rots service status valkey 6379
rots service logs valkey 6379 --follow

# Enable/disable at boot
rots service enable valkey 6379
rots service disable redis 6380

# List available service packages
rots service
```

### Generating Cloud-Init Configurations

```bash
# Generate basic cloud-init config
rots cloudinit generate > user-data.yaml

# Include PostgreSQL repository
rots cloudinit generate --include-postgresql --postgresql-key /path/to/pgdg.asc

# Include Valkey repository
rots cloudinit generate --include-valkey --valkey-key /path/to/valkey.gpg

# Validate configuration
rots cloudinit validate user-data.yaml
```

## Environment Variables

```bash
# Use a specific image tag
TAG=v0.23.0 rots instances redeploy --web 7043

# Use a different image
IMAGE=ghcr.io/onetimesecret/onetimesecret TAG=latest rots instances deploy --web 7044
```

## Prerequisites

- Linux with systemd
- Podman installed and configured
- Python 3.11+

## Server Setup

FHS-compliant directory structure:

### OTS Container Configuration

```
/etc/onetimesecret/              # System configuration
├── config.yaml                  # Application configuration
├── auth.yaml                    # Authentication config
└── logging.yaml                 # Logging config

/etc/default/onetimesecret       # Environment file (shared by all instances)

/etc/containers/systemd/         # Quadlet templates (managed by tool)
├── onetime-web@.container
├── onetime-worker@.container
└── onetime-scheduler@.container

/var/lib/onetimesecret/          # Runtime data
└── deployments.db               # Deployment timeline (SQLite)
```

### Service Configuration (Valkey/Redis)

```
/etc/valkey/                     # Valkey system configuration
├── valkey.conf                  # Default config template
└── instances/                   # Instance configs (created by tool)
    ├── 6379.conf
    └── 6379-secrets.conf        # Secrets file (mode 0640)

/var/lib/valkey/                 # Runtime data
└── 6379/
    └── dump.rdb
```

## How It Works

### Container Management

1. **Quadlet templates**: Writes systemd unit templates to `/etc/containers/systemd/`
2. **Environment**: Reads from `/etc/default/onetimesecret`
3. **Secrets**: Uses Podman secrets for sensitive values
4. **Timeline**: Records deployments to SQLite for audit and rollback

### Service Management

1. **Config files**: Copies package defaults to instance-specific configs
2. **Secrets**: Creates separate secrets files with restricted permissions
3. **Data directories**: Creates per-instance data directories with correct ownership
4. **systemd**: Manages services using package-provided templates

## Troubleshooting

```bash
# Check instance status
rots instances status
systemctl status onetime-web@7043

# View logs
rots instances logs --web 7043 -f
journalctl -u onetime-web@7043 -f

# Unified log filtering (all instance types)
journalctl -t onetime -f

# List all onetime systemd units
systemctl list-units 'onetime-*'

# Verify Quadlet templates
cat /etc/containers/systemd/onetime-web@.container

# Reload systemd after manual changes
systemctl daemon-reload
```

## Development

```bash
# Editable install
git clone https://github.com/onetimesecret/ots-containers.git
cd ots-containers
pip install -e ".[dev,test]"

# Run tests
pytest tests/

# Run with coverage (CI threshold: 70%)
pytest tests/ --cov=ots_containers --cov-fail-under=70

# Pre-commit hooks
pre-commit install
```

### Running as root

```bash
# Use full path
sudo /home/youruser/.local/bin/rots instances status

# Or create symlink
sudo ln -s /home/youruser/.local/bin/rots /usr/local/bin/rots
```

## License

MIT
