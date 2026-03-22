# tests/commands/proxy/test_app.py
"""Tests for proxy app commands."""

import json
import logging
from pathlib import Path
from unittest.mock import ANY

import pytest


class TestRenderCommand:
    """Test render command."""

    def test_render_dry_run_prints_output(self, tmp_path, mocker, capsys):
        """Should print rendered content in dry-run mode."""
        from rots.commands.proxy.app import render

        template = tmp_path / "test.template"
        template.write_text("Hello $WORLD")

        # Patch where it's used, not where it's defined
        mocker.patch(
            "rots.commands.proxy.app.render_template",
            return_value="Hello Rendered",
        )

        render(template=template, output=tmp_path / "out", dry_run=True)

        captured = capsys.readouterr()
        assert "Hello Rendered" in captured.out

    def test_render_writes_to_output(self, tmp_path, mocker, caplog):
        """Should write rendered content to output file."""
        from rots.commands.proxy.app import render

        template = tmp_path / "test.template"
        template.write_text("Hello $WORLD")
        output = tmp_path / "output.conf"

        mocker.patch(
            "rots.commands.proxy.app.render_template",
            return_value="Hello Rendered",
        )
        mocker.patch("rots.commands.proxy.app.validate_caddy_config")

        with caplog.at_level(logging.INFO, logger="rots.commands.proxy.app"):
            render(template=template, output=output, dry_run=False)

        assert output.exists()
        assert output.read_text() == "Hello Rendered"
        assert "[ok]" in caplog.text

    def test_render_validates_before_writing(self, tmp_path, mocker):
        """Should call validate_caddy_config before writing."""
        from rots.commands.proxy.app import render

        template = tmp_path / "test.template"
        template.write_text("Hello")
        output = tmp_path / "output.conf"

        mocker.patch(
            "rots.commands.proxy.app.render_template",
            return_value="rendered content",
        )
        mock_validate = mocker.patch("rots.commands.proxy.app.validate_caddy_config")

        render(template=template, output=output, dry_run=False)

        mock_validate.assert_called_once_with("rendered content", executor=ANY, source_dir=tmp_path)

    def test_render_error_exits(self, tmp_path, mocker):
        """Should exit with error message on ProxyError."""
        from rots.commands.proxy._helpers import ProxyError
        from rots.commands.proxy.app import render

        template = tmp_path / "test.template"
        template.write_text("Hello")

        mocker.patch(
            "rots.commands.proxy.app.render_template",
            side_effect=ProxyError("test error"),
        )

        with pytest.raises(SystemExit) as exc_info:
            render(template=template, output=tmp_path / "out", dry_run=False)

        assert exc_info.value.code is not None
        assert "test error" in str(exc_info.value)

    def test_render_uses_config_defaults(self, mocker):
        """Should use Config paths when no args provided."""
        from pathlib import Path

        from rots.commands.proxy.app import render
        from rots.config import Config

        mock_render = mocker.patch(
            "rots.commands.proxy.app.render_template",
            return_value="content",
        )
        mocker.patch("rots.commands.proxy.app.validate_caddy_config")
        # Mock the file write
        mocker.patch.object(Path, "write_text")
        mocker.patch.object(Path, "mkdir")

        cfg = Config()

        render(template=None, output=None, dry_run=False)

        mock_render.assert_called_once_with(cfg.proxy_template, executor=ANY)


