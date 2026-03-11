# tests/commands/test_init.py
"""Tests for init command with permission handling."""

import logging
from pathlib import Path

import pytest
from ots_shared.ssh import LocalExecutor


def _mock_config(mocker, tmp_path, **overrides):
    """Build a mock Config that returns a LocalExecutor.

    Ensures init always takes the local code path in tests.
    """
    quadlet_dir = overrides.get("quadlet_dir", tmp_path / "etc" / "containers" / "systemd")

    mock = mocker.MagicMock()
    mock.config_dir = overrides.get("config_dir", tmp_path / "etc" / "onetimesecret")
    mock.var_dir = overrides.get("var_dir", tmp_path / "var" / "lib" / "onetimesecret")
    mock.web_template_path = quadlet_dir / "onetime-web@.container"
    mock.worker_template_path = quadlet_dir / "onetime-worker@.container"
    mock.scheduler_template_path = quadlet_dir / "onetime-scheduler@.container"
    mock.config_yaml = mock.config_dir / "config.yaml"
    mock.db_path = mock.var_dir / "deployments.db"
    mock.get_executor.return_value = LocalExecutor()

    mocker.patch("rots.commands.init.Config", return_value=mock)
    return mock


class TestInitCommandImports:
    """Verify init command module imports correctly."""

    def test_init_app_exists(self):
        """Init app should be importable."""
        from rots.commands import init

        assert init.app is not None

    def test_init_function_exists(self):
        """init command should be defined."""
        from rots.commands import init

        assert hasattr(init, "init")
        assert callable(init.init)


class TestCreateDirectory:
    """Test _create_directory helper function."""

    def test_creates_directory_when_not_exists(self, tmp_path, mocker, caplog):
        """Should create directory and return True."""
        from rots.commands.init import _create_directory

        mocker.patch("os.chown")

        new_dir = tmp_path / "new_dir"
        with caplog.at_level(logging.INFO):
            result = _create_directory(new_dir, mode=0o755, quiet=False)

        assert result is True
        assert new_dir.exists()
        assert "[created]" in caplog.text

    def test_returns_false_when_exists(self, tmp_path, caplog):
        """Should return False and not recreate existing directory."""
        from rots.commands.init import _create_directory

        existing_dir = tmp_path / "existing"
        existing_dir.mkdir()

        with caplog.at_level(logging.INFO):
            result = _create_directory(existing_dir, mode=0o755, quiet=False)

        assert result is False
        assert "[ok]" in caplog.text

    def test_returns_none_on_permission_error(self, tmp_path, mocker, caplog):
        """Should return None and print denied message on PermissionError."""
        from rots.commands.init import _create_directory

        mocker.patch.object(Path, "mkdir", side_effect=PermissionError("denied"))

        new_dir = tmp_path / "denied_dir"
        with caplog.at_level(logging.ERROR):
            result = _create_directory(new_dir, mode=0o755, quiet=False)

        assert result is None
        assert "[denied]" in caplog.text
        assert "permission denied" in caplog.text

    def test_quiet_mode_suppresses_exists_output(self, tmp_path, caplog):
        """Should suppress output when quiet=True and dir exists."""
        from rots.commands.init import _create_directory

        existing_dir = tmp_path / "existing"
        existing_dir.mkdir()

        with caplog.at_level(logging.INFO):
            _create_directory(existing_dir, mode=0o755, quiet=True)

        # quiet=True suppresses logger.info calls (via if-guard)
        assert "[ok]" not in caplog.text

    def test_quiet_mode_suppresses_created_output(self, tmp_path, mocker, caplog):
        """Should suppress output when quiet=True and dir created."""
        from rots.commands.init import _create_directory

        mocker.patch("os.chown")

        new_dir = tmp_path / "new_dir"
        with caplog.at_level(logging.INFO):
            _create_directory(new_dir, mode=0o755, quiet=True)

        # quiet=True suppresses logger.info calls (via if-guard)
        assert "[created]" not in caplog.text

    def test_permission_error_always_prints(self, tmp_path, mocker, caplog):
        """Permission errors should always print, even in quiet mode."""
        from rots.commands.init import _create_directory

        mocker.patch.object(Path, "mkdir", side_effect=PermissionError("denied"))

        new_dir = tmp_path / "denied_dir"
        with caplog.at_level(logging.ERROR):
            _create_directory(new_dir, mode=0o755, quiet=True)

        assert "[denied]" in caplog.text


