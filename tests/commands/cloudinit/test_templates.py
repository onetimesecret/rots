# tests/commands/cloudinit/test_templates.py
"""Tests for cloud-init template generation."""

import yaml

from ots_containers.commands.cloudinit.templates import (
    DEFAULT_CADDY_PLUGINS,
    DEFAULT_CADDY_VERSION,
    generate_cloudinit_config,
    get_debian13_sources_list,
)

_DUMMY_GPG_KEY = (
    "-----BEGIN PGP PUBLIC KEY BLOCK-----\ntest-key\n-----END PGP PUBLIC KEY BLOCK-----"
)


class TestGenerateCloudInitConfig:
    """Tests for generate_cloudinit_config function."""

    def test_basic_config_generation(self):
        """Basic config should include Debian 13 repositories."""
        config = generate_cloudinit_config()

        # Parse as YAML
        data = yaml.safe_load(config)

        # Check basic structure
        assert data["package_update"] is True
        assert data["package_upgrade"] is True
        assert data["package_reboot_if_required"] is True

        # Check apt sources_list exists and has DEB822 format
        assert "apt" in data
        assert "sources_list" in data["apt"]
        sources_list = data["apt"]["sources_list"]

        # Verify DEB822 format
        assert "Types: deb" in sources_list
        assert "URIs: http://deb.debian.org/debian" in sources_list
        assert "Suites: trixie" in sources_list
        assert "Components: main contrib non-free non-free-firmware" in sources_list
        assert "Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg" in sources_list

        # Verify all three main sources
        assert "trixie trixie-updates" in sources_list
        assert "trixie-backports" in sources_list
        assert "trixie-security" in sources_list
        assert "http://security.debian.org/debian-security" in sources_list

    def test_config_with_postgresql(self):
        """Config with PostgreSQL should include apt source."""
        config = generate_cloudinit_config(
            include_postgresql=True,
            postgresql_gpg_key=_DUMMY_GPG_KEY,
        )
        data = yaml.safe_load(config)

        assert "apt" in data
        assert "sources" in data["apt"]
        assert "postgresql" in data["apt"]["sources"]

        pg_source = data["apt"]["sources"]["postgresql"]
        assert "source" in pg_source
        assert "trixie-pgdg" in pg_source["source"]
        assert "key" in pg_source

        # Check packages
        assert "packages" in data
        assert "postgresql-client" in data["packages"]

    def test_config_with_valkey(self):
        """Config with Valkey should include apt source."""
        config = generate_cloudinit_config(
            include_valkey=True,
            valkey_gpg_key=_DUMMY_GPG_KEY,
        )
        data = yaml.safe_load(config)

        assert "apt" in data
        assert "sources" in data["apt"]
        assert "valkey" in data["apt"]["sources"]

        valkey_source = data["apt"]["sources"]["valkey"]
        assert "source" in valkey_source
        assert "valkey" in valkey_source["source"]
        assert "key" in valkey_source

        # Check packages
        assert "packages" in data
        assert "valkey" in data["packages"]

    def test_config_with_custom_gpg_keys(self):
        """Config should use provided GPG keys."""
        pg_key = (
            "-----BEGIN PGP PUBLIC KEY BLOCK-----\ntest-pg-key\n-----END PGP PUBLIC KEY BLOCK-----"
        )
        valkey_key = (
            "-----BEGIN PGP PUBLIC KEY BLOCK-----\n"
            "test-valkey-key\n"
            "-----END PGP PUBLIC KEY BLOCK-----"
        )

        config = generate_cloudinit_config(
            include_postgresql=True,
            include_valkey=True,
            postgresql_gpg_key=pg_key,
            valkey_gpg_key=valkey_key,
        )

        # Raw content check (since YAML parsing handles multiline differently)
        assert "test-pg-key" in config
        assert "test-valkey-key" in config

    def test_config_includes_common_packages(self):
        """Config should include common packages."""
        config = generate_cloudinit_config()
        data = yaml.safe_load(config)

        assert "packages" in data
        packages = data["packages"]

        # Check common packages
        assert "curl" in packages
        assert "wget" in packages
        assert "git" in packages
        assert "vim" in packages
        assert "podman" in packages
        assert "systemd-container" in packages

    def test_valid_yaml_output(self):
        """Generated config should be valid YAML."""
        config = generate_cloudinit_config(
            include_postgresql=True,
            include_valkey=True,
            postgresql_gpg_key=_DUMMY_GPG_KEY,
            valkey_gpg_key=_DUMMY_GPG_KEY,
        )

        # Should not raise
        data = yaml.safe_load(config)
        assert isinstance(data, dict)

    def test_config_starts_with_cloud_config_marker(self):
        """Config should start with #cloud-config."""
        config = generate_cloudinit_config()
        assert config.startswith("#cloud-config\n")


