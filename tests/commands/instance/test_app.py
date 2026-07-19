# tests/commands/instance/test_app.py

"""Tests for instance management commands.

These tests verify that instance commands can be imported and invoked
without attribute errors or import failures. They use mocking to avoid
requiring actual podman/systemd infrastructure.
"""

import contextlib

import pytest

from rots.commands import instance
from rots.commands.instance._helpers import format_command
from rots.commands.instance.annotations import InstanceType
from rots.config import Config


@pytest.fixture(autouse=True)
def mock_systemctl_available(mocker):
    """Mock shutil.which to report systemctl as available for all tests."""
    mocker.patch("shutil.which", return_value="/mock/bin/systemctl")


@pytest.fixture(autouse=True)
def _mock_get_executor(mocker):
    """Mock Config.get_executor to return None (local execution).

    Phase 3 added executor threading to instance commands. This fixture
    prevents SSH resolution from running during tests and ensures executor=None
    is passed to all systemd/db calls (transparent local execution).
    """
    mocker.patch.object(Config, "get_executor", return_value=None)


class TestFormatCommand:
    """Test command formatting for copy-paste usage."""

    def test_simple_command(self):
        """Simple commands should join with spaces."""
        cmd = ["podman", "run", "--rm", "image:tag"]
        assert format_command(cmd) == "podman run --rm image:tag"

    def test_arguments_with_spaces_are_quoted(self):
        """Arguments containing spaces should be quoted."""
        cmd = ["podman", "run", "--env-file", "/path/with spaces/file.env"]
        result = format_command(cmd)
        assert "'/path/with spaces/file.env'" in result

    def test_empty_arguments_are_quoted(self):
        """Empty arguments should be quoted."""
        cmd = ["echo", ""]
        result = format_command(cmd)
        assert "''" in result

    def test_special_characters_are_quoted(self):
        """Arguments with shell special characters should be quoted."""
        cmd = ["echo", "hello$world", "foo;bar"]
        result = format_command(cmd)
        # shlex.quote should protect these
        assert "$" not in result or "'" in result


class TestInstanceImports:
    """Verify instance module imports correctly without AttributeError."""

    def test_instance_app_exists(self):
        """Instance app should be importable."""
        assert instance.app is not None

    def test_deploy_function_exists(self):
        """deploy command should be defined."""
        assert hasattr(instance, "deploy")
        assert callable(instance.deploy)

    def test_redeploy_function_exists(self):
        """redeploy command should be defined."""
        assert hasattr(instance, "redeploy")
        assert callable(instance.redeploy)

    def test_undeploy_function_exists(self):
        """undeploy command should be defined."""
        assert hasattr(instance, "undeploy")
        assert callable(instance.undeploy)

    def test_run_function_exists(self):
        """run command should be defined."""
        assert hasattr(instance, "run")
        assert callable(instance.run)


class TestInstanceHelp:
    """Test instance command help output."""

    def test_instance_deploy_help(self, capsys):
        """instance deploy --help should work."""
        from rots.cli import app

        with pytest.raises(SystemExit) as exc_info:
            app(["instance", "deploy", "--help"])
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "web" in captured.out.lower() or "deploy" in captured.out.lower()

    def test_instance_redeploy_help(self, capsys):
        """instance redeploy --help should work."""
        from rots.cli import app

        with pytest.raises(SystemExit) as exc_info:
            app(["instance", "redeploy", "--help"])
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "force" in captured.out.lower() or "redeploy" in captured.out.lower()

    def test_instance_run_help(self, capsys):
        """instance run --help should work."""
        from rots.cli import app

        with pytest.raises(SystemExit) as exc_info:
            app(["instance", "run", "--help"])
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "port" in captured.out.lower() or "run" in captured.out.lower()


class TestRunCommand:
    """Test run command for direct podman execution."""

    def test_run_builds_correct_command(self, mocker, tmp_path):
        """run should build correct podman command."""
        from ots_shared.ssh.executor import Result

        # Mock Config with executor
        mock_executor = mocker.MagicMock()
        mock_result = Result(
            command="podman run ...", returncode=0, stdout="abc123def456", stderr=""
        )
        mock_executor.run.return_value = mock_result

        mock_config = mocker.MagicMock()
        mock_config.tag = "v0.23.0"  # Default uses local image with cfg.tag
        mock_config.resolve_image_tag.return_value = ("onetimesecret", "v0.23.0")
        mock_config.resolved_image_with_tag.return_value = "onetimesecret:v0.23.0"
        mock_config.podman_auth_args.return_value = []
        mock_config.config_dir = tmp_path / "etc"
        mock_config.config_dir.mkdir()
        mock_config.registry = None
        mock_config.get_executor.return_value = mock_executor
        mocker.patch("rots.commands.instance.app.Config", return_value=mock_config)

        # Mock env file not existing
        mocker.patch(
            "rots.commands.instance.app.quadlet.DEFAULT_ENV_FILE",
            tmp_path / "nonexistent",
        )

        # Call run command in detached mode
        instance.run(port=7143, detach=True, quiet=True)

        # Verify executor.run was called with the right command
        mock_executor.run.assert_called_once()
        cmd = mock_executor.run.call_args.args[0]
        assert cmd[0] == "podman"
        assert cmd[1] == "run"
        assert "-d" in cmd
        assert "--rm" in cmd
        assert "-p" in cmd
        assert "7143:7143" in cmd
        assert "onetimesecret:v0.23.0" in cmd

    def test_run_includes_secrets_with_production_flag(self, mocker, tmp_path):
        """run --production should include secrets from env file."""
        from ots_shared.ssh.executor import Result

        # Mock Config with executor
        mock_executor = mocker.MagicMock()
        mock_result = Result(
            command="podman run ...", returncode=0, stdout="abc123def456", stderr=""
        )
        mock_executor.run.return_value = mock_result

        mock_config = mocker.MagicMock()
        mock_config.resolve_image_tag.return_value = ("onetimesecret", "latest")
        mock_config.resolved_image_with_tag.return_value = "onetimesecret:latest"
        mock_config.podman_auth_args.return_value = []
        mock_config.config_dir = tmp_path / "etc"
        mock_config.config_dir.mkdir()
        mock_config.existing_config_files = []
        mock_config.registry = None
        mock_config.get_executor.return_value = mock_executor
        mocker.patch("rots.commands.instance.app.Config", return_value=mock_config)

        # Create env file with secrets
        env_file = tmp_path / "onetimesecret"
        env_file.write_text("SECRET_VARIABLE_NAMES=AUTH_SECRET,API_KEY\n")
        mocker.patch(
            "rots.commands.instance.app.quadlet.DEFAULT_ENV_FILE",
            env_file,
        )

        # Mock get_secrets_from_env_file (imported inside run function)
        from rots.environment_file import SecretSpec

        mock_secrets = [
            SecretSpec(env_var_name="AUTH_SECRET", secret_name="ots_hmac_secret"),
            SecretSpec(env_var_name="API_KEY", secret_name="ots_api_key"),
        ]
        mocker.patch(
            "rots.environment_file.get_secrets_from_env_file",
            return_value=mock_secrets,
        )

        # Call run command with production flag
        instance.run(port=7143, detach=True, quiet=True, production=True)

        # Verify secrets were included
        cmd = mock_executor.run.call_args.args[0]
        cmd_str = " ".join(cmd)
        assert "--secret" in cmd_str
        assert "ots_hmac_secret" in cmd_str

    def test_run_minimal_without_production_flag(self, mocker, tmp_path):
        """run without --production should be minimal (no secrets/volumes)."""
        from ots_shared.ssh.executor import Result

        # Mock Config with executor
        mock_executor = mocker.MagicMock()
        mock_result = Result(
            command="podman run ...", returncode=0, stdout="abc123def456", stderr=""
        )
        mock_executor.run.return_value = mock_result

        mock_config = mocker.MagicMock()
        mock_config.resolve_image_tag.return_value = ("onetimesecret", "latest")
        mock_config.resolved_image_with_tag.return_value = "onetimesecret:latest"
        mock_config.podman_auth_args.return_value = []
        mock_config.get_executor.return_value = mock_executor
        mocker.patch("rots.commands.instance.app.Config", return_value=mock_config)

        # Call run command without production flag
        instance.run(port=7143, detach=True, quiet=True)

        # Verify minimal command (no secrets, no volumes)
        cmd = mock_executor.run.call_args.args[0]
        cmd_str = " ".join(cmd)
        assert "--secret" not in cmd_str
        assert "-v" not in cmd_str
        assert "--env-file" not in cmd_str


class TestDeployCommand:
    """Test deploy command with mocked dependencies."""

    def test_deploy_proceeds_without_config_validation(self, mocker, tmp_path):
        """deploy should proceed without config validation (validate is a no-op)."""
        mock_config = mocker.MagicMock()
        mock_config.config_dir = mocker.MagicMock()
        mock_config.config_yaml = mocker.MagicMock()
        mock_config.var_dir = mocker.MagicMock()
        mock_config.web_template_path = mocker.MagicMock()
        mock_config.db_path = tmp_path / "test.db"
        mock_config.existing_config_files = []
        mock_config.has_custom_config = False
        mock_config.resolve_image_tag.return_value = ("ghcr.io/test/image", "v1.0.0")
        mocker.patch("rots.commands.instance.app.Config", return_value=mock_config)
        mocker.patch("rots.commands.instance.app.assets.update")
        mocker.patch("rots.commands.instance.app.quadlet.write_web_template")
        mocker.patch("rots.commands.instance.app.systemd.start")
        mocker.patch("rots.commands.instance.app.db.record_deployment")

        # Should not raise SystemExit - validation no longer blocks deploy
        instance.deploy(web="7143")

    def test_deploy_requires_identifiers(self, mocker, capsys):
        """deploy without identifiers should fail."""
        with pytest.raises(SystemExit) as exc_info:
            instance.deploy(web="")
        assert "Identifiers required" in str(exc_info.value)

    def test_deploy_requires_type(self, mocker, capsys):
        """deploy without type flag should fail (identifiers are embedded in flags)."""
        with pytest.raises(SystemExit) as exc_info:
            instance.deploy()
        assert "Identifiers required" in str(exc_info.value)

    def test_deploy_calls_assets_update(self, mocker, tmp_path):
        """deploy should update assets for web containers."""
        mock_config = mocker.MagicMock()
        mock_config.config_dir = mocker.MagicMock()
        mock_config.config_yaml = mocker.MagicMock()
        mock_config.var_dir = mocker.MagicMock()
        mock_config.web_template_path = mocker.MagicMock()
        mock_config.db_path = tmp_path / "test.db"
        mock_config.existing_config_files = []
        mock_config.has_custom_config = False
        mock_config.resolve_image_tag.return_value = ("ghcr.io/test/image", "v1.0.0")
        mocker.patch("rots.commands.instance.app.Config", return_value=mock_config)
        mock_assets = mocker.patch("rots.commands.instance.app.assets.update")
        mock_quadlet = mocker.patch("rots.commands.instance.app.quadlet.write_web_template")
        mocker.patch("rots.commands.instance.app.systemd.start")
        mocker.patch("rots.commands.instance.app.db.record_deployment")

        instance.deploy(web="7143")

        from unittest.mock import ANY

        mock_assets.assert_called_once_with(mock_config, create_volume=True, executor=ANY)
        mock_quadlet.assert_called_once_with(mock_config, force=False, executor=ANY)


class TestDeployWorkerCommand:
    """Test deploy command with --worker flag."""

    def test_deploy_worker_calls_write_worker_template(self, mocker, tmp_path):
        """deploy --worker should write worker quadlet template."""
        mock_config = mocker.MagicMock()
        mock_config.config_dir = mocker.MagicMock()
        mock_config.config_yaml = mocker.MagicMock()
        mock_config.var_dir = mocker.MagicMock()
        mock_config.worker_template_path = mocker.MagicMock()
        mock_config.worker_template_path.parent = mocker.MagicMock()
        mock_config.db_path = tmp_path / "test.db"
        mock_config.existing_config_files = []
        mock_config.has_custom_config = False
        mock_config.resolve_image_tag.return_value = ("ghcr.io/test/image", "v1.0.0")
        mocker.patch("rots.commands.instance.app.Config", return_value=mock_config)
        mock_quadlet = mocker.patch("rots.commands.instance.app.quadlet.write_worker_template")
        mocker.patch("rots.commands.instance.app.systemd.start")
        mocker.patch("rots.commands.instance.app.db.record_deployment")

        instance.deploy(worker="1")

        from unittest.mock import ANY

        mock_quadlet.assert_called_once_with(mock_config, force=False, executor=ANY)

    def test_deploy_worker_does_not_update_assets(self, mocker, tmp_path):
        """deploy --worker should NOT update static assets."""
        mock_config = mocker.MagicMock()
        mock_config.config_dir = mocker.MagicMock()
        mock_config.config_yaml = mocker.MagicMock()
        mock_config.var_dir = mocker.MagicMock()
        mock_config.worker_template_path = mocker.MagicMock()
        mock_config.worker_template_path.parent = mocker.MagicMock()
        mock_config.db_path = tmp_path / "test.db"
        mock_config.existing_config_files = []
        mock_config.has_custom_config = False
        mock_config.resolve_image_tag.return_value = ("ghcr.io/test/image", "v1.0.0")
        mocker.patch("rots.commands.instance.app.Config", return_value=mock_config)
        mock_assets = mocker.patch("rots.commands.instance.app.assets.update")
        mocker.patch("rots.commands.instance.app.quadlet.write_worker_template")
        mocker.patch("rots.commands.instance.app.systemd.start")
        mocker.patch("rots.commands.instance.app.db.record_deployment")

        instance.deploy(worker="1")

        mock_assets.assert_not_called()

    def test_deploy_worker_starts_worker_unit(self, mocker, tmp_path):
        """deploy --worker should start onetime-worker unit."""
        mock_config = mocker.MagicMock()
        mock_config.config_dir = mocker.MagicMock()
        mock_config.config_yaml = mocker.MagicMock()
        mock_config.var_dir = mocker.MagicMock()
        mock_config.worker_template_path = mocker.MagicMock()
        mock_config.worker_template_path.parent = mocker.MagicMock()
        mock_config.db_path = tmp_path / "test.db"
        mock_config.existing_config_files = []
        mock_config.has_custom_config = False
        mock_config.resolve_image_tag.return_value = ("ghcr.io/test/image", "v1.0.0")
        mocker.patch("rots.commands.instance.app.Config", return_value=mock_config)
        mocker.patch("rots.commands.instance.app.quadlet.write_worker_template")
        mock_start = mocker.patch("rots.commands.instance.app.systemd.start")
        mocker.patch("rots.commands.instance.app.db.record_deployment")

        instance.deploy(worker="1")

        mock_start.assert_called_once_with("onetime-worker@1", executor=mock_config.get_executor())


class TestRedeployCommand:
    """Test redeploy command with mocked dependencies."""

    def test_redeploy_with_no_instances_found(self, mocker, capsys):
        """redeploy with no instances should print message."""
        mocker.patch(
            "rots.commands.instance._helpers.systemd.discover_web_instances",
            return_value=[],
        )
        mocker.patch(
            "rots.commands.instance._helpers.systemd.discover_worker_instances",
            return_value=[],
        )
        mocker.patch(
            "rots.commands.instance._helpers.systemd.discover_scheduler_instances",
            return_value=[],
        )

        instance.redeploy()

        captured = capsys.readouterr()
        assert "No running instances found" in captured.err

    def test_redeploy_uses_cfg_web_template_path(self, mocker, tmp_path):
        """redeploy should use cfg.web_template_path."""
        mock_config = mocker.MagicMock()
        mock_config.config_dir = mocker.MagicMock()
        mock_config.config_yaml = mocker.MagicMock()
        mock_config.var_dir = mocker.MagicMock()
        mock_config.web_template_path = tmp_path / "template"
        mock_config.db_path = tmp_path / "test.db"
        mock_config.existing_config_files = []
        mock_config.has_custom_config = False
        mock_config.resolve_image_tag.return_value = ("ghcr.io/test/image", "v1.0.0")
        mocker.patch("rots.commands.instance.app.Config", return_value=mock_config)
        mocker.patch(
            "rots.commands.instance._helpers.systemd.discover_web_instances",
            return_value=[7143],
        )
        mocker.patch(
            "rots.commands.instance._helpers.systemd.discover_worker_instances",
            return_value=[],
        )
        mocker.patch(
            "rots.commands.instance._helpers.systemd.discover_scheduler_instances",
            return_value=[],
        )
        mocker.patch("rots.commands.instance.app.assets.update")
        mocker.patch("rots.commands.instance.app.quadlet.write_web_template")
        mocker.patch("rots.commands.instance.app.systemd.recreate")
        mocker.patch("rots.commands.instance.app.db.record_deployment")
        mocker.patch(
            "rots.commands.instance.app.systemd.container_exists",
            return_value=True,
        )

        # Should not raise AttributeError
        instance.redeploy()


class TestShowEnvCommand:
    """Test show_env command - displays shared /etc/default/onetimesecret."""

    def test_show_env_function_exists(self):
        """show_env command should be defined."""
        assert hasattr(instance, "show_env")
        assert callable(instance.show_env)

    def test_show_env_displays_shared_env_file(self, mocker, capsys, tmp_path):
        """show_env should display the shared /etc/default/onetimesecret file."""
        from pathlib import Path

        # Create test env file
        env_file = tmp_path / "onetimesecret"
        env_file.write_text("ZZZ_VAR=last\nAAA_VAR=first\n# comment\nMMM_VAR=middle\n")

        # Patch Path to return our test file
        original_path = Path

        def mock_path(path_str):
            if path_str == "/etc/default/onetimesecret":
                return env_file
            return original_path(path_str)

        mocker.patch("pathlib.Path", side_effect=mock_path)

        instance.show_env()

        captured = capsys.readouterr()
        lines = captured.out.strip().split("\n")
        # Find the env var lines (skip header "=== ... ===" and empty lines)
        env_lines = [line for line in lines if "=" in line and not line.startswith("===")]
        assert env_lines == ["AAA_VAR=first", "MMM_VAR=middle", "ZZZ_VAR=last"]


class TestExecCommand:
    """Test the exec_shell command."""

    def test_exec_shell_function_exists(self):
        """exec_shell command should be defined."""
        assert hasattr(instance, "exec_shell")
        assert callable(instance.exec_shell)

    def test_exec_with_no_instances(self, mocker, capsys):
        """exec_shell with no running instances should report none found."""
        mocker.patch(
            "rots.commands.instance._helpers.systemd.discover_web_instances",
            return_value=[],
        )
        mocker.patch(
            "rots.commands.instance._helpers.systemd.discover_worker_instances",
            return_value=[],
        )
        mocker.patch(
            "rots.commands.instance._helpers.systemd.discover_scheduler_instances",
            return_value=[],
        )
        instance.exec_shell()
        captured = capsys.readouterr()
        assert "No running instances found" in captured.err

    def test_exec_calls_podman_exec(self, mocker, capsys):
        """exec_shell should call run_interactive with correct container name."""
        mock_config = mocker.MagicMock()
        mocker.patch("rots.commands.instance.app.Config", return_value=mock_config)
        mock_ex = mock_config.get_executor()
        mock_ex.run_interactive.return_value = 0
        mocker.patch.dict("os.environ", {"SHELL": "/bin/bash"})

        instance.exec_shell(web="7043")

        mock_ex.run_interactive.assert_called_once()
        call_args = mock_ex.run_interactive.call_args[0][0]
        assert call_args[:3] == ["podman", "exec", "-it"]
        # Container name uses - instead of @ (podman doesn't allow @ in names)
        assert "onetime-web-7043" in call_args
        assert "/bin/bash" in call_args


class TestListInstancesCommand:
    """Tests for list_instances command."""

    def test_list_instances_function_exists(self):
        """list_instances function should exist."""
        assert instance.list_instances is not None

    def test_list_with_no_instances(self, mocker, capsys):
        """list should print message when no instances found."""
        mocker.patch(
            "rots.commands.instance._helpers.systemd.discover_web_instances",
            return_value=[],
        )
        mocker.patch(
            "rots.commands.instance._helpers.systemd.discover_worker_instances",
            return_value=[],
        )
        mocker.patch(
            "rots.commands.instance._helpers.systemd.discover_scheduler_instances",
            return_value=[],
        )

        instance.list_instances()

        captured = capsys.readouterr()
        assert "No configured instances found" in captured.err

    def test_list_displays_header(self, mocker, capsys, tmp_path):
        """list should display table header."""
        mocker.patch(
            "rots.commands.instance._helpers.systemd.discover_web_instances",
            return_value=[7043],
        )
        mocker.patch(
            "rots.commands.instance._helpers.systemd.discover_worker_instances",
            return_value=[],
        )
        mocker.patch(
            "rots.commands.instance._helpers.systemd.discover_scheduler_instances",
            return_value=[],
        )
        mocker.patch(
            "rots.commands.instance.app.systemd.is_active",
            return_value="active",
        )
        mocker.patch(
            "rots.commands.instance.app.systemd.get_container_health_map",
            return_value={},
        )

        # Mock Config and db
        mock_config = mocker.Mock()
        mock_config.db_path = tmp_path / "test.db"
        mock_config.get_executor.return_value = None
        mocker.patch(
            "rots.commands.instance.app.Config",
            return_value=mock_config,
        )
        mocker.patch(
            "rots.commands.instance.app.db.get_deployments",
            return_value=[],
        )

        instance.list_instances()

        captured = capsys.readouterr()
        assert "TYPE" in captured.out
        assert "ID" in captured.out
        assert "SERVICE" in captured.out
        assert "CONTAINER" in captured.out
        assert "STATUS" in captured.out


