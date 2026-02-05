# tests/commands/instance/test_shell.py
"""Tests for the shell command.

These tests verify the shell command builds correct podman commands
for ephemeral and persistent migration shells.
"""

import subprocess

import pytest

from ots_containers.commands import instance


class TestShellCommand:
    """Test the shell command."""

    def test_shell_function_exists(self):
        """shell command should be defined."""
        assert hasattr(instance, "shell")
        assert callable(instance.shell)

    def test_shell_builds_tmpfs_command(self, mocker, tmp_path):
        """shell should use tmpfs by default."""
        # Mock Config
        mock_config = mocker.MagicMock()
        mock_config.tag = "current"
        mock_config.config_dir = tmp_path / "etc"
        mock_config.config_dir.mkdir()
        mocker.patch("ots_containers.commands.instance.app.Config", return_value=mock_config)

        # Mock env file not existing
        mocker.patch(
            "ots_containers.commands.instance.app.quadlet.DEFAULT_ENV_FILE",
            tmp_path / "nonexistent",
        )

        # Mock subprocess.run
        mock_run = mocker.patch("subprocess.run")
        mock_run.return_value = subprocess.CompletedProcess([], 0)

        # Call shell command
        instance.shell(quiet=True)

        # Verify podman run was called
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]

        assert cmd[0] == "podman"
        assert cmd[1] == "run"
        assert "--rm" in cmd
        assert "-it" in cmd
        assert "--network=host" in cmd
        assert "--tmpfs" in cmd
        assert "/app/data" in cmd[cmd.index("--tmpfs") + 1]
        assert "/bin/bash" in cmd

    def test_shell_builds_persistent_volume_command(self, mocker, tmp_path):
        """shell --persistent should create named volume."""
        # Mock Config
        mock_config = mocker.MagicMock()
        mock_config.tag = "current"
        mock_config.config_dir = tmp_path / "etc"
        mock_config.config_dir.mkdir()
        mocker.patch("ots_containers.commands.instance.app.Config", return_value=mock_config)

        # Mock env file not existing
        mocker.patch(
            "ots_containers.commands.instance.app.quadlet.DEFAULT_ENV_FILE",
            tmp_path / "nonexistent",
        )

        # Mock subprocess.run
        mock_run = mocker.patch("subprocess.run")
        mock_run.return_value = subprocess.CompletedProcess([], 0)

        # Call shell command with persistent flag
        instance.shell(persistent="upgrade-v024", quiet=True)

        # Verify podman run was called
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]

        assert "-v" in cmd
        # Find the volume mount argument
        v_idx = cmd.index("-v")
        volume_arg = cmd[v_idx + 1]
        assert "ots-migration-upgrade-v024:/app/data" in volume_arg
        assert "--tmpfs" not in cmd

    def test_shell_includes_secrets_from_env_file(self, mocker, tmp_path):
        """shell should include secrets when env file exists."""
        from ots_containers.environment_file import SecretSpec

        # Mock Config
        mock_config = mocker.MagicMock()
        mock_config.tag = "current"
        mock_config.config_dir = tmp_path / "etc"
        mock_config.config_dir.mkdir()
        mocker.patch("ots_containers.commands.instance.app.Config", return_value=mock_config)

        # Create env file with secrets
        env_file = tmp_path / "onetimesecret"
        env_file.write_text("SECRET_VARIABLE_NAMES=HMAC_SECRET,API_KEY\n")
        mocker.patch(
            "ots_containers.commands.instance.app.quadlet.DEFAULT_ENV_FILE",
            env_file,
        )

        # Mock get_secrets_from_env_file
        mock_secrets = [
            SecretSpec(env_var_name="HMAC_SECRET", secret_name="ots_hmac_secret"),
            SecretSpec(env_var_name="API_KEY", secret_name="ots_api_key"),
        ]
        mocker.patch(
            "ots_containers.commands.instance._helpers.get_secrets_from_env_file",
            return_value=mock_secrets,
        )

        # Mock subprocess.run
        mock_run = mocker.patch("subprocess.run")
        mock_run.return_value = subprocess.CompletedProcess([], 0)

        # Call shell command
        instance.shell(quiet=True)

        # Verify secrets were included
        cmd = mock_run.call_args[0][0]
        cmd_str = " ".join(cmd)
        assert "--secret" in cmd_str
        assert "ots_hmac_secret" in cmd_str
        assert "ots_api_key" in cmd_str

    def test_shell_includes_env_file(self, mocker, tmp_path):
        """shell should include --env-file when file exists."""
        # Mock Config
        mock_config = mocker.MagicMock()
        mock_config.tag = "current"
        mock_config.config_dir = tmp_path / "etc"
        mock_config.config_dir.mkdir()
        mocker.patch("ots_containers.commands.instance.app.Config", return_value=mock_config)

        # Create env file
        env_file = tmp_path / "onetimesecret"
        env_file.write_text("REDIS_URL=redis://localhost:6379\n")
        mocker.patch(
            "ots_containers.commands.instance.app.quadlet.DEFAULT_ENV_FILE",
            env_file,
        )

        # Mock subprocess.run
        mock_run = mocker.patch("subprocess.run")
        mock_run.return_value = subprocess.CompletedProcess([], 0)

        # Call shell command
        instance.shell(quiet=True)

        # Verify env file was included
        cmd = mock_run.call_args[0][0]
        assert "--env-file" in cmd
        env_idx = cmd.index("--env-file")
        assert str(env_file) == cmd[env_idx + 1]

    def test_shell_mounts_config_readonly(self, mocker, tmp_path):
        """shell should mount config directory read-only."""
        # Mock Config
        mock_config = mocker.MagicMock()
        mock_config.tag = "current"
        mock_config.config_dir = tmp_path / "etc"
        mock_config.config_dir.mkdir()
        mocker.patch("ots_containers.commands.instance.app.Config", return_value=mock_config)

        # Mock env file not existing
        mocker.patch(
            "ots_containers.commands.instance.app.quadlet.DEFAULT_ENV_FILE",
            tmp_path / "nonexistent",
        )

        # Mock subprocess.run
        mock_run = mocker.patch("subprocess.run")
        mock_run.return_value = subprocess.CompletedProcess([], 0)

        # Call shell command
        instance.shell(quiet=True)

        # Verify config mount
        cmd = mock_run.call_args[0][0]
        cmd_str = " ".join(cmd)
        assert f"{mock_config.config_dir}:/app/etc:ro" in cmd_str

    def test_shell_runs_command_with_bash_c(self, mocker, tmp_path):
        """shell -c should run command via bash -c."""
        # Mock Config
        mock_config = mocker.MagicMock()
        mock_config.tag = "current"
        mock_config.config_dir = tmp_path / "etc"
        mock_config.config_dir.mkdir()
        mocker.patch("ots_containers.commands.instance.app.Config", return_value=mock_config)

        # Mock env file not existing
        mocker.patch(
            "ots_containers.commands.instance.app.quadlet.DEFAULT_ENV_FILE",
            tmp_path / "nonexistent",
        )

        # Mock subprocess.run
        mock_run = mocker.patch("subprocess.run")
        mock_run.return_value = subprocess.CompletedProcess([], 0)

        # Call shell command with -c
        instance.shell(command="bin/ots migrate", quiet=True)

        # Verify command was passed to bash -c
        cmd = mock_run.call_args[0][0]
        assert "/bin/bash" in cmd
        assert "-c" in cmd
        assert "bin/ots migrate" in cmd
        # Should not have -it when command is provided
        assert "-it" not in cmd

    def test_shell_uses_interactive_when_no_command(self, mocker, tmp_path):
        """shell without -c should be interactive."""
        # Mock Config
        mock_config = mocker.MagicMock()
        mock_config.tag = "current"
        mock_config.config_dir = tmp_path / "etc"
        mock_config.config_dir.mkdir()
        mocker.patch("ots_containers.commands.instance.app.Config", return_value=mock_config)

        # Mock env file not existing
        mocker.patch(
            "ots_containers.commands.instance.app.quadlet.DEFAULT_ENV_FILE",
            tmp_path / "nonexistent",
        )

        # Mock subprocess.run
        mock_run = mocker.patch("subprocess.run")
        mock_run.return_value = subprocess.CompletedProcess([], 0)

        # Call shell command without -c
        instance.shell(quiet=True)

        # Verify interactive mode
        cmd = mock_run.call_args[0][0]
        assert "-it" in cmd
        assert "-c" not in cmd

    def test_shell_uses_local_image_by_default(self, mocker, tmp_path):
        """shell should use local image by default."""
        # Mock Config
        mock_config = mocker.MagicMock()
        mock_config.tag = "v0.24.0"
        mock_config.config_dir = tmp_path / "etc"
        mock_config.config_dir.mkdir()
        mocker.patch("ots_containers.commands.instance.app.Config", return_value=mock_config)

        # Mock env file not existing
        mocker.patch(
            "ots_containers.commands.instance.app.quadlet.DEFAULT_ENV_FILE",
            tmp_path / "nonexistent",
        )

        # Mock subprocess.run
        mock_run = mocker.patch("subprocess.run")
        mock_run.return_value = subprocess.CompletedProcess([], 0)

        # Call shell command
        instance.shell(quiet=True)

        # Verify local image used
        cmd = mock_run.call_args[0][0]
        # Should use localhost/onetimesecret format
        assert "onetimesecret:v0.24.0" in cmd
        assert "ghcr.io" not in " ".join(cmd)

    def test_shell_uses_remote_image_with_flag(self, mocker, tmp_path):
        """shell --remote should use registry image."""
        # Mock Config
        mock_config = mocker.MagicMock()
        mock_config.tag = "v0.24.0"
        mock_config.image = "ghcr.io/onetimesecret/onetimesecret"
        mock_config.config_dir = tmp_path / "etc"
        mock_config.config_dir.mkdir()
        mock_config.resolve_image_tag.return_value = (
            "ghcr.io/onetimesecret/onetimesecret",
            "v0.24.0",
        )
        mocker.patch("ots_containers.commands.instance.app.Config", return_value=mock_config)

        # Mock env file not existing
        mocker.patch(
            "ots_containers.commands.instance.app.quadlet.DEFAULT_ENV_FILE",
            tmp_path / "nonexistent",
        )

        # Mock subprocess.run
        mock_run = mocker.patch("subprocess.run")
        mock_run.return_value = subprocess.CompletedProcess([], 0)

        # Call shell command with --remote
        instance.shell(remote=True, quiet=True)

        # Verify registry image used
        cmd = mock_run.call_args[0][0]
        assert "ghcr.io/onetimesecret/onetimesecret:v0.24.0" in cmd

    def test_shell_uses_specified_tag(self, mocker, tmp_path):
        """shell --tag should override default tag."""
        # Mock Config
        mock_config = mocker.MagicMock()
        mock_config.tag = "current"
        mock_config.config_dir = tmp_path / "etc"
        mock_config.config_dir.mkdir()
        mocker.patch("ots_containers.commands.instance.app.Config", return_value=mock_config)

        # Mock env file not existing
        mocker.patch(
            "ots_containers.commands.instance.app.quadlet.DEFAULT_ENV_FILE",
            tmp_path / "nonexistent",
        )

        # Mock subprocess.run
        mock_run = mocker.patch("subprocess.run")
        mock_run.return_value = subprocess.CompletedProcess([], 0)

        # Call shell command with --tag
        instance.shell(tag="test-tag-123", quiet=True)

        # Verify specified tag used
        cmd = mock_run.call_args[0][0]
        assert "onetimesecret:test-tag-123" in cmd

    def test_shell_exits_with_command_exit_code(self, mocker, tmp_path):
        """shell should propagate exit code from command."""
        # Mock Config
        mock_config = mocker.MagicMock()
        mock_config.tag = "current"
        mock_config.config_dir = tmp_path / "etc"
        mock_config.config_dir.mkdir()
        mocker.patch("ots_containers.commands.instance.app.Config", return_value=mock_config)

        # Mock env file not existing
        mocker.patch(
            "ots_containers.commands.instance.app.quadlet.DEFAULT_ENV_FILE",
            tmp_path / "nonexistent",
        )

        # Mock subprocess.run to fail
        mock_run = mocker.patch("subprocess.run")
        mock_run.side_effect = subprocess.CalledProcessError(42, "podman")

        # Call shell command
        with pytest.raises(SystemExit) as exc_info:
            instance.shell(quiet=True)

        assert exc_info.value.code == 42

    def test_shell_prints_command_when_not_quiet(self, mocker, tmp_path, capsys):
        """shell should print command when not quiet."""
        # Mock Config
        mock_config = mocker.MagicMock()
        mock_config.tag = "current"
        mock_config.config_dir = tmp_path / "etc"
        mock_config.config_dir.mkdir()
        mocker.patch("ots_containers.commands.instance.app.Config", return_value=mock_config)

        # Mock env file not existing
        mocker.patch(
            "ots_containers.commands.instance.app.quadlet.DEFAULT_ENV_FILE",
            tmp_path / "nonexistent",
        )

        # Mock subprocess.run
        mock_run = mocker.patch("subprocess.run")
        mock_run.return_value = subprocess.CompletedProcess([], 0)

        # Call shell command without quiet
        instance.shell(quiet=False)

        # Verify command was printed
        captured = capsys.readouterr()
        assert "podman run" in captured.out

    def test_shell_suppresses_output_when_quiet(self, mocker, tmp_path, capsys):
        """shell --quiet should suppress output."""
        # Mock Config
        mock_config = mocker.MagicMock()
        mock_config.tag = "current"
        mock_config.config_dir = tmp_path / "etc"
        mock_config.config_dir.mkdir()
        mocker.patch("ots_containers.commands.instance.app.Config", return_value=mock_config)

        # Mock env file not existing
        mocker.patch(
            "ots_containers.commands.instance.app.quadlet.DEFAULT_ENV_FILE",
            tmp_path / "nonexistent",
        )

        # Mock subprocess.run
        mock_run = mocker.patch("subprocess.run")
        mock_run.return_value = subprocess.CompletedProcess([], 0)

        # Call shell command with quiet
        instance.shell(quiet=True)

        # Verify no output
        captured = capsys.readouterr()
        assert captured.out == ""


