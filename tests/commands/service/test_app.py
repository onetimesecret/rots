# tests/commands/service/test_app.py
"""Tests for service command app."""

from unittest.mock import MagicMock, patch

from ots_containers.commands.service.app import (
    app,
    disable,
    enable,
    init,
    list_all,
    list_instances,
    logs,
    restart,
    start,
    status,
    stop,
)


class TestServiceAppExists:
    """Tests for service app structure."""

    def test_app_exists(self):
        """Test service app is defined."""
        assert app is not None
        # Now supports both "service" and "services" as aliases
        assert "service" in app.name or app.name == ("service", "services")

    def test_init_command_exists(self):
        """Test init command is registered."""
        assert init is not None

    def test_enable_command_exists(self):
        """Test enable command is registered."""
        assert enable is not None

    def test_disable_command_exists(self):
        """Test disable command is registered."""
        assert disable is not None

    def test_start_command_exists(self):
        """Test start command is registered."""
        assert start is not None

    def test_stop_command_exists(self):
        """Test stop command is registered."""
        assert stop is not None

    def test_restart_command_exists(self):
        """Test restart command is registered."""
        assert restart is not None

    def test_status_command_exists(self):
        """Test status command is registered."""
        assert status is not None

    def test_logs_command_exists(self):
        """Test logs command is registered."""
        assert logs is not None

    def test_list_command_exists(self):
        """Test list command is registered."""
        assert list_instances is not None


class TestDefaultCommand:
    """Tests for default command (list_all)."""

    @patch("ots_containers.commands.service.app.is_service_enabled")
    @patch("ots_containers.commands.service.app.is_service_active")
    @patch("subprocess.run")
    def test_default_no_instances(self, mock_run, mock_active, mock_enabled, capsys):
        """Test default command when no instances found."""
        mock_run.return_value = MagicMock(stdout="")

        list_all()

        captured = capsys.readouterr()
        assert "No service instances found" in captured.out
        assert "Available packages:" in captured.out
        assert "valkey" in captured.out
        assert "redis" in captured.out