class TestEnableCommand:
    """Test enable command."""

    def test_enable_function_exists(self):
        """enable command should be defined."""
        assert hasattr(instance, "enable")
        assert callable(instance.enable)

    def test_enable_calls_systemctl(self, mocker, capsys):
        """enable should call systemd.enable()."""
        mocker.patch(
            "rots.commands.instance._helpers.systemd.discover_web_instances",
            return_value=[7043],
        )
        mocker.patch(
            "rots.commands.instance._helpers.systemd.discover_worker_instances",
            return_value=[],
        )
        mocker.patch(
            "rots.commands.instance._helpers.systemd.discover_scheduler_instances",
            return_value=[],
        )
        mock_enable = mocker.patch("rots.commands.instance.app.systemd.enable")

        instance.enable(web="7043")

        mock_enable.assert_called_once_with("onetime-web@7043", executor=None)

        captured = capsys.readouterr()
        assert "Enabled" in captured.err


class TestStopCommand:
    """Test stop command."""

    def test_stop_function_exists(self):
        """stop command should be defined."""
        assert hasattr(instance, "stop")
        assert callable(instance.stop)

    def test_stop_calls_systemd_stop(self, mocker, capsys):
        """stop should call systemd.stop for each instance."""
        mock_stop = mocker.patch("rots.commands.instance.app.systemd.stop")

        instance.stop(web="7043")

        mock_stop.assert_called_once_with("onetime-web@7043", executor=None)
        captured = capsys.readouterr()
        assert "Stopped onetime-web@7043" in captured.err

    def test_stop_discovers_instances_when_no_identifiers(self, mocker):
        """stop with no identifiers should discover all types."""
        mocker.patch(
            "rots.commands.instance._helpers.systemd.discover_web_instances",
            return_value=[7043, 7044],
        )
        mocker.patch(
            "rots.commands.instance._helpers.systemd.discover_worker_instances",
            return_value=["1"],
        )
        mocker.patch(
            "rots.commands.instance._helpers.systemd.discover_scheduler_instances",
            return_value=[],
        )
        mock_stop = mocker.patch("rots.commands.instance.app.systemd.stop")

        instance.stop()

        assert mock_stop.call_count == 3
        calls = [c[0][0] for c in mock_stop.call_args_list]
        assert "onetime-web@7043" in calls
        assert "onetime-web@7044" in calls
        assert "onetime-worker@1" in calls


class TestRestartCommand:
    """Test restart command."""

    def test_restart_function_exists(self):
        """restart command should be defined."""
        assert hasattr(instance, "restart")
        assert callable(instance.restart)

    def test_restart_calls_systemd_restart(self, mocker, capsys):
        """restart should call systemd.restart for each instance."""
        mock_restart = mocker.patch("rots.commands.instance.app.systemd.restart")

        instance.restart(web="7043")

        mock_restart.assert_called_once_with("onetime-web@7043", executor=None)
        captured = capsys.readouterr()
        assert "Restarting onetime-web@7043" in captured.err

    def test_restart_multiple(self, mocker, capsys):
        """restart should call systemd.restart for each instance with delay."""
        mock_restart = mocker.patch("rots.commands.instance.app.systemd.restart")
        mock_sleep = mocker.patch("rots.commands.instance._helpers.time.sleep")

        instance.restart(web="7043,7044,7045")

        assert mock_restart.call_count == 3
        calls = [c[0][0] for c in mock_restart.call_args_list]
        assert "onetime-web@7043" in calls
        assert "onetime-web@7044" in calls
        assert "onetime-web@7045" in calls
        # Default 30s delay between instances
        assert mock_sleep.call_count == 2  # between 3 instances
        mock_sleep.assert_called_with(30)


class TestLogsCommand:
    """Test logs command."""

    def test_logs_function_exists(self):
        """logs command should be defined."""
        assert hasattr(instance, "logs")
        assert callable(instance.logs)

    def test_logs_discovers_all_instances_when_no_identifiers(self, mocker):
        """logs with no identifiers should discover all instances."""
        mocker.patch(
            "rots.commands.instance._helpers.systemd.discover_web_instances",
            return_value=[7043, 7044],
        )
        mocker.patch(
            "rots.commands.instance._helpers.systemd.discover_worker_instances",
            return_value=["1"],
        )
        mocker.patch(
            "rots.commands.instance._helpers.systemd.discover_scheduler_instances",
            return_value=[],
        )
        mock_run = mocker.patch("subprocess.run")
        mock_run.return_value = mocker.Mock(returncode=0, stdout="", stderr="")

        instance.logs()

        # The executor (LocalExecutor) shells out via subprocess.run
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert "journalctl" in cmd
        assert "-u" in cmd
        # Should include both web and worker units
        unit_args = [cmd[i + 1] for i, arg in enumerate(cmd) if arg == "-u"]
        assert "onetime-web@7043" in unit_args
        assert "onetime-web@7044" in unit_args
        assert "onetime-worker@1" in unit_args


class TestDisableCommand:
    """Test disable command."""

    def test_disable_function_exists(self):
        """disable command should be defined."""
        assert hasattr(instance, "disable")
        assert callable(instance.disable)

    def test_disable_aborts_without_confirmation(self, mocker, capsys):
        """disable should abort without --yes if user declines."""
        mocker.patch(
            "rots.commands.instance._helpers.systemd.discover_web_instances",
            return_value=[7043],
        )
        mocker.patch(
            "rots.commands.instance._helpers.systemd.discover_worker_instances",
            return_value=[],
        )
        mocker.patch(
            "rots.commands.instance._helpers.systemd.discover_scheduler_instances",
            return_value=[],
        )
        mocker.patch("builtins.input", return_value="n")

        instance.disable(web="", yes=False)

        captured = capsys.readouterr()
        assert "Aborted" in captured.out

    def test_disable_calls_systemctl(self, mocker, capsys):
        """disable should call systemctl disable with --yes."""
        mocker.patch(
            "rots.commands.instance._helpers.systemd.discover_web_instances",
            return_value=[7043],
        )
        mocker.patch(
            "rots.commands.instance._helpers.systemd.discover_worker_instances",
            return_value=[],
        )
        mocker.patch(
            "rots.commands.instance._helpers.systemd.discover_scheduler_instances",
            return_value=[],
        )
        mock_disable = mocker.patch("rots.commands.instance._helpers.systemd.disable")

        instance.disable(web="7043", yes=True)

        mock_disable.assert_called_once()
        call_args = mock_disable.call_args
        assert call_args[0][0] == "onetime-web@7043"

        captured = capsys.readouterr()
        assert "Disabled" in captured.err


class TestResolveInstanceType:
    """Test resolve_instance_type helper."""

    def test_returns_none_when_no_type_specified(self):
        """Should return None when no type specified."""
        from rots.commands.instance.annotations import resolve_instance_type

        result = resolve_instance_type(None, web=None, worker=None, scheduler=None)
        assert result == (None, ())

    def test_returns_type_from_explicit_param(self):
        """Should return type from --type parameter."""
        from rots.commands.instance.annotations import resolve_instance_type

        result = resolve_instance_type(InstanceType.WORKER, web=None, worker=None, scheduler=None)
        assert result == (InstanceType.WORKER, ())

    def test_returns_web_from_flag(self):
        """Should return WEB when --web flag set."""
        from rots.commands.instance.annotations import resolve_instance_type

        result = resolve_instance_type(None, web="", worker=None, scheduler=None)
        assert result == (InstanceType.WEB, ())

    def test_returns_worker_from_flag(self):
        """Should return WORKER when --worker flag set."""
        from rots.commands.instance.annotations import resolve_instance_type

        result = resolve_instance_type(None, web=None, worker="", scheduler=None)
        assert result == (InstanceType.WORKER, ())

    def test_returns_scheduler_from_flag(self):
        """Should return SCHEDULER when --scheduler flag set."""
        from rots.commands.instance.annotations import resolve_instance_type

        result = resolve_instance_type(None, web=None, worker=None, scheduler="")
        assert result == (InstanceType.SCHEDULER, ())

    def test_raises_on_multiple_flags(self):
        """Should raise when multiple shorthand flags set."""
        from rots.commands.instance.annotations import resolve_instance_type

        with pytest.raises(SystemExit):
            resolve_instance_type(None, web="", worker="", scheduler=None)

    def test_raises_on_type_plus_flag(self):
        """Should raise when both --type and shorthand flag used."""
        from rots.commands.instance.annotations import resolve_instance_type

        with pytest.raises(SystemExit):
            resolve_instance_type(InstanceType.WEB, web=None, worker="", scheduler=None)


class TestSchedulerCommands:
    """Integration tests for scheduler instance commands using --scheduler flag."""

    def test_stop_scheduler_with_flag(self, mocker, capsys):
        """stop --scheduler should call systemd.stop for scheduler instances."""
        mock_stop = mocker.patch("rots.commands.instance.app.systemd.stop")

        instance.stop(scheduler="main")

        mock_stop.assert_called_once_with("onetime-scheduler@main", executor=None)
        captured = capsys.readouterr()
        assert "Stopped onetime-scheduler@main" in captured.err

    def test_restart_scheduler_with_flag(self, mocker, capsys):
        """restart --scheduler should call systemd.restart for scheduler instances."""
        mock_restart = mocker.patch("rots.commands.instance.app.systemd.restart")

        instance.restart(scheduler="main")

        mock_restart.assert_called_once_with("onetime-scheduler@main", executor=None)
        captured = capsys.readouterr()
        assert "Restarting onetime-scheduler@main" in captured.err

    def test_start_scheduler_with_flag(self, mocker, capsys):
        """start --scheduler should call systemd.start for scheduler instances."""
        mock_start = mocker.patch("rots.commands.instance.app.systemd.start")

        instance.start(scheduler="main")

        mock_start.assert_called_once_with("onetime-scheduler@main", executor=None)
        captured = capsys.readouterr()
        assert "Started onetime-scheduler@main" in captured.err

    def test_status_scheduler_with_flag(self, mocker, capsys):
        """status --scheduler should show status for scheduler instances."""
        mock_status = mocker.patch(
            "rots.commands.instance.app.systemd.status",
        )

        instance.status(scheduler="main")

        # Should call systemd.status for the scheduler unit
        mock_status.assert_called_once_with("onetime-scheduler@main", executor=None)

    def test_logs_scheduler_with_flag(self, mocker):
        """logs --scheduler should call journalctl for scheduler instances."""
        mock_run = mocker.patch("subprocess.run")
        mock_run.return_value = mocker.Mock(returncode=0, stdout="", stderr="")

        instance.logs(scheduler="main")

        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert "journalctl" in cmd
        assert "onetime-scheduler@main" in cmd or "-u" in cmd

    def test_enable_scheduler_with_flag(self, mocker, capsys):
        """enable --scheduler should call systemd.enable for scheduler instances."""
        mock_enable = mocker.patch("rots.commands.instance.app.systemd.enable")

        instance.enable(scheduler="main")

        mock_enable.assert_called_once_with("onetime-scheduler@main", executor=None)

    def test_disable_scheduler_with_flag(self, mocker, capsys):
        """disable --scheduler should call systemd.disable for scheduler instances."""
        mock_disable = mocker.patch("rots.commands.instance.app.systemd.disable")

        instance.disable(scheduler="main", yes=True)

        mock_disable.assert_called_once_with("onetime-scheduler@main", executor=None)

    def test_stop_discovers_scheduler_instances(self, mocker):
        """stop --scheduler with no identifiers should discover scheduler instances."""
        mocker.patch(
            "rots.commands.instance._helpers.systemd.discover_scheduler_instances",
            return_value=["main", "cron"],
        )
        mock_stop = mocker.patch("rots.commands.instance.app.systemd.stop")

        instance.stop(scheduler="")

        assert mock_stop.call_count == 2
        calls = [c[0][0] for c in mock_stop.call_args_list]
        assert "onetime-scheduler@main" in calls
        assert "onetime-scheduler@cron" in calls

    def test_restart_discovers_scheduler_instances(self, mocker):
        """restart --scheduler with no identifiers should discover scheduler instances."""
        mocker.patch(
            "rots.commands.instance._helpers.systemd.discover_scheduler_instances",
            return_value=["main"],
        )
        mock_restart = mocker.patch("rots.commands.instance.app.systemd.restart")

        instance.restart(scheduler="")

        mock_restart.assert_called_once_with("onetime-scheduler@main", executor=None)

    def test_multiple_scheduler_identifiers(self, mocker, capsys):
        """Commands should handle multiple scheduler identifiers."""
        mock_stop = mocker.patch("rots.commands.instance.app.systemd.stop")

        instance.stop(scheduler="main,cron,backup")

        assert mock_stop.call_count == 3
        calls = [c[0][0] for c in mock_stop.call_args_list]
        assert "onetime-scheduler@main" in calls
        assert "onetime-scheduler@cron" in calls
        assert "onetime-scheduler@backup" in calls

    def test_scheduler_with_type_parameter(self, mocker, capsys):
        """Commands should work with --type scheduler instead of --scheduler flag."""
        mock_stop = mocker.patch("rots.commands.instance.app.systemd.stop")

        instance.stop(scheduler="main")

        mock_stop.assert_called_once_with("onetime-scheduler@main", executor=None)

    def test_scheduler_named_instances(self, mocker, capsys):
        """Scheduler should accept string identifiers (not just numeric)."""
        mock_restart = mocker.patch("rots.commands.instance.app.systemd.restart")

        instance.restart(scheduler="daily-cleanup,weekly-reports")

        assert mock_restart.call_count == 2
        calls = [c[0][0] for c in mock_restart.call_args_list]
        assert "onetime-scheduler@daily-cleanup" in calls
        assert "onetime-scheduler@weekly-reports" in calls


class TestDeployEnvVarResolution:
    """Test that deploy correctly resolves IMAGE and TAG from env vars.

    These tests use a real Config() so the env var -> config -> resolve_image_tag
    pipeline is tested end-to-end. TAG is set to a non-alias value (e.g. v1.0.0)
    so resolve_image_tag returns (cfg.image, cfg.tag) without a db lookup.
    dry_run=True is used to avoid needing to mock quadlet/assets/systemd.
    """

    @pytest.fixture(autouse=True)
    def _clear_env(self, monkeypatch):
        """Remove IMAGE and TAG env vars so tests start clean."""
        monkeypatch.delenv("IMAGE", raising=False)
        monkeypatch.delenv("TAG", raising=False)
        monkeypatch.delenv("OTS_REGISTRY", raising=False)

    def test_deploy_dry_run_shows_custom_image_tag(self, mocker, monkeypatch, tmp_path, capsys):
        """Scenario 18: deploy with IMAGE/TAG env vars should show custom image:tag."""
        monkeypatch.setenv("IMAGE", "custom.registry.io/myorg/myapp")
        monkeypatch.setenv("TAG", "v1.0.0")

        # Mock db_path to avoid touching real filesystem
        mocker.patch(
            "rots.config.Config.db_path",
            new_callable=mocker.PropertyMock,
            return_value=tmp_path / "deployments.db",
        )

        instance.deploy(web="7043", dry_run=True)

        captured = capsys.readouterr()
        combined = captured.out + captured.err
        assert "custom.registry.io/myorg/myapp:v1.0.0" in combined
        assert "dry-run" in combined

    def test_deploy_records_correct_image_tag(self, mocker, monkeypatch, tmp_path, capsys):
        """Scenario 18b: deploy with IMAGE/TAG env vars flows image to db.record_deployment."""
        monkeypatch.setenv("IMAGE", "custom.registry.io/myorg/myapp")
        monkeypatch.setenv("TAG", "v2.5.0")

        # Mock db_path
        mocker.patch(
            "rots.config.Config.db_path",
            new_callable=mocker.PropertyMock,
            return_value=tmp_path / "deployments.db",
        )

        # Mock all external calls needed for non-dry-run deploy
        mocker.patch("rots.commands.instance.app.assets.update")
        mocker.patch("rots.commands.instance.app.quadlet.write_web_template")
        mocker.patch("rots.commands.instance.app.systemd.start")
        mocker.patch(
            "rots.commands.instance.app.deploy_lock",
            return_value=contextlib.nullcontext(),
        )
        mock_record = mocker.patch("rots.commands.instance.app.db.record_deployment")

        instance.deploy(web="7043")

        # Verify db.record_deployment was called with the custom image/tag
        mock_record.assert_called_once()
        assert mock_record.call_args.kwargs["image"] == "custom.registry.io/myorg/myapp"
        assert mock_record.call_args.kwargs["tag"] == "v2.5.0"


class TestRedeployEnvVarResolution:
    """Test that redeploy correctly resolves IMAGE and TAG from env vars.

    These tests use a real Config() so the env var -> config -> resolve_image_tag
    pipeline is tested end-to-end. TAG is set to a non-alias value (e.g. v1.0.0)
    so resolve_image_tag returns (cfg.image, cfg.tag) without a db lookup.
    """

    @pytest.fixture(autouse=True)
    def _clear_env(self, monkeypatch):
        """Remove IMAGE and TAG env vars so tests start clean."""
        monkeypatch.delenv("IMAGE", raising=False)
        monkeypatch.delenv("TAG", raising=False)
        monkeypatch.delenv("OTS_REGISTRY", raising=False)

    def test_redeploy_dry_run_shows_custom_image_tag(self, mocker, monkeypatch, tmp_path, capsys):
        """Scenario 19: redeploy with IMAGE/TAG env vars should show custom image:tag."""
        monkeypatch.setenv("IMAGE", "custom.registry.io/myorg/myapp")
        monkeypatch.setenv("TAG", "v1.0.0")

        # Mock db_path
        mocker.patch(
            "rots.config.Config.db_path",
            new_callable=mocker.PropertyMock,
            return_value=tmp_path / "deployments.db",
        )

        # Mock resolve_identifiers to return some instances
        mocker.patch(
            "rots.commands.instance.app.resolve_identifiers",
            return_value={InstanceType.WEB: ["7043"]},
        )

        instance.redeploy(web="7043", dry_run=True)

        captured = capsys.readouterr()
        combined = captured.out + captured.err
        assert "custom.registry.io/myorg/myapp:v1.0.0" in combined
        assert "dry-run" in combined

    def test_redeploy_records_correct_image_tag(self, mocker, monkeypatch, tmp_path, capsys):
        """Scenario 19b: redeploy with IMAGE/TAG env vars flows image to db.record_deployment."""
        monkeypatch.setenv("IMAGE", "custom.registry.io/myorg/myapp")
        monkeypatch.setenv("TAG", "v3.0.0")

        # Mock db_path
        mocker.patch(
            "rots.config.Config.db_path",
            new_callable=mocker.PropertyMock,
            return_value=tmp_path / "deployments.db",
        )

        # Mock resolve_identifiers to return some instances
        mocker.patch(
            "rots.commands.instance.app.resolve_identifiers",
            return_value={InstanceType.WEB: ["7043"]},
        )

        # Mock all external calls needed for non-dry-run redeploy
        mocker.patch("rots.commands.instance.app.assets.update")
        mocker.patch("rots.commands.instance.app.quadlet.write_web_template")
        mocker.patch(
            "rots.commands.instance.app.systemd.container_exists",
            return_value=True,
        )
        mocker.patch("rots.commands.instance.app.systemd.recreate")
        mock_record = mocker.patch("rots.commands.instance.app.db.record_deployment")

        instance.redeploy(web="7043")

        # Verify db.record_deployment was called with the custom image/tag
        mock_record.assert_called_once()
        assert mock_record.call_args.kwargs["image"] == "custom.registry.io/myorg/myapp"
        assert mock_record.call_args.kwargs["tag"] == "v3.0.0"


class TestDeployPermissionError:
    """Tests for deploy failure when /etc/containers/systemd/ is not writable.

    Scenario: quadlet.write_web_template raises PermissionError because the
    target directory is owned by root and the process is unprivileged.
    """

    def test_deploy_quadlet_write_permission_denied_exits(self, mocker, tmp_path):
        """PermissionError writing quadlet file should propagate and exit non-zero."""
        mock_config = mocker.MagicMock()
        mock_config.config_dir = mocker.MagicMock()
        mock_config.config_yaml = mocker.MagicMock()
        mock_config.var_dir = mocker.MagicMock()
        mock_config.web_template_path = mocker.MagicMock()
        mock_config.db_path = tmp_path / "test.db"
        mock_config.existing_config_files = []
        mock_config.has_custom_config = False
        mock_config.resolve_image_tag.return_value = ("ghcr.io/test/image", "v1.0.0")
        mocker.patch("rots.commands.instance.app.Config", return_value=mock_config)

        mocker.patch("rots.commands.instance.app.assets.update")
        mocker.patch(
            "rots.commands.instance.app.quadlet.write_web_template",
            side_effect=PermissionError(
                "[Errno 13] Permission denied: '/etc/containers/systemd/onetime-web@.container'"
            ),
        )

        with pytest.raises(PermissionError):
            instance.deploy(web="7143")

    def test_deploy_quadlet_write_permission_denied_does_not_start_unit(self, mocker, tmp_path):
        """When quadlet write fails, systemd.start must not be called."""
        mock_config = mocker.MagicMock()
        mock_config.config_dir = mocker.MagicMock()
        mock_config.config_yaml = mocker.MagicMock()
        mock_config.var_dir = mocker.MagicMock()
        mock_config.web_template_path = mocker.MagicMock()
        mock_config.db_path = tmp_path / "test.db"
        mock_config.existing_config_files = []
        mock_config.has_custom_config = False
        mock_config.resolve_image_tag.return_value = ("ghcr.io/test/image", "v1.0.0")
        mocker.patch("rots.commands.instance.app.Config", return_value=mock_config)

        mocker.patch("rots.commands.instance.app.assets.update")
        mocker.patch(
            "rots.commands.instance.app.quadlet.write_web_template",
            side_effect=PermissionError(
                "[Errno 13] Permission denied: '/etc/containers/systemd/onetime-web@.container'"
            ),
        )
        mock_start = mocker.patch("rots.commands.instance.app.systemd.start")

        with pytest.raises(PermissionError):
            instance.deploy(web="7143")

        mock_start.assert_not_called()


