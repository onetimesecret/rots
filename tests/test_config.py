# tests/test_config.py
"""Tests for config module - Config dataclass."""

from pathlib import Path


class TestConfigDefaults:
    """Test Config dataclass default values."""

    def test_default_config_dir(self):
        """Should default to /etc/onetimesecret."""
        from ots_containers.config import Config

        cfg = Config()
        assert cfg.config_dir == Path("/etc/onetimesecret")

    def test_default_var_dir(self):
        """Should default to /var/lib/onetimesecret."""
        from ots_containers.config import Config

        cfg = Config()
        assert cfg.var_dir == Path("/var/lib/onetimesecret")

    def test_default_web_template_path(self):
        """Should default to systemd quadlet location for web."""
        from ots_containers.config import Config

        cfg = Config()
        assert cfg.web_template_path == Path("/etc/containers/systemd/onetime-web@.container")

    def test_default_worker_template_path(self):
        """Should default to systemd quadlet location for worker."""
        from ots_containers.config import Config

        cfg = Config()
        assert cfg.worker_template_path == Path("/etc/containers/systemd/onetime-worker@.container")

    def test_default_scheduler_template_path(self):
        """Should default to systemd quadlet location for scheduler."""
        from ots_containers.config import Config

        cfg = Config()
        assert cfg.scheduler_template_path == Path(
            "/etc/containers/systemd/onetime-scheduler@.container"
        )


class TestConfigImageSettings:
    """Test Config image-related settings."""

    def test_default_image(self, monkeypatch):
        """Should default to ghcr.io/onetimesecret/onetimesecret."""
        monkeypatch.delenv("IMAGE", raising=False)
        from ots_containers.config import Config

        cfg = Config()
        assert cfg.image == "ghcr.io/onetimesecret/onetimesecret"

    def test_image_from_env(self, monkeypatch):
        """Should use IMAGE env var when set."""
        monkeypatch.setenv("IMAGE", "custom/image")
        from ots_containers.config import Config

        cfg = Config()
        assert cfg.image == "custom/image"

    def test_default_tag(self, monkeypatch):
        """Should default to 'current'."""
        monkeypatch.delenv("TAG", raising=False)
        from ots_containers.config import Config

        cfg = Config()
        assert cfg.tag == "current"

    def test_tag_from_env(self, monkeypatch):
        """Should use TAG env var when set."""
        monkeypatch.setenv("TAG", "v1.2.3")
        from ots_containers.config import Config

        cfg = Config()
        assert cfg.tag == "v1.2.3"

    def test_image_with_tag_property(self, monkeypatch):
        """Should combine image and tag correctly."""
        monkeypatch.setenv("IMAGE", "myregistry/myimage")
        monkeypatch.setenv("TAG", "latest")
        from ots_containers.config import Config

        cfg = Config()
        assert cfg.image_with_tag == "myregistry/myimage:latest"


class TestConfigPaths:
    """Test Config path properties and methods."""

    def test_config_yaml_path(self):
        """Should return correct path for config.yaml."""
        from ots_containers.config import Config

        cfg = Config(config_dir=Path("/etc/ots"))
        assert cfg.config_yaml == Path("/etc/ots/config.yaml")

    def test_db_path_with_writable_var_dir(self, tmp_path):
        """Should use system path when var_dir is writable."""
        from ots_containers.config import Config

        cfg = Config(var_dir=tmp_path)
        assert cfg.db_path == tmp_path / "deployments.db"

    def test_db_path_falls_back_to_user_space(self):
        """Should fall back to ~/.local/share when var_dir not writable."""
        from ots_containers.config import Config

        # Non-existent path triggers fallback
        cfg = Config(var_dir=Path("/nonexistent/path"))
        assert ".local/share/ots-containers/deployments.db" in str(cfg.db_path)


class TestConfigValidate:
    """Test Config.validate method."""

    def test_validate_is_noop(self, tmp_path):
        """Should not raise even when config files are missing (validate is a no-op)."""
        from ots_containers.config import Config

        cfg = Config(config_dir=tmp_path)
        cfg.validate()  # Should not raise

    def test_validate_with_all_files(self, tmp_path):
        """Should not raise when all required files exist."""
        from ots_containers.config import Config

        # Only config.yaml is required now (secrets via Podman, infra env in /etc/default)
        (tmp_path / "config.yaml").touch()

        cfg = Config(config_dir=tmp_path)
        cfg.validate()  # Should not raise