class TestPushCommand:
    """Test push command."""

    def _mock_remote_executor(self, mocker):
        """Create a mock remote (SSH) executor that passes is_remote() checks."""
        mock_ex = mocker.MagicMock()
        # Make it NOT an instance of LocalExecutor so is_remote() returns True
        mock_ex.__class__ = type("SSHExecutor", (), {})
        mock_result = mocker.MagicMock()
        mock_result.ok = True
        mock_result.stdout = ""
        mock_result.stderr = ""
        mock_ex.run.return_value = mock_result
        return mock_ex

    def test_push_requires_remote_host(self, tmp_path, mocker):
        """push should exit when running locally (no --host)."""
        from rots.commands.proxy.app import push

        template = tmp_path / "Caddyfile.template"
        template.write_text("localhost { }")

        with pytest.raises(SystemExit) as exc_info:
            push(source=template)

        assert "remote host" in str(exc_info.value)

    def test_push_missing_template_exits(self, tmp_path, mocker):
        """push should exit when template file doesn't exist."""
        from rots.commands.proxy.app import push

        missing = tmp_path / "nonexistent.template"

        # Need remote executor to pass the remote check
        mock_ex = self._mock_remote_executor(mocker)
        mock_cfg = mocker.MagicMock()
        mock_cfg.get_executor.return_value = mock_ex
        mocker.patch("rots.commands.proxy.app.Config", return_value=mock_cfg)

        with pytest.raises(SystemExit) as exc_info:
            push(source=missing)

        assert "not found" in str(exc_info.value)

    def test_push_dry_run_prints_actions(self, tmp_path, mocker, caplog):
        """push --dry-run should print expected actions without side effects."""
        from rots.commands.proxy.app import push

        template = tmp_path / "Caddyfile.template"
        template.write_text("localhost { }")

        mock_ex = self._mock_remote_executor(mocker)
        mock_cfg = mocker.MagicMock()
        mock_cfg.get_executor.return_value = mock_ex
        mock_cfg.proxy_template.return_value = "/etc/onetimesecret/Caddyfile.template"
        mock_cfg.proxy_config.return_value = "/etc/caddy/Caddyfile"
        mocker.patch("rots.commands.proxy.app.Config", return_value=mock_cfg)

        with caplog.at_level(logging.INFO, logger="rots.commands.proxy.app"):
            push(source=template, dry_run=True)

        assert "Would push" in caplog.text
        assert "Would render" in caplog.text
        assert "Would reload" in caplog.text
        # Executor should NOT have been called for actual operations
        mock_ex.run.assert_not_called()

    def test_push_happy_path(self, tmp_path, mocker, caplog):
        """push should push template, render, validate, and reload."""
        from rots.commands.proxy.app import push

        template = tmp_path / "Caddyfile.template"
        template.write_text("localhost { respond 200 }")

        mock_ex = self._mock_remote_executor(mocker)
        mock_cfg = mocker.MagicMock()
        mock_cfg.get_executor.return_value = mock_ex
        mock_cfg.proxy_template = tmp_path / "remote-template"
        mock_cfg.proxy_config = tmp_path / "remote-config"
        mocker.patch("rots.commands.proxy.app.Config", return_value=mock_cfg)

        mock_render = mocker.patch(
            "rots.commands.proxy.app.render_template",
            return_value="rendered content",
        )
        mock_validate = mocker.patch("rots.commands.proxy.app.validate_caddy_config")
        mock_reload = mocker.patch("rots.commands.proxy.app.reload_caddy")

        with caplog.at_level(logging.INFO, logger="rots.commands.proxy.app"):
            push(source=template)

        # Verify all three steps completed
        assert "[ok] Pushed" in caplog.text
        assert "[ok] Rendered" in caplog.text
        assert "[ok] Caddy reloaded" in caplog.text
        # Verify render, validate, reload were called
        mock_render.assert_called_once_with(mock_cfg.proxy_template, executor=mock_ex)
        mock_validate.assert_called_once_with(
            "rendered content", executor=mock_ex, source_dir=mock_cfg.proxy_template.parent
        )
        mock_reload.assert_called_once_with(executor=mock_ex)

    def test_push_write_failure_exits(self, tmp_path, mocker):
        """push should exit when remote write fails."""
        from rots.commands.proxy.app import push

        template = tmp_path / "Caddyfile.template"
        template.write_text("localhost { }")

        mock_ex = self._mock_remote_executor(mocker)
        # First run (mkdir) succeeds, second run (tee) fails
        mock_ok = mocker.MagicMock(ok=True, stdout="", stderr="")
        mock_fail = mocker.MagicMock(ok=False, stdout="", stderr="permission denied")
        mock_ex.run.side_effect = [mock_ok, mock_fail]

        mock_cfg = mocker.MagicMock()
        mock_cfg.get_executor.return_value = mock_ex
        mock_cfg.proxy_template = tmp_path / "remote-template"
        mock_cfg.proxy_config = tmp_path / "remote-config"
        mocker.patch("rots.commands.proxy.app.Config", return_value=mock_cfg)

        with pytest.raises(SystemExit) as exc_info:
            push(source=template)

        assert "Failed to write" in str(exc_info.value)