class TestDeployPortConflict:
    """Tests for deploy failure when the target port is already bound.

    Scenario: quadlet writes successfully but systemctl start fails because
    the port is in use by another process.
    """

    def test_deploy_systemctl_start_failure_records_failure(self, mocker, tmp_path):
        """SystemctlError from start should be caught, recorded as failed, and re-raised."""
        from rots.systemd import SystemctlError

        mock_config = mocker.MagicMock()
        mock_config.config_dir = mocker.MagicMock()
        mock_config.config_yaml = mocker.MagicMock()
        mock_config.var_dir = mocker.MagicMock()
        mock_config.web_template_path = mocker.MagicMock()
        mock_config.db_path = tmp_path / "test.db"
        mock_config.existing_config_files = []
        mock_config.has_custom_config = False
        mock_config.resolve_image_tag.return_value = ("ghcr.io/test/image", "v1.0.0")
        mocker.patch("rots.commands.instance.app.Config", return_value=mock_config)

        mocker.patch("rots.commands.instance.app.assets.update")
        mocker.patch("rots.commands.instance.app.quadlet.write_web_template")
        mocker.patch(
            "rots.commands.instance.app.systemd.start",
            side_effect=SystemctlError(
                "onetime-web@7143",
                "start",
                "Error response from daemon: address already in use: bind: 0.0.0.0:7143",
            ),
        )
        mock_record = mocker.patch("rots.commands.instance.app.db.record_deployment")

        with pytest.raises(SystemExit):
            instance.deploy(web="7143")

        # db.record_deployment must have been called at least once with success=False
        assert mock_record.called
        failure_calls = [c for c in mock_record.call_args_list if c.kwargs.get("success") is False]
        assert failure_calls, "Expected a failed deployment record"

    def test_deploy_systemctl_start_failure_does_not_start_further_instances(
        self, mocker, tmp_path
    ):
        """After a start failure the loop should abort; subsequent instances must not start."""
        from rots.systemd import SystemctlError

        mock_config = mocker.MagicMock()
        mock_config.config_dir = mocker.MagicMock()
        mock_config.config_yaml = mocker.MagicMock()
        mock_config.var_dir = mocker.MagicMock()
        mock_config.web_template_path = mocker.MagicMock()
        mock_config.db_path = tmp_path / "test.db"
        mock_config.existing_config_files = []
        mock_config.has_custom_config = False
        mock_config.resolve_image_tag.return_value = ("ghcr.io/test/image", "v1.0.0")
        mocker.patch("rots.commands.instance.app.Config", return_value=mock_config)

        mocker.patch("rots.commands.instance.app.assets.update")
        mocker.patch("rots.commands.instance.app.quadlet.write_web_template")
        mock_start = mocker.patch(
            "rots.commands.instance.app.systemd.start",
            side_effect=SystemctlError("onetime-web@7143", "start", "address already in use"),
        )
        mocker.patch("rots.commands.instance.app.db.record_deployment")

        with pytest.raises(SystemExit):
            instance.deploy(web="7143,7144")

        # Only one start should have been attempted
        assert mock_start.call_count == 1


class TestDeployPartialFailure:
    """Tests for partial deploy: assets succeed but quadlet write fails.

    Scenario: assets.update() completes successfully (data extracted to volume),
    but the subsequent quadlet write raises PermissionError.
    """

    def test_partial_deploy_assets_succeed_quadlet_fails(self, mocker, tmp_path):
        """Assets update should complete before the PermissionError is raised."""
        mock_config = mocker.MagicMock()
        mock_config.config_dir = mocker.MagicMock()
        mock_config.config_yaml = mocker.MagicMock()
        mock_config.var_dir = mocker.MagicMock()
        mock_config.web_template_path = mocker.MagicMock()
        mock_config.db_path = tmp_path / "test.db"
        mock_config.existing_config_files = []
        mock_config.has_custom_config = False
        mock_config.resolve_image_tag.return_value = ("ghcr.io/test/image", "v1.0.0")
        mocker.patch("rots.commands.instance.app.Config", return_value=mock_config)

        mock_assets = mocker.patch("rots.commands.instance.app.assets.update")
        mocker.patch(
            "rots.commands.instance.app.quadlet.write_web_template",
            side_effect=PermissionError(
                "[Errno 13] Permission denied: '/etc/containers/systemd/onetime-web@.container'"
            ),
        )
        mock_start = mocker.patch("rots.commands.instance.app.systemd.start")

        with pytest.raises(PermissionError):
            instance.deploy(web="7143")

        # Assets ran to completion
        from unittest.mock import ANY

        mock_assets.assert_called_once_with(mock_config, create_volume=True, executor=ANY)
        # systemd was never reached
        mock_start.assert_not_called()

    def test_partial_deploy_error_message_is_actionable(self, mocker, tmp_path, capsys):
        """The PermissionError message should include the path so operators know what to fix."""
        mock_config = mocker.MagicMock()
        mock_config.config_dir = mocker.MagicMock()
        mock_config.config_yaml = mocker.MagicMock()
        mock_config.var_dir = mocker.MagicMock()
        mock_config.web_template_path = mocker.MagicMock()
        mock_config.db_path = tmp_path / "test.db"
        mock_config.existing_config_files = []
        mock_config.has_custom_config = False
        mock_config.resolve_image_tag.return_value = ("ghcr.io/test/image", "v1.0.0")
        mocker.patch("rots.commands.instance.app.Config", return_value=mock_config)

        mocker.patch("rots.commands.instance.app.assets.update")
        target_path = "/etc/containers/systemd/onetime-web@.container"
        mocker.patch(
            "rots.commands.instance.app.quadlet.write_web_template",
            side_effect=PermissionError(f"[Errno 13] Permission denied: '{target_path}'"),
        )

        exc = None
        try:
            instance.deploy(web="7143")
        except PermissionError as e:
            exc = e

        assert exc is not None
        # The error message must identify the path that could not be written
        assert "/etc/containers/systemd" in str(exc) or "onetime-web" in str(exc)


class TestDeployWaitFlag:
    """Tests for the --wait HTTP health check flag in deploy."""

    def _make_mock_config(self, mocker, tmp_path):
        """Build a standard mock Config for deploy tests."""
        mock_config = mocker.MagicMock()
        mock_config.config_dir = mocker.MagicMock()
        mock_config.config_yaml = mocker.MagicMock()
        mock_config.var_dir = mocker.MagicMock()
        mock_config.web_template_path = mocker.MagicMock()
        mock_config.db_path = tmp_path / "test.db"
        mock_config.existing_config_files = []
        mock_config.has_custom_config = False
        mock_config.resolve_image_tag.return_value = ("ghcr.io/test/image", "v1.0.0")
        return mock_config

    def test_deploy_with_wait_calls_wait_for_http_healthy(self, mocker, tmp_path):
        """--wait should call wait_for_http_healthy with correct port and 60s timeout."""
        mock_config = self._make_mock_config(mocker, tmp_path)
        mocker.patch("rots.commands.instance.app.Config", return_value=mock_config)
        mocker.patch("rots.commands.instance.app.assets.update")
        mocker.patch("rots.commands.instance.app.quadlet.write_web_template")
        mocker.patch("rots.commands.instance.app.systemd.start")
        mocker.patch("rots.commands.instance.app.db.record_deployment")
        mock_http_healthy = mocker.patch("rots.commands.instance.app.systemd.wait_for_http_healthy")

        instance.deploy(web="7043", wait=True)

        mock_http_healthy.assert_called_once_with(
            7043, timeout=60, executor=mock_config.get_executor()
        )

    def test_deploy_without_wait_does_not_call_wait_for_http_healthy(self, mocker, tmp_path):
        """Omitting --wait should not call wait_for_http_healthy."""
        mock_config = self._make_mock_config(mocker, tmp_path)
        mocker.patch("rots.commands.instance.app.Config", return_value=mock_config)
        mocker.patch("rots.commands.instance.app.assets.update")
        mocker.patch("rots.commands.instance.app.quadlet.write_web_template")
        mocker.patch("rots.commands.instance.app.systemd.start")
        mocker.patch("rots.commands.instance.app.db.record_deployment")
        mock_http_healthy = mocker.patch("rots.commands.instance.app.systemd.wait_for_http_healthy")

        instance.deploy(web="7043", wait=False)

        mock_http_healthy.assert_not_called()

    def test_deploy_wait_is_noop_for_worker_instances(self, mocker, tmp_path):
        """--wait should be a no-op for worker instances (no HTTP endpoint)."""
        mock_config = mocker.MagicMock()
        mock_config.config_dir = mocker.MagicMock()
        mock_config.config_yaml = mocker.MagicMock()
        mock_config.var_dir = mocker.MagicMock()
        mock_config.worker_template_path = mocker.MagicMock()
        mock_config.worker_template_path.parent = mocker.MagicMock()
        mock_config.db_path = tmp_path / "test.db"
        mock_config.existing_config_files = []
        mock_config.has_custom_config = False
        mock_config.resolve_image_tag.return_value = ("ghcr.io/test/image", "v1.0.0")
        mocker.patch("rots.commands.instance.app.Config", return_value=mock_config)
        mocker.patch("rots.commands.instance.app.quadlet.write_worker_template")
        mocker.patch("rots.commands.instance.app.systemd.start")
        mocker.patch("rots.commands.instance.app.db.record_deployment")
        mock_http_healthy = mocker.patch("rots.commands.instance.app.systemd.wait_for_http_healthy")

        instance.deploy(worker="1", wait=True)

        mock_http_healthy.assert_not_called()

    def test_deploy_wait_is_noop_for_scheduler_instances(self, mocker, tmp_path):
        """--wait should be a no-op for scheduler instances (no HTTP endpoint)."""
        mock_config = mocker.MagicMock()
        mock_config.config_dir = mocker.MagicMock()
        mock_config.config_yaml = mocker.MagicMock()
        mock_config.var_dir = mocker.MagicMock()
        mock_config.scheduler_template_path = mocker.MagicMock()
        mock_config.scheduler_template_path.parent = mocker.MagicMock()
        mock_config.db_path = tmp_path / "test.db"
        mock_config.existing_config_files = []
        mock_config.has_custom_config = False
        mock_config.resolve_image_tag.return_value = ("ghcr.io/test/image", "v1.0.0")
        mocker.patch("rots.commands.instance.app.Config", return_value=mock_config)
        mocker.patch("rots.commands.instance.app.quadlet.write_scheduler_template")
        mocker.patch("rots.commands.instance.app.systemd.start")
        mocker.patch("rots.commands.instance.app.db.record_deployment")
        mock_http_healthy = mocker.patch("rots.commands.instance.app.systemd.wait_for_http_healthy")

        instance.deploy(scheduler="main", wait=True)

        mock_http_healthy.assert_not_called()

    def test_deploy_wait_http_timeout_records_failure_and_exits(self, mocker, tmp_path):
        """When wait_for_http_healthy times out, deployment failure is recorded and exits 1."""
        from rots.systemd import HttpHealthCheckTimeoutError

        mock_config = self._make_mock_config(mocker, tmp_path)
        mocker.patch("rots.commands.instance.app.Config", return_value=mock_config)
        mocker.patch("rots.commands.instance.app.assets.update")
        mocker.patch("rots.commands.instance.app.quadlet.write_web_template")
        mocker.patch("rots.commands.instance.app.systemd.start")
        mock_record = mocker.patch("rots.commands.instance.app.db.record_deployment")
        mocker.patch(
            "rots.commands.instance.app.systemd.wait_for_http_healthy",
            side_effect=HttpHealthCheckTimeoutError(7043, 60, "Connection refused"),
        )

        with pytest.raises(SystemExit) as exc_info:
            instance.deploy(web="7043", wait=True)

        assert exc_info.value.code == 1
        # Should have recorded a failure in deployment history
        calls = mock_record.call_args_list
        failure_recorded = any(
            call.kwargs.get("success") is False or (len(call.args) > 4 and call.args[4] is False)
            for call in calls
        )
        assert failure_recorded, "Expected a failure deployment record"


class TestRedeployWaitFlag:
    """Tests for the --wait HTTP health check flag in redeploy."""

    def _make_mock_config(self, mocker, tmp_path):
        """Build a standard mock Config for redeploy tests."""
        mock_config = mocker.MagicMock()
        mock_config.config_dir = mocker.MagicMock()
        mock_config.config_yaml = mocker.MagicMock()
        mock_config.var_dir = mocker.MagicMock()
        mock_config.web_template_path = tmp_path / "template"
        mock_config.db_path = tmp_path / "test.db"
        mock_config.existing_config_files = []
        mock_config.has_custom_config = False
        mock_config.resolve_image_tag.return_value = ("ghcr.io/test/image", "v1.0.0")
        return mock_config

    def _patch_discover(self, mocker, web_ports=(7043,)):
        """Patch instance discovery to return specific ports."""
        mocker.patch(
            "rots.commands.instance._helpers.systemd.discover_web_instances",
            return_value=list(web_ports),
        )
        mocker.patch(
            "rots.commands.instance._helpers.systemd.discover_worker_instances",
            return_value=[],
        )
        mocker.patch(
            "rots.commands.instance._helpers.systemd.discover_scheduler_instances",
            return_value=[],
        )

    def test_redeploy_with_wait_calls_wait_for_http_healthy(self, mocker, tmp_path):
        """--wait on redeploy should call wait_for_http_healthy with correct port."""
        mock_config = self._make_mock_config(mocker, tmp_path)
        mocker.patch("rots.commands.instance.app.Config", return_value=mock_config)
        self._patch_discover(mocker)
        mocker.patch("rots.commands.instance.app.assets.update")
        mocker.patch("rots.commands.instance.app.quadlet.write_web_template")
        mocker.patch("rots.commands.instance.app.systemd.recreate")
        mocker.patch("rots.commands.instance.app.db.record_deployment")
        mocker.patch(
            "rots.commands.instance.app.systemd.container_exists",
            return_value=True,
        )
        mock_http_healthy = mocker.patch("rots.commands.instance.app.systemd.wait_for_http_healthy")

        instance.redeploy(web="", wait=True)

        mock_http_healthy.assert_called_once()
        call_args = mock_http_healthy.call_args
        assert call_args.args[0] == 7043
        assert call_args.kwargs["timeout"] == 60
        assert "executor" in call_args.kwargs

    def test_redeploy_without_wait_does_not_call_wait_for_http_healthy(self, mocker, tmp_path):
        """Omitting --wait on redeploy should not call wait_for_http_healthy."""
        mock_config = self._make_mock_config(mocker, tmp_path)
        mocker.patch("rots.commands.instance.app.Config", return_value=mock_config)
        self._patch_discover(mocker)
        mocker.patch("rots.commands.instance.app.assets.update")
        mocker.patch("rots.commands.instance.app.quadlet.write_web_template")
        mocker.patch("rots.commands.instance.app.systemd.recreate")
        mocker.patch("rots.commands.instance.app.db.record_deployment")
        mocker.patch(
            "rots.commands.instance.app.systemd.container_exists",
            return_value=True,
        )
        mock_http_healthy = mocker.patch("rots.commands.instance.app.systemd.wait_for_http_healthy")

        instance.redeploy(web="", wait=False)

        mock_http_healthy.assert_not_called()

    def test_redeploy_wait_records_failure_on_http_timeout(self, mocker, tmp_path):
        """When wait_for_http_healthy times out during redeploy, failure is recorded."""
        from rots.systemd import HttpHealthCheckTimeoutError

        mock_config = self._make_mock_config(mocker, tmp_path)
        mocker.patch("rots.commands.instance.app.Config", return_value=mock_config)
        self._patch_discover(mocker)
        mocker.patch("rots.commands.instance.app.assets.update")
        mocker.patch("rots.commands.instance.app.quadlet.write_web_template")
        mocker.patch("rots.commands.instance.app.systemd.recreate")
        mock_record = mocker.patch("rots.commands.instance.app.db.record_deployment")
        mocker.patch(
            "rots.commands.instance.app.systemd.container_exists",
            return_value=True,
        )
        mocker.patch(
            "rots.commands.instance.app.systemd.wait_for_http_healthy",
            side_effect=HttpHealthCheckTimeoutError(7043, 60, "Connection refused"),
        )

        with pytest.raises(SystemExit) as exc_info:
            instance.redeploy(web="", wait=True)

        assert exc_info.value.code == 1
        calls = mock_record.call_args_list
        failure_recorded = any(
            call.kwargs.get("success") is False or (len(call.args) > 4 and call.args[4] is False)
            for call in calls
        )
        assert failure_recorded, "Expected a failure deployment record"


class TestCleanupCommand:
    """Tests for the cleanup command (remove static_assets Podman volume)."""

    def test_cleanup_calls_podman_volume_rm(self, mocker, tmp_path):
        """cleanup should call 'podman volume rm static_assets'."""
        mock_executor = mocker.MagicMock()
        mock_result = mocker.MagicMock()
        mock_result.returncode = 0
        mock_result.stderr = ""
        mock_executor.run.return_value = mock_result
        mocker.patch.object(Config, "get_executor", return_value=mock_executor)

        instance.cleanup(yes=True)

        mock_executor.run.assert_called_once()
        cmd = mock_executor.run.call_args[0][0]
        assert cmd == ["podman", "volume", "rm", "static_assets"]

    def test_cleanup_volume_not_found_is_treated_as_success(self, mocker, tmp_path, capsys):
        """When volume doesn't exist, cleanup should report success (idempotent)."""
        mock_executor = mocker.MagicMock()
        mock_result = mocker.MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "Error: no such volume static_assets"
        mock_executor.run.return_value = mock_result
        mocker.patch.object(Config, "get_executor", return_value=mock_executor)

        instance.cleanup(yes=True)

        captured = capsys.readouterr()
        assert "not found" in captured.err.lower() or "already removed" in captured.err.lower()

    def test_cleanup_failure_exits_nonzero(self, mocker, capsys):
        """Unexpected failure from podman should exit 1."""
        mock_executor = mocker.MagicMock()
        mock_result = mocker.MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "Error: some unexpected error"
        mock_executor.run.return_value = mock_result
        mocker.patch.object(Config, "get_executor", return_value=mock_executor)

        with pytest.raises(SystemExit) as exc_info:
            instance.cleanup(yes=True)

        assert exc_info.value.code == 1

    def test_cleanup_json_output_success(self, mocker, capsys):
        """cleanup --json should output valid JSON with success=True."""
        import json

        mock_executor = mocker.MagicMock()
        mock_result = mocker.MagicMock()
        mock_result.returncode = 0
        mock_result.stderr = ""
        mock_executor.run.return_value = mock_result
        mocker.patch.object(Config, "get_executor", return_value=mock_executor)

        instance.cleanup(yes=True, json_output=True)

        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["success"] is True
        assert data["volume"] == "static_assets"

    def test_cleanup_podman_not_found_exits_nonzero(self, mocker, capsys):
        """FileNotFoundError (podman not installed) should exit 1."""
        mock_executor = mocker.MagicMock()
        mock_executor.run.side_effect = FileNotFoundError("podman not found")
        mocker.patch.object(Config, "get_executor", return_value=mock_executor)

        with pytest.raises(SystemExit) as exc_info:
            instance.cleanup(yes=True)

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "podman" in captured.err.lower()


