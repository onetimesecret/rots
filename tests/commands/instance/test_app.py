# tests/commands/instance/test_app.py
"""Tests for instance management commands.

These tests verify that instance commands can be imported and invoked
without attribute errors or import failures. They use mocking to avoid
requiring actual podman/systemd infrastructure.
"""

import pytest

from ots_containers.commands import instance
from ots_containers.commands.instance._helpers import format_command
from ots_containers.commands.instance.annotations import InstanceType


@pytest.fixture(autouse=True)
def mock_systemctl_available(mocker):
    """Mock shutil.which to report systemctl as available for all tests."""
    mocker.patch("shutil.which", return_value="/mock/bin/systemctl")


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
        from ots_containers.cli import app

        with pytest.raises(SystemExit) as exc_info:
            app(["instance", "deploy", "--help"])
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "web" in captured.out.lower() or "deploy" in captured.out.lower()

    def test_instance_redeploy_help(self, capsys):
        """instance redeploy --help should work."""
        from ots_containers.cli import app

        with pytest.raises(SystemExit) as exc_info:
            app(["instance", "redeploy", "--help"])
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "force" in captured.out.lower() or "redeploy" in captured.out.lower()

    def test_instance_run_help(self, capsys):
        """instance run --help should work."""
        from ots_containers.cli import app

        with pytest.raises(SystemExit) as exc_info:
            app(["instance", "run", "--help"])
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "port" in captured.out.lower() or "run" in captured.out.lower()


