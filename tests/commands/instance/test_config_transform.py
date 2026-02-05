# tests/commands/instance/test_config_transform.py
"""Tests for the config-transform command.

These tests verify the config-transform command handles config file
transformation with proper backup and apply workflow.
"""

import subprocess

import pytest

from ots_containers.commands import instance


class TestConfigTransformCommand:
    """Test the config-transform command."""

    def test_config_transform_function_exists(self):
        """config_transform command should be defined."""
        assert hasattr(instance, "config_transform")
        assert callable(instance.config_transform)

    def test_config_transform_rejects_path_traversal(self, mocker, tmp_path):
        """config_transform should reject path traversal attempts."""
        # Mock Config
        mock_config = mocker.MagicMock()
        mock_config.config_dir = tmp_path / "etc"
        mock_config.config_dir.mkdir()
        mocker.patch("ots_containers.commands.instance.app.Config", return_value=mock_config)

        with pytest.raises(SystemExit) as exc_info:
            instance.config_transform(command="echo test", file="../etc/passwd")
        assert "path traversal" in str(exc_info.value).lower()

    def test_config_transform_rejects_absolute_path(self, mocker, tmp_path):
        """config_transform should reject absolute file paths."""
        # Mock Config
        mock_config = mocker.MagicMock()
        mock_config.config_dir = tmp_path / "etc"
        mock_config.config_dir.mkdir()
        mocker.patch("ots_containers.commands.instance.app.Config", return_value=mock_config)

        with pytest.raises(SystemExit) as exc_info:
            instance.config_transform(command="echo test", file="/etc/passwd")
        assert "path traversal" in str(exc_info.value).lower()

    def test_config_transform_checks_file_exists(self, mocker, tmp_path):
        """config_transform should verify config file exists."""
        # Mock Config
        mock_config = mocker.MagicMock()
        mock_config.config_dir = tmp_path / "etc"
        mock_config.config_dir.mkdir()
        mocker.patch("ots_containers.commands.instance.app.Config", return_value=mock_config)

        with pytest.raises(SystemExit) as exc_info:
            instance.config_transform(command="echo test", file="nonexistent.yaml")
        assert "not found" in str(exc_info.value).lower()

    def test_config_transform_creates_volume(self, mocker, tmp_path):
        """config_transform should create temporary volume."""
        # Mock Config
        mock_config = mocker.MagicMock()
        mock_config.tag = "current"
        mock_config.config_dir = tmp_path / "etc"
        mock_config.config_dir.mkdir()
        (mock_config.config_dir / "config.yaml").write_text("key: value\n")
        mocker.patch("ots_containers.commands.instance.app.Config", return_value=mock_config)

        # Mock env file not existing
        mocker.patch(
            "ots_containers.commands.instance.app.quadlet.DEFAULT_ENV_FILE",
            tmp_path / "nonexistent",
        )

        # Mock subprocess.run - capture all calls
        mock_run = mocker.patch("subprocess.run")
        mock_run.return_value = subprocess.CompletedProcess(
            [], 0, stdout="key: new_value\n", stderr=""
        )

        # Call config_transform - will fail when trying to read transformed file
        # but we can verify volume was created
        try:
            instance.config_transform(command="echo test", quiet=True)
        except SystemExit:
            pass

        # Verify volume create was called
        calls = mock_run.call_args_list
        volume_create_call = [c for c in calls if "volume" in str(c) and "create" in str(c)]
        assert len(volume_create_call) >= 1

    def test_config_transform_cleans_up_volume_on_success(self, mocker, tmp_path):
        """config_transform should cleanup volume after success."""
        # Mock Config
        mock_config = mocker.MagicMock()
        mock_config.tag = "current"
        mock_config.config_dir = tmp_path / "etc"
        mock_config.config_dir.mkdir()
        (mock_config.config_dir / "config.yaml").write_text("key: value\n")
        mocker.patch("ots_containers.commands.instance.app.Config", return_value=mock_config)

        # Mock env file not existing
        mocker.patch(
            "ots_containers.commands.instance.app.quadlet.DEFAULT_ENV_FILE",
            tmp_path / "nonexistent",
        )

        def mock_run_side_effect(*args, **kwargs):
            cmd = args[0] if args else kwargs.get("args", [])
            if "volume" in cmd and "rm" in cmd:
                return subprocess.CompletedProcess(cmd, 0)
            if "volume" in cmd and "create" in cmd:
                return subprocess.CompletedProcess(cmd, 0)
            if "/bin/cp" in cmd:
                return subprocess.CompletedProcess(cmd, 0)
            if "/bin/cat" in cmd:
                return subprocess.CompletedProcess(cmd, 0, stdout="key: new_value\n", stderr="")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        mock_run = mocker.patch("subprocess.run", side_effect=mock_run_side_effect)

        # Call config_transform
        instance.config_transform(command="echo test", quiet=True)

        # Verify volume rm was called
        calls = [str(c) for c in mock_run.call_args_list]
        volume_rm_calls = [c for c in calls if "volume" in c and "rm" in c]
        assert len(volume_rm_calls) >= 1

    def test_config_transform_cleans_up_volume_on_error(self, mocker, tmp_path):
        """config_transform should cleanup volume even on error."""
        # Mock Config
        mock_config = mocker.MagicMock()
        mock_config.tag = "current"
        mock_config.config_dir = tmp_path / "etc"
        mock_config.config_dir.mkdir()
        (mock_config.config_dir / "config.yaml").write_text("key: value\n")
        mocker.patch("ots_containers.commands.instance.app.Config", return_value=mock_config)

        # Mock env file not existing
        mocker.patch(
            "ots_containers.commands.instance.app.quadlet.DEFAULT_ENV_FILE",
            tmp_path / "nonexistent",
        )

        call_count = [0]

        def mock_run_side_effect(*args, **kwargs):
            call_count[0] += 1
            cmd = args[0] if args else kwargs.get("args", [])
            # Volume operations succeed
            if "volume" in cmd:
                return subprocess.CompletedProcess(cmd, 0)
            # Copy succeeds
            if "/bin/cp" in cmd:
                return subprocess.CompletedProcess(cmd, 0)
            # Migration command fails
            if "/bin/bash" in cmd and "-c" in cmd:
                return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="error")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        mock_run = mocker.patch("subprocess.run", side_effect=mock_run_side_effect)

        # Call config_transform - should fail but cleanup
        with pytest.raises(SystemExit):
            instance.config_transform(command="failing-command", quiet=True)

        # Verify volume rm was still called
        calls = [str(c) for c in mock_run.call_args_list]
        volume_rm_calls = [c for c in calls if "volume" in c and "rm" in c]
        assert len(volume_rm_calls) >= 1

    def test_config_transform_dry_run_shows_diff(self, mocker, tmp_path, capsys):
        """config_transform dry-run should show diff without changes."""
        # Mock Config
        mock_config = mocker.MagicMock()
        mock_config.tag = "current"
        mock_config.config_dir = tmp_path / "etc"
        mock_config.config_dir.mkdir()
        (mock_config.config_dir / "config.yaml").write_text("key: old_value\n")
        mocker.patch("ots_containers.commands.instance.app.Config", return_value=mock_config)

        # Mock env file not existing
        mocker.patch(
            "ots_containers.commands.instance.app.quadlet.DEFAULT_ENV_FILE",
            tmp_path / "nonexistent",
        )

        def mock_run_side_effect(*args, **kwargs):
            cmd = args[0] if args else kwargs.get("args", [])
            if "volume" in cmd:
                return subprocess.CompletedProcess(cmd, 0)
            if "/bin/cp" in cmd:
                return subprocess.CompletedProcess(cmd, 0)
            if "/bin/cat" in cmd:
                return subprocess.CompletedProcess(cmd, 0, stdout="key: new_value\n", stderr="")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        mocker.patch("subprocess.run", side_effect=mock_run_side_effect)

        # Call config_transform (dry-run is default)
        instance.config_transform(command="transform", quiet=True)

        # Verify diff was shown
        captured = capsys.readouterr()
        assert "old_value" in captured.out
        assert "new_value" in captured.out
        # Should indicate dry-run
        assert "dry run" in captured.out.lower() or "no changes made" in captured.out.lower()

        # File should not be modified
        assert (mock_config.config_dir / "config.yaml").read_text() == "key: old_value\n"

    def test_config_transform_apply_creates_backup(self, mocker, tmp_path, capsys):
        """config_transform --apply should create backup file."""
        # Mock Config
        mock_config = mocker.MagicMock()
        mock_config.tag = "current"
        mock_config.config_dir = tmp_path / "etc"
        mock_config.config_dir.mkdir()
        config_file = mock_config.config_dir / "config.yaml"
        config_file.write_text("key: old_value\n")
        mocker.patch("ots_containers.commands.instance.app.Config", return_value=mock_config)

        # Mock env file not existing
        mocker.patch(
            "ots_containers.commands.instance.app.quadlet.DEFAULT_ENV_FILE",
            tmp_path / "nonexistent",
        )

        def mock_run_side_effect(*args, **kwargs):
            cmd = args[0] if args else kwargs.get("args", [])
            if "volume" in cmd:
                return subprocess.CompletedProcess(cmd, 0)
            if "/bin/cp" in cmd:
                return subprocess.CompletedProcess(cmd, 0)
            if "/bin/cat" in cmd:
                return subprocess.CompletedProcess(cmd, 0, stdout="key: new_value\n", stderr="")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        mocker.patch("subprocess.run", side_effect=mock_run_side_effect)

        # Call config_transform with --apply
        instance.config_transform(command="transform", apply=True, quiet=True)

        # Verify backup was created
        backups = list(mock_config.config_dir.glob("config.yaml.bak.*"))
        assert len(backups) >= 1
        assert backups[0].read_text() == "key: old_value\n"

    def test_config_transform_apply_updates_file(self, mocker, tmp_path, capsys):
        """config_transform --apply should update the config file."""
        # Mock Config
        mock_config = mocker.MagicMock()
        mock_config.tag = "current"
        mock_config.config_dir = tmp_path / "etc"
        mock_config.config_dir.mkdir()
        config_file = mock_config.config_dir / "config.yaml"
        config_file.write_text("key: old_value\n")
        mocker.patch("ots_containers.commands.instance.app.Config", return_value=mock_config)

        # Mock env file not existing
        mocker.patch(
            "ots_containers.commands.instance.app.quadlet.DEFAULT_ENV_FILE",
            tmp_path / "nonexistent",
        )

        def mock_run_side_effect(*args, **kwargs):
            cmd = args[0] if args else kwargs.get("args", [])
            if "volume" in cmd:
                return subprocess.CompletedProcess(cmd, 0)
            if "/bin/cp" in cmd:
                return subprocess.CompletedProcess(cmd, 0)
            if "/bin/cat" in cmd:
                return subprocess.CompletedProcess(cmd, 0, stdout="key: new_value\n", stderr="")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        mocker.patch("subprocess.run", side_effect=mock_run_side_effect)

        # Call config_transform with --apply
        instance.config_transform(command="transform", apply=True, quiet=True)

        # Verify file was updated
        assert config_file.read_text() == "key: new_value\n"

    def test_config_transform_handles_no_changes(self, mocker, tmp_path, capsys):
        """config_transform should report when no changes detected."""
        # Mock Config
        mock_config = mocker.MagicMock()
        mock_config.tag = "current"
        mock_config.config_dir = tmp_path / "etc"
        mock_config.config_dir.mkdir()
        config_file = mock_config.config_dir / "config.yaml"
        config_file.write_text("key: value\n")
        mocker.patch("ots_containers.commands.instance.app.Config", return_value=mock_config)

        # Mock env file not existing
        mocker.patch(
            "ots_containers.commands.instance.app.quadlet.DEFAULT_ENV_FILE",
            tmp_path / "nonexistent",
        )

        def mock_run_side_effect(*args, **kwargs):
            cmd = args[0] if args else kwargs.get("args", [])
            if "volume" in cmd:
                return subprocess.CompletedProcess(cmd, 0)
            if "/bin/cp" in cmd:
                return subprocess.CompletedProcess(cmd, 0)
            if "/bin/cat" in cmd:
                # Return same content - no changes
                return subprocess.CompletedProcess(cmd, 0, stdout="key: value\n", stderr="")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        mocker.patch("subprocess.run", side_effect=mock_run_side_effect)

        # Call config_transform
        instance.config_transform(command="transform", quiet=True)

        # Verify "no changes" message
        captured = capsys.readouterr()
        assert "no changes" in captured.out.lower()

    def test_config_transform_fails_when_command_fails(self, mocker, tmp_path, capsys):
        """config_transform should fail when migration command fails."""
        # Mock Config
        mock_config = mocker.MagicMock()
        mock_config.tag = "current"
        mock_config.config_dir = tmp_path / "etc"
        mock_config.config_dir.mkdir()
        (mock_config.config_dir / "config.yaml").write_text("key: value\n")
        mocker.patch("ots_containers.commands.instance.app.Config", return_value=mock_config)

        # Mock env file not existing
        mocker.patch(
            "ots_containers.commands.instance.app.quadlet.DEFAULT_ENV_FILE",
            tmp_path / "nonexistent",
        )

        def mock_run_side_effect(*args, **kwargs):
            cmd = args[0] if args else kwargs.get("args", [])
            if "volume" in cmd:
                return subprocess.CompletedProcess(cmd, 0)
            if "/bin/cp" in cmd:
                return subprocess.CompletedProcess(cmd, 0)
            # Migration fails
            if "/bin/bash" in cmd and "-c" in cmd:
                return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="Migration error")
            return subprocess.CompletedProcess(cmd, 0)

        mocker.patch("subprocess.run", side_effect=mock_run_side_effect)

        with pytest.raises(SystemExit) as exc_info:
            instance.config_transform(command="failing-migration", quiet=True)
        assert exc_info.value.code == 1

        captured = capsys.readouterr()
        assert "failed" in captured.out.lower()

    def test_config_transform_fails_when_no_output_file(self, mocker, tmp_path, capsys):
        """config_transform should fail when no .new file is produced."""
        # Mock Config
        mock_config = mocker.MagicMock()
        mock_config.tag = "current"
        mock_config.config_dir = tmp_path / "etc"
        mock_config.config_dir.mkdir()
        (mock_config.config_dir / "config.yaml").write_text("key: value\n")
        mocker.patch("ots_containers.commands.instance.app.Config", return_value=mock_config)

        # Mock env file not existing
        mocker.patch(
            "ots_containers.commands.instance.app.quadlet.DEFAULT_ENV_FILE",
            tmp_path / "nonexistent",
        )

        def mock_run_side_effect(*args, **kwargs):
            cmd = args[0] if args else kwargs.get("args", [])
            if "volume" in cmd:
                return subprocess.CompletedProcess(cmd, 0)
            if "/bin/cp" in cmd:
                return subprocess.CompletedProcess(cmd, 0)
            if "/bin/bash" in cmd and "-c" in cmd:
                return subprocess.CompletedProcess(cmd, 0)
            # cat fails - file doesn't exist
            if "/bin/cat" in cmd:
                return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="")
            return subprocess.CompletedProcess(cmd, 0)

        mocker.patch("subprocess.run", side_effect=mock_run_side_effect)

        with pytest.raises(SystemExit) as exc_info:
            instance.config_transform(command="no-output", quiet=True)
        assert exc_info.value.code == 1

        captured = capsys.readouterr()
        assert "no transformed file" in captured.out.lower()

    def test_config_transform_uses_custom_file(self, mocker, tmp_path):
        """config_transform -f should use specified file."""
        # Mock Config
        mock_config = mocker.MagicMock()
        mock_config.tag = "current"
        mock_config.config_dir = tmp_path / "etc"
        mock_config.config_dir.mkdir()
        (mock_config.config_dir / "auth.yaml").write_text("auth: config\n")
        mocker.patch("ots_containers.commands.instance.app.Config", return_value=mock_config)

        # Mock env file not existing
        mocker.patch(
            "ots_containers.commands.instance.app.quadlet.DEFAULT_ENV_FILE",
            tmp_path / "nonexistent",
        )

        def mock_run_side_effect(*args, **kwargs):
            cmd = args[0] if args else kwargs.get("args", [])
            if "volume" in cmd:
                return subprocess.CompletedProcess(cmd, 0)
            if "/bin/cp" in cmd:
                # Verify auth.yaml is being copied
                cmd_str = " ".join(cmd)
                assert "auth.yaml" in cmd_str
                return subprocess.CompletedProcess(cmd, 0)
            if "/bin/cat" in cmd:
                # Verify auth.yaml.new is being read
                cmd_str = " ".join(cmd)
                assert "auth.yaml.new" in cmd_str
                return subprocess.CompletedProcess(cmd, 0, stdout="auth: new_config\n", stderr="")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        mocker.patch("subprocess.run", side_effect=mock_run_side_effect)

        # Call with custom file
        instance.config_transform(command="transform", file="auth.yaml", quiet=True)

    def test_config_transform_includes_secrets(self, mocker, tmp_path):
        """config_transform should include secrets from env file."""
        from ots_containers.environment_file import SecretSpec

        # Mock Config
        mock_config = mocker.MagicMock()
        mock_config.tag = "current"
        mock_config.config_dir = tmp_path / "etc"
        mock_config.config_dir.mkdir()
        (mock_config.config_dir / "config.yaml").write_text("key: value\n")
        mocker.patch("ots_containers.commands.instance.app.Config", return_value=mock_config)

        # Create env file with secrets
        env_file = tmp_path / "onetimesecret"
        env_file.write_text("SECRET_VARIABLE_NAMES=HMAC_SECRET\n")
        mocker.patch(
            "ots_containers.commands.instance.app.quadlet.DEFAULT_ENV_FILE",
            env_file,
        )

        # Mock get_secrets_from_env_file
        mock_secrets = [
            SecretSpec(env_var_name="HMAC_SECRET", secret_name="ots_hmac_secret"),
        ]
        mocker.patch(
            "ots_containers.commands.instance._helpers.get_secrets_from_env_file",
            return_value=mock_secrets,
        )

        migration_cmd_args = []

        def mock_run_side_effect(*args, **kwargs):
            cmd = args[0] if args else kwargs.get("args", [])
            if "volume" in cmd:
                return subprocess.CompletedProcess(cmd, 0)
            if "/bin/cp" in cmd:
                return subprocess.CompletedProcess(cmd, 0)
            if "/bin/bash" in cmd and "-c" in cmd:
                migration_cmd_args.extend(cmd)
                return subprocess.CompletedProcess(cmd, 0)
            if "/bin/cat" in cmd:
                return subprocess.CompletedProcess(cmd, 0, stdout="key: value\n", stderr="")
            return subprocess.CompletedProcess(cmd, 0)

        mocker.patch("subprocess.run", side_effect=mock_run_side_effect)

        instance.config_transform(command="transform", quiet=True)

        # Verify secrets were included in migration command
        cmd_str = " ".join(migration_cmd_args)
        assert "--secret" in cmd_str
        assert "ots_hmac_secret" in cmd_str

    def test_config_transform_numbered_backup(self, mocker, tmp_path):
        """config_transform should create numbered backups if needed."""
        # Mock Config
        mock_config = mocker.MagicMock()
        mock_config.tag = "current"
        mock_config.config_dir = tmp_path / "etc"
        mock_config.config_dir.mkdir()
        config_file = mock_config.config_dir / "config.yaml"
        config_file.write_text("key: old_value\n")

        mocker.patch("ots_containers.commands.instance.app.Config", return_value=mock_config)

        # Mock env file not existing
        mocker.patch(
            "ots_containers.commands.instance.app.quadlet.DEFAULT_ENV_FILE",
            tmp_path / "nonexistent",
        )

        # Mock time.strftime to return consistent timestamp
        mocker.patch("time.strftime", return_value="20250127-120000")

        # Create existing backup with same timestamp
        existing_backup = mock_config.config_dir / "config.yaml.bak.20250127-120000"
        existing_backup.write_text("previous backup\n")

        def mock_run_side_effect(*args, **kwargs):
            cmd = args[0] if args else kwargs.get("args", [])
            if "volume" in cmd:
                return subprocess.CompletedProcess(cmd, 0)
            if "/bin/cp" in cmd:
                return subprocess.CompletedProcess(cmd, 0)
            if "/bin/cat" in cmd:
                return subprocess.CompletedProcess(cmd, 0, stdout="key: new_value\n", stderr="")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        mocker.patch("subprocess.run", side_effect=mock_run_side_effect)

        # Call config_transform with --apply
        instance.config_transform(command="transform", apply=True, quiet=True)

        # Should create numbered backup since timestamp backup exists
        numbered_backup = mock_config.config_dir / "config.yaml.bak.20250127-120000.1"
        assert numbered_backup.exists()
        assert numbered_backup.read_text() == "key: old_value\n"


class TestConfigTransformHelp:
    """Test config-transform command help output."""

    def test_config_transform_help(self, capsys):
        """instance config-transform --help should work."""
        from ots_containers.cli import app

        with pytest.raises(SystemExit) as exc_info:
            app(["instance", "config-transform", "--help"])
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "command" in captured.out.lower()
        assert "apply" in captured.out.lower()
        assert "file" in captured.out.lower()


class TestConfigTransformCLI:
    """Test config-transform CLI integration."""

    def test_config_transform_requires_command(self, capsys):
        """config-transform should require --command argument."""
        from ots_containers.cli import app

        with pytest.raises(SystemExit) as exc_info:
            app(["instance", "config-transform"])
        # cyclopts returns non-zero for missing required args
        assert exc_info.value.code != 0