class TestInitCommand:
    """Tests for init command."""

    @patch("ots_containers.commands.service.app.check_default_service_conflict")
    @patch("ots_containers.commands.service.app.systemctl")
    @patch("ots_containers.commands.service.app.create_secrets_file")
    @patch("ots_containers.commands.service.app.ensure_data_dir")
    @patch("ots_containers.commands.service.app.update_config_value")
    @patch("ots_containers.commands.service.app.copy_default_config")
    def test_init_calls_copy_default_config(
        self,
        mock_copy,
        mock_update,
        mock_data,
        mock_secrets,
        mock_systemctl,
        mock_check_conflict,
        capsys,
        tmp_path,
    ):
        """Test init copies default config."""
        mock_copy.return_value = tmp_path / "test.conf"
        mock_data.return_value = tmp_path / "data"
        mock_secrets.return_value = None

        init("valkey", "6379", start=False, enable=False)

        mock_copy.assert_called_once()
        call_args = mock_copy.call_args
        assert call_args[0][0].name == "valkey"
        assert call_args[0][1] == "6379"

    @patch("ots_containers.commands.service.app.check_default_service_conflict")
    @patch("ots_containers.commands.service.app.systemctl")
    @patch("ots_containers.commands.service.app.create_secrets_file")
    @patch("ots_containers.commands.service.app.ensure_data_dir")
    @patch("ots_containers.commands.service.app.update_config_value")
    @patch("ots_containers.commands.service.app.copy_default_config")
    def test_init_updates_port_and_bind(
        self,
        mock_copy,
        mock_update,
        mock_data,
        mock_secrets,
        mock_systemctl,
        mock_check_conflict,
        tmp_path,
    ):
        """Test init updates port and bind in config."""
        mock_copy.return_value = tmp_path / "test.conf"
        mock_data.return_value = tmp_path / "data"
        mock_secrets.return_value = None

        init("valkey", "6379", port=6379, bind="0.0.0.0", start=False, enable=False)

        # Check update_config_value was called for port and bind
        call_keys = [call[0][1] for call in mock_update.call_args_list]
        assert "port" in call_keys
        assert "bind" in call_keys

    @patch("ots_containers.commands.service.app.check_default_service_conflict")
    @patch("ots_containers.commands.service.app.systemctl")
    @patch("ots_containers.commands.service.app.create_secrets_file")
    @patch("ots_containers.commands.service.app.add_secrets_include")
    @patch("ots_containers.commands.service.app.ensure_data_dir")
    @patch("ots_containers.commands.service.app.update_config_value")
    @patch("ots_containers.commands.service.app.copy_default_config")
    def test_init_creates_secrets_file(
        self,
        mock_copy,
        mock_update,
        mock_data,
        mock_add_include,
        mock_secrets,
        mock_systemctl,
        mock_check_conflict,
        tmp_path,
    ):
        """Test init creates secrets file when not skipped."""
        mock_copy.return_value = tmp_path / "test.conf"
        mock_data.return_value = tmp_path / "data"
        mock_secrets.return_value = tmp_path / "test.secrets"

        init("valkey", "6379", no_secrets=False, start=False, enable=False)

        mock_secrets.assert_called_once()

    @patch("ots_containers.commands.service.app.check_default_service_conflict")
    @patch("ots_containers.commands.service.app.systemctl")
    @patch("ots_containers.commands.service.app.create_secrets_file")
    @patch("ots_containers.commands.service.app.ensure_data_dir")
    @patch("ots_containers.commands.service.app.update_config_value")
    @patch("ots_containers.commands.service.app.copy_default_config")
    def test_init_skips_secrets_with_no_secrets(
        self,
        mock_copy,
        mock_update,
        mock_data,
        mock_secrets,
        mock_systemctl,
        mock_check_conflict,
        tmp_path,
    ):
        """Test init skips secrets file with --no-secrets."""
        mock_copy.return_value = tmp_path / "test.conf"
        mock_data.return_value = tmp_path / "data"

        init("valkey", "6379", no_secrets=True, start=False, enable=False)

        mock_secrets.assert_not_called()

    @patch("ots_containers.commands.service.app.check_default_service_conflict")
    @patch("ots_containers.commands.service.app.systemctl")
    @patch("ots_containers.commands.service.app.create_secrets_file")
    @patch("ots_containers.commands.service.app.ensure_data_dir")
    @patch("ots_containers.commands.service.app.update_config_value")
    @patch("ots_containers.commands.service.app.copy_default_config")
    def test_init_enables_service(
        self,
        mock_copy,
        mock_update,
        mock_data,
        mock_secrets,
        mock_systemctl,
        mock_check_conflict,
        tmp_path,
    ):
        """Test init enables service when enable=True."""
        mock_copy.return_value = tmp_path / "test.conf"
        mock_data.return_value = tmp_path / "data"
        mock_secrets.return_value = None

        init("valkey", "6379", enable=True, start=False)

        mock_systemctl.assert_called()
        calls = [str(call) for call in mock_systemctl.call_args_list]
        assert any("enable" in call for call in calls)

    @patch("ots_containers.commands.service.app.check_default_service_conflict")
    @patch("ots_containers.commands.service.app.systemctl")
    @patch("ots_containers.commands.service.app.create_secrets_file")
    @patch("ots_containers.commands.service.app.ensure_data_dir")
    @patch("ots_containers.commands.service.app.update_config_value")
    @patch("ots_containers.commands.service.app.copy_default_config")
    def test_init_starts_service(
        self,
        mock_copy,
        mock_update,
        mock_data,
        mock_secrets,
        mock_systemctl,
        mock_check_conflict,
        tmp_path,
    ):
        """Test init starts service when start=True."""
        mock_copy.return_value = tmp_path / "test.conf"
        mock_data.return_value = tmp_path / "data"
        mock_secrets.return_value = None

        init("valkey", "6379", enable=False, start=True)

        mock_systemctl.assert_called()
        calls = [str(call) for call in mock_systemctl.call_args_list]
        assert any("start" in call for call in calls)


