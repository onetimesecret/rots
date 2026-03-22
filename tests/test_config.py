# tests/test_config.py
"""Tests for config module - Config dataclass."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest


class TestConfigDefaults:
    """Test Config dataclass default values."""

    def test_default_config_dir(self):
        """Should default to /etc/onetimesecret."""
        from rots.config import Config

        cfg = Config()
        assert cfg.config_dir == Path("/etc/onetimesecret")

    def test_default_var_dir(self):
        """Should default to /var/lib/onetimesecret."""
        from rots.config import Config

        cfg = Config()
        assert cfg.var_dir == Path("/var/lib/onetimesecret")

    def test_default_web_template_path(self):
        """Should default to systemd quadlet location for web."""
        from rots.config import Config

        cfg = Config()
        assert cfg.web_template_path == Path("/etc/containers/systemd/onetime-web@.container")

    def test_default_worker_template_path(self):
        """Should default to systemd quadlet location for worker."""
        from rots.config import Config

        cfg = Config()
        assert cfg.worker_template_path == Path("/etc/containers/systemd/onetime-worker@.container")

    def test_default_scheduler_template_path(self):
        """Should default to systemd quadlet location for scheduler."""
        from rots.config import Config

        cfg = Config()
        assert cfg.scheduler_template_path == Path(
            "/etc/containers/systemd/onetime-scheduler@.container"
        )


class TestConfigImageSettings:
    """Test Config image-related settings."""

    def test_default_image(self, monkeypatch):
        """Should default to ghcr.io/onetimesecret/onetimesecret."""
        monkeypatch.delenv("IMAGE", raising=False)
        from rots.config import Config

        cfg = Config()
        assert cfg.image == "ghcr.io/onetimesecret/onetimesecret"

    def test_image_from_env(self, monkeypatch):
        """Should use IMAGE env var when set."""
        monkeypatch.setenv("IMAGE", "custom/image")
        from rots.config import Config

        cfg = Config()
        assert cfg.image == "custom/image"

    def test_default_tag(self, monkeypatch):
        """Should default to '@current' sentinel (not the literal registry tag 'current')."""
        monkeypatch.delenv("TAG", raising=False)
        from rots.config import Config

        cfg = Config()
        assert cfg.tag == "@current"

    def test_tag_from_env(self, monkeypatch):
        """Should use TAG env var when set."""
        monkeypatch.setenv("TAG", "v1.2.3")
        from rots.config import Config

        cfg = Config()
        assert cfg.tag == "v1.2.3"

    def test_image_with_tag_property(self, monkeypatch):
        """Should combine image and tag correctly."""
        monkeypatch.setenv("IMAGE", "myregistry/myimage")
        monkeypatch.setenv("TAG", "latest")
        from rots.config import Config

        cfg = Config()
        assert cfg.image_with_tag == "myregistry/myimage:latest"


class TestConfigPaths:
    """Test Config path properties and methods."""

    def test_config_yaml_path(self):
        """Should return correct path for config.yaml."""
        from rots.config import Config

        cfg = Config(config_dir=Path("/etc/ots"))
        assert cfg.config_yaml == Path("/etc/ots/config.yaml")

    def test_db_path_with_writable_var_dir(self, tmp_path):
        """Should use system path when var_dir is writable."""
        from rots.config import Config

        cfg = Config(var_dir=tmp_path)
        assert cfg.db_path == tmp_path / "deployments.db"

    def test_db_path_falls_back_to_user_space(self):
        """Should fall back to ~/.local/share when var_dir not writable."""
        from rots.config import Config

        # Non-existent path triggers fallback
        cfg = Config(var_dir=Path("/nonexistent/path"))
        assert ".local/share/rots/deployments.db" in str(cfg.db_path)


class TestConfigValidate:
    """Test Config.validate method."""

    def test_validate_accepts_defaults(self, monkeypatch):
        """Should not raise with default image and @current sentinel tag."""
        monkeypatch.delenv("IMAGE", raising=False)
        monkeypatch.delenv("TAG", raising=False)
        from rots.config import Config

        cfg = Config()
        cfg.validate()  # Should not raise

    def test_validate_accepts_valid_tag(self, monkeypatch):
        """Should accept a well-formed OCI tag."""
        monkeypatch.setenv("TAG", "v1.2.3-rc1")
        monkeypatch.delenv("IMAGE", raising=False)
        from rots.config import Config

        cfg = Config()
        cfg.validate()  # Should not raise

    def test_validate_accepts_sentinel_current(self, monkeypatch):
        """Should accept the @current sentinel tag."""
        monkeypatch.delenv("IMAGE", raising=False)
        monkeypatch.delenv("TAG", raising=False)
        from rots.config import Config

        cfg = Config()
        assert cfg.tag == "@current"
        cfg.validate()  # Should not raise

    def test_validate_accepts_sentinel_rollback(self, monkeypatch):
        """Should accept the @rollback sentinel tag."""
        monkeypatch.setenv("TAG", "@rollback")
        monkeypatch.delenv("IMAGE", raising=False)
        from rots.config import Config

        cfg = Config()
        cfg.validate()  # Should not raise

    def test_validate_rejects_tag_with_shell_metacharacters(self, monkeypatch):
        """Should reject tags containing shell metacharacters."""
        monkeypatch.delenv("IMAGE", raising=False)
        from rots.config import Config

        with pytest.raises(ValueError, match="Invalid tag"):
            Config(tag="; rm -rf /")

    def test_validate_rejects_tag_with_spaces(self, monkeypatch):
        """Should reject tags containing whitespace."""
        monkeypatch.delenv("IMAGE", raising=False)
        from rots.config import Config

        with pytest.raises(ValueError, match="Invalid tag"):
            Config(tag="v1 2")

    def test_validate_rejects_empty_tag(self, monkeypatch):
        """Should reject empty string as tag."""
        monkeypatch.delenv("IMAGE", raising=False)
        from rots.config import Config

        with pytest.raises(ValueError, match="Invalid tag"):
            Config(tag="")

    def test_validate_rejects_image_with_shell_metacharacters(self, monkeypatch):
        """Should reject image names with shell injection attempts."""
        monkeypatch.delenv("TAG", raising=False)
        from rots.config import Config

        with pytest.raises(ValueError, match="Invalid image"):
            Config(image="$(whoami)")

    def test_validate_rejects_image_with_spaces(self, monkeypatch):
        """Should reject image names containing whitespace."""
        monkeypatch.delenv("TAG", raising=False)
        from rots.config import Config

        with pytest.raises(ValueError, match="Invalid image"):
            Config(image="my image")

    def test_validate_rejects_empty_image(self, monkeypatch):
        """Should reject empty string as image name."""
        monkeypatch.delenv("TAG", raising=False)
        from rots.config import Config

        with pytest.raises(ValueError, match="Invalid image"):
            Config(image="")

    def test_validate_accepts_ghcr_image(self, monkeypatch):
        """Should accept standard ghcr.io image path."""
        monkeypatch.delenv("IMAGE", raising=False)
        monkeypatch.delenv("TAG", raising=False)
        from rots.config import Config

        cfg = Config(image="ghcr.io/onetimesecret/onetimesecret")
        cfg.validate()  # Should not raise

    def test_validate_accepts_tag_with_dots_and_hyphens(self, monkeypatch):
        """Should accept tags with dots, hyphens, and underscores."""
        monkeypatch.delenv("IMAGE", raising=False)
        from rots.config import Config

        cfg = Config(tag="v0.19.0-beta_1")
        cfg.validate()  # Should not raise

    def test_validate_config_files_optional(self, tmp_path, monkeypatch):
        """Should not raise even when config files are missing."""
        monkeypatch.delenv("IMAGE", raising=False)
        monkeypatch.delenv("TAG", raising=False)
        from rots.config import Config

        cfg = Config(config_dir=tmp_path)
        cfg.validate()  # Should not raise

    def test_validate_rejects_tag_with_colon(self, monkeypatch):
        """Should reject tags containing colons (would break image:tag format)."""
        monkeypatch.delenv("IMAGE", raising=False)
        from rots.config import Config

        with pytest.raises(ValueError, match="Invalid tag"):
            Config(tag="v1:latest")

    def test_validate_rejects_image_with_backtick(self, monkeypatch):
        """Should reject image names with backtick command substitution."""
        monkeypatch.delenv("TAG", raising=False)
        from rots.config import Config

        with pytest.raises(ValueError, match="Invalid image"):
            Config(image="`whoami`/image")


class TestConfigFiles:
    """Test CONFIG_FILES module-level constant."""

    def test_config_files_contains_expected_files(self):
        """CONFIG_FILES should list the six known config files."""
        from rots.config import CONFIG_FILES

        assert "config.yaml" in CONFIG_FILES
        assert "auth.yaml" in CONFIG_FILES
        assert "logging.yaml" in CONFIG_FILES
        assert "billing.yaml" in CONFIG_FILES
        assert "Caddyfile.template" in CONFIG_FILES
        assert "puma.rb" in CONFIG_FILES

    def test_config_files_length(self):
        """CONFIG_FILES should contain exactly 6 entries."""
        from rots.config import CONFIG_FILES

        assert len(CONFIG_FILES) == 6

    def test_config_files_is_tuple(self):
        """CONFIG_FILES should be a tuple (immutable)."""
        from rots.config import CONFIG_FILES

        assert isinstance(CONFIG_FILES, tuple)


class TestExistingConfigFiles:
    """Test Config.existing_config_files property."""

    def test_returns_empty_when_config_dir_missing(self):
        """Should return empty list when config_dir does not exist."""
        from rots.config import Config

        cfg = Config(config_dir=Path("/nonexistent/config/dir"))
        assert cfg.existing_config_files == []

    def test_returns_empty_when_no_yaml_files(self, tmp_path):
        """Should return empty list when config_dir exists but has no yaml files."""
        from rots.config import Config

        config_dir = tmp_path / "etc"
        config_dir.mkdir()

        cfg = Config(config_dir=config_dir)
        assert cfg.existing_config_files == []

    def test_returns_only_existing_files(self, tmp_path):
        """Should return only files that actually exist on disk."""
        from rots.config import Config

        config_dir = tmp_path / "etc"
        config_dir.mkdir()
        (config_dir / "config.yaml").touch()
        (config_dir / "auth.yaml").touch()
        # logging.yaml intentionally not created

        cfg = Config(config_dir=config_dir)
        result = cfg.existing_config_files
        assert len(result) == 2
        assert config_dir / "config.yaml" in result
        assert config_dir / "auth.yaml" in result
        assert config_dir / "logging.yaml" not in result

    def test_returns_all_three_when_all_exist(self, tmp_path):
        """Should return all 3 paths when all config files exist."""
        from rots.config import Config

        config_dir = tmp_path / "etc"
        config_dir.mkdir()
        (config_dir / "config.yaml").touch()
        (config_dir / "auth.yaml").touch()
        (config_dir / "logging.yaml").touch()

        cfg = Config(config_dir=config_dir)
        result = cfg.existing_config_files
        assert len(result) == 3
        assert config_dir / "config.yaml" in result
        assert config_dir / "auth.yaml" in result
        assert config_dir / "logging.yaml" in result

    def test_ignores_non_config_files(self, tmp_path):
        """Should not include files not listed in CONFIG_FILES."""
        from rots.config import Config

        config_dir = tmp_path / "etc"
        config_dir.mkdir()
        (config_dir / "config.yaml").touch()
        (config_dir / "random.yaml").touch()  # Not in CONFIG_FILES

        cfg = Config(config_dir=config_dir)
        result = cfg.existing_config_files
        assert len(result) == 1
        assert config_dir / "config.yaml" in result
        assert config_dir / "random.yaml" not in result


class TestHasCustomConfig:
    """Test Config.has_custom_config property."""

    def test_false_when_no_config_dir(self):
        """Should return False when config_dir does not exist."""
        from rots.config import Config

        cfg = Config(config_dir=Path("/nonexistent/config/dir"))
        assert cfg.has_custom_config is False

    def test_false_when_empty_config_dir(self, tmp_path):
        """Should return False when config_dir exists but is empty."""
        from rots.config import Config

        config_dir = tmp_path / "etc"
        config_dir.mkdir()

        cfg = Config(config_dir=config_dir)
        assert cfg.has_custom_config is False

    def test_true_when_one_yaml_exists(self, tmp_path):
        """Should return True when at least one config file exists."""
        from rots.config import Config

        config_dir = tmp_path / "etc"
        config_dir.mkdir()
        (config_dir / "config.yaml").touch()

        cfg = Config(config_dir=config_dir)
        assert cfg.has_custom_config is True

    def test_true_when_all_yaml_files_exist(self, tmp_path):
        """Should return True when all config files exist."""
        from rots.config import Config

        config_dir = tmp_path / "etc"
        config_dir.mkdir()
        (config_dir / "config.yaml").touch()
        (config_dir / "auth.yaml").touch()
        (config_dir / "logging.yaml").touch()

        cfg = Config(config_dir=config_dir)
        assert cfg.has_custom_config is True


class TestGetExistingConfigFilesRemote:
    """Test Config.get_existing_config_files() with remote executor."""

    def test_delegates_to_property_when_no_executor(self, tmp_path):
        """Should use local Path.exists() when executor is None."""
        from rots.config import Config

        config_dir = tmp_path / "etc"
        config_dir.mkdir()
        (config_dir / "config.yaml").touch()

        cfg = Config(config_dir=config_dir)
        result = cfg.get_existing_config_files(executor=None)
        assert len(result) == 1
        assert config_dir / "config.yaml" in result

    def test_delegates_to_property_for_local_executor(self, tmp_path):
        """Should use local Path.exists() when executor is LocalExecutor."""
        from ots_shared.ssh import LocalExecutor

        from rots.config import Config

        config_dir = tmp_path / "etc"
        config_dir.mkdir()
        (config_dir / "config.yaml").touch()
        (config_dir / "auth.yaml").touch()

        cfg = Config(config_dir=config_dir)
        result = cfg.get_existing_config_files(executor=LocalExecutor())
        assert len(result) == 2

    def test_probes_remote_filesystem_for_ssh_executor(self):
        """Should use 'test -f' via executor for each config file on remote hosts."""
        from pathlib import Path
        from unittest.mock import MagicMock

        try:
            import paramiko
        except ImportError:
            pytest.skip("paramiko not installed")

        from ots_shared.ssh import SSHExecutor
        from ots_shared.ssh.executor import Result

        from rots.config import Config

        mock_client = MagicMock(spec=paramiko.SSHClient)
        ex = SSHExecutor(mock_client)

        # config.yaml exists, all others do not
        ex.run = MagicMock(
            side_effect=[
                Result(command="test", returncode=0, stdout="", stderr=""),  # config.yaml
                Result(command="test", returncode=1, stdout="", stderr=""),  # auth.yaml
                Result(command="test", returncode=1, stdout="", stderr=""),  # logging.yaml
                Result(command="test", returncode=1, stdout="", stderr=""),  # billing.yaml
                Result(command="test", returncode=1, stdout="", stderr=""),  # Caddyfile.template
                Result(command="test", returncode=1, stdout="", stderr=""),  # puma.rb
            ]
        )

        cfg = Config(config_dir=Path("/etc/onetimesecret"))
        result = cfg.get_existing_config_files(executor=ex)

        assert len(result) == 1
        assert result[0] == Path("/etc/onetimesecret/config.yaml")
        assert ex.run.call_count == 6


class TestConfigRegistry:
    """Test Config.registry field from OTS_REGISTRY env var."""

    def test_registry_from_env(self, monkeypatch):
        """Should read OTS_REGISTRY env var."""
        monkeypatch.setenv("OTS_REGISTRY", "registry.example.com/myorg")
        from rots.config import Config

        cfg = Config()
        assert cfg.registry == "registry.example.com/myorg"

    def test_registry_defaults_to_none(self, monkeypatch):
        """Should default to None when OTS_REGISTRY is absent."""
        monkeypatch.delenv("OTS_REGISTRY", raising=False)
        from rots.config import Config

        cfg = Config()
        assert cfg.registry is None


class TestConfigRegistryAuthFile:
    """Test Config.registry_auth_file property resolution chain."""

    def test_env_var_overrides_all(self, monkeypatch, tmp_path):
        """REGISTRY_AUTH_FILE env var should override all other paths."""
        auth_file = tmp_path / "custom-auth.json"
        auth_file.touch()
        monkeypatch.setenv("REGISTRY_AUTH_FILE", str(auth_file))
        # Set XDG_RUNTIME_DIR too to prove env var takes precedence
        monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "xdg"))
        from rots.config import Config

        cfg = Config()
        assert cfg.registry_auth_file == auth_file

    def test_explicit_override_takes_highest_priority(self, monkeypatch, tmp_path):
        """_registry_auth_file field should override even REGISTRY_AUTH_FILE env var."""
        env_file = tmp_path / "env-auth.json"
        explicit_file = tmp_path / "explicit-auth.json"
        monkeypatch.setenv("REGISTRY_AUTH_FILE", str(env_file))
        from rots.config import Config

        cfg = Config(_registry_auth_file=explicit_file)
        assert cfg.registry_auth_file == explicit_file

    def test_xdg_runtime_dir_fallback(self, monkeypatch, tmp_path):
        """Should use XDG_RUNTIME_DIR/containers/auth.json when file exists."""
        monkeypatch.delenv("REGISTRY_AUTH_FILE", raising=False)
        xdg_dir = tmp_path / "run" / "user" / "1000"
        auth_dir = xdg_dir / "containers"
        auth_dir.mkdir(parents=True)
        auth_file = auth_dir / "auth.json"
        auth_file.touch()
        monkeypatch.setenv("XDG_RUNTIME_DIR", str(xdg_dir))
        from rots.config import Config

        cfg = Config()
        assert cfg.registry_auth_file == auth_file

    def test_xdg_runtime_dir_skipped_when_file_missing(self, monkeypatch, tmp_path):
        """Should skip XDG_RUNTIME_DIR when auth.json does not exist there."""
        monkeypatch.delenv("REGISTRY_AUTH_FILE", raising=False)
        xdg_dir = tmp_path / "run" / "user" / "1000"
        xdg_dir.mkdir(parents=True)
        # Do NOT create containers/auth.json
        monkeypatch.setenv("XDG_RUNTIME_DIR", str(xdg_dir))
        from rots.config import Config

        cfg = Config()
        # Should fall through to user config or system path, not the XDG path
        assert cfg.registry_auth_file != xdg_dir / "containers" / "auth.json"

    def test_user_config_fallback_on_macos(self, monkeypatch):
        """On macOS (darwin), should return ~/.config/containers/auth.json."""
        import sys

        monkeypatch.delenv("REGISTRY_AUTH_FILE", raising=False)
        monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
        from rots.config import Config

        cfg = Config()
        # On macOS (our test platform), should return user config path
        if sys.platform == "darwin":
            expected = Path.home() / ".config" / "containers" / "auth.json"
            assert cfg.registry_auth_file == expected


class TestGetRegistryAuthFile:
    """Test Config.get_registry_auth_file(executor) method."""

    def test_none_executor_delegates_to_property(self, monkeypatch):
        """get_registry_auth_file(None) should return same as registry_auth_file property."""
        monkeypatch.delenv("REGISTRY_AUTH_FILE", raising=False)
        from rots.config import Config

        cfg = Config()
        assert cfg.get_registry_auth_file(None) == cfg.registry_auth_file

    def test_local_executor_delegates_to_property(self, monkeypatch):
        """get_registry_auth_file(LocalExecutor()) should return same as registry_auth_file."""
        monkeypatch.delenv("REGISTRY_AUTH_FILE", raising=False)
        from ots_shared.ssh import LocalExecutor

        from rots.config import Config

        cfg = Config()
        ex = LocalExecutor()
        assert cfg.get_registry_auth_file(ex) == cfg.registry_auth_file

    def test_remote_executor_with_override_returns_override(self):
        """get_registry_auth_file with _registry_auth_file override should return it."""
        from rots.config import Config

        override = Path("/custom/auth.json")
        mock_executor = MagicMock()
        cfg = Config(_registry_auth_file=override)
        assert cfg.get_registry_auth_file(mock_executor) == override

    def test_remote_executor_with_env_var(self, monkeypatch):
        """get_registry_auth_file should use REGISTRY_AUTH_FILE env var for remote."""
        monkeypatch.setenv("REGISTRY_AUTH_FILE", "/env/auth.json")
        from rots.config import Config

        mock_executor = MagicMock()
        cfg = Config()
        assert cfg.get_registry_auth_file(mock_executor) == Path("/env/auth.json")

    def test_remote_executor_probes_remote_paths(self, monkeypatch):
        """get_registry_auth_file should probe remote filesystem for known paths."""
        monkeypatch.delenv("REGISTRY_AUTH_FILE", raising=False)
        from ots_shared.ssh.executor import Result

        from rots.config import Config

        mock_executor = MagicMock()
        # First candidate (/run/containers/0/auth.json) exists
        mock_executor.run.return_value = Result(command="test", returncode=0, stdout="", stderr="")
        cfg = Config()
        result = cfg.get_registry_auth_file(mock_executor)
        assert result == Path("/run/containers/0/auth.json")

    def test_remote_executor_falls_through_to_etc_path(self, monkeypatch):
        """When first candidate doesn't exist, should try /etc/containers/auth.json."""
        monkeypatch.delenv("REGISTRY_AUTH_FILE", raising=False)
        from ots_shared.ssh.executor import Result

        from rots.config import Config

        mock_executor = MagicMock()
        mock_executor.run.side_effect = [
            Result(command="test", returncode=1, stdout="", stderr=""),  # /run/... not found
            Result(command="test", returncode=0, stdout="", stderr=""),  # /etc/... found
        ]
        cfg = Config()
        result = cfg.get_registry_auth_file(mock_executor)
        assert result == Path("/etc/containers/auth.json")

    def test_remote_executor_defaults_when_no_paths_exist(self, monkeypatch):
        """When no remote paths exist, should default to /etc/containers/auth.json."""
        monkeypatch.delenv("REGISTRY_AUTH_FILE", raising=False)
        from ots_shared.ssh.executor import Result

        from rots.config import Config

        mock_executor = MagicMock()
        mock_executor.run.return_value = Result(command="test", returncode=1, stdout="", stderr="")
        cfg = Config()
        result = cfg.get_registry_auth_file(mock_executor)
        assert result == Path("/etc/containers/auth.json")


