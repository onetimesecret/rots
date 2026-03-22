# tests/commands/instance/test_config_transform.py
"""Tests for the config-transform command.

These tests verify the config-transform command handles config file
transformation with proper backup and apply workflow.
"""

import subprocess

import pytest
from ots_shared.ssh import LocalExecutor

from rots.commands import instance


class TestConfigTransformCommand:
    """Test the config-transform command."""

    def test_config_transform_function_exists(self):
        """config_transform command should be defined."""
        assert hasattr(instance, "config_transform")
        assert callable(instance.config_transform)

    def test_config_transform_passes_host_to_get_executor(self, mocker, tmp_path):
        """config_transform should pass host from context to get_executor."""
        from rots import context

        mock_config = mocker.MagicMock()
        mock_config.get_executor.return_value = LocalExecutor()
        mock_config.tag = "current"
        mock_config.image = "ghcr.io/onetimesecret/onetimesecret"
        mock_config.resolve_image_tag.return_value = (
            "ghcr.io/onetimesecret/onetimesecret",
            "current",
        )
        mock_config.resolved_image_with_tag.return_value = (
            "ghcr.io/onetimesecret/onetimesecret:current"
        )
        mock_config.podman_auth_args.return_value = []
        mock_config.config_dir = tmp_path / "etc"
        mock_config.config_dir.mkdir()
        (mock_config.config_dir / "config.yaml").write_text("key: value\n")
        mocker.patch("rots.commands.instance.app.Config", return_value=mock_config)

        # Mock env file not existing
        mocker.patch(
            "rots.commands.instance.app.quadlet.DEFAULT_ENV_FILE",
            tmp_path / "nonexistent",
        )

        def mock_run_side_effect(*args, **kwargs):
            cmd = args[0] if args else kwargs.get("args", [])
            if "volume" in cmd:
                return subprocess.CompletedProcess(cmd, 0)
            if "/bin/cp" in cmd:
                return subprocess.CompletedProcess(cmd, 0)
            if "/bin/cat" in cmd:
                return subprocess.CompletedProcess(cmd, 0, stdout="key: value\n", stderr="")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        mocker.patch("subprocess.run", side_effect=mock_run_side_effect)

        # Set host context var to simulate --host flag
        token = context.host_var.set("web1.example.com")
        try:
            instance.config_transform(command="echo test", quiet=True)
        finally:
            context.host_var.reset(token)

        # Verify get_executor was called with the host argument
        mock_config.get_executor.assert_called_once_with(host="web1.example.com")

    def test_config_transform_rejects_path_traversal(self, mocker, tmp_path):
        """config_transform should reject path traversal attempts."""
        # Mock Config
        mock_config = mocker.MagicMock()
        mock_config.get_executor.return_value = LocalExecutor()
        mock_config.config_dir = tmp_path / "etc"
        mock_config.config_dir.mkdir()
        mock_config.get_executor.return_value = LocalExecutor()
        mocker.patch("rots.commands.instance.app.Config", return_value=mock_config)

        with pytest.raises(SystemExit) as exc_info:
            instance.config_transform(command="echo test", file="../etc/passwd")
        assert "path traversal" in str(exc_info.value).lower()

    def test_config_transform_rejects_absolute_path(self, mocker, tmp_path):
        """config_transform should reject absolute file paths."""
        # Mock Config
        mock_config = mocker.MagicMock()
        mock_config.get_executor.return_value = LocalExecutor()
        mock_config.config_dir = tmp_path / "etc"
        mock_config.config_dir.mkdir()
        mocker.patch("rots.commands.instance.app.Config", return_value=mock_config)

        with pytest.raises(SystemExit) as exc_info:
            instance.config_transform(command="echo test", file="/etc/passwd")
        assert "path traversal" in str(exc_info.value).lower()

    def test_config_transform_checks_file_exists(self, mocker, tmp_path):
        """config_transform should verify config file exists."""
        # Mock Config
        mock_config = mocker.MagicMock()
        mock_config.get_executor.return_value = LocalExecutor()
        mock_config.config_dir = tmp_path / "etc"
        mock_config.config_dir.mkdir()
        mocker.patch("rots.commands.instance.app.Config", return_value=mock_config)

        with pytest.raises(SystemExit) as exc_info:
            instance.config_transform(command="echo test", file="nonexistent.yaml")
        assert "not found" in str(exc_info.value).lower()

    def test_config_transform_creates_volume(self, mocker, tmp_path):
        """config_transform should create temporary volume."""
        # Mock Config
        mock_config = mocker.MagicMock()
        mock_config.get_executor.return_value = LocalExecutor()
        mock_config.tag = "current"
        mock_config.image = "ghcr.io/onetimesecret/onetimesecret"
        mock_config.resolve_image_tag.return_value = (
            "ghcr.io/onetimesecret/onetimesecret",
            "current",
        )
        mock_config.resolved_image_with_tag.return_value = (
            "ghcr.io/onetimesecret/onetimesecret:current"
        )
        mock_config.podman_auth_args.return_value = []
        mock_config.config_dir = tmp_path / "etc"
        mock_config.config_dir.mkdir()
        (mock_config.config_dir / "config.yaml").write_text("key: value\n")
        mocker.patch("rots.commands.instance.app.Config", return_value=mock_config)

        # Mock env file not existing
        mocker.patch(
            "rots.commands.instance.app.quadlet.DEFAULT_ENV_FILE",
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
        mock_config.get_executor.return_value = LocalExecutor()
        mock_config.tag = "current"
        mock_config.image = "ghcr.io/onetimesecret/onetimesecret"
        mock_config.resolve_image_tag.return_value = (
            "ghcr.io/onetimesecret/onetimesecret",
            "current",
        )
        mock_config.resolved_image_with_tag.return_value = (
            "ghcr.io/onetimesecret/onetimesecret:current"
        )
        mock_config.podman_auth_args.return_value = []
        mock_config.config_dir = tmp_path / "etc"
        mock_config.config_dir.mkdir()
        (mock_config.config_dir / "config.yaml").write_text("key: value\n")
        mocker.patch("rots.commands.instance.app.Config", return_value=mock_config)

        # Mock env file not existing
        mocker.patch(
            "rots.commands.instance.app.quadlet.DEFAULT_ENV_FILE",
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
        mock_config.get_executor.return_value = LocalExecutor()
        mock_config.tag = "current"
        mock_config.image = "ghcr.io/onetimesecret/onetimesecret"
        mock_config.resolve_image_tag.return_value = (
            "ghcr.io/onetimesecret/onetimesecret",
            "current",
        )
        mock_config.resolved_image_with_tag.return_value = (
            "ghcr.io/onetimesecret/onetimesecret:current"
        )
        mock_config.podman_auth_args.return_value = []
        mock_config.config_dir = tmp_path / "etc"
        mock_config.config_dir.mkdir()
        (mock_config.config_dir / "config.yaml").write_text("key: value\n")
        mocker.patch("rots.commands.instance.app.Config", return_value=mock_config)

        # Mock env file not existing
        mocker.patch(
            "rots.commands.instance.app.quadlet.DEFAULT_ENV_FILE",
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
        mock_config.get_executor.return_value = LocalExecutor()
        mock_config.tag = "current"
        mock_config.image = "ghcr.io/onetimesecret/onetimesecret"
        mock_config.resolve_image_tag.return_value = (
            "ghcr.io/onetimesecret/onetimesecret",
            "current",
        )
        mock_config.resolved_image_with_tag.return_value = (
            "ghcr.io/onetimesecret/onetimesecret:current"
        )
        mock_config.podman_auth_args.return_value = []
        mock_config.config_dir = tmp_path / "etc"
        mock_config.config_dir.mkdir()
        (mock_config.config_dir / "config.yaml").write_text("key: old_value\n")
        mocker.patch("rots.commands.instance.app.Config", return_value=mock_config)

        # Mock env file not existing
        mocker.patch(
            "rots.commands.instance.app.quadlet.DEFAULT_ENV_FILE",
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
        instance.config_transform(command="transform")

        # Verify diff was shown (stdout) and dry-run message logged (stderr)
        captured = capsys.readouterr()
        assert "old_value" in captured.out
        assert "new_value" in captured.out
        assert "dry run" in captured.err.lower() or "no changes made" in captured.err.lower()

        # File should not be modified
        assert (mock_config.config_dir / "config.yaml").read_text() == "key: old_value\n"

    def test_config_transform_apply_creates_backup(self, mocker, tmp_path, capsys):
        """config_transform --apply should create backup file."""
        # Mock Config
        mock_config = mocker.MagicMock()
        mock_config.get_executor.return_value = LocalExecutor()
        mock_config.tag = "current"
        mock_config.image = "ghcr.io/onetimesecret/onetimesecret"
        mock_config.resolve_image_tag.return_value = (
            "ghcr.io/onetimesecret/onetimesecret",
            "current",
        )
        mock_config.resolved_image_with_tag.return_value = (
            "ghcr.io/onetimesecret/onetimesecret:current"
        )
        mock_config.podman_auth_args.return_value = []
        mock_config.config_dir = tmp_path / "etc"
        mock_config.config_dir.mkdir()
        config_file = mock_config.config_dir / "config.yaml"
        config_file.write_text("key: old_value\n")
        mocker.patch("rots.commands.instance.app.Config", return_value=mock_config)

        # Mock env file not existing
        mocker.patch(
            "rots.commands.instance.app.quadlet.DEFAULT_ENV_FILE",
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
        mock_config.get_executor.return_value = LocalExecutor()
        mock_config.tag = "current"
        mock_config.image = "ghcr.io/onetimesecret/onetimesecret"
        mock_config.resolve_image_tag.return_value = (
            "ghcr.io/onetimesecret/onetimesecret",
            "current",
        )
        mock_config.resolved_image_with_tag.return_value = (
            "ghcr.io/onetimesecret/onetimesecret:current"
        )
        mock_config.podman_auth_args.return_value = []
        mock_config.config_dir = tmp_path / "etc"
        mock_config.config_dir.mkdir()
        config_file = mock_config.config_dir / "config.yaml"
        config_file.write_text("key: old_value\n")
        mocker.patch("rots.commands.instance.app.Config", return_value=mock_config)

        # Mock env file not existing
        mocker.patch(
            "rots.commands.instance.app.quadlet.DEFAULT_ENV_FILE",
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
        mock_config.get_executor.return_value = LocalExecutor()
        mock_config.tag = "current"
        mock_config.image = "ghcr.io/onetimesecret/onetimesecret"
        mock_config.resolve_image_tag.return_value = (
            "ghcr.io/onetimesecret/onetimesecret",
            "current",
        )
        mock_config.resolved_image_with_tag.return_value = (
            "ghcr.io/onetimesecret/onetimesecret:current"
        )
        mock_config.podman_auth_args.return_value = []
        mock_config.config_dir = tmp_path / "etc"
        mock_config.config_dir.mkdir()
        config_file = mock_config.config_dir / "config.yaml"
        config_file.write_text("key: value\n")
        mocker.patch("rots.commands.instance.app.Config", return_value=mock_config)

        # Mock env file not existing
        mocker.patch(
            "rots.commands.instance.app.quadlet.DEFAULT_ENV_FILE",
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

        # Call config_transform (quiet=False to see status messages)
        instance.config_transform(command="transform", quiet=False)

        # Verify "no changes" message
        captured = capsys.readouterr()
        assert "no changes" in captured.err.lower()

    def test_config_transform_fails_when_command_fails(self, mocker, tmp_path, capsys):
        """config_transform should fail when migration command fails."""
        # Mock Config
        mock_config = mocker.MagicMock()
        mock_config.get_executor.return_value = LocalExecutor()
        mock_config.tag = "current"
        mock_config.image = "ghcr.io/onetimesecret/onetimesecret"
        mock_config.resolve_image_tag.return_value = (
            "ghcr.io/onetimesecret/onetimesecret",
            "current",
        )
        mock_config.resolved_image_with_tag.return_value = (
            "ghcr.io/onetimesecret/onetimesecret:current"
        )
        mock_config.podman_auth_args.return_value = []
        mock_config.config_dir = tmp_path / "etc"
        mock_config.config_dir.mkdir()
        (mock_config.config_dir / "config.yaml").write_text("key: value\n")
        mocker.patch("rots.commands.instance.app.Config", return_value=mock_config)

        # Mock env file not existing
        mocker.patch(
            "rots.commands.instance.app.quadlet.DEFAULT_ENV_FILE",
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
        assert "failed" in captured.err.lower()

    def test_config_transform_fails_when_no_output_file(self, mocker, tmp_path, capsys):
        """config_transform should fail when no .new file is produced."""
        # Mock Config
        mock_config = mocker.MagicMock()
        mock_config.get_executor.return_value = LocalExecutor()
        mock_config.tag = "current"
        mock_config.image = "ghcr.io/onetimesecret/onetimesecret"
        mock_config.resolve_image_tag.return_value = (
            "ghcr.io/onetimesecret/onetimesecret",
            "current",
        )
        mock_config.resolved_image_with_tag.return_value = (
            "ghcr.io/onetimesecret/onetimesecret:current"
        )
        mock_config.podman_auth_args.return_value = []
        mock_config.config_dir = tmp_path / "etc"
        mock_config.config_dir.mkdir()
        (mock_config.config_dir / "config.yaml").write_text("key: value\n")
        mocker.patch("rots.commands.instance.app.Config", return_value=mock_config)

        # Mock env file not existing
        mocker.patch(
            "rots.commands.instance.app.quadlet.DEFAULT_ENV_FILE",
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
        assert "no transformed file" in captured.err.lower()

    def test_config_transform_uses_custom_file(self, mocker, tmp_path):
        """config_transform -f should use specified file."""
        # Mock Config
        mock_config = mocker.MagicMock()
        mock_config.get_executor.return_value = LocalExecutor()
        mock_config.tag = "current"
        mock_config.image = "ghcr.io/onetimesecret/onetimesecret"
        mock_config.resolve_image_tag.return_value = (
            "ghcr.io/onetimesecret/onetimesecret",
            "current",
        )
        mock_config.resolved_image_with_tag.return_value = (
            "ghcr.io/onetimesecret/onetimesecret:current"
        )
        mock_config.podman_auth_args.return_value = []
        mock_config.config_dir = tmp_path / "etc"
        mock_config.config_dir.mkdir()
        (mock_config.config_dir / "auth.yaml").write_text("auth: config\n")
        mocker.patch("rots.commands.instance.app.Config", return_value=mock_config)

        # Mock env file not existing
        mocker.patch(
            "rots.commands.instance.app.quadlet.DEFAULT_ENV_FILE",
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
        from rots.environment_file import SecretSpec

        # Mock Config
        mock_config = mocker.MagicMock()
        mock_config.get_executor.return_value = LocalExecutor()
        mock_config.tag = "current"
        mock_config.image = "ghcr.io/onetimesecret/onetimesecret"
        mock_config.resolve_image_tag.return_value = (
            "ghcr.io/onetimesecret/onetimesecret",
            "current",
        )
        mock_config.resolved_image_with_tag.return_value = (
            "ghcr.io/onetimesecret/onetimesecret:current"
        )
        mock_config.podman_auth_args.return_value = []
        mock_config.config_dir = tmp_path / "etc"
        mock_config.config_dir.mkdir()
        (mock_config.config_dir / "config.yaml").write_text("key: value\n")
        mocker.patch("rots.commands.instance.app.Config", return_value=mock_config)

        # Create env file with secrets
        env_file = tmp_path / "onetimesecret"
        env_file.write_text("SECRET_VARIABLE_NAMES=AUTH_SECRET\n")
        mocker.patch(
            "rots.commands.instance.app.quadlet.DEFAULT_ENV_FILE",
            env_file,
        )

        # Mock get_secrets_from_env_file
        mock_secrets = [
            SecretSpec(env_var_name="AUTH_SECRET", secret_name="ots_hmac_secret"),
        ]
        mocker.patch(
            "rots.commands.instance._helpers.get_secrets_from_env_file",
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
        mock_config.get_executor.return_value = LocalExecutor()
        mock_config.tag = "current"
        mock_config.image = "ghcr.io/onetimesecret/onetimesecret"
        mock_config.resolve_image_tag.return_value = (
            "ghcr.io/onetimesecret/onetimesecret",
            "current",
        )
        mock_config.resolved_image_with_tag.return_value = (
            "ghcr.io/onetimesecret/onetimesecret:current"
        )
        mock_config.podman_auth_args.return_value = []
        mock_config.config_dir = tmp_path / "etc"
        mock_config.config_dir.mkdir()
        config_file = mock_config.config_dir / "config.yaml"
        config_file.write_text("key: old_value\n")

        mocker.patch("rots.commands.instance.app.Config", return_value=mock_config)

        # Mock env file not existing
        mocker.patch(
            "rots.commands.instance.app.quadlet.DEFAULT_ENV_FILE",
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
        from rots.cli import app

        with pytest.raises(SystemExit) as exc_info:
            app(["instance", "config-transform", "--help"])
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "command" in captured.out.lower()
        assert "apply" in captured.out.lower()
        assert "file" in captured.out.lower()


class TestConfigTransformRemote:
    """Test config-transform command with remote executor.

    The config_transform command uses isinstance(ex, LocalExecutor) to
    detect remote mode. When remote, it uses executor.run() for file
    existence checks, content reads, backups, and writes instead of
    local Path operations.
    """

    def _make_remote_executor(self, mocker):
        """Create a mock executor that is NOT a LocalExecutor (triggers remote mode)."""
        from unittest.mock import MagicMock

        mock_ex = MagicMock()
        # Not a LocalExecutor -> is_remote = True
        mock_ex.__class__ = type("SSHExecutor", (), {})
        return mock_ex

    def test_config_transform_remote_checks_file_via_executor(self, mocker, tmp_path):
        """config_transform remote should use executor 'test -f' to check file exists."""
        from unittest.mock import MagicMock

        mock_config = mocker.MagicMock()
        mock_ex = self._make_remote_executor(mocker)
        mock_config.get_executor.return_value = mock_ex
        mock_config.tag = "current"
        mock_config.image = "ghcr.io/onetimesecret/onetimesecret"
        mock_config.resolve_image_tag.return_value = (
            "ghcr.io/onetimesecret/onetimesecret",
            "current",
        )
        mock_config.resolved_image_with_tag.return_value = (
            "ghcr.io/onetimesecret/onetimesecret:current"
        )
        mock_config.podman_auth_args.return_value = []
        mock_config.config_dir = tmp_path / "etc"
        mock_config.config_dir.mkdir()
        mocker.patch("rots.commands.instance.app.Config", return_value=mock_config)

        # Simulate file not found on remote
        test_result = MagicMock()
        test_result.ok = False
        mock_ex.run.return_value = test_result

        with pytest.raises(SystemExit) as exc_info:
            instance.config_transform(command="echo test", quiet=True)

        assert "not found" in str(exc_info.value).lower()
        # Verify 'test -f' was called
        first_call = mock_ex.run.call_args_list[0]
        cmd = first_call[0][0]
        assert cmd[0] == "test"
        assert cmd[1] == "-f"

    def test_config_transform_remote_reads_original_via_cat(self, mocker, tmp_path):
        """config_transform remote should read original config via 'cat'."""
        from unittest.mock import MagicMock

        mock_config = mocker.MagicMock()
        mock_ex = self._make_remote_executor(mocker)
        mock_config.get_executor.return_value = mock_ex
        mock_config.tag = "current"
        mock_config.image = "ghcr.io/test/img"
        mock_config.resolve_image_tag.return_value = ("ghcr.io/test/img", "current")
        mock_config.resolved_image_with_tag.return_value = "ghcr.io/test/img:current"
        mock_config.podman_auth_args.return_value = []
        mock_config.config_dir = tmp_path / "etc"
        mock_config.config_dir.mkdir()
        mocker.patch("rots.commands.instance.app.Config", return_value=mock_config)
        mocker.patch(
            "rots.commands.instance.app.quadlet.DEFAULT_ENV_FILE",
            tmp_path / "nonexistent",
        )

        call_log = []

        def mock_run(cmd, **kwargs):
            call_log.append(cmd)
            result = MagicMock()
            # 'test -f' succeeds
            if cmd[0] == "test":
                result.ok = True
                return result
            # env file check fails
            if cmd[0] == "test" and "-f" in cmd:
                result.ok = False
                return result
            # podman volume create
            if "volume" in cmd and "create" in cmd:
                result.ok = True
                result.returncode = 0
                return result
            # podman run (copy, transform, read)
            if "podman" in cmd:
                result.ok = True
                result.returncode = 0
                if "/bin/cat" in cmd:
                    result.stdout = "key: new_value\n"
                else:
                    result.stdout = ""
                result.stderr = ""
                return result
            # cat for original content
            if cmd[0] == "cat":
                result.ok = True
                result.stdout = "key: new_value\n"  # same content = no diff
                result.returncode = 0
                return result
            result.ok = True
            result.returncode = 0
            result.stdout = ""
            result.stderr = ""
            return result

        mock_ex.run.side_effect = mock_run

        instance.config_transform(command="echo test", quiet=True)

        # Verify 'cat' was used to read original content (remote path)
        cat_calls = [c for c in call_log if c and c[0] == "cat"]
        assert len(cat_calls) >= 1

    def test_config_transform_remote_apply_uses_cp_and_tee(self, mocker, tmp_path, capsys):
        """config_transform remote --apply should use cp for backup and tee for write."""
        from unittest.mock import MagicMock

        mock_config = mocker.MagicMock()
        mock_ex = self._make_remote_executor(mocker)
        mock_config.get_executor.return_value = mock_ex
        mock_config.tag = "current"
        mock_config.image = "ghcr.io/test/img"
        mock_config.resolve_image_tag.return_value = ("ghcr.io/test/img", "current")
        mock_config.resolved_image_with_tag.return_value = "ghcr.io/test/img:current"
        mock_config.podman_auth_args.return_value = []
        mock_config.config_dir = tmp_path / "etc"
        mock_config.config_dir.mkdir()
        mocker.patch("rots.commands.instance.app.Config", return_value=mock_config)
        mocker.patch(
            "rots.commands.instance.app.quadlet.DEFAULT_ENV_FILE",
            tmp_path / "nonexistent",
        )

        call_log = []

        def mock_run(cmd, **kwargs):
            call_log.append((cmd, kwargs))
            result = MagicMock()
            result.ok = True
            result.returncode = 0
            result.stdout = ""
            result.stderr = ""
            if cmd[0] == "test":
                return result
            if "podman" in cmd:
                if "/bin/cat" in cmd:
                    result.stdout = "key: new_value\n"
                return result
            if cmd[0] == "cat":
                result.stdout = "key: old_value\n"
                return result
            return result

        mock_ex.run.side_effect = mock_run

        instance.config_transform(command="echo test", apply=True, quiet=True)

        # Verify 'cp -p' was used for backup (remote path)
        cp_calls = [c for c, kw in call_log if c and c[0] == "cp"]
        assert len(cp_calls) >= 1
        assert "-p" in cp_calls[0]

        # Verify 'tee' was used to write new content (remote path)
        tee_calls = [c for c, kw in call_log if c and c[0] == "tee"]
        assert len(tee_calls) >= 1


class TestConfigTransformPositionalReference:
    """config_transform() accepts positional image reference."""

    def test_reference_overrides_image_and_tag(self, mocker, tmp_path):
        """config_transform with positional reference should override image and tag."""
        mock_config = mocker.MagicMock()
        mock_config.get_executor.return_value = LocalExecutor()
        mock_config.tag = "current"
        mock_config.image = "ghcr.io/onetimesecret/onetimesecret"
        mock_config.resolve_image_tag.return_value = ("custom/image", "v2.0")
        mock_config.resolved_image_with_tag.return_value = "custom/image:v2.0"
        mock_config.podman_auth_args.return_value = []
        mock_config.config_dir = tmp_path / "etc"
        mock_config.config_dir.mkdir()
        (mock_config.config_dir / "config.yaml").write_text("key: value\n")
        mocker.patch("rots.commands.instance.app.Config", return_value=mock_config)
        mocker.patch(
            "rots.commands.instance.app.quadlet.DEFAULT_ENV_FILE",
            tmp_path / "nonexistent",
        )

        # Track dataclasses.replace calls
        replace_calls = []

        def tracking_replace(obj, **kwargs):
            replace_calls.append(kwargs)
            for k, v in kwargs.items():
                setattr(obj, k, v)
            new_image = kwargs.get("image", obj.image)
            new_tag = kwargs.get("tag", obj.tag)
            obj.resolve_image_tag.return_value = (new_image, new_tag)
            obj.resolved_image_with_tag.return_value = f"{new_image}:{new_tag}"
            obj.podman_auth_args.return_value = []
            return obj

        mocker.patch(
            "rots.commands.instance.app.dataclasses.replace",
            side_effect=tracking_replace,
        )

        def mock_run_side_effect(*args, **kwargs):
            cmd = args[0] if args else kwargs.get("args", [])
            if "volume" in cmd:
                return subprocess.CompletedProcess(cmd, 0)
            if "/bin/cp" in cmd:
                return subprocess.CompletedProcess(cmd, 0)
            if "/bin/cat" in cmd:
                return subprocess.CompletedProcess(cmd, 0, stdout="key: value\n", stderr="")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        mocker.patch("subprocess.run", side_effect=mock_run_side_effect)

        instance.config_transform(reference="custom/image:v2.0", command="echo test", quiet=True)

        assert len(replace_calls) == 1
        assert replace_calls[0]["image"] == "custom/image"
        assert replace_calls[0]["tag"] == "v2.0"

    def test_reference_tag_beats_flag_tag(self, mocker, tmp_path):
        """Positional ref tag takes precedence over --tag flag."""
        mock_config = mocker.MagicMock()
        mock_config.get_executor.return_value = LocalExecutor()
        mock_config.tag = "current"
        mock_config.image = "ghcr.io/onetimesecret/onetimesecret"
        mock_config.resolve_image_tag.return_value = ("img", "ref-tag")
        mock_config.resolved_image_with_tag.return_value = "img:ref-tag"
        mock_config.podman_auth_args.return_value = []
        mock_config.config_dir = tmp_path / "etc"
        mock_config.config_dir.mkdir()
        (mock_config.config_dir / "config.yaml").write_text("key: value\n")
        mocker.patch("rots.commands.instance.app.Config", return_value=mock_config)
        mocker.patch(
            "rots.commands.instance.app.quadlet.DEFAULT_ENV_FILE",
            tmp_path / "nonexistent",
        )

        replace_calls = []

        def tracking_replace(obj, **kwargs):
            replace_calls.append(kwargs)
            for k, v in kwargs.items():
                setattr(obj, k, v)
            new_image = kwargs.get("image", obj.image)
            new_tag = kwargs.get("tag", obj.tag)
            obj.resolve_image_tag.return_value = (new_image, new_tag)
            obj.resolved_image_with_tag.return_value = f"{new_image}:{new_tag}"
            obj.podman_auth_args.return_value = []
            return obj

        mocker.patch(
            "rots.commands.instance.app.dataclasses.replace",
            side_effect=tracking_replace,
        )

        def mock_run_side_effect(*args, **kwargs):
            cmd = args[0] if args else kwargs.get("args", [])
            if "volume" in cmd:
                return subprocess.CompletedProcess(cmd, 0)
            if "/bin/cp" in cmd:
                return subprocess.CompletedProcess(cmd, 0)
            if "/bin/cat" in cmd:
                return subprocess.CompletedProcess(cmd, 0, stdout="key: value\n", stderr="")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        mocker.patch("subprocess.run", side_effect=mock_run_side_effect)

        instance.config_transform(
            reference="img:ref-tag", command="echo test", tag="flag-tag", quiet=True
        )

        assert len(replace_calls) == 1
        # Reference tag should win over flag tag
        assert replace_calls[0]["tag"] == "ref-tag"

    def test_no_reference_no_replace(self, mocker, tmp_path):
        """config_transform without reference or tag should not call replace."""
        mock_config = mocker.MagicMock()
        mock_config.get_executor.return_value = LocalExecutor()
        mock_config.tag = "current"
        mock_config.image = "ghcr.io/onetimesecret/onetimesecret"
        mock_config.resolve_image_tag.return_value = (
            "ghcr.io/onetimesecret/onetimesecret",
            "current",
        )
        mock_config.resolved_image_with_tag.return_value = (
            "ghcr.io/onetimesecret/onetimesecret:current"
        )
        mock_config.podman_auth_args.return_value = []
        mock_config.config_dir = tmp_path / "etc"
        mock_config.config_dir.mkdir()
        (mock_config.config_dir / "config.yaml").write_text("key: value\n")
        mocker.patch("rots.commands.instance.app.Config", return_value=mock_config)
        mocker.patch(
            "rots.commands.instance.app.quadlet.DEFAULT_ENV_FILE",
            tmp_path / "nonexistent",
        )

        replace_calls = []

        def tracking_replace(obj, **kwargs):
            replace_calls.append(kwargs)
            for k, v in kwargs.items():
                setattr(obj, k, v)
            return obj

        mocker.patch(
            "rots.commands.instance.app.dataclasses.replace",
            side_effect=tracking_replace,
        )

        def mock_run_side_effect(*args, **kwargs):
            cmd = args[0] if args else kwargs.get("args", [])
            if "volume" in cmd:
                return subprocess.CompletedProcess(cmd, 0)
            if "/bin/cp" in cmd:
                return subprocess.CompletedProcess(cmd, 0)
            if "/bin/cat" in cmd:
                return subprocess.CompletedProcess(cmd, 0, stdout="key: value\n", stderr="")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        mocker.patch("subprocess.run", side_effect=mock_run_side_effect)

        instance.config_transform(command="echo test", quiet=True)

        assert len(replace_calls) == 0


class TestConfigTransformCLI:
    """Test config-transform CLI integration."""

    def test_config_transform_requires_command(self, capsys):
        """config-transform should require --command argument."""
        from rots.cli import app

        with pytest.raises(SystemExit) as exc_info:
            app(["instance", "config-transform"])
        # cyclopts returns non-zero for missing required args
        assert exc_info.value.code != 0
