# tests/commands/instance/test_shell.py

"""Tests for the shell command.

These tests verify the shell command builds correct podman commands
for ephemeral and persistent migration shells.
"""

import pytest

from rots.commands import instance
from rots.config import DEFAULT_IMAGE, Config

pytestmark = pytest.mark.quick


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

    mocker.patch("rots.commands.instance.app.Config", lambda: cfg)

    # Mock env file not existing by default
    env_file = config_overrides.get("env_file", tmp_path / "nonexistent")
    mocker.patch(
        "rots.commands.instance.app.quadlet.DEFAULT_ENV_FILE",
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
        "rots.commands.instance.app.dataclasses.replace",
        side_effect=tracking_replace,
    )

    return cfg, mock_executor


def _stub_run(mock_executor, host_exists=True, env_ok=False):
    """Route executor.run() by command so tests are deterministic.

    The mock executor is a plain MagicMock, so isinstance(ex, LocalExecutor)
    is False and the shell command takes the REMOTE branch: env-file existence
    uses ``test -f`` and general -v host existence uses ``test -e``. A single
    shared return_value can't serve both an existing-host mount and a missing
    env file, so route by the leading command tokens instead.
    """
    from types import SimpleNamespace

    def _run(cmd_list, *args, **kwargs):
        head = list(cmd_list[:2])
        if head == ["test", "-f"]:
            return SimpleNamespace(ok=env_ok)
        if head == ["test", "-e"]:
            return SimpleNamespace(ok=host_exists)
        return SimpleNamespace(ok=True)

    mock_executor.run.side_effect = _run


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

    def test_shell_layers_env_files_no_secret_injection(self, mocker, tmp_path):
        """shell resolves secrets from layered env files, never via --secret.

        Secrets now live in the merged base + .local env files (the single
        source of truth the quadlet loads). The boot-test shell must layer the
        same --env-file args and must NOT inject --secret from the podman store.
        """
        env_file = tmp_path / "onetimesecret"
        env_file.write_text("SECRET_VARIABLE_NAMES=AUTH_SECRET,API_KEY\n")
        local_file = tmp_path / "onetimesecret.local"
        local_file.write_text("AUTH_SECRET=from-local\n")
        mocker.patch("rots.quadlet.LOCAL_ENV_FILE", local_file)

        _mock_config, mock_executor = _setup_shell_mocks(mocker, tmp_path, env_file=env_file)

        instance.shell(quiet=True)

        cmd = _get_cmd_from_executor(mock_executor, interactive=True)
        cmd_str = " ".join(cmd)
        assert "--secret" not in cmd_str
        # Base file layered first, .local override second (later file wins).
        assert cmd.count("--env-file") == 2
        assert str(env_file) in cmd
        assert str(local_file) in cmd
        assert cmd.index(str(local_file)) > cmd.index(str(env_file))

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
        from rots.config import DEFAULT_IMAGE

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
        from rots.config import DEFAULT_IMAGE

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
        assert "podman run" in captured.err

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
        mock_config.resolve_image_tag.assert_called()

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


class TestShellSentinelRejection:
    """shell should reject unresolved sentinel tags."""

    def test_shell_rejects_at_current_sentinel(self, mocker, tmp_path, capsys):
        """shell should exit 1 when resolve_image_tag returns @current."""
        _mock_config, _mock_executor = _setup_shell_mocks(
            mocker,
            tmp_path,
            tag="@current",
            resolve_image_tag=(DEFAULT_IMAGE, "@current"),
        )

        with pytest.raises(SystemExit) as exc_info:
            instance.shell(quiet=True)

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "sentinel" in captured.err
        assert "--tag" in captured.err

    def test_shell_rejects_at_rollback_sentinel(self, mocker, tmp_path, capsys):
        """shell should exit 1 when resolve_image_tag returns @rollback."""
        _mock_config, _mock_executor = _setup_shell_mocks(
            mocker,
            tmp_path,
            tag="@rollback",
            resolve_image_tag=(DEFAULT_IMAGE, "@rollback"),
        )

        with pytest.raises(SystemExit) as exc_info:
            instance.shell(quiet=True)

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "sentinel" in captured.err


