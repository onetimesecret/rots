# tests/commands/test_init.py
"""Tests for init command with permission handling."""

from pathlib import Path

import pytest


class TestInitCommandImports:
    """Verify init command module imports correctly."""

    def test_init_app_exists(self):
        """Init app should be importable."""
        from ots_containers.commands import init

        assert init.app is not None

    def test_init_function_exists(self):
        """init command should be defined."""
        from ots_containers.commands import init

        assert hasattr(init, "init")
        assert callable(init.init)


class TestCreateDirectory:
    """Test _create_directory helper function."""

    def test_creates_directory_when_not_exists(self, tmp_path, mocker, capsys):
        """Should create directory and return True."""
        from ots_containers.commands.init import _create_directory

        # Mock os.chown to avoid permission issues in tests
        mocker.patch("os.chown")

        new_dir = tmp_path / "new_dir"
        result = _create_directory(new_dir, mode=0o755, quiet=False)

        assert result is True
        assert new_dir.exists()
        captured = capsys.readouterr()
        assert "[created]" in captured.out

    def test_returns_false_when_exists(self, tmp_path, capsys):
        """Should return False and not recreate existing directory."""
        from ots_containers.commands.init import _create_directory

        existing_dir = tmp_path / "existing"
        existing_dir.mkdir()

        result = _create_directory(existing_dir, mode=0o755, quiet=False)

        assert result is False
        captured = capsys.readouterr()
        assert "[ok]" in captured.out

    def test_returns_none_on_permission_error(self, tmp_path, mocker, capsys):
        """Should return None and print denied message on PermissionError."""
        from ots_containers.commands.init import _create_directory

        # Mock mkdir to raise PermissionError
        mocker.patch.object(Path, "mkdir", side_effect=PermissionError("denied"))

        new_dir = tmp_path / "denied_dir"
        result = _create_directory(new_dir, mode=0o755, quiet=False)

        assert result is None
        captured = capsys.readouterr()
        assert "[denied]" in captured.out
        assert "permission denied" in captured.out

    def test_quiet_mode_suppresses_exists_output(self, tmp_path, capsys):
        """Should suppress output when quiet=True and dir exists."""
        from ots_containers.commands.init import _create_directory

        existing_dir = tmp_path / "existing"
        existing_dir.mkdir()

        _create_directory(existing_dir, mode=0o755, quiet=True)

        captured = capsys.readouterr()
        assert captured.out == ""

    def test_quiet_mode_suppresses_created_output(self, tmp_path, mocker, capsys):
        """Should suppress output when quiet=True and dir created."""
        from ots_containers.commands.init import _create_directory

        mocker.patch("os.chown")

        new_dir = tmp_path / "new_dir"
        _create_directory(new_dir, mode=0o755, quiet=True)

        captured = capsys.readouterr()
        assert captured.out == ""

    def test_permission_error_always_prints(self, tmp_path, mocker, capsys):
        """Permission errors should always print, even in quiet mode."""
        from ots_containers.commands.init import _create_directory

        mocker.patch.object(Path, "mkdir", side_effect=PermissionError("denied"))

        new_dir = tmp_path / "denied_dir"
        _create_directory(new_dir, mode=0o755, quiet=True)

        captured = capsys.readouterr()
        assert "[denied]" in captured.out