class TestRunCommand:
    """Test run command for direct podman execution."""

    def test_run_builds_correct_command(self, mocker, tmp_path):
        """run should build correct podman command."""
        import subprocess

        # Mock Config
        mock_config = mocker.MagicMock()
        mock_config.tag = "v0.23.0"  # Default uses local image with cfg.tag
        mock_config.resolve_image_tag.return_value = ("onetimesecret", "v0.23.0")
        mock_config.config_dir = tmp_path / "etc"
        mock_config.config_dir.mkdir()
        mock_config.registry = None
        mocker.patch("ots_containers.commands.instance.app.Config", return_value=mock_config)

        # Mock env file not existing
        mocker.patch(
            "ots_containers.commands.instance.app.quadlet.DEFAULT_ENV_FILE",
            tmp_path / "nonexistent",
        )

        # Mock subprocess.run
        mock_run = mocker.patch("subprocess.run")
        mock_run.return_value = subprocess.CompletedProcess([], 0, stdout="abc123")

        # Call run command in detached mode
        instance.run(port=7143, detach=True, quiet=True)

        # Verify podman run was called
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "podman"
        assert cmd[1] == "run"
        assert "-d" in cmd
        assert "--rm" in cmd
        assert "-p" in cmd
        assert "7143:7143" in cmd
        assert "onetimesecret:v0.23.0" in cmd

    def test_run_includes_secrets_with_production_flag(self, mocker, tmp_path):
        """run --production should include secrets from env file."""
        import subprocess

        # Mock Config
        mock_config = mocker.MagicMock()
        mock_config.resolve_image_tag.return_value = ("onetimesecret", "latest")
        mock_config.config_dir = tmp_path / "etc"
        mock_config.config_dir.mkdir()
        mock_config.existing_config_files = []
        mock_config.registry = None
        mocker.patch("ots_containers.commands.instance.app.Config", return_value=mock_config)

        # Create env file with secrets
        env_file = tmp_path / "onetimesecret"
        env_file.write_text("SECRET_VARIABLE_NAMES=HMAC_SECRET,API_KEY\n")
        mocker.patch(
            "ots_containers.commands.instance.app.quadlet.DEFAULT_ENV_FILE",
            env_file,
        )

        # Mock get_secrets_from_env_file (imported inside run function)
        from ots_containers.environment_file import SecretSpec

        mock_secrets = [
            SecretSpec(env_var_name="HMAC_SECRET", secret_name="ots_hmac_secret"),
            SecretSpec(env_var_name="API_KEY", secret_name="ots_api_key"),
        ]
        mocker.patch(
            "ots_containers.environment_file.get_secrets_from_env_file",
            return_value=mock_secrets,
        )

        # Mock subprocess.run
        mock_run = mocker.patch("subprocess.run")
        mock_run.return_value = subprocess.CompletedProcess([], 0, stdout="abc123")

        # Call run command with production flag
        instance.run(port=7143, detach=True, quiet=True, production=True)

        # Verify secrets were included
        cmd = mock_run.call_args[0][0]
        cmd_str = " ".join(cmd)
        assert "--secret" in cmd_str
        assert "ots_hmac_secret" in cmd_str

    def test_run_minimal_without_production_flag(self, mocker, tmp_path):
        """run without --production should be minimal (no secrets/volumes)."""
        import subprocess

        # Mock Config
        mock_config = mocker.MagicMock()
        mock_config.resolve_image_tag.return_value = ("onetimesecret", "latest")
        mocker.patch("ots_containers.commands.instance.app.Config", return_value=mock_config)

        # Mock subprocess.run
        mock_run = mocker.patch("subprocess.run")
        mock_run.return_value = subprocess.CompletedProcess([], 0, stdout="abc123")

        # Call run command without production flag
        instance.run(port=7143, detach=True, quiet=True)

        # Verify minimal command (no secrets, no volumes)
        cmd = mock_run.call_args[0][0]
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
        mocker.patch("ots_containers.commands.instance.app.Config", return_value=mock_config)
        mocker.patch("ots_containers.commands.instance.app.assets.update")
        mocker.patch("ots_containers.commands.instance.app.quadlet.write_web_template")
        mocker.patch("ots_containers.commands.instance.app.systemd.start")
        mocker.patch("ots_containers.commands.instance.app.db.record_deployment")

        # Should not raise SystemExit - validation no longer blocks deploy
        instance.deploy(identifiers=("7143",), web=True)

    def test_deploy_requires_identifiers(self, mocker, capsys):
        """deploy without identifiers should fail."""
        with pytest.raises(SystemExit) as exc_info:
            instance.deploy(identifiers=(), web=True)
        assert "Identifiers required" in str(exc_info.value)

    def test_deploy_requires_type(self, mocker, capsys):
        """deploy with identifiers but no type should fail."""
        with pytest.raises(SystemExit) as exc_info:
            instance.deploy(identifiers=("7143",))
        assert "Instance type required" in str(exc_info.value)

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
        mocker.patch("ots_containers.commands.instance.app.Config", return_value=mock_config)
        mock_assets = mocker.patch("ots_containers.commands.instance.app.assets.update")
        mock_quadlet = mocker.patch(
            "ots_containers.commands.instance.app.quadlet.write_web_template"
        )
        mocker.patch("ots_containers.commands.instance.app.systemd.start")
        mocker.patch("ots_containers.commands.instance.app.db.record_deployment")

        instance.deploy(identifiers=("7143",), web=True)

        mock_assets.assert_called_once_with(mock_config, create_volume=True)
        mock_quadlet.assert_called_once_with(mock_config, force=False)


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
        mocker.patch("ots_containers.commands.instance.app.Config", return_value=mock_config)
        mock_quadlet = mocker.patch(
            "ots_containers.commands.instance.app.quadlet.write_worker_template"
        )
        mocker.patch("ots_containers.commands.instance.app.systemd.start")
        mocker.patch("ots_containers.commands.instance.app.db.record_deployment")

        instance.deploy(identifiers=("1",), worker=True)

        mock_quadlet.assert_called_once_with(mock_config, force=False)

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
        mocker.patch("ots_containers.commands.instance.app.Config", return_value=mock_config)
        mock_assets = mocker.patch("ots_containers.commands.instance.app.assets.update")
        mocker.patch("ots_containers.commands.instance.app.quadlet.write_worker_template")
        mocker.patch("ots_containers.commands.instance.app.systemd.start")
        mocker.patch("ots_containers.commands.instance.app.db.record_deployment")

        instance.deploy(identifiers=("1",), worker=True)

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
        mocker.patch("ots_containers.commands.instance.app.Config", return_value=mock_config)
        mocker.patch("ots_containers.commands.instance.app.quadlet.write_worker_template")
        mock_start = mocker.patch("ots_containers.commands.instance.app.systemd.start")
        mocker.patch("ots_containers.commands.instance.app.db.record_deployment")

        instance.deploy(identifiers=("1",), worker=True)

        mock_start.assert_called_once_with("onetime-worker@1")


