# tests/commands/cloudinit/test_templates.py
"""Tests for cloud-init template generation."""

import yaml

from rots.commands.cloudinit.templates import (
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
        """xcaddy should add runcmd with repo setup, build, and service enable."""
        config = generate_cloudinit_config(include_xcaddy=True)
        data = yaml.safe_load(config)

        assert "runcmd" in data
        runcmd = data["runcmd"]

        # 5 base commands + 9 xcaddy commands (gpg, repo, apt-get update, install,
        # build, install binary, daemon-reload, enable, start)
        assert len(runcmd) == 14

        # Base setup commands (always present)
        assert "mkdir -p" in runcmd[0]
        assert "chown" in runcmd[1]
        assert "podman.socket" in runcmd[2]
        assert "pip3 install" in runcmd[3]
        assert "rots init" in runcmd[4]

        # GPG key import (xcaddy commands start at index 5)
        assert "gpg.key" in runcmd[5]
        assert "caddy-xcaddy-archive-keyring.gpg" in runcmd[5]

        # Repo file
        assert "debian.deb.txt" in runcmd[6]
        assert "caddy-xcaddy.list" in runcmd[6]

        # apt-get update and install
        assert runcmd[7] == "apt-get update"
        assert runcmd[8] == "apt-get install -y xcaddy"

        # Build with default plugins
        assert "xcaddy build" in runcmd[9]
        for plugin in DEFAULT_CADDY_PLUGINS:
            assert f"--with {plugin}" in runcmd[9]

        # Install binary
        assert "install -m 0755" in runcmd[10]
        assert "/usr/local/bin/caddy" in runcmd[10]

        # Enable and start caddy service
        assert runcmd[11] == "systemctl daemon-reload"
        assert runcmd[12] == "systemctl enable caddy"
        assert runcmd[13] == "systemctl start caddy"

    def test_xcaddy_uses_default_caddy_version(self):
        """xcaddy build should use DEFAULT_CADDY_VERSION."""
        config = generate_cloudinit_config(include_xcaddy=True)
        data = yaml.safe_load(config)

        build_cmd = data["runcmd"][9]
        assert f"CADDY_VERSION={DEFAULT_CADDY_VERSION}" in build_cmd

    def test_xcaddy_custom_caddy_version(self):
        """xcaddy build should respect custom caddy version."""
        config = generate_cloudinit_config(include_xcaddy=True, caddy_version="v2.9.0")
        data = yaml.safe_load(config)

        build_cmd = data["runcmd"][9]
        assert "CADDY_VERSION=v2.9.0" in build_cmd

    def test_xcaddy_custom_plugins(self):
        """xcaddy build should use custom plugin list when provided."""
        plugins = ["github.com/caddy-dns/cloudflare"]
        config = generate_cloudinit_config(include_xcaddy=True, caddy_plugins=plugins)
        data = yaml.safe_load(config)

        build_cmd = data["runcmd"][9]
        assert "--with github.com/caddy-dns/cloudflare" in build_cmd
        # Default plugins should not be present
        assert "caddy-l4" not in build_cmd

    def test_no_xcaddy_means_no_caddy_runcmds(self):
        """Without xcaddy, runcmd only contains base setup commands (no caddy commands)."""
        config = generate_cloudinit_config()
        data = yaml.safe_load(config)

        runcmd = data.get("runcmd", [])
        # runcmd always has the 5 base setup commands
        assert len(runcmd) == 5
        assert "mkdir -p" in runcmd[0]
        assert "chown" in runcmd[1]
        # No xcaddy-specific commands
        assert not any("xcaddy" in cmd for cmd in runcmd)
        assert not any("caddy" in cmd for cmd in runcmd)

    def test_xcaddy_creates_caddy_user(self):
        """xcaddy should create a caddy system user."""
        config = generate_cloudinit_config(include_xcaddy=True)
        data = yaml.safe_load(config)

        assert "users" in data
        users = data["users"]
        caddy_user = next((u for u in users if u.get("name") == "caddy"), None)
        assert caddy_user is not None
        assert caddy_user["system"] is True
        assert caddy_user["shell"] == "/usr/sbin/nologin"
        assert caddy_user["home"] == "/var/lib/caddy"

    def test_xcaddy_writes_caddyfile(self):
        """xcaddy should write a Caddyfile via write_files."""
        config = generate_cloudinit_config(include_xcaddy=True)
        data = yaml.safe_load(config)

        assert "write_files" in data
        files = {f["path"]: f for f in data["write_files"]}
        assert "/etc/caddy/Caddyfile" in files
        caddyfile = files["/etc/caddy/Caddyfile"]
        assert caddyfile["owner"] == "caddy:caddy"
        assert "content" in caddyfile

    def test_xcaddy_writes_systemd_service(self):
        """xcaddy should write a caddy.service systemd unit via write_files."""
        config = generate_cloudinit_config(include_xcaddy=True)
        data = yaml.safe_load(config)

        assert "write_files" in data
        files = {f["path"]: f for f in data["write_files"]}
        assert "/etc/systemd/system/caddy.service" in files
        service = files["/etc/systemd/system/caddy.service"]
        assert service["owner"] == "root:root"
        content = service["content"]
        assert "ExecStart=/usr/local/bin/caddy" in content
        assert "User=caddy" in content
        assert "WantedBy=multi-user.target" in content

    def test_no_xcaddy_no_caddy_user_or_caddy_files(self):
        """Without xcaddy, no caddy user or caddy-specific write_files entries."""
        config = generate_cloudinit_config()
        data = yaml.safe_load(config)

        # users section always present (onetimesecret user), but no caddy user
        assert "users" in data
        user_names = [u.get("name") for u in data["users"]]
        assert "caddy" not in user_names

        # write_files always present (OTS env scaffold), but no caddy files
        assert "write_files" in data
        file_paths = [f["path"] for f in data["write_files"]]
        assert "/etc/caddy/Caddyfile" not in file_paths
        assert "/etc/systemd/system/caddy.service" not in file_paths

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


class TestOTSBaseConfig:
    """Tests for always-present OTS-specific sections added by the yaml.dump() rewrite."""

    def test_onetimesecret_user_always_created(self):
        """onetimesecret system user should always appear in the users section."""
        config = generate_cloudinit_config()
        data = yaml.safe_load(config)

        assert "users" in data
        ots_user = next((u for u in data["users"] if u.get("name") == "onetimesecret"), None)
        assert ots_user is not None
        assert ots_user["system"] is True
        assert ots_user["shell"] == "/usr/sbin/nologin"
        assert ots_user["no_create_home"] is True

    def test_ots_env_file_always_in_write_files(self):
        """write_files should always include /etc/default/onetimesecret."""
        config = generate_cloudinit_config()
        data = yaml.safe_load(config)

        assert "write_files" in data
        files = {f["path"]: f for f in data["write_files"]}
        assert "/etc/default/onetimesecret" in files

        env_entry = files["/etc/default/onetimesecret"]
        assert env_entry["owner"] == "root:onetimesecret"
        assert env_entry["permissions"] == "0640"
        content = env_entry["content"]
        assert "SECRET_VARIABLE_NAMES" in content
        assert "REDIS_URL" in content

    def test_base_runcmd_always_present(self):
        """Base OTS runcmd commands should always appear."""
        config = generate_cloudinit_config()
        data = yaml.safe_load(config)

        runcmd = data["runcmd"]
        assert len(runcmd) == 5

        assert "mkdir -p" in runcmd[0]
        assert "/etc/onetimesecret" in runcmd[0]
        assert "chown onetimesecret:onetimesecret" in runcmd[1]
        assert runcmd[2] == "systemctl enable --now podman.socket"
        assert runcmd[3] == "pip3 install rots"
        assert runcmd[4] == "rots init"

    def test_default_timezone_is_utc(self):
        """Default timezone should be UTC."""
        config = generate_cloudinit_config()
        data = yaml.safe_load(config)
        assert data["timezone"] == "UTC"

    def test_custom_timezone(self):
        """Specified timezone should appear in the output."""
        config = generate_cloudinit_config(timezone="America/New_York")
        data = yaml.safe_load(config)
        assert data["timezone"] == "America/New_York"

    def test_hostname_absent_by_default(self):
        """hostname key should not appear when not specified."""
        config = generate_cloudinit_config()
        data = yaml.safe_load(config)
        assert "hostname" not in data

    def test_custom_hostname_included(self):
        """Specified hostname should appear in the output."""
        config = generate_cloudinit_config(hostname="ots-prod-1")
        data = yaml.safe_load(config)
        assert data["hostname"] == "ots-prod-1"

    def test_ssh_authorized_keys_absent_by_default(self):
        """ssh_authorized_keys should not appear when not specified."""
        config = generate_cloudinit_config()
        data = yaml.safe_load(config)
        assert "ssh_authorized_keys" not in data

    def test_single_ssh_authorized_key(self):
        """A single provided SSH key should appear in the output."""
        key = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAA test@example.com"
        config = generate_cloudinit_config(ssh_authorized_keys=[key])
        data = yaml.safe_load(config)
        assert "ssh_authorized_keys" in data
        assert data["ssh_authorized_keys"] == [key]

    def test_multiple_ssh_authorized_keys(self):
        """Multiple SSH keys should all appear in the output."""
        keys = [
            "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAA key1@example.com",
            "ssh-rsa AAAAB3NzaC1yc2EAAA key2@example.com",
        ]
        config = generate_cloudinit_config(ssh_authorized_keys=keys)
        data = yaml.safe_load(config)
        assert data["ssh_authorized_keys"] == keys

    def test_uses_yaml_dump_not_string_concat(self):
        """Output must be valid YAML produced by yaml.dump (not fragile string concat)."""
        config = generate_cloudinit_config(
            include_postgresql=True,
            include_valkey=True,
            postgresql_gpg_key=_DUMMY_GPG_KEY,
            valkey_gpg_key=_DUMMY_GPG_KEY,
            timezone="Europe/Berlin",
            hostname="ots-test",
            ssh_authorized_keys=["ssh-ed25519 AAAA test@host"],
        )
        # yaml.safe_load must succeed without error
        data = yaml.safe_load(config)
        assert isinstance(data, dict)
        # Spot-check all new sections present and correctly typed
        assert data["timezone"] == "Europe/Berlin"
        assert data["hostname"] == "ots-test"
        assert isinstance(data["users"], list)
        assert isinstance(data["write_files"], list)
        assert isinstance(data["runcmd"], list)
        assert data["ssh_authorized_keys"] == ["ssh-ed25519 AAAA test@host"]