class TestConfigPrivateImage:
    """Test Config.private_image and private_image_with_tag properties."""

    def test_private_image_none_when_no_registry(self, monkeypatch):
        """Should return None when registry is None."""
        monkeypatch.delenv("OTS_REGISTRY", raising=False)
        from rots.config import Config

        cfg = Config()
        assert cfg.private_image is None

    def test_private_image_with_registry(self, monkeypatch):
        """Should return formatted string when registry is set."""
        monkeypatch.setenv("OTS_REGISTRY", "registry.example.com/myorg")
        from rots.config import Config

        cfg = Config()
        # Default IMAGE is ghcr.io/onetimesecret/onetimesecret; strip
        # the ghcr.io registry, keep onetimesecret/onetimesecret path.
        assert cfg.private_image == "registry.example.com/myorg/onetimesecret/onetimesecret"

    def test_private_image_with_tag_none_when_no_registry(self, monkeypatch):
        """Should return None when registry is None."""
        monkeypatch.delenv("OTS_REGISTRY", raising=False)
        from rots.config import Config

        cfg = Config()
        assert cfg.private_image_with_tag is None

    def test_private_image_with_tag(self, monkeypatch):
        """Should combine registry, image path, and tag."""
        monkeypatch.setenv("OTS_REGISTRY", "registry.example.com/myorg")
        monkeypatch.setenv("TAG", "v2.0.0")
        from rots.config import Config

        cfg = Config()
        expected = "registry.example.com/myorg/onetimesecret/onetimesecret:v2.0.0"
        assert cfg.private_image_with_tag == expected

    def test_private_image_with_tag_uses_default_tag(self, monkeypatch):
        """Should use default tag when TAG env var is absent."""
        monkeypatch.setenv("OTS_REGISTRY", "registry.example.com/myorg")
        monkeypatch.delenv("TAG", raising=False)
        from rots.config import Config

        cfg = Config()
        expected = "registry.example.com/myorg/onetimesecret/onetimesecret@current"
        assert cfg.private_image_with_tag == expected

    def test_private_image_custom_image_strips_registry(self, monkeypatch):
        """IMAGE env var with registry prefix: strip registry, keep full image path."""
        monkeypatch.setenv("IMAGE", "docker.io/myorg/customapp")
        monkeypatch.setenv("OTS_REGISTRY", "registry.example.com")
        from rots.config import Config

        cfg = Config()
        # docker.io is a registry (contains dot) -> strip it, keep myorg/customapp
        assert cfg.private_image == "registry.example.com/myorg/customapp"

    def test_private_image_deep_registry_path(self, monkeypatch):
        """IMAGE with registry prefix: strip registry hostname, keep image path."""
        monkeypatch.setenv("IMAGE", "registry.corp.com/team/webapp")
        monkeypatch.setenv("OTS_REGISTRY", "myreg.example.com")
        from rots.config import Config

        cfg = Config()
        # registry.corp.com is a registry (contains dot) -> strip it, keep team/webapp
        assert cfg.private_image == "myreg.example.com/team/webapp"