class TestInitIdempotency:
    """Tests for init command idempotency (BUG: config modification on re-run)."""

    @patch("ots_containers.commands.service.app.check_default_service_conflict")
    @patch("ots_containers.commands.service.app.update_config_value")
    @patch("ots_containers.commands.service.app.copy_default_config")
    def test_init_skips_modifications_when_config_exists(
        self,
        mock_copy,
        mock_update,
        mock_check_conflict,
        capsys,
    ):
        """init should skip all modifications when config already exists (idempotent)."""
        mock_copy.side_effect = FileExistsError("Config already exists: /etc/valkey/...")

        init("valkey", "6379", start=False, enable=False)

        # update_config_value must NOT be called when config already exists
        mock_update.assert_not_called()
        captured = capsys.readouterr()
        assert "already exists" in captured.out
        assert "Skipping" in captured.out

    @patch("ots_containers.commands.service.app.check_default_service_conflict")
    @patch("ots_containers.commands.service.app.update_config_value")
    @patch("ots_containers.commands.service.app.copy_default_config")
    def test_init_returns_early_when_config_exists(
        self,
        mock_copy,
        mock_update,
        mock_check_conflict,
        capsys,
    ):
        """init should return early (not reach start/enable) when config already exists."""
        mock_copy.side_effect = FileExistsError("Config already exists")

        # Should not raise SystemExit - just return cleanly
        init("valkey", "6379", start=True, enable=True)

        captured = capsys.readouterr()
        assert "already configured" in captured.out.lower()

    @patch("ots_containers.commands.service.app.check_default_service_conflict")
    @patch("ots_containers.commands.service.app.systemctl")
    @patch("ots_containers.commands.service.app.create_secrets_file")
    @patch("ots_containers.commands.service.app.ensure_data_dir")
    @patch("ots_containers.commands.service.app.update_config_value")
    @patch("ots_containers.commands.service.app.copy_default_config")
    def test_init_force_overwrites_existing_config(
        self,
        mock_copy,
        mock_update,
        mock_data,
        mock_secrets,
        mock_systemctl,
        mock_check_conflict,
        tmp_path,
        capsys,
    ):
        """init --force should delete existing config and recreate from defaults."""
        existing_config = tmp_path / "6379.conf"
        existing_config.write_text("old config content\n")

        call_count = [0]

        def copy_side_effect(pkg, instance):
            call_count[0] += 1
            if call_count[0] == 1:
                raise FileExistsError("Config already exists")
            return tmp_path / "6379.conf"

        mock_copy.side_effect = copy_side_effect
        mock_data.return_value = tmp_path / "data"
        mock_secrets.return_value = None

        # Mock the pkg.config_file to return our existing config
        with patch("ots_containers.commands.service.app.get_package") as mock_get_pkg:
            mock_pkg = MagicMock()
            mock_pkg.name = "valkey"
            mock_pkg.template_unit = "valkey-server@.service"
            mock_pkg.port_config_key = "port"
            mock_pkg.bind_config_key = "bind"
            mock_pkg.config_file.return_value = existing_config
            mock_pkg.data_dir = tmp_path
            mock_pkg.secrets = None
            mock_pkg.instance_unit.return_value = "valkey-server@6379.service"
            mock_pkg.default_config = tmp_path / "default.conf"
            mock_get_pkg.return_value = mock_pkg

            init("valkey", "6379", force=True, start=False, enable=False)

        captured = capsys.readouterr()
        assert "force" in captured.out.lower() or "Removed" in captured.out

    def test_init_dry_run_existing_config_shows_skip_notice(self, capsys, tmp_path):
        """init --dry-run with existing config should show skip notice."""
        with patch("ots_containers.commands.service.app.get_package") as mock_get_pkg:
            mock_pkg = MagicMock()
            mock_pkg.name = "valkey"
            mock_pkg.template_unit = "valkey-server@.service"
            mock_pkg.port_config_key = "port"
            mock_pkg.bind_config_key = "bind"
            existing_config = tmp_path / "6379.conf"
            existing_config.write_text("port 6379\n")
            mock_pkg.config_file.return_value = existing_config
            mock_pkg.data_dir = tmp_path
            mock_pkg.secrets = None
            mock_get_pkg.return_value = mock_pkg

            init("valkey", "6379", dry_run=True, start=False, enable=False)

        captured = capsys.readouterr()
        assert "already exists" in captured.out
        assert "skip" in captured.out.lower() or "force" in captured.out.lower()


class TestEnableCommand:
    """Tests for enable command."""

    @patch("ots_containers.commands.service.app.systemctl")
    def test_enable_calls_systemctl(self, mock_systemctl, capsys):
        """Test enable calls systemctl enable."""
        enable("valkey", "6379")

        mock_systemctl.assert_called_once_with("enable", "valkey-server@6379.service")

    @patch("ots_containers.commands.service.app.systemctl")
    def test_enable_prints_enabled(self, mock_systemctl, capsys):
        """Test enable prints enabled message."""
        enable("valkey", "6379")

        captured = capsys.readouterr()
        assert "Enabling" in captured.out
        assert "Enabled" in captured.out


class TestDisableCommand:
    """Tests for disable command."""

    @patch("ots_containers.commands.service.app.systemctl")
    def test_disable_calls_systemctl(self, mock_systemctl, capsys):
        """Test disable calls systemctl stop and disable."""
        disable("valkey", "6379", yes=True)

        # Should call stop then disable
        assert mock_systemctl.call_count >= 2


