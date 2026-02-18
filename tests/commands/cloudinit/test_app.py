# tests/commands/cloudinit/test_app.py
"""Tests for cloud-init command app."""

import pytest
import yaml

from ots_containers.commands.cloudinit.app import app


class TestCloudInitGenerate:
    """Tests for cloudinit generate command."""

    def test_generate_to_stdout(self, capsys):
        """Generate should output to stdout by default."""
        with pytest.raises(SystemExit) as exc_info:
            app(["generate"])

        assert exc_info.value.code == 0

        captured = capsys.readouterr()
        assert "#cloud-config" in captured.out
        assert "Types: deb" in captured.out
        assert "trixie" in captured.out

    def test_generate_to_file(self, tmp_path, capsys):
        """Generate should write to specified file."""
        output_file = tmp_path / "cloud-init.yaml"

        with pytest.raises(SystemExit) as exc_info:
            app(["generate", "--output", str(output_file)])

        assert exc_info.value.code == 0

        captured = capsys.readouterr()
        assert "[created]" in captured.out

        assert output_file.exists()
        content = output_file.read_text()
        assert "#cloud-config" in content

        # Validate YAML
        data = yaml.safe_load(content)
        assert "apt" in data

    def test_generate_with_postgresql(self, capsys):
        """--include-postgresql without a key should exit with code 1."""
        with pytest.raises(SystemExit) as exc_info:
            app(["generate", "--include-postgresql"])

        assert exc_info.value.code == 1

        captured = capsys.readouterr()
        assert "postgresql" in captured.err.lower()
        assert "key" in captured.err.lower()

    def test_generate_with_valkey(self, capsys):
        """--include-valkey without a key should exit with code 1."""
        with pytest.raises(SystemExit) as exc_info:
            app(["generate", "--include-valkey"])

        assert exc_info.value.code == 1

        captured = capsys.readouterr()
        assert "valkey" in captured.err.lower()
        assert "key" in captured.err.lower()

    def test_generate_with_postgresql_key(self, tmp_path, capsys):
        """Generate with PostgreSQL key file should include key content."""
        key_file = tmp_path / "pgp.asc"
        key_content = (
            "-----BEGIN PGP PUBLIC KEY BLOCK-----\ntest-key\n-----END PGP PUBLIC KEY BLOCK-----"
        )
        key_file.write_text(key_content)

        with pytest.raises(SystemExit) as exc_info:
            app(["generate", "--include-postgresql", "--postgresql-key", str(key_file)])

        assert exc_info.value.code == 0

        captured = capsys.readouterr()
        output = captured.out
        assert "test-key" in output

    def test_generate_error_when_no_key_provided(self, capsys):
        """Should exit with code 1 and helpful message when key is missing."""
        with pytest.raises(SystemExit) as exc_info:
            app(["generate", "--include-postgresql"])

        assert exc_info.value.code == 1

        captured = capsys.readouterr()
        assert "postgresql-key" in captured.err.lower()
        assert "curl" in captured.err.lower()

    def test_generate_with_xcaddy(self, capsys):
        """Generate with xcaddy should include runcmd section."""
        with pytest.raises(SystemExit) as exc_info:
            app(["generate", "--include-xcaddy"])

        assert exc_info.value.code == 0

        captured = capsys.readouterr()
        output = captured.out

        data = yaml.safe_load(output)
        assert "runcmd" in data
        assert "debian-keyring" in data["packages"]
        # Build command should contain xcaddy
        build_cmds = [cmd for cmd in data["runcmd"] if "xcaddy build" in cmd]
        assert len(build_cmds) == 1

    def test_generate_with_xcaddy_custom_version(self, capsys):
        """Generate with xcaddy and custom version."""
        with pytest.raises(SystemExit) as exc_info:
            app(["generate", "--include-xcaddy", "--caddy-version", "v2.9.0"])

        assert exc_info.value.code == 0

        captured = capsys.readouterr()
        data = yaml.safe_load(captured.out)
        build_cmd = [cmd for cmd in data["runcmd"] if "xcaddy build" in cmd][0]
        assert "CADDY_VERSION=v2.9.0" in build_cmd


class TestCloudInitValidate:
    """Tests for cloudinit validate command."""

    def test_validate_valid_config(self, tmp_path, capsys):
        """Validate should pass for valid config."""
        config_file = tmp_path / "valid.yaml"
        config_file.write_text(
            """#cloud-config
package_update: true
apt:
  sources_list: |
    Types: deb
    URIs: http://deb.debian.org/debian
    Suites: trixie
    Components: main
    Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg
"""
        )

        with pytest.raises(SystemExit) as exc_info:
            app(["validate", str(config_file)])

        assert exc_info.value.code == 0

        captured = capsys.readouterr()
        assert "[ok]" in captured.out

    def test_validate_missing_file(self):
        """Validate should fail for missing file."""
        with pytest.raises(SystemExit) as exc_info:
            app(["validate", "/nonexistent/file.yaml"])

        assert exc_info.value.code == 1

    def test_validate_invalid_yaml(self, tmp_path):
        """Validate should fail for invalid YAML."""
        config_file = tmp_path / "invalid.yaml"
        config_file.write_text("invalid: yaml: syntax: [")

        with pytest.raises(SystemExit) as exc_info:
            app(["validate", str(config_file)])

        assert exc_info.value.code == 1

    def test_validate_warns_on_non_deb822_format(self, tmp_path, capsys):
        """Validate should warn if DEB822 format not detected."""
        config_file = tmp_path / "old-format.yaml"
        config_file.write_text(
            """#cloud-config
package_update: true
apt:
  sources_list: |
    deb http://deb.debian.org/debian trixie main
"""
        )

        # This should fail validation
        with pytest.raises(SystemExit) as exc_info:
            app(["validate", str(config_file)])

        assert exc_info.value.code == 1

        captured = capsys.readouterr()
        assert "DEB822" in captured.err


class TestCloudInitDefault:
    """Tests for cloudinit default command."""

    def test_default_shows_help(self, capsys):
        """Default command should show help info."""
        with pytest.raises(SystemExit) as exc_info:
            app([])

        assert exc_info.value.code == 0

        captured = capsys.readouterr()
        assert "Cloud-init" in captured.out
        assert "Debian 13" in captured.out
        assert "DEB822" in captured.out
