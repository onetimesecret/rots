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


class TestRemoteRenderTemplate:
    """Test render_template with a remote executor."""

    def _make_executor(self, mocker, responses):
        """Create a mock executor that returns successive Results."""
        from ots_shared.ssh.executor import Result

        mock_ex = mocker.MagicMock()
        mock_ex.run.side_effect = [
            Result(command=r[0], returncode=r[1], stdout=r[2], stderr=r[3]) for r in responses
        ]
        return mock_ex

    def test_render_template_remote(self, mocker):
        """Should read template and pipe through envsubst on remote."""
        from pathlib import Path

        from ots_containers.commands.proxy._helpers import render_template

        ex = self._make_executor(
            mocker,
            [
                ("test -f /tpl", 0, "", ""),  # template exists
                ("cat /tpl", 0, "Hello $NAME", ""),  # read template
                ("envsubst", 0, "Hello World", ""),  # envsubst result
            ],
        )

        result = render_template(Path("/tpl"), executor=ex)
        assert result == "Hello World"
        assert ex.run.call_count == 3
        # envsubst call (3rd) should have timeout=30
        envsubst_call = ex.run.call_args_list[2]
        assert envsubst_call[1]["timeout"] == 30

    def test_render_template_remote_missing(self, mocker):
        """Should raise ProxyError when remote template not found."""
        from pathlib import Path

        from ots_containers.commands.proxy._helpers import ProxyError, render_template

        ex = self._make_executor(
            mocker,
            [("test -f /missing", 1, "", "")],  # template missing
        )

        with pytest.raises(ProxyError, match="Template not found"):
            render_template(Path("/missing"), executor=ex)


class TestRemoteValidateCaddyConfig:
    """Test validate_caddy_config with a remote executor."""

    def _make_executor(self, mocker, responses):
        from ots_shared.ssh.executor import Result

        mock_ex = mocker.MagicMock()
        mock_ex.run.side_effect = [
            Result(command=r[0], returncode=r[1], stdout=r[2], stderr=r[3]) for r in responses
        ]
        return mock_ex

    def test_validate_remote_success(self, mocker):
        """Should validate on remote host using mktemp for unique temp file."""
        from ots_containers.commands.proxy._helpers import validate_caddy_config

        ex = self._make_executor(
            mocker,
            [
                ("mktemp ...", 0, "/tmp/ots-caddy-validate.abc123\n", ""),  # mktemp
                ("tee /tmp/...", 0, "", ""),  # write temp file
                ("caddy validate ...", 0, "", ""),  # validate
                ("rm -f /tmp/...", 0, "", ""),  # cleanup
            ],
        )

        # Should not raise
        validate_caddy_config("localhost { }", executor=ex)
        assert ex.run.call_count == 4
        # Verify timeout kwargs on remote calls
        mktemp_call = ex.run.call_args_list[0]
        assert mktemp_call[1].get("timeout") == 10, "mktemp should have timeout=10"
        validate_call = ex.run.call_args_list[2]
        assert validate_call[1].get("timeout") == 30, "caddy validate should have timeout=30"
        rm_call = ex.run.call_args_list[3]
        assert rm_call[1].get("timeout") == 10, "rm cleanup should have timeout=10"

    def test_validate_remote_failure(self, mocker):
        """Should raise ProxyError on remote validation failure."""
        from ots_containers.commands.proxy._helpers import ProxyError, validate_caddy_config

        ex = self._make_executor(
            mocker,
            [
                ("mktemp ...", 0, "/tmp/ots-caddy-validate.xyz789\n", ""),  # mktemp
                ("tee /tmp/...", 0, "", ""),  # write temp file
                ("caddy validate ...", 1, "", "syntax error"),  # validate fails
                ("rm -f /tmp/...", 0, "", ""),  # cleanup still runs
            ],
        )

        with pytest.raises(ProxyError, match="Caddy validation failed"):
            validate_caddy_config("invalid config", executor=ex)

        # Cleanup rm should still run even on failure, with timeout=10
        rm_call = ex.run.call_args_list[3]
        assert rm_call[1].get("timeout") == 10, (
            "rm cleanup should run with timeout=10 even on failure"
        )

    def test_validate_remote_mktemp_failure(self, mocker):
        """Should raise ProxyError when mktemp fails on remote."""
        from ots_containers.commands.proxy._helpers import ProxyError, validate_caddy_config

        ex = self._make_executor(
            mocker,
            [
                ("mktemp ...", 1, "", "Permission denied"),  # mktemp fails
            ],
        )

        with pytest.raises(ProxyError, match="Failed to create temp file"):
            validate_caddy_config("test content", executor=ex)


class TestRemoteReloadCaddy:
    """Test reload_caddy with a remote executor."""

    def test_reload_remote(self, mocker):
        """Should reload caddy on remote via executor."""
        from ots_shared.ssh.executor import Result

        from ots_containers.commands.proxy._helpers import reload_caddy

        mock_ex = mocker.MagicMock()
        mock_ex.run.return_value = Result(
            command="sudo -- systemctl reload caddy",
            returncode=0,
            stdout="",
            stderr="",
        )

        reload_caddy(executor=mock_ex)

        mock_ex.run.assert_called_once_with(
            ["systemctl", "reload", "caddy"],
            sudo=True,
            timeout=30,
            check=True,
        )