class TestConfigResolveImageTag:
    """Test Config.resolve_image_tag() with a real SQLite database."""

    def test_resolves_current_alias(self, monkeypatch, tmp_path):
        """Should resolve 'current' tag to the aliased image and tag from the database."""
        from rots import db
        from rots.config import Config

        # Set up a real database
        db_path = tmp_path / "deployments.db"
        db.init_db(db_path)
        db.set_current(db_path, "ghcr.io/onetimesecret/onetimesecret", "v1.5.0")

        # Config points var_dir at tmp_path so db_path resolves there
        monkeypatch.delenv("TAG", raising=False)
        cfg = Config(var_dir=tmp_path)
        assert cfg.db_path == db_path

        # resolve_image_tag should look up "current" and find the alias
        image, tag = cfg.resolve_image_tag()
        assert image == "ghcr.io/onetimesecret/onetimesecret"
        assert tag == "v1.5.0"

    def test_resolves_rollback_alias(self, monkeypatch, tmp_path):
        """Should resolve 'rollback' tag to the previous image and tag."""
        from rots import db
        from rots.config import Config

        db_path = tmp_path / "deployments.db"
        db.init_db(db_path)
        # First deploy sets current
        db.set_current(db_path, "ghcr.io/onetimesecret/onetimesecret", "v1.0.0")
        # Second deploy moves v1.0.0 to rollback, sets v2.0.0 as current
        db.set_current(db_path, "ghcr.io/onetimesecret/onetimesecret", "v2.0.0")

        monkeypatch.setenv("TAG", "rollback")
        cfg = Config(var_dir=tmp_path)
        image, tag = cfg.resolve_image_tag()
        assert image == "ghcr.io/onetimesecret/onetimesecret"
        assert tag == "v1.0.0"

    def test_falls_back_to_sentinel_when_no_alias(self, monkeypatch, tmp_path):
        """When no CURRENT alias is set, resolve_image_tag returns the sentinel '@current'.

        Callers (e.g. image pull) are expected to detect the sentinel and raise
        a helpful error rather than passing '@current' to the registry.
        """
        from rots import db
        from rots.config import Config

        db_path = tmp_path / "deployments.db"
        db.init_db(db_path)
        # Database is empty - no aliases set

        monkeypatch.delenv("TAG", raising=False)
        monkeypatch.delenv("IMAGE", raising=False)
        cfg = Config(var_dir=tmp_path)
        image, tag = cfg.resolve_image_tag()
        assert image == "ghcr.io/onetimesecret/onetimesecret"
        # The sentinel is returned unchanged so callers can detect the unresolved case
        assert tag == "@current"

    def test_non_alias_tag_passes_through(self, monkeypatch, tmp_path):
        """A concrete tag like 'v3.0.0' should pass through without database lookup."""
        from rots.config import Config

        monkeypatch.setenv("TAG", "v3.0.0")
        monkeypatch.setenv("IMAGE", "myregistry/myimage")
        cfg = Config(var_dir=tmp_path)
        image, tag = cfg.resolve_image_tag()
        assert image == "myregistry/myimage"
        assert tag == "v3.0.0"