class TestCopyTemplate:
    """Test _copy_template helper function."""

    def test_copies_file_when_dest_not_exists(self, tmp_path, mocker, caplog):
        """Should copy file and return True."""
        from rots.commands.init import _copy_template

        mocker.patch("os.chown")

        src = tmp_path / "source.txt"
        src.write_text("content")
        dest = tmp_path / "dest.txt"

        with caplog.at_level(logging.INFO):
            result = _copy_template(src, dest, quiet=False)

        assert result is True
        assert dest.exists()
        assert dest.read_text() == "content"
        assert "[copied]" in caplog.text

    def test_returns_false_when_dest_exists(self, tmp_path, caplog):
        """Should return False when destination exists."""
        from rots.commands.init import _copy_template

        src = tmp_path / "source.txt"
        src.write_text("new content")
        dest = tmp_path / "dest.txt"
        dest.write_text("old content")

        with caplog.at_level(logging.INFO):
            result = _copy_template(src, dest, quiet=False)

        assert result is False
        assert dest.read_text() == "old content"  # unchanged
        assert "[ok]" in caplog.text

    def test_returns_false_when_source_missing(self, tmp_path, caplog):
        """Should return False when source doesn't exist."""
        from rots.commands.init import _copy_template

        src = tmp_path / "nonexistent.txt"
        dest = tmp_path / "dest.txt"

        with caplog.at_level(logging.INFO):
            result = _copy_template(src, dest, quiet=False)

        assert result is False
        assert not dest.exists()
        assert "[skip]" in caplog.text
        assert "not found" in caplog.text

    def test_returns_none_on_permission_error(self, tmp_path, mocker, caplog):
        """Should return None on PermissionError."""
        from rots.commands.init import _copy_template

        mocker.patch("shutil.copy2", side_effect=PermissionError("denied"))

        src = tmp_path / "source.txt"
        src.write_text("content")
        dest = tmp_path / "dest.txt"

        with caplog.at_level(logging.ERROR):
            result = _copy_template(src, dest, quiet=False)

        assert result is None
        assert "[denied]" in caplog.text
        assert "permission denied" in caplog.text


