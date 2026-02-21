# tests/commands/service/test_helpers.py
"""Tests for service command helpers."""

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from ots_containers.commands.service._helpers import (
    add_secrets_include,
    copy_default_config,
    create_secrets_file,
    ensure_data_dir,
    ensure_instances_dir,
    is_service_active,
    is_service_enabled,
    systemctl,
    systemctl_json,
    update_config_value,
)
from ots_containers.commands.service.packages import ServicePackage


class TestEnsureInstancesDir:
    """Tests for ensure_instances_dir function."""

    def test_creates_directory(self, tmp_path):
        """Test creates instances directory if not exists."""
        pkg = ServicePackage(
            name="test",
            template="test@",
            config_dir=tmp_path / "etc" / "test",
            data_dir=tmp_path / "var" / "test",
        )

        result = ensure_instances_dir(pkg)

        assert result.exists()
        assert result == tmp_path / "etc" / "test" / "instances"

    def test_returns_existing_directory(self, tmp_path):
        """Test returns existing directory."""
        instances_dir = tmp_path / "etc" / "test" / "instances"
        instances_dir.mkdir(parents=True)

        pkg = ServicePackage(
            name="test",
            template="test@",
            config_dir=tmp_path / "etc" / "test",
            data_dir=tmp_path / "var" / "test",
        )

        result = ensure_instances_dir(pkg)

        assert result == instances_dir


class TestCopyDefaultConfig:
    """Tests for copy_default_config function."""

    def test_copies_config_file(self, tmp_path):
        """Test copies default config to instance config."""
        # Create default config
        default_config = tmp_path / "default.conf"
        default_config.write_text("key value\n")

        pkg = ServicePackage(
            name="test",
            template="test@",
            config_dir=tmp_path / "etc" / "test",
            data_dir=tmp_path / "var" / "test",
            default_config=default_config,
        )

        result = copy_default_config(pkg, "6379")

        assert result.exists()
        assert result.read_text() == "key value\n"
        assert result == tmp_path / "etc" / "test" / "instances" / "6379.conf"

    def test_raises_if_default_config_missing(self, tmp_path):
        """Test raises FileNotFoundError if default config missing."""
        pkg = ServicePackage(
            name="test",
            template="test@",
            config_dir=tmp_path / "etc" / "test",
            data_dir=tmp_path / "var" / "test",
            default_config=tmp_path / "nonexistent.conf",
        )

        with pytest.raises(FileNotFoundError):
            copy_default_config(pkg, "6379")

    def test_raises_if_dest_exists(self, tmp_path):
        """Test raises FileExistsError if destination exists."""
        default_config = tmp_path / "default.conf"
        default_config.write_text("key value\n")

        pkg = ServicePackage(
            name="test",
            template="test@",
            config_dir=tmp_path / "etc" / "test",
            data_dir=tmp_path / "var" / "test",
            default_config=default_config,
        )

        # Create destination
        dest = pkg.config_file("6379")
        dest.parent.mkdir(parents=True)
        dest.write_text("existing")

        with pytest.raises(FileExistsError):
            copy_default_config(pkg, "6379")


class TestUpdateConfigValue:
    """Tests for update_config_value function."""

    def test_updates_existing_key(self, tmp_path):
        """Test updates existing config key."""
        config_file = tmp_path / "test.conf"
        config_file.write_text("port 6379\nbind 127.0.0.1\n")

        pkg = ServicePackage(
            name="test",
            template="test@",
            config_dir=tmp_path,
            data_dir=tmp_path,
            config_format="space",
        )

        update_config_value(config_file, "port", "6380", pkg)

        content = config_file.read_text()
        assert "port 6380" in content
        assert "port 6379" not in content

    def test_adds_new_key(self, tmp_path):
        """Test adds new config key if not exists."""
        config_file = tmp_path / "test.conf"
        config_file.write_text("port 6379\n")

        pkg = ServicePackage(
            name="test",
            template="test@",
            config_dir=tmp_path,
            data_dir=tmp_path,
            config_format="space",
        )

        update_config_value(config_file, "bind", "0.0.0.0", pkg)

        content = config_file.read_text()
        assert "bind 0.0.0.0" in content

    def test_skips_comments(self, tmp_path):
        """Test skips comment lines."""
        config_file = tmp_path / "test.conf"
        config_file.write_text("# port 1234\nport 6379\n")

        pkg = ServicePackage(
            name="test",
            template="test@",
            config_dir=tmp_path,
            data_dir=tmp_path,
            config_format="space",
        )

        update_config_value(config_file, "port", "6380", pkg)

        content = config_file.read_text()
        assert "# port 1234" in content  # Comment preserved
        assert "port 6380" in content  # Actual value updated


class TestCreateSecretsFile:
    """Tests for create_secrets_file function."""

    def test_creates_secrets_file(self, tmp_path):
        """Test creates secrets file with correct permissions."""
        from ots_containers.commands.service.packages import SecretConfig

        pkg = ServicePackage(
            name="test",
            template="test@",
            config_dir=tmp_path / "etc" / "test",
            data_dir=tmp_path / "var" / "test",
            secrets=SecretConfig(
                secret_keys=("requirepass",),
                secrets_file_pattern="{instance}.secrets",
            ),
        )

        result = create_secrets_file(pkg, "6379", {"requirepass": "secret123"})

        assert result is not None
        assert result.exists()
        content = result.read_text()
        assert "requirepass secret123" in content

    def test_returns_none_without_secrets_config(self, tmp_path):
        """Test returns None if package has no secrets config."""
        pkg = ServicePackage(
            name="test",
            template="test@",
            config_dir=tmp_path / "etc" / "test",
            data_dir=tmp_path / "var" / "test",
            secrets=None,
        )

        result = create_secrets_file(pkg, "6379")

        assert result is None


