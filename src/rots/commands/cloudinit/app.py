# src/rots/commands/cloudinit/app.py

"""Cloud-init configuration generation commands.

Generates cloud-init YAML with Debian 13 (Trixie) DEB822-style apt sources.
"""

import logging
import sys
from pathlib import Path
from typing import Annotated

import cyclopts

from .templates import DEFAULT_CADDY_VERSION, generate_cloudinit_config

logger = logging.getLogger(__name__)

app = cyclopts.App(
    name="cloudinit",
    help="Generate cloud-init configurations for OTS infrastructure",
)


@app.default
def _default():
    """Show cloud-init commands help."""
    print("Cloud-init configuration generation for OTS infrastructure")
    print()
    print("Supports Debian 13 (Trixie) DEB822-style apt sources")
    print()
    print("Use 'rots cloudinit --help' for available commands")


@app.command
def generate(
    output: Annotated[
        str | None,
        cyclopts.Parameter(help="Output file path (default: stdout)"),
    ] = None,
    *,
    include_postgresql: Annotated[
        bool, cyclopts.Parameter(help="Include PostgreSQL apt repository")
    ] = False,
    include_valkey: Annotated[
        bool, cyclopts.Parameter(help="Include Valkey apt repository")
    ] = False,
    include_xcaddy: Annotated[
        bool,
        cyclopts.Parameter(help="Include xcaddy repo and build custom Caddy (web profile)"),
    ] = False,
    caddy_version: Annotated[
        str,
        cyclopts.Parameter(help="Caddy version to build with xcaddy"),
    ] = DEFAULT_CADDY_VERSION,
    caddy_plugins: Annotated[
        list[str] | None,
        cyclopts.Parameter(help="Caddy plugins to include (repeatable)"),
    ] = None,
    postgresql_key: Annotated[
        str | None,
        cyclopts.Parameter(help="Path to PostgreSQL GPG key file"),
    ] = None,
    valkey_key: Annotated[
        str | None,
        cyclopts.Parameter(help="Path to Valkey GPG key file"),
    ] = None,
    ssh_authorized_key: Annotated[
        list[str] | None,
        cyclopts.Parameter(help="SSH public key to add to the default user (repeatable)"),
    ] = None,
    timezone: Annotated[
        str,
        cyclopts.Parameter(help="System timezone (e.g. UTC, America/New_York)"),
    ] = "UTC",
    hostname: Annotated[
        str | None,
        cyclopts.Parameter(help="System hostname"),
    ] = None,
):
    """Generate cloud-init configuration with Debian 13 apt sources.

    Generates an opinionated cloud-init YAML file that takes a fresh Debian 13
    VM to a running OneTimeSecret instance in a single boot:

    - Debian 13 (Trixie) main repositories in DEB822 format
    - onetimesecret system user/group created at boot
    - /etc/default/onetimesecret scaffolded for operator configuration
    - podman socket enabled; rots init invoked via runcmd
    - Optional PostgreSQL official repository
    - Optional Valkey repository
    - Optional xcaddy repo and custom Caddy build (web profile)
    - Package update/upgrade configuration

    Example:
        rots cloudinit generate > user-data.yaml
        rots cloudinit generate --output /tmp/cloud-init.yaml
        rots cloudinit generate --include-postgresql --postgresql-key /path/to/pgp.asc
        rots cloudinit generate --include-xcaddy
        rots cloudinit generate --include-xcaddy --caddy-version v2.10.2
        rots cloudinit generate --timezone America/New_York --hostname ots-prod-1
        rots cloudinit generate --ssh-authorized-key "ssh-ed25519 AAAA..."
    """
    # Read GPG keys if provided
    postgresql_gpg = None
    valkey_gpg = None

    if include_postgresql and postgresql_key:
        postgresql_gpg = Path(postgresql_key).read_text()

    if include_valkey and valkey_key:
        valkey_gpg = Path(valkey_key).read_text()

    # Generate configuration
    try:
        config = generate_cloudinit_config(
            include_postgresql=include_postgresql,
            include_valkey=include_valkey,
            include_xcaddy=include_xcaddy,
            postgresql_gpg_key=postgresql_gpg,
            valkey_gpg_key=valkey_gpg,
            caddy_version=caddy_version,
            caddy_plugins=caddy_plugins,
            ssh_authorized_keys=ssh_authorized_key or None,
            timezone=timezone,
            hostname=hostname,
        )
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    # Output
    if output:
        Path(output).write_text(config)
        logger.info(f"[created] {output}")
    else:
        print(config)


@app.command
def validate(
    file_path: Annotated[
        str,
        cyclopts.Parameter(help="Path to cloud-init YAML file"),
    ],
):
    """Validate a cloud-init configuration file.

    Checks for:
    - Valid YAML syntax
    - Required cloud-init sections
    - DEB822 apt sources format

    Example:
        rots cloudinit validate user-data.yaml
    """
    import yaml

    try:
        config_path = Path(file_path)
        if not config_path.exists():
            print(f"File not found: {file_path}", file=sys.stderr)
            raise SystemExit(1)

        content = config_path.read_text()
        data = yaml.safe_load(content)

        # Basic validation
        errors = []

        if not isinstance(data, dict):
            errors.append("Root element must be a dictionary")

        if "apt" in data and "sources_list" in data["apt"]:
            sources_list = data["apt"]["sources_list"]
            # Check for DEB822 format indicators
            if "Types:" not in sources_list and "URIs:" not in sources_list:
                errors.append("apt.sources_list doesn't appear to use DEB822 format")

        if errors:
            print(f"Validation failed for {file_path}:", file=sys.stderr)
            for error in errors:
                print(f"  - {error}", file=sys.stderr)
            raise SystemExit(1)
        else:
            logger.info(f"[ok] {file_path} is valid")

    except yaml.YAMLError as e:
        print(f"Invalid YAML in {file_path}:", file=sys.stderr)
        print(f"  {e}", file=sys.stderr)
        raise SystemExit(1)
    except SystemExit:
        raise
    except Exception as e:
        raise SystemExit(f"Validation failed: {e}") from e