class TestConfigValkeyService:
    """Test Config.valkey_service field from OTS_VALKEY_SERVICE env var."""

    def test_valkey_service_from_env(self, monkeypatch):
        """Should read OTS_VALKEY_SERVICE env var and store it in valkey_service."""
        monkeypatch.setenv("OTS_VALKEY_SERVICE", "valkey-server@6379.service")
        from rots.config import Config

        cfg = Config()
        assert cfg.valkey_service == "valkey-server@6379.service"

    def test_valkey_service_defaults_to_none(self, monkeypatch):
        """Should default to None when OTS_VALKEY_SERVICE is not set."""
        monkeypatch.delenv("OTS_VALKEY_SERVICE", raising=False)
        from rots.config import Config

        cfg = Config()
        assert cfg.valkey_service is None

    def test_valkey_service_explicit_override(self, monkeypatch):
        """Constructor override takes precedence over env var."""
        monkeypatch.setenv("OTS_VALKEY_SERVICE", "valkey-server@6380.service")
        from rots.config import Config

        cfg = Config(valkey_service="redis@6379.service")
        assert cfg.valkey_service == "redis@6379.service"

    def test_valkey_service_in_quadlet_adds_after_and_wants(self, mocker, tmp_path, monkeypatch):
        """When valkey_service is set the written quadlet should include After= and Wants= lines."""
        mocker.patch("rots.quadlet.systemd.daemon_reload")
        monkeypatch.delenv("OTS_VALKEY_SERVICE", raising=False)
        from rots import quadlet
        from rots.config import Config

        cfg = Config(
            web_template_path=tmp_path / "onetime-web@.container",
            var_dir=tmp_path / "var",
            valkey_service="valkey-server@6379.service",
        )

        quadlet.write_web_template(cfg, force=True)

        content = cfg.web_template_path.read_text()
        assert "valkey-server@6379.service" in content
        assert "After=" in content
        assert "Wants=" in content

    def test_no_valkey_service_quadlet_omits_valkey_lines(self, mocker, tmp_path, monkeypatch):
        """When valkey_service is None the quadlet should not reference valkey unit."""
        mocker.patch("rots.quadlet.systemd.daemon_reload")
        monkeypatch.delenv("OTS_VALKEY_SERVICE", raising=False)
        from rots import quadlet
        from rots.config import Config

        cfg = Config(
            web_template_path=tmp_path / "onetime-web@.container",
            var_dir=tmp_path / "var",
            valkey_service=None,
        )

        quadlet.write_web_template(cfg, force=True)

        content = cfg.web_template_path.read_text()
        assert "valkey-server" not in content


class TestConfigResourceLimits:
    """Test Config.memory_max and cpu_quota fields from env vars."""

    def test_memory_max_from_env(self, monkeypatch):
        """Should read MEMORY_MAX env var."""
        monkeypatch.setenv("MEMORY_MAX", "1G")
        from rots.config import Config

        cfg = Config()
        assert cfg.memory_max == "1G"

    def test_memory_max_defaults_to_none(self, monkeypatch):
        """Should default to None when MEMORY_MAX is not set."""
        monkeypatch.delenv("MEMORY_MAX", raising=False)
        from rots.config import Config

        cfg = Config()
        assert cfg.memory_max is None

    def test_cpu_quota_from_env(self, monkeypatch):
        """Should read CPU_QUOTA env var."""
        monkeypatch.setenv("CPU_QUOTA", "80%")
        from rots.config import Config

        cfg = Config()
        assert cfg.cpu_quota == "80%"

    def test_cpu_quota_defaults_to_none(self, monkeypatch):
        """Should default to None when CPU_QUOTA is not set."""
        monkeypatch.delenv("CPU_QUOTA", raising=False)
        from rots.config import Config

        cfg = Config()
        assert cfg.cpu_quota is None

    def test_memory_max_explicit_override(self, monkeypatch):
        """Constructor value overrides env var."""
        monkeypatch.setenv("MEMORY_MAX", "512M")
        from rots.config import Config

        cfg = Config(memory_max="2G")
        assert cfg.memory_max == "2G"

    def test_cpu_quota_explicit_override(self, monkeypatch):
        """Constructor value overrides env var."""
        monkeypatch.setenv("CPU_QUOTA", "50%")
        from rots.config import Config

        cfg = Config(cpu_quota="90%")
        assert cfg.cpu_quota == "90%"


class TestSystemDbPath:
    """Test Config.system_db_path property."""

    def test_system_db_path_is_var_dir_plus_filename(self):
        """system_db_path should always be var_dir / deployments.db."""
        from rots.config import Config

        cfg = Config(var_dir=Path("/var/lib/onetimesecret"))
        assert cfg.system_db_path == Path("/var/lib/onetimesecret/deployments.db")

    def test_system_db_path_with_custom_var_dir(self, tmp_path):
        """system_db_path should use the configured var_dir."""
        from rots.config import Config

        cfg = Config(var_dir=tmp_path)
        assert cfg.system_db_path == tmp_path / "deployments.db"


class TestGetDbPath:
    """Test Config.get_db_path(executor) method."""

    def test_get_db_path_none_falls_through_to_db_path(self, tmp_path):
        """get_db_path(None) should return the same as db_path property."""
        from rots.config import Config

        cfg = Config(var_dir=tmp_path)
        assert cfg.get_db_path(None) == cfg.db_path

    def test_get_db_path_local_executor_falls_through_to_db_path(self, tmp_path):
        """get_db_path(LocalExecutor()) should return the same as db_path."""
        from ots_shared.ssh import LocalExecutor

        from rots.config import Config

        cfg = Config(var_dir=tmp_path)
        ex = LocalExecutor()
        assert cfg.get_db_path(ex) == cfg.db_path

    def test_get_db_path_remote_executor_returns_system_db_path(self, tmp_path):
        """get_db_path with a non-local executor should return system_db_path."""
        from rots.config import Config

        # Use a mock executor that is not a LocalExecutor
        mock_executor = MagicMock()
        cfg = Config(var_dir=tmp_path)
        assert cfg.get_db_path(mock_executor) == cfg.system_db_path

    def test_get_db_path_ssh_executor_returns_system_db_path(self, mocker):
        """get_db_path(SSHExecutor) should return system_db_path."""
        try:
            import paramiko
        except ImportError:
            pytest.skip("paramiko not installed")

        from ots_shared.ssh import SSHExecutor

        from rots.config import Config

        mock_client = MagicMock(spec=paramiko.SSHClient)
        ssh_ex = SSHExecutor(mock_client)
        cfg = Config()
        assert cfg.get_db_path(ssh_ex) == cfg.system_db_path

    def test_get_db_path_system_vs_local_difference(self):
        """system_db_path and db_path should differ when var_dir is not writable."""
        from rots.config import Config

        cfg = Config(var_dir=Path("/nonexistent/path"))
        # system_db_path always returns var_dir / deployments.db
        assert cfg.system_db_path == Path("/nonexistent/path/deployments.db")
        # db_path falls back to user space since /nonexistent/path is not writable
        assert cfg.db_path != cfg.system_db_path