class TestCopyTemplate:
    """Test _copy_template helper function."""

    def test_copies_file_when_dest_not_exists(self, tmp_path, mocker, capsys):
        """Should copy file and return True."""
        from ots_containers.commands.init import _copy_template

        mocker.patch("os.chown")

        src = tmp_path / "source.txt"
        src.write_text("content")
        dest = tmp_path / "dest.txt"

        result = _copy_template(src, dest, quiet=False)

        assert result is True
        assert dest.exists()
        assert dest.read_text() == "content"
        captured = capsys.readouterr()
        assert "[copied]" in captured.out

    def test_returns_false_when_dest_exists(self, tmp_path, capsys):
        """Should return False when destination exists."""
        from ots_containers.commands.init import _copy_template

        src = tmp_path / "source.txt"
        src.write_text("new content")
        dest = tmp_path / "dest.txt"
        dest.write_text("old content")

        result = _copy_template(src, dest, quiet=False)

        assert result is False
        assert dest.read_text() == "old content"  # unchanged
        captured = capsys.readouterr()
        assert "[ok]" in captured.out

    def test_returns_false_when_source_missing(self, tmp_path, capsys):
        """Should return False when source doesn't exist."""
        from ots_containers.commands.init import _copy_template

        src = tmp_path / "nonexistent.txt"
        dest = tmp_path / "dest.txt"

        result = _copy_template(src, dest, quiet=False)

        assert result is False
        assert not dest.exists()
        captured = capsys.readouterr()
        assert "[skip]" in captured.out
        assert "not found" in captured.out

    def test_returns_none_on_permission_error(self, tmp_path, mocker, capsys):
        """Should return None on PermissionError."""
        from ots_containers.commands.init import _copy_template

        mocker.patch("shutil.copy2", side_effect=PermissionError("denied"))

        src = tmp_path / "source.txt"
        src.write_text("content")
        dest = tmp_path / "dest.txt"

        result = _copy_template(src, dest, quiet=False)

        assert result is None
        captured = capsys.readouterr()
        assert "[denied]" in captured.out
        assert "permission denied" in captured.out