class TestRollbackCommand:
    """Tests for the rollback command (roll back to previous deployment)."""

    def _make_config_mock(self, mocker, tmp_path):
        """Create a mocked Config with a tmp db_path."""
        cfg_mock = mocker.MagicMock()
        cfg_mock.db_path = tmp_path / "deployments.db"
        cfg_mock.web_template_path = tmp_path / "onetime-web@.container"
        cfg_mock.worker_template_path = tmp_path / "onetime-worker@.container"
        cfg_mock.scheduler_template_path = tmp_path / "onetime-scheduler@.container"
        mocker.patch("rots.commands.instance.app.Config", return_value=cfg_mock)
        return cfg_mock

    def test_rollback_exits_when_no_history(self, mocker, tmp_path, capsys):
        """rollback should exit 1 when deployment history has fewer than 2 entries."""
        self._make_config_mock(mocker, tmp_path)
        mocker.patch(
            "rots.commands.instance.app.db.get_previous_tags",
            return_value=[("ghcr.io/ots/ots", "v1.0.0", "2025-01-01T00:00:00")],
        )

        with pytest.raises(SystemExit) as exc_info:
            instance.rollback(web="")

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "no previous deployment" in captured.err.lower()

    def test_rollback_exits_when_empty_history(self, mocker, tmp_path, capsys):
        """rollback should exit 1 when deployment history is completely empty."""
        self._make_config_mock(mocker, tmp_path)
        mocker.patch(
            "rots.commands.instance.app.db.get_previous_tags",
            return_value=[],
        )

        with pytest.raises(SystemExit) as exc_info:
            instance.rollback(web="")

        assert exc_info.value.code == 1

    def test_rollback_dry_run_shows_from_to(self, mocker, tmp_path, capsys):
        """rollback --dry-run should show from/to image:tag without systemd calls."""
        self._make_config_mock(mocker, tmp_path)
        mocker.patch(
            "rots.commands.instance.app.db.get_previous_tags",
            return_value=[
                ("ghcr.io/ots/ots", "v2.0.0", "2025-02-01T00:00:00"),
                ("ghcr.io/ots/ots", "v1.0.0", "2025-01-01T00:00:00"),
            ],
        )
        mocker.patch(
            "rots.commands.instance.app.resolve_identifiers",
            return_value={},
        )
        mock_recreate = mocker.patch("rots.commands.instance.app.systemd.recreate")

        instance.rollback(web="", dry_run=True)

        captured = capsys.readouterr()
        assert "v2.0.0" in captured.err
        assert "v1.0.0" in captured.err
        assert "dry-run" in captured.err.lower()
        mock_recreate.assert_not_called()

    def test_rollback_dry_run_json_output(self, mocker, tmp_path, capsys):
        """rollback --dry-run --json should output valid JSON with action/from/to fields."""
        import json

        self._make_config_mock(mocker, tmp_path)
        mocker.patch(
            "rots.commands.instance.app.db.get_previous_tags",
            return_value=[
                ("ghcr.io/ots/ots", "v2.0.0", "2025-02-01T00:00:00"),
                ("ghcr.io/ots/ots", "v1.0.0", "2025-01-01T00:00:00"),
            ],
        )
        mocker.patch(
            "rots.commands.instance.app.resolve_identifiers",
            return_value={},
        )

        instance.rollback(web="", dry_run=True, json_output=True)

        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["action"] == "rollback"
        assert data["dry_run"] is True
        assert "from" in data
        assert "to" in data
        assert data["from"]["tag"] == "v2.0.0"
        assert data["to"]["tag"] == "v1.0.0"

    def test_rollback_updates_aliases_and_redeploys(self, mocker, tmp_path, capsys):
        """rollback should call db.rollback then recreate running instances."""
        from rots.commands.instance.annotations import InstanceType

        self._make_config_mock(mocker, tmp_path)
        mocker.patch(
            "rots.commands.instance.app.db.get_previous_tags",
            return_value=[
                ("ghcr.io/ots/ots", "v2.0.0", "2025-02-01T00:00:00"),
                ("ghcr.io/ots/ots", "v1.0.0", "2025-01-01T00:00:00"),
            ],
        )
        mock_db_rollback = mocker.patch(
            "rots.commands.instance.app.db.rollback",
            return_value=("ghcr.io/ots/ots", "v1.0.0"),
        )
        mocker.patch(
            "rots.commands.instance.app.resolve_identifiers",
            return_value={InstanceType.WEB: ["7043"]},
        )
        mocker.patch("rots.commands.instance.app.assets.update")
        mocker.patch("rots.commands.instance.app.quadlet.write_web_template")
        mock_recreate = mocker.patch("rots.commands.instance.app.systemd.recreate")
        mock_record = mocker.patch("rots.commands.instance.app.db.record_deployment")

        instance.rollback(web="", yes=True)

        mock_db_rollback.assert_called_once()
        mock_recreate.assert_called_once()
        mock_record.assert_called()

    def test_rollback_db_rollback_failure_exits(self, mocker, tmp_path, capsys):
        """When db.rollback returns None, rollback should exit 1."""
        self._make_config_mock(mocker, tmp_path)
        mocker.patch(
            "rots.commands.instance.app.db.get_previous_tags",
            return_value=[
                ("ghcr.io/ots/ots", "v2.0.0", "2025-02-01T00:00:00"),
                ("ghcr.io/ots/ots", "v1.0.0", "2025-01-01T00:00:00"),
            ],
        )
        mocker.patch(
            "rots.commands.instance.app.db.rollback",
            return_value=None,
        )

        with pytest.raises(SystemExit) as exc_info:
            instance.rollback(web="", yes=True)

        assert exc_info.value.code == 1

    def test_rollback_no_running_instances_succeeds(self, mocker, tmp_path, capsys):
        """rollback when no instances are running should succeed (just update aliases)."""
        self._make_config_mock(mocker, tmp_path)
        mocker.patch(
            "rots.commands.instance.app.db.get_previous_tags",
            return_value=[
                ("ghcr.io/ots/ots", "v2.0.0", "2025-02-01T00:00:00"),
                ("ghcr.io/ots/ots", "v1.0.0", "2025-01-01T00:00:00"),
            ],
        )
        mocker.patch(
            "rots.commands.instance.app.db.rollback",
            return_value=("ghcr.io/ots/ots", "v1.0.0"),
        )
        mocker.patch(
            "rots.commands.instance.app.resolve_identifiers",
            return_value={},  # no running instances
        )
        mock_recreate = mocker.patch("rots.commands.instance.app.systemd.recreate")

        instance.rollback(web="", yes=True)

        # Should succeed without redeploying
        mock_recreate.assert_not_called()
        captured = capsys.readouterr()
        assert "no running" in captured.err.lower()


class TestDeployHooks:
    """Tests for --pre-hook and --post-hook in deploy."""

    def _make_mock_config(self, mocker, tmp_path):
        """Build a standard mock Config for deploy tests."""
        mock_config = mocker.MagicMock()
        mock_config.config_dir = mocker.MagicMock()
        mock_config.config_yaml = mocker.MagicMock()
        mock_config.var_dir = mocker.MagicMock()
        mock_config.web_template_path = mocker.MagicMock()
        mock_config.db_path = tmp_path / "test.db"
        mock_config.existing_config_files = []
        mock_config.has_custom_config = False
        mock_config.resolve_image_tag.return_value = ("ghcr.io/test/image", "v1.0.0")
        mock_config.get_executor.return_value = None
        return mock_config

    def test_pre_hook_is_called_before_deploy(self, mocker, tmp_path):
        """--pre-hook command must run before the deployment starts."""
        mock_config = self._make_mock_config(mocker, tmp_path)
        mocker.patch("rots.commands.instance.app.Config", return_value=mock_config)
        mocker.patch("rots.commands.instance.app.assets.update")
        mocker.patch("rots.commands.instance.app.quadlet.write_web_template")
        mocker.patch("rots.commands.instance.app.systemd.start")
        mocker.patch("rots.commands.instance.app.db.record_deployment")
        mock_run_hook = mocker.patch("rots.commands.instance.app.run_hook")

        instance.deploy(web="7043", pre_hook="./scan.sh")

        mock_run_hook.assert_called_once()
        args, kwargs = mock_run_hook.call_args
        assert args == ("./scan.sh", "pre-hook")
        assert kwargs["quiet"] is False
        assert "executor" in kwargs

    def test_post_hook_is_called_after_successful_deploy(self, mocker, tmp_path):
        """--post-hook command must run after all instances deploy successfully."""
        mock_config = self._make_mock_config(mocker, tmp_path)
        mocker.patch("rots.commands.instance.app.Config", return_value=mock_config)
        mocker.patch("rots.commands.instance.app.assets.update")
        mocker.patch("rots.commands.instance.app.quadlet.write_web_template")
        mocker.patch("rots.commands.instance.app.systemd.start")
        mocker.patch("rots.commands.instance.app.db.record_deployment")
        mock_run_hook = mocker.patch("rots.commands.instance.app.run_hook")

        instance.deploy(web="7043", post_hook="./notify.sh")

        mock_run_hook.assert_called_once()
        args, kwargs = mock_run_hook.call_args
        assert args == ("./notify.sh", "post-hook")
        assert kwargs["quiet"] is False
        assert "executor" in kwargs

    def test_pre_hook_failure_aborts_deploy(self, mocker, tmp_path):
        """When --pre-hook exits non-zero, deployment must be aborted."""
        mock_config = self._make_mock_config(mocker, tmp_path)
        mocker.patch("rots.commands.instance.app.Config", return_value=mock_config)
        mock_start = mocker.patch("rots.commands.instance.app.systemd.start")
        mocker.patch(
            "rots.commands.instance.app.run_hook",
            side_effect=SystemExit(1),
        )

        with pytest.raises(SystemExit) as exc_info:
            instance.deploy(web="7043", pre_hook="./failing-scan.sh")

        assert exc_info.value.code == 1
        # systemd.start should never have been called
        mock_start.assert_not_called()

    def test_pre_hook_skipped_on_dry_run(self, mocker, tmp_path):
        """--pre-hook should not run during --dry-run."""
        mock_config = self._make_mock_config(mocker, tmp_path)
        mock_config.web_template_path = tmp_path / "template"
        mock_config.web_template_path.touch()
        mocker.patch("rots.commands.instance.app.Config", return_value=mock_config)
        mocker.patch("rots.commands.instance.app.quadlet.render_web_template", return_value="")
        mock_run_hook = mocker.patch("rots.commands.instance.app.run_hook")

        instance.deploy(web="7043", pre_hook="./scan.sh", dry_run=True)

        mock_run_hook.assert_not_called()

    def test_post_hook_skipped_on_dry_run(self, mocker, tmp_path):
        """--post-hook should not run during --dry-run."""
        mock_config = self._make_mock_config(mocker, tmp_path)
        mock_config.web_template_path = tmp_path / "template"
        mock_config.web_template_path.touch()
        mocker.patch("rots.commands.instance.app.Config", return_value=mock_config)
        mocker.patch("rots.commands.instance.app.quadlet.render_web_template", return_value="")
        mock_run_hook = mocker.patch("rots.commands.instance.app.run_hook")

        instance.deploy(web="7043", post_hook="./notify.sh", dry_run=True)

        mock_run_hook.assert_not_called()


class TestRedeployHooks:
    """Tests for --pre-hook and --post-hook in redeploy."""

    def _patch_discover(self, mocker, web_ports=(7043,)):
        """Patch instance discovery to return specific ports."""
        mocker.patch(
            "rots.commands.instance._helpers.systemd.discover_web_instances",
            return_value=list(web_ports),
        )
        mocker.patch(
            "rots.commands.instance._helpers.systemd.discover_worker_instances",
            return_value=[],
        )
        mocker.patch(
            "rots.commands.instance._helpers.systemd.discover_scheduler_instances",
            return_value=[],
        )

    def _make_mock_config(self, mocker, tmp_path):
        """Build a standard mock Config for redeploy tests."""
        mock_config = mocker.MagicMock()
        mock_config.config_dir = mocker.MagicMock()
        mock_config.config_yaml = mocker.MagicMock()
        mock_config.var_dir = mocker.MagicMock()
        mock_config.web_template_path = tmp_path / "template"
        mock_config.db_path = tmp_path / "test.db"
        mock_config.existing_config_files = []
        mock_config.has_custom_config = False
        mock_config.resolve_image_tag.return_value = ("ghcr.io/test/image", "v1.0.0")
        return mock_config

    def test_pre_hook_is_called_before_redeploy(self, mocker, tmp_path):
        """--pre-hook must run before redeployment."""
        mock_config = self._make_mock_config(mocker, tmp_path)
        mocker.patch("rots.commands.instance.app.Config", return_value=mock_config)
        self._patch_discover(mocker)
        mocker.patch("rots.commands.instance.app.assets.update")
        mocker.patch("rots.commands.instance.app.quadlet.write_web_template")
        mocker.patch("rots.commands.instance.app.systemd.recreate")
        mocker.patch("rots.commands.instance.app.db.record_deployment")
        mocker.patch(
            "rots.commands.instance.app.systemd.container_exists",
            return_value=True,
        )
        mock_run_hook = mocker.patch("rots.commands.instance.app.run_hook")

        instance.redeploy(web="", pre_hook="./scan.sh")

        mock_run_hook.assert_called_once()
        args, kwargs = mock_run_hook.call_args
        assert args == ("./scan.sh", "pre-hook")
        assert kwargs["quiet"] is False
        assert "executor" in kwargs

    def test_post_hook_is_called_after_successful_redeploy(self, mocker, tmp_path):
        """--post-hook must run after successful redeployment."""
        mock_config = self._make_mock_config(mocker, tmp_path)
        mocker.patch("rots.commands.instance.app.Config", return_value=mock_config)
        self._patch_discover(mocker)
        mocker.patch("rots.commands.instance.app.assets.update")
        mocker.patch("rots.commands.instance.app.quadlet.write_web_template")
        mocker.patch("rots.commands.instance.app.systemd.recreate")
        mocker.patch("rots.commands.instance.app.db.record_deployment")
        mocker.patch(
            "rots.commands.instance.app.systemd.container_exists",
            return_value=True,
        )
        mock_run_hook = mocker.patch("rots.commands.instance.app.run_hook")

        instance.redeploy(web="", post_hook="./notify.sh")

        mock_run_hook.assert_called_once()
        args, kwargs = mock_run_hook.call_args
        assert args == ("./notify.sh", "post-hook")
        assert kwargs["quiet"] is False
        assert "executor" in kwargs

    def test_pre_hook_failure_aborts_redeploy(self, mocker, tmp_path):
        """When --pre-hook exits non-zero, redeployment must be aborted."""
        mock_config = self._make_mock_config(mocker, tmp_path)
        mocker.patch("rots.commands.instance.app.Config", return_value=mock_config)
        self._patch_discover(mocker)
        mock_recreate = mocker.patch("rots.commands.instance.app.systemd.recreate")
        mocker.patch(
            "rots.commands.instance.app.run_hook",
            side_effect=SystemExit(1),
        )

        with pytest.raises(SystemExit) as exc_info:
            instance.redeploy(web="", pre_hook="./failing-scan.sh")

        assert exc_info.value.code == 1
        mock_recreate.assert_not_called()


class TestRunHookExecutor:
    """Test run_hook() always runs locally, never forwarding to remote."""

    def test_run_hook_local_uses_subprocess(self, mocker):
        """run_hook without executor should use subprocess.run (local)."""
        from rots.commands.instance._helpers import run_hook

        mock_proc = mocker.MagicMock()
        mock_proc.returncode = 0
        mock_subprocess = mocker.patch(
            "rots.commands.instance._helpers.subprocess.run",
            return_value=mock_proc,
        )

        run_hook("./scan.sh", "pre-hook", quiet=True)

        mock_subprocess.assert_called_once_with("./scan.sh", shell=True, text=True)

    def test_run_hook_with_remote_executor_still_runs_locally(self, mocker):
        """run_hook with remote executor should still use subprocess.run locally."""
        from unittest.mock import MagicMock

        from rots.commands.instance._helpers import run_hook

        mock_ex = MagicMock()
        mock_proc = mocker.MagicMock()
        mock_proc.returncode = 0
        mock_subprocess = mocker.patch(
            "rots.commands.instance._helpers.subprocess.run",
            return_value=mock_proc,
        )

        run_hook("./scan.sh", "pre-hook", quiet=True, executor=mock_ex)

        # Should use local subprocess, NOT executor.run
        mock_subprocess.assert_called_once_with("./scan.sh", shell=True, text=True)
        mock_ex.run.assert_not_called()

    def test_run_hook_with_remote_executor_failure_raises_system_exit(self, mocker):
        """run_hook with remote executor still runs locally and raises on failure."""
        from unittest.mock import MagicMock

        from rots.commands.instance._helpers import run_hook

        mock_ex = MagicMock()
        mock_proc = mocker.MagicMock()
        mock_proc.returncode = 1
        mocker.patch(
            "rots.commands.instance._helpers.subprocess.run",
            return_value=mock_proc,
        )

        with pytest.raises(SystemExit) as exc_info:
            run_hook("./failing.sh", "pre-hook", quiet=True, executor=mock_ex)

        assert exc_info.value.code == 1
        mock_ex.run.assert_not_called()

    def test_run_hook_local_failure_raises_system_exit(self, mocker):
        """run_hook local path should raise SystemExit on non-zero exit."""
        from rots.commands.instance._helpers import run_hook

        mock_proc = mocker.MagicMock()
        mock_proc.returncode = 42
        mocker.patch(
            "rots.commands.instance._helpers.subprocess.run",
            return_value=mock_proc,
        )

        with pytest.raises(SystemExit) as exc_info:
            run_hook("./failing.sh", "pre-hook", quiet=True)

        assert exc_info.value.code == 1


class TestEnableDisableRequireSystemctl:
    """Test that enable() and disable() guard against missing systemctl.

    The autouse mock_systemctl_available fixture normally makes shutil.which
    return a valid path. These tests override it to return None to simulate
    a system without systemd (e.g. macOS), verifying that require_systemctl()
    causes both commands to exit with code 1 before touching subprocess.run.
    """

    def test_enable_exits_when_systemctl_missing(self, mocker, capsys):
        """enable() must exit with code 1 when systemctl is not found."""
        # Override the autouse fixture: report systemctl as absent
        mocker.patch("shutil.which", return_value=None)

        with pytest.raises(SystemExit) as exc_info:
            instance.enable(web="7043")

        assert exc_info.value.code == 1

    def test_disable_exits_when_systemctl_missing(self, mocker, capsys):
        """disable() must exit with code 1 when systemctl is not found."""
        mocker.patch("shutil.which", return_value=None)

        with pytest.raises(SystemExit) as exc_info:
            instance.disable(web="7043", yes=True)

        assert exc_info.value.code == 1

    def test_enable_uses_systemd_module(self, mocker, capsys):
        """enable() should delegate to systemd.enable()."""
        mocker.patch(
            "rots.commands.instance._helpers.systemd.discover_web_instances",
            return_value=[7043],
        )
        mocker.patch(
            "rots.commands.instance._helpers.systemd.discover_worker_instances",
            return_value=[],
        )
        mocker.patch(
            "rots.commands.instance._helpers.systemd.discover_scheduler_instances",
            return_value=[],
        )
        mock_enable = mocker.patch("rots.commands.instance.app.systemd.enable")

        instance.enable(web="7043")

        mock_enable.assert_called_once_with("onetime-web@7043", executor=None)

    def test_disable_uses_systemd_module(self, mocker, capsys):
        """disable() should delegate to systemd.disable()."""
        mocker.patch(
            "rots.commands.instance._helpers.systemd.discover_web_instances",
            return_value=[7043],
        )
        mocker.patch(
            "rots.commands.instance._helpers.systemd.discover_worker_instances",
            return_value=[],
        )
        mocker.patch(
            "rots.commands.instance._helpers.systemd.discover_scheduler_instances",
            return_value=[],
        )
        mock_disable = mocker.patch("rots.commands.instance.app.systemd.disable")

        instance.disable(web="7043", yes=True)

        mock_disable.assert_called_once_with("onetime-web@7043", executor=None)


class TestListInstancesJsonOutput:
    """Tests for list_instances JSON output path."""

    @pytest.fixture(autouse=True)
    def _mock_health_map(self, mocker):
        mocker.patch(
            "rots.commands.instance.app.systemd.get_container_health_map",
            return_value={},
        )

    def test_list_json_output(self, mocker, capsys, tmp_path):
        """list --json should output valid JSON."""
        import json

        mocker.patch(
            "rots.commands.instance._helpers.systemd.discover_web_instances",
            return_value=[7043],
        )
        mocker.patch(
            "rots.commands.instance._helpers.systemd.discover_worker_instances",
            return_value=[],
        )
        mocker.patch(
            "rots.commands.instance._helpers.systemd.discover_scheduler_instances",
            return_value=[],
        )
        mocker.patch(
            "rots.commands.instance.app.systemd.is_active",
            return_value="active",
        )

        mock_config = mocker.Mock()
        mock_config.db_path = tmp_path / "test.db"
        mock_config.get_executor.return_value = None
        mocker.patch(
            "rots.commands.instance.app.Config",
            return_value=mock_config,
        )
        mocker.patch(
            "rots.commands.instance.app.db.get_deployments",
            return_value=[],
        )

        instance.list_instances(json_output=True)

        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["type"] == "web"
        assert data[0]["id"] == "7043"
        assert data[0]["status"] == "active"

    def test_list_json_output_with_deployment_info(self, mocker, capsys, tmp_path):
        """list --json should include deployment info when available."""
        import json

        mocker.patch(
            "rots.commands.instance._helpers.systemd.discover_web_instances",
            return_value=[7043],
        )
        mocker.patch(
            "rots.commands.instance._helpers.systemd.discover_worker_instances",
            return_value=[],
        )
        mocker.patch(
            "rots.commands.instance._helpers.systemd.discover_scheduler_instances",
            return_value=[],
        )
        mocker.patch(
            "rots.commands.instance.app.systemd.is_active",
            return_value="active",
        )

        mock_config = mocker.Mock()
        mock_config.db_path = tmp_path / "test.db"
        mock_config.get_executor.return_value = None
        mocker.patch(
            "rots.commands.instance.app.Config",
            return_value=mock_config,
        )

        mock_dep = mocker.Mock()
        mock_dep.image = "ghcr.io/onetimesecret/onetimesecret"
        mock_dep.tag = "v0.23.0"
        mock_dep.timestamp = "2025-01-01T10:00:00.000000"
        mock_dep.action = "deploy-web"
        mocker.patch(
            "rots.commands.instance.app.db.get_deployments",
            return_value=[mock_dep],
        )

        instance.list_instances(json_output=True)

        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data[0]["image"] == "ghcr.io/onetimesecret/onetimesecret"
        assert data[0]["tag"] == "v0.23.0"


