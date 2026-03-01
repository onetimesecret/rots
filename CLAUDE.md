# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Build & Test Commands

```bash
# Install for development (editable)
pip install -e ".[dev,test]"

# Run all tests
pytest tests/

# Run single test file
pytest tests/test_quadlet.py

# Run single test by name
pytest tests/test_quadlet.py -k "test_template"

# Run service management tests
pytest tests/test_service.py

# Run cloud-init tests
pytest tests/commands/cloudinit/

# Run tests with coverage (CI threshold: 70%)
pytest tests/ --cov=rots --cov-report=term-missing --cov-fail-under=70

# IMPORTANT: See docs/TESTING.md for testing patterns
# Key rule: mock responses must use tmp_path, not real system paths

# Lint and format
ruff check src/
ruff format src/
ruff check src/ --fix  # auto-fix

# Type checking
pyright src/

# Pre-commit hooks (auto-installed)
pre-commit run --all-files
```

## Git Notes

When running git commands with long output, use `git --no-pager diff` etc.

## Architecture

This is a dual-purpose service orchestration tool:

1. **Container management**: OneTimeSecret containers via Podman Quadlets (systemd integration)
2. **Service management**: Native systemd services for dependencies (Valkey, Redis)

### Core Modules (`src/rots/`)

- **cli.py** - Entry point (`app`), registers subcommand groups
- **config.py** - `Config` dataclass: image, tag, paths. Reads from env vars (IMAGE, TAG, etc.)
- **quadlet.py** - Writes systemd Quadlet template to `/etc/containers/systemd/onetime@.container`
- **systemd.py** - Wrappers around `systemctl`: start/stop/restart/status, `discover_instances()` for auto-detection
- **podman.py** - `Podman` class: chainable interface to podman CLI (e.g., `podman.container.ls()`)
- **assets.py** - Extracts `/app/public` from container image to shared volume

### Commands (`src/rots/commands/`)

#### Container Commands

- **instance.py** - Main operations: `deploy`, `redeploy`, `undeploy`, `start`, `stop`, `restart`, `status`, `logs`, `list`
- **assets.py** - `sync` command for static asset updates
- **image.py** - Container image management
- **proxy.py** - Caddy reverse proxy configuration

#### Service Commands (`service/`)

- **app.py** - Service lifecycle: `init`, `start`, `stop`, `restart`, `status`, `logs`, `enable`, `disable`, `list_instances`
- **packages.py** - Service package definitions: `VALKEY`, `REDIS` with config paths, secrets handling, systemd templates
- **\_helpers.py** - Shared utilities: config file management, secrets creation, systemctl wrappers

#### Cloud-Init Commands (`cloudinit/`)

- **app.py** - Cloud-init generation: `generate`, `validate`
- **templates.py** - DEB822-style apt sources templates for Debian 13 (Trixie), PostgreSQL, and Valkey repositories

### Key Patterns

- Uses **cyclopts** for CLI framework (decorators like `@app.command()`)
- **Port-based instance identification**: Each instance runs on a specific port (e.g., 7043 for containers, 6379 for services)
- **Container auto-discovery**: `systemd.discover_instances()` finds running `onetime@*` services
- **Container env templating**: `/etc/onetimesecret/.env` → `/var/lib/onetimesecret/.env-{port}` (FHS-compliant)
- **Service config copy-on-write**: Package defaults (`/etc/valkey/valkey.conf`) → instance configs (`/etc/valkey/instances/6379.conf`)
- **Service secrets separation**: Sensitive data in separate files with restricted permissions (mode 0640, owned by service user)
- **Package-provided templates**: Uses existing systemd templates (`valkey-server@.service`) rather than creating custom units
- ## Verification
  Don't invent technical rationales. When working with runtime behavior, get or
  ask for actual output (podman ps, systemctl status, etc.) before changing code.
  Documentation and memories are not substitutes for verified information.