class TestCloseSSHCache:
    """Test _close_ssh_cache() atexit cleanup function."""

    def test_close_ssh_cache_closes_all_clients(self):
        """Should call close() on every cached client."""
        from rots.config import _close_ssh_cache, _ssh_cache

        mock_client_a = MagicMock()
        mock_client_b = MagicMock()
        _ssh_cache["host-a.example.com"] = mock_client_a
        _ssh_cache["host-b.example.com"] = mock_client_b

        _close_ssh_cache()

        mock_client_a.close.assert_called_once()
        mock_client_b.close.assert_called_once()
        assert len(_ssh_cache) == 0

    def test_close_ssh_cache_clears_cache(self):
        """After running, the cache dict should be empty."""
        from rots.config import _close_ssh_cache, _ssh_cache

        _ssh_cache["host-c.example.com"] = MagicMock()

        _close_ssh_cache()

        assert _ssh_cache == {}

    def test_close_ssh_cache_ignores_close_errors(self):
        """Should not raise if client.close() throws."""
        from rots.config import _close_ssh_cache, _ssh_cache

        mock_client = MagicMock()
        mock_client.close.side_effect = RuntimeError("already closed")
        _ssh_cache["host-d.example.com"] = mock_client

        _close_ssh_cache()  # should not raise

        assert len(_ssh_cache) == 0

    def test_close_ssh_cache_noop_when_empty(self):
        """Should not raise when the cache is already empty."""
        from rots.config import _close_ssh_cache, _ssh_cache

        _ssh_cache.clear()

        _close_ssh_cache()  # should not raise

        assert len(_ssh_cache) == 0


class TestMetaHostFlagRouting:
    """Test that _meta() routes --host flag through context.host_var."""

    def test_meta_sets_host_var_when_host_provided(self, mocker):
        """_meta(host='myhost') should set context.host_var."""
        from rots import context
        from rots.cli import _meta

        mocker.patch("rots.cli._configure_logging")
        mocker.patch("rots.cli.app")

        # Save state so we can restore after the test
        token = context.host_var.set(None)
        try:
            _meta("version", verbose=False, host="eu1.example.com")
            assert context.host_var.get(None) == "eu1.example.com"
        finally:
            context.host_var.reset(token)

    def test_meta_does_not_set_host_var_when_host_none(self, mocker):
        """_meta(host=None) should leave context.host_var at default (None)."""
        from rots import context
        from rots.cli import _meta

        mocker.patch("rots.cli._configure_logging")
        mocker.patch("rots.cli.app")

        # Reset to known state
        token = context.host_var.set(None)
        try:
            _meta("version", verbose=False, host=None)
            assert context.host_var.get(None) is None
        finally:
            context.host_var.reset(token)

    def test_host_var_default_is_none(self):
        """context.host_var should be defined with default=None."""
        # The ContextVar was created with default=None — verify by
        # inspecting the ContextVar itself rather than reading the current
        # context (which may be modified by earlier tests in the same process).
        import contextvars

        from rots import context

        # Create a truly empty context and read the var there
        empty_ctx = contextvars.Context()
        result = empty_ctx.run(context.host_var.get, None)
        assert result is None


class TestGetExecutorSSHErrors:
    """Test that SSH exceptions in get_executor produce user-friendly SystemExit messages."""

    def _clear_cache(self, hostname: str):
        """Ensure hostname is not in the SSH cache so get_executor tries to connect."""
        from rots.config import _ssh_cache

        _ssh_cache.pop(hostname, None)

    def test_authentication_exception_gives_friendly_message(self, mocker, monkeypatch):
        """AuthenticationException should produce a SystemExit mentioning 'Authentication'."""
        from paramiko.ssh_exception import AuthenticationException

        from rots.config import Config

        hostname = "test-auth-failure.example.com"
        self._clear_cache(hostname)
        mocker.patch("ots_shared.ssh.resolve_host", return_value=hostname)
        mocker.patch(
            "ots_shared.ssh.ssh_connect",
            side_effect=AuthenticationException("Auth failed"),
        )

        cfg = Config()
        with pytest.raises(SystemExit) as exc_info:
            cfg.get_executor(host=hostname)

        msg = str(exc_info.value)
        assert "Authentication" in msg
        assert hostname in msg

    def test_no_valid_connections_gives_friendly_message(self, mocker, monkeypatch):
        """NoValidConnectionsError should produce a SystemExit with the host name."""
        from paramiko.ssh_exception import NoValidConnectionsError

        from rots.config import Config

        hostname = "test-no-conn.example.com"
        self._clear_cache(hostname)
        mocker.patch("ots_shared.ssh.resolve_host", return_value=hostname)
        mocker.patch(
            "ots_shared.ssh.ssh_connect",
            side_effect=NoValidConnectionsError({("::1", 22): OSError("refused")}),
        )

        cfg = Config()
        with pytest.raises(SystemExit) as exc_info:
            cfg.get_executor(host=hostname)

        msg = str(exc_info.value)
        # NoValidConnectionsError is an OSError subclass, caught by the OSError handler
        assert hostname in msg

    def test_socket_timeout_gives_friendly_message(self, mocker, monkeypatch):
        """socket.timeout should produce a SystemExit mentioning 'timed out'."""
        from rots.config import Config

        hostname = "test-timeout.example.com"
        self._clear_cache(hostname)
        mocker.patch("ots_shared.ssh.resolve_host", return_value=hostname)
        mocker.patch(
            "ots_shared.ssh.ssh_connect",
            side_effect=TimeoutError("Connection timed out"),
        )

        cfg = Config()
        with pytest.raises(SystemExit) as exc_info:
            cfg.get_executor(host=hostname)

        msg = str(exc_info.value)
        assert "timed out" in msg
        assert hostname in msg

    def test_import_error_gives_install_hint(self, mocker, monkeypatch):
        """ImportError (no paramiko) should tell the user how to install it."""
        from rots.config import Config

        hostname = "test-import.example.com"
        self._clear_cache(hostname)
        mocker.patch("ots_shared.ssh.resolve_host", return_value=hostname)
        mocker.patch(
            "ots_shared.ssh.ssh_connect",
            side_effect=ImportError("No module named 'paramiko'"),
        )

        cfg = Config()
        with pytest.raises(SystemExit) as exc_info:
            cfg.get_executor(host=hostname)

        msg = str(exc_info.value)
        assert "paramiko" in msg

    def test_local_executor_returned_when_host_is_none(self, mocker):
        """When resolve_host returns None, get_executor should return a LocalExecutor."""
        from ots_shared.ssh import LocalExecutor

        from rots.config import Config

        mocker.patch("ots_shared.ssh.resolve_host", return_value=None)

        cfg = Config()
        ex = cfg.get_executor(host=None)
        assert isinstance(ex, LocalExecutor)


