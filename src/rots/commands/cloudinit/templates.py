# packages/rots/src/rots/commands/cloudinit/templates.py

"""Cloud-init configuration templates using a Composition/Builder pattern."""

import shlex
from dataclasses import dataclass, field

import yaml

DEFAULT_CADDY_VERSION = "v2.10.2"

DEFAULT_CADDY_PLUGINS = [
    "github.com/mholt/caddy-l4",
    "github.com/caddy-dns/hetzner",
    "github.com/caddy-dns/cloudflare",
    "github.com/digilolnet/caddy-bunny-ip",
]

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
    """Get just the Debian 13 DEB822 sources.list content."""
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


@dataclass
class CloudInitBuilder:
    """Builder pattern for programmatic generation of cloud-init configurations."""

    hostname: str | None = None
    timezone: str = "UTC"
    packages: set[str] = field(default_factory=set)
    runcmds: list[str] = field(default_factory=list)
    write_files: list[dict] = field(default_factory=list)
    users: list[dict] = field(default_factory=list)
    apt_sources: dict = field(default_factory=dict)
    ssh_authorized_keys: list[str] = field(default_factory=list)

    def add_package(self, pkg: str) -> "CloudInitBuilder":
        self.packages.add(pkg)
        return self

    def add_runcmd(self, cmd: str) -> "CloudInitBuilder":
        self.runcmds.append(cmd)
        return self

    def add_file(
        self,
        path: str,
        content: str,
        owner: str = "root:root",
        permissions: str = "0644",
        append: bool = False,
        defer: bool = False,
    ) -> "CloudInitBuilder":
        file_def = {
            "path": path,
            "owner": owner,
            "permissions": permissions,
            "content": _LiteralStr(content),
        }
        if append:
            file_def["append"] = True
        if defer:
            file_def["defer"] = True
        self.write_files.append(file_def)
        return self

    def add_apt_source(self, name: str, source: str, key: str | None = None) -> "CloudInitBuilder":
        self.apt_sources[name] = {"source": source}
        if key:
            self.apt_sources[name]["key"] = _LiteralStr(key)
        return self

    def add_user(
        self,
        name: str,
        groups: list[str] | None = None,
        shell: str = "/bin/bash",
        ssh_keys: list[str] | None = None,
        **kwargs,
    ) -> "CloudInitBuilder":
        user_def: dict = {"name": name, "shell": shell}
        if groups:
            user_def["groups"] = ", ".join(groups)
        if ssh_keys:
            user_def["ssh_authorized_keys"] = ssh_keys
        user_def.update(kwargs)
        self.users.append(user_def)
        return self

    def build(self) -> str:
        apt: dict = {"sources_list": _LiteralStr(get_debian13_sources_list())}
        if self.apt_sources:
            apt["sources"] = self.apt_sources

        doc: dict = {
            "package_update": True,
            "package_upgrade": True,
            "package_reboot_if_required": True,
        }

        if self.hostname:
            doc["hostname"] = self.hostname

        doc["timezone"] = self.timezone

        if self.users:
            doc["users"] = self.users

        doc["apt"] = apt
        doc["packages"] = sorted(list(self.packages))

        if self.write_files:
            doc["write_files"] = self.write_files

        if self.runcmds:
            doc["runcmd"] = self.runcmds

        if self.ssh_authorized_keys:
            doc["ssh_authorized_keys"] = self.ssh_authorized_keys

        yaml_body = yaml.dump(
            doc,
            Dumper=_OTSDumper,
            default_flow_style=False,
            allow_unicode=True,
            sort_keys=False,
        )

        return "#cloud-config\n" + yaml_body


# --- Feature Compositions ---


def configure_base_ots(builder: CloudInitBuilder) -> None:
    """Configures the foundational tools and environment for OneTimeSecret."""
    for pkg in [
        "curl",
        "wget",
        "git",
        "vim",
        "podman",
        "systemd-container",
        "pipx",
    ]:
        builder.add_package(pkg)

    builder.add_user(
        name="onetimesecret",
        system=True,
        shell="/usr/sbin/nologin",
        no_create_home=True,
        groups=[],
    )

    builder.add_file(
        path="/etc/default/onetimesecret",
        owner="root:onetimesecret",
        permissions="0640",
        content=_DEFAULT_OTS_ENV_CONTENT,
    )

    cmds = [
        "mkdir -p /etc/onetimesecret /var/lib/onetimesecret",
        "chown onetimesecret:onetimesecret /etc/onetimesecret /var/lib/onetimesecret",
        "systemctl enable --now podman.socket",
        "pipx install rots",
        "pipx ensurepath",
        "/root/.local/bin/rots init",
        "/root/.local/bin/rots sidecar install",
    ]
    for cmd in cmds:
        builder.add_runcmd(cmd)