class TestCopyTemplateRemote:
    """Test _copy_template remote path using cp -p."""

    def test_remote_uses_cp_p_not_shutil(self, mocker, capsys):
        """Remote execution should use 'cp -p src dest' via executor.run, not shutil.copy2."""
        from rots.commands.init import _copy_template

        src = Path("/etc/onetimesecret/config.yaml.example")
        dest = Path("/etc/onetimesecret/config.yaml")

        mock_executor = mocker.MagicMock()
        # cp -p succeeds
        mock_executor.run.return_value = mocker.MagicMock(ok=True, stderr="")

        # Mock is_remote to return True (used inside _copy_template)
        mocker.patch("ots_shared.ssh.is_remote", return_value=True)

        # Mock _path_exists: dest does not exist, src exists
        mocker.patch(
            "rots.commands.init._path_exists",
            side_effect=[False, True],
        )
        mock_copy2 = mocker.patch("shutil.copy2")

        result = _copy_template(src, dest, quiet=True, executor=mock_executor)

        assert result is True
        mock_executor.run.assert_called_once_with(["cp", "-p", str(src), str(dest)], sudo=True)
        mock_copy2.assert_not_called()

    def test_remote_failure_returns_none(self, mocker, caplog):
        """Remote copy failure (non-zero exit) should return None."""
        from rots.commands.init import _copy_template

        src = Path("/etc/onetimesecret/config.yaml.example")
        dest = Path("/etc/onetimesecret/config.yaml")

        mock_executor = mocker.MagicMock()
        mock_executor.run.return_value = mocker.MagicMock(ok=False, stderr="Permission denied")

        mocker.patch("ots_shared.ssh.is_remote", return_value=True)
        mocker.patch(
            "rots.commands.init._path_exists",
            side_effect=[False, True],
        )

        with caplog.at_level(logging.ERROR):
            result = _copy_template(src, dest, quiet=False, executor=mock_executor)

        assert result is None
        assert "[denied]" in caplog.text

    def test_remote_prints_copied_on_success(self, mocker, caplog):
        """Remote copy should print [copied] message when not quiet."""
        from rots.commands.init import _copy_template

        src = Path("/etc/onetimesecret/config.yaml.example")
        dest = Path("/etc/onetimesecret/config.yaml")

        mock_executor = mocker.MagicMock()
        mock_executor.run.return_value = mocker.MagicMock(ok=True, stderr="")

        mocker.patch("ots_shared.ssh.is_remote", return_value=True)
        mocker.patch(
            "rots.commands.init._path_exists",
            side_effect=[False, True],
        )

        with caplog.at_level(logging.INFO):
            result = _copy_template(src, dest, quiet=False, executor=mock_executor)

        assert result is True
        assert "[copied]" in caplog.text

    def test_remote_dest_exists_skips_copy(self, mocker):
        """Remote copy should skip when dest already exists."""
        from rots.commands.init import _copy_template

        src = Path("/etc/onetimesecret/config.yaml.example")
        dest = Path("/etc/onetimesecret/config.yaml")

        mock_executor = mocker.MagicMock()

        mocker.patch(
            "rots.commands.init._path_exists",
            side_effect=[True],  # dest exists
        )

        result = _copy_template(src, dest, quiet=True, executor=mock_executor)

        assert result is False
        mock_executor.run.assert_not_called()


class TestPathExists:
    """Test _path_exists helper function."""

    def test_local_existing_file_returns_true(self, tmp_path):
        """_path_exists should return True for an existing local file."""
        from rots.commands.init import _path_exists

        existing = tmp_path / "exists.txt"
        existing.touch()

        result = _path_exists(existing, executor=LocalExecutor())
        assert result is True

    def test_local_missing_file_returns_false(self, tmp_path):
        """_path_exists should return False for a non-existent local file."""
        from rots.commands.init import _path_exists

        missing = tmp_path / "missing.txt"

        result = _path_exists(missing, executor=LocalExecutor())
        assert result is False

    def test_local_existing_directory_returns_true(self, tmp_path):
        """_path_exists should return True for an existing local directory."""
        from rots.commands.init import _path_exists

        result = _path_exists(tmp_path, executor=LocalExecutor())
        assert result is True

    def test_remote_delegates_to_executor(self, mocker):
        """_path_exists should call executor.run(['test', '-e', path]) for remote."""
        from rots.commands.init import _path_exists

        mocker.patch("ots_shared.ssh.is_remote", return_value=True)

        mock_executor = mocker.MagicMock()
        mock_executor.run.return_value = mocker.MagicMock(ok=True)

        path = Path("/etc/onetimesecret/config.yaml")
        result = _path_exists(path, executor=mock_executor)

        assert result is True
        mock_executor.run.assert_called_once_with(["test", "-e", str(path)])

    def test_remote_returns_false_when_test_fails(self, mocker):
        """_path_exists should return False when remote 'test -e' fails."""
        from rots.commands.init import _path_exists

        mocker.patch("ots_shared.ssh.is_remote", return_value=True)

        mock_executor = mocker.MagicMock()
        mock_executor.run.return_value = mocker.MagicMock(ok=False)

        path = Path("/nonexistent/path")
        result = _path_exists(path, executor=mock_executor)

        assert result is False
        mock_executor.run.assert_called_once_with(["test", "-e", str(path)])