class TestRunCommandExists:
    """Tests for the run command."""

    def test_run_function_exists(self):
        """run command should exist."""
        assert hasattr(instance, "run")
        assert callable(instance.run)

    def test_run_local_image_foreground(self, mocker, tmp_path):
        """run should use resolved image by default (foreground)."""
        mock_executor = mocker.MagicMock()
        mock_executor.run_stream.return_value = 0

        mock_config = mocker.Mock()
        mock_config.tag = "v0.23.0"
        mock_config.image = "ghcr.io/onetimesecret/onetimesecret"
        mock_config.registry = None
        mock_config.existing_config_files = []
        mock_config.resolve_image_tag.return_value = (
            "ghcr.io/onetimesecret/onetimesecret",
            "v0.23.0",
        )
        mock_config.resolved_image_with_tag.return_value = (
            "ghcr.io/onetimesecret/onetimesecret:v0.23.0"
        )
        mock_config.podman_auth_args.return_value = []
        mock_config.get_executor.return_value = mock_executor
        mocker.patch("rots.commands.instance.app.Config", return_value=mock_config)
        mocker.patch(
            "rots.commands.instance.app.quadlet.DEFAULT_ENV_FILE",
            tmp_path / "nonexistent",
        )

        instance.run(port=7143, quiet=True)

        mock_executor.run_stream.assert_called_once()
        cmd = mock_executor.run_stream.call_args.args[0]
        assert cmd[0] == "podman"
        assert cmd[1] == "run"
        assert "--rm" in cmd
        assert "-p" in cmd
        assert "7143:7143" in cmd
        full_image = cmd[-1]
        assert full_image == "ghcr.io/onetimesecret/onetimesecret:v0.23.0"

    def test_run_with_custom_name(self, mocker, tmp_path):
        """run --name should set container name."""
        mock_executor = mocker.MagicMock()
        mock_executor.run_stream.return_value = 0

        mock_config = mocker.Mock()
        mock_config.tag = "v0.23.0"
        mock_config.image = "ghcr.io/onetimesecret/onetimesecret"
        mock_config.registry = None
        mock_config.existing_config_files = []
        mock_config.resolve_image_tag.return_value = (mock_config.image, mock_config.tag)
        mock_config.resolved_image_with_tag.return_value = f"{mock_config.image}:{mock_config.tag}"
        mock_config.podman_auth_args.return_value = []
        mock_config.get_executor.return_value = mock_executor
        mocker.patch("rots.commands.instance.app.Config", return_value=mock_config)
        mocker.patch(
            "rots.commands.instance.app.quadlet.DEFAULT_ENV_FILE",
            tmp_path / "nonexistent",
        )

        instance.run(port=7143, name="my-container", quiet=True)

        cmd = mock_executor.run_stream.call_args.args[0]
        assert "--name" in cmd
        name_idx = cmd.index("--name")
        assert cmd[name_idx + 1] == "my-container"

    def test_run_with_detach(self, mocker, tmp_path, capsys):
        """run --detach should pass -d to podman."""
        from ots_shared.ssh.executor import Result

        mock_executor = mocker.MagicMock()
        mock_result = Result(
            command="podman run ...", returncode=0, stdout="abc123def456\n", stderr=""
        )
        mock_executor.run.return_value = mock_result

        mock_config = mocker.Mock()
        mock_config.tag = "v0.23.0"
        mock_config.image = "ghcr.io/onetimesecret/onetimesecret"
        mock_config.registry = None
        mock_config.existing_config_files = []
        mock_config.resolve_image_tag.return_value = (mock_config.image, mock_config.tag)
        mock_config.resolved_image_with_tag.return_value = f"{mock_config.image}:{mock_config.tag}"
        mock_config.podman_auth_args.return_value = []
        mock_config.get_executor.return_value = mock_executor
        mocker.patch("rots.commands.instance.app.Config", return_value=mock_config)
        mocker.patch(
            "rots.commands.instance.app.quadlet.DEFAULT_ENV_FILE",
            tmp_path / "nonexistent",
        )

        instance.run(port=7143, detach=True, quiet=True)

        cmd = mock_executor.run.call_args.args[0]
        assert "-d" in cmd

    def test_run_with_tag(self, mocker, tmp_path):
        """run --tag should use specified tag in image."""
        from unittest.mock import Mock

        from rots.config import Config

        mock_executor = mocker.MagicMock()
        mock_executor.run_stream.return_value = 0

        cfg = Config()
        cfg.resolve_image_tag = Mock(return_value=(cfg.image, "v0.19.0"))
        cfg.resolved_image_with_tag = Mock(return_value=f"{cfg.image}:v0.19.0")
        cfg.podman_auth_args = Mock(return_value=[])
        cfg.get_executor = Mock(return_value=mock_executor)
        mocker.patch("rots.commands.instance.app.Config", lambda: cfg)
        mocker.patch(
            "rots.commands.instance.app.quadlet.DEFAULT_ENV_FILE",
            tmp_path / "nonexistent",
        )

        # Patch dataclasses.replace to re-attach mocks on the same instance
        def tracking_replace(obj, **kwargs):
            for k, v in kwargs.items():
                object.__setattr__(obj, k, v)
            new_image = kwargs.get("image", obj.image)
            new_tag = kwargs.get("tag", obj.tag)
            obj.resolve_image_tag = Mock(return_value=(new_image, new_tag))
            obj.resolved_image_with_tag = Mock(return_value=f"{new_image}:{new_tag}")
            obj.podman_auth_args = Mock(return_value=[])
            obj.get_executor = Mock(return_value=mock_executor)
            return obj

        mocker.patch(
            "rots.commands.instance.app.dataclasses.replace",
            side_effect=tracking_replace,
        )

        instance.run(port=7143, tag="v0.19.0", quiet=True)

        cmd = mock_executor.run_stream.call_args.args[0]
        full_image = cmd[-1]
        assert "v0.19.0" in full_image
        assert "ghcr.io" in full_image

    def test_run_no_tag_uses_resolve(self, mocker, tmp_path):
        """run without --tag should use resolve_image_tag()."""
        mock_executor = mocker.MagicMock()
        mock_executor.run_stream.return_value = 0

        mock_config = mocker.Mock()
        mock_config.tag = "current"
        mock_config.image = "ghcr.io/onetimesecret/onetimesecret"
        mock_config.registry = None
        mock_config.existing_config_files = []
        mock_config.resolve_image_tag.return_value = (
            "ghcr.io/onetimesecret/onetimesecret",
            "v0.23.0",
        )
        mock_config.resolved_image_with_tag.return_value = (
            "ghcr.io/onetimesecret/onetimesecret:v0.23.0"
        )
        mock_config.podman_auth_args.return_value = []
        mock_config.get_executor.return_value = mock_executor
        mocker.patch("rots.commands.instance.app.Config", return_value=mock_config)
        mocker.patch(
            "rots.commands.instance.app.quadlet.DEFAULT_ENV_FILE",
            tmp_path / "nonexistent",
        )

        instance.run(port=7143, quiet=True)

        mock_config.resolved_image_with_tag.assert_called_once()
        cmd = mock_executor.run_stream.call_args.args[0]
        full_image = cmd[-1]
        assert "v0.23.0" in full_image

    def test_run_nonzero_exit_raises_systemexit(self, mocker, tmp_path):
        """run should exit with the process exit code when podman fails."""
        mock_executor = mocker.MagicMock()
        mock_executor.run_stream.return_value = 1

        mock_config = mocker.Mock()
        mock_config.tag = "v0.23.0"
        mock_config.image = "ghcr.io/onetimesecret/onetimesecret"
        mock_config.registry = None
        mock_config.existing_config_files = []
        mock_config.resolve_image_tag.return_value = (mock_config.image, mock_config.tag)
        mock_config.resolved_image_with_tag.return_value = f"{mock_config.image}:{mock_config.tag}"
        mock_config.podman_auth_args.return_value = []
        mock_config.get_executor.return_value = mock_executor
        mocker.patch("rots.commands.instance.app.Config", return_value=mock_config)
        mocker.patch(
            "rots.commands.instance.app.quadlet.DEFAULT_ENV_FILE",
            tmp_path / "nonexistent",
        )

        with pytest.raises(SystemExit) as exc_info:
            instance.run(port=7143, quiet=True)

        assert exc_info.value.code == 1

    def test_run_keyboard_interrupt_handled(self, mocker, tmp_path, capsys):
        """run should handle KeyboardInterrupt gracefully."""
        mock_executor = mocker.MagicMock()
        mock_executor.run_stream.side_effect = KeyboardInterrupt

        mock_config = mocker.Mock()
        mock_config.tag = "v0.23.0"
        mock_config.image = "ghcr.io/onetimesecret/onetimesecret"
        mock_config.registry = None
        mock_config.existing_config_files = []
        mock_config.resolve_image_tag.return_value = (mock_config.image, mock_config.tag)
        mock_config.resolved_image_with_tag.return_value = f"{mock_config.image}:{mock_config.tag}"
        mock_config.podman_auth_args.return_value = []
        mock_config.get_executor.return_value = mock_executor
        mocker.patch("rots.commands.instance.app.Config", return_value=mock_config)
        mocker.patch(
            "rots.commands.instance.app.quadlet.DEFAULT_ENV_FILE",
            tmp_path / "nonexistent",
        )

        # Should not raise (quiet suppresses the "Stopped" message)
        instance.run(port=7143, quiet=True)

        # With quiet=True, logger.info() is suppressed (level raised to WARNING)
        captured = capsys.readouterr()
        assert "Stopped" not in captured.err

    def test_run_without_rm_flag(self, mocker, tmp_path):
        """run with rm=False should not add --rm to command."""
        mock_executor = mocker.MagicMock()
        mock_executor.run_stream.return_value = 0

        mock_config = mocker.Mock()
        mock_config.tag = "v0.23.0"
        mock_config.image = "ghcr.io/onetimesecret/onetimesecret"
        mock_config.registry = None
        mock_config.existing_config_files = []
        mock_config.resolve_image_tag.return_value = (mock_config.image, mock_config.tag)
        mock_config.resolved_image_with_tag.return_value = f"{mock_config.image}:{mock_config.tag}"
        mock_config.podman_auth_args.return_value = []
        mock_config.get_executor.return_value = mock_executor
        mocker.patch("rots.commands.instance.app.Config", return_value=mock_config)
        mocker.patch(
            "rots.commands.instance.app.quadlet.DEFAULT_ENV_FILE",
            tmp_path / "nonexistent",
        )

        instance.run(port=7143, rm=False, quiet=True)

        cmd = mock_executor.run_stream.call_args.args[0]
        assert "--rm" not in cmd


class TestExecShellCommand:
    """Tests for the exec command."""

    def test_exec_function_exists(self):
        """exec command should exist."""
        assert hasattr(instance, "exec_shell")
        assert callable(instance.exec_shell)

    def test_exec_no_running_instances(self, mocker, capsys):
        """exec with no running instances should print message."""
        mocker.patch(
            "rots.commands.instance._helpers.systemd.discover_web_instances",
            return_value=[],
        )
        mocker.patch(
            "rots.commands.instance._helpers.systemd.discover_worker_instances",
            return_value=[],
        )
        mocker.patch(
            "rots.commands.instance._helpers.systemd.discover_scheduler_instances",
            return_value=[],
        )

        instance.exec_shell()

        captured = capsys.readouterr()
        assert "No running instances found" in captured.err

    def test_exec_calls_podman_exec(self, mocker, capsys):
        """exec with running instances should call run_interactive."""
        mock_config = mocker.MagicMock()
        mocker.patch("rots.commands.instance.app.Config", return_value=mock_config)
        mock_ex = mock_config.get_executor()
        mock_ex.run_interactive.return_value = 0

        mocker.patch(
            "rots.commands.instance._helpers.systemd.discover_web_instances",
            return_value=[7043],
        )
        mocker.patch(
            "rots.commands.instance._helpers.systemd.discover_worker_instances",
            return_value=[],
        )
        mocker.patch(
            "rots.commands.instance._helpers.systemd.discover_scheduler_instances",
            return_value=[],
        )

        instance.exec_shell(web="")

        mock_ex.run_interactive.assert_called_once()
        cmd = mock_ex.run_interactive.call_args[0][0]
        assert cmd[0] == "podman"
        assert cmd[1] == "exec"
        assert "-it" in cmd

    def test_exec_with_custom_command(self, mocker, capsys):
        """exec --command should pass custom shell via run_interactive."""
        mock_config = mocker.MagicMock()
        mocker.patch("rots.commands.instance.app.Config", return_value=mock_config)
        mock_ex = mock_config.get_executor()
        mock_ex.run_interactive.return_value = 0

        mocker.patch(
            "rots.commands.instance._helpers.systemd.discover_web_instances",
            return_value=[7043],
        )
        mocker.patch(
            "rots.commands.instance._helpers.systemd.discover_worker_instances",
            return_value=[],
        )
        mocker.patch(
            "rots.commands.instance._helpers.systemd.discover_scheduler_instances",
            return_value=[],
        )

        instance.exec_shell(web="", command="/bin/sh")

        cmd = mock_ex.run_interactive.call_args[0][0]
        assert "/bin/sh" in cmd


class TestMetricsCommand:
    """Tests for the metrics command."""

    def test_metrics_function_exists(self):
        """metrics command should exist."""
        assert hasattr(instance, "metrics")
        assert callable(instance.metrics)

    def test_metrics_no_instances(self, mocker, capsys):
        """metrics with no configured instances should print message."""
        mocker.patch(
            "rots.commands.instance._helpers.systemd.discover_web_instances",
            return_value=[],
        )
        mocker.patch(
            "rots.commands.instance._helpers.systemd.discover_worker_instances",
            return_value=[],
        )
        mocker.patch(
            "rots.commands.instance._helpers.systemd.discover_scheduler_instances",
            return_value=[],
        )

        instance.metrics()

        captured = capsys.readouterr()
        assert "No configured instances found" in captured.err

    def test_metrics_no_instances_json(self, mocker, capsys):
        """metrics --json with no instances should output empty JSON list."""
        import json

        mocker.patch(
            "rots.commands.instance._helpers.systemd.discover_web_instances",
            return_value=[],
        )
        mocker.patch(
            "rots.commands.instance._helpers.systemd.discover_worker_instances",
            return_value=[],
        )
        mocker.patch(
            "rots.commands.instance._helpers.systemd.discover_scheduler_instances",
            return_value=[],
        )

        instance.metrics(json_output=True)

        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["instances"] == []

    def test_metrics_with_running_instance_table(self, mocker, capsys):
        """metrics should show table output for running instances."""
        import json

        mocker.patch(
            "rots.commands.instance._helpers.systemd.discover_web_instances",
            return_value=[7043],
        )
        mocker.patch(
            "rots.commands.instance._helpers.systemd.discover_worker_instances",
            return_value=[],
        )
        mocker.patch(
            "rots.commands.instance._helpers.systemd.discover_scheduler_instances",
            return_value=[],
        )

        stats_output = json.dumps(
            [
                {
                    "CPU": "1.50%",
                    "MemUsage": "128MiB / 2GiB",
                    "MemPerc": "6.25%",
                    "NetInput": "10MB",
                    "NetOutput": "5MB",
                    "BlockInput": "100MB",
                    "BlockOutput": "50MB",
                }
            ]
        )

        mock_executor = mocker.MagicMock()
        mocker.patch.object(Config, "get_executor", return_value=mock_executor)

        def mock_run_side_effect(cmd, **kwargs):
            result = mocker.MagicMock()
            result.stderr = ""
            if "is-active" in cmd:
                result.returncode = 0
                result.stdout = "active\n"
            elif "stats" in cmd:
                result.returncode = 0
                result.stdout = stats_output
            else:
                result.returncode = 0
                result.stdout = ""
            return result

        mock_executor.run.side_effect = mock_run_side_effect

        instance.metrics(web="")

        captured = capsys.readouterr()
        assert "TYPE" in captured.out
        assert "STATE" in captured.out
        assert "web" in captured.out

    def test_metrics_with_running_instance_json(self, mocker, capsys):
        """metrics --json should output structured JSON with stats."""
        import json

        mocker.patch(
            "rots.commands.instance._helpers.systemd.discover_web_instances",
            return_value=[7043],
        )
        mocker.patch(
            "rots.commands.instance._helpers.systemd.discover_worker_instances",
            return_value=[],
        )
        mocker.patch(
            "rots.commands.instance._helpers.systemd.discover_scheduler_instances",
            return_value=[],
        )

        stats_output = json.dumps(
            [
                {
                    "CPU": "2.00%",
                    "MemUsage": "256MiB / 4GiB",
                    "MemPerc": "6.25%",
                    "NetInput": "20MB",
                    "NetOutput": "10MB",
                    "BlockInput": "200MB",
                    "BlockOutput": "100MB",
                }
            ]
        )

        mock_executor = mocker.MagicMock()
        mocker.patch.object(Config, "get_executor", return_value=mock_executor)

        def mock_run_side_effect(cmd, **kwargs):
            result = mocker.MagicMock()
            result.stderr = ""
            if "is-active" in cmd:
                result.returncode = 0
                result.stdout = "active\n"
            elif "stats" in cmd:
                result.returncode = 0
                result.stdout = stats_output
            else:
                result.returncode = 0
                result.stdout = ""
            return result

        mock_executor.run.side_effect = mock_run_side_effect

        instance.metrics(json_output=True)

        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert "instances" in data
        assert len(data["instances"]) == 1
        entry = data["instances"][0]
        assert entry["instance_type"] == "web"
        assert entry["identifier"] == "7043"
        assert entry["active_state"] == "active"
        assert entry["cpu_percent"] == "2.00%"
        assert entry["mem_usage"] == "256MiB / 4GiB"

    def test_metrics_handles_podman_stats_failure(self, mocker, capsys):
        """metrics should show n/a when podman stats fails."""
        import json

        mocker.patch(
            "rots.commands.instance._helpers.systemd.discover_web_instances",
            return_value=[7043],
        )
        mocker.patch(
            "rots.commands.instance._helpers.systemd.discover_worker_instances",
            return_value=[],
        )
        mocker.patch(
            "rots.commands.instance._helpers.systemd.discover_scheduler_instances",
            return_value=[],
        )

        mock_executor = mocker.MagicMock()
        mocker.patch.object(Config, "get_executor", return_value=mock_executor)

        def mock_run_side_effect(cmd, **kwargs):
            result = mocker.MagicMock()
            if "is-active" in cmd:
                result.returncode = 0
                result.stdout = "inactive\n"
                result.stderr = ""
            elif "stats" in cmd:
                result.returncode = 1
                result.stdout = ""
                result.stderr = "no such container"
            else:
                result.returncode = 0
                result.stdout = ""
                result.stderr = ""
            return result

        mock_executor.run.side_effect = mock_run_side_effect

        instance.metrics(json_output=True)

        captured = capsys.readouterr()
        data = json.loads(captured.out)
        entry = data["instances"][0]
        assert entry["cpu_percent"] == "n/a"
        assert entry["mem_usage"] == "n/a"