class TestRedeployCommand:
    """Test redeploy command with mocked dependencies."""

    def test_redeploy_with_no_instances_found(self, mocker, capsys):
        """redeploy with no instances should print message."""
        mocker.patch(
            "ots_containers.commands.instance._helpers.systemd.discover_web_instances",
            return_value=[],
        )
        mocker.patch(
            "ots_containers.commands.instance._helpers.systemd.discover_worker_instances",
            return_value=[],
        )
        mocker.patch(
            "ots_containers.commands.instance._helpers.systemd.discover_scheduler_instances",
            return_value=[],
        )

        instance.redeploy(identifiers=())

        captured = capsys.readouterr()
        assert "No running instances found" in captured.out

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
        mocker.patch("ots_containers.commands.instance.app.Config", return_value=mock_config)
        mocker.patch(
            "ots_containers.commands.instance._helpers.systemd.discover_web_instances",
            return_value=[7143],
        )
        mocker.patch(
            "ots_containers.commands.instance._helpers.systemd.discover_worker_instances",
            return_value=[],
        )
        mocker.patch(
            "ots_containers.commands.instance._helpers.systemd.discover_scheduler_instances",
            return_value=[],
        )
        mocker.patch("ots_containers.commands.instance.app.assets.update")
        mocker.patch("ots_containers.commands.instance.app.quadlet.write_web_template")
        mocker.patch("ots_containers.commands.instance.app.systemd.recreate")
        mocker.patch("ots_containers.commands.instance.app.db.record_deployment")
        mocker.patch(
            "ots_containers.commands.instance.app.systemd.container_exists",
            return_value=True,
        )

        # Should not raise AttributeError
        instance.redeploy(identifiers=())


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
            "ots_containers.commands.instance._helpers.systemd.discover_web_instances",
            return_value=[],
        )
        mocker.patch(
            "ots_containers.commands.instance._helpers.systemd.discover_worker_instances",
            return_value=[],
        )
        mocker.patch(
            "ots_containers.commands.instance._helpers.systemd.discover_scheduler_instances",
            return_value=[],
        )
        instance.exec_shell(identifiers=())
        captured = capsys.readouterr()
        assert "No running instances found" in captured.out

    def test_exec_calls_podman_exec(self, mocker, capsys):
        """exec_shell should call podman exec with correct container name."""
        mock_run = mocker.patch("ots_containers.commands.instance.app.subprocess.run")
        mocker.patch.dict("os.environ", {"SHELL": "/bin/bash"})

        instance.exec_shell(identifiers=("7043",), web=True)

        mock_run.assert_called_once()
        call_args = mock_run.call_args[0][0]
        assert call_args[:3] == ["podman", "exec", "-it"]
        # Container name uses -- as separator: systemd-onetime-web--7043
        assert "systemd-onetime-web--7043" in call_args
        assert "/bin/bash" in call_args


