# src/ots_containers/commands/cloudinit/templates.py

"""Cloud-init configuration templates with Debian 13 DEB822 apt sources."""

DEFAULT_CADDY_VERSION = "v2.10.2"

DEFAULT_CADDY_PLUGINS = [
    "github.com/mholt/caddy-l4",
    "github.com/caddy-dns/hetzner",
    "github.com/caddy-dns/cloudflare",
    "github.com/digilolnet/caddy-bunny-ip",
]


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
) -> str:
    """Generate cloud-init YAML with Debian 13 DEB822-style apt sources.

    Args:
        include_postgresql: Include PostgreSQL official repository
        include_valkey: Include Valkey repository
        include_xcaddy: Include xcaddy repo and build custom Caddy binary
        postgresql_gpg_key: PostgreSQL GPG public key content
        valkey_gpg_key: Valkey GPG public key content
        caddy_version: Caddy version to build (default: v2.10.2)
        caddy_plugins: Caddy plugins to include (default: OTS web profile)

    Returns:
        Complete cloud-init YAML configuration as string
    """
    # Base configuration with Debian 13 main repositories
    # Get the sources list and indent it for YAML
    sources_list = get_debian13_sources_list().rstrip("\n")
    indented_sources = "\n".join(f"    {line}" for line in sources_list.split("\n"))

    config_parts = [
        "#cloud-config",
        "# Generated cloud-init configuration for OTS infrastructure",
        "# Debian 13 (Trixie) with DEB822-style apt sources",
        "",
        "package_update: true",
        "package_upgrade: true",
        "package_reboot_if_required: true",
        "",
        "apt:",
        "  sources_list: |",
        indented_sources,
    ]

    # Add third-party repositories if requested
    sources = []

    if include_postgresql:
        postgresql_source = {
            "source": "deb http://apt.postgresql.org/pub/repos/apt trixie-pgdg main",
        }
        if postgresql_gpg_key:
            postgresql_source["key"] = postgresql_gpg_key
        else:
            postgresql_source["key"] = "# PostgreSQL GPG key placeholder - replace with actual key"
        sources.append(("postgresql", postgresql_source))

    if include_valkey:
        valkey_source = {
            "source": "deb https://packages.valkey.io/deb/ trixie main",
        }
        if valkey_gpg_key:
            valkey_source["key"] = valkey_gpg_key
        else:
            valkey_source["key"] = "# Valkey GPG key placeholder - replace with actual key"
        sources.append(("valkey", valkey_source))

    # Add sources section if we have any
    if sources:
        config_parts.append("  sources:")
        for name, source_config in sources:
            config_parts.append(f"    {name}:")
            config_parts.append(f'      source: "{source_config["source"]}"')
            if "key" in source_config:
                # Multi-line key handling
                key_content = source_config["key"]
                if "\n" in key_content:
                    config_parts.append("      key: |")
                    for line in key_content.split("\n"):
                        config_parts.append(f"        {line}")
                else:
                    config_parts.append(f'      key: "{key_content}"')

    # Add common packages section
    config_parts.extend(
        [
            "",
            "packages:",
            "  - curl",
            "  - wget",
            "  - git",
            "  - vim",
            "  - podman",
            "  - systemd-container",
        ]
    )

    if include_postgresql:
        config_parts.append("  - postgresql-client")

    if include_valkey:
        config_parts.append("  - valkey")

    if include_xcaddy:
        config_parts.extend(
            [
                "  - debian-keyring",
                "  - debian-archive-keyring",
                "  - apt-transport-https",
                "  - gnupg",
            ]
        )

    # Add runcmd section for xcaddy repo setup and build
    if include_xcaddy:
        import shlex

        plugins = caddy_plugins if caddy_plugins is not None else DEFAULT_CADDY_PLUGINS
        quoted_version = shlex.quote(caddy_version)
        build_args = " ".join(f"--with {shlex.quote(p)}" for p in plugins)

        config_parts.extend(
            [
                "",
                "runcmd:",
                "  # xcaddy: add Cloudsmith apt repository",
                "  - >-",
                "    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/xcaddy/gpg.key'",
                "    | gpg --dearmor -o /usr/share/keyrings/caddy-xcaddy-archive-keyring.gpg",
                "  - >-",
                "    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/xcaddy/debian.deb.txt'",
                "    | tee /etc/apt/sources.list.d/caddy-xcaddy.list",
                "  - apt-get update",
                "  - apt-get install -y xcaddy",
                "  # Build custom Caddy binary with plugins",
                f"  - CADDY_VERSION={quoted_version} xcaddy build {build_args}",
                "  - install -m 0755 ./caddy /usr/local/bin/caddy",
            ]
        )

    return "\n".join(config_parts) + "\n"