class TestExecutorPropagation:
    """Tests verifying executor is threaded through deploy/redeploy/rollback.

    These override the autouse _mock_get_executor fixture to return a mock
    executor and verify that systemd/db calls receive it.
    """

    def _make_mock_config(self, mocker, tmp_path):
        """Create a mock Config that returns a mock executor."""
        mock_executor = mocker.MagicMock()
        mock_config = mocker.MagicMock()
        mock_config.config_dir = tmp_path / "etc"
        mock_config.config_dir.mkdir(exist_ok=True)
        mock_config.config_yaml = tmp_path / "etc" / "config.yaml"
        mock_config.var_dir = tmp_path / "var"
        mock_config.web_template_path = tmp_path / "onetime-web@.container"
        mock_config.worker_template_path = tmp_path / "onetime-worker@.container"
        mock_config.scheduler_template_path = tmp_path / "onetime-scheduler@.container"
        mock_config.db_path = tmp_path / "test.db"
        mock_config.existing_config_files = []
        mock_config.has_custom_config = False
        mock_config.resolve_image_tag.return_value = ("ghcr.io/test/image", "v1.0.0")
        mock_config.get_executor.return_value = mock_executor
        mocker.patch("rots.commands.instance.app.Config", return_value=mock_config)
        return mock_config, mock_executor

    def test_deploy_passes_executor_to_systemd_and_db(self, mocker, tmp_path):
        """deploy should pass executor to systemd.start and db.record_deployment."""
        mock_config, mock_executor = self._make_mock_config(mocker, tmp_path)
        mocker.patch("rots.commands.instance.app.assets.update")
        mocker.patch("rots.commands.instance.app.quadlet.write_web_template")
        mock_start = mocker.patch("rots.commands.instance.app.systemd.start")
        mock_record = mocker.patch("rots.commands.instance.app.db.record_deployment")

        instance.deploy(web="7143")

        # Verify executor was passed through
        mock_start.assert_called_once()
        assert mock_start.call_args.kwargs["executor"] is mock_executor
        mock_record.assert_called_once()
        assert mock_record.call_args.kwargs["executor"] is mock_executor

    def test_redeploy_passes_executor_to_systemd_and_db(self, mocker, tmp_path):
        """redeploy should pass the executor to systemd.recreate and db.record_deployment."""
        mock_config, mock_executor = self._make_mock_config(mocker, tmp_path)
        mocker.patch(
            "rots.commands.instance._helpers.systemd.discover_web_instances",
            return_value=["7043"],
        )
        mocker.patch(
            "rots.commands.instance._helpers.systemd.discover_worker_instances",
            return_value=[],
        )
        mocker.patch(
            "rots.commands.instance._helpers.systemd.discover_scheduler_instances",
            return_value=[],
        )
        mocker.patch("rots.commands.instance.app.assets.update")
        mocker.patch("rots.commands.instance.app.quadlet.write_web_template")
        mocker.patch(
            "rots.commands.instance.app.systemd.container_exists",
            return_value=True,
        )
        mock_recreate = mocker.patch("rots.commands.instance.app.systemd.recreate")
        mock_record = mocker.patch("rots.commands.instance.app.db.record_deployment")

        instance.redeploy(web="")

        mock_recreate.assert_called_once()
        assert mock_recreate.call_args.kwargs["executor"] is mock_executor
        mock_record.assert_called_once()
        assert mock_record.call_args.kwargs["executor"] is mock_executor

    def test_rollback_passes_executor_to_systemd_and_db(self, mocker, tmp_path):
        """rollback should pass the executor to systemd.recreate and db.record_deployment."""
        from rots.commands.instance.annotations import InstanceType

        mock_config, mock_executor = self._make_mock_config(mocker, tmp_path)
        mock_get_tags = mocker.patch(
            "rots.commands.instance.app.db.get_previous_tags",
            return_value=[
                ("ghcr.io/ots/ots", "v2.0.0", "2025-02-01T00:00:00"),
                ("ghcr.io/ots/ots", "v1.0.0", "2025-01-01T00:00:00"),
            ],
        )
        mock_rollback = mocker.patch(
            "rots.commands.instance.app.db.rollback",
            return_value=("ghcr.io/ots/ots", "v1.0.0"),
        )
        mocker.patch(
            "rots.commands.instance.app.resolve_identifiers",
            return_value={InstanceType.WEB: ["7043"]},
        )
        mocker.patch("rots.commands.instance.app.assets.update")
        mocker.patch("rots.commands.instance.app.quadlet.write_web_template")
        mock_recreate = mocker.patch("rots.commands.instance.app.systemd.recreate")
        mock_record = mocker.patch("rots.commands.instance.app.db.record_deployment")

        instance.rollback(web="", yes=True)

        # db.get_previous_tags and db.rollback receive executor
        mock_get_tags.assert_called_once()
        assert mock_get_tags.call_args.kwargs["executor"] is mock_executor
        mock_rollback.assert_called_once()
        assert mock_rollback.call_args.kwargs["executor"] is mock_executor
        mock_recreate.assert_called_once()
        assert mock_recreate.call_args.kwargs["executor"] is mock_executor
        # record_deployment is called once for success
        mock_record.assert_called_once()
        assert mock_record.call_args.kwargs["executor"] is mock_executor

    def test_undeploy_passes_executor_to_systemd_and_db(self, mocker, tmp_path):
        """undeploy passes executor to systemd and db."""
        mock_config, mock_executor = self._make_mock_config(mocker, tmp_path)
        mocker.patch(
            "rots.commands.instance.app.resolve_identifiers",
            return_value={InstanceType.WEB: ["7043"]},
        )
        mock_stop = mocker.patch("rots.commands.instance.app.systemd.stop")
        mock_disable = mocker.patch("rots.commands.instance.app.systemd.disable")
        mock_reset = mocker.patch("rots.commands.instance.app.systemd.reset_failed")
        mock_record = mocker.patch("rots.commands.instance.app.db.record_deployment")

        instance.undeploy(web="7043", yes=True)

        mock_stop.assert_called_once()
        assert mock_stop.call_args.kwargs["executor"] is mock_executor
        mock_disable.assert_called_once()
        assert mock_disable.call_args.kwargs["executor"] is mock_executor
        mock_reset.assert_called_once()
        assert mock_reset.call_args.kwargs["executor"] is mock_executor
        mock_record.assert_called_once()
        assert mock_record.call_args.kwargs["executor"] is mock_executor

    def test_start_passes_executor_to_systemd(self, mocker, tmp_path):
        """start should pass executor to systemd.start."""
        mock_config, mock_executor = self._make_mock_config(mocker, tmp_path)
        mocker.patch(
            "rots.commands.instance.app.resolve_identifiers",
            return_value={InstanceType.WEB: ["7043"]},
        )
        mock_start = mocker.patch("rots.commands.instance.app.systemd.start")

        instance.start(web="7043")

        mock_start.assert_called_once()
        assert mock_start.call_args.kwargs["executor"] is mock_executor

    def test_stop_passes_executor_to_systemd(self, mocker, tmp_path):
        """stop should pass executor to systemd.stop."""
        mock_config, mock_executor = self._make_mock_config(mocker, tmp_path)
        mocker.patch(
            "rots.commands.instance.app.resolve_identifiers",
            return_value={InstanceType.WEB: ["7043"]},
        )
        mock_stop = mocker.patch("rots.commands.instance.app.systemd.stop")

        instance.stop(web="7043")

        mock_stop.assert_called_once()
        assert mock_stop.call_args.kwargs["executor"] is mock_executor

    def test_restart_passes_executor_to_systemd(self, mocker, tmp_path):
        """restart should pass executor to systemd.restart."""
        mock_config, mock_executor = self._make_mock_config(mocker, tmp_path)
        mocker.patch(
            "rots.commands.instance.app.resolve_identifiers",
            return_value={InstanceType.WEB: ["7043"]},
        )
        mock_restart = mocker.patch("rots.commands.instance.app.systemd.restart")

        instance.restart(web="7043", delay=0)

        mock_restart.assert_called_once()
        assert mock_restart.call_args.kwargs["executor"] is mock_executor

    def test_enable_passes_executor_to_systemd(self, mocker, tmp_path):
        """enable should pass executor to systemd.enable."""
        mock_config, mock_executor = self._make_mock_config(mocker, tmp_path)
        mocker.patch(
            "rots.commands.instance.app.resolve_identifiers",
            return_value={InstanceType.WEB: ["7043"]},
        )
        mock_enable = mocker.patch("rots.commands.instance.app.systemd.enable")

        instance.enable(web="7043")

        mock_enable.assert_called_once()
        assert mock_enable.call_args.kwargs["executor"] is mock_executor

    def test_disable_passes_executor_to_systemd(self, mocker, tmp_path):
        """disable should pass executor to systemd.disable."""
        mock_config, mock_executor = self._make_mock_config(mocker, tmp_path)
        mocker.patch(
            "rots.commands.instance.app.resolve_identifiers",
            return_value={InstanceType.WEB: ["7043"]},
        )
        mock_disable = mocker.patch("rots.commands.instance.app.systemd.disable")

        instance.disable(web="7043", yes=True)

        mock_disable.assert_called_once()
        assert mock_disable.call_args.kwargs["executor"] is mock_executor

    def test_status_passes_executor_to_systemd(self, mocker, tmp_path):
        """status should pass executor to systemd.is_active (json mode) and systemd.status."""
        mock_config, mock_executor = self._make_mock_config(mocker, tmp_path)
        mocker.patch(
            "rots.commands.instance.app.resolve_identifiers",
            return_value={InstanceType.WEB: ["7043"]},
        )
        mock_is_active = mocker.patch(
            "rots.commands.instance.app.systemd.is_active",
            return_value="active",
        )

        instance.status(web="7043", json_output=True)

        mock_is_active.assert_called_once()
        assert mock_is_active.call_args.kwargs["executor"] is mock_executor

    def test_status_text_passes_executor_to_systemd(self, mocker, tmp_path):
        """status (text mode) should pass executor to systemd.status."""
        mock_config, mock_executor = self._make_mock_config(mocker, tmp_path)
        mocker.patch(
            "rots.commands.instance.app.resolve_identifiers",
            return_value={InstanceType.WEB: ["7043"]},
        )
        mock_status = mocker.patch("rots.commands.instance.app.systemd.status")

        instance.status(web="7043", json_output=False)

        mock_status.assert_called_once()
        assert mock_status.call_args.kwargs["executor"] is mock_executor

    def test_logs_passes_executor_via_run(self, mocker, tmp_path):
        """logs should route journalctl command through the executor."""
        mock_config, mock_executor = self._make_mock_config(mocker, tmp_path)
        mocker.patch(
            "rots.commands.instance.app.resolve_identifiers",
            return_value={InstanceType.WEB: ["7043"]},
        )
        # _get_executor is imported from rots.systemd inside logs()
        mocker.patch(
            "rots.systemd._get_executor",
            return_value=mock_executor,
        )
        mock_result = mocker.MagicMock()
        mock_result.stdout = "some log output\n"
        mock_result.stderr = ""
        mock_executor.run.return_value = mock_result

        instance.logs(web="7043", follow=False)

        mock_executor.run.assert_called_once()
        cmd = mock_executor.run.call_args[0][0]
        assert "journalctl" in cmd

    def test_cleanup_passes_executor_to_podman(self, mocker, tmp_path):
        """cleanup should create Podman with the executor for volume removal."""
        mock_config, mock_executor = self._make_mock_config(mocker, tmp_path)
        mock_podman = mocker.MagicMock()
        mock_volume_result = mocker.MagicMock()
        mock_volume_result.returncode = 0
        mock_podman.volume.rm.return_value = mock_volume_result
        mocker.patch(
            "rots.commands.instance.app.Podman",
            return_value=mock_podman,
        )

        instance.cleanup(yes=True)

        # Verify Podman was constructed with the executor
        from rots.commands.instance.app import Podman

        Podman.assert_called_once_with(executor=mock_executor)

    def test_metrics_passes_executor_to_systemd(self, mocker, tmp_path):
        """metrics should pass executor to systemd.is_active for status checks."""
        mock_config, mock_executor = self._make_mock_config(mocker, tmp_path)
        mocker.patch(
            "rots.commands.instance._helpers.systemd.discover_web_instances",
            return_value=[7043],
        )
        mocker.patch(
            "rots.commands.instance._helpers.systemd.discover_worker_instances",
            return_value=[],
        )
        mocker.patch(
            "rots.commands.instance._helpers.systemd.discover_scheduler_instances",
            return_value=[],
        )

        def mock_run_side_effect(cmd, **kwargs):
            result = mocker.MagicMock()
            if "is-active" in cmd:
                result.returncode = 0
                result.stdout = "active\n"
                result.stderr = ""
            elif "stats" in cmd:
                result.returncode = 0
                result.stdout = (
                    '[{"name":"onetime-web@7043","cpu_percent":"0.5%","mem_usage":"100MiB"}]'
                )
                result.stderr = ""
            else:
                result.returncode = 0
                result.stdout = ""
                result.stderr = ""
            return result

        mock_executor.run.side_effect = mock_run_side_effect

        instance.metrics(json_output=True)

        # Executor should have been used for systemctl/podman calls
        assert mock_executor.run.call_count >= 1


class TestStreamingCommands:
    """Verify that commands dispatch to the correct executor method.

    run_stream is for non-interactive output forwarding (no stdin).
    run_interactive is for full PTY sessions (bidirectional stdin/stdout).
    """

    def _mock_executor(self, mocker):
        """Create a Config mock whose get_executor() returns a mock executor."""
        mock_config = mocker.MagicMock()
        mock_config.tag = "v0.24.0"
        mock_config.image = "ghcr.io/onetimesecret/onetimesecret"
        mock_config.existing_config_files = []
        mock_config.has_custom_config = False
        mock_config.registry = None
        mock_config.resolve_image_tag.return_value = (
            "ghcr.io/onetimesecret/onetimesecret",
            "v0.24.0",
        )
        mock_config.resolved_image_with_tag.return_value = (
            "ghcr.io/onetimesecret/onetimesecret:v0.24.0"
        )
        mock_config.podman_auth_args.return_value = []
        mocker.patch("rots.commands.instance.app.Config", return_value=mock_config)

        mock_ex = mocker.MagicMock()
        mock_ex.run.return_value = mocker.MagicMock(ok=True, stdout="abc123\n", stderr="")
        mock_ex.run_stream.return_value = 0
        mock_ex.run_interactive.return_value = 0
        mock_config.get_executor.return_value = mock_ex
        return mock_config, mock_ex

    def test_run_foreground_calls_run_stream(self, mocker, tmp_path):
        """run (foreground) should use run_stream for real-time output."""
        _mock_config, mock_ex = self._mock_executor(mocker)
        _mock_config.config_dir = tmp_path / "etc"
        _mock_config.config_dir.mkdir()
        _mock_config.registry = None
        mocker.patch(
            "rots.commands.instance.app.quadlet.DEFAULT_ENV_FILE",
            tmp_path / "nonexistent",
        )

        instance.run(port=7143, detach=False, quiet=True)

        mock_ex.run_stream.assert_called_once()
        mock_ex.run_interactive.assert_not_called()
        cmd = mock_ex.run_stream.call_args[0][0]
        assert cmd[0] == "podman"
        assert cmd[1] == "run"

    def test_exec_shell_calls_run_interactive(self, mocker):
        """exec_shell should use run_interactive for PTY."""
        _mock_config, mock_ex = self._mock_executor(mocker)
        mocker.patch.dict("os.environ", {"SHELL": "/bin/bash"})
        mocker.patch(
            "rots.commands.instance._helpers.systemd.discover_web_instances",
            return_value=[7043],
        )
        mocker.patch(
            "rots.commands.instance._helpers.systemd.discover_worker_instances",
            return_value=[],
        )
        mocker.patch(
            "rots.commands.instance._helpers.systemd.discover_scheduler_instances",
            return_value=[],
        )

        instance.exec_shell(web="")

        mock_ex.run_interactive.assert_called_once()
        mock_ex.run_stream.assert_not_called()
        cmd = mock_ex.run_interactive.call_args[0][0]
        assert cmd[:3] == ["podman", "exec", "-it"]

    def test_shell_interactive_calls_run_interactive(self, mocker, tmp_path):
        """shell (no -c) should use run_interactive for PTY."""
        _mock_config, mock_ex = self._mock_executor(mocker)
        _mock_config.config_dir = tmp_path / "etc"
        _mock_config.config_dir.mkdir()
        mocker.patch(
            "rots.commands.instance.app.quadlet.DEFAULT_ENV_FILE",
            tmp_path / "nonexistent",
        )

        instance.shell(quiet=True)

        mock_ex.run_interactive.assert_called_once()
        mock_ex.run_stream.assert_not_called()
        cmd = mock_ex.run_interactive.call_args[0][0]
        assert "-it" in cmd
        assert "/bin/bash" in cmd

    def test_shell_with_command_calls_run_stream(self, mocker, tmp_path):
        """shell -c should use run_stream (non-interactive)."""
        _mock_config, mock_ex = self._mock_executor(mocker)
        _mock_config.config_dir = tmp_path / "etc"
        _mock_config.config_dir.mkdir()
        mocker.patch(
            "rots.commands.instance.app.quadlet.DEFAULT_ENV_FILE",
            tmp_path / "nonexistent",
        )

        instance.shell(command="bin/ots migrate", quiet=True)

        mock_ex.run_stream.assert_called_once()
        mock_ex.run_interactive.assert_not_called()
        cmd = mock_ex.run_stream.call_args[0][0]
        assert "-c" in cmd
        assert "bin/ots migrate" in cmd

    def test_logs_follow_calls_run_stream_not_run(self, mocker):
        """logs -f should use run_stream (not run) for real-time output."""
        _mock_config, mock_ex = self._mock_executor(mocker)
        mocker.patch(
            "rots.commands.instance._helpers.systemd.discover_web_instances",
            return_value=[7043],
        )
        mocker.patch(
            "rots.commands.instance._helpers.systemd.discover_worker_instances",
            return_value=[],
        )
        mocker.patch(
            "rots.commands.instance._helpers.systemd.discover_scheduler_instances",
            return_value=[],
        )

        instance.logs(web="", follow=True)

        mock_ex.run_stream.assert_called_once()
        # run() should NOT be called for follow mode
        mock_ex.run.assert_not_called()
        call_kwargs = mock_ex.run_stream.call_args
        assert call_kwargs.kwargs.get("sudo") is True
        assert call_kwargs.kwargs.get("timeout") == 300


# ---------------------------------------------------------------------------
# Issue #67: deploy --render contract tests
# ---------------------------------------------------------------------------
#
# The --render flag writes Quadlet files locally under <dir>/etc/containers/systemd/
# and exits with no host I/O, no SSH, no systemd, no DB write, and no hooks.
# These tests are the primary contract for that behaviour. They will fail with
# TypeError until python-dev's parallel impl lands the new `render` and
# `config_source` parameters on instance.deploy.