class TestConfigFiles:
    """Test CONFIG_FILES module-level constant."""

    def test_config_files_contains_expected_files(self):
        """CONFIG_FILES should list the four known config files."""
        from ots_containers.config import CONFIG_FILES

        assert "config.yaml" in CONFIG_FILES
        assert "auth.yaml" in CONFIG_FILES
        assert "logging.yaml" in CONFIG_FILES
        assert "billing.yaml" in CONFIG_FILES

    def test_config_files_length(self):
        """CONFIG_FILES should contain exactly 4 entries."""
        from ots_containers.config import CONFIG_FILES

        assert len(CONFIG_FILES) == 4

    def test_config_files_is_tuple(self):
        """CONFIG_FILES should be a tuple (immutable)."""
        from ots_containers.config import CONFIG_FILES

        assert isinstance(CONFIG_FILES, tuple)


class TestExistingConfigFiles:
    """Test Config.existing_config_files property."""

    def test_returns_empty_when_config_dir_missing(self):
        """Should return empty list when config_dir does not exist."""
        from ots_containers.config import Config

        cfg = Config(config_dir=Path("/nonexistent/config/dir"))
        assert cfg.existing_config_files == []

    def test_returns_empty_when_no_yaml_files(self, tmp_path):
        """Should return empty list when config_dir exists but has no yaml files."""
        from ots_containers.config import Config

        config_dir = tmp_path / "etc"
        config_dir.mkdir()

        cfg = Config(config_dir=config_dir)
        assert cfg.existing_config_files == []

    def test_returns_only_existing_files(self, tmp_path):
        """Should return only files that actually exist on disk."""
        from ots_containers.config import Config

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
        from ots_containers.config import Config

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
        from ots_containers.config import Config

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
        from ots_containers.config import Config

        cfg = Config(config_dir=Path("/nonexistent/config/dir"))
        assert cfg.has_custom_config is False

    def test_false_when_empty_config_dir(self, tmp_path):
        """Should return False when config_dir exists but is empty."""
        from ots_containers.config import Config

        config_dir = tmp_path / "etc"
        config_dir.mkdir()

        cfg = Config(config_dir=config_dir)
        assert cfg.has_custom_config is False

    def test_true_when_one_yaml_exists(self, tmp_path):
        """Should return True when at least one config file exists."""
        from ots_containers.config import Config

        config_dir = tmp_path / "etc"
        config_dir.mkdir()
        (config_dir / "config.yaml").touch()

        cfg = Config(config_dir=config_dir)
        assert cfg.has_custom_config is True

    def test_true_when_all_yaml_files_exist(self, tmp_path):
        """Should return True when all config files exist."""
        from ots_containers.config import Config

        config_dir = tmp_path / "etc"
        config_dir.mkdir()
        (config_dir / "config.yaml").touch()
        (config_dir / "auth.yaml").touch()
        (config_dir / "logging.yaml").touch()

        cfg = Config(config_dir=config_dir)
        assert cfg.has_custom_config is True


class TestConfigRegistry:
    """Test Config.registry field from OTS_REGISTRY env var."""

    def test_registry_from_env(self, monkeypatch):
        """Should read OTS_REGISTRY env var."""
        monkeypatch.setenv("OTS_REGISTRY", "registry.example.com/myorg")
        from ots_containers.config import Config

        cfg = Config()
        assert cfg.registry == "registry.example.com/myorg"

    def test_registry_defaults_to_none(self, monkeypatch):
        """Should default to None when OTS_REGISTRY is absent."""
        monkeypatch.delenv("OTS_REGISTRY", raising=False)
        from ots_containers.config import Config

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
        from ots_containers.config import Config

        cfg = Config()
        assert cfg.registry_auth_file == auth_file

    def test_explicit_override_takes_highest_priority(self, monkeypatch, tmp_path):
        """_registry_auth_file field should override even REGISTRY_AUTH_FILE env var."""
        env_file = tmp_path / "env-auth.json"
        explicit_file = tmp_path / "explicit-auth.json"
        monkeypatch.setenv("REGISTRY_AUTH_FILE", str(env_file))
        from ots_containers.config import Config

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
        from ots_containers.config import Config

        cfg = Config()
        assert cfg.registry_auth_file == auth_file

    def test_xdg_runtime_dir_skipped_when_file_missing(self, monkeypatch, tmp_path):
        """Should skip XDG_RUNTIME_DIR when auth.json does not exist there."""
        monkeypatch.delenv("REGISTRY_AUTH_FILE", raising=False)
        xdg_dir = tmp_path / "run" / "user" / "1000"
        xdg_dir.mkdir(parents=True)
        # Do NOT create containers/auth.json
        monkeypatch.setenv("XDG_RUNTIME_DIR", str(xdg_dir))
        from ots_containers.config import Config

        cfg = Config()
        # Should fall through to user config or system path, not the XDG path
        assert cfg.registry_auth_file != xdg_dir / "containers" / "auth.json"

    def test_user_config_fallback_on_macos(self, monkeypatch):
        """On macOS (darwin), should return ~/.config/containers/auth.json."""
        import sys

        monkeypatch.delenv("REGISTRY_AUTH_FILE", raising=False)
        monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
        from ots_containers.config import Config

        cfg = Config()
        # On macOS (our test platform), should return user config path
        if sys.platform == "darwin":
            expected = Path.home() / ".config" / "containers" / "auth.json"
            assert cfg.registry_auth_file == expected