class TestPushDirectoryCommand:
    """Test push command with directory source."""

    def _mock_remote_executor(self, mocker):
        """Create a mock remote (SSH) executor that passes is_remote() checks."""
        mock_ex = mocker.MagicMock()
        # Make it NOT an instance of LocalExecutor so is_remote() returns True
        mock_ex.__class__ = type("SSHExecutor", (), {})
        mock_result = mocker.MagicMock()
        mock_result.ok = True
        mock_result.stdout = ""
        mock_result.stderr = ""
        mock_ex.run.return_value = mock_result
        return mock_ex

    @staticmethod
    def _make_source_dir(tmp_path):
        """Create a source directory with a template and snippet files."""
        source = tmp_path / "caddy"
        source.mkdir()
        (source / "Caddyfile.template").write_text("import snippets/global.caddy")
        snippets = source / "snippets"
        snippets.mkdir()
        (snippets / "global.caddy").write_text("(global) { log }")
        (snippets / "tls.caddy").write_text("(tls) { tls internal }")
        return source

    def test_push_directory_dry_run_lists_files(self, tmp_path, mocker, caplog):
        """push --dry-run with a directory should list all files."""
        from rots.commands.proxy.app import push

        source = self._make_source_dir(tmp_path)

        mock_ex = self._mock_remote_executor(mocker)
        mock_cfg = mocker.MagicMock()
        mock_cfg.get_executor.return_value = mock_ex
        mock_cfg.proxy_template = tmp_path / "remote" / "Caddyfile.template"
        mock_cfg.proxy_config = tmp_path / "remote" / "Caddyfile"
        mocker.patch("rots.commands.proxy.app.Config", return_value=mock_cfg)

        with caplog.at_level(logging.INFO, logger="rots.commands.proxy"):
            push(source=source, dry_run=True)

        assert "Caddyfile.template" in caplog.text
        assert "snippets/global.caddy" in caplog.text
        assert "snippets/tls.caddy" in caplog.text
        assert "Would push 3 file(s)" in caplog.text
        mock_ex.run.assert_not_called()
        mock_ex.put_file.assert_not_called()

    def test_push_directory_pushes_all_files(self, tmp_path, mocker, caplog):
        """push with a directory should call put_file for each file."""
        from rots.commands.proxy.app import push

        source = self._make_source_dir(tmp_path)

        mock_ex = self._mock_remote_executor(mocker)
        mock_cfg = mocker.MagicMock()
        mock_cfg.get_executor.return_value = mock_ex
        mock_cfg.proxy_template = tmp_path / "remote" / "Caddyfile.template"
        mock_cfg.proxy_config = tmp_path / "remote" / "Caddyfile"
        mocker.patch("rots.commands.proxy.app.Config", return_value=mock_cfg)
        mocker.patch("rots.commands.proxy.app.render_template", return_value="rendered")
        mocker.patch("rots.commands.proxy.app.validate_caddy_config")
        mocker.patch("rots.commands.proxy.app.reload_caddy")

        with caplog.at_level(logging.INFO, logger="rots.commands.proxy"):
            push(source=source)

        assert "[ok] Pushed 3 file(s)" in caplog.text
        # Verify all 3 source files appear in the output
        assert "Caddyfile.template" in caplog.text
        assert "snippets/global.caddy" in caplog.text
        assert "snippets/tls.caddy" in caplog.text

    def test_push_directory_auto_detects_template(self, tmp_path, mocker, capsys):
        """push with a directory should auto-detect *.template for render."""
        from rots.commands.proxy.app import push

        source = self._make_source_dir(tmp_path)

        mock_ex = self._mock_remote_executor(mocker)
        mock_cfg = mocker.MagicMock()
        mock_cfg.get_executor.return_value = mock_ex
        mock_cfg.proxy_template = tmp_path / "remote" / "Caddyfile.template"
        mock_cfg.proxy_config = tmp_path / "remote" / "Caddyfile"
        mocker.patch("rots.commands.proxy.app.Config", return_value=mock_cfg)
        mock_render = mocker.patch(
            "rots.commands.proxy.app.render_template", return_value="rendered"
        )
        mocker.patch("rots.commands.proxy.app.validate_caddy_config")
        mocker.patch("rots.commands.proxy.app.reload_caddy")

        push(source=source)

        # render_template called with the remote path for the auto-detected template
        remote_tpl = mock_cfg.proxy_template.parent / "Caddyfile.template"
        mock_render.assert_called_once_with(remote_tpl, executor=mock_ex)

    def test_push_directory_multiple_templates_requires_flag(self, tmp_path, mocker):
        """push should error when directory has multiple *.template files."""
        from rots.commands.proxy.app import push

        source = tmp_path / "caddy"
        source.mkdir()
        (source / "A.template").write_text("a")
        (source / "B.template").write_text("b")

        mock_ex = self._mock_remote_executor(mocker)
        mock_cfg = mocker.MagicMock()
        mock_cfg.get_executor.return_value = mock_ex
        mock_cfg.proxy_template = tmp_path / "remote" / "Caddyfile.template"
        mock_cfg.proxy_config = tmp_path / "remote" / "Caddyfile"
        mocker.patch("rots.commands.proxy.app.Config", return_value=mock_cfg)

        with pytest.raises(SystemExit) as exc_info:
            push(source=source)

        assert "--template" in str(exc_info.value)

    def test_push_directory_explicit_template_flag(self, tmp_path, mocker, capsys):
        """push --template should select the specified file for render."""
        from rots.commands.proxy.app import push

        source = tmp_path / "caddy"
        source.mkdir()
        (source / "A.template").write_text("a")
        (source / "B.template").write_text("b")

        mock_ex = self._mock_remote_executor(mocker)
        mock_cfg = mocker.MagicMock()
        mock_cfg.get_executor.return_value = mock_ex
        mock_cfg.proxy_template = tmp_path / "remote" / "Caddyfile.template"
        mock_cfg.proxy_config = tmp_path / "remote" / "Caddyfile"
        mocker.patch("rots.commands.proxy.app.Config", return_value=mock_cfg)
        mock_render = mocker.patch(
            "rots.commands.proxy.app.render_template", return_value="rendered"
        )
        mocker.patch("rots.commands.proxy.app.validate_caddy_config")
        mocker.patch("rots.commands.proxy.app.reload_caddy")

        push(source=source, template="B.template")

        remote_tpl = mock_cfg.proxy_template.parent / "B.template"
        mock_render.assert_called_once_with(remote_tpl, executor=mock_ex)

    def test_push_directory_no_render_skips_pipeline(self, tmp_path, mocker, caplog):
        """push --no-render should skip render/validate/reload."""
        from rots.commands.proxy.app import push

        source = self._make_source_dir(tmp_path)

        mock_ex = self._mock_remote_executor(mocker)
        mock_cfg = mocker.MagicMock()
        mock_cfg.get_executor.return_value = mock_ex
        mock_cfg.proxy_template = tmp_path / "remote" / "Caddyfile.template"
        mock_cfg.proxy_config = tmp_path / "remote" / "Caddyfile"
        mocker.patch("rots.commands.proxy.app.Config", return_value=mock_cfg)
        mock_render = mocker.patch("rots.commands.proxy.app.render_template")
        mock_validate = mocker.patch("rots.commands.proxy.app.validate_caddy_config")
        mock_reload = mocker.patch("rots.commands.proxy.app.reload_caddy")

        with caplog.at_level(logging.INFO, logger="rots.commands.proxy"):
            push(source=source, no_render=True)

        assert "[ok] Pushed 3 file(s)" in caplog.text
        mock_render.assert_not_called()
        mock_validate.assert_not_called()
        mock_reload.assert_not_called()

    def test_push_directory_custom_remote_dir(self, tmp_path, mocker, caplog):
        """push --remote-dir should override destination."""
        from rots.commands.proxy.app import push

        source = self._make_source_dir(tmp_path)
        custom_dest = Path("/opt/caddy/config")

        mock_ex = self._mock_remote_executor(mocker)
        mock_cfg = mocker.MagicMock()
        mock_cfg.get_executor.return_value = mock_ex
        mock_cfg.proxy_template = tmp_path / "remote" / "Caddyfile.template"
        mock_cfg.proxy_config = tmp_path / "remote" / "Caddyfile"
        mocker.patch("rots.commands.proxy.app.Config", return_value=mock_cfg)
        mocker.patch("rots.commands.proxy.app.render_template", return_value="rendered")
        mocker.patch("rots.commands.proxy.app.validate_caddy_config")
        mocker.patch("rots.commands.proxy.app.reload_caddy")

        with caplog.at_level(logging.INFO, logger="rots.commands.proxy"):
            push(source=source, remote_dir=custom_dest)

        # Verify files were pushed to the custom destination
        assert str(custom_dest) in caplog.text