def configure_postgresql(
    builder: CloudInitBuilder, server: bool = False, gpg_key: str | None = None
) -> None:
    """Configures PostgreSQL repository and packages."""
    if not gpg_key:
        raise ValueError(
            "PostgreSQL GPG key is required.\n"
            "Obtain the key with:\n"
            "  curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc\n"
            "Then pass it with: --postgresql-key /path/to/key.asc"
        )

    builder.add_apt_source(
        name="postgresql",
        source="deb http://apt.postgresql.org/pub/repos/apt trixie-pgdg main",
        key=gpg_key,
    )

    if server:
        builder.add_package("postgresql-17")
    else:
        builder.add_package("postgresql-client")


def configure_valkey(builder: CloudInitBuilder, gpg_key: str | None = None) -> None:
    """Configures Valkey repository and packages."""
    if not gpg_key:
        raise ValueError(
            "Valkey GPG key is required.\n"
            "Obtain the key with:\n"
            "  curl -fsSL https://packages.valkey.io/valkey.gpg\n"
            "Then pass it with: --valkey-key /path/to/key.gpg"
        )

    builder.add_apt_source(
        name="valkey",
        source="deb https://packages.valkey.io/deb/ trixie main",
        key=gpg_key,
    )
    builder.add_package("valkey")


def configure_rabbitmq(builder: CloudInitBuilder, gpg_key: str | None = None) -> None:
    """Configures RabbitMQ repository and dependencies."""
    if not gpg_key:
        raise ValueError(
            "RabbitMQ GPG key is required.\n"
            "Obtain the key with:\n"
            '  curl -1sLf "https://keys.openpgp.org/vks/v1/by-fingerprint/'
            '0A9AF2115F4687BD29803A206B73A36E6026DFCA" | gpg --dearmor\n'
            "Then pass it with: --rabbitmq-key /path/to/key.gpg"
        )

    builder.add_package("apt-transport-https")
    builder.add_package("gnupg")

    builder.add_apt_source(
        name="rabbitmq",
        source=(
            "deb [arch=amd64] https://deb1.rabbitmq.com/rabbitmq-server/debian/trixie trixie main\n"
            "deb [arch=amd64] https://deb2.rabbitmq.com/rabbitmq-server/debian/trixie trixie main"
        ),
        key=gpg_key,
    )

    for pkg in [
        "erlang-base",
        "erlang-asn1",
        "erlang-crypto",
        "erlang-eldap",
        "erlang-ftp",
        "erlang-inets",
        "erlang-mnesia",
        "erlang-os-mon",
        "erlang-parsetools",
        "erlang-public-key",
        "erlang-runtime-tools",
        "erlang-snmp",
        "erlang-ssl",
        "erlang-syntax-tools",
        "erlang-tftp",
        "erlang-tools",
        "erlang-xmerl",
        "rabbitmq-server",
    ]:
        builder.add_package(pkg)


def configure_xcaddy(
    builder: CloudInitBuilder,
    version: str = DEFAULT_CADDY_VERSION,
    plugins: list[str] | None = None,
) -> None:
    """Configures Caddy building via xcaddy."""
    builder.add_package("apt-transport-https")
    builder.add_package("gnupg")
    builder.add_package("debian-keyring")
    builder.add_package("debian-archive-keyring")

    builder.add_user(
        name="caddy",
        system=True,
        shell="/usr/sbin/nologin",
        home="/var/lib/caddy",
        create_home=True,
        groups=[],
    )

    builder.add_file(
        path="/etc/caddy/Caddyfile",
        owner="caddy:caddy",
        content=_DEFAULT_CADDYFILE_CONTENT,
    )

    builder.add_file(
        path="/etc/systemd/system/caddy.service",
        content=_DEFAULT_CADDY_SERVICE_CONTENT,
    )

    plugins_list = plugins if plugins is not None else DEFAULT_CADDY_PLUGINS
    quoted_version = shlex.quote(version)
    build_args = " ".join(f"--with {shlex.quote(p)}" for p in plugins_list)

    cmds = [
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
    for cmd in cmds:
        builder.add_runcmd(cmd)