class TestGetExecutorResolutionChain:
    """Test Config.get_executor() host resolution chain and executor type returned."""

    def _clear_cache(self, hostname: str):
        """Remove hostname from SSH cache so get_executor attempts a new connection."""
        from rots.config import _ssh_cache

        _ssh_cache.pop(hostname, None)

    def test_returns_local_executor_when_no_host(self, mocker):
        """get_executor(host=None) with no OTS_HOST or .otsinfra.env returns LocalExecutor."""
        from ots_shared.ssh import LocalExecutor

        from rots.config import Config

        mocker.patch("ots_shared.ssh.resolve_host", return_value=None)

        cfg = Config()
        ex = cfg.get_executor(host=None)
        assert isinstance(ex, LocalExecutor)

    def test_returns_ssh_executor_when_host_flag_set(self, mocker):
        """get_executor(host='myhost') should connect via SSH and return SSHExecutor."""
        try:
            import paramiko
        except ImportError:
            pytest.skip("paramiko not installed")

        from ots_shared.ssh import SSHExecutor

        from rots.config import Config

        hostname = "test-host-flag.example.com"
        self._clear_cache(hostname)

        # resolve_host returns the explicit flag
        mocker.patch("ots_shared.ssh.resolve_host", return_value=hostname)
        # ssh_connect returns a mock SSHClient
        mock_client = MagicMock(spec=paramiko.SSHClient)
        mocker.patch("ots_shared.ssh.ssh_connect", return_value=mock_client)

        cfg = Config()
        ex = cfg.get_executor(host=hostname)
        assert isinstance(ex, SSHExecutor)

        # Clean up
        self._clear_cache(hostname)

    def test_returns_ssh_executor_when_ots_host_env_set(self, mocker, monkeypatch):
        """get_executor(host=None) with OTS_HOST env var should return SSHExecutor."""
        try:
            import paramiko
        except ImportError:
            pytest.skip("paramiko not installed")

        from ots_shared.ssh import SSHExecutor

        from rots.config import Config

        hostname = "test-env-host.example.com"
        self._clear_cache(hostname)

        # resolve_host returns the env-derived host
        mocker.patch("ots_shared.ssh.resolve_host", return_value=hostname)
        mock_client = MagicMock(spec=paramiko.SSHClient)
        mocker.patch("ots_shared.ssh.ssh_connect", return_value=mock_client)

        cfg = Config()
        ex = cfg.get_executor(host=None)
        assert isinstance(ex, SSHExecutor)

        # Clean up
        self._clear_cache(hostname)

    def test_returns_ssh_executor_when_otsinfra_env_found(self, mocker):
        """get_executor(host=None) with .otsinfra.env providing host should return SSHExecutor."""
        try:
            import paramiko
        except ImportError:
            pytest.skip("paramiko not installed")

        from ots_shared.ssh import SSHExecutor

        from rots.config import Config

        hostname = "test-otsinfra.example.com"
        self._clear_cache(hostname)

        # resolve_host discovers host from .otsinfra.env
        mocker.patch("ots_shared.ssh.resolve_host", return_value=hostname)
        mock_client = MagicMock(spec=paramiko.SSHClient)
        mocker.patch("ots_shared.ssh.ssh_connect", return_value=mock_client)

        cfg = Config()
        ex = cfg.get_executor(host=None)
        assert isinstance(ex, SSHExecutor)

        # Clean up
        self._clear_cache(hostname)

    def test_ssh_cache_reuses_connection(self, mocker):
        """Multiple get_executor calls for the same host should reuse the SSH connection."""
        try:
            import paramiko
        except ImportError:
            pytest.skip("paramiko not installed")

        from ots_shared.ssh import SSHExecutor

        from rots.config import Config

        hostname = "test-cache.example.com"
        self._clear_cache(hostname)

        mocker.patch("ots_shared.ssh.resolve_host", return_value=hostname)
        mock_client = MagicMock(spec=paramiko.SSHClient)
        mock_connect = mocker.patch("ots_shared.ssh.ssh_connect", return_value=mock_client)

        cfg = Config()
        ex1 = cfg.get_executor(host=hostname)
        ex2 = cfg.get_executor(host=hostname)

        # ssh_connect should only be called once (cached)
        mock_connect.assert_called_once()
        assert isinstance(ex1, SSHExecutor)
        assert isinstance(ex2, SSHExecutor)

        # Clean up
        self._clear_cache(hostname)

    def test_resolve_host_receives_host_flag(self, mocker):
        """get_executor should pass the host parameter to resolve_host."""
        from rots.config import Config

        mock_resolve = mocker.patch("ots_shared.ssh.resolve_host", return_value=None)

        cfg = Config()
        cfg.get_executor(host="explicit-host.example.com")

        mock_resolve.assert_called_once_with(host_flag="explicit-host.example.com")


class TestConfigValidateResourceLimits:
    """Test Config.validate() for memory_max and cpu_quota fields."""

    def _make_config(self, monkeypatch, **overrides):
        """Create a Config with valid defaults, applying overrides."""
        monkeypatch.delenv("IMAGE", raising=False)
        monkeypatch.delenv("TAG", raising=False)
        monkeypatch.delenv("MEMORY_MAX", raising=False)
        monkeypatch.delenv("CPU_QUOTA", raising=False)
        monkeypatch.delenv("OTS_VALKEY_SERVICE", raising=False)

        from rots.config import Config

        return Config(**overrides)

    # --- memory_max positive tests ---

    @pytest.mark.parametrize("value", ["512M", "1G", "2G", "100K", "4T", "infinity", "1024"])
    def test_validate_accepts_valid_memory_max(self, monkeypatch, value):
        """Valid memory_max values should pass validation."""
        cfg = self._make_config(monkeypatch, memory_max=value)
        cfg.validate()  # should not raise

    # --- memory_max negative tests ---

    def test_validate_rejects_memory_max_with_newline(self, monkeypatch):
        """memory_max containing newline (directive injection) should fail."""
        with pytest.raises(ValueError, match="MEMORY_MAX"):
            self._make_config(monkeypatch, memory_max="512M\nExecStart=/evil")

    def test_validate_rejects_memory_max_with_shell_chars(self, monkeypatch):
        """memory_max with shell metacharacters should fail."""
        with pytest.raises(ValueError, match="MEMORY_MAX"):
            self._make_config(monkeypatch, memory_max="512M; rm -rf /")

    def test_validate_rejects_memory_max_empty_string(self, monkeypatch):
        """Empty string memory_max is falsy so skipped; non-matching string should fail."""
        # Empty string is falsy, so validate() skips it -- that's fine.
        self._make_config(monkeypatch, memory_max="")
        # But a non-matching string should fail:
        with pytest.raises(ValueError, match="MEMORY_MAX"):
            self._make_config(monkeypatch, memory_max="not-a-size")

    def test_validate_rejects_memory_max_with_spaces(self, monkeypatch):
        """memory_max with spaces should fail."""
        with pytest.raises(ValueError, match="MEMORY_MAX"):
            self._make_config(monkeypatch, memory_max="512 M")

    # --- cpu_quota positive tests ---

    @pytest.mark.parametrize("value", ["80%", "150%", "1%", "99999%"])
    def test_validate_accepts_valid_cpu_quota(self, monkeypatch, value):
        """Valid cpu_quota values should pass validation."""
        cfg = self._make_config(monkeypatch, cpu_quota=value)
        cfg.validate()  # should not raise

    # --- cpu_quota negative tests ---

    def test_validate_rejects_cpu_quota_with_newline(self, monkeypatch):
        """cpu_quota containing newline (directive injection) should fail."""
        with pytest.raises(ValueError, match="CPU_QUOTA"):
            self._make_config(monkeypatch, cpu_quota="80%\nExecStart=/evil")

    def test_validate_rejects_cpu_quota_without_percent(self, monkeypatch):
        """cpu_quota without % sign should fail."""
        with pytest.raises(ValueError, match="CPU_QUOTA"):
            self._make_config(monkeypatch, cpu_quota="80")

    def test_validate_rejects_cpu_quota_with_letters(self, monkeypatch):
        """cpu_quota with letters should fail."""
        with pytest.raises(ValueError, match="CPU_QUOTA"):
            self._make_config(monkeypatch, cpu_quota="eighty%")

    # --- None values (skipped) ---

    def test_validate_skips_none_memory_max(self, monkeypatch):
        """memory_max=None should be valid (not set)."""
        cfg = self._make_config(monkeypatch, memory_max=None)
        cfg.validate()  # should not raise

    def test_validate_skips_none_cpu_quota(self, monkeypatch):
        """cpu_quota=None should be valid (not set)."""
        cfg = self._make_config(monkeypatch, cpu_quota=None)
        cfg.validate()  # should not raise


class TestConfigValidateValkeyService:
    """Test Config.validate() for valkey_service field."""

    def _make_config(self, monkeypatch, **overrides):
        """Create a Config with valid defaults, applying overrides."""
        monkeypatch.delenv("IMAGE", raising=False)
        monkeypatch.delenv("TAG", raising=False)
        monkeypatch.delenv("MEMORY_MAX", raising=False)
        monkeypatch.delenv("CPU_QUOTA", raising=False)
        monkeypatch.delenv("OTS_VALKEY_SERVICE", raising=False)

        from rots.config import Config

        return Config(**overrides)

    @pytest.mark.parametrize(
        "value",
        [
            "valkey-server@6379.service",
            "redis.service",
            "nginx.service",
            "my-app@80.service",
        ],
    )
    def test_validate_accepts_valid_valkey_service(self, monkeypatch, value):
        """Valid systemd unit names should pass validation."""
        cfg = self._make_config(monkeypatch, valkey_service=value)
        cfg.validate()  # should not raise

    def test_validate_rejects_valkey_service_with_newline(self, monkeypatch):
        """valkey_service with newline injection should fail."""
        with pytest.raises(ValueError, match="OTS_VALKEY_SERVICE"):
            self._make_config(monkeypatch, valkey_service="valkey.service\nExecStart=/malicious")

    def test_validate_rejects_valkey_service_with_space(self, monkeypatch):
        """valkey_service with spaces should fail."""
        with pytest.raises(ValueError, match="OTS_VALKEY_SERVICE"):
            self._make_config(monkeypatch, valkey_service="valkey .service")

    def test_validate_skips_none_valkey_service(self, monkeypatch):
        """valkey_service=None should be valid (not set)."""
        cfg = self._make_config(monkeypatch, valkey_service=None)
        cfg.validate()  # should not raise