class TestDiffCommand:
    """Test diff command."""

    def test_diff_equivalent_configs(self, tmp_path, mocker, caplog):
        """Should print [ok] when configs produce identical JSON."""
        from rots.commands.proxy.app import diff

        old = tmp_path / "old.conf"
        new = tmp_path / "new.conf"
        old.write_text("old")
        new.write_text("new")

        mocker.patch(
            "rots.commands.proxy.app.adapt_to_json",
            return_value='{"same": true}\n',
        )

        with caplog.at_level(logging.INFO, logger="rots.commands.proxy.app"):
            diff(old=old, new=new)

        assert "[ok] Configs are equivalent" in caplog.text

    def test_diff_different_configs_exits_1(self, tmp_path, mocker, capsys):
        """Should print unified diff and exit 1 when configs differ."""
        from rots.commands.proxy.app import diff

        old = tmp_path / "old.conf"
        new = tmp_path / "new.conf"
        old.write_text("old")
        new.write_text("new")

        def mock_adapt(path, **kwargs):
            if path == old:
                return '{\n  "key": "old_value"\n}\n'
            return '{\n  "key": "new_value"\n}\n'

        mocker.patch("rots.commands.proxy.app.adapt_to_json", side_effect=mock_adapt)

        with pytest.raises(SystemExit) as exc_info:
            diff(old=old, new=new)

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "---" in captured.out
        assert "+++" in captured.out
        assert '-  "key": "old_value"' in captured.out
        assert '+  "key": "new_value"' in captured.out

    def test_diff_adapt_error_exits(self, tmp_path, mocker):
        """Should exit with error message when caddy adapt fails."""
        from rots.commands.proxy._helpers import ProxyError
        from rots.commands.proxy.app import diff

        old = tmp_path / "old.conf"
        new = tmp_path / "new.conf"
        old.write_text("old")
        new.write_text("new")

        mocker.patch(
            "rots.commands.proxy.app.adapt_to_json",
            side_effect=ProxyError("caddy adapt failed for old.conf"),
        )

        with pytest.raises(SystemExit) as exc_info:
            diff(old=old, new=new)

        assert "caddy adapt failed" in str(exc_info.value)

    def test_diff_shows_file_names_in_output(self, tmp_path, mocker, capsys):
        """Should use file paths as labels in the diff header."""
        from rots.commands.proxy.app import diff

        old = tmp_path / "monolith.conf"
        new = tmp_path / "snippets.conf"
        old.write_text("old")
        new.write_text("new")

        def mock_adapt(path, **kwargs):
            if path == old:
                return '{"a": 1}\n'
            return '{"a": 2}\n'

        mocker.patch("rots.commands.proxy.app.adapt_to_json", side_effect=mock_adapt)

        with pytest.raises(SystemExit):
            diff(old=old, new=new)

        captured = capsys.readouterr()
        assert str(old) in captured.out
        assert str(new) in captured.out


class TestReloadCommand:
    """Test reload command."""

    def test_reload_calls_helper(self, mocker, caplog):
        """Should call reload_caddy helper with executor."""
        from rots.commands.proxy.app import reload

        mock_reload = mocker.patch("rots.commands.proxy.app.reload_caddy")

        with caplog.at_level(logging.INFO, logger="rots.commands.proxy.app"):
            reload()

        mock_reload.assert_called_once_with(executor=ANY)
        assert "[ok]" in caplog.text

    def test_reload_error_exits(self, mocker):
        """Should exit with error message on ProxyError."""
        from rots.commands.proxy._helpers import ProxyError
        from rots.commands.proxy.app import reload

        mocker.patch(
            "rots.commands.proxy.app.reload_caddy",
            side_effect=ProxyError("reload failed"),
        )

        with pytest.raises(SystemExit) as exc_info:
            reload()

        assert exc_info.value.code is not None
        assert "reload failed" in str(exc_info.value)


class TestStatusCommand:
    """Test status command — now uses executor.run() instead of subprocess.run()."""

    def test_status_prints_output(self, mocker, capsys):
        """Should print systemctl output via executor."""
        from rots.commands.proxy.app import status

        # Mock subprocess.run which LocalExecutor uses internally
        mocker.patch(
            "subprocess.run",
            return_value=mocker.MagicMock(
                returncode=0,
                stdout="active (running)",
                stderr="",
            ),
        )

        status()

        captured = capsys.readouterr()
        assert "active (running)" in captured.out

    def test_status_shows_stderr_if_present(self, mocker, capsys):
        """Should show stderr output if present."""
        from rots.commands.proxy.app import status

        mocker.patch(
            "subprocess.run",
            return_value=mocker.MagicMock(
                returncode=0,
                stdout="output",
                stderr="warning message",
            ),
        )

        status()

        captured = capsys.readouterr()
        assert "output" in captured.out
        assert "warning message" in captured.out

    def test_status_error_exits(self, mocker):
        """Should exit on exception."""
        from rots.commands.proxy.app import status

        mocker.patch("subprocess.run", side_effect=Exception("systemctl failed"))

        with pytest.raises(SystemExit) as exc_info:
            status()

        assert exc_info.value.code is not None