class TestListInstancesCommand:
    """Tests for list_instances command."""

    def test_list_instances_function_exists(self):
        """list_instances function should exist."""
        assert instance.list_instances is not None

    def test_list_with_no_instances(self, mocker, capsys):
        """list should print message when no instances found."""
        mocker.patch(
            "ots_containers.commands.instance._helpers.systemd.discover_web_instances",
            return_value=[],
        )
        mocker.patch(
            "ots_containers.commands.instance._helpers.systemd.discover_worker_instances",
            return_value=[],
        )
        mocker.patch(
            "ots_containers.commands.instance._helpers.systemd.discover_scheduler_instances",
            return_value=[],
        )

        instance.list_instances()

        captured = capsys.readouterr()
        assert "No configured instances found" in captured.out

    def test_list_displays_header(self, mocker, capsys, tmp_path):
        """list should display table header."""
        mocker.patch(
            "ots_containers.commands.instance._helpers.systemd.discover_web_instances",
            return_value=[7043],
        )
        mocker.patch(
            "ots_containers.commands.instance._helpers.systemd.discover_worker_instances",
            return_value=[],
        )
        mocker.patch(
            "ots_containers.commands.instance._helpers.systemd.discover_scheduler_instances",
            return_value=[],
        )
        mocker.patch(
            "ots_containers.commands.instance.app.subprocess.run",
            return_value=mocker.Mock(stdout="active\n", stderr=""),
        )

        # Mock Config and db
        mock_config = mocker.Mock()
        mock_config.db_path = tmp_path / "test.db"
        mocker.patch(
            "ots_containers.commands.instance.app.Config",
            return_value=mock_config,
        )
        mocker.patch(
            "ots_containers.commands.instance.app.db.get_deployments",
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
        """enable should call systemctl enable."""
        mocker.patch(
            "ots_containers.commands.instance._helpers.systemd.discover_web_instances",
            return_value=[7043],
        )
        mocker.patch(
            "ots_containers.commands.instance._helpers.systemd.discover_worker_instances",
            return_value=[],
        )
        mocker.patch(
            "ots_containers.commands.instance._helpers.systemd.discover_scheduler_instances",
            return_value=[],
        )
        mock_run = mocker.patch("ots_containers.commands.instance.app.subprocess.run")

        instance.enable(identifiers=("7043",), web=True)

        mock_run.assert_called_once()
        call_args = mock_run.call_args[0][0]
        assert "systemctl" in call_args
        assert "enable" in call_args
        assert "onetime-web@7043" in call_args

        captured = capsys.readouterr()
        assert "Enabled" in captured.out


class TestStopCommand:
    """Test stop command."""

    def test_stop_function_exists(self):
        """stop command should be defined."""
        assert hasattr(instance, "stop")
        assert callable(instance.stop)

    def test_stop_calls_systemd_stop(self, mocker, capsys):
        """stop should call systemd.stop for each instance."""
        mock_stop = mocker.patch("ots_containers.commands.instance.app.systemd.stop")

        instance.stop(identifiers=("7043",), web=True)

        mock_stop.assert_called_once_with("onetime-web@7043")
        captured = capsys.readouterr()
        assert "Stopped onetime-web@7043" in captured.out

    def test_stop_discovers_instances_when_no_identifiers(self, mocker):
        """stop with no identifiers should discover all types."""
        mocker.patch(
            "ots_containers.commands.instance._helpers.systemd.discover_web_instances",
            return_value=[7043, 7044],
        )
        mocker.patch(
            "ots_containers.commands.instance._helpers.systemd.discover_worker_instances",
            return_value=["1"],
        )
        mocker.patch(
            "ots_containers.commands.instance._helpers.systemd.discover_scheduler_instances",
            return_value=[],
        )
        mock_stop = mocker.patch("ots_containers.commands.instance.app.systemd.stop")

        instance.stop(identifiers=())

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
        mock_restart = mocker.patch("ots_containers.commands.instance.app.systemd.restart")

        instance.restart(identifiers=("7043",), web=True)

        mock_restart.assert_called_once_with("onetime-web@7043")
        captured = capsys.readouterr()
        assert "Restarting onetime-web@7043" in captured.out

    def test_restart_multiple(self, mocker, capsys):
        """restart should call systemd.restart for each instance with delay."""
        mock_restart = mocker.patch("ots_containers.commands.instance.app.systemd.restart")
        mock_sleep = mocker.patch("ots_containers.commands.instance._helpers.time.sleep")

        instance.restart(identifiers=("7043", "7044", "7045"), web=True)

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
            "ots_containers.commands.instance._helpers.systemd.discover_web_instances",
            return_value=[7043, 7044],
        )
        mocker.patch(
            "ots_containers.commands.instance._helpers.systemd.discover_worker_instances",
            return_value=["1"],
        )
        mocker.patch(
            "ots_containers.commands.instance._helpers.systemd.discover_scheduler_instances",
            return_value=[],
        )
        mock_run = mocker.patch("ots_containers.commands.instance.app.subprocess.run")

        instance.logs(identifiers=())

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
            "ots_containers.commands.instance._helpers.systemd.discover_web_instances",
            return_value=[7043],
        )
        mocker.patch(
            "ots_containers.commands.instance._helpers.systemd.discover_worker_instances",
            return_value=[],
        )
        mocker.patch(
            "ots_containers.commands.instance._helpers.systemd.discover_scheduler_instances",
            return_value=[],
        )
        mocker.patch("builtins.input", return_value="n")

        instance.disable(identifiers=(), web=True, yes=False)

        captured = capsys.readouterr()
        assert "Aborted" in captured.out

    def test_disable_calls_systemctl(self, mocker, capsys):
        """disable should call systemctl disable with --yes."""
        mocker.patch(
            "ots_containers.commands.instance._helpers.systemd.discover_web_instances",
            return_value=[7043],
        )
        mocker.patch(
            "ots_containers.commands.instance._helpers.systemd.discover_worker_instances",
            return_value=[],
        )
        mocker.patch(
            "ots_containers.commands.instance._helpers.systemd.discover_scheduler_instances",
            return_value=[],
        )
        mock_run = mocker.patch("ots_containers.commands.instance.app.subprocess.run")

        instance.disable(identifiers=("7043",), web=True, yes=True)

        mock_run.assert_called_once()
        call_args = mock_run.call_args[0][0]
        assert "systemctl" in call_args
        assert "disable" in call_args
        assert "onetime-web@7043" in call_args

        captured = capsys.readouterr()
        assert "Disabled" in captured.out