class TestStartCommand:
    """Tests for start command."""

    @patch("ots_containers.commands.service.app.systemctl")
    def test_start_calls_systemctl(self, mock_systemctl, capsys):
        """Test start calls systemctl start."""
        start("valkey", "6379")

        mock_systemctl.assert_called_once_with("start", "valkey-server@6379.service")


class TestStopCommand:
    """Tests for stop command."""

    @patch("ots_containers.commands.service.app.systemctl")
    def test_stop_calls_systemctl(self, mock_systemctl, capsys):
        """Test stop calls systemctl stop."""
        stop("valkey", "6379")

        mock_systemctl.assert_called_once_with("stop", "valkey-server@6379.service")


class TestRestartCommand:
    """Tests for restart command."""

    @patch("ots_containers.commands.service.app.systemctl")
    def test_restart_calls_systemctl(self, mock_systemctl, capsys):
        """Test restart calls systemctl restart."""
        restart("valkey", "6379")

        mock_systemctl.assert_called_once_with("restart", "valkey-server@6379.service")


class TestStatusCommand:
    """Tests for status command."""

    @patch("ots_containers.commands.service.app.systemctl")
    def test_status_calls_systemctl_with_instance(self, mock_systemctl, capsys):
        """Test status calls systemctl status for specific instance."""
        mock_systemctl.return_value = MagicMock(stdout="active", stderr="")

        status("valkey", "6379")

        mock_systemctl.assert_called_once_with("status", "valkey-server@6379.service", check=False)

    @patch("subprocess.run")
    def test_status_lists_all_without_instance(self, mock_run, capsys):
        """Test status lists all instances when no instance given."""
        mock_run.return_value = MagicMock(stdout="", stderr="")

        status("valkey", None)

        mock_run.assert_called_once()
        call_args = mock_run.call_args[0][0]
        assert "list-units" in call_args


class TestLogsCommand:
    """Tests for logs command."""

    @patch("subprocess.run")
    def test_logs_calls_journalctl(self, mock_run):
        """Test logs calls journalctl."""
        logs("valkey", "6379")

        mock_run.assert_called_once()
        call_args = mock_run.call_args[0][0]
        assert "journalctl" in call_args
        assert "-u" in call_args
        assert "valkey-server@6379.service" in call_args

    @patch("subprocess.run")
    def test_logs_with_follow(self, mock_run):
        """Test logs with follow flag."""
        logs("valkey", "6379", follow=True)

        call_args = mock_run.call_args[0][0]
        assert "-f" in call_args

    @patch("subprocess.run")
    def test_logs_with_lines(self, mock_run):
        """Test logs with lines parameter."""
        logs("valkey", "6379", lines=100)

        call_args = mock_run.call_args[0][0]
        assert "-n" in call_args
        assert "100" in call_args


class TestListCommand:
    """Tests for list command."""

    @patch("ots_containers.commands.service.app.is_service_enabled")
    @patch("ots_containers.commands.service.app.is_service_active")
    @patch("subprocess.run")
    def test_list_calls_systemctl(self, mock_run, mock_active, mock_enabled, capsys):
        """Test list calls systemctl list-units."""
        mock_run.return_value = MagicMock(stdout="")

        list_instances("valkey")

        mock_run.assert_called_once()
        call_args = mock_run.call_args[0][0]
        assert "list-units" in call_args
        assert "--type=service" in call_args


