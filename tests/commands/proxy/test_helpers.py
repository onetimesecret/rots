# tests/commands/proxy/test_helpers.py
"""Tests for proxy command helpers."""

import json
import socket
import subprocess
import urllib.request

import pytest


class TestRenderTemplate:
    """Test render_template function."""

    def test_render_template_calls_envsubst(self, tmp_path, mocker):
        """Should call envsubst with template content."""
        from rots.commands.proxy._helpers import render_template

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
        from rots.commands.proxy._helpers import ProxyError, render_template

        missing = tmp_path / "nonexistent.template"

        with pytest.raises(ProxyError) as exc_info:
            render_template(missing)

        assert "Template not found" in str(exc_info.value)

    def test_render_template_envsubst_failure_raises(self, tmp_path, mocker):
        """Should raise ProxyError when envsubst fails."""
        from rots.commands.proxy._helpers import ProxyError, render_template

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
        from rots.commands.proxy._helpers import ProxyError, render_template

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
        from rots.commands.proxy._helpers import validate_caddy_config

        mock_result = mocker.Mock()
        mock_result.returncode = 0
        mocker.patch("subprocess.run", return_value=mock_result)

        # Should not raise
        validate_caddy_config("localhost:8080 {\n  respond 200\n}")

    def test_validate_caddy_config_failure_raises(self, mocker):
        """Should raise ProxyError when validation fails."""
        from rots.commands.proxy._helpers import ProxyError, validate_caddy_config

        mock_result = mocker.Mock()
        mock_result.returncode = 1
        mock_result.stderr = "syntax error at line 1"
        mocker.patch("subprocess.run", return_value=mock_result)

        with pytest.raises(ProxyError) as exc_info:
            validate_caddy_config("invalid config")

        assert "Caddy validation failed" in str(exc_info.value)

    def test_validate_caddy_config_calls_caddy_correctly(self, mocker):
        """Should call caddy validate with temp file."""
        from rots.commands.proxy._helpers import validate_caddy_config

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
        from rots.commands.proxy._helpers import validate_caddy_config

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
        from rots.commands.proxy._helpers import ProxyError, validate_caddy_config

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
        from rots.commands.proxy._helpers import reload_caddy

        mock_run = mocker.patch("subprocess.run")

        reload_caddy()

        mock_run.assert_called_once_with(
            ["sudo", "systemctl", "reload", "caddy"],
            check=True,
        )

    def test_reload_caddy_failure_raises(self, mocker):
        """Should raise ProxyError when reload fails."""
        from rots.commands.proxy._helpers import ProxyError, reload_caddy

        mocker.patch(
            "subprocess.run",
            side_effect=subprocess.CalledProcessError(1, "cmd"),
        )

        with pytest.raises(ProxyError) as exc_info:
            reload_caddy()

        assert "Failed to reload" in str(exc_info.value)


class TestAdaptToJson:
    """Test adapt_to_json function."""

    def test_adapt_to_json_returns_sorted_json(self, tmp_path, mocker):
        """Should run caddy adapt and return sorted JSON."""
        from rots.commands.proxy._helpers import adapt_to_json

        config = tmp_path / "Caddyfile"
        config.write_text("localhost { }")

        # caddy adapt returns unsorted JSON
        raw_json = '{"z_key": 1, "a_key": 2}'
        mock_result = mocker.Mock()
        mock_result.returncode = 0
        mock_result.stdout = raw_json
        mock_result.stderr = ""
        mocker.patch("subprocess.run", return_value=mock_result)

        result = adapt_to_json(config)

        import json

        parsed = json.loads(result)
        assert parsed == {"a_key": 2, "z_key": 1}
        # Keys should be sorted in output
        keys = list(json.loads(result).keys())
        assert keys == ["a_key", "z_key"]

    def test_adapt_to_json_missing_file_raises(self, tmp_path):
        """Should raise ProxyError when config file not found."""
        from rots.commands.proxy._helpers import ProxyError, adapt_to_json

        missing = tmp_path / "nonexistent.conf"

        with pytest.raises(ProxyError, match="Config file not found"):
            adapt_to_json(missing)

    def test_adapt_to_json_caddy_failure_raises(self, tmp_path, mocker):
        """Should raise ProxyError when caddy adapt fails."""
        from rots.commands.proxy._helpers import ProxyError, adapt_to_json

        config = tmp_path / "Caddyfile"
        config.write_text("invalid {{{")

        mock_result = mocker.Mock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = "adapt: parse error"
        mocker.patch("subprocess.run", return_value=mock_result)

        with pytest.raises(ProxyError, match="caddy adapt failed"):
            adapt_to_json(config)

    def test_adapt_to_json_invalid_json_raises(self, tmp_path, mocker):
        """Should raise ProxyError when caddy adapt returns invalid JSON."""
        from rots.commands.proxy._helpers import ProxyError, adapt_to_json

        config = tmp_path / "Caddyfile"
        config.write_text("localhost { }")

        mock_result = mocker.Mock()
        mock_result.returncode = 0
        mock_result.stdout = "not json at all"
        mock_result.stderr = ""
        mocker.patch("subprocess.run", return_value=mock_result)

        with pytest.raises(ProxyError, match="invalid JSON"):
            adapt_to_json(config)

    def test_adapt_to_json_caddy_not_found_raises(self, tmp_path, mocker):
        """Should raise ProxyError when caddy not in PATH."""
        from rots.commands.proxy._helpers import ProxyError, adapt_to_json

        config = tmp_path / "Caddyfile"
        config.write_text("localhost { }")

        mocker.patch("subprocess.run", side_effect=FileNotFoundError("caddy not found"))

        with pytest.raises(ProxyError, match="caddy not found"):
            adapt_to_json(config)

    def test_adapt_to_json_calls_caddy_adapt_correctly(self, tmp_path, mocker):
        """Should call caddy adapt with --config and --adapter caddyfile."""
        from rots.commands.proxy._helpers import adapt_to_json

        config = tmp_path / "Caddyfile"
        config.write_text("localhost { }")

        mock_result = mocker.Mock()
        mock_result.returncode = 0
        mock_result.stdout = "{}"
        mock_result.stderr = ""
        mock_run = mocker.patch("subprocess.run", return_value=mock_result)

        adapt_to_json(config)

        call_args = mock_run.call_args[0][0]
        assert call_args == ["caddy", "adapt", "--config", str(config), "--adapter", "caddyfile"]


