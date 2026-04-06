# tests/commands/service/test_packages.py
"""Tests for service package registry."""

from pathlib import Path

import pytest

from rots.commands.service.packages import (
    PACKAGES,
    RABBITMQ,
    REDIS,
    VALKEY,
    SecretConfig,
    ServicePackage,
    get_package,
    list_packages,
)

pytestmark = pytest.mark.quick


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


class TestSingletonPackage:
    """Tests for singleton service packages (e.g., RabbitMQ)."""

    def test_rabbitmq_package_exists(self):
        """Test RABBITMQ package is defined correctly."""
        assert RABBITMQ.name == "rabbitmq"
        assert RABBITMQ.template == "rabbitmq-server"
        assert RABBITMQ.config_dir == Path("/etc/rabbitmq")
        assert RABBITMQ.data_dir == Path("/var/lib/rabbitmq")
        assert RABBITMQ.default_port == 5672
        assert RABBITMQ.singleton is True

    def test_singleton_instance_unit(self):
        """Singleton instance_unit() returns template.service (no @)."""
        assert RABBITMQ.instance_unit() == "rabbitmq-server.service"

    def test_singleton_instance_unit_ignores_argument(self):
        """Singleton instance_unit() ignores any instance argument."""
        assert RABBITMQ.instance_unit("foo") == "rabbitmq-server.service"
        assert RABBITMQ.instance_unit("5672") == "rabbitmq-server.service"

    def test_singleton_template_unit(self):
        """Singleton template_unit returns same as instance_unit (no @)."""
        assert RABBITMQ.template_unit == "rabbitmq-server.service"

    def test_singleton_config_file(self):
        """Singleton config_file() returns fixed path without instance substitution."""
        assert RABBITMQ.config_file() == Path("/etc/rabbitmq/rabbitmq.conf")

    def test_singleton_config_file_ignores_argument(self):
        """Singleton config_file() ignores any instance argument."""
        assert RABBITMQ.config_file("5672") == Path("/etc/rabbitmq/rabbitmq.conf")

    def test_singleton_data_path(self):
        """Singleton data_path() returns data_dir directly (no instance subdir)."""
        assert RABBITMQ.data_path() == Path("/var/lib/rabbitmq")

    def test_singleton_data_path_ignores_argument(self):
        """Singleton data_path() ignores any instance argument."""
        assert RABBITMQ.data_path("5672") == Path("/var/lib/rabbitmq")

    def test_singleton_secrets_file_returns_none(self):
        """RABBITMQ has no secrets config, so secrets_file() returns None."""
        assert RABBITMQ.secrets_file() is None

    def test_non_singleton_packages_unchanged(self):
        """Existing packages are not singletons."""
        assert VALKEY.singleton is False
        assert REDIS.singleton is False


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

    def test_packages_dict_contains_rabbitmq(self):
        """Test PACKAGES contains rabbitmq."""
        assert "rabbitmq" in PACKAGES
        assert PACKAGES["rabbitmq"] is RABBITMQ

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

    def test_get_package_rabbitmq(self):
        """Test get_package returns rabbitmq."""
        pkg = get_package("rabbitmq")
        assert pkg is RABBITMQ

    def test_get_package_unknown_lists_available_packages(self):
        """SystemExit message for unknown package lists all available package names."""
        with pytest.raises(SystemExit) as exc_info:
            get_package("bogus")
        msg = str(exc_info.value)
        assert "valkey" in msg
        assert "redis" in msg
        assert "rabbitmq" in msg

    def test_list_packages(self):
        """Test list_packages returns sorted list."""
        packages = list_packages()
        assert packages == ["rabbitmq", "redis", "valkey"]
        assert packages == sorted(packages)