class TestShellPrivateRegistry:
    """shell should use private registry when OTS_REGISTRY is set.

    Since resolve_image_tag() now applies OTS_REGISTRY centrally,
    the mock return value includes the registry-applied image.
    """

    def test_shell_uses_private_registry(self, mocker, tmp_path):
        """shell should use registry-applied image from resolve_image_tag."""
        _mock_config, mock_executor = _setup_shell_mocks(
            mocker,
            tmp_path,
            tag="edge",
            resolve_image_tag=(
                "container-registry.infra.onetime.co/onetimesecret/onetimesecret",
                "edge",
            ),
        )

        instance.shell(quiet=True)

        cmd = _get_cmd_from_executor(mock_executor, interactive=True)
        assert "container-registry.infra.onetime.co/onetimesecret/onetimesecret:edge" in cmd

    def test_shell_no_registry_uses_default_image(self, mocker, tmp_path):
        """shell without OTS_REGISTRY should use default image path."""
        _mock_config, mock_executor = _setup_shell_mocks(
            mocker,
            tmp_path,
            tag="edge",
            resolve_image_tag=(DEFAULT_IMAGE, "edge"),
        )

        instance.shell(quiet=True)

        cmd = _get_cmd_from_executor(mock_executor, interactive=True)
        assert f"{DEFAULT_IMAGE}:edge" in cmd


