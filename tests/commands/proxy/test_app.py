# tests/commands/proxy/test_app.py
"""Tests for proxy app commands."""

import pytest


class TestRenderCommand:
    """Test render command."""

    def test_render_dry_run_prints_output(self, tmp_path, mocker, capsys):
        """Should print rendered content in dry-run mode."""
        from ots_containers.commands.proxy.app import render

        template = tmp_path / "test.template"
        template.write_text("Hello $WORLD")

        # Patch where it's used, not where it's defined
        mocker.patch(
            "ots_containers.commands.proxy.app.render_template",
            return_value="Hello Rendered",
        )

        render(template=template, output=tmp_path / "out", dry_run=True)

        captured = capsys.readouterr()
        assert "Hello Rendered" in captured.out

    def test_render_writes_to_output(self, tmp_path, mocker, capsys):
        """Should write rendered content to output file."""
        from ots_containers.commands.proxy.app import render

        template = tmp_path / "test.template"
        template.write_text("Hello $WORLD")
        output = tmp_path / "output.conf"

        mocker.patch(
            "ots_containers.commands.proxy.app.render_template",
            return_value="Hello Rendered",
        )
        mocker.patch("ots_containers.commands.proxy.app.validate_caddy_config")

        render(template=template, output=output, dry_run=False)

        assert output.exists()
        assert output.read_text() == "Hello Rendered"
        captured = capsys.readouterr()
        assert "[ok]" in captured.out

    def test_render_validates_before_writing(self, tmp_path, mocker):
        """Should call validate_caddy_config before writing."""
        from ots_containers.commands.proxy.app import render

        template = tmp_path / "test.template"
        template.write_text("Hello")
        output = tmp_path / "output.conf"

        mocker.patch(
            "ots_containers.commands.proxy.app.render_template",
            return_value="rendered content",
        )
        mock_validate = mocker.patch("ots_containers.commands.proxy.app.validate_caddy_config")

        render(template=template, output=output, dry_run=False)

        mock_validate.assert_called_once_with("rendered content")

    def test_render_error_exits(self, tmp_path, mocker):
        """Should exit with error message on ProxyError."""
        from ots_containers.commands.proxy._helpers import ProxyError
        from ots_containers.commands.proxy.app import render

        template = tmp_path / "test.template"
        template.write_text("Hello")

        mocker.patch(
            "ots_containers.commands.proxy.app.render_template",
            side_effect=ProxyError("test error"),
        )

        with pytest.raises(SystemExit) as exc_info:
            render(template=template, output=tmp_path / "out", dry_run=False)

        assert exc_info.value.code is not None
        assert "test error" in str(exc_info.value)

    def test_render_uses_config_defaults(self, mocker):
        """Should use Config paths when no args provided."""
        from pathlib import Path

        from ots_containers.commands.proxy.app import render
        from ots_containers.config import Config

        mock_render = mocker.patch(
            "ots_containers.commands.proxy.app.render_template",
            return_value="content",
        )
        mocker.patch("ots_containers.commands.proxy.app.validate_caddy_config")
        # Mock the file write
        mocker.patch.object(Path, "write_text")
        mocker.patch.object(Path, "mkdir")

        cfg = Config()

        render(template=None, output=None, dry_run=False)

        mock_render.assert_called_once_with(cfg.proxy_template)


class TestReloadCommand:
    """Test reload command."""

    def test_reload_calls_helper(self, mocker, capsys):
        """Should call reload_caddy helper."""
        from ots_containers.commands.proxy.app import reload

        mock_reload = mocker.patch("ots_containers.commands.proxy.app.reload_caddy")

        reload()

        mock_reload.assert_called_once()
        captured = capsys.readouterr()
        assert "[ok]" in captured.out

    def test_reload_error_exits(self, mocker):
        """Should exit with error message on ProxyError."""
        from ots_containers.commands.proxy._helpers import ProxyError
        from ots_containers.commands.proxy.app import reload

        mocker.patch(
            "ots_containers.commands.proxy.app.reload_caddy",
            side_effect=ProxyError("reload failed"),
        )

        with pytest.raises(SystemExit) as exc_info:
            reload()

        assert exc_info.value.code is not None
        assert "reload failed" in str(exc_info.value)