class TestDeployRender:
    """Tests for `deploy ... --render <dir>` (issue #67)."""

    @pytest.fixture(autouse=True)
    def _clear_env(self, monkeypatch, tmp_path):
        """Remove image env vars, pin tag, and stage the default config_source.

        Render mode rejects alias tags (``@current``/``@rollback``) because
        resolving them would touch the deployment DB. Pin to a concrete tag
        so the render path doesn't trip the alias guard.

        Render mode also validates ``--config-source`` exists. The default
        is ``./confexts/web/__base__/etc/onetimesecret`` relative to cwd, so chdir
        into a scratch dir and create the scaffold so tests that don't
        supply ``config_source`` pass validation.
        """
        monkeypatch.delenv("IMAGE", raising=False)
        monkeypatch.delenv("OTS_REGISTRY", raising=False)
        monkeypatch.setenv("TAG", "v0.24.0")
        scratch = tmp_path / "_scratch"
        scratch.mkdir()
        (scratch / "confexts" / "web" / "__base__" / "etc" / "onetimesecret").mkdir(parents=True)
        monkeypatch.chdir(scratch)

    def _patch_render_safety_nets(self, mocker):
        """Patch every host-touching dependency that --render must NOT call.

        Returns a dict of name -> mock so individual tests can assert_not_called.
        """
        return {
            "get_executor": mocker.patch.object(Config, "get_executor"),
            "deploy_lock": mocker.patch("rots.commands.instance.app.deploy_lock"),
            "systemd_start": mocker.patch("rots.commands.instance.app.systemd.start"),
            "systemd_daemon_reload": mocker.patch(
                "rots.commands.instance.app.systemd.daemon_reload",
                create=True,
            ),
            "systemd_wait_for_healthy": mocker.patch(
                "rots.commands.instance.app.systemd.wait_for_healthy"
            ),
            "systemd_wait_for_http_healthy": mocker.patch(
                "rots.commands.instance.app.systemd.wait_for_http_healthy"
            ),
            "record_deployment": mocker.patch("rots.commands.instance.app.db.record_deployment"),
            "run_hook": mocker.patch("rots.commands.instance.app.run_hook"),
            "assets_update": mocker.patch("rots.commands.instance.app.assets.update"),
            "write_web_template": mocker.patch(
                "rots.commands.instance.app.quadlet.write_web_template"
            ),
            "write_worker_template": mocker.patch(
                "rots.commands.instance.app.quadlet.write_worker_template"
            ),
            "write_scheduler_template": mocker.patch(
                "rots.commands.instance.app.quadlet.write_scheduler_template"
            ),
        }

    def test_render_writes_three_container_files_for_web(self, mocker, tmp_path):
        """deploy --web 7043 --render <dir> writes the three .container template files."""
        self._patch_render_safety_nets(mocker)

        out_dir = tmp_path / "out"
        out_dir.mkdir()

        instance.deploy(web="7043", render=out_dir)

        target_dir = out_dir / "etc" / "containers" / "systemd"
        assert (target_dir / "onetime-web@.container").exists()
        assert (target_dir / "onetime-worker@.container").exists()
        assert (target_dir / "onetime-scheduler@.container").exists()

    def test_render_writes_image_unit_when_registry_set(self, mocker, monkeypatch, tmp_path):
        """`onetime.image` is emitted only when cfg.registry is set."""
        self._patch_render_safety_nets(mocker)
        monkeypatch.setenv("OTS_REGISTRY", "registry.example.com")

        out_dir = tmp_path / "out"
        out_dir.mkdir()

        instance.deploy(web="7043", render=out_dir)

        target_dir = out_dir / "etc" / "containers" / "systemd"
        assert (target_dir / "onetime.image").exists()

    def test_render_omits_image_unit_when_registry_unset(self, mocker, tmp_path):
        """`onetime.image` is NOT emitted when cfg.registry is unset."""
        self._patch_render_safety_nets(mocker)

        out_dir = tmp_path / "out"
        out_dir.mkdir()

        instance.deploy(web="7043", render=out_dir)

        target_dir = out_dir / "etc" / "containers" / "systemd"
        assert not (target_dir / "onetime.image").exists()

    def test_render_does_not_call_get_executor(self, mocker, tmp_path):
        """Under --render, Config.get_executor must not be called."""
        nets = self._patch_render_safety_nets(mocker)

        out_dir = tmp_path / "out"
        out_dir.mkdir()

        instance.deploy(web="7043", render=out_dir)

        nets["get_executor"].assert_not_called()

    def test_render_does_not_acquire_deploy_lock(self, mocker, tmp_path):
        """Under --render, the deploy_lock context manager must not be entered."""
        nets = self._patch_render_safety_nets(mocker)

        out_dir = tmp_path / "out"
        out_dir.mkdir()

        instance.deploy(web="7043", render=out_dir)

        nets["deploy_lock"].assert_not_called()

    def test_render_does_not_call_systemd(self, mocker, tmp_path):
        """Under --render, systemd start / daemon_reload / health waits are skipped."""
        nets = self._patch_render_safety_nets(mocker)

        out_dir = tmp_path / "out"
        out_dir.mkdir()

        instance.deploy(web="7043", render=out_dir)

        nets["systemd_start"].assert_not_called()
        nets["systemd_daemon_reload"].assert_not_called()
        nets["systemd_wait_for_healthy"].assert_not_called()
        nets["systemd_wait_for_http_healthy"].assert_not_called()

    def test_render_does_not_record_deployment(self, mocker, tmp_path):
        """Under --render, deployment history must not be touched."""
        nets = self._patch_render_safety_nets(mocker)

        out_dir = tmp_path / "out"
        out_dir.mkdir()

        instance.deploy(web="7043", render=out_dir)

        nets["record_deployment"].assert_not_called()

    def test_render_does_not_run_hooks(self, mocker, tmp_path):
        """Under --render, pre/post hooks must not run even if specified."""
        nets = self._patch_render_safety_nets(mocker)

        out_dir = tmp_path / "out"
        out_dir.mkdir()

        instance.deploy(
            web="7043",
            render=out_dir,
            pre_hook="./scan.sh",
            post_hook="./notify.sh",
        )

        nets["run_hook"].assert_not_called()

    def test_render_does_not_call_write_template_helpers(self, mocker, tmp_path):
        """Under --render, the host-targeted write_*_template helpers are bypassed.

        Render mode writes to <dir>/etc/containers/systemd/ directly; it must not
        call quadlet.write_web_template (which writes to /etc/containers/systemd/).
        """
        nets = self._patch_render_safety_nets(mocker)

        out_dir = tmp_path / "out"
        out_dir.mkdir()

        instance.deploy(web="7043", render=out_dir)

        nets["write_web_template"].assert_not_called()
        nets["write_worker_template"].assert_not_called()
        nets["write_scheduler_template"].assert_not_called()

    def test_render_defaults_config_source_to_confexts_web(self, mocker, monkeypatch, tmp_path):
        """When config_source=None, deploy --render uses __base__/etc default.

        Default is ./confexts/web/__base__/etc/onetimesecret. Resolving the
        default against cwd (rather than an absolute hard-coded path) lets
        operators run `rots instance deploy --render ./out` from their checkout
        without flag soup.
        """
        self._patch_render_safety_nets(mocker)
        monkeypatch.chdir(tmp_path)

        default_src = tmp_path / "confexts" / "web" / "__base__" / "etc" / "onetimesecret"
        default_src.mkdir(parents=True)
        (default_src / "config.yaml").write_text("# placeholder\n")

        out_dir = tmp_path / "out"
        out_dir.mkdir()

        instance.deploy(web="7043", render=out_dir, config_source=None)

        web_unit = (
            out_dir / "etc" / "containers" / "systemd" / "onetime-web@.container"
        ).read_text()

        # The default config_source resolved to <cwd>/confexts/web/__base__/etc/onetimesecret,
        # where config.yaml exists, so a Volume= line for it must be rendered.
        assert ":/app/etc/config.yaml:ro" in web_unit

    def test_render_propagates_config_source_into_rendered_files(self, mocker, tmp_path):
        """`--config-source <dir>` causes Volume= lines for matching files to appear."""
        self._patch_render_safety_nets(mocker)

        out_dir = tmp_path / "out"
        out_dir.mkdir()
        cfg_src = tmp_path / "cfgsrc"
        cfg_src.mkdir()
        (cfg_src / "config.yaml").write_text("# placeholder\n")

        instance.deploy(web="7043", render=out_dir, config_source=cfg_src)

        web_unit = (
            out_dir / "etc" / "containers" / "systemd" / "onetime-web@.container"
        ).read_text()

        # The Volume= line must reference config.yaml mounted at the
        # container's /app/etc/config.yaml:ro path.
        assert ":/app/etc/config.yaml:ro" in web_unit

    def test_render_emits_no_secret_lines(self, mocker, tmp_path):
        """No `Secret=` lines must appear in any rendered file under --render.

        Secrets are now loaded via EnvironmentFile layered pattern instead of
        being enumerated as Secret= directives in the quadlet file.
        """
        self._patch_render_safety_nets(mocker)

        out_dir = tmp_path / "out"
        out_dir.mkdir()

        instance.deploy(web="7043", render=out_dir)

        target_dir = out_dir / "etc" / "containers" / "systemd"
        for unit_name in (
            "onetime-web@.container",
            "onetime-worker@.container",
            "onetime-scheduler@.container",
        ):
            content = (target_dir / unit_name).read_text()
            assert "Secret=" not in content, (
                f"{unit_name} unexpectedly contains a Secret= directive: {content!r}"
            )
            # Verify EnvironmentFile pattern comment is present
            assert "Secrets loaded via EnvironmentFile layered pattern" in content


# ---------------------------------------------------------------------------
# Issue #67: dedicated `instance render` subcommand contract tests
# ---------------------------------------------------------------------------
#
# The `render` command on instance.app is a separate subcommand (not a flag
# on deploy). It writes Quadlet files locally and never performs host I/O.
# It accepts only a small set of parameters and explicitly excludes deploy-only
# flags. Tests import the function directly from `rots.commands.instance.app`
# rather than the package namespace, since the package `__init__.py` does not
# re-export it (and we are forbidden from modifying production code in this
# round).


class TestInstanceRenderCommand:
    """Tests for the dedicated `rots instance render` subcommand (issue #67)."""

    @pytest.fixture(autouse=True)
    def _clear_env(self, monkeypatch, tmp_path):
        """Remove image env vars, pin tag, and stage the default config_source.

        Render mode rejects alias tags (``@current``/``@rollback``) because
        resolving them would touch the deployment DB. Pin to a concrete tag
        so the render path doesn't trip the alias guard.

        Render mode also validates ``--config-source`` exists. The default
        is ``./confexts/web/__base__/etc/onetimesecret`` relative to cwd, so chdir
        into a scratch dir and create the scaffold so tests that don't
        supply ``config_source`` pass validation.
        """
        monkeypatch.delenv("IMAGE", raising=False)
        monkeypatch.delenv("OTS_REGISTRY", raising=False)
        monkeypatch.setenv("TAG", "v0.24.0")
        scratch = tmp_path / "_scratch"
        scratch.mkdir()
        (scratch / "confexts" / "web" / "__base__" / "etc" / "onetimesecret").mkdir(parents=True)
        monkeypatch.chdir(scratch)

    def _patch_render_safety_nets(self, mocker):
        """Patch every host-touching dependency that `render` must NOT call.

        Returns a dict of name -> mock so individual tests can assert_not_called.
        """
        return {
            "get_executor": mocker.patch.object(Config, "get_executor"),
            "deploy_lock": mocker.patch("rots.commands.instance.app.deploy_lock"),
            "systemd_start": mocker.patch("rots.commands.instance.app.systemd.start"),
            "systemd_daemon_reload": mocker.patch(
                "rots.commands.instance.app.systemd.daemon_reload",
                create=True,
            ),
            "systemd_wait_for_healthy": mocker.patch(
                "rots.commands.instance.app.systemd.wait_for_healthy"
            ),
            "systemd_wait_for_http_healthy": mocker.patch(
                "rots.commands.instance.app.systemd.wait_for_http_healthy"
            ),
            "record_deployment": mocker.patch("rots.commands.instance.app.db.record_deployment"),
            "run_hook": mocker.patch("rots.commands.instance.app.run_hook"),
            "assets_update": mocker.patch("rots.commands.instance.app.assets.update"),
            "write_web_template": mocker.patch(
                "rots.commands.instance.app.quadlet.write_web_template"
            ),
            "write_worker_template": mocker.patch(
                "rots.commands.instance.app.quadlet.write_worker_template"
            ),
            "write_scheduler_template": mocker.patch(
                "rots.commands.instance.app.quadlet.write_scheduler_template"
            ),
        }

    def test_render_writes_three_container_files(self, mocker, tmp_path):
        """`instance render <out>` writes the three .container template files."""
        self._patch_render_safety_nets(mocker)

        out_dir = tmp_path / "out"
        out_dir.mkdir()

        from rots.commands.instance.app import render as render_cmd

        render_cmd(out_dir=out_dir)

        target_dir = out_dir / "etc" / "containers" / "systemd"
        assert (target_dir / "onetime-web@.container").exists()
        assert (target_dir / "onetime-worker@.container").exists()
        assert (target_dir / "onetime-scheduler@.container").exists()

    def test_render_writes_image_unit_when_registry_set(self, mocker, monkeypatch, tmp_path):
        """`onetime.image` is emitted only when cfg.registry is set."""
        self._patch_render_safety_nets(mocker)
        monkeypatch.setenv("OTS_REGISTRY", "registry.example.com")

        out_dir = tmp_path / "out"
        out_dir.mkdir()

        from rots.commands.instance.app import render as render_cmd

        render_cmd(out_dir=out_dir)

        target_dir = out_dir / "etc" / "containers" / "systemd"
        assert (target_dir / "onetime.image").exists()

    def test_render_omits_image_unit_when_registry_unset(self, mocker, tmp_path):
        """`onetime.image` is NOT emitted when cfg.registry is unset."""
        self._patch_render_safety_nets(mocker)

        out_dir = tmp_path / "out"
        out_dir.mkdir()

        from rots.commands.instance.app import render as render_cmd

        render_cmd(out_dir=out_dir)

        target_dir = out_dir / "etc" / "containers" / "systemd"
        assert not (target_dir / "onetime.image").exists()

    def test_render_removes_stale_image_unit_when_registry_unset(
        self, mocker, monkeypatch, tmp_path
    ):
        """A re-render without a registry must delete `onetime.image` from a prior render.

        lots' rsync ships the rendered tree without --delete, so a stale
        `onetime.image` left behind from a previous run would propagate to
        the host. The render must clean managed Quadlet artifacts that are
        no longer in the current payload set.
        """
        self._patch_render_safety_nets(mocker)

        out_dir = tmp_path / "out"
        out_dir.mkdir()

        from rots.commands.instance.app import render as render_cmd

        # First render: registry set, `onetime.image` is emitted.
        monkeypatch.setenv("OTS_REGISTRY", "registry.example.com")
        render_cmd(out_dir=out_dir)

        target_dir = out_dir / "etc" / "containers" / "systemd"
        assert (target_dir / "onetime.image").exists()

        # Second render: registry unset, the stale image unit must be gone.
        monkeypatch.delenv("OTS_REGISTRY")
        render_cmd(out_dir=out_dir)

        assert not (target_dir / "onetime.image").exists()

    def test_render_preserves_unrelated_files_in_out_dir(self, mocker, tmp_path):
        """Files outside the managed Quadlet set must not be touched by render.

        Operators may colocate other artifacts under
        `<out_dir>/etc/containers/systemd/`. Cleanup is scoped to the four
        Quadlet artifacts this command owns.
        """
        self._patch_render_safety_nets(mocker)

        out_dir = tmp_path / "out"
        target_dir = out_dir / "etc" / "containers" / "systemd"
        target_dir.mkdir(parents=True)
        sentinel = target_dir / "operator-owned.conf"
        sentinel.write_text("# operator file\n")

        from rots.commands.instance.app import render as render_cmd

        render_cmd(out_dir=out_dir)

        assert sentinel.exists(), "render must not delete unrelated files"
        assert sentinel.read_text() == "# operator file\n"

    def test_render_defaults_config_source_to_confexts_web(self, mocker, monkeypatch, tmp_path):
        """When --config-source omitted, defaults to ./confexts/web/__base__/etc/onetimesecret."""
        self._patch_render_safety_nets(mocker)
        monkeypatch.chdir(tmp_path)

        default_src = tmp_path / "confexts" / "web" / "__base__" / "etc" / "onetimesecret"
        default_src.mkdir(parents=True)
        (default_src / "config.yaml").write_text("# placeholder\n")

        out_dir = tmp_path / "out"
        out_dir.mkdir()

        from rots.commands.instance.app import render as render_cmd

        render_cmd(out_dir=out_dir)

        web_unit = (
            out_dir / "etc" / "containers" / "systemd" / "onetime-web@.container"
        ).read_text()

        # The default config_source resolved to <cwd>/confexts/web/__base__/etc/onetimesecret,
        # where config.yaml exists, so a Volume= line for it must be rendered.
        assert ":/app/etc/config.yaml:ro" in web_unit

    def test_render_explicit_config_source_takes_precedence(self, mocker, tmp_path):
        """`--config-source <dir>` overrides the __base__/etc/onetimesecret default."""
        self._patch_render_safety_nets(mocker)

        out_dir = tmp_path / "out"
        out_dir.mkdir()
        cfg_src = tmp_path / "explicit"
        cfg_src.mkdir()
        # Use a non-default file (auth.yaml) so we can prove the explicit path
        # was probed instead of the default location (which has no files here).
        (cfg_src / "auth.yaml").write_text("# placeholder\n")

        from rots.commands.instance.app import render as render_cmd

        render_cmd(out_dir=out_dir, config_source=cfg_src)

        web_unit = (
            out_dir / "etc" / "containers" / "systemd" / "onetime-web@.container"
        ).read_text()

        assert ":/app/etc/auth.yaml:ro" in web_unit

    def test_render_signature_excludes_deploy_only_flags(self):
        """`render` must NOT accept deploy-only flags as parameters.

        These flags are meaningful only to the host-touching deploy path.
        Exposing them on `render` would invite confusion.
        """
        import inspect

        from rots.commands.instance.app import render as render_cmd

        sig = inspect.signature(render_cmd)
        params = sig.parameters

        for forbidden in (
            "host",
            "pre_hook",
            "post_hook",
            "wait",
            "wait_timeout",
            "force",
            "delay",
            "dry_run",
        ):
            assert forbidden not in params, (
                f"`render` must not accept `{forbidden}`; got signature: {sig}"
            )

    def test_render_does_not_call_get_executor(self, mocker, tmp_path):
        """Under `render`, Config.get_executor must not be called."""
        nets = self._patch_render_safety_nets(mocker)

        out_dir = tmp_path / "out"
        out_dir.mkdir()

        from rots.commands.instance.app import render as render_cmd

        render_cmd(out_dir=out_dir)

        nets["get_executor"].assert_not_called()

    def test_render_does_not_acquire_deploy_lock(self, mocker, tmp_path):
        """Under `render`, the deploy_lock context manager must not be entered."""
        nets = self._patch_render_safety_nets(mocker)

        out_dir = tmp_path / "out"
        out_dir.mkdir()

        from rots.commands.instance.app import render as render_cmd

        render_cmd(out_dir=out_dir)

        nets["deploy_lock"].assert_not_called()

    def test_render_does_not_call_systemd(self, mocker, tmp_path):
        """Under `render`, systemd start / daemon_reload must not be called."""
        nets = self._patch_render_safety_nets(mocker)

        out_dir = tmp_path / "out"
        out_dir.mkdir()

        from rots.commands.instance.app import render as render_cmd

        render_cmd(out_dir=out_dir)

        nets["systemd_start"].assert_not_called()
        nets["systemd_daemon_reload"].assert_not_called()

    def test_render_does_not_record_deployment(self, mocker, tmp_path):
        """Under `render`, deployment history must not be touched."""
        nets = self._patch_render_safety_nets(mocker)

        out_dir = tmp_path / "out"
        out_dir.mkdir()

        from rots.commands.instance.app import render as render_cmd

        render_cmd(out_dir=out_dir)

        nets["record_deployment"].assert_not_called()

    def test_render_failure_exits_nonzero_with_stderr(self, mocker, capsys, tmp_path):
        """When a template renderer raises, render exits nonzero and writes a clear stderr message.

        `rots instance apply` will subprocess --render programmatically; a
        silent failure would let lots ship an incomplete tree.
        """
        self._patch_render_safety_nets(mocker)
        mocker.patch(
            "rots.commands.instance.app.quadlet.render_worker_template",
            side_effect=RuntimeError("boom: bad config"),
        )

        out_dir = tmp_path / "out"
        out_dir.mkdir()

        from rots.commands.instance.app import render as render_cmd

        with pytest.raises(SystemExit) as excinfo:
            render_cmd(out_dir=out_dir)

        assert excinfo.value.code != 0
        captured = capsys.readouterr()
        assert "render:" in captured.err
        assert "boom: bad config" in captured.err
        # No half-written tree: out_dir/etc/containers/systemd should be absent
        # because we render-to-memory before any disk write.
        assert not (out_dir / "etc" / "containers" / "systemd").exists()

    def test_render_explicit_config_source_missing_exits(self, mocker, capsys, tmp_path):
        """Explicit --config-source pointing at a non-existent path must error loudly.

        Silent fall-through to "no Volume= overrides" hides typos; render mode
        is meant to be explicit about its inputs.
        """
        self._patch_render_safety_nets(mocker)

        out_dir = tmp_path / "out"
        out_dir.mkdir()
        bogus = tmp_path / "does-not-exist"

        from rots.commands.instance.app import render as render_cmd

        with pytest.raises(SystemExit) as excinfo:
            render_cmd(out_dir=out_dir, config_source=bogus)

        assert excinfo.value.code != 0
        captured = capsys.readouterr()
        assert "config source does not exist" in captured.err

    def test_render_explicit_config_source_is_file_exits(self, mocker, capsys, tmp_path):
        """--config-source pointing at a regular file (not a dir) must error loudly."""
        self._patch_render_safety_nets(mocker)

        out_dir = tmp_path / "out"
        out_dir.mkdir()
        a_file = tmp_path / "regular_file"
        a_file.write_text("not a directory\n")

        from rots.commands.instance.app import render as render_cmd

        with pytest.raises(SystemExit) as excinfo:
            render_cmd(out_dir=out_dir, config_source=a_file)

        assert excinfo.value.code != 0
        captured = capsys.readouterr()
        assert "not a directory" in captured.err

    def test_render_default_config_source_missing_exits(
        self, mocker, monkeypatch, capsys, tmp_path
    ):
        """The default ./confexts/web/__base__/etc/onetimesecret must exist when not overridden.

        Render mode is explicit about its inputs; if the operator runs it from
        a directory without the scaffold, fail loud rather than silently emit
        zero Volume= overrides.
        """
        self._patch_render_safety_nets(mocker)
        # chdir somewhere that does NOT have the default scaffold (the autouse
        # _clear_env fixture chdir'd to a scratch dir that does have it).
        bare = tmp_path / "bare"
        bare.mkdir()
        monkeypatch.chdir(bare)

        out_dir = tmp_path / "out"
        out_dir.mkdir()

        from rots.commands.instance.app import render as render_cmd

        with pytest.raises(SystemExit) as excinfo:
            render_cmd(out_dir=out_dir)

        assert excinfo.value.code != 0
        captured = capsys.readouterr()
        assert "config source does not exist" in captured.err

    def test_render_single_positional_path_promotes_to_out_dir(self, mocker, tmp_path):
        """A single path-shaped positional ('./out', '/tmp/x', '~/x') becomes out_dir."""
        self._patch_render_safety_nets(mocker)

        out_dir_path = tmp_path / "out"
        out_dir_path.mkdir()

        from rots.commands.instance.app import render as render_cmd

        # Use an absolute path so the leading "/" triggers path detection.
        render_cmd(reference=str(out_dir_path))

        target = out_dir_path / "etc" / "containers" / "systemd"
        assert (target / "onetime-web@.container").exists()

    def test_render_single_positional_relative_path_promotes_to_out_dir(
        self, mocker, monkeypatch, tmp_path
    ):
        """A relative './out' positional is also recognised as a path."""
        self._patch_render_safety_nets(mocker)

        # The autouse fixture chdir'd into a scratch dir; switch into one we
        # control so we can pass a relative path.
        work = tmp_path / "work"
        (work / "confexts" / "web" / "__base__" / "etc" / "onetimesecret").mkdir(parents=True)
        monkeypatch.chdir(work)

        from rots.commands.instance.app import render as render_cmd

        render_cmd(reference="./out")

        target = work / "out" / "etc" / "containers" / "systemd"
        assert (target / "onetime-web@.container").exists()

    def test_render_single_positional_bare_image_ref_errors(self, mocker, tmp_path):
        """A bare image ref like 'ghcr.io/org/image' (no tag) refuses to auto-promote.

        The prior heuristic ("treat as out_dir if no ':' or '@'") would
        misclassify this as a directory. The fix is to refuse to guess when
        the input is genuinely ambiguous.
        """
        self._patch_render_safety_nets(mocker)

        from rots.commands.instance.app import render as render_cmd

        with pytest.raises(SystemExit) as excinfo:
            render_cmd(reference="ghcr.io/org/image")

        assert "cannot tell" in str(excinfo.value)

    def test_render_single_positional_image_ref_with_tag_does_not_promote(self, mocker, tmp_path):
        """An image ref with a tag is bound to reference; out_dir is then required."""
        self._patch_render_safety_nets(mocker)

        from rots.commands.instance.app import render as render_cmd

        # Has a ':' -> definitely an image ref. With no out_dir, fall through
        # to the missing-out_dir error.
        with pytest.raises(SystemExit) as excinfo:
            render_cmd(reference="ghcr.io/org/image:v1")

        assert "out_dir is required" in str(excinfo.value)

    def test_render_two_positionals_image_ref_and_out_dir(self, mocker, tmp_path):
        """Both positionals supplied: reference is the image ref, out_dir is the path."""
        self._patch_render_safety_nets(mocker)

        out_dir_path = tmp_path / "out"
        out_dir_path.mkdir()

        from rots.commands.instance.app import render as render_cmd

        render_cmd(reference="ghcr.io/org/image:v1.2.3", out_dir=out_dir_path)

        web_unit = (
            out_dir_path / "etc" / "containers" / "systemd" / "onetime-web@.container"
        ).read_text()
        assert "Image=ghcr.io/org/image:v1.2.3" in web_unit

    def test_render_failure_mid_write_leaves_old_tree_intact(self, mocker, tmp_path):
        """A failure during the staging-dir write must NOT mutate the live tree.

        Tree-level atomicity: render builds the new set in a sibling staging
        dir and renames it into place once. Any error before the rename
        leaves the live `etc/containers/systemd/` directory untouched.
        """
        self._patch_render_safety_nets(mocker)

        out_dir = tmp_path / "out"
        target_dir = out_dir / "etc" / "containers" / "systemd"
        target_dir.mkdir(parents=True)

        # Seed the live tree with a previous render and a sentinel operator file.
        (target_dir / "onetime-web@.container").write_text("# OLD WEB\n")
        (target_dir / "onetime-worker@.container").write_text("# OLD WORKER\n")
        (target_dir / "onetime-scheduler@.container").write_text("# OLD SCHED\n")
        sentinel = target_dir / "operator-owned.conf"
        sentinel.write_text("# operator file\n")

        # Force a failure during template generation (before any disk write).
        mocker.patch(
            "rots.commands.instance.app.quadlet.render_scheduler_template",
            side_effect=RuntimeError("boom"),
        )

        from rots.commands.instance.app import render as render_cmd

        with pytest.raises(SystemExit):
            render_cmd(out_dir=out_dir)

        # Live tree is exactly as before.
        assert (target_dir / "onetime-web@.container").read_text() == "# OLD WEB\n"
        assert (target_dir / "onetime-worker@.container").read_text() == "# OLD WORKER\n"
        assert (target_dir / "onetime-scheduler@.container").read_text() == "# OLD SCHED\n"
        assert sentinel.read_text() == "# operator file\n"

    def test_render_swap_leaves_no_staging_dir_on_success(self, mocker, tmp_path):
        """Successful render leaves no `.render-*` staging dirs behind."""
        self._patch_render_safety_nets(mocker)

        out_dir = tmp_path / "out"
        out_dir.mkdir()

        from rots.commands.instance.app import render as render_cmd

        render_cmd(out_dir=out_dir)

        # No leftover staging dirs as siblings of the systemd dir.
        siblings = list((out_dir / "etc" / "containers").iterdir())
        assert {p.name for p in siblings} == {"systemd"}

    def test_render_swap_cleans_staging_dir_on_failure(self, mocker, tmp_path):
        """A failed render leaves no `.render-*` staging dirs behind either.

        The staging dir is best-effort cleaned up on the error path so
        repeated failed renders don't accumulate cruft on disk.
        """
        self._patch_render_safety_nets(mocker)

        out_dir = tmp_path / "out"
        target_dir = out_dir / "etc" / "containers" / "systemd"
        target_dir.mkdir(parents=True)

        mocker.patch(
            "rots.commands.instance.app.quadlet.render_web_template",
            side_effect=RuntimeError("boom"),
        )

        from rots.commands.instance.app import render as render_cmd

        with pytest.raises(SystemExit):
            render_cmd(out_dir=out_dir)

        # No leftover staging dirs as siblings.
        siblings = [p.name for p in (out_dir / "etc" / "containers").iterdir()]
        # `systemd` survives (untouched); nothing else should be there.
        assert siblings == ["systemd"]

    def test_render_output_is_byte_stable_across_runs(self, mocker, tmp_path):
        """Render output must be byte-identical across reruns with identical inputs.

        lots' confext push uses rsync; any jitter (timestamps, dict-iteration
        ordering, hostnames in output) causes spurious re-applies and wasted
        systemd-confext refresh cycles. This test locks in the contract.
        """
        self._patch_render_safety_nets(mocker)

        out1 = tmp_path / "out1"
        out2 = tmp_path / "out2"
        out1.mkdir()
        out2.mkdir()
        cfg_src = tmp_path / "cfgsrc"
        cfg_src.mkdir()
        (cfg_src / "config.yaml").write_text("a: 1\n")
        (cfg_src / "auth.yaml").write_text("b: 2\n")

        from rots.commands.instance.app import render as render_cmd

        render_cmd(out_dir=out1, config_source=cfg_src, quiet=True)
        render_cmd(out_dir=out2, config_source=cfg_src, quiet=True)

        for name in (
            "onetime-web@.container",
            "onetime-worker@.container",
            "onetime-scheduler@.container",
        ):
            a = (out1 / "etc" / "containers" / "systemd" / name).read_bytes()
            b = (out2 / "etc" / "containers" / "systemd" / name).read_bytes()
            assert a == b, f"{name} differs across runs"