class TestGetDebian13SourcesList:
    """Tests for get_debian13_sources_list function."""

    def test_returns_deb822_format(self):
        """Should return DEB822 formatted sources."""
        sources = get_debian13_sources_list()

        assert "Types: deb" in sources
        assert "URIs: http://deb.debian.org/debian" in sources
        assert "Suites: trixie" in sources
        assert "Components: main contrib non-free non-free-firmware" in sources

    def test_includes_all_debian_sources(self):
        """Should include main, backports, and security."""
        sources = get_debian13_sources_list()

        assert "trixie trixie-updates" in sources
        assert "trixie-backports" in sources
        assert "trixie-security" in sources
        assert "http://security.debian.org/debian-security" in sources

    def test_sources_separated_by_blank_lines(self):
        """DEB822 sources should be separated by blank lines."""
        sources = get_debian13_sources_list()
        # Should have blank lines between source blocks
        assert "\n\nTypes: deb" in sources


class TestXcaddyCloudInit:
    """Tests for xcaddy cloud-init template generation."""

    def test_xcaddy_adds_prereq_packages(self):
        """xcaddy should add keyring and transport packages."""
        config = generate_cloudinit_config(include_xcaddy=True)
        data = yaml.safe_load(config)

        packages = data["packages"]
        assert "debian-keyring" in packages
        assert "debian-archive-keyring" in packages
        assert "apt-transport-https" in packages
        assert "gnupg" in packages

    def test_xcaddy_adds_runcmd_section(self):
        """xcaddy should add runcmd with repo setup and build."""
        config = generate_cloudinit_config(include_xcaddy=True)
        data = yaml.safe_load(config)

        assert "runcmd" in data
        runcmd = data["runcmd"]

        # Should have 2 base setup commands plus 6 xcaddy commands
        assert len(runcmd) == 8

        # Base setup commands (always present)
        assert "mkdir -p" in runcmd[0]
        assert "chown" in runcmd[1]

        # GPG key import
        assert "gpg.key" in runcmd[2]
        assert "caddy-xcaddy-archive-keyring.gpg" in runcmd[2]

        # Repo file
        assert "debian.deb.txt" in runcmd[3]
        assert "caddy-xcaddy.list" in runcmd[3]

        # apt-get update and install
        assert runcmd[4] == "apt-get update"
        assert runcmd[5] == "apt-get install -y xcaddy"

        # Build with default plugins
        assert "xcaddy build" in runcmd[6]
        for plugin in DEFAULT_CADDY_PLUGINS:
            assert f"--with {plugin}" in runcmd[6]

        # Install binary
        assert "install -m 0755" in runcmd[7]
        assert "/usr/local/bin/caddy" in runcmd[7]

    def test_xcaddy_uses_default_caddy_version(self):
        """xcaddy build should use DEFAULT_CADDY_VERSION."""
        config = generate_cloudinit_config(include_xcaddy=True)
        data = yaml.safe_load(config)

        build_cmd = data["runcmd"][6]
        assert f"CADDY_VERSION={DEFAULT_CADDY_VERSION}" in build_cmd

    def test_xcaddy_custom_caddy_version(self):
        """xcaddy build should respect custom caddy version."""
        config = generate_cloudinit_config(include_xcaddy=True, caddy_version="v2.9.0")
        data = yaml.safe_load(config)

        build_cmd = data["runcmd"][6]
        assert "CADDY_VERSION=v2.9.0" in build_cmd

    def test_xcaddy_custom_plugins(self):
        """xcaddy build should use custom plugin list when provided."""
        plugins = ["github.com/caddy-dns/cloudflare"]
        config = generate_cloudinit_config(include_xcaddy=True, caddy_plugins=plugins)
        data = yaml.safe_load(config)

        build_cmd = data["runcmd"][6]
        assert "--with github.com/caddy-dns/cloudflare" in build_cmd
        # Default plugins should not be present
        assert "caddy-l4" not in build_cmd

    def test_no_xcaddy_means_no_runcmd(self):
        """Without xcaddy, runcmd only contains base setup commands (mkdir/chown)."""
        config = generate_cloudinit_config()
        data = yaml.safe_load(config)

        runcmd = data.get("runcmd", [])
        # runcmd always has the 2 base setup commands
        assert len(runcmd) == 2
        assert "mkdir -p" in runcmd[0]
        assert "chown" in runcmd[1]
        # No xcaddy-specific commands
        assert not any("xcaddy" in cmd for cmd in runcmd)
        assert not any("caddy" in cmd for cmd in runcmd)

    def test_xcaddy_valid_yaml(self):
        """Config with xcaddy should produce valid YAML."""
        config = generate_cloudinit_config(
            include_xcaddy=True,
            include_postgresql=True,
            include_valkey=True,
            postgresql_gpg_key=_DUMMY_GPG_KEY,
            valkey_gpg_key=_DUMMY_GPG_KEY,
        )
        data = yaml.safe_load(config)
        assert isinstance(data, dict)
        assert "runcmd" in data
        assert "apt" in data