class TestInitCommand:
    """Test init command behavior."""

    def test_check_mode_reports_missing_directories(self, tmp_path, mocker, caplog):
        """Check mode should report missing directories without creating."""
        from rots.commands.init import init

        env_file = tmp_path / "etc" / "default" / "onetimesecret"

        _mock_config(mocker, tmp_path)
        mocker.patch("rots.commands.init.DEFAULT_ENV_FILE", env_file)

        with caplog.at_level(logging.INFO):
            result = init(check=True)

        assert result == 1  # Missing components (quadlet, var_dir)
        assert "[missing]" in caplog.text
        # Config files are now optional, so they show [optional]
        assert "[optional]" in caplog.text
        assert "Missing components" in caplog.text
        # Directories should NOT be created
        assert not (tmp_path / "etc" / "onetimesecret").exists()

    def test_check_mode_reports_ok_when_all_present(self, tmp_path, mocker, caplog):
        """Check mode should report OK when all components exist."""
        from rots.commands.init import init

        # Create directory structure
        config_dir = tmp_path / "etc" / "onetimesecret"
        var_dir = tmp_path / "var" / "lib" / "onetimesecret"
        quadlet_dir = tmp_path / "etc" / "containers" / "systemd"
        users_dir = quadlet_dir / "users"
        env_file = tmp_path / "etc" / "default" / "onetimesecret"

        config_dir.mkdir(parents=True)
        var_dir.mkdir(parents=True)
        quadlet_dir.mkdir(parents=True)
        users_dir.mkdir(parents=True)
        env_file.parent.mkdir(parents=True)
        env_file.touch()

        (config_dir / "config.yaml").touch()
        (var_dir / "deployments.db").touch()
        (quadlet_dir / "onetime-web@.container").touch()
        (quadlet_dir / "onetime-worker@.container").touch()
        (quadlet_dir / "onetime-scheduler@.container").touch()

        _mock_config(
            mocker, tmp_path, config_dir=config_dir, var_dir=var_dir, quadlet_dir=quadlet_dir
        )
        mocker.patch("rots.commands.init.DEFAULT_ENV_FILE", env_file)

        with caplog.at_level(logging.INFO):
            result = init(check=True)

        assert result == 0
        assert "[ok]" in caplog.text
        assert "All components present" in caplog.text

    def test_handles_permission_errors_gracefully(self, tmp_path, mocker, caplog):
        """Init should continue after permission errors and report failure."""
        from rots.commands.init import init

        env_file = tmp_path / "etc" / "default" / "onetimesecret"

        _mock_config(mocker, tmp_path)
        mocker.patch("rots.commands.init.DEFAULT_ENV_FILE", env_file)

        # Make mkdir always fail with PermissionError
        mocker.patch.object(Path, "mkdir", side_effect=PermissionError("denied"))

        with caplog.at_level(logging.WARNING):
            result = init(quiet=False)

        assert result == 1
        assert "[denied]" in caplog.text
        assert "Initialization incomplete" in caplog.text
        assert "sudo" in caplog.text

    def test_creates_directories_successfully(self, tmp_path, mocker, caplog):
        """Init should create all directories when permissions allow."""
        from rots.commands.init import init

        config_dir = tmp_path / "etc" / "onetimesecret"
        var_dir = tmp_path / "var" / "lib" / "onetimesecret"
        quadlet_dir = tmp_path / "etc" / "containers" / "systemd"
        env_file = tmp_path / "etc" / "default" / "onetimesecret"
        env_file.parent.mkdir(parents=True)
        env_file.touch()

        _mock_config(
            mocker, tmp_path, config_dir=config_dir, var_dir=var_dir, quadlet_dir=quadlet_dir
        )
        mocker.patch("rots.commands.init.DEFAULT_ENV_FILE", env_file)
        mocker.patch("os.chown")
        mocker.patch("rots.commands.init.db.init_db")

        with caplog.at_level(logging.INFO):
            result = init(quiet=False)

        assert result == 0
        assert config_dir.exists()
        assert var_dir.exists()
        assert quadlet_dir.exists()
        assert "Initialization complete" in caplog.text

    def test_copies_templates_from_source(self, tmp_path, mocker, caplog):
        """Init should copy all CONFIG_FILES when --source provided."""
        from rots.commands.init import init

        # Create source directory with all config file templates
        source_dir = tmp_path / "source"
        source_dir.mkdir()
        (source_dir / "config.yaml").write_text("site: example")
        (source_dir / "auth.yaml").write_text("auth: basic")
        (source_dir / "logging.yaml").write_text("level: info")

        config_dir = tmp_path / "etc" / "onetimesecret"
        var_dir = tmp_path / "var" / "lib" / "onetimesecret"
        quadlet_dir = tmp_path / "etc" / "containers" / "systemd"
        env_file = tmp_path / "etc" / "default" / "onetimesecret"
        env_file.parent.mkdir(parents=True)
        env_file.touch()

        _mock_config(
            mocker, tmp_path, config_dir=config_dir, var_dir=var_dir, quadlet_dir=quadlet_dir
        )
        mocker.patch("rots.commands.init.DEFAULT_ENV_FILE", env_file)
        mocker.patch("os.chown")
        mocker.patch("rots.commands.init.db.init_db")

        with caplog.at_level(logging.INFO):
            result = init(source_dir=source_dir, quiet=False)

        assert result == 0
        # All three CONFIG_FILES should be copied
        assert (config_dir / "config.yaml").exists()
        assert (config_dir / "config.yaml").read_text() == "site: example"
        assert (config_dir / "auth.yaml").exists()
        assert (config_dir / "auth.yaml").read_text() == "auth: basic"
        assert (config_dir / "logging.yaml").exists()
        assert (config_dir / "logging.yaml").read_text() == "level: info"
        assert "[copied]" in caplog.text

    def test_check_reports_optional_for_missing_config_files(self, tmp_path, mocker, caplog):
        """Check mode should report [optional] for missing config files."""
        from rots.commands.init import init

        # Create all directories and deploy db so only config files are missing
        config_dir = tmp_path / "etc" / "onetimesecret"
        var_dir = tmp_path / "var" / "lib" / "onetimesecret"
        quadlet_dir = tmp_path / "etc" / "containers" / "systemd"
        users_dir = quadlet_dir / "users"
        env_file = tmp_path / "etc" / "default" / "onetimesecret"

        config_dir.mkdir(parents=True)
        var_dir.mkdir(parents=True)
        quadlet_dir.mkdir(parents=True)
        users_dir.mkdir(parents=True)
        env_file.parent.mkdir(parents=True)
        env_file.touch()

        # Create quadlet templates and db so those pass
        (quadlet_dir / "onetime-web@.container").touch()
        (quadlet_dir / "onetime-worker@.container").touch()
        (quadlet_dir / "onetime-scheduler@.container").touch()
        (var_dir / "deployments.db").touch()

        _mock_config(
            mocker, tmp_path, config_dir=config_dir, var_dir=var_dir, quadlet_dir=quadlet_dir
        )
        mocker.patch("rots.commands.init.DEFAULT_ENV_FILE", env_file)

        with caplog.at_level(logging.INFO):
            result = init(check=True)

        # Should still pass because config files are optional
        assert result == 0
        # Each missing config file should show [optional]
        assert caplog.text.count("[optional]") >= 3
        assert "config.yaml" in caplog.text
        assert "auth.yaml" in caplog.text
        assert "logging.yaml" in caplog.text
        assert "All components present" in caplog.text

    def test_database_permission_error_handled(self, tmp_path, mocker, caplog):
        """Database creation permission errors should be handled gracefully."""
        from rots.commands.init import init

        config_dir = tmp_path / "etc" / "onetimesecret"
        var_dir = tmp_path / "var" / "lib" / "onetimesecret"
        quadlet_dir = tmp_path / "etc" / "containers" / "systemd"
        env_file = tmp_path / "etc" / "default" / "onetimesecret"

        # Pre-create directories so we get past dir creation
        config_dir.mkdir(parents=True)
        var_dir.mkdir(parents=True)
        quadlet_dir.mkdir(parents=True)
        env_file.parent.mkdir(parents=True)
        env_file.touch()

        _mock_config(
            mocker, tmp_path, config_dir=config_dir, var_dir=var_dir, quadlet_dir=quadlet_dir
        )
        mocker.patch("rots.commands.init.DEFAULT_ENV_FILE", env_file)
        mocker.patch("os.chown")
        mocker.patch(
            "rots.commands.init.db.init_db",
            side_effect=PermissionError("cannot create db"),
        )

        with caplog.at_level(logging.WARNING):
            result = init(quiet=False)

        assert result == 1
        assert "[denied]" in caplog.text
        assert "Initialization incomplete" in caplog.text