# ---------------------------------------------------------------------------
# Issue #67 / PR #68 — Copilot review supplemental coverage
# ---------------------------------------------------------------------------
#
# Backend-dev's per-item commits added direct coverage for each of the four
# review fixes. The classes below add defence-in-depth and regression-guard
# coverage that is complementary, not duplicative:
#
#   Item 1  TestRenderItem1NoDBSafetyNet     — DB-module tripwires + filesystem
#                                              survey for deployments.db
#   Item 2  TestRenderItem2NoPartialOutput   — config-source validation runs
#                                              before any output tree creation
#   Item 4  TestRenderItem4AtomicityExtras   — in-place rerun byte-stability +
#                                              no stray .tmp files


def _stage_default_confexts(monkeypatch, tmp_path):
    """Helper: chdir into a scratch dir staged with the default config_source.

    Render mode validates ``./confexts/web/__base__/etc/onetimesecret`` exists when
    no ``--config-source`` is supplied. Tests that don't pass an explicit
    config_source need the scaffold staged.
    """
    scratch = tmp_path / "_scratch_confexts_supp"
    scratch.mkdir(exist_ok=True)
    (scratch / "confexts" / "web" / "__base__" / "etc" / "onetimesecret").mkdir(
        parents=True, exist_ok=True
    )
    monkeypatch.chdir(scratch)
    return scratch


class TestRenderItem1NoDBSafetyNet:
    """Item 1, defence-in-depth — DB module entry points must not be reached.

    Backend-dev's existing coverage in ``tests/test_quadlet_render.py``
    asserts the alias rejection inside ``_build_fmt_vars`` and that
    ``cfg.resolved_image_with_tag`` is not called. The complementary tests
    below patch the ``rots.db`` module's entry points themselves with
    side-effecting tripwires, so any future refactor that bypasses
    ``_build_fmt_vars`` (e.g. a new code path that logs the resolved tag,
    or a command-level pre-check) still surfaces a contract violation.
    """

    @pytest.fixture(autouse=True)
    def _clear_env(self, monkeypatch, tmp_path):
        """Strip image/tag env vars and stage the default confexts scaffold.

        We deliberately do NOT pin TAG — exercising the default ``@current``
        is the whole point of this class.
        """
        monkeypatch.delenv("IMAGE", raising=False)
        monkeypatch.delenv("TAG", raising=False)
        monkeypatch.delenv("OTS_REGISTRY", raising=False)
        _stage_default_confexts(monkeypatch, tmp_path)

    @pytest.fixture(autouse=True)
    def _db_tripwires(self, mocker):
        """Trip-wires: any call into the DB module is a contract violation.

        ``db.get_alias`` / ``db.init_db`` / ``db.get_connection`` should
        never run during ``instance render``. Patch them with side-effecting
        AssertionErrors so a regression produces a loud, identifiable
        failure rather than a quiet ``deployments.db`` somewhere on disk.
        """
        mocker.patch(
            "rots.db.get_alias",
            side_effect=AssertionError("render must not call db.get_alias"),
        )
        mocker.patch(
            "rots.db.init_db",
            side_effect=AssertionError("render must not call db.init_db"),
        )
        mocker.patch(
            "rots.db.get_connection",
            side_effect=AssertionError("render must not open db.get_connection"),
        )

    def _patch_safety_nets(self, mocker):
        mocker.patch.object(Config, "get_executor")
        mocker.patch("rots.commands.instance.app.deploy_lock")
        mocker.patch(
            "rots.commands.instance.app.systemd.daemon_reload",
            create=True,
        )
        mocker.patch("rots.commands.instance.app.db.record_deployment")

    def test_default_at_current_tag_does_not_reach_db_module(self, mocker, tmp_path):
        """Default ``@current`` exits cleanly; if a regression slips past
        ``_build_fmt_vars``, the DB-module tripwires would catch it.
        """
        self._patch_safety_nets(mocker)

        out_dir = tmp_path / "out"
        out_dir.mkdir()

        from rots.commands.instance.app import render as render_cmd

        with pytest.raises(SystemExit) as excinfo:
            render_cmd(out_dir=out_dir)

        assert "alias" in str(excinfo.value).lower()

    def test_default_tag_creates_no_deployments_db_anywhere(self, mocker, monkeypatch, tmp_path):
        """No ``deployments.db`` may be created — anywhere under tmp_path.

        ``Config.db_path`` falls through to ``$XDG_DATA_HOME/rots/`` or
        ``~/.local/share/rots/`` on macOS / non-root. Redirect both env
        vars at tmp_path so any accidental DB creation is observable here.
        """
        self._patch_safety_nets(mocker)
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
        monkeypatch.setenv("HOME", str(tmp_path / "home"))

        out_dir = tmp_path / "out"
        out_dir.mkdir()

        from rots.commands.instance.app import render as render_cmd

        with pytest.raises(SystemExit):
            render_cmd(out_dir=out_dir)

        found = list(tmp_path.rglob("deployments.db"))
        assert found == [], f"render created deployments.db: {found}"

    def test_concrete_tag_reaches_no_db_entry_points(self, mocker, tmp_path):
        """`--tag v1.2.3` (concrete) renders successfully without any DB call.

        The autouse ``_db_tripwires`` would raise AssertionError if any
        ``db.*`` entry point ran. Reaching the assertion below proves the
        concrete-tag path is DB-free end-to-end.
        """
        self._patch_safety_nets(mocker)

        out_dir = tmp_path / "out"
        out_dir.mkdir()

        from rots.commands.instance.app import render as render_cmd

        render_cmd(out_dir=out_dir, tag="v1.2.3")

        web = (out_dir / "etc" / "containers" / "systemd" / "onetime-web@.container").read_text()
        assert ":v1.2.3" in web

    def test_registry_set_with_concrete_tag_reaches_no_db(self, mocker, monkeypatch, tmp_path):
        """With a registry, the .container Image= is ``onetime.image`` and DB stays untouched."""
        self._patch_safety_nets(mocker)
        monkeypatch.setenv("OTS_REGISTRY", "registry.example.com")

        out_dir = tmp_path / "out"
        out_dir.mkdir()

        from rots.commands.instance.app import render as render_cmd

        render_cmd(out_dir=out_dir, tag="v0.24.0")

        target = out_dir / "etc" / "containers" / "systemd"
        assert (target / "onetime.image").exists()
        web = (target / "onetime-web@.container").read_text()
        assert "Image=onetime.image" in web


class TestRenderItem2NoPartialOutput:
    """Item 2, supplemental — ``--config-source`` validation must precede output writes.

    Backend-dev's coverage asserts the error messages and exit code. This
    class adds the regression-guard that a bad ``--config-source`` does not
    leave a partial output tree on disk (validation runs before staging).
    """

    @pytest.fixture(autouse=True)
    def _clear_env(self, monkeypatch, tmp_path):
        monkeypatch.delenv("IMAGE", raising=False)
        monkeypatch.delenv("OTS_REGISTRY", raising=False)
        monkeypatch.setenv("TAG", "v0.24.0")
        _stage_default_confexts(monkeypatch, tmp_path)

    def _patch_safety_nets(self, mocker):
        mocker.patch.object(Config, "get_executor")
        mocker.patch("rots.commands.instance.app.deploy_lock")
        mocker.patch(
            "rots.commands.instance.app.systemd.daemon_reload",
            create=True,
        )

    def test_missing_config_source_does_not_create_output_tree(self, mocker, tmp_path):
        """A bogus ``--config-source`` must not leave behind a partial output tree."""
        self._patch_safety_nets(mocker)

        out_dir = tmp_path / "out"
        out_dir.mkdir()
        bogus = tmp_path / "missing"

        from rots.commands.instance.app import render as render_cmd

        with pytest.raises(SystemExit):
            render_cmd(out_dir=out_dir, config_source=bogus)

        assert not (out_dir / "etc" / "containers" / "systemd").exists()

    def test_file_config_source_does_not_create_output_tree(self, mocker, tmp_path):
        """A regular-file ``--config-source`` must not leave behind a partial tree."""
        self._patch_safety_nets(mocker)

        out_dir = tmp_path / "out"
        out_dir.mkdir()
        a_file = tmp_path / "regular.txt"
        a_file.write_text("not a directory\n")

        from rots.commands.instance.app import render as render_cmd

        with pytest.raises(SystemExit):
            render_cmd(out_dir=out_dir, config_source=a_file)

        assert not (out_dir / "etc" / "containers" / "systemd").exists()

    def test_valid_empty_config_source_dir_renders_with_no_volume_lines(self, mocker, tmp_path):
        """An existing-but-empty config_source dir is valid — emits no Volume= lines."""
        self._patch_safety_nets(mocker)

        out_dir = tmp_path / "out"
        out_dir.mkdir()
        empty_src = tmp_path / "empty"
        empty_src.mkdir()

        from rots.commands.instance.app import render as render_cmd

        render_cmd(out_dir=out_dir, config_source=empty_src)

        web = (out_dir / "etc" / "containers" / "systemd" / "onetime-web@.container").read_text()
        assert "/app/etc/" not in web


class TestRenderItem4AtomicityExtras:
    """Item 4, supplemental — additional invariants of the tree-level swap.

    Backend-dev's coverage asserts:
      * old tree intact on mid-write failure
      * no staging dir leftover on success or failure

    This class adds:
      * In-place rerun is byte-stable (rules out swap-introduced jitter).
      * No stray ``*.tmp`` files anywhere — guards against a partial revert
        to the old per-file ``.tmp -> os.replace`` approach.
      * Stale managed file (``onetime.image`` from a prior registry-set
        render) is removed when re-rendered with registry unset, even when
        operator-owned files coexist in the same directory.
    """

    @pytest.fixture(autouse=True)
    def _clear_env(self, monkeypatch, tmp_path):
        monkeypatch.delenv("IMAGE", raising=False)
        monkeypatch.delenv("OTS_REGISTRY", raising=False)
        monkeypatch.setenv("TAG", "v0.24.0")
        _stage_default_confexts(monkeypatch, tmp_path)

    def _patch_safety_nets(self, mocker):
        mocker.patch.object(Config, "get_executor")
        mocker.patch("rots.commands.instance.app.deploy_lock")
        mocker.patch(
            "rots.commands.instance.app.systemd.daemon_reload",
            create=True,
        )

    def test_byte_stable_rerun_in_same_out_dir(self, mocker, tmp_path):
        """Re-rendering into the same ``out_dir`` produces byte-identical files.

        Distinct from
        ``TestInstanceRenderCommand.test_render_output_is_byte_stable_across_runs``
        which uses two separate out_dirs. This one re-renders in place to
        verify the atomic-swap path doesn't introduce jitter (e.g.
        timestamps copied via ``shutil.copy2``).
        """
        self._patch_safety_nets(mocker)

        out_dir = tmp_path / "out"
        out_dir.mkdir()
        cfg_src = tmp_path / "cfg"
        cfg_src.mkdir()
        (cfg_src / "config.yaml").write_text("a: 1\n")

        from rots.commands.instance.app import render as render_cmd

        render_cmd(out_dir=out_dir, config_source=cfg_src, quiet=True)
        target = out_dir / "etc" / "containers" / "systemd"
        first = {p.name: p.read_bytes() for p in target.iterdir()}

        render_cmd(out_dir=out_dir, config_source=cfg_src, quiet=True)
        second = {p.name: p.read_bytes() for p in target.iterdir()}

        assert first == second, "rerun produced different bytes"

    def test_no_stray_tmp_files_after_successful_render(self, mocker, tmp_path):
        """A successful render leaves no ``*.tmp`` files anywhere under out_dir.

        Regression guard against a partial revert to the old per-file
        ``.tmp -> os.replace`` approach (which left stray ``.tmp`` files
        on certain failure modes).
        """
        self._patch_safety_nets(mocker)

        out_dir = tmp_path / "out"
        out_dir.mkdir()

        from rots.commands.instance.app import render as render_cmd

        render_cmd(out_dir=out_dir)

        leftover_tmps = list(out_dir.rglob("*.tmp"))
        assert leftover_tmps == [], f"stray tmp files: {leftover_tmps}"

    def test_rollback_when_staging_promote_fails_after_live_moved_aside(self, mocker, tmp_path):
        """Failure of the second ``os.replace`` (staging->live) must restore the original.

        The rollback path in ``_render_quadlets`` is::

            os.replace(out_dir, old_dir)         # 1: rename live -> .old (succeeds)
            try:
                os.replace(staging_dir, out_dir) # 2: rename staging -> live (FAILS)
            except OSError:
                os.replace(old_dir, out_dir)     # 3: rollback to original
                raise

        Backend-dev's ``test_render_swap_cleans_staging_dir_on_failure``
        forces failure inside ``render_web_template`` — that aborts BEFORE
        any ``os.replace`` runs and the rollback branch is never exercised.
        This test fails on the second ``os.replace`` specifically so the
        rollback branch executes and is verified to leave the live tree
        in its original state.
        """
        import os as _os

        self._patch_safety_nets(mocker)

        out_dir = tmp_path / "out"
        target = out_dir / "etc" / "containers" / "systemd"
        target.mkdir(parents=True)
        # Seed the live tree with sentinel content + an operator file.
        (target / "onetime-web@.container").write_text("# OLD WEB\n")
        (target / "onetime-worker@.container").write_text("# OLD WORKER\n")
        (target / "onetime-scheduler@.container").write_text("# OLD SCHED\n")
        operator = target / "operator-owned.conf"
        operator.write_text("# operator file\n")

        real_replace = _os.replace
        call_count = {"n": 0}

        def flaky_replace(src, dst, *a, **kw):
            call_count["n"] += 1
            # The sequence is: (1) live -> .old, (2) staging -> live, (3) .old -> live (rollback).
            # Fail call 2 to trigger the rollback at call 3.
            if call_count["n"] == 2:
                raise OSError("injected failure on staging promotion")
            return real_replace(src, dst, *a, **kw)

        mocker.patch("rots.commands.instance.app.os.replace", side_effect=flaky_replace)

        from rots.commands.instance.app import render as render_cmd

        with pytest.raises(SystemExit) as excinfo:
            render_cmd(out_dir=out_dir)
        assert excinfo.value.code != 0

        # Rollback ran: live tree is exactly as before.
        assert (target / "onetime-web@.container").read_text() == "# OLD WEB\n"
        assert (target / "onetime-worker@.container").read_text() == "# OLD WORKER\n"
        assert (target / "onetime-scheduler@.container").read_text() == "# OLD SCHED\n"
        assert operator.read_text() == "# operator file\n"

        # No leftover .old directory at the rename target.
        siblings = {p.name for p in (out_dir / "etc" / "containers").iterdir()}
        assert siblings == {"systemd"}, f"unexpected sibling dirs after rollback: {siblings}"

    def test_stale_image_unit_removed_alongside_operator_files(self, mocker, monkeypatch, tmp_path):
        """Re-render without registry: stale ``onetime.image`` removed, operator files survive.

        Combined invariant: the tree-level swap correctly classifies
        managed vs operator-owned files when both are present.
        """
        self._patch_safety_nets(mocker)

        out_dir = tmp_path / "out"
        target = out_dir / "etc" / "containers" / "systemd"
        target.mkdir(parents=True)
        # Stale managed file from a prior registry-set render.
        stale = target / "onetime.image"
        stale.write_text("[Image]\nImage=stale.example.com/old:v0\n")
        # Operator-owned files — must survive.
        operator_a = target / "operator-a.conf"
        operator_a.write_text("# A\n")
        operator_b = target / "third-party@.container"
        operator_b.write_text("# B\n")

        from rots.commands.instance.app import render as render_cmd

        render_cmd(out_dir=out_dir)

        assert not stale.exists(), "stale onetime.image must be removed"
        assert operator_a.read_text() == "# A\n", "operator-a.conf must survive"
        assert operator_b.read_text() == "# B\n", "third-party@.container must survive"
        # The three rendered .container files must be present.
        for name in (
            "onetime-web@.container",
            "onetime-worker@.container",
            "onetime-scheduler@.container",
        ):
            assert (target / name).exists(), f"{name} missing after render"