class TestConfigResolveImageTagWithOverride:
    """Test the dataclasses.replace pattern for image reference overrides.

    Verifies that creating a new Config via dataclasses.replace(cfg, image=..., tag=...)
    correctly resolves through resolve_image_tag(). This is the pattern used by commands
    that accept a positional image reference argument.
    """

    def test_replace_with_concrete_tag_skips_alias_lookup(self, monkeypatch, mocker):
        """A concrete tag (not @current/@rollback) should pass through without DB lookup."""
        import dataclasses

        monkeypatch.delenv("IMAGE", raising=False)
        monkeypatch.delenv("TAG", raising=False)

        from rots.config import Config

        cfg = Config()
        new_cfg = dataclasses.replace(cfg, image="custom/image", tag="v1.0.0")

        # Mock the db module so we can verify it's not called for concrete tags
        mock_get_alias = mocker.patch("rots.db.get_alias")

        image, tag = new_cfg.resolve_image_tag()
        assert image == "custom/image"
        assert tag == "v1.0.0"
        mock_get_alias.assert_not_called()

    def test_replace_preserves_default_tag_alias_resolution(self, monkeypatch, mocker):
        """Replacing only image preserves @current tag, which triggers alias lookup."""
        import dataclasses

        monkeypatch.delenv("IMAGE", raising=False)
        monkeypatch.delenv("TAG", raising=False)

        from rots.config import DEFAULT_TAG, Config

        cfg = Config()
        assert cfg.tag == DEFAULT_TAG  # @current

        new_cfg = dataclasses.replace(cfg, image="custom/image")
        assert new_cfg.tag == DEFAULT_TAG

        # Mock alias lookup to return a resolved value
        mock_alias = mocker.MagicMock()
        mock_alias.image = "custom/image"
        mock_alias.tag = "v0.23.0"
        mocker.patch("rots.db.get_alias", return_value=mock_alias)

        image, tag = new_cfg.resolve_image_tag()
        assert image == "custom/image"
        assert tag == "v0.23.0"

    def test_replace_does_not_mutate_original(self, monkeypatch):
        """dataclasses.replace should create a new Config without modifying the original."""
        import dataclasses

        monkeypatch.delenv("IMAGE", raising=False)
        monkeypatch.delenv("TAG", raising=False)

        from rots.config import DEFAULT_IMAGE, DEFAULT_TAG, Config

        cfg = Config()
        new_cfg = dataclasses.replace(cfg, image="new/image", tag="new-tag")

        assert cfg.image == DEFAULT_IMAGE
        assert cfg.tag == DEFAULT_TAG
        assert new_cfg.image == "new/image"
        assert new_cfg.tag == "new-tag"

    def test_replace_with_env_image_then_positional_override(self, monkeypatch, mocker):
        """Positional ref image should override IMAGE env via replace."""
        import dataclasses

        monkeypatch.setenv("IMAGE", "env/image")
        monkeypatch.setenv("TAG", "env-tag")

        from rots.config import Config

        cfg = Config()
        assert cfg.image == "env/image"
        assert cfg.tag == "env-tag"

        # Simulate positional override
        new_cfg = dataclasses.replace(cfg, image="pos/image", tag="pos-tag")

        mock_get_alias = mocker.patch("rots.db.get_alias")
        image, tag = new_cfg.resolve_image_tag()
        assert image == "pos/image"
        assert tag == "pos-tag"
        mock_get_alias.assert_not_called()

    def test_replace_with_alias_not_set_returns_literal_tag(self, monkeypatch, mocker):
        """When @current alias is not set, resolve_image_tag returns the literal tag."""
        import dataclasses

        monkeypatch.delenv("IMAGE", raising=False)
        monkeypatch.delenv("TAG", raising=False)

        from rots.config import Config

        cfg = Config()
        new_cfg = dataclasses.replace(cfg, image="custom/image")

        # Alias lookup returns None (no alias set)
        mocker.patch("rots.db.get_alias", return_value=None)

        image, tag = new_cfg.resolve_image_tag()
        assert image == "custom/image"
        assert tag == "@current"  # literal sentinel returned when no alias


class TestConfigDataclassesReplace:
    """Verify validate() fires on dataclasses.replace(), not just construction."""

    def test_replace_rejects_bad_tag(self, monkeypatch):
        import dataclasses

        monkeypatch.delenv("IMAGE", raising=False)
        monkeypatch.delenv("TAG", raising=False)
        monkeypatch.delenv("OTS_REGISTRY", raising=False)

        from rots.config import Config

        cfg = Config()
        with pytest.raises(ValueError, match="Invalid tag"):
            dataclasses.replace(cfg, tag="$(whoami)")

    def test_replace_rejects_bad_image(self, monkeypatch):
        import dataclasses

        monkeypatch.delenv("IMAGE", raising=False)
        monkeypatch.delenv("TAG", raising=False)
        monkeypatch.delenv("OTS_REGISTRY", raising=False)

        from rots.config import Config

        cfg = Config()
        with pytest.raises(ValueError, match="Invalid image"):
            dataclasses.replace(cfg, image="../evil")

    def test_replace_rejects_bad_registry(self, monkeypatch):
        import dataclasses

        monkeypatch.delenv("IMAGE", raising=False)
        monkeypatch.delenv("TAG", raising=False)
        monkeypatch.delenv("OTS_REGISTRY", raising=False)

        from rots.config import Config

        cfg = Config()
        with pytest.raises(ValueError, match="Invalid OTS_REGISTRY"):
            dataclasses.replace(cfg, registry="reg/../evil")

    def test_replace_accepts_valid_overrides(self, monkeypatch):
        import dataclasses

        monkeypatch.delenv("IMAGE", raising=False)
        monkeypatch.delenv("TAG", raising=False)
        monkeypatch.delenv("OTS_REGISTRY", raising=False)

        from rots.config import Config

        cfg = Config()
        new_cfg = dataclasses.replace(cfg, image="ghcr.io/other/image", tag="v2.0")
        assert new_cfg.image == "ghcr.io/other/image"
        assert new_cfg.tag == "v2.0"


class TestImageExplicitFlag:
    """Test _image_explicit flag through resolve_image_tag()."""

    def test_no_image_env_sets_explicit_false(self, monkeypatch):
        """Config without IMAGE env should have _image_explicit=False."""
        monkeypatch.delenv("IMAGE", raising=False)
        monkeypatch.delenv("TAG", raising=False)
        from rots.config import Config

        cfg = Config()
        assert cfg._image_explicit is False

    def test_image_env_sets_explicit_true(self, monkeypatch):
        """Config with IMAGE env should auto-detect _image_explicit=True."""
        monkeypatch.setenv("IMAGE", "ghcr.io/org/img")
        monkeypatch.delenv("TAG", raising=False)
        from rots.config import Config

        cfg = Config()
        assert cfg._image_explicit is True

    def test_explicit_flag_via_replace(self, monkeypatch):
        """dataclasses.replace with _image_explicit=True should persist."""
        import dataclasses

        monkeypatch.delenv("IMAGE", raising=False)
        monkeypatch.delenv("TAG", raising=False)
        from rots.config import Config

        cfg = Config()
        assert cfg._image_explicit is False

        new_cfg = dataclasses.replace(cfg, image="custom/img", _image_explicit=True)
        assert new_cfg._image_explicit is True

    def test_resolve_uses_alias_image_when_not_explicit(self, monkeypatch):
        """When _image_explicit is False, resolve_image_tag should use alias image."""
        monkeypatch.delenv("IMAGE", raising=False)
        monkeypatch.delenv("TAG", raising=False)
        from unittest.mock import patch

        from rots.config import Config

        cfg = Config()
        assert cfg._image_explicit is False

        alias = MagicMock()
        alias.image = "alias/image"
        alias.tag = "v1.0.0"

        with patch("rots.db.get_alias", return_value=alias):
            image, tag = cfg.resolve_image_tag()
            assert image == "alias/image"
            assert tag == "v1.0.0"

    def test_resolve_keeps_caller_image_when_explicit(self, monkeypatch):
        """When _image_explicit is True, resolve_image_tag should keep cfg.image."""
        monkeypatch.setenv("IMAGE", "ghcr.io/explicit/img")
        monkeypatch.delenv("TAG", raising=False)
        from unittest.mock import patch

        from rots.config import Config

        cfg = Config()
        assert cfg._image_explicit is True

        alias = MagicMock()
        alias.image = "alias/image"
        alias.tag = "v1.0.0"

        with patch("rots.db.get_alias", return_value=alias):
            image, tag = cfg.resolve_image_tag()
            assert image == "ghcr.io/explicit/img"
            assert tag == "v1.0.0"

    def test_cli_positional_override_preserves_through_resolve(self, monkeypatch):
        """CLI positional ref via replace(_image_explicit=True) should survive resolve."""
        import dataclasses
        from unittest.mock import patch

        monkeypatch.delenv("IMAGE", raising=False)
        monkeypatch.delenv("TAG", raising=False)
        from rots.config import Config

        cfg = Config()
        cfg = dataclasses.replace(cfg, image="cli/image", tag="v2.0", _image_explicit=True)

        alias = MagicMock()
        alias.image = "alias/image"
        alias.tag = "v1.0.0"

        with patch("rots.db.get_alias", return_value=alias):
            # With explicit tag "v2.0" (not an alias), resolve returns as-is
            image, tag = cfg.resolve_image_tag()
            assert image == "cli/image"
            assert tag == "v2.0"


