# tests/commands/service/test_packages.py
"""Tests for service package registry."""

from pathlib import Path

import pytest

from ots_containers.commands.service.packages import (
    PACKAGES,
    REDIS,
    VALKEY,
    SecretConfig,
    ServicePackage,
    get_package,
    list_packages,
)


class TestSecretConfig:
    """Tests for SecretConfig dataclass."""

    def test_default_values(self):
        """Test default values are set correctly."""
        config = SecretConfig()
        assert config.secret_keys == ()
        assert config.secrets_file_pattern == "{instance}.secrets"
        assert config.include_directive == "include {secrets_path}"
        assert config.config_with_secrets_mode == 0o600
        assert config.secrets_file_mode == 0o600
        assert config.secrets_owned_by_service is True

    def test_custom_values(self):
        """Test custom values can be set."""
        config = SecretConfig(
            secret_keys=("password", "token"),
            secrets_file_pattern="{instance}.secret",
            include_directive=None,
            config_with_secrets_mode=0o640,
            secrets_file_mode=0o400,
            secrets_owned_by_service=False,
        )
        assert config.secret_keys == ("password", "token")
        assert config.secrets_file_pattern == "{instance}.secret"
        assert config.include_directive is None
        assert config.config_with_secrets_mode == 0o640
        assert config.secrets_file_mode == 0o400
        assert config.secrets_owned_by_service is False

    def test_frozen(self):
        """Test SecretConfig is immutable (frozen dataclass)."""
        from dataclasses import FrozenInstanceError

        config = SecretConfig()
        with pytest.raises(FrozenInstanceError):
            setattr(config, "secret_keys", ("new",))


class TestServicePackage:
    """Tests for ServicePackage dataclass."""

    def test_valkey_package_exists(self):
        """Test VALKEY package is defined correctly."""
        assert VALKEY.name == "valkey"
        assert VALKEY.template == "valkey-server@"
        assert VALKEY.config_dir == Path("/etc/valkey")
        assert VALKEY.data_dir == Path("/var/lib/valkey")
        assert VALKEY.default_port == 6379

    def test_redis_package_exists(self):
        """Test REDIS package is defined correctly."""
        assert REDIS.name == "redis"
        assert REDIS.template == "redis-server@"
        assert REDIS.config_dir == Path("/etc/redis")
        assert REDIS.data_dir == Path("/var/lib/redis")
        assert REDIS.default_port == 6379

    def test_instances_dir_property(self):
        """Test instances_dir derived property."""
        assert VALKEY.instances_dir == Path("/etc/valkey/instances")
        assert REDIS.instances_dir == Path("/etc/redis/instances")

    def test_template_unit_property(self):
        """Test template_unit derived property."""
        assert VALKEY.template_unit == "valkey-server@.service"
        assert REDIS.template_unit == "redis-server@.service"

    def test_instance_unit(self):
        """Test instance_unit method."""
        assert VALKEY.instance_unit("6379") == "valkey-server@6379.service"
        assert REDIS.instance_unit("6380") == "redis-server@6380.service"

    def test_config_file(self):
        """Test config_file method."""
        assert VALKEY.config_file("6379") == Path("/etc/valkey/valkey-6379.conf")
        assert REDIS.config_file("6380") == Path("/etc/redis/instances/6380.conf")

    def test_secrets_file(self):
        """Test secrets_file method."""
        assert VALKEY.secrets_file("6379") == Path("/etc/valkey/valkey-6379.secrets")
        assert REDIS.secrets_file("6380") == Path("/etc/redis/instances/6380.secrets")

    def test_secrets_file_returns_none_without_secrets(self):
        """Test secrets_file returns None if no secrets config."""
        pkg = ServicePackage(
            name="test",
            template="test@",
            config_dir=Path("/etc/test"),
            data_dir=Path("/var/lib/test"),
            secrets=None,
        )
        assert pkg.secrets_file("123") is None

    def test_data_path(self):
        """Test data_path method."""
        assert VALKEY.data_path("6379") == Path("/var/lib/valkey/6379")
        assert REDIS.data_path("6380") == Path("/var/lib/redis/6380")

    def test_frozen(self):
        """Test ServicePackage is immutable (frozen dataclass)."""
        from dataclasses import FrozenInstanceError

        with pytest.raises(FrozenInstanceError):
            setattr(VALKEY, "name", "changed")


class TestPackageRegistry:
    """Tests for package registry functions."""

    def test_packages_dict_contains_valkey(self):
        """Test PACKAGES contains valkey."""
        assert "valkey" in PACKAGES
        assert PACKAGES["valkey"] is VALKEY

    def test_packages_dict_contains_redis(self):
        """Test PACKAGES contains redis."""
        assert "redis" in PACKAGES
        assert PACKAGES["redis"] is REDIS

    def test_get_package_valkey(self):
        """Test get_package returns valkey."""
        pkg = get_package("valkey")
        assert pkg is VALKEY

    def test_get_package_redis(self):
        """Test get_package returns redis."""
        pkg = get_package("redis")
        assert pkg is REDIS

    def test_get_package_unknown_raises(self):
        """Test get_package raises SystemExit for unknown package."""
        with pytest.raises(SystemExit) as exc_info:
            get_package("unknown")
        assert "unknown" in str(exc_info.value)
        assert "Available" in str(exc_info.value)

    def test_get_package_unknown_lists_available_packages(self):
        """SystemExit message for unknown package lists all available package names."""
        with pytest.raises(SystemExit) as exc_info:
            get_package("bogus")
        msg = str(exc_info.value)
        assert "valkey" in msg
        assert "redis" in msg

    def test_list_packages(self):
        """Test list_packages returns sorted list."""
        packages = list_packages()
        assert packages == ["redis", "valkey"]
        assert packages == sorted(packages)