class TestShellVolumes:
    """Test --data-volume and general -v/--volume handling.

    NOTE: the mock executor is a plain MagicMock, so the shell command takes
    the REMOTE branch (Path used verbatim, remote ``mkdir -p`` for
    --data-volume, remote ``test -e`` for general -v host existence).
    """

    def test_data_volume_mounts_app_data_and_mkdirs(self, mocker, tmp_path):
        """--data-volume bind-mounts at /app/data (rw,U) and mkdirs on remote."""
        _mock_config, mock_executor = _setup_shell_mocks(mocker, tmp_path)

        instance.shell(data_volume="/host/data", quiet=True)

        cmd = _get_cmd_from_executor(mock_executor, interactive=True)
        assert "/host/data:/app/data:rw,U" in cmd
        assert "--tmpfs" not in cmd
        # Remote branch creates the host directory
        mock_executor.run.assert_any_call(["mkdir", "-p", "/host/data"])

    def test_general_volume_passes_through_verbatim(self, mocker, tmp_path):
        """-v HOST:CONTAINER[:OPTS] is passed through verbatim (incl :ro)."""
        _mock_config, mock_executor = _setup_shell_mocks(mocker, tmp_path)
        _stub_run(mock_executor, host_exists=True)

        instance.shell(volume=("/host/certs:/app/etc/tls:ro",), quiet=True)

        cmd = _get_cmd_from_executor(mock_executor, interactive=True)
        assert "/host/certs:/app/etc/tls:ro" in cmd
        # No implicit U or rw added
        assert not any("/app/etc/tls:rw" in part for part in cmd)

    def test_general_volume_repeatable(self, mocker, tmp_path):
        """Repeatable -v yields one mount arg per spec."""
        _mock_config, mock_executor = _setup_shell_mocks(mocker, tmp_path)
        _stub_run(mock_executor, host_exists=True)

        instance.shell(volume=("/host/a:/app/x", "/host/b:/app/y"), quiet=True)

        cmd = _get_cmd_from_executor(mock_executor, interactive=True)
        assert "/host/a:/app/x" in cmd
        assert "/host/b:/app/y" in cmd
        # tmpfs default (no -v for data), config none => exactly two -v
        assert cmd.count("-v") == 2

    def test_general_volume_rejects_relative_target(self, mocker, tmp_path, capsys):
        """Non-absolute container target is rejected."""
        _mock_config, _mock_executor = _setup_shell_mocks(mocker, tmp_path)

        with pytest.raises(SystemExit) as exc_info:
            instance.shell(volume=("/host/certs:relative/path",), quiet=True)

        assert exc_info.value.code == 1
        assert "must be absolute" in capsys.readouterr().err

    def test_general_volume_rejects_bare_form(self, mocker, tmp_path, capsys):
        """Bare -v ./data (no colon) is rejected with a --data-volume hint."""
        _mock_config, _mock_executor = _setup_shell_mocks(mocker, tmp_path)

        with pytest.raises(SystemExit) as exc_info:
            instance.shell(volume=("./data",), quiet=True)

        assert exc_info.value.code == 1
        assert "--data-volume" in capsys.readouterr().err

    def test_general_volume_rejects_app_data_target(self, mocker, tmp_path, capsys):
        """-v targeting /app/data is rejected in favor of --data-volume."""
        _mock_config, _mock_executor = _setup_shell_mocks(mocker, tmp_path)

        with pytest.raises(SystemExit) as exc_info:
            instance.shell(volume=("/host/data:/app/data",), quiet=True)

        assert exc_info.value.code == 1
        err = capsys.readouterr().err
        assert "/app/data" in err
        assert "--data-volume" in err

    def test_general_volume_missing_host_fails_loud(self, mocker, tmp_path, capsys):
        """Missing host path fails loud (no mkdir)."""
        _mock_config, mock_executor = _setup_shell_mocks(mocker, tmp_path)
        _stub_run(mock_executor, host_exists=False)

        with pytest.raises(SystemExit) as exc_info:
            instance.shell(volume=("/host/missing:/app/x",), quiet=True)

        assert exc_info.value.code == 1
        assert "does not exist" in capsys.readouterr().err

    def test_general_volume_rejects_relative_host_on_remote(self, mocker, tmp_path, capsys):
        """Relative -v host is rejected on remote (would become a named volume)."""
        _mock_config, mock_executor = _setup_shell_mocks(mocker, tmp_path)
        _stub_run(mock_executor, host_exists=True)

        with pytest.raises(SystemExit) as exc_info:
            instance.shell(volume=("./certs:/app/etc/tls:ro",), quiet=True)

        assert exc_info.value.code == 1
        assert "must be absolute on remote" in capsys.readouterr().err

    def test_data_volume_rejects_relative_host_on_remote(self, mocker, tmp_path, capsys):
        """Relative --data-volume host is rejected on remote (no mkdir)."""
        _mock_config, mock_executor = _setup_shell_mocks(mocker, tmp_path)

        with pytest.raises(SystemExit) as exc_info:
            instance.shell(data_volume="./data", quiet=True)

        assert exc_info.value.code == 1
        assert "must be absolute on remote" in capsys.readouterr().err
        # Rejection happens before the remote mkdir side effect
        mkdir_calls = [
            c for c in mock_executor.run.call_args_list if list(c.args[0][:2]) == ["mkdir", "-p"]
        ]
        assert mkdir_calls == []

    def test_invalid_general_volume_aborts_before_data_volume_mkdir(self, mocker, tmp_path):
        """An invalid -v aborts before the --data-volume mkdir (validate-before-mutate)."""
        _mock_config, mock_executor = _setup_shell_mocks(mocker, tmp_path)

        with pytest.raises(SystemExit) as exc_info:
            instance.shell(data_volume="/host/data", volume=("/bad",), quiet=True)

        assert exc_info.value.code == 1
        # No mkdir side effect for --data-volume should have run
        mkdir_calls = [
            c for c in mock_executor.run.call_args_list if list(c.args[0][:2]) == ["mkdir", "-p"]
        ]
        assert mkdir_calls == []

    def test_persistent_and_data_volume_mutually_exclusive(self, mocker, tmp_path, capsys):
        """--persistent and --data-volume both target /app/data and cannot combine."""
        _mock_config, _mock_executor = _setup_shell_mocks(mocker, tmp_path)

        with pytest.raises(SystemExit) as exc_info:
            instance.shell(persistent="upgrade-v024", data_volume="/host/data", quiet=True)

        assert exc_info.value.code == 1
        assert "mutually exclusive" in capsys.readouterr().err

    def test_persistent_allows_general_volume(self, mocker, tmp_path):
        """--persistent is NOT mutually exclusive with the general -v flag."""
        _mock_config, mock_executor = _setup_shell_mocks(mocker, tmp_path)
        _stub_run(mock_executor, host_exists=True)

        instance.shell(persistent="upgrade-v024", volume=("/host/x:/app/x",), quiet=True)

        cmd = _get_cmd_from_executor(mock_executor, interactive=True)
        joined = " ".join(cmd)
        assert "ots-migration-upgrade-v024:/app/data" in joined
        assert "/host/x:/app/x" in joined


