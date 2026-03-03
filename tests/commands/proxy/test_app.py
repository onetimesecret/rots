# tests/commands/proxy/test_app.py
"""Tests for proxy app commands."""

import json
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

    def test_render_writes_to_output(self, tmp_path, mocker, capsys):
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

        render(template=template, output=output, dry_run=False)

        assert output.exists()
        assert output.read_text() == "Hello Rendered"
        captured = capsys.readouterr()
        assert "[ok]" in captured.out

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

        mock_validate.assert_called_once_with("rendered content", executor=ANY)

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
            push(template_file=template)

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
            push(template_file=missing)

        assert "not found" in str(exc_info.value)

    def test_push_dry_run_prints_actions(self, tmp_path, mocker, capsys):
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

        push(template_file=template, dry_run=True)

        captured = capsys.readouterr()
        assert "Would push" in captured.out
        assert "Would render" in captured.out
        assert "Would reload" in captured.out
        # Executor should NOT have been called for actual operations
        mock_ex.run.assert_not_called()

    def test_push_happy_path(self, tmp_path, mocker, capsys):
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

        push(template_file=template)

        captured = capsys.readouterr()
        # Verify all three steps completed
        assert "[ok] Pushed" in captured.out
        assert "[ok] Rendered" in captured.out
        assert "[ok] Caddy reloaded" in captured.out
        # Verify render, validate, reload were called
        mock_render.assert_called_once_with(mock_cfg.proxy_template, executor=mock_ex)
        mock_validate.assert_called_once_with("rendered content", executor=mock_ex)
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
            push(template_file=template)

        assert "Failed to write" in str(exc_info.value)


class TestDiffCommand:
    """Test diff command."""

    def test_diff_equivalent_configs(self, tmp_path, mocker, capsys):
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

        diff(old=old, new=new)

        captured = capsys.readouterr()
        assert "[ok] Configs are equivalent" in captured.out

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

    def test_reload_calls_helper(self, mocker, capsys):
        """Should call reload_caddy helper with executor."""
        from rots.commands.proxy.app import reload

        mock_reload = mocker.patch("rots.commands.proxy.app.reload_caddy")

        reload()

        mock_reload.assert_called_once_with(executor=ANY)
        captured = capsys.readouterr()
        assert "[ok]" in captured.out

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

    def test_validate_existing_file(self, tmp_path, mocker, capsys):
        """Should validate an existing config file."""
        from rots.commands.proxy.app import validate

        config = tmp_path / "Caddyfile"
        config.write_text("localhost { }")

        mock_validate = mocker.patch("rots.commands.proxy.app.validate_caddy_config")

        validate(config_file=config)

        mock_validate.assert_called_once_with("localhost { }", executor=ANY, source_dir=tmp_path)
        captured = capsys.readouterr()
        assert "[ok]" in captured.out

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

    def test_validate_success_with_output(self, tmp_path, mocker, capsys):
        """Should print [ok] when validation succeeds."""
        from rots.commands.proxy.app import validate

        config = tmp_path / "Caddyfile"
        config.write_text("localhost { }")

        mocker.patch("rots.commands.proxy.app.validate_caddy_config")

        validate(config_file=config)

        captured = capsys.readouterr()
        assert "[ok]" in captured.out

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