class TestInitDefaults:
    """Verify that --start and --enable default to False (opt-in, not opt-out)."""

    @patch("ots_containers.commands.service.app.check_default_service_conflict")
    @patch("ots_containers.commands.service.app.systemctl")
    @patch("ots_containers.commands.service.app.create_secrets_file")
    @patch("ots_containers.commands.service.app.ensure_data_dir")
    @patch("ots_containers.commands.service.app.update_config_value")
    @patch("ots_containers.commands.service.app.copy_default_config")
    def test_init_does_not_start_by_default(
        self,
        mock_copy,
        mock_update,
        mock_data,
        mock_secrets,
        mock_systemctl,
        mock_check_conflict,
        tmp_path,
    ):
        """init() must not call systemctl start unless --start is explicitly set."""
        mock_copy.return_value = tmp_path / "test.conf"
        mock_data.return_value = tmp_path / "data"
        mock_secrets.return_value = None

        # Call with all defaults — neither start nor enable specified
        init("valkey", "6379")

        calls = [str(call) for call in mock_systemctl.call_args_list]
        assert not any("start" in call for call in calls), (
            "systemctl start was called despite --start defaulting to False"
        )

    @patch("ots_containers.commands.service.app.check_default_service_conflict")
    @patch("ots_containers.commands.service.app.systemctl")
    @patch("ots_containers.commands.service.app.create_secrets_file")
    @patch("ots_containers.commands.service.app.ensure_data_dir")
    @patch("ots_containers.commands.service.app.update_config_value")
    @patch("ots_containers.commands.service.app.copy_default_config")
    def test_init_does_not_enable_by_default(
        self,
        mock_copy,
        mock_update,
        mock_data,
        mock_secrets,
        mock_systemctl,
        mock_check_conflict,
        tmp_path,
    ):
        """init() must not call systemctl enable unless --enable is explicitly set."""
        mock_copy.return_value = tmp_path / "test.conf"
        mock_data.return_value = tmp_path / "data"
        mock_secrets.return_value = None

        init("valkey", "6379")

        calls = [str(call) for call in mock_systemctl.call_args_list]
        assert not any("enable" in call for call in calls), (
            "systemctl enable was called despite --enable defaulting to False"
        )

    @patch("ots_containers.commands.service.app.check_default_service_conflict")
    @patch("ots_containers.commands.service.app.systemctl")
    @patch("ots_containers.commands.service.app.create_secrets_file")
    @patch("ots_containers.commands.service.app.ensure_data_dir")
    @patch("ots_containers.commands.service.app.update_config_value")
    @patch("ots_containers.commands.service.app.copy_default_config")
    def test_init_no_systemctl_calls_by_default(
        self,
        mock_copy,
        mock_update,
        mock_data,
        mock_secrets,
        mock_systemctl,
        mock_check_conflict,
        tmp_path,
    ):
        """init() with all defaults should not invoke systemctl at all."""
        mock_copy.return_value = tmp_path / "test.conf"
        mock_data.return_value = tmp_path / "data"
        mock_secrets.return_value = None

        init("valkey", "6379")

        mock_systemctl.assert_not_called()