class TestShellHelp:
    """Test shell command help output."""

    def test_shell_help(self, capsys):
        """instance shell --help should work."""
        from rots.cli import app

        with pytest.raises(SystemExit) as exc_info:
            app(["instance", "shell", "--help"])
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        out = captured.out.lower()
        assert "persistent" in out
        assert "tmpfs" in out or "ephemeral" in out
        assert "data-volume" in out
        assert "host:container" in out


class TestBuildEnvFileArgs:
    """Test build_env_file_args helper (replaces deleted build_secret_args).

    Ad-hoc podman runs now layer the baseline env file plus an optional
    /etc/default/onetimesecret.local override as --env-file args, matching the
    quadlet's EnvironmentFile= layering. Secrets resolve from these files, so
    no --secret injection is emitted.
    """

    def test_returns_empty_when_no_files_exist(self, mocker, tmp_path):
        """No base and no .local -> no args at all."""
        from rots.commands.instance._helpers import build_env_file_args

        missing_base = tmp_path / "nonexistent"
        mocker.patch("rots.quadlet.LOCAL_ENV_FILE", tmp_path / "nonexistent.local")

        result = build_env_file_args(missing_base)
        assert result == []
        assert "--secret" not in result

    def test_emits_base_env_file_only(self, mocker, tmp_path):
        """Base present, .local absent -> single --env-file for the base."""
        from rots.commands.instance._helpers import build_env_file_args

        base = tmp_path / "onetimesecret"
        base.write_text("SECRET_VARIABLE_NAMES=AUTH_SECRET\nAUTH_SECRET=x\n")
        mocker.patch("rots.quadlet.LOCAL_ENV_FILE", tmp_path / "missing.local")

        result = build_env_file_args(base)
        assert result == ["--env-file", str(base)]
        assert "--secret" not in result

    def test_layers_base_then_local_when_local_exists(self, mocker, tmp_path):
        """Base + .local present -> both layered, base first, .local second."""
        from rots.commands.instance._helpers import build_env_file_args

        base = tmp_path / "onetimesecret"
        base.write_text("AUTH_SECRET=base\n")
        local = tmp_path / "onetimesecret.local"
        local.write_text("AUTH_SECRET=override\n")
        mocker.patch("rots.quadlet.LOCAL_ENV_FILE", local)

        result = build_env_file_args(base)
        assert result == ["--env-file", str(base), "--env-file", str(local)]
        assert "--secret" not in result

    def test_uses_executor_for_remote_existence_checks(self, mocker, tmp_path):
        """Remote runs test -f via the executor rather than touching local fs."""
        from rots.commands.instance._helpers import build_env_file_args

        base = tmp_path / "onetimesecret"
        local = tmp_path / "onetimesecret.local"
        mocker.patch("rots.quadlet.LOCAL_ENV_FILE", local)

        mock_ex = mocker.MagicMock()  # not a LocalExecutor -> _is_remote() True
        mock_ex.run.return_value.ok = True

        result = build_env_file_args(base, executor=mock_ex)
        assert result == ["--env-file", str(base), "--env-file", str(local)]
        assert "--secret" not in result
        mock_ex.run.assert_any_call(["test", "-f", str(base)])
        mock_ex.run.assert_any_call(["test", "-f", str(local)])