class TestStatusCommand:
    """Test status command."""

    def test_status_calls_systemctl(self, mocker, capsys):
        """Should call systemctl status caddy."""
        from ots_containers.commands.proxy.app import status

        mock_run = mocker.patch(
            "subprocess.run",
            return_value=mocker.MagicMock(stdout="active (running)", stderr=""),
        )

        status()

        mock_run.assert_called_once()
        call_args = mock_run.call_args[0][0]
        assert "systemctl" in call_args
        assert "status" in call_args
        assert "caddy" in call_args

        captured = capsys.readouterr()
        assert "active (running)" in captured.out

    def test_status_shows_stderr_if_present(self, mocker, capsys):
        """Should show stderr output if present."""
        from ots_containers.commands.proxy.app import status

        mocker.patch(
            "subprocess.run",
            return_value=mocker.MagicMock(stdout="output", stderr="warning message"),
        )

        status()

        captured = capsys.readouterr()
        assert "output" in captured.out
        assert "warning message" in captured.out

    def test_status_error_exits(self, mocker):
        """Should exit on exception."""
        from ots_containers.commands.proxy.app import status

        mocker.patch("subprocess.run", side_effect=Exception("systemctl failed"))

        with pytest.raises(SystemExit) as exc_info:
            status()

        assert exc_info.value.code is not None


class TestValidateCommand:
    """Test validate command."""

    def test_validate_calls_caddy(self, tmp_path, mocker, capsys):
        """Should call caddy validate with config file."""
        from ots_containers.commands.proxy.app import validate

        config = tmp_path / "Caddyfile"
        config.write_text("localhost { }")

        mock_run = mocker.patch(
            "subprocess.run",
            return_value=mocker.MagicMock(returncode=0, stdout="", stderr=""),
        )

        validate(config_file=config)

        mock_run.assert_called_once()
        call_args = mock_run.call_args[0][0]
        assert "caddy" in call_args
        assert "validate" in call_args
        assert "--config" in call_args
        assert str(config) in call_args

        captured = capsys.readouterr()
        assert "[ok]" in captured.out

    def test_validate_uses_default_path(self, tmp_path, mocker):
        """Should use default config path when none provided."""
        from ots_containers.commands.proxy.app import validate

        # Create a mock config with a real path that exists
        default_config = tmp_path / "Caddyfile"
        default_config.write_text("localhost { }")

        mock_cfg = mocker.MagicMock()
        mock_cfg.proxy_config = default_config
        mocker.patch(
            "ots_containers.commands.proxy.app.Config",
            return_value=mock_cfg,
        )

        mock_run = mocker.patch(
            "subprocess.run",
            return_value=mocker.MagicMock(returncode=0, stdout="", stderr=""),
        )

        validate(config_file=None)

        mock_run.assert_called_once()
        call_args = mock_run.call_args[0][0]
        assert str(default_config) in call_args

    def test_validate_success_with_stdout(self, tmp_path, mocker, capsys):
        """Should print stdout when validation succeeds with output."""
        from ots_containers.commands.proxy.app import validate

        config = tmp_path / "Caddyfile"
        config.write_text("localhost { }")

        mocker.patch(
            "subprocess.run",
            return_value=mocker.MagicMock(returncode=0, stdout="Valid config", stderr=""),
        )

        validate(config_file=config)

        captured = capsys.readouterr()
        assert "[ok]" in captured.out
        assert "Valid config" in captured.out

    def test_validate_missing_file_exits(self, tmp_path):
        """Should exit if config file doesn't exist."""
        from ots_containers.commands.proxy.app import validate

        missing = tmp_path / "nonexistent"

        with pytest.raises(SystemExit) as exc_info:
            validate(config_file=missing)

        assert exc_info.value.code is not None
        assert "not found" in str(exc_info.value)

    def test_validate_failure_exits(self, tmp_path, mocker):
        """Should exit on validation failure."""
        from ots_containers.commands.proxy.app import validate

        config = tmp_path / "Caddyfile"
        config.write_text("invalid config")

        mocker.patch(
            "subprocess.run",
            return_value=mocker.MagicMock(returncode=1, stdout="", stderr="syntax error"),
        )

        with pytest.raises(SystemExit) as exc_info:
            validate(config_file=config)

        assert exc_info.value.code == 1

    def test_validate_caddy_not_found_exits(self, tmp_path, mocker):
        """Should exit if caddy not in PATH."""
        from ots_containers.commands.proxy.app import validate

        config = tmp_path / "Caddyfile"
        config.write_text("localhost { }")

        mocker.patch("subprocess.run", side_effect=FileNotFoundError())

        with pytest.raises(SystemExit) as exc_info:
            validate(config_file=config)

        assert "caddy not found" in str(exc_info.value)