class TestValidateCommand:
    """Test validate command — now reads file then calls validate_caddy_config helper."""

    def test_validate_existing_file(self, tmp_path, mocker, caplog):
        """Should validate an existing config file."""
        from rots.commands.proxy.app import validate

        config = tmp_path / "Caddyfile"
        config.write_text("localhost { }")

        mock_validate = mocker.patch("rots.commands.proxy.app.validate_caddy_config")

        with caplog.at_level(logging.INFO, logger="rots.commands.proxy.app"):
            validate(config_file=config)

        mock_validate.assert_called_once_with("localhost { }", executor=ANY, source_dir=tmp_path)
        assert "[ok]" in caplog.text

    def test_validate_uses_default_path(self, tmp_path, mocker):
        """Should use default config path when none provided."""
        from rots.commands.proxy.app import validate

        # Create a mock config with a real path that exists
        default_config = tmp_path / "Caddyfile"
        default_config.write_text("localhost { }")

        mock_cfg = mocker.MagicMock()
        mock_cfg.proxy_config = default_config
        # get_executor returns a LocalExecutor
        from ots_shared.ssh import LocalExecutor

        mock_cfg.get_executor.return_value = LocalExecutor()
        mocker.patch(
            "rots.commands.proxy.app.Config",
            return_value=mock_cfg,
        )

        mock_validate = mocker.patch("rots.commands.proxy.app.validate_caddy_config")

        validate(config_file=None)

        mock_validate.assert_called_once_with("localhost { }", executor=ANY, source_dir=tmp_path)

    def test_validate_success_with_output(self, tmp_path, mocker, caplog):
        """Should print [ok] when validation succeeds."""
        from rots.commands.proxy.app import validate

        config = tmp_path / "Caddyfile"
        config.write_text("localhost { }")

        mocker.patch("rots.commands.proxy.app.validate_caddy_config")

        with caplog.at_level(logging.INFO, logger="rots.commands.proxy.app"):
            validate(config_file=config)

        assert "[ok]" in caplog.text

    def test_validate_missing_file_exits(self, tmp_path):
        """Should exit if config file doesn't exist."""
        from rots.commands.proxy.app import validate

        missing = tmp_path / "nonexistent"

        with pytest.raises(SystemExit) as exc_info:
            validate(config_file=missing)

        assert exc_info.value.code is not None
        assert "not found" in str(exc_info.value)

    def test_validate_failure_exits(self, tmp_path, mocker):
        """Should exit on validation failure."""
        from rots.commands.proxy._helpers import ProxyError
        from rots.commands.proxy.app import validate

        config = tmp_path / "Caddyfile"
        config.write_text("invalid config")

        mocker.patch(
            "rots.commands.proxy.app.validate_caddy_config",
            side_effect=ProxyError("Caddy validation failed:\nsyntax error"),
        )

        with pytest.raises(SystemExit) as exc_info:
            validate(config_file=config)

        assert "Caddy validation failed" in str(exc_info.value)

    def test_validate_caddy_not_found_exits(self, tmp_path, mocker):
        """Should exit if caddy not in PATH."""
        from rots.commands.proxy.app import validate

        config = tmp_path / "Caddyfile"
        config.write_text("localhost { }")

        mocker.patch(
            "rots.commands.proxy.app.validate_caddy_config",
            side_effect=FileNotFoundError(),
        )

        with pytest.raises(SystemExit) as exc_info:
            validate(config_file=config)

        assert "caddy not found" in str(exc_info.value)