class TestServiceErrorPaths:
    """Tests for error paths that should raise SystemExit(1).

    Each command wraps the systemctl call in a try/except and raises
    SystemExit(1) on CalledProcessError so the caller gets a non-zero exit.
    """

    import subprocess as _subprocess

    @patch("ots_containers.commands.service.app.check_default_service_conflict")
    @patch("ots_containers.commands.service.app.systemctl")
    @patch("ots_containers.commands.service.app.create_secrets_file")
    @patch("ots_containers.commands.service.app.ensure_data_dir")
    @patch("ots_containers.commands.service.app.update_config_value")
    @patch("ots_containers.commands.service.app.copy_default_config")
    def test_init_copy_default_config_file_not_found_exits(
        self,
        mock_copy,
        mock_update,
        mock_data,
        mock_secrets,
        mock_systemctl,
        mock_check_conflict,
        capsys,
        tmp_path,
    ):
        """init() exits with code 1 when copy_default_config raises FileNotFoundError."""
        import pytest

        mock_copy.side_effect = FileNotFoundError("package default config not found")
        mock_data.return_value = tmp_path / "data"
        mock_secrets.return_value = None

        with pytest.raises(SystemExit) as exc_info:
            init("valkey", "6379", start=False, enable=False)

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "ERROR" in captured.out

    @patch("ots_containers.commands.service.app.check_default_service_conflict")
    @patch("ots_containers.commands.service.app.systemctl")
    @patch("ots_containers.commands.service.app.create_secrets_file")
    @patch("ots_containers.commands.service.app.ensure_data_dir")
    @patch("ots_containers.commands.service.app.update_config_value")
    @patch("ots_containers.commands.service.app.copy_default_config")
    def test_init_start_called_process_error_exits(
        self,
        mock_copy,
        mock_update,
        mock_data,
        mock_secrets,
        mock_systemctl,
        mock_check_conflict,
        capsys,
        tmp_path,
    ):
        """init() exits with code 1 when systemctl start raises CalledProcessError."""
        import subprocess

        import pytest

        mock_copy.return_value = tmp_path / "test.conf"
        mock_data.return_value = tmp_path / "data"
        mock_secrets.return_value = None

        # First call (enable, if any) succeeds; start call raises
        def systemctl_side_effect(action, unit, **kwargs):
            if action == "start":
                raise subprocess.CalledProcessError(1, "systemctl", stderr="start failed")
            return MagicMock()

        mock_systemctl.side_effect = systemctl_side_effect

        with pytest.raises(SystemExit) as exc_info:
            init("valkey", "6379", start=True, enable=False)

        assert exc_info.value.code == 1

    @patch("ots_containers.commands.service.app.systemctl")
    def test_enable_called_process_error_exits(self, mock_systemctl, capsys):
        """enable() exits with code 1 when systemctl enable raises CalledProcessError."""
        import subprocess

        import pytest

        mock_systemctl.side_effect = subprocess.CalledProcessError(
            1, "systemctl", stderr="enable failed"
        )

        with pytest.raises(SystemExit) as exc_info:
            enable("valkey", "6379")

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "ERROR" in captured.out

    @patch("ots_containers.commands.service.app.systemctl")
    def test_disable_called_process_error_exits(self, mock_systemctl, capsys):
        """disable() exits with code 1 when systemctl disable raises CalledProcessError."""
        import subprocess

        import pytest

        def systemctl_side_effect(action, unit, **kwargs):
            if action == "disable":
                raise subprocess.CalledProcessError(1, "systemctl", stderr="disable failed")
            return MagicMock()

        mock_systemctl.side_effect = systemctl_side_effect

        with pytest.raises(SystemExit) as exc_info:
            disable("valkey", "6379", yes=True)

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "ERROR" in captured.out

    @patch("ots_containers.commands.service.app.systemctl")
    def test_start_called_process_error_exits(self, mock_systemctl, capsys):
        """start() exits with code 1 when systemctl start raises CalledProcessError."""
        import subprocess

        import pytest

        mock_systemctl.side_effect = subprocess.CalledProcessError(
            1, "systemctl", stderr="start failed"
        )

        with pytest.raises(SystemExit) as exc_info:
            start("valkey", "6379")

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "ERROR" in captured.out

    @patch("ots_containers.commands.service.app.systemctl")
    def test_stop_called_process_error_exits(self, mock_systemctl, capsys):
        """stop() exits with code 1 when systemctl stop raises CalledProcessError."""
        import subprocess

        import pytest

        mock_systemctl.side_effect = subprocess.CalledProcessError(
            1, "systemctl", stderr="stop failed"
        )

        with pytest.raises(SystemExit) as exc_info:
            stop("valkey", "6379")

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "ERROR" in captured.out

    @patch("ots_containers.commands.service.app.systemctl")
    def test_restart_called_process_error_exits(self, mock_systemctl, capsys):
        """restart() exits with code 1 when systemctl restart raises CalledProcessError."""
        import subprocess

        import pytest

        mock_systemctl.side_effect = subprocess.CalledProcessError(
            1, "systemctl", stderr="restart failed"
        )

        with pytest.raises(SystemExit) as exc_info:
            restart("valkey", "6379")

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "ERROR" in captured.out


class TestInitNonNumericInstance:
    """Tests for init with non-numeric instance name (BUG-1)."""

    def test_init_non_numeric_instance_without_port_exits(self, capsys):
        """init with non-numeric instance and no --port should raise SystemExit."""
        import pytest

        with pytest.raises(SystemExit):
            init("valkey", "primary")

    def test_init_non_numeric_instance_with_port_succeeds(self, capsys, tmp_path):
        """init with non-numeric instance and explicit --port should work."""
        with (
            patch("ots_containers.commands.service.app.check_default_service_conflict"),
            patch("ots_containers.commands.service.app.copy_default_config") as mock_copy,
            patch("ots_containers.commands.service.app.update_config_value"),
            patch("ots_containers.commands.service.app.ensure_data_dir") as mock_data,
            patch("ots_containers.commands.service.app.create_secrets_file") as mock_secrets,
            patch("ots_containers.commands.service.app.systemctl"),
        ):
            mock_copy.return_value = tmp_path / "primary.conf"
            mock_data.return_value = tmp_path / "data"
            mock_secrets.return_value = None

            init("valkey", "primary", port=6379, start=False, enable=False)

        captured = capsys.readouterr()
        assert "primary" in captured.out
        assert "6379" in captured.out


