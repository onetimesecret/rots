# src/rots/commands/cloudinit/templates.py

"""Cloud-init configuration templates with Debian 13 DEB822 apt sources."""

import yaml

DEFAULT_CADDY_VERSION = "v2.10.2"

DEFAULT_CADDY_PLUGINS = [
    "github.com/mholt/caddy-l4",
    "github.com/caddy-dns/hetzner",
    "github.com/caddy-dns/cloudflare",
    "github.com/digilolnet/caddy-bunny-ip",
]

# Default environment file content for /etc/default/onetimesecret.
# Written to the VM by the write_files cloud-init section so operators
# have a scaffold to fill in before running `ots env process`.
_DEFAULT_OTS_ENV_CONTENT = """\
# /etc/default/onetimesecret
#
# OneTime Secret - Environment Variables
#
# This file is sourced by the systemd quadlet container.
# Secret values listed in SECRET_VARIABLE_NAMES are stored
# in podman secrets (not in this file).
#
# Usage:
#   1. Set SECRET_VARIABLE_NAMES with your secret env var names
#   2. Add secret values as regular entries (STRIPE_API_KEY=sk_live_xxx)
#   3. Run: ots env process
#   4. Secret values are moved to podman secrets
#   5. This file is updated: _STRIPE_API_KEY=ots_stripe_api_key

# Secret variable names (comma, space, or colon separated)
SECRET_VARIABLE_NAMES=STRIPE_API_KEY,STRIPE_WEBHOOK_SIGNING_SECRET,SECRET,SESSION_SECRET,AUTH_SECRET,SMTP_PASSWORD

# Connection strings (not secrets - stored here)
AUTH_DATABASE_URL=
RABBITMQ_URL=
REDIS_URL=

# Mail configuration
SMTP_USERNAME=
SMTP_HOST=
SMTP_PORT=587
SMTP_AUTH=login
SMTP_TLS=true

# Core settings
HOST=
COLONEL=

# Runtime flags
AUTHENTICATION_MODE=full
SSL=true
RACK_ENV=production
"""

_DEFAULT_CADDYFILE_CONTENT = """\
# /etc/caddy/Caddyfile
# Basic Caddyfile - replace with your site configuration
{
    # Global options
    admin off
}

# Example: serve a site (replace with actual domain/config)
:80 {
    respond "OK" 200
}
"""

_DEFAULT_CADDY_SERVICE_CONTENT = """\
[Unit]
Description=Caddy
Documentation=https://caddyserver.com/docs/
After=network.target network-online.target
Requires=network-online.target

[Service]
Type=notify
User=caddy
Group=caddy
ExecStart=/usr/local/bin/caddy run --environ --config /etc/caddy/Caddyfile
ExecReload=/usr/local/bin/caddy reload --config /etc/caddy/Caddyfile --force
TimeoutStopSec=5s
LimitNOFILE=1048576
AmbientCapabilities=CAP_NET_BIND_SERVICE
Restart=on-abnormal

[Install]
WantedBy=multi-user.target
"""


class _LiteralStr(str):
    """Marker subclass so the YAML dumper renders as a literal block scalar (``|``)."""


def _literal_representer(dumper: yaml.Dumper, data: "_LiteralStr") -> yaml.ScalarNode:
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")


class _OTSDumper(yaml.Dumper):
    """yaml.Dumper subclass that writes _LiteralStr values as literal block scalars."""


_OTSDumper.add_representer(_LiteralStr, _literal_representer)


def get_debian13_sources_list() -> str:
    """Get just the Debian 13 DEB822 sources.list content.

    Returns:
        DEB822-formatted sources.list content
    """
    return """Types: deb
URIs: http://deb.debian.org/debian
Suites: trixie trixie-updates
Components: main contrib non-free non-free-firmware
Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg

Types: deb
URIs: http://deb.debian.org/debian
Suites: trixie-backports
Components: main contrib non-free non-free-firmware
Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg

Types: deb
URIs: http://security.debian.org/debian-security
Suites: trixie-security
Components: main contrib non-free non-free-firmware
Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg
"""