class TestResolvedImageWithTagRegistry:
    """Test resolved_image_with_tag() with registry prefix."""

    def test_no_registry_returns_plain_image(self, monkeypatch):
        """Without registry, returns image:tag without prefix."""
        monkeypatch.delenv("IMAGE", raising=False)
        monkeypatch.delenv("TAG", raising=False)
        monkeypatch.delenv("OTS_REGISTRY", raising=False)
        from unittest.mock import patch

        from rots.config import Config

        cfg = Config(tag="v1.0.0")

        with patch("rots.db.get_alias", return_value=None):
            result = cfg.resolved_image_with_tag()
            assert result == f"{cfg.image}:v1.0.0"
            assert "registry" not in result.lower()

    def test_registry_prefixes_image(self, monkeypatch):
        """With OTS_REGISTRY, should strip source registry and prepend new one."""
        monkeypatch.delenv("IMAGE", raising=False)
        monkeypatch.delenv("TAG", raising=False)
        monkeypatch.setenv("OTS_REGISTRY", "registry.example.com")
        from unittest.mock import patch

        from rots.config import Config

        cfg = Config(tag="v1.0.0")

        with patch("rots.db.get_alias", return_value=None):
            result = cfg.resolved_image_with_tag()
            # Default IMAGE ghcr.io/onetimesecret/onetimesecret -> strip ghcr.io
            assert result == "registry.example.com/onetimesecret/onetimesecret:v1.0.0"

    def test_registry_strips_source_registry_only(self, monkeypatch):
        """Registry should strip only the source hostname, keeping image path."""
        monkeypatch.setenv("IMAGE", "ghcr.io/custom/org/myapp")
        monkeypatch.delenv("TAG", raising=False)
        monkeypatch.setenv("OTS_REGISTRY", "private.registry.io")
        from unittest.mock import patch

        from rots.config import Config

        cfg = Config(tag="v2.0.0")

        with patch("rots.db.get_alias", return_value=None):
            result = cfg.resolved_image_with_tag()
            # ghcr.io is the registry -> strip it, keep custom/org/myapp
            assert result == "private.registry.io/custom/org/myapp:v2.0.0"


class TestPodmanAuthArgs:
    """Test podman_auth_args() helper."""

    def test_no_registry_returns_empty(self, monkeypatch):
        """Without registry, should return empty list."""
        monkeypatch.delenv("OTS_REGISTRY", raising=False)
        monkeypatch.delenv("IMAGE", raising=False)
        monkeypatch.delenv("TAG", raising=False)
        from rots.config import Config

        cfg = Config()
        assert cfg.podman_auth_args() == []

    def test_with_registry_returns_authfile_args(self, monkeypatch):
        """With registry, should return --authfile args."""
        monkeypatch.setenv("OTS_REGISTRY", "registry.example.com")
        monkeypatch.delenv("IMAGE", raising=False)
        monkeypatch.delenv("TAG", raising=False)
        from rots.config import Config

        cfg = Config()
        args = cfg.podman_auth_args()
        assert len(args) == 2
        assert args[0] == "--authfile"
        assert isinstance(args[1], str)


class TestJoinImageTag:
    """Test join_image_tag() utility function."""

    def test_named_tag(self):
        """Named tag should use colon separator."""
        from rots.config import join_image_tag

        assert join_image_tag("ghcr.io/org/image", "v1.0") == "ghcr.io/org/image:v1.0"

    def test_digest_tag(self):
        """Digest tag (starting with @) should use @ separator, not colon."""
        from rots.config import join_image_tag

        assert (
            join_image_tag("ghcr.io/org/image", "@sha256:abc123")
            == "ghcr.io/org/image@sha256:abc123"
        )

    def test_simple_image_latest(self):
        """Simple image name with latest tag."""
        from rots.config import join_image_tag

        assert join_image_tag("myapp", "latest") == "myapp:latest"

    def test_sentinel_current(self):
        """Sentinel @current should use @ separator."""
        from rots.config import join_image_tag

        assert join_image_tag("myapp", "@current") == "myapp@current"

    def test_sentinel_rollback(self):
        """Sentinel @rollback should use @ separator."""
        from rots.config import join_image_tag

        assert join_image_tag("myapp", "@rollback") == "myapp@rollback"


class TestValidateRejectsEmbeddedTag:
    """Test that Config.validate() rejects IMAGE values with embedded tags."""

    def test_rejects_image_with_tag(self, monkeypatch):
        """IMAGE containing a tag after the last slash should be rejected."""
        monkeypatch.delenv("IMAGE", raising=False)
        monkeypatch.delenv("TAG", raising=False)
        from rots.config import Config

        with pytest.raises(ValueError, match="IMAGE should not include a tag"):
            Config(image="ghcr.io/org/app:v1.0")

    def test_accepts_registry_port_without_tag(self, monkeypatch):
        """Registry port (colon before last slash) should be allowed."""
        monkeypatch.delenv("IMAGE", raising=False)
        monkeypatch.delenv("TAG", raising=False)
        from rots.config import Config

        cfg = Config(image="registry:5000/org/app")
        cfg.validate()  # should not raise

    def test_accepts_simple_image(self, monkeypatch):
        """Simple image without slashes or colons should be allowed."""
        monkeypatch.delenv("IMAGE", raising=False)
        monkeypatch.delenv("TAG", raising=False)
        from rots.config import Config

        cfg = Config(image="onetimesecret/onetimesecret")
        cfg.validate()  # should not raise

    def test_rejects_image_with_latest_tag(self, monkeypatch):
        """IMAGE with :latest should be rejected."""
        monkeypatch.delenv("IMAGE", raising=False)
        monkeypatch.delenv("TAG", raising=False)
        from rots.config import Config

        with pytest.raises(ValueError, match="IMAGE should not include a tag"):
            Config(image="docker.io/org/app:latest")

    def test_accepts_registry_port_only(self, monkeypatch):
        """Image like registry:5000/image (colon before slash) should be allowed."""
        monkeypatch.delenv("IMAGE", raising=False)
        monkeypatch.delenv("TAG", raising=False)
        from rots.config import Config

        cfg = Config(image="registry:5000/image")
        cfg.validate()  # should not raise

    def test_rejects_bare_image_with_tag(self, monkeypatch):
        """myapp:v1.0 (no slash) should be rejected."""
        monkeypatch.delenv("IMAGE", raising=False)
        monkeypatch.delenv("TAG", raising=False)
        from rots.config import Config

        with pytest.raises(ValueError, match="should not include a tag"):
            Config(image="myapp:v1.0", tag="latest")


class TestImageWithTagDigest:
    """Test image_with_tag property handles digest tags correctly via join_image_tag."""

    def test_image_with_tag_digest(self, monkeypatch):
        """image_with_tag with a digest tag should use @ separator."""
        monkeypatch.delenv("IMAGE", raising=False)
        monkeypatch.delenv("TAG", raising=False)
        from rots.config import Config

        cfg = Config(tag="@sha256:abc123")
        assert "@sha256:abc123" in cfg.image_with_tag
        assert ":@sha256" not in cfg.image_with_tag

    def test_image_with_tag_named(self, monkeypatch):
        """image_with_tag with a named tag should use colon separator."""
        monkeypatch.setenv("IMAGE", "myregistry/myimage")
        monkeypatch.setenv("TAG", "v1.0")
        from rots.config import Config

        cfg = Config()
        assert cfg.image_with_tag == "myregistry/myimage:v1.0"


class TestResolvedImageWithTagDigest:
    """Test resolved_image_with_tag() produces correct output for digest tags with registry."""

    def test_digest_tag_with_registry(self, monkeypatch):
        """With OTS_REGISTRY and digest tag, should use @ separator."""
        monkeypatch.delenv("IMAGE", raising=False)
        monkeypatch.setenv("TAG", "@sha256:deadbeef")
        monkeypatch.setenv("OTS_REGISTRY", "registry.example.com")
        from unittest.mock import patch

        from rots.config import Config

        cfg = Config()

        with patch("rots.db.get_alias", return_value=None):
            result = cfg.resolved_image_with_tag()
            assert result == "registry.example.com/onetimesecret/onetimesecret@sha256:deadbeef"
            assert ":@sha256" not in result

    def test_digest_tag_without_registry(self, monkeypatch):
        """Without registry and digest tag, should use @ separator."""
        monkeypatch.delenv("IMAGE", raising=False)
        monkeypatch.setenv("TAG", "@sha256:deadbeef")
        monkeypatch.delenv("OTS_REGISTRY", raising=False)
        from unittest.mock import patch

        from rots.config import Config

        cfg = Config()

        with patch("rots.db.get_alias", return_value=None):
            result = cfg.resolved_image_with_tag()
            assert "onetimesecret@sha256:deadbeef" in result
            assert ":@sha256" not in result
