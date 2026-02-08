# tests/commands/proxy/test_helpers.py
"""Tests for proxy command helpers."""

import subprocess

import pytest


class TestRenderTemplate:
    """Test render_template function."""

    def test_render_template_calls_envsubst(self, tmp_path, mocker):
        """Should call envsubst with template content."""
        from ots_containers.commands.proxy._helpers import render_template

        template = tmp_path / "test.template"
        template.write_text("Hello $NAME")

        mock_result = mocker.Mock()
        mock_result.stdout = "Hello World"
        mock_run = mocker.patch("subprocess.run", return_value=mock_result)

        result = render_template(template)

        assert result == "Hello World"
        mock_run.assert_called_once()
        call_args = mock_run.call_args
        assert call_args[0][0] == ["envsubst"]
        assert call_args[1]["capture_output"] is True
        assert call_args[1]["text"] is True
        assert call_args[1]["check"] is True

    def test_render_template_missing_file_raises(self, tmp_path):
        """Should raise ProxyError when template not found."""
        from ots_containers.commands.proxy._helpers import ProxyError, render_template

        missing = tmp_path / "nonexistent.template"

        with pytest.raises(ProxyError) as exc_info:
            render_template(missing)

        assert "Template not found" in str(exc_info.value)

    def test_render_template_envsubst_failure_raises(self, tmp_path, mocker):
        """Should raise ProxyError when envsubst fails."""
        from ots_containers.commands.proxy._helpers import ProxyError, render_template

        template = tmp_path / "test.template"
        template.write_text("Hello $NAME")

        mocker.patch(
            "subprocess.run",
            side_effect=subprocess.CalledProcessError(1, "envsubst", stderr="error"),
        )

        with pytest.raises(ProxyError) as exc_info:
            render_template(template)

        assert "envsubst failed" in str(exc_info.value)

    def test_render_template_envsubst_not_found_raises(self, tmp_path, mocker):
        """Should raise ProxyError when envsubst not installed."""
        from ots_containers.commands.proxy._helpers import ProxyError, render_template

        template = tmp_path / "test.template"
        template.write_text("Hello $NAME")

        mocker.patch(
            "subprocess.run",
            side_effect=FileNotFoundError("envsubst not found"),
        )

        with pytest.raises(ProxyError) as exc_info:
            render_template(template)

        assert "envsubst not found" in str(exc_info.value)


class TestValidateCaddyConfig:
    """Test validate_caddy_config function."""

    def test_validate_caddy_config_success(self, mocker):
        """Should pass when caddy validate succeeds."""
        from ots_containers.commands.proxy._helpers import validate_caddy_config

        mock_result = mocker.Mock()
        mock_result.returncode = 0
        mocker.patch("subprocess.run", return_value=mock_result)

        # Should not raise
        validate_caddy_config("localhost:8080 {\n  respond 200\n}")

    def test_validate_caddy_config_failure_raises(self, mocker):
        """Should raise ProxyError when validation fails."""
        from ots_containers.commands.proxy._helpers import ProxyError, validate_caddy_config

        mock_result = mocker.Mock()
        mock_result.returncode = 1
        mock_result.stderr = "syntax error at line 1"
        mocker.patch("subprocess.run", return_value=mock_result)

        with pytest.raises(ProxyError) as exc_info:
            validate_caddy_config("invalid config")

        assert "Caddy validation failed" in str(exc_info.value)

    def test_validate_caddy_config_calls_caddy_correctly(self, mocker):
        """Should call caddy validate with temp file."""
        from ots_containers.commands.proxy._helpers import validate_caddy_config

        mock_result = mocker.Mock()
        mock_result.returncode = 0
        mock_run = mocker.patch("subprocess.run", return_value=mock_result)

        validate_caddy_config("test content")

        mock_run.assert_called_once()
        call_args = mock_run.call_args[0][0]
        assert call_args[0] == "caddy"
        assert call_args[1] == "validate"
        assert call_args[2] == "--config"
        # Fourth arg is temp file path

    def test_validate_caddy_config_passes_caddyfile_adapter(self, mocker):
        """Regression: must pass --adapter caddyfile to avoid JSON parse errors.

        Without this flag, caddy tries to parse Caddyfile syntax as JSON and
        fails on '#' comments with 'invalid character looking for beginning of
        value'. See the fix in _helpers.py line 72.
        """
        from ots_containers.commands.proxy._helpers import validate_caddy_config

        mock_result = mocker.Mock()
        mock_result.returncode = 0
        mock_run = mocker.patch("subprocess.run", return_value=mock_result)

        validate_caddy_config(
            "# A comment that would break JSON parsing\nlocalhost:8080 {\n  respond 200\n}"
        )

        mock_run.assert_called_once()
        call_args = mock_run.call_args[0][0]
        assert "--adapter" in call_args, "Missing --adapter flag in caddy validate command"
        adapter_index = call_args.index("--adapter")
        assert call_args[adapter_index + 1] == "caddyfile", (
            f"Expected 'caddyfile' after --adapter, got '{call_args[adapter_index + 1]}'"
        )

    def test_validate_caddy_config_caddy_not_found_raises(self, mocker):
        """Should raise ProxyError when caddy not installed."""
        from ots_containers.commands.proxy._helpers import ProxyError, validate_caddy_config

        mocker.patch(
            "subprocess.run",
            side_effect=FileNotFoundError("caddy not found"),
        )

        with pytest.raises(ProxyError) as exc_info:
            validate_caddy_config("test content")

        assert "caddy not found" in str(exc_info.value)


class TestReloadCaddy:
    """Test reload_caddy function."""

    def test_reload_caddy_calls_systemctl(self, mocker):
        """Should call systemctl reload caddy."""
        from ots_containers.commands.proxy._helpers import reload_caddy

        mock_run = mocker.patch("subprocess.run")

        reload_caddy()

        mock_run.assert_called_once_with(
            ["sudo", "systemctl", "reload", "caddy"],
            check=True,
        )

    def test_reload_caddy_failure_raises(self, mocker):
        """Should raise ProxyError when reload fails."""
        from ots_containers.commands.proxy._helpers import ProxyError, reload_caddy

        mocker.patch(
            "subprocess.run",
            side_effect=subprocess.CalledProcessError(1, "cmd"),
        )

        with pytest.raises(ProxyError) as exc_info:
            reload_caddy()

        assert "Failed to reload" in str(exc_info.value)
