# tests/commands/instance/test_shell.py
"""Tests for the shell command.

These tests verify the shell command builds correct podman commands
for ephemeral and persistent migration shells.
"""

import pytest

from ots_containers.commands import instance
from ots_containers.config import DEFAULT_IMAGE, Config


def _setup_shell_mocks(mocker, tmp_path, **config_overrides):
    """Set up standard mocks for shell tests.

    Returns (mock_config, mock_executor) so tests can inspect calls.
    """
    from unittest.mock import Mock

    image = config_overrides.get("image", DEFAULT_IMAGE)
    tag = config_overrides.get("tag", "current")

    cfg = Config(image=image, tag=tag)
    cfg.config_dir = tmp_path / "etc"
    cfg.config_dir.mkdir(exist_ok=True)
    cfg.get_existing_config_files = Mock(
        return_value=config_overrides.get("existing_config_files", [])
    )

    # Default resolve_image_tag returns (image, tag) — can be overridden
    default_resolve = (image, tag)
    cfg.resolve_image_tag = Mock(
        return_value=config_overrides.get("resolve_image_tag", default_resolve)
    )

    mocker.patch("ots_containers.commands.instance.app.Config", lambda: cfg)

    # Mock env file not existing by default
    env_file = config_overrides.get("env_file", tmp_path / "nonexistent")
    mocker.patch(
        "ots_containers.commands.instance.app.quadlet.DEFAULT_ENV_FILE",
        env_file,
    )

    # Set up executor mock — all methods return 0 (success)
    mock_executor = mocker.MagicMock()
    mock_executor.run_interactive.return_value = 0
    mock_executor.run_stream.return_value = 0

    # Mock executor.run() for "test -f" file existence checks:
    # return ok=True if the env_file was provided (exists), False otherwise
    env_exists = env_file.exists()
    mock_run_result = mocker.MagicMock()
    mock_run_result.ok = env_exists
    mock_executor.run.return_value = mock_run_result

    cfg.get_executor = Mock(return_value=mock_executor)

    # Track dataclasses.replace calls; apply kwargs to same cfg and re-attach mocks
    def tracking_replace(obj, **kwargs):
        for k, v in kwargs.items():
            object.__setattr__(obj, k, v)
        new_image = kwargs.get("image", obj.image)
        new_tag = kwargs.get("tag", obj.tag)
        obj.resolve_image_tag = Mock(return_value=(new_image, new_tag))
        obj.get_executor = Mock(return_value=mock_executor)
        obj.get_existing_config_files = Mock(
            return_value=config_overrides.get("existing_config_files", [])
        )
        return obj

    mocker.patch(
        "ots_containers.commands.instance.app.dataclasses.replace",
        side_effect=tracking_replace,
    )

    return cfg, mock_executor


def _get_cmd_from_executor(mock_executor, interactive=True):
    """Extract the command list from the executor mock's call args."""
    if interactive:
        mock_executor.run_interactive.assert_called_once()
        return mock_executor.run_interactive.call_args[0][0]
    else:
        mock_executor.run_stream.assert_called_once()
        return mock_executor.run_stream.call_args[0][0]