class TestConfigPrivateImage:
    """Test Config.private_image and private_image_with_tag properties."""

    def test_private_image_none_when_no_registry(self, monkeypatch):
        """Should return None when registry is None."""
        monkeypatch.delenv("OTS_REGISTRY", raising=False)
        from ots_containers.config import Config

        cfg = Config()
        assert cfg.private_image is None

    def test_private_image_with_registry(self, monkeypatch):
        """Should return formatted string when registry is set."""
        monkeypatch.setenv("OTS_REGISTRY", "registry.example.com/myorg")
        from ots_containers.config import Config

        cfg = Config()
        assert cfg.private_image == "registry.example.com/myorg/onetimesecret"

    def test_private_image_with_tag_none_when_no_registry(self, monkeypatch):
        """Should return None when registry is None."""
        monkeypatch.delenv("OTS_REGISTRY", raising=False)
        from ots_containers.config import Config

        cfg = Config()
        assert cfg.private_image_with_tag is None

    def test_private_image_with_tag(self, monkeypatch):
        """Should combine registry, image name, and tag."""
        monkeypatch.setenv("OTS_REGISTRY", "registry.example.com/myorg")
        monkeypatch.setenv("TAG", "v2.0.0")
        from ots_containers.config import Config

        cfg = Config()
        assert cfg.private_image_with_tag == "registry.example.com/myorg/onetimesecret:v2.0.0"

    def test_private_image_with_tag_uses_default_tag(self, monkeypatch):
        """Should use default tag when TAG env var is absent."""
        monkeypatch.setenv("OTS_REGISTRY", "registry.example.com/myorg")
        monkeypatch.delenv("TAG", raising=False)
        from ots_containers.config import Config

        cfg = Config()
        assert cfg.private_image_with_tag == "registry.example.com/myorg/onetimesecret:current"


class TestConfigResolveImageTag:
    """Test Config.resolve_image_tag() with a real SQLite database."""

    def test_resolves_current_alias(self, monkeypatch, tmp_path):
        """Should resolve 'current' tag to the aliased image and tag from the database."""
        from ots_containers import db
        from ots_containers.config import Config

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
        from ots_containers import db
        from ots_containers.config import Config

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

    def test_falls_back_to_literal_tag_when_no_alias(self, monkeypatch, tmp_path):
        """Should return literal image/tag when 'current' has no alias in the database."""
        from ots_containers import db
        from ots_containers.config import Config

        db_path = tmp_path / "deployments.db"
        db.init_db(db_path)
        # Database is empty - no aliases set

        monkeypatch.delenv("TAG", raising=False)
        monkeypatch.delenv("IMAGE", raising=False)
        cfg = Config(var_dir=tmp_path)
        image, tag = cfg.resolve_image_tag()
        assert image == "ghcr.io/onetimesecret/onetimesecret"
        assert tag == "current"

    def test_non_alias_tag_passes_through(self, monkeypatch, tmp_path):
        """A concrete tag like 'v3.0.0' should pass through without database lookup."""
        from ots_containers.config import Config

        monkeypatch.setenv("TAG", "v3.0.0")
        monkeypatch.setenv("IMAGE", "myregistry/myimage")
        cfg = Config(var_dir=tmp_path)
        image, tag = cfg.resolve_image_tag()
        assert image == "myregistry/myimage"
        assert tag == "v3.0.0"