class TestInitEnvFileScaffold:
    """Test step 4: Infrastructure Configuration (env file scaffold)."""

    def test_check_reports_missing_env_file(self, tmp_path, mocker, caplog):
        """init(check=True) reports [missing] for DEFAULT_ENV_FILE when it does not exist."""
        from rots.commands.init import init

        quadlet_dir = tmp_path / "etc" / "containers" / "systemd"
        env_file = tmp_path / "etc" / "default" / "onetimesecret"
        # env_file intentionally NOT created

        # Create directories and quadlet files so only env_file is missing
        config_dir = tmp_path / "etc" / "onetimesecret"
        var_dir = tmp_path / "var" / "lib" / "onetimesecret"
        config_dir.mkdir(parents=True)
        var_dir.mkdir(parents=True)
        quadlet_dir.mkdir(parents=True)
        (quadlet_dir / "onetime-web@.container").touch()
        (quadlet_dir / "onetime-worker@.container").touch()
        (quadlet_dir / "onetime-scheduler@.container").touch()
        (var_dir / "deployments.db").touch()

        _mock_config(
            mocker, tmp_path, config_dir=config_dir, var_dir=var_dir, quadlet_dir=quadlet_dir
        )
        mocker.patch("rots.commands.init.DEFAULT_ENV_FILE", env_file)

        with caplog.at_level(logging.INFO):
            result = init(check=True)

        assert result == 1  # Missing env file makes all_ok = False
        assert "[missing]" in caplog.text
        assert str(env_file) in caplog.text

    def test_check_reports_ok_for_existing_env_file(self, tmp_path, mocker, caplog):
        """init(check=True) reports [ok] for DEFAULT_ENV_FILE when it exists."""
        from rots.commands.init import init

        quadlet_dir = tmp_path / "etc" / "containers" / "systemd"
        env_file = tmp_path / "etc" / "default" / "onetimesecret"
        config_dir = tmp_path / "etc" / "onetimesecret"
        var_dir = tmp_path / "var" / "lib" / "onetimesecret"

        config_dir.mkdir(parents=True)
        var_dir.mkdir(parents=True)
        quadlet_dir.mkdir(parents=True)
        (quadlet_dir / "users").mkdir()
        env_file.parent.mkdir(parents=True)
        env_file.touch()
        (quadlet_dir / "onetime-web@.container").touch()
        (quadlet_dir / "onetime-worker@.container").touch()
        (quadlet_dir / "onetime-scheduler@.container").touch()
        (var_dir / "deployments.db").touch()

        _mock_config(
            mocker, tmp_path, config_dir=config_dir, var_dir=var_dir, quadlet_dir=quadlet_dir
        )
        mocker.patch("rots.commands.init.DEFAULT_ENV_FILE", env_file)

        with caplog.at_level(logging.INFO):
            result = init(check=True)

        assert result == 0
        assert "[ok]" in caplog.text
        assert str(env_file) in caplog.text

    def test_creates_env_file_with_template_content(self, tmp_path, mocker, caplog):
        """init() creates DEFAULT_ENV_FILE with ENV_FILE_TEMPLATE content when it does not exist."""
        from rots.commands.init import init
        from rots.environment_file import ENV_FILE_TEMPLATE

        config_dir = tmp_path / "etc" / "onetimesecret"
        var_dir = tmp_path / "var" / "lib" / "onetimesecret"
        quadlet_dir = tmp_path / "etc" / "containers" / "systemd"
        env_file = tmp_path / "etc" / "default" / "onetimesecret"
        # env_file intentionally NOT created

        _mock_config(
            mocker, tmp_path, config_dir=config_dir, var_dir=var_dir, quadlet_dir=quadlet_dir
        )
        mocker.patch("rots.commands.init.DEFAULT_ENV_FILE", env_file)
        mocker.patch("os.chown")
        mocker.patch("rots.commands.init.db.init_db")

        with caplog.at_level(logging.INFO):
            result = init(quiet=False)

        assert result == 0
        assert env_file.exists()
        assert env_file.read_text() == ENV_FILE_TEMPLATE
        assert "[created]" in caplog.text
        assert str(env_file) in caplog.text

    def test_skips_env_file_creation_when_already_exists(self, tmp_path, mocker, caplog):
        """init() reports [ok] and skips creation when DEFAULT_ENV_FILE already exists."""
        from rots.commands.init import init

        config_dir = tmp_path / "etc" / "onetimesecret"
        var_dir = tmp_path / "var" / "lib" / "onetimesecret"
        quadlet_dir = tmp_path / "etc" / "containers" / "systemd"
        env_file = tmp_path / "etc" / "default" / "onetimesecret"
        env_file.parent.mkdir(parents=True)
        env_file.write_text("EXISTING_CONTENT=unchanged\n")

        _mock_config(
            mocker, tmp_path, config_dir=config_dir, var_dir=var_dir, quadlet_dir=quadlet_dir
        )
        mocker.patch("rots.commands.init.DEFAULT_ENV_FILE", env_file)
        mocker.patch("os.chown")
        mocker.patch("rots.commands.init.db.init_db")

        with caplog.at_level(logging.INFO):
            init(quiet=False)

        # Content must not be overwritten
        assert env_file.read_text() == "EXISTING_CONTENT=unchanged\n"
        assert "[ok]" in caplog.text
        assert str(env_file) in caplog.text

    def test_env_file_permission_error_sets_failure(self, tmp_path, mocker, caplog):
        """init() reports [denied] and sets all_ok=False on PermissionError for DEFAULT_ENV_FILE."""
        from rots.commands.init import init

        config_dir = tmp_path / "etc" / "onetimesecret"
        var_dir = tmp_path / "var" / "lib" / "onetimesecret"
        quadlet_dir = tmp_path / "etc" / "containers" / "systemd"
        env_file = tmp_path / "etc" / "default" / "onetimesecret"
        # env_file NOT created - so write will be attempted

        _mock_config(
            mocker, tmp_path, config_dir=config_dir, var_dir=var_dir, quadlet_dir=quadlet_dir
        )
        mocker.patch("rots.commands.init.DEFAULT_ENV_FILE", env_file)
        mocker.patch("os.chown")
        mocker.patch("rots.commands.init.db.init_db")

        # Make mkdir for parent of env_file raise PermissionError
        original_mkdir = Path.mkdir

        def selective_mkdir(self, *args, **kwargs):
            if self == env_file.parent:
                raise PermissionError("denied")
            return original_mkdir(self, *args, **kwargs)

        mocker.patch.object(Path, "mkdir", selective_mkdir)

        with caplog.at_level(logging.WARNING):
            result = init(quiet=False)

        assert result == 1
        assert "[denied]" in caplog.text
        assert "Initialization incomplete" in caplog.text


class TestInitHelp:
    """Test init help output."""

    def test_init_help(self, capsys):
        """init --help should work."""
        from rots.cli import app

        with pytest.raises(SystemExit) as exc_info:
            app(["init", "--help"])
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "initialize" in captured.out.lower() or "init" in captured.out.lower()

    def test_init_check_help(self, capsys):
        """init --check flag should be documented."""
        from rots.cli import app

        with pytest.raises(SystemExit) as exc_info:
            app(["init", "--help"])
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "check" in captured.out.lower()