class TestResolveInstanceType:
    """Test resolve_instance_type helper."""

    def test_returns_none_when_no_type_specified(self):
        """Should return None when no type specified."""
        from ots_containers.commands.instance.annotations import resolve_instance_type

        result = resolve_instance_type(None, web=False, worker=False, scheduler=False)
        assert result is None

    def test_returns_type_from_explicit_param(self):
        """Should return type from --type parameter."""
        from ots_containers.commands.instance.annotations import resolve_instance_type

        result = resolve_instance_type(
            InstanceType.WORKER, web=False, worker=False, scheduler=False
        )
        assert result == InstanceType.WORKER

    def test_returns_web_from_flag(self):
        """Should return WEB when --web flag set."""
        from ots_containers.commands.instance.annotations import resolve_instance_type

        result = resolve_instance_type(None, web=True, worker=False, scheduler=False)
        assert result == InstanceType.WEB

    def test_returns_worker_from_flag(self):
        """Should return WORKER when --worker flag set."""
        from ots_containers.commands.instance.annotations import resolve_instance_type

        result = resolve_instance_type(None, web=False, worker=True, scheduler=False)
        assert result == InstanceType.WORKER

    def test_returns_scheduler_from_flag(self):
        """Should return SCHEDULER when --scheduler flag set."""
        from ots_containers.commands.instance.annotations import resolve_instance_type

        result = resolve_instance_type(None, web=False, worker=False, scheduler=True)
        assert result == InstanceType.SCHEDULER

    def test_raises_on_multiple_flags(self):
        """Should raise when multiple shorthand flags set."""
        from ots_containers.commands.instance.annotations import resolve_instance_type

        with pytest.raises(SystemExit):
            resolve_instance_type(None, web=True, worker=True, scheduler=False)

    def test_raises_on_type_plus_flag(self):
        """Should raise when both --type and shorthand flag used."""
        from ots_containers.commands.instance.annotations import resolve_instance_type

        with pytest.raises(SystemExit):
            resolve_instance_type(InstanceType.WEB, web=False, worker=True, scheduler=False)


