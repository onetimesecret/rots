# ots-containers

Service orchestration CLI for [OneTimeSecret](https://github.com/onetimesecret/onetimesecret) infrastructure.

**Dual-purpose management tool:**

- **Container orchestration**: Containerized OTS deployments via Podman Quadlets (systemd integration)
- **Service management**: Native systemd services for dependencies (Valkey, Redis)

## Installation

### With pipx (Recommended)

```bash
pipx install git+https://github.com/onetimesecret/ots-containers.git
```

### With pip

```bash
pip install git+https://github.com/onetimesecret/ots-containers.git
```

### From source

```bash
git clone https://github.com/onetimesecret/ots-containers.git
cd ots-containers
pipx install .
```

## Usage

```bash
ots-containers --help
ots-containers --version
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
ots-containers instances
ots-containers instances --json

# List by type
ots-containers instances --web
ots-containers instances --worker
ots-containers instances --scheduler

# Deploy instances
ots-containers instances deploy --web 7043 7044
ots-containers instances deploy --worker billing emails
ots-containers instances deploy --scheduler main

# Redeploy (regenerate quadlet and restart)
ots-containers instances redeploy                    # all running
ots-containers instances redeploy --web 7043         # specific

# Start/stop/restart
ots-containers instances start --web 7043
ots-containers instances stop --scheduler main
ots-containers instances restart                     # all running

# Status and logs
ots-containers instances status
ots-containers instances logs --web 7043 -f
ots-containers instances logs --scheduler main -f

# Enable/disable at boot
ots-containers instances enable --web 7043
ots-containers instances disable --scheduler main -y

# Interactive shell
ots-containers instances exec --web 7043
```

### Managing systemd Services (Valkey, Redis)

```bash
# Initialize new service instance
ots-containers service init valkey 6379
ots-containers service init redis 6380 --bind 0.0.0.0

# Start/stop/restart
ots-containers service start valkey 6379
ots-containers service stop redis 6380
ots-containers service restart valkey 6379

# Status and logs
ots-containers service status valkey 6379
ots-containers service logs valkey 6379 --follow

# Enable/disable at boot
ots-containers service enable valkey 6379
ots-containers service disable redis 6380

# List available service packages
ots-containers service
```

## Environment Variables

```bash
# Use a specific image tag
TAG=v0.23.0 ots-containers instances redeploy --web 7043

# Use a different image
IMAGE=ghcr.io/onetimesecret/onetimesecret TAG=latest ots-containers instances deploy --web 7044
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
ots-containers instances status
systemctl status onetime-web@7043

# View logs
ots-containers instances logs --web 7043 -f
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
sudo /home/youruser/.local/bin/ots-containers instances status

# Or create symlink
sudo ln -s /home/youruser/.local/bin/ots-containers /usr/local/bin/ots-containers
```

## License

MIT