class TestAddSecretsInclude:
    """Tests for add_secrets_include function."""

    def test_adds_include_directive(self, tmp_path):
        """Test adds include directive to config file."""
        from ots_containers.commands.service.packages import SecretConfig

        config_file = tmp_path / "test.conf"
        config_file.write_text("port 6379\n")
        secrets_file = tmp_path / "test.secrets"

        pkg = ServicePackage(
            name="test",
            template="test@",
            config_dir=tmp_path,
            data_dir=tmp_path,
            secrets=SecretConfig(include_directive="include {secrets_path}"),
        )

        add_secrets_include(config_file, secrets_file, pkg)

        content = config_file.read_text()
        assert f"include {secrets_file}" in content

    def test_skips_if_already_present(self, tmp_path):
        """Test skips if include already present."""
        from ots_containers.commands.service.packages import SecretConfig

        secrets_file = tmp_path / "test.secrets"
        config_file = tmp_path / "test.conf"
        config_file.write_text(f"port 6379\ninclude {secrets_file}\n")

        pkg = ServicePackage(
            name="test",
            template="test@",
            config_dir=tmp_path,
            data_dir=tmp_path,
            secrets=SecretConfig(include_directive="include {secrets_path}"),
        )

        add_secrets_include(config_file, secrets_file, pkg)

        content = config_file.read_text()
        # Should not duplicate
        assert content.count(f"include {secrets_file}") == 1


class TestEnsureDataDir:
    """Tests for ensure_data_dir function."""

    def test_creates_data_directory(self, tmp_path):
        """Test creates data directory."""
        pkg = ServicePackage(
            name="test",
            template="test@",
            config_dir=tmp_path / "etc" / "test",
            data_dir=tmp_path / "var" / "test",
        )

        result = ensure_data_dir(pkg, "6379")

        assert result.exists()
        assert result == tmp_path / "var" / "test" / "6379"


class TestSystemctl:
    """Tests for systemctl wrapper function."""

    @patch("subprocess.run")
    def test_calls_subprocess_run(self, mock_run):
        """Test systemctl calls subprocess.run."""
        mock_run.return_value = MagicMock(returncode=0)

        systemctl("status", "test.service")

        mock_run.assert_called_once()
        call_args = mock_run.call_args[0][0]
        assert call_args == ["systemctl", "status", "test.service"]

    @patch("subprocess.run")
    def test_raises_on_failure_by_default(self, mock_run):
        """Test raises CalledProcessError on failure by default."""
        mock_run.side_effect = subprocess.CalledProcessError(1, "systemctl")

        with pytest.raises(subprocess.CalledProcessError):
            systemctl("start", "test.service")

    @patch("subprocess.run")
    def test_no_raise_when_check_false(self, mock_run):
        """Test does not raise when check=False."""
        mock_run.return_value = MagicMock(returncode=1)

        result = systemctl("status", "test.service", check=False)

        assert result.returncode == 1


class TestSystemctlJson:
    """Tests for systemctl_json function."""

    @patch("subprocess.run")
    def test_returns_parsed_json(self, mock_run):
        """Test returns parsed JSON output."""
        mock_run.return_value = MagicMock(stdout='{"key": "value"}', returncode=0)

        result = systemctl_json("show", "test.service")

        assert result == {"key": "value"}

    @patch("subprocess.run")
    def test_returns_none_on_failure(self, mock_run):
        """Test returns None on command failure."""
        mock_run.return_value = MagicMock(stdout="", stderr="error", returncode=1)

        result = systemctl_json("show", "test.service")

        assert result is None


class TestIsServiceActive:
    """Tests for is_service_active function."""

    @patch("ots_containers.commands.service._helpers.systemctl")
    def test_returns_true_when_active(self, mock_systemctl):
        """Test returns True when service is active."""
        mock_systemctl.return_value = MagicMock(returncode=0)

        result = is_service_active("test.service")

        assert result is True

    @patch("ots_containers.commands.service._helpers.systemctl")
    def test_returns_false_when_inactive(self, mock_systemctl):
        """Test returns False when service is inactive."""
        mock_systemctl.return_value = MagicMock(returncode=3)

        result = is_service_active("test.service")

        assert result is False


class TestIsServiceEnabled:
    """Tests for is_service_enabled function."""

    @patch("ots_containers.commands.service._helpers.systemctl")
    def test_returns_true_when_enabled(self, mock_systemctl):
        """Test returns True when service is enabled."""
        mock_systemctl.return_value = MagicMock(returncode=0)

        result = is_service_enabled("test.service")

        assert result is True

    @patch("ots_containers.commands.service._helpers.systemctl")
    def test_returns_false_when_disabled(self, mock_systemctl):
        """Test returns False when service is disabled."""
        mock_systemctl.return_value = MagicMock(returncode=1)

        result = is_service_enabled("test.service")

        assert result is False