class TestListAllWithInstances:
    """Tests for list_all (default command) when instances are found."""

    @patch("ots_containers.commands.service.app.is_service_enabled")
    @patch("ots_containers.commands.service.app.is_service_active")
    @patch("subprocess.run")
    def test_list_all_with_instances_shows_table(self, mock_run, mock_active, mock_enabled, capsys):
        """list_all should display a table when instances are found."""
        mock_active.return_value = True
        mock_enabled.return_value = True
        # Return output that looks like systemctl --plain output for valkey
        mock_run.return_value = MagicMock(
            stdout="valkey-server@6379.service loaded active running Valkey\n"
        )
        with patch("ots_containers.commands.service.app.get_package") as mock_get_pkg:
            mock_pkg = MagicMock()
            mock_pkg.name = "valkey"
            mock_pkg.template = "valkey-server@"
            mock_pkg.config_file.return_value = MagicMock(exists=lambda: True)
            mock_get_pkg.return_value = mock_pkg

            with patch(
                "ots_containers.commands.service.app.list_packages", return_value=["valkey"]
            ):
                list_all()

        captured = capsys.readouterr()
        assert "PACKAGE" in captured.out
        assert "INSTANCE" in captured.out

    @patch("ots_containers.commands.service.app.is_service_enabled")
    @patch("ots_containers.commands.service.app.is_service_active")
    @patch("subprocess.run")
    def test_list_all_json_output(self, mock_run, mock_active, mock_enabled, capsys):
        """list_all --json should output valid JSON."""
        import json

        mock_active.return_value = True
        mock_enabled.return_value = False
        mock_run.return_value = MagicMock(
            stdout="valkey-server@6379.service loaded active running Valkey\n"
        )
        with patch("ots_containers.commands.service.app.get_package") as mock_get_pkg:
            mock_pkg = MagicMock()
            mock_pkg.name = "valkey"
            mock_pkg.template = "valkey-server@"
            mock_pkg.config_file.return_value = MagicMock(exists=lambda: False)
            mock_get_pkg.return_value = mock_pkg

            with patch(
                "ots_containers.commands.service.app.list_packages", return_value=["valkey"]
            ):
                list_all(json_output=True)

        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert isinstance(data, list)


class TestListInstancesWithInstances:
    """Tests for list_instances when instances are found."""

    @patch("ots_containers.commands.service.app.is_service_enabled")
    @patch("ots_containers.commands.service.app.is_service_active")
    @patch("subprocess.run")
    def test_list_instances_shows_instance_details(
        self, mock_run, mock_active, mock_enabled, capsys, tmp_path
    ):
        """list_instances should show each instance when systemctl returns data."""
        mock_active.return_value = True
        mock_enabled.return_value = True
        mock_run.return_value = MagicMock(
            stdout="valkey-server@6379.service loaded active running Valkey\n"
        )

        with patch("ots_containers.commands.service.app.get_package") as mock_get_pkg:
            mock_pkg = MagicMock()
            mock_pkg.name = "valkey"
            mock_pkg.template = "valkey-server@"
            mock_pkg.config_file.return_value = MagicMock(exists=lambda: True)
            mock_pkg.use_instances_subdir = True
            mock_pkg.instances_dir = tmp_path
            mock_get_pkg.return_value = mock_pkg

            list_instances("valkey")

        captured = capsys.readouterr()
        assert "6379" in captured.out

    @patch("ots_containers.commands.service.app.is_service_enabled")
    @patch("ots_containers.commands.service.app.is_service_active")
    @patch("subprocess.run")
    def test_list_instances_json_output(
        self, mock_run, mock_active, mock_enabled, capsys, tmp_path
    ):
        """list_instances --json should output valid JSON."""
        import json

        mock_active.return_value = False
        mock_enabled.return_value = False
        mock_run.return_value = MagicMock(
            stdout="valkey-server@6379.service loaded inactive dead Valkey\n"
        )

        with patch("ots_containers.commands.service.app.get_package") as mock_get_pkg:
            mock_pkg = MagicMock()
            mock_pkg.name = "valkey"
            mock_pkg.template = "valkey-server@"
            mock_pkg.config_file.return_value = MagicMock(exists=lambda: False)
            mock_pkg.use_instances_subdir = True
            mock_pkg.instances_dir = tmp_path
            mock_get_pkg.return_value = mock_pkg

            list_instances("valkey", json_output=True)

        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert isinstance(data, list)
        assert data[0]["instance"] == "6379"