class TestShellCommand:
    """Test the shell command."""

    def test_shell_function_exists(self):
        """shell command should be defined."""
        assert hasattr(instance, "shell")
        assert callable(instance.shell)

    def test_shell_builds_tmpfs_command(self, mocker, tmp_path):
        """shell should use tmpfs by default."""
        _mock_config, mock_executor = _setup_shell_mocks(mocker, tmp_path)

        instance.shell(quiet=True)

        cmd = _get_cmd_from_executor(mock_executor, interactive=True)
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
        _mock_config, mock_executor = _setup_shell_mocks(mocker, tmp_path)

        instance.shell(persistent="upgrade-v024", quiet=True)

        cmd = _get_cmd_from_executor(mock_executor, interactive=True)
        assert "-v" in cmd
        v_idx = cmd.index("-v")
        volume_arg = cmd[v_idx + 1]
        assert "ots-migration-upgrade-v024:/app/data" in volume_arg
        assert "--tmpfs" not in cmd

    def test_shell_includes_secrets_from_env_file(self, mocker, tmp_path):
        """shell should include secrets when env file exists."""
        from ots_containers.environment_file import SecretSpec

        env_file = tmp_path / "onetimesecret"
        env_file.write_text("SECRET_VARIABLE_NAMES=HMAC_SECRET,API_KEY\n")

        _mock_config, mock_executor = _setup_shell_mocks(mocker, tmp_path, env_file=env_file)

        mock_secrets = [
            SecretSpec(env_var_name="HMAC_SECRET", secret_name="ots_hmac_secret"),
            SecretSpec(env_var_name="API_KEY", secret_name="ots_api_key"),
        ]
        mocker.patch(
            "ots_containers.commands.instance._helpers.get_secrets_from_env_file",
            return_value=mock_secrets,
        )

        instance.shell(quiet=True)

        cmd = _get_cmd_from_executor(mock_executor, interactive=True)
        cmd_str = " ".join(cmd)
        assert "--secret" in cmd_str
        assert "ots_hmac_secret" in cmd_str
        assert "ots_api_key" in cmd_str

    def test_shell_includes_env_file(self, mocker, tmp_path):
        """shell should include --env-file when file exists."""
        env_file = tmp_path / "onetimesecret"
        env_file.write_text("REDIS_URL=redis://localhost:6379\n")

        _mock_config, mock_executor = _setup_shell_mocks(mocker, tmp_path, env_file=env_file)

        instance.shell(quiet=True)

        cmd = _get_cmd_from_executor(mock_executor, interactive=True)
        assert "--env-file" in cmd
        env_idx = cmd.index("--env-file")
        assert str(env_file) == cmd[env_idx + 1]

    def test_shell_mounts_config_readonly(self, mocker, tmp_path):
        """shell should mount individual config files read-only."""
        config_dir = tmp_path / "etc"
        config_dir.mkdir()
        config_yaml = config_dir / "config.yaml"
        config_yaml.touch()

        _mock_config, mock_executor = _setup_shell_mocks(
            mocker, tmp_path, existing_config_files=[config_yaml]
        )

        instance.shell(quiet=True)

        cmd = _get_cmd_from_executor(mock_executor, interactive=True)
        cmd_str = " ".join(cmd)
        assert "config.yaml:/app/etc/config.yaml:ro" in cmd_str

    def test_shell_no_config_files_no_mount(self, mocker, tmp_path):
        """shell should not mount config when no config files exist."""
        _mock_config, mock_executor = _setup_shell_mocks(mocker, tmp_path)

        instance.shell(quiet=True)

        cmd = _get_cmd_from_executor(mock_executor, interactive=True)
        cmd_str = " ".join(cmd)
        assert "/app/etc" not in cmd_str

    def test_shell_runs_command_with_bash_c(self, mocker, tmp_path):
        """shell -c should run command via bash -c."""
        _mock_config, mock_executor = _setup_shell_mocks(mocker, tmp_path)

        instance.shell(command="bin/ots migrate", quiet=True)

        # Non-interactive: uses run_stream
        cmd = _get_cmd_from_executor(mock_executor, interactive=False)
        assert "/bin/bash" in cmd
        assert "-c" in cmd
        assert "bin/ots migrate" in cmd
        # Should not have -it when command is provided
        assert "-it" not in cmd

    def test_shell_uses_interactive_when_no_command(self, mocker, tmp_path):
        """shell without -c should be interactive."""
        _mock_config, mock_executor = _setup_shell_mocks(mocker, tmp_path)

        instance.shell(quiet=True)

        cmd = _get_cmd_from_executor(mock_executor, interactive=True)
        assert "-it" in cmd
        assert "-c" not in cmd

    def test_shell_uses_config_image_by_default(self, mocker, tmp_path):
        """shell should use cfg.image (from IMAGE env or DEFAULT_IMAGE)."""
        from ots_containers.config import DEFAULT_IMAGE

        _mock_config, mock_executor = _setup_shell_mocks(
            mocker,
            tmp_path,
            tag="v0.24.0",
            resolve_image_tag=(DEFAULT_IMAGE, "v0.24.0"),
        )

        instance.shell(quiet=True)

        cmd = _get_cmd_from_executor(mock_executor, interactive=True)
        assert f"{DEFAULT_IMAGE}:v0.24.0" in cmd

    def test_shell_uses_registry_image_via_config(self, mocker, tmp_path):
        """shell should use registry image when IMAGE env specifies one."""
        _mock_config, mock_executor = _setup_shell_mocks(
            mocker,
            tmp_path,
            tag="v0.24.0",
            image="ghcr.io/onetimesecret/onetimesecret",
            resolve_image_tag=("ghcr.io/onetimesecret/onetimesecret", "v0.24.0"),
        )

        instance.shell(quiet=True)

        cmd = _get_cmd_from_executor(mock_executor, interactive=True)
        assert "ghcr.io/onetimesecret/onetimesecret:v0.24.0" in cmd

    def test_shell_uses_specified_tag(self, mocker, tmp_path):
        """shell --tag should override default tag."""
        from ots_containers.config import DEFAULT_IMAGE

        _mock_config, mock_executor = _setup_shell_mocks(mocker, tmp_path)

        instance.shell(tag="test-tag-123", quiet=True)

        cmd = _get_cmd_from_executor(mock_executor, interactive=True)
        assert f"{DEFAULT_IMAGE}:test-tag-123" in cmd

    def test_shell_exits_with_command_exit_code(self, mocker, tmp_path):
        """shell should propagate exit code from command."""
        _mock_config, mock_executor = _setup_shell_mocks(mocker, tmp_path)
        mock_executor.run_interactive.return_value = 42

        with pytest.raises(SystemExit) as exc_info:
            instance.shell(quiet=True)

        assert exc_info.value.code == 42

    def test_shell_prints_command_when_not_quiet(self, mocker, tmp_path, capsys):
        """shell should print command when not quiet."""
        _mock_config, mock_executor = _setup_shell_mocks(mocker, tmp_path)

        instance.shell(quiet=False)

        captured = capsys.readouterr()
        assert "podman run" in captured.out

    def test_shell_suppresses_output_when_quiet(self, mocker, tmp_path, capsys):
        """shell --quiet should suppress output."""
        _mock_config, mock_executor = _setup_shell_mocks(mocker, tmp_path)

        instance.shell(quiet=True)

        captured = capsys.readouterr()
        assert captured.out == ""