class TestSchedulerCommands:
    """Integration tests for scheduler instance commands using --scheduler flag."""

    def test_stop_scheduler_with_flag(self, mocker, capsys):
        """stop --scheduler should call systemd.stop for scheduler instances."""
        mock_stop = mocker.patch("ots_containers.commands.instance.app.systemd.stop")

        instance.stop(identifiers=("main",), scheduler=True)

        mock_stop.assert_called_once_with("onetime-scheduler@main")
        captured = capsys.readouterr()
        assert "Stopped onetime-scheduler@main" in captured.out

    def test_restart_scheduler_with_flag(self, mocker, capsys):
        """restart --scheduler should call systemd.restart for scheduler instances."""
        mock_restart = mocker.patch("ots_containers.commands.instance.app.systemd.restart")

        instance.restart(identifiers=("main",), scheduler=True)

        mock_restart.assert_called_once_with("onetime-scheduler@main")
        captured = capsys.readouterr()
        assert "Restarting onetime-scheduler@main" in captured.out

    def test_start_scheduler_with_flag(self, mocker, capsys):
        """start --scheduler should call systemd.start for scheduler instances."""
        mock_start = mocker.patch("ots_containers.commands.instance.app.systemd.start")

        instance.start(identifiers=("main",), scheduler=True)

        mock_start.assert_called_once_with("onetime-scheduler@main")
        captured = capsys.readouterr()
        assert "Started onetime-scheduler@main" in captured.out

    def test_status_scheduler_with_flag(self, mocker, capsys):
        """status --scheduler should show status for scheduler instances."""
        mock_run = mocker.patch(
            "ots_containers.commands.instance.app.subprocess.run",
            return_value=mocker.Mock(returncode=0, stdout="active"),
        )

        instance.status(identifiers=("main",), scheduler=True)

        # Should call systemctl status for the scheduler unit
        mock_run.assert_called()
        cmd = mock_run.call_args[0][0]
        assert "systemctl" in cmd
        assert "onetime-scheduler@main" in cmd

    def test_logs_scheduler_with_flag(self, mocker):
        """logs --scheduler should call journalctl for scheduler instances."""
        mock_run = mocker.patch("ots_containers.commands.instance.app.subprocess.run")

        instance.logs(identifiers=("main",), scheduler=True)

        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert "journalctl" in cmd
        assert "onetime-scheduler@main" in cmd or "-u" in cmd

    def test_enable_scheduler_with_flag(self, mocker, capsys):
        """enable --scheduler should call systemctl enable for scheduler instances."""
        mock_run = mocker.patch("ots_containers.commands.instance.app.subprocess.run")

        instance.enable(identifiers=("main",), scheduler=True)

        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert "systemctl" in cmd
        assert "enable" in cmd
        assert "onetime-scheduler@main" in cmd

    def test_disable_scheduler_with_flag(self, mocker, capsys):
        """disable --scheduler should call systemctl disable for scheduler instances."""
        mock_run = mocker.patch("ots_containers.commands.instance.app.subprocess.run")

        instance.disable(identifiers=("main",), scheduler=True, yes=True)

        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert "systemctl" in cmd
        assert "disable" in cmd
        assert "onetime-scheduler@main" in cmd

    def test_stop_discovers_scheduler_instances(self, mocker):
        """stop --scheduler with no identifiers should discover scheduler instances."""
        mocker.patch(
            "ots_containers.commands.instance._helpers.systemd.discover_scheduler_instances",
            return_value=["main", "cron"],
        )
        mock_stop = mocker.patch("ots_containers.commands.instance.app.systemd.stop")

        instance.stop(identifiers=(), scheduler=True)

        assert mock_stop.call_count == 2
        calls = [c[0][0] for c in mock_stop.call_args_list]
        assert "onetime-scheduler@main" in calls
        assert "onetime-scheduler@cron" in calls

    def test_restart_discovers_scheduler_instances(self, mocker):
        """restart --scheduler with no identifiers should discover scheduler instances."""
        mocker.patch(
            "ots_containers.commands.instance._helpers.systemd.discover_scheduler_instances",
            return_value=["main"],
        )
        mock_restart = mocker.patch("ots_containers.commands.instance.app.systemd.restart")

        instance.restart(identifiers=(), scheduler=True)

        mock_restart.assert_called_once_with("onetime-scheduler@main")

    def test_multiple_scheduler_identifiers(self, mocker, capsys):
        """Commands should handle multiple scheduler identifiers."""
        mock_stop = mocker.patch("ots_containers.commands.instance.app.systemd.stop")

        instance.stop(identifiers=("main", "cron", "backup"), scheduler=True)

        assert mock_stop.call_count == 3
        calls = [c[0][0] for c in mock_stop.call_args_list]
        assert "onetime-scheduler@main" in calls
        assert "onetime-scheduler@cron" in calls
        assert "onetime-scheduler@backup" in calls

    def test_scheduler_with_type_parameter(self, mocker, capsys):
        """Commands should work with --type scheduler instead of --scheduler flag."""
        mock_stop = mocker.patch("ots_containers.commands.instance.app.systemd.stop")

        instance.stop(identifiers=("main",), instance_type=InstanceType.SCHEDULER)

        mock_stop.assert_called_once_with("onetime-scheduler@main")

    def test_scheduler_named_instances(self, mocker, capsys):
        """Scheduler should accept string identifiers (not just numeric)."""
        mock_restart = mocker.patch("ots_containers.commands.instance.app.systemd.restart")

        instance.restart(identifiers=("daily-cleanup", "weekly-reports"), scheduler=True)

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
            "ots_containers.config.Config.db_path",
            new_callable=mocker.PropertyMock,
            return_value=tmp_path / "deployments.db",
        )

        instance.deploy(identifiers=("7043",), web=True, dry_run=True)

        captured = capsys.readouterr()
        assert "custom.registry.io/myorg/myapp:v1.0.0" in captured.out
        assert "dry-run" in captured.out

    def test_deploy_records_correct_image_tag(self, mocker, monkeypatch, tmp_path, capsys):
        """Scenario 18b: deploy with IMAGE/TAG env vars flows image to db.record_deployment."""
        monkeypatch.setenv("IMAGE", "custom.registry.io/myorg/myapp")
        monkeypatch.setenv("TAG", "v2.5.0")

        # Mock db_path
        mocker.patch(
            "ots_containers.config.Config.db_path",
            new_callable=mocker.PropertyMock,
            return_value=tmp_path / "deployments.db",
        )

        # Mock all external calls needed for non-dry-run deploy
        mocker.patch("ots_containers.commands.instance.app.assets.update")
        mocker.patch("ots_containers.commands.instance.app.quadlet.write_web_template")
        mocker.patch("ots_containers.commands.instance.app.systemd.start")
        mock_record = mocker.patch("ots_containers.commands.instance.app.db.record_deployment")

        instance.deploy(identifiers=("7043",), web=True)

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
            "ots_containers.config.Config.db_path",
            new_callable=mocker.PropertyMock,
            return_value=tmp_path / "deployments.db",
        )

        # Mock resolve_identifiers to return some instances
        mocker.patch(
            "ots_containers.commands.instance.app.resolve_identifiers",
            return_value={InstanceType.WEB: ["7043"]},
        )

        instance.redeploy(identifiers=("7043",), web=True, dry_run=True)

        captured = capsys.readouterr()
        assert "custom.registry.io/myorg/myapp:v1.0.0" in captured.out
        assert "dry-run" in captured.out

    def test_redeploy_records_correct_image_tag(self, mocker, monkeypatch, tmp_path, capsys):
        """Scenario 19b: redeploy with IMAGE/TAG env vars flows image to db.record_deployment."""
        monkeypatch.setenv("IMAGE", "custom.registry.io/myorg/myapp")
        monkeypatch.setenv("TAG", "v3.0.0")

        # Mock db_path
        mocker.patch(
            "ots_containers.config.Config.db_path",
            new_callable=mocker.PropertyMock,
            return_value=tmp_path / "deployments.db",
        )

        # Mock resolve_identifiers to return some instances
        mocker.patch(
            "ots_containers.commands.instance.app.resolve_identifiers",
            return_value={InstanceType.WEB: ["7043"]},
        )

        # Mock all external calls needed for non-dry-run redeploy
        mocker.patch("ots_containers.commands.instance.app.assets.update")
        mocker.patch("ots_containers.commands.instance.app.quadlet.write_web_template")
        mocker.patch(
            "ots_containers.commands.instance.app.systemd.container_exists",
            return_value=True,
        )
        mocker.patch("ots_containers.commands.instance.app.systemd.recreate")
        mock_record = mocker.patch("ots_containers.commands.instance.app.db.record_deployment")

        instance.redeploy(identifiers=("7043",), web=True)

        # Verify db.record_deployment was called with the custom image/tag
        mock_record.assert_called_once()
        assert mock_record.call_args.kwargs["image"] == "custom.registry.io/myorg/myapp"
        assert mock_record.call_args.kwargs["tag"] == "v3.0.0"