class TestInitDryRunCreate:
    """Tests for init --dry-run when config does not exist."""

    def test_init_dry_run_no_existing_config_shows_create(self, capsys, tmp_path):
        """init --dry-run with no existing config should show 'Would create'."""
        non_existing = tmp_path / "nope.conf"

        with patch("ots_containers.commands.service.app.get_package") as mock_get_pkg:
            mock_pkg = MagicMock()
            mock_pkg.name = "valkey"
            mock_pkg.template_unit = "valkey-server@.service"
            mock_pkg.port_config_key = "port"
            mock_pkg.bind_config_key = "bind"
            mock_pkg.config_file.return_value = non_existing
            mock_pkg.data_dir = tmp_path
            mock_pkg.secrets = None
            mock_get_pkg.return_value = mock_pkg

            init("valkey", "6379", dry_run=True, start=False, enable=False)

        captured = capsys.readouterr()
        assert "create" in captured.out.lower() or "Would" in captured.out


class TestInitForceFileNotFound:
    """Tests for init --force when default config is missing after removing existing."""

    @patch("ots_containers.commands.service.app.check_default_service_conflict")
    @patch("ots_containers.commands.service.app.systemctl")
    @patch("ots_containers.commands.service.app.create_secrets_file")
    @patch("ots_containers.commands.service.app.ensure_data_dir")
    @patch("ots_containers.commands.service.app.update_config_value")
    @patch("ots_containers.commands.service.app.copy_default_config")
    def test_init_force_recreate_fails_with_file_not_found(
        self,
        mock_copy,
        mock_update,
        mock_data,
        mock_secrets,
        mock_systemctl,
        mock_check_conflict,
        capsys,
        tmp_path,
    ):
        """init --force exits when default config is missing after removing existing."""
        import pytest

        existing_config = tmp_path / "6379.conf"
        existing_config.write_text("old config\n")

        call_count = [0]

        def copy_side_effect(pkg, instance):
            call_count[0] += 1
            if call_count[0] == 1:
                raise FileExistsError("exists")
            raise FileNotFoundError("default config missing")

        mock_copy.side_effect = copy_side_effect

        with patch("ots_containers.commands.service.app.get_package") as mock_get_pkg:
            mock_pkg = MagicMock()
            mock_pkg.name = "valkey"
            mock_pkg.template_unit = "valkey-server@.service"
            mock_pkg.port_config_key = "port"
            mock_pkg.bind_config_key = "bind"
            mock_pkg.config_file.return_value = existing_config
            mock_pkg.data_dir = tmp_path
            mock_pkg.secrets = None
            mock_pkg.instance_unit.return_value = "valkey-server@6379.service"
            mock_get_pkg.return_value = mock_pkg

            with pytest.raises(SystemExit) as exc_info:
                init("valkey", "6379", force=True, start=False, enable=False)

        assert exc_info.value.code == 1


class TestInitEnableWarning:
    """Tests for init --enable when systemctl enable raises CalledProcessError (warning)."""

    @patch("ots_containers.commands.service.app.check_default_service_conflict")
    @patch("ots_containers.commands.service.app.systemctl")
    @patch("ots_containers.commands.service.app.create_secrets_file")
    @patch("ots_containers.commands.service.app.ensure_data_dir")
    @patch("ots_containers.commands.service.app.update_config_value")
    @patch("ots_containers.commands.service.app.copy_default_config")
    def test_init_enable_failure_shows_warning_not_exit(
        self,
        mock_copy,
        mock_update,
        mock_data,
        mock_secrets,
        mock_systemctl,
        mock_check_conflict,
        capsys,
        tmp_path,
    ):
        """init --enable with systemctl CalledProcessError prints WARNING but doesn't exit."""
        import subprocess

        mock_copy.return_value = tmp_path / "test.conf"
        mock_data.return_value = tmp_path / "data"
        mock_secrets.return_value = None

        def systemctl_side_effect(action, unit, **kwargs):
            if action == "enable":
                raise subprocess.CalledProcessError(1, "systemctl", stderr="permission denied")
            return MagicMock()

        mock_systemctl.side_effect = systemctl_side_effect

        # Should NOT raise SystemExit — enable failure is a warning
        init("valkey", "6379", enable=True, start=False)

        captured = capsys.readouterr()
        assert "WARNING" in captured.out or "Could not enable" in captured.out


class TestDisableAbort:
    """Tests for disable confirmation prompt abort."""

    @patch("ots_containers.commands.service.app.systemctl")
    def test_disable_aborts_when_user_says_no(self, mock_systemctl, capsys, monkeypatch):
        """disable should abort without calling systemctl when user declines."""
        monkeypatch.setattr("builtins.input", lambda _: "n")

        disable("valkey", "6379", yes=False)

        mock_systemctl.assert_not_called()
        captured = capsys.readouterr()
        assert "Aborted" in captured.out