class TestTraceCommand:
    """Test trace command."""

    def _mock_remote_executor(self, mocker):
        """Create a mock remote executor for rejection tests."""
        mock_ex = mocker.MagicMock()
        mock_ex.__class__ = type("SSHExecutor", (), {})
        return mock_ex

    def test_rejects_remote_host(self, tmp_path, mocker):
        """trace should exit when --host is used."""
        from rots.commands.proxy.app import trace

        config = tmp_path / "Caddyfile"
        config.write_text("localhost { }")

        mock_ex = self._mock_remote_executor(mocker)
        mock_cfg = mocker.MagicMock()
        mock_cfg.get_executor.return_value = mock_ex
        mocker.patch("rots.commands.proxy.app.Config", return_value=mock_cfg)

        with pytest.raises(SystemExit) as exc_info:
            trace(config_file=config, url="localhost/test")

        assert "local-only" in str(exc_info.value)

    def test_exits_on_missing_config(self, tmp_path, mocker):
        """trace should exit when config file doesn't exist."""
        from rots.commands.proxy.app import trace

        missing = tmp_path / "nonexistent.conf"

        with pytest.raises(SystemExit) as exc_info:
            trace(config_file=missing, url="localhost/test")

        assert "not found" in str(exc_info.value)

    def test_parses_host_and_path(self, tmp_path, mocker, capsys):
        """trace should split url into Host header and request path."""
        from rots.commands.proxy.app import trace

        config = tmp_path / "Caddyfile"
        config.write_text("localhost { }")

        adapted_json = json.dumps(
            {
                "apps": {
                    "http": {
                        "servers": {
                            "srv0": {
                                "listen": [":443"],
                                "routes": [
                                    {
                                        "handle": [
                                            {
                                                "handler": "reverse_proxy",
                                                "upstreams": [{"dial": "app:3000"}],
                                            }
                                        ]
                                    }
                                ],
                            }
                        }
                    }
                }
            }
        )

        mocker.patch(
            "rots.commands.proxy.app.adapt_to_json",
            return_value=adapted_json,
        )

        captured_req = {}

        # The echo server list starts empty; urlopen's side-effect populates
        # it (simulating Caddy forwarding the request to the echo backend).
        echo_entry = {
            "method": "GET",
            "path": "/api/v2/status",
            "headers": {"Host": "us.onetime.co"},
        }
        echo_body = json.dumps(echo_entry).encode()
        shared_received: list[dict] = []

        def mock_run_echo(port):
            import contextlib

            @contextlib.contextmanager
            def _ctx():
                yield f"127.0.0.1:{port}", shared_received

            return _ctx()

        def mock_run_caddy(cfg, port):
            import contextlib

            captured_req["config"] = cfg
            captured_req["port"] = port

            @contextlib.contextmanager
            def _ctx():
                mock_proc = mocker.MagicMock()
                mock_proc.pid = 12345
                yield mock_proc

            return _ctx()

        # Mock urllib — the side-effect appends to shared_received to
        # simulate the echo server capturing the forwarded request.
        mock_resp = mocker.MagicMock()
        mock_resp.status = 200
        mock_resp.headers = {"X-Custom": "value"}
        mock_resp.read.return_value = echo_body
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = mocker.MagicMock(return_value=False)

        def fake_urlopen(req, **_kwargs):
            shared_received.append(echo_entry)
            return mock_resp

        mocker.patch("urllib.request.urlopen", side_effect=fake_urlopen)

        mocker.patch("rots.commands.proxy.app.run_echo_server", side_effect=mock_run_echo)
        mocker.patch("rots.commands.proxy.app.run_caddy", side_effect=mock_run_caddy)
        mocker.patch("rots.commands.proxy.app.find_free_port", side_effect=[9000, 9001])

        trace(config_file=config, url="us.onetime.co/api/v2/status")

        captured = capsys.readouterr()
        assert "caddy pid=12345 on 127.0.0.1:9000" in captured.out
        assert "us.onetime.co/api/v2/status" in captured.out
        assert "forwarded request:" in captured.out
        assert "response: 200" in captured.out
        # upstream (request) prints before response
        assert captured.out.index("forwarded request:") < captured.out.index("response:")
        # echo JSON body is only shown with --verbose (DEBUG logging)
        assert "echo:" not in captured.out

    def test_handles_blocked_request(self, tmp_path, mocker, capsys):
        """trace should show blocked message when echo server gets no request."""
        import io

        from rots.commands.proxy.app import trace

        config = tmp_path / "Caddyfile"
        config.write_text("localhost { }")

        adapted_json = json.dumps(
            {
                "apps": {
                    "http": {
                        "servers": {
                            "srv0": {
                                "listen": [":443"],
                                "routes": [],
                            }
                        }
                    }
                }
            }
        )

        mocker.patch(
            "rots.commands.proxy.app.adapt_to_json",
            return_value=adapted_json,
        )

        def mock_run_echo(port):
            import contextlib

            @contextlib.contextmanager
            def _ctx():
                yield f"127.0.0.1:{port}", []  # empty received = blocked

            return _ctx()

        def mock_run_caddy(cfg, port):
            import contextlib

            @contextlib.contextmanager
            def _ctx():
                mock_proc = mocker.MagicMock()
                mock_proc.pid = 99999
                yield mock_proc

            return _ctx()

        import urllib.error

        mocker.patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.HTTPError(
                url="http://localhost",
                code=404,
                msg="Not Found",
                hdrs={},  # type: ignore[arg-type]
                fp=io.BytesIO(b""),
            ),
        )

        mocker.patch("rots.commands.proxy.app.run_echo_server", side_effect=mock_run_echo)
        mocker.patch("rots.commands.proxy.app.run_caddy", side_effect=mock_run_caddy)
        mocker.patch("rots.commands.proxy.app.find_free_port", side_effect=[9000, 9001])

        trace(config_file=config, url="example.com/.env")

        captured = capsys.readouterr()
        assert "blocked: 404" in captured.out

    def test_render_flag_runs_envsubst(self, tmp_path, mocker, capsys):
        """--render should pipe through render_template before adapting."""
        from rots.commands.proxy.app import trace

        config = tmp_path / "Caddyfile.template"
        config.write_text("$HOST { }")

        adapted_json = json.dumps(
            {
                "apps": {
                    "http": {
                        "servers": {
                            "srv0": {
                                "listen": [":443"],
                                "routes": [
                                    {
                                        "handle": [
                                            {
                                                "handler": "reverse_proxy",
                                                "upstreams": [{"dial": "app:3000"}],
                                            }
                                        ]
                                    }
                                ],
                            }
                        }
                    }
                }
            }
        )

        mock_render = mocker.patch(
            "rots.commands.proxy.app.render_template",
            return_value="localhost { }",
        )
        mock_adapt = mocker.patch(
            "rots.commands.proxy.app.adapt_to_json",
            return_value=adapted_json,
        )

        echo_entry = {"method": "GET", "path": "/", "headers": {"Host": "localhost"}}
        shared_received: list[dict] = []

        def mock_run_echo(port):
            import contextlib

            @contextlib.contextmanager
            def _ctx():
                yield f"127.0.0.1:{port}", shared_received

            return _ctx()

        def mock_run_caddy(cfg, port):
            import contextlib

            @contextlib.contextmanager
            def _ctx():
                mock_proc = mocker.MagicMock()
                mock_proc.pid = 11111
                yield mock_proc

            return _ctx()

        mock_resp = mocker.MagicMock()
        mock_resp.status = 200
        mock_resp.headers = {}
        mock_resp.read.return_value = json.dumps(echo_entry).encode()

        def fake_urlopen(req, **_kw):
            shared_received.append(echo_entry)
            return mock_resp

        mocker.patch("urllib.request.urlopen", side_effect=fake_urlopen)
        mocker.patch("rots.commands.proxy.app.run_echo_server", side_effect=mock_run_echo)
        mocker.patch("rots.commands.proxy.app.run_caddy", side_effect=mock_run_caddy)
        mocker.patch("rots.commands.proxy.app.find_free_port", side_effect=[9000, 9001])

        trace(config_file=config, url="localhost/test", render=True)

        mock_render.assert_called_once_with(config, executor=ANY)
        # adapt_to_json receives the temp file in the same directory
        adapt_call_path = mock_adapt.call_args[0][0]
        assert adapt_call_path.parent == tmp_path

        captured = capsys.readouterr()
        assert "response: 200" in captured.out

    def test_exits_on_proxy_error(self, tmp_path, mocker):
        """trace should exit cleanly on ProxyError."""
        from rots.commands.proxy._helpers import ProxyError
        from rots.commands.proxy.app import trace

        config = tmp_path / "Caddyfile"
        config.write_text("localhost { }")

        mocker.patch(
            "rots.commands.proxy.app.adapt_to_json",
            side_effect=ProxyError("caddy adapt failed"),
        )

        with pytest.raises(SystemExit) as exc_info:
            trace(config_file=config, url="localhost/test")

        assert "caddy adapt failed" in str(exc_info.value)

    def test_live_skips_echo_server(self, tmp_path, mocker, capsys):
        """--live should not start an echo server and should show body instead of upstream."""
        from rots.commands.proxy.app import trace

        config = tmp_path / "Caddyfile"
        config.write_text("localhost { }")

        adapted_json = json.dumps(
            {
                "apps": {
                    "http": {
                        "servers": {
                            "srv0": {
                                "listen": [":443"],
                                "routes": [
                                    {
                                        "handle": [
                                            {
                                                "handler": "reverse_proxy",
                                                "upstreams": [{"dial": "app:3000"}],
                                            }
                                        ]
                                    }
                                ],
                            }
                        }
                    }
                }
            }
        )

        mocker.patch(
            "rots.commands.proxy.app.adapt_to_json",
            return_value=adapted_json,
        )

        def mock_run_caddy(cfg, port):
            import contextlib

            @contextlib.contextmanager
            def _ctx():
                yield mocker.MagicMock(pid=99999)

            return _ctx()

        mock_resp = mocker.MagicMock()
        mock_resp.status = 200
        mock_resp.headers = {"X-Custom": "live-value"}
        mock_resp.read.return_value = b"OK"

        mocker.patch("urllib.request.urlopen", return_value=mock_resp)
        mocker.patch("rots.commands.proxy.app.run_caddy", side_effect=mock_run_caddy)
        mocker.patch("rots.commands.proxy.app.find_free_port", return_value=9000)

        # Echo server should NOT be started
        mock_echo = mocker.patch("rots.commands.proxy.app.run_echo_server")

        trace(config_file=config, url="localhost/health", live=True)

        mock_echo.assert_not_called()
        captured = capsys.readouterr()
        assert "-> live upstream" in captured.out
        assert "127.0.0.1:" not in captured.out
        assert "response: 200" in captured.out
        assert "body: OK" in captured.out
        assert "forwarded request:" not in captured.out