def generate_cloudinit_config(
    *,
    include_postgresql: bool = False,
    include_valkey: bool = False,
    include_xcaddy: bool = False,
    postgresql_gpg_key: str | None = None,
    valkey_gpg_key: str | None = None,
    caddy_version: str = DEFAULT_CADDY_VERSION,
    caddy_plugins: list[str] | None = None,
    ssh_authorized_keys: list[str] | None = None,
    timezone: str = "UTC",
    hostname: str | None = None,
) -> str:
    """Generate cloud-init YAML with Debian 13 DEB822-style apt sources.

    Produces an opinionated cloud-init that takes a fresh Debian 13 VM to a
    running OneTimeSecret instance in a single boot, including:

    - Debian 13 (Trixie) DEB822-style apt sources
    - ``onetimesecret`` system user and group (no login shell, no home dir)
    - ``/etc/default/onetimesecret`` scaffolded with placeholder values
    - ``/etc/onetimesecret/`` and ``/var/lib/onetimesecret/`` directories
    - podman socket enabled and ``rots init`` invoked via runcmd
    - Optional third-party repositories (PostgreSQL, Valkey, xcaddy/Caddy)

    Args:
        include_postgresql: Include PostgreSQL official repository
        include_valkey: Include Valkey repository
        include_xcaddy: Include xcaddy repo and build custom Caddy binary
        postgresql_gpg_key: PostgreSQL GPG public key content (required when
            include_postgresql=True). Obtain with:
            ``curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc``
        valkey_gpg_key: Valkey GPG public key content (required when
            include_valkey=True). Obtain with:
            ``curl -fsSL https://packages.valkey.io/valkey.gpg``
        caddy_version: Caddy version to build (default: v2.10.2)
        caddy_plugins: Caddy plugins to include (default: OTS web profile)
        ssh_authorized_keys: SSH public keys to add to the default cloud-init user
        timezone: System timezone (default: UTC)
        hostname: System hostname (default: not set)

    Returns:
        Complete cloud-init YAML configuration as string

    Raises:
        ValueError: When a repository key is required but not provided.
            cloud-init silently fails to add the repository when the key is
            invalid, so refusing to generate is safer than emitting a
            placeholder.
    """
    if include_postgresql and not postgresql_gpg_key:
        raise ValueError(
            "PostgreSQL GPG key is required when --include-postgresql is used.\n"
            "Obtain the key with:\n"
            "  curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc\n"
            "Then pass it with: --postgresql-key /path/to/key.asc"
        )
    if include_valkey and not valkey_gpg_key:
        raise ValueError(
            "Valkey GPG key is required when --include-valkey is used.\n"
            "Obtain the key with:\n"
            "  curl -fsSL https://packages.valkey.io/valkey.gpg\n"
            "Then pass it with: --valkey-key /path/to/key.gpg"
        )

    # ------------------------------------------------------------------ apt --
    apt: dict = {
        "sources_list": _LiteralStr(get_debian13_sources_list()),
    }

    sources: dict = {}
    if include_postgresql:
        pg_entry: dict = {
            "source": "deb http://apt.postgresql.org/pub/repos/apt trixie-pgdg main",
        }
        if postgresql_gpg_key:
            pg_entry["key"] = _LiteralStr(postgresql_gpg_key)
        sources["postgresql"] = pg_entry

    if include_valkey:
        valkey_entry: dict = {
            "source": "deb https://packages.valkey.io/deb/ trixie main",
        }
        if valkey_gpg_key:
            valkey_entry["key"] = _LiteralStr(valkey_gpg_key)
        sources["valkey"] = valkey_entry

    if sources:
        apt["sources"] = sources

    # --------------------------------------------------------------- packages -
    packages = [
        "curl",
        "wget",
        "git",
        "vim",
        "podman",
        "systemd-container",
        "python3-pip",
    ]

    if include_postgresql:
        packages.append("postgresql-client")

    if include_valkey:
        packages.append("valkey")

    if include_xcaddy:
        packages.extend(
            [
                "debian-keyring",
                "debian-archive-keyring",
                "apt-transport-https",
                "gnupg",
            ]
        )

    # ----------------------------------------------------------------- users --
    # Always create the OTS service account.
    users: list[dict] = [
        {
            "name": "onetimesecret",
            "system": True,
            "shell": "/usr/sbin/nologin",
            "no_create_home": True,
            "groups": [],
        }
    ]

    if include_xcaddy:
        users.append(
            {
                "name": "caddy",
                "system": True,
                "shell": "/usr/sbin/nologin",
                "home": "/var/lib/caddy",
                "create_home": True,
                "groups": [],
            }
        )

    # ------------------------------------------------------------ write_files --
    # Always scaffold the OTS env file so operators can fill it in.
    write_files: list[dict] = [
        {
            "path": "/etc/default/onetimesecret",
            "owner": "root:onetimesecret",
            "permissions": "0640",
            "content": _LiteralStr(_DEFAULT_OTS_ENV_CONTENT),
        },
    ]

    if include_xcaddy:
        write_files.extend(
            [
                {
                    "path": "/etc/caddy/Caddyfile",
                    "owner": "caddy:caddy",
                    "permissions": "0644",
                    "content": _LiteralStr(_DEFAULT_CADDYFILE_CONTENT),
                },
                {
                    "path": "/etc/systemd/system/caddy.service",
                    "owner": "root:root",
                    "permissions": "0644",
                    "content": _LiteralStr(_DEFAULT_CADDY_SERVICE_CONTENT),
                },
            ]
        )

    # --------------------------------------------------------------- runcmd ---
    runcmd: list[str] = [
        # Create required OTS directories with correct ownership
        "mkdir -p /etc/onetimesecret /var/lib/onetimesecret",
        "chown onetimesecret:onetimesecret /etc/onetimesecret /var/lib/onetimesecret",
        # Enable podman socket so rots can manage containers
        "systemctl enable --now podman.socket",
        # Install rots CLI then run init to scaffold the deployment DB
        "pip3 install rots",
        "rots init",
    ]

    if include_xcaddy:
        import shlex

        plugins = caddy_plugins if caddy_plugins is not None else DEFAULT_CADDY_PLUGINS
        quoted_version = shlex.quote(caddy_version)
        build_args = " ".join(f"--with {shlex.quote(p)}" for p in plugins)

        runcmd.extend(
            [
                (
                    "curl -1sLf 'https://dl.cloudsmith.io/public/caddy/xcaddy/gpg.key'"
                    " | gpg --dearmor -o /usr/share/keyrings/caddy-xcaddy-archive-keyring.gpg"
                ),
                (
                    "curl -1sLf 'https://dl.cloudsmith.io/public/caddy/xcaddy/debian.deb.txt'"
                    " | tee /etc/apt/sources.list.d/caddy-xcaddy.list"
                ),
                "apt-get update",
                "apt-get install -y xcaddy",
                f"CADDY_VERSION={quoted_version} xcaddy build {build_args}",
                "install -m 0755 ./caddy /usr/local/bin/caddy",
                "systemctl daemon-reload",
                "systemctl enable caddy",
                "systemctl start caddy",
            ]
        )

    # ------------------------------------------------ assemble the document ---
    doc: dict = {
        "package_update": True,
        "package_upgrade": True,
        "package_reboot_if_required": True,
    }

    if hostname:
        doc["hostname"] = hostname

    doc["timezone"] = timezone
    doc["users"] = users
    doc["apt"] = apt
    doc["packages"] = packages
    doc["write_files"] = write_files
    doc["runcmd"] = runcmd

    if ssh_authorized_keys:
        doc["ssh_authorized_keys"] = ssh_authorized_keys

    yaml_body = yaml.dump(
        doc,
        Dumper=_OTSDumper,
        default_flow_style=False,
        allow_unicode=True,
        sort_keys=False,
    )

    # cloud-init requires the #cloud-config header before the YAML body
    return "#cloud-config\n" + yaml_body