class TestInitCommand:
    """Test init command behavior."""

    def test_check_mode_reports_missing_directories(self, tmp_path, mocker, capsys):
        """Check mode should report missing directories without creating."""
        from ots_containers.commands.init import init

        quadlet_dir = tmp_path / "etc" / "containers" / "systemd"

        # Create a mock config pointing to tmp_path subdirs
        mock_config = mocker.MagicMock()
        mock_config.config_dir = tmp_path / "etc" / "onetimesecret"
        mock_config.var_dir = tmp_path / "var" / "lib" / "onetimesecret"
        mock_config.web_template_path = quadlet_dir / "onetime-web@.container"
        mock_config.worker_template_path = quadlet_dir / "onetime-worker@.container"
        mock_config.scheduler_template_path = quadlet_dir / "onetime-scheduler@.container"
        mock_config.config_yaml = mock_config.config_dir / "config.yaml"
        mock_config.db_path = mock_config.var_dir / "deployments.db"

        mocker.patch("ots_containers.commands.init.Config", return_value=mock_config)

        result = init(check=True)

        assert result == 1  # Missing components (quadlet, var_dir)
        captured = capsys.readouterr()
        assert "[missing]" in captured.out
        # Config files are now optional, so they show [optional]
        assert "[optional]" in captured.out
        assert "Missing components" in captured.out
        # Directories should NOT be created
        assert not mock_config.config_dir.exists()

    def test_check_mode_reports_ok_when_all_present(self, tmp_path, mocker, capsys):
        """Check mode should report OK when all components exist."""
        from ots_containers.commands.init import init

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

        mock_config = mocker.MagicMock()
        mock_config.config_dir = config_dir
        mock_config.var_dir = var_dir
        mock_config.web_template_path = quadlet_dir / "onetime-web@.container"
        mock_config.worker_template_path = quadlet_dir / "onetime-worker@.container"
        mock_config.scheduler_template_path = quadlet_dir / "onetime-scheduler@.container"
        mock_config.config_yaml = config_dir / "config.yaml"
        mock_config.db_path = var_dir / "deployments.db"

        mocker.patch("ots_containers.commands.init.Config", return_value=mock_config)
        mocker.patch("ots_containers.commands.init.DEFAULT_ENV_FILE", env_file)

        result = init(check=True)

        assert result == 0
        captured = capsys.readouterr()
        assert "[ok]" in captured.out
        assert "All components present" in captured.out

    def test_handles_permission_errors_gracefully(self, tmp_path, mocker, capsys):
        """Init should continue after permission errors and report failure."""
        from ots_containers.commands.init import init

        quadlet_dir = tmp_path / "etc" / "containers" / "systemd"

        mock_config = mocker.MagicMock()
        mock_config.config_dir = tmp_path / "etc" / "onetimesecret"
        mock_config.var_dir = tmp_path / "var" / "lib" / "onetimesecret"
        mock_config.web_template_path = quadlet_dir / "onetime-web@.container"
        mock_config.worker_template_path = quadlet_dir / "onetime-worker@.container"
        mock_config.scheduler_template_path = quadlet_dir / "onetime-scheduler@.container"
        mock_config.config_yaml = mock_config.config_dir / "config.yaml"
        mock_config.db_path = mock_config.var_dir / "deployments.db"

        mocker.patch("ots_containers.commands.init.Config", return_value=mock_config)

        # Make mkdir always fail with PermissionError
        mocker.patch.object(Path, "mkdir", side_effect=PermissionError("denied"))

        result = init(quiet=False)

        assert result == 1
        captured = capsys.readouterr()
        assert "[denied]" in captured.out
        assert "Initialization incomplete" in captured.out
        assert "sudo" in captured.out

    def test_creates_directories_successfully(self, tmp_path, mocker, capsys):
        """Init should create all directories when permissions allow."""
        from ots_containers.commands.init import init

        config_dir = tmp_path / "etc" / "onetimesecret"
        var_dir = tmp_path / "var" / "lib" / "onetimesecret"
        quadlet_dir = tmp_path / "etc" / "containers" / "systemd"
        env_file = tmp_path / "etc" / "default" / "onetimesecret"
        env_file.parent.mkdir(parents=True)
        env_file.touch()

        mock_config = mocker.MagicMock()
        mock_config.config_dir = config_dir
        mock_config.var_dir = var_dir
        mock_config.web_template_path = quadlet_dir / "onetime-web@.container"
        mock_config.worker_template_path = quadlet_dir / "onetime-worker@.container"
        mock_config.scheduler_template_path = quadlet_dir / "onetime-scheduler@.container"
        mock_config.config_yaml = config_dir / "config.yaml"
        mock_config.db_path = var_dir / "deployments.db"

        mocker.patch("ots_containers.commands.init.Config", return_value=mock_config)
        mocker.patch("ots_containers.commands.init.DEFAULT_ENV_FILE", env_file)
        mocker.patch("os.chown")  # Avoid chown permission issues
        mocker.patch("ots_containers.commands.init.db.init_db")

        result = init(quiet=False)

        assert result == 0
        assert config_dir.exists()
        assert var_dir.exists()
        assert quadlet_dir.exists()
        captured = capsys.readouterr()
        assert "Initialization complete" in captured.out

    def test_copies_templates_from_source(self, tmp_path, mocker, capsys):
        """Init should copy all CONFIG_FILES when --source provided."""
        from ots_containers.commands.init import init

        # Create source directory with all config file templates
        source_dir = tmp_path / "source"
        source_dir.mkdir()
        (source_dir / "config.yaml").write_text("site: example")
        (source_dir / "auth.yaml").write_text("auth: basic")
        (source_dir / "logging.yaml").write_text("level: info")

        # Create target structure
        config_dir = tmp_path / "etc" / "onetimesecret"
        var_dir = tmp_path / "var" / "lib" / "onetimesecret"
        quadlet_dir = tmp_path / "etc" / "containers" / "systemd"
        env_file = tmp_path / "etc" / "default" / "onetimesecret"
        env_file.parent.mkdir(parents=True)
        env_file.touch()

        mock_config = mocker.MagicMock()
        mock_config.config_dir = config_dir
        mock_config.var_dir = var_dir
        mock_config.web_template_path = quadlet_dir / "onetime-web@.container"
        mock_config.worker_template_path = quadlet_dir / "onetime-worker@.container"
        mock_config.scheduler_template_path = quadlet_dir / "onetime-scheduler@.container"
        mock_config.config_yaml = config_dir / "config.yaml"
        mock_config.db_path = var_dir / "deployments.db"

        mocker.patch("ots_containers.commands.init.Config", return_value=mock_config)
        mocker.patch("ots_containers.commands.init.DEFAULT_ENV_FILE", env_file)
        mocker.patch("os.chown")
        mocker.patch("ots_containers.commands.init.db.init_db")

        result = init(source_dir=source_dir, quiet=False)

        assert result == 0
        # All three CONFIG_FILES should be copied
        assert (config_dir / "config.yaml").exists()
        assert (config_dir / "config.yaml").read_text() == "site: example"
        assert (config_dir / "auth.yaml").exists()
        assert (config_dir / "auth.yaml").read_text() == "auth: basic"
        assert (config_dir / "logging.yaml").exists()
        assert (config_dir / "logging.yaml").read_text() == "level: info"
        captured = capsys.readouterr()
        assert "[copied]" in captured.out

    def test_check_reports_optional_for_missing_config_files(self, tmp_path, mocker, capsys):
        """Check mode should report [optional] for missing config files."""
        from ots_containers.commands.init import init

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

        mock_config = mocker.MagicMock()
        mock_config.config_dir = config_dir
        mock_config.var_dir = var_dir
        mock_config.web_template_path = quadlet_dir / "onetime-web@.container"
        mock_config.worker_template_path = quadlet_dir / "onetime-worker@.container"
        mock_config.scheduler_template_path = quadlet_dir / "onetime-scheduler@.container"
        mock_config.config_yaml = config_dir / "config.yaml"
        mock_config.db_path = var_dir / "deployments.db"

        mocker.patch("ots_containers.commands.init.Config", return_value=mock_config)
        mocker.patch("ots_containers.commands.init.DEFAULT_ENV_FILE", env_file)

        result = init(check=True)

        # Should still pass because config files are optional
        assert result == 0
        captured = capsys.readouterr()
        # Each missing config file should show [optional]
        assert captured.out.count("[optional]") >= 3
        assert "config.yaml" in captured.out
        assert "auth.yaml" in captured.out
        assert "logging.yaml" in captured.out
        assert "All components present" in captured.out

    def test_database_permission_error_handled(self, tmp_path, mocker, capsys):
        """Database creation permission errors should be handled gracefully."""
        from ots_containers.commands.init import init

        config_dir = tmp_path / "etc" / "onetimesecret"
        var_dir = tmp_path / "var" / "lib" / "onetimesecret"
        quadlet_dir = tmp_path / "etc" / "containers" / "systemd"

        # Pre-create directories so we get past dir creation
        config_dir.mkdir(parents=True)
        var_dir.mkdir(parents=True)
        quadlet_dir.mkdir(parents=True)

        mock_config = mocker.MagicMock()
        mock_config.config_dir = config_dir
        mock_config.var_dir = var_dir
        mock_config.web_template_path = quadlet_dir / "onetime-web@.container"
        mock_config.worker_template_path = quadlet_dir / "onetime-worker@.container"
        mock_config.scheduler_template_path = quadlet_dir / "onetime-scheduler@.container"
        mock_config.config_yaml = config_dir / "config.yaml"
        mock_config.db_path = var_dir / "deployments.db"
        # db_path doesn't exist, so init_db will be called

        mocker.patch("ots_containers.commands.init.Config", return_value=mock_config)
        mocker.patch("os.chown")
        mocker.patch(
            "ots_containers.commands.init.db.init_db",
            side_effect=PermissionError("cannot create db"),
        )

        result = init(quiet=False)

        assert result == 1
        captured = capsys.readouterr()
        assert "[denied]" in captured.out
        assert "Initialization incomplete" in captured.out


class TestInitHelp:
    """Test init help output."""

    def test_init_help(self, capsys):
        """init --help should work."""
        from ots_containers.cli import app

        with pytest.raises(SystemExit) as exc_info:
            app(["init", "--help"])
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "initialize" in captured.out.lower() or "init" in captured.out.lower()

    def test_init_check_help(self, capsys):
        """init --check flag should be documented."""
        from ots_containers.cli import app

        with pytest.raises(SystemExit) as exc_info:
            app(["init", "--help"])
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "check" in captured.out.lower()