class TestProbeCommand:
    """Test probe command."""

    def _make_probe_result(self, **kwargs):
        """Build a ProbeResult with sensible defaults."""
        from rots.commands.proxy._helpers import ProbeResult

        defaults = {
            "url": "https://example.com/api/v2/status",
            "http_code": 200,
            "ssl_verify_result": 0,
            "ssl_verify_ok": True,
            "cert_issuer": "R11",
            "cert_subject": "CN=example.com",
            "cert_expiry": "Aug 17 23:59:59 2026 GMT",
            "http_version": "2",
            "time_namelookup": 0.005,
            "time_connect": 0.020,
            "time_appconnect": 0.080,
            "time_starttransfer": 0.150,
            "time_total": 0.180,
            "response_headers": {
                "X-Frame-Options": ["DENY"],
                "O-Via": ["B76s2"],
                "Strict-Transport-Security": ["max-age=63072000"],
            },
            "curl_json": {},
        }
        defaults.update(kwargs)
        return ProbeResult(**defaults)

    def test_human_output(self, mocker, capsys):
        """Should print human-readable probe output."""
        from rots.commands.proxy.app import probe

        mocker.patch(
            "rots.commands.proxy.app.run_probe",
            return_value=self._make_probe_result(),
        )

        probe(url="https://example.com/api/v2/status")

        captured = capsys.readouterr()
        assert "https://example.com/api/v2/status" in captured.out
        assert "[ok] verified" in captured.out
        assert "issuer:  R11" in captured.out
        assert "status: 200" in captured.out
        assert "dns:" in captured.out
        assert "X-Frame-Options: DENY" in captured.out

    def test_json_output(self, mocker, capsys):
        """Should print JSON probe output."""
        from rots.commands.proxy.app import probe

        mocker.patch(
            "rots.commands.proxy.app.run_probe",
            return_value=self._make_probe_result(),
        )

        probe(url="https://example.com/api/v2/status", json_output=True)

        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert output["http_code"] == 200
        assert output["tls"]["verified"] is True
        assert output["tls"]["issuer"] == "R11"
        assert "dns_ms" in output["timing"]
        assert output["headers"]["X-Frame-Options"] == ["DENY"]

    def test_assertion_pass_no_exit(self, mocker, capsys):
        """Should not raise SystemExit when assertions pass."""
        from rots.commands.proxy.app import probe

        mocker.patch(
            "rots.commands.proxy.app.run_probe",
            return_value=self._make_probe_result(),
        )

        # Should not raise
        probe(
            url="https://example.com/api/v2/status",
            expect_status=200,
            expect_header=("O-Via: B76s2",),
        )

        captured = capsys.readouterr()
        assert "[ok] status" in captured.out
        assert "[ok] header O-Via" in captured.out

    def test_assertion_fail_exits_1(self, mocker):
        """Should raise SystemExit(1) when assertions fail."""
        from rots.commands.proxy.app import probe

        mocker.patch(
            "rots.commands.proxy.app.run_probe",
            return_value=self._make_probe_result(http_code=404),
        )

        with pytest.raises(SystemExit) as exc_info:
            probe(url="https://example.com/api/v2/status", expect_status=200)

        assert exc_info.value.code == 1

    def test_proxy_error_exits(self, mocker):
        """Should exit with error message on ProxyError."""
        from rots.commands.proxy._helpers import ProxyError
        from rots.commands.proxy.app import probe

        mocker.patch(
            "rots.commands.proxy.app.run_probe",
            side_effect=ProxyError("curl not found in PATH"),
        )

        with pytest.raises(SystemExit) as exc_info:
            probe(url="https://example.com/api/v2/status")

        assert "curl not found" in str(exc_info.value)

    def test_resolve_passthrough(self, mocker):
        """Should pass --resolve to run_probe."""
        from rots.commands.proxy.app import probe

        mock_run = mocker.patch(
            "rots.commands.proxy.app.run_probe",
            return_value=self._make_probe_result(),
        )

        probe(url="https://example.com/", resolve="example.com:443:10.0.0.5")

        mock_run.assert_called_once()
        call_kwargs = mock_run.call_args
        assert call_kwargs[1]["resolve"] == "example.com:443:10.0.0.5"

    def test_expect_header_evaluation(self, mocker, capsys):
        """Should evaluate header assertions and show results."""
        from rots.commands.proxy.app import probe

        mocker.patch(
            "rots.commands.proxy.app.run_probe",
            return_value=self._make_probe_result(),
        )

        # X-Frame-Options: DENY should pass, X-Missing: value should fail
        with pytest.raises(SystemExit) as exc_info:
            probe(
                url="https://example.com/api/v2/status",
                expect_header=("X-Frame-Options: DENY", "X-Missing: value"),
            )

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "[ok] header X-Frame-Options" in captured.out
        assert "[FAIL] header X-Missing" in captured.out

    def test_method_passthrough(self, mocker):
        """Should pass --method to run_probe."""
        from rots.commands.proxy.app import probe

        mock_run = mocker.patch(
            "rots.commands.proxy.app.run_probe",
            return_value=self._make_probe_result(),
        )

        probe(url="https://example.com/", method="HEAD")

        mock_run.assert_called_once()
        assert mock_run.call_args[1]["method"] == "HEAD"

    def test_insecure_passthrough(self, mocker):
        """Should pass --insecure to run_probe."""
        from rots.commands.proxy.app import probe

        mock_run = mocker.patch(
            "rots.commands.proxy.app.run_probe",
            return_value=self._make_probe_result(),
        )

        probe(url="https://example.com/", insecure=True)

        mock_run.assert_called_once()
        assert mock_run.call_args[1]["insecure"] is True

    def test_follow_passthrough(self, mocker):
        """Should pass --follow to run_probe."""
        from rots.commands.proxy.app import probe

        mock_run = mocker.patch(
            "rots.commands.proxy.app.run_probe",
            return_value=self._make_probe_result(),
        )

        probe(url="https://example.com/", follow_redirects=True)

        mock_run.assert_called_once()
        assert mock_run.call_args[1]["follow_redirects"] is True

    def test_retry_succeeds_after_probe_failure(self, mocker, capsys):
        """Should succeed when run_probe fails first then succeeds on retry."""
        from rots.commands.proxy._helpers import ProxyError
        from rots.commands.proxy.app import probe

        mock_run = mocker.patch(
            "rots.commands.proxy.app.run_probe",
            side_effect=[
                ProxyError("connection refused"),
                self._make_probe_result(),
            ],
        )
        mocker.patch("rots.commands.proxy.app.time.sleep")

        # Should not raise
        probe(url="https://example.com/api/v2/status", retries=1)

        assert mock_run.call_count == 2
        captured = capsys.readouterr()
        assert "status: 200" in captured.out

    def test_retry_exhausted_exits(self, mocker):
        """Should raise SystemExit when all retry attempts fail."""
        from rots.commands.proxy._helpers import ProxyError
        from rots.commands.proxy.app import probe

        mocker.patch(
            "rots.commands.proxy.app.run_probe",
            side_effect=ProxyError("connection refused"),
        )
        mocker.patch("rots.commands.proxy.app.time.sleep")

        with pytest.raises(SystemExit) as exc_info:
            probe(url="https://example.com/api/v2/status", retries=2)

        assert "connection refused" in str(exc_info.value)

    def test_retry_assertion_failure_then_pass(self, mocker, capsys):
        """Should retry when assertions fail and succeed on next attempt."""
        from rots.commands.proxy.app import probe

        mock_run = mocker.patch(
            "rots.commands.proxy.app.run_probe",
            side_effect=[
                self._make_probe_result(http_code=503),
                self._make_probe_result(http_code=200),
            ],
        )
        mocker.patch("rots.commands.proxy.app.time.sleep")

        # Should not raise — second attempt returns 200
        probe(
            url="https://example.com/api/v2/status",
            expect_status=200,
            retries=1,
        )

        assert mock_run.call_count == 2
        captured = capsys.readouterr()
        assert "[ok] status" in captured.out

    def test_retry_delay_called(self, mocker):
        """Should sleep with the configured delay between retries."""
        from rots.commands.proxy._helpers import ProxyError
        from rots.commands.proxy.app import probe

        mocker.patch(
            "rots.commands.proxy.app.run_probe",
            side_effect=[
                ProxyError("timeout"),
                self._make_probe_result(),
            ],
        )
        mock_sleep = mocker.patch("rots.commands.proxy.app.time.sleep")

        probe(url="https://example.com/", retries=1, retry_delay=2.5)

        mock_sleep.assert_called_once_with(2.5)

    def test_no_retry_default(self, mocker, capsys):
        """With retries=0 (default), run_probe should be called exactly once."""
        from rots.commands.proxy.app import probe

        mock_run = mocker.patch(
            "rots.commands.proxy.app.run_probe",
            return_value=self._make_probe_result(),
        )

        probe(url="https://example.com/api/v2/status")

        mock_run.assert_called_once()

    def test_cert_days_passthrough(self, mocker, capsys):
        """Should pass expect_cert_days to evaluate_assertions."""
        from rots.commands.proxy.app import probe

        mocker.patch(
            "rots.commands.proxy.app.run_probe",
            return_value=self._make_probe_result(),
        )
        mock_eval = mocker.patch(
            "rots.commands.proxy.app.evaluate_assertions",
            return_value=[],
        )

        probe(url="https://example.com/api/v2/status", expect_cert_days=30)

        mock_eval.assert_called_once()
        assert mock_eval.call_args[1]["expect_cert_days"] == 30