class TestRemoteAdaptToJson:
    """Test adapt_to_json with a remote executor."""

    def _make_executor(self, mocker, responses):
        from ots_shared.ssh.executor import Result

        mock_ex = mocker.MagicMock()
        mock_ex.run.side_effect = [
            Result(command=r[0], returncode=r[1], stdout=r[2], stderr=r[3]) for r in responses
        ]
        return mock_ex

    def test_adapt_to_json_remote(self, mocker):
        """Should run caddy adapt on remote and return sorted JSON."""
        from pathlib import Path

        from rots.commands.proxy._helpers import adapt_to_json

        ex = self._make_executor(
            mocker,
            [
                ("test -f /etc/caddy/Caddyfile", 0, "", ""),
                ("caddy adapt ...", 0, '{"z": 1, "a": 2}', ""),
            ],
        )

        result = adapt_to_json(Path("/etc/caddy/Caddyfile"), executor=ex)

        import json

        assert list(json.loads(result).keys()) == ["a", "z"]

    def test_adapt_to_json_remote_missing(self, mocker):
        """Should raise ProxyError when remote file not found."""
        from pathlib import Path

        from rots.commands.proxy._helpers import ProxyError, adapt_to_json

        ex = self._make_executor(
            mocker,
            [("test -f /missing", 1, "", "")],
        )

        with pytest.raises(ProxyError, match="Config file not found"):
            adapt_to_json(Path("/missing"), executor=ex)


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

        from rots.commands.proxy._helpers import render_template

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

        from rots.commands.proxy._helpers import ProxyError, render_template

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
        from rots.commands.proxy._helpers import validate_caddy_config

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
        from rots.commands.proxy._helpers import ProxyError, validate_caddy_config

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
        from rots.commands.proxy._helpers import ProxyError, validate_caddy_config

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

        from rots.commands.proxy._helpers import reload_caddy

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


class TestFindFreePort:
    """Test find_free_port function."""

    def test_returns_int_in_valid_range(self):
        """Should return an integer port in the ephemeral range."""
        from rots.commands.proxy._helpers import find_free_port

        port = find_free_port()
        assert isinstance(port, int)
        assert 1024 < port < 65536

    def test_port_is_bindable(self):
        """Returned port should be immediately bindable."""
        from rots.commands.proxy._helpers import find_free_port

        port = find_free_port()
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", port))  # should not raise