class TestShellImageReference:
    """Test shell command image reference handling.

    Verifies that shell correctly resolves the image reference based on
    the precedence: --tag flag > TAG env > @current alias > DEFAULT_TAG.
    """

    def test_shell_default_resolution_path(self, mocker, tmp_path):
        """shell without --tag should go through resolve_image_tag()."""
        mock_config, mock_executor = _setup_shell_mocks(
            mocker,
            tmp_path,
            resolve_image_tag=("ghcr.io/onetimesecret/onetimesecret", "v0.23.0"),
        )

        instance.shell(quiet=True)

        cmd = _get_cmd_from_executor(mock_executor, interactive=True)
        assert "ghcr.io/onetimesecret/onetimesecret:v0.23.0" in cmd
        mock_config.resolve_image_tag.assert_called_once()

    def test_shell_tag_flag_bypasses_resolve(self, mocker, tmp_path):
        """shell --tag sets the tag via replace; resolve_image_tag passes it through."""

        mock_config, mock_executor = _setup_shell_mocks(mocker, tmp_path)

        instance.shell(tag="v0.24.0", quiet=True)

        cmd = _get_cmd_from_executor(mock_executor, interactive=True)
        assert f"{DEFAULT_IMAGE}:v0.24.0" in cmd

    def test_shell_image_env_override(self, mocker, tmp_path):
        """shell should use IMAGE env var via config when set."""
        _mock_config, mock_executor = _setup_shell_mocks(
            mocker,
            tmp_path,
            image="registry.example.com/custom/app",
            tag="v1.0.0",
            resolve_image_tag=("registry.example.com/custom/app", "v1.0.0"),
        )

        instance.shell(quiet=True)

        cmd = _get_cmd_from_executor(mock_executor, interactive=True)
        assert "registry.example.com/custom/app:v1.0.0" in cmd

    def test_shell_tag_flag_with_custom_image(self, mocker, tmp_path):
        """shell --tag with IMAGE env set should use custom image + flag tag."""
        _mock_config, mock_executor = _setup_shell_mocks(
            mocker,
            tmp_path,
            image="registry.example.com/custom/app",
        )

        instance.shell(tag="test-tag", quiet=True)

        cmd = _get_cmd_from_executor(mock_executor, interactive=True)
        assert "registry.example.com/custom/app:test-tag" in cmd

    def test_shell_current_alias_resolution(self, mocker, tmp_path):
        """shell should resolve @current alias to actual tag."""
        _mock_config, mock_executor = _setup_shell_mocks(
            mocker,
            tmp_path,
            tag="@current",
            resolve_image_tag=("ghcr.io/onetimesecret/onetimesecret", "v0.22.1"),
        )

        instance.shell(quiet=True)

        cmd = _get_cmd_from_executor(mock_executor, interactive=True)
        # Should use the resolved tag, not the literal "@current"
        assert "ghcr.io/onetimesecret/onetimesecret:v0.22.1" in cmd


class TestShellPositionalReference:
    """shell() accepts positional image reference."""

    def test_reference_overrides_image_and_tag(self, mocker, tmp_path):
        """shell with positional reference should override both image and tag."""
        _mock_config, mock_executor = _setup_shell_mocks(mocker, tmp_path)

        instance.shell(reference="custom/image:v2.0", quiet=True)

        cmd = _get_cmd_from_executor(mock_executor, interactive=True)
        assert "custom/image:v2.0" in cmd

    def test_reference_image_only(self, mocker, tmp_path):
        """shell with positional reference (no tag) should override image."""
        _mock_config, mock_executor = _setup_shell_mocks(mocker, tmp_path)

        instance.shell(reference="custom/image", quiet=True)

        cmd = _get_cmd_from_executor(mock_executor, interactive=True)
        assert any("custom/image" in part for part in cmd)

    def test_reference_tag_beats_flag_tag(self, mocker, tmp_path):
        """Positional ref tag takes precedence over --tag flag."""
        _mock_config, mock_executor = _setup_shell_mocks(mocker, tmp_path)

        instance.shell(reference="img:ref-tag", tag="flag-tag", quiet=True)

        cmd = _get_cmd_from_executor(mock_executor, interactive=True)
        assert "img:ref-tag" in cmd

    def test_reference_with_registry_port(self, mocker, tmp_path):
        """shell with registry:port/image:tag should parse correctly."""
        _mock_config, mock_executor = _setup_shell_mocks(mocker, tmp_path)

        instance.shell(reference="registry:5000/org/image:v1.0", quiet=True)

        cmd = _get_cmd_from_executor(mock_executor, interactive=True)
        assert "registry:5000/org/image:v1.0" in cmd


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