class TestShellHelp:
    """Test shell command help output."""

    def test_shell_help(self, capsys):
        """instance shell --help should work."""
        from ots_containers.cli import app

        with pytest.raises(SystemExit) as exc_info:
            app(["instance", "shell", "--help"])
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "persistent" in captured.out.lower()
        assert "tmpfs" in captured.out.lower() or "ephemeral" in captured.out.lower()


class TestBuildSecretArgs:
    """Test build_secret_args helper function."""

    def test_build_secret_args_returns_empty_for_missing_file(self, tmp_path):
        """build_secret_args should return empty list for missing file."""
        from ots_containers.commands.instance._helpers import build_secret_args

        missing_file = tmp_path / "nonexistent"
        result = build_secret_args(missing_file)
        assert result == []

    def test_build_secret_args_returns_secret_flags(self, mocker, tmp_path):
        """build_secret_args should return --secret flags."""
        from ots_containers.commands.instance._helpers import build_secret_args
        from ots_containers.environment_file import SecretSpec

        env_file = tmp_path / "env"
        env_file.write_text("SECRET_VARIABLE_NAMES=HMAC_SECRET\n")

        mock_secrets = [
            SecretSpec(env_var_name="HMAC_SECRET", secret_name="ots_hmac_secret"),
        ]
        mocker.patch(
            "ots_containers.commands.instance._helpers.get_secrets_from_env_file",
            return_value=mock_secrets,
        )

        result = build_secret_args(env_file)
        assert result == [
            "--secret",
            "ots_hmac_secret,type=env,target=HMAC_SECRET",
        ]

    def test_build_secret_args_handles_multiple_secrets(self, mocker, tmp_path):
        """build_secret_args should handle multiple secrets."""
        from ots_containers.commands.instance._helpers import build_secret_args
        from ots_containers.environment_file import SecretSpec

        env_file = tmp_path / "env"
        env_file.write_text("SECRET_VARIABLE_NAMES=A,B,C\n")

        mock_secrets = [
            SecretSpec(env_var_name="A", secret_name="ots_a"),
            SecretSpec(env_var_name="B", secret_name="ots_b"),
            SecretSpec(env_var_name="C", secret_name="ots_c"),
        ]
        mocker.patch(
            "ots_containers.commands.instance._helpers.get_secrets_from_env_file",
            return_value=mock_secrets,
        )

        result = build_secret_args(env_file)
        assert len(result) == 6  # 3 secrets * 2 args each (--secret, value)
        assert result[0] == "--secret"
        assert "ots_a" in result[1]
        assert result[2] == "--secret"
        assert "ots_b" in result[3]