class TestPatchCaddyJson:
    """Test patch_caddy_json function."""

    def _minimal_config(self, *, upstreams: str = "app:3000") -> dict:
        """Build a minimal Caddy JSON config for testing."""
        return {
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
                                            "upstreams": [{"dial": upstreams}],
                                        }
                                    ]
                                }
                            ],
                        }
                    }
                }
            }
        }

    def test_replaces_listen_port(self):
        from rots.commands.proxy._helpers import patch_caddy_json

        result = patch_caddy_json(self._minimal_config(), caddy_port=9999, echo_addr="x:1")
        srv = result["apps"]["http"]["servers"]["srv0"]
        assert srv["listen"] == ["127.0.0.1:9999"]

    def test_replaces_upstreams(self):
        from rots.commands.proxy._helpers import patch_caddy_json

        result = patch_caddy_json(
            self._minimal_config(), caddy_port=9999, echo_addr="127.0.0.1:8888"
        )
        route_handlers = result["apps"]["http"]["servers"]["srv0"]["routes"][0]["handle"]
        # First handler is the injected X-Trace-Route marker
        assert route_handlers[0]["handler"] == "headers"
        assert route_handlers[1]["upstreams"][0]["dial"] == "127.0.0.1:8888"

    def test_disables_https(self):
        from rots.commands.proxy._helpers import patch_caddy_json

        result = patch_caddy_json(self._minimal_config(), caddy_port=9999, echo_addr="x:1")
        srv = result["apps"]["http"]["servers"]["srv0"]
        assert srv["automatic_https"] == {"disable": True}

    def test_disables_admin(self):
        from rots.commands.proxy._helpers import patch_caddy_json

        result = patch_caddy_json(self._minimal_config(), caddy_port=9999, echo_addr="x:1")
        assert result["admin"]["disabled"] is True

    def test_does_not_mutate_input(self):
        from rots.commands.proxy._helpers import patch_caddy_json

        original = self._minimal_config()
        import copy

        frozen = copy.deepcopy(original)
        patch_caddy_json(original, caddy_port=9999, echo_addr="x:1")
        assert original == frozen

    def test_handles_nested_subroutes(self):
        from rots.commands.proxy._helpers import patch_caddy_json

        config = {
            "apps": {
                "http": {
                    "servers": {
                        "srv0": {
                            "listen": [":443"],
                            "routes": [
                                {
                                    "handle": [
                                        {
                                            "handler": "subroute",
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
                                    ]
                                }
                            ],
                        }
                    }
                }
            }
        }
        result = patch_caddy_json(config, caddy_port=9999, echo_addr="127.0.0.1:7777")
        # handle[0] is the injected marker, handle[1] is the subroute
        nested = result["apps"]["http"]["servers"]["srv0"]["routes"][0]["handle"][1]
        proxy = nested["routes"][0]["handle"][0]
        assert proxy["upstreams"][0]["dial"] == "127.0.0.1:7777"

    def test_merges_multiple_servers(self):
        from rots.commands.proxy._helpers import patch_caddy_json

        config = {
            "apps": {
                "http": {
                    "servers": {
                        "srv0": {
                            "listen": [":443"],
                            "routes": [{"handle": [{"handler": "static_response"}]}],
                        },
                        "srv1": {
                            "listen": [":8443"],
                            "routes": [{"handle": [{"handler": "encode"}]}],
                        },
                    }
                }
            }
        }
        result = patch_caddy_json(config, caddy_port=5555, echo_addr="x:1")
        servers = result["apps"]["http"]["servers"]
        assert len(servers) == 1
        srv = servers["srv0"]
        assert srv["listen"] == ["127.0.0.1:5555"]
        # Routes from both original servers are merged
        assert len(srv["routes"]) == 2

    def test_strips_tls_app(self):
        from rots.commands.proxy._helpers import patch_caddy_json

        config = {
            "apps": {
                "http": {
                    "servers": {
                        "srv0": {"listen": [":443"], "routes": []},
                    }
                },
                "tls": {"automation": {"policies": [{"issuers": [{"module": "acme"}]}]}},
            }
        }
        result = patch_caddy_json(config, caddy_port=9999, echo_addr="x:1")
        assert "tls" not in result["apps"]

    def test_strips_tls_connection_policies(self):
        from rots.commands.proxy._helpers import patch_caddy_json

        config = {
            "apps": {
                "http": {
                    "servers": {
                        "srv0": {
                            "listen": [":443"],
                            "routes": [],
                            "tls_connection_policies": [{"match": {}}],
                        },
                    }
                }
            }
        }
        result = patch_caddy_json(config, caddy_port=9999, echo_addr="x:1")
        assert "tls_connection_policies" not in result["apps"]["http"]["servers"]["srv0"]

    def test_injects_trace_header_with_hosts(self):
        from rots.commands.proxy._helpers import patch_caddy_json

        config = {
            "apps": {
                "http": {
                    "servers": {
                        "srv0": {
                            "listen": [":443"],
                            "routes": [
                                {
                                    "match": [{"host": ["a.example.com", "b.example.com"]}],
                                    "handle": [{"handler": "static_response"}],
                                }
                            ],
                        }
                    }
                }
            }
        }
        result = patch_caddy_json(config, caddy_port=9999, echo_addr="x:1")
        marker = result["apps"]["http"]["servers"]["srv0"]["routes"][0]["handle"][0]
        assert marker["handler"] == "headers"
        label = marker["response"]["set"]["X-Trace-Route"][0]
        assert label == ":443 a.example.com, b.example.com"

    def test_injects_trace_header_catchall(self):
        from rots.commands.proxy._helpers import patch_caddy_json

        result = patch_caddy_json(self._minimal_config(), caddy_port=9999, echo_addr="x:1")
        marker = result["apps"]["http"]["servers"]["srv0"]["routes"][0]["handle"][0]
        assert marker["handler"] == "headers"
        label = marker["response"]["set"]["X-Trace-Route"][0]
        # No host matcher → wildcard
        assert label == ":443 *"

    def test_live_mode_preserves_upstreams(self):
        from rots.commands.proxy._helpers import patch_caddy_json

        result = patch_caddy_json(
            self._minimal_config(upstreams="app:3000"),
            caddy_port=9999,
            echo_addr=None,
        )
        routes = result["apps"]["http"]["servers"]["srv0"]["routes"]
        # Find the reverse_proxy handler (skip injected trace header)
        proxy = next(
            h for r in routes for h in r.get("handle", []) if h.get("handler") == "reverse_proxy"
        )
        assert proxy["upstreams"][0]["dial"] == "app:3000"

    def test_raises_on_missing_servers(self):
        from rots.commands.proxy._helpers import ProxyError, patch_caddy_json

        with pytest.raises(ProxyError, match="No apps.http.servers"):
            patch_caddy_json({}, caddy_port=9999, echo_addr="x:1")

        with pytest.raises(ProxyError, match="No apps.http.servers"):
            patch_caddy_json({"apps": {"http": {}}}, caddy_port=9999, echo_addr="x:1")


class TestParseTraceUrl:
    """Test parse_trace_url function."""

    def test_full_url_parsed(self):
        """Should parse a full https URL and expose semantic attributes."""
        from rots.commands.proxy._helpers import parse_trace_url

        parsed = parse_trace_url("https://us.onetime.co/api/v2/status")
        assert parsed.scheme == "https"
        assert parsed.hostname == "us.onetime.co"
        assert parsed.path == "/api/v2/status"
        assert parsed.query == ""

    def test_bare_host_path_gets_scheme(self):
        """Should prepend https:// when no scheme is provided."""
        from rots.commands.proxy._helpers import parse_trace_url

        parsed = parse_trace_url("us.onetime.co/api/v2/status")
        assert parsed.scheme == "https"
        assert parsed.hostname == "us.onetime.co"
        assert parsed.path == "/api/v2/status"

    def test_preserves_query_string(self):
        """Should preserve query parameters as a separate attribute."""
        from rots.commands.proxy._helpers import parse_trace_url

        parsed = parse_trace_url("https://example.com/search?q=test&page=2")
        assert parsed.hostname == "example.com"
        assert parsed.path == "/search"
        assert parsed.query == "q=test&page=2"

    def test_no_hostname_raises(self):
        """Should raise ProxyError when URL has no hostname."""
        from rots.commands.proxy._helpers import ProxyError, parse_trace_url

        with pytest.raises(ProxyError, match="no hostname"):
            parse_trace_url("https:///no-host")

    def test_bare_host_no_path(self):
        """Should handle a bare hostname with no path."""
        from rots.commands.proxy._helpers import parse_trace_url

        parsed = parse_trace_url("example.com")
        assert parsed.hostname == "example.com"
        assert parsed.path == ""

    def test_http_scheme_preserved(self):
        """Should not override an explicit http:// scheme."""
        from rots.commands.proxy._helpers import parse_trace_url

        parsed = parse_trace_url("http://localhost:8080/health")
        assert parsed.scheme == "http"
        assert parsed.hostname == "localhost"
        assert parsed.port == 8080
        assert parsed.path == "/health"


class TestRunEchoServer:
    """Test run_echo_server context manager."""

    def test_returns_json_body(self):
        from rots.commands.proxy._helpers import find_free_port, run_echo_server

        port = find_free_port()
        with run_echo_server(port) as (addr, _received):
            resp = urllib.request.urlopen(f"http://{addr}/test")  # noqa: S310
            body = json.loads(resp.read())
            assert body["method"] == "GET"
            assert body["path"] == "/test"

    def test_captures_host_header(self):
        from rots.commands.proxy._helpers import find_free_port, run_echo_server

        port = find_free_port()
        with run_echo_server(port) as (addr, received):
            req = urllib.request.Request(f"http://{addr}/hello")
            req.add_header("Host", "example.com")
            urllib.request.urlopen(req)  # noqa: S310
            assert len(received) == 1
            assert received[0]["headers"]["Host"] == "example.com"

    def test_shuts_down_after_context_exit(self):
        from rots.commands.proxy._helpers import find_free_port, run_echo_server

        port = find_free_port()
        with run_echo_server(port):
            pass  # server active inside
        # After exiting, port should be unbound (server shut down)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", port))  # should not raise
