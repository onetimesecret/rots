# tests/integration/test_ssh_remote.py
"""Integration tests exercising remote code paths through FakeSSHServer.

These tests use the FakeSSHServer fixture (paramiko server mode) to run
SSHExecutor against a real transport layer without requiring a remote host.
They verify that the remote executor wiring in db and systemd modules works
end-to-end through the SSH transport.

Unlike unit tests that mock executor.run(), these tests exercise:
- SSHExecutor command serialisation (shlex quoting)
- Paramiko transport round-trip
- sqlite3 CLI invocation patterns (scripted responses)
- Remote branch selection in db/systemd modules
"""

from __future__ import annotations

import json

from ots_shared.ssh.executor import SSHExecutor


class TestSSHExecutorBasic:
    """Verify SSHExecutor works against FakeSSHServer."""

    def test_echo_roundtrip(self, fake_ssh_server):
        """SSHExecutor.run should capture stdout from the remote command."""
        fake_ssh_server.add_response("echo", stdout="hello\n")
        client = fake_ssh_server.connect()
        try:
            executor = SSHExecutor(client)
            result = executor.run(["echo", "hello"])
            assert result.ok
            assert result.stdout.strip() == "hello"
        finally:
            client.close()

    def test_non_zero_exit(self, fake_ssh_server):
        """SSHExecutor.run should capture non-zero exit codes."""
        fake_ssh_server.add_response("false", exit_code=1)
        client = fake_ssh_server.connect()
        try:
            executor = SSHExecutor(client)
            result = executor.run(["false"])
            assert not result.ok
            assert result.returncode == 1
        finally:
            client.close()


class TestDbRemoteViaSsh:
    """Test db module remote paths through a real SSH transport.

    FakeSSHServer returns scripted sqlite3 responses so we can verify
    the db module's remote query/execute wiring end-to-end.
    """

    def test_get_previous_tags_via_ssh(self, fake_ssh_server, tmp_path):
        """get_previous_tags with SSHExecutor should parse sqlite3 -json output."""
        from ots_containers import db

        # Script the sqlite3 response
        fake_ssh_server.add_response(
            "sqlite3",
            stdout=json.dumps(
                [
                    {"image": "img", "tag": "v2", "last_used": "2026-01-02 00:00:00"},
                    {"image": "img", "tag": "v1", "last_used": "2026-01-01 00:00:00"},
                ]
            ),
        )

        client = fake_ssh_server.connect()
        try:
            executor = SSHExecutor(client)
            db_path = tmp_path / "remote.db"
            tags = db.get_previous_tags(db_path, executor=executor)

            assert len(tags) == 2
            assert tags[0] == ("img", "v2", "2026-01-02 00:00:00")
            assert tags[1] == ("img", "v1", "2026-01-01 00:00:00")
        finally:
            client.close()

    def test_get_alias_via_ssh(self, fake_ssh_server, tmp_path):
        """get_alias with SSHExecutor should parse alias JSON from sqlite3."""
        from ots_containers import db

        fake_ssh_server.add_response(
            "sqlite3",
            stdout=json.dumps(
                [
                    {
                        "alias": "CURRENT",
                        "image": "ghcr.io/org/app",
                        "tag": "v3.0.0",
                        "set_at": "2026-01-20 10:00:00",
                    }
                ]
            ),
        )

        client = fake_ssh_server.connect()
        try:
            executor = SSHExecutor(client)
            db_path = tmp_path / "remote.db"
            alias = db.get_alias(db_path, "CURRENT", executor=executor)

            assert alias is not None
            assert alias.image == "ghcr.io/org/app"
            assert alias.tag == "v3.0.0"
        finally:
            client.close()

    def test_get_alias_not_found_via_ssh(self, fake_ssh_server, tmp_path):
        """get_alias with SSHExecutor should return None for empty result."""
        from ots_containers import db

        # Empty response = no rows
        fake_ssh_server.add_response("sqlite3", stdout="")

        client = fake_ssh_server.connect()
        try:
            executor = SSHExecutor(client)
            db_path = tmp_path / "remote.db"
            alias = db.get_alias(db_path, "NONEXISTENT", executor=executor)

            assert alias is None
        finally:
            client.close()


class TestSystemdRemoteViaSsh:
    """Test systemd module remote paths through a real SSH transport."""

    def test_require_systemctl_via_ssh(self, fake_ssh_server):
        """require_systemctl with SSHExecutor should pass when 'which' succeeds."""
        from ots_containers import systemd

        fake_ssh_server.add_response("which", stdout="/usr/bin/systemctl\n")

        client = fake_ssh_server.connect()
        try:
            executor = SSHExecutor(client)
            # Should not raise
            systemd.require_systemctl(executor=executor)
        finally:
            client.close()

    def test_require_podman_via_ssh(self, fake_ssh_server):
        """require_podman with SSHExecutor should pass when 'which' succeeds."""
        from ots_containers import systemd

        fake_ssh_server.add_response("which", stdout="/usr/bin/podman\n")

        client = fake_ssh_server.connect()
        try:
            executor = SSHExecutor(client)
            # Should not raise
            systemd.require_podman(executor=executor)
        finally:
            client.close()


class TestProxyRenderRemoteViaSsh:
    """Test proxy render_template remote path through a real SSH transport.

    The FakeSSHServer handles one command per connection. These tests verify
    that individual SSH commands in the render pipeline round-trip correctly
    through the transport layer. The full multi-command flow is covered by
    unit tests in test_helpers.py.
    """

    def test_render_template_remote_missing_raises(self, fake_ssh_server):
        """render_template should raise ProxyError when template doesn't exist remotely.

        This path only issues one command (test -f) before raising, so it
        works with the single-command FakeSSHServer.
        """
        from pathlib import Path

        import pytest

        from ots_containers.commands.proxy._helpers import ProxyError, render_template

        # Script: test -f fails (file not found)
        fake_ssh_server.add_response("test", exit_code=1)

        client = fake_ssh_server.connect()
        try:
            executor = SSHExecutor(client)
            with pytest.raises(ProxyError, match="Template not found"):
                render_template(
                    Path("/nonexistent/template"),
                    executor=executor,
                )
        finally:
            client.close()


class TestProxyReloadRemoteViaSsh:
    """Test proxy reload_caddy remote path through a real SSH transport."""

    def test_reload_remote_success(self, fake_ssh_server):
        """reload_caddy with SSHExecutor should succeed when systemctl reload returns 0."""
        from ots_containers.commands.proxy._helpers import reload_caddy

        fake_ssh_server.add_response("sudo -- systemctl reload caddy", stdout="")

        client = fake_ssh_server.connect()
        try:
            executor = SSHExecutor(client)
            # Should not raise
            reload_caddy(executor=executor)
        finally:
            client.close()

    def test_reload_remote_failure_raises(self, fake_ssh_server):
        """reload_caddy should raise ProxyError when systemctl reload fails."""
        import pytest

        from ots_containers.commands.proxy._helpers import ProxyError, reload_caddy

        fake_ssh_server.add_response(
            "sudo -- systemctl reload caddy",
            exit_code=1,
            stderr="Unit caddy.service not found.",
        )

        client = fake_ssh_server.connect()
        try:
            executor = SSHExecutor(client)
            with pytest.raises(ProxyError, match="Failed to reload caddy"):
                reload_caddy(executor=executor)
        finally:
            client.close()


class TestServiceSystemctlRemoteViaSsh:
    """Test service _helpers.systemctl remote path through a real SSH transport."""

    def test_systemctl_remote_success(self, fake_ssh_server):
        """systemctl with SSHExecutor should capture active status."""
        from ots_containers.commands.service._helpers import systemctl

        fake_ssh_server.add_response("systemctl is-active", stdout="active\n")

        client = fake_ssh_server.connect()
        try:
            executor = SSHExecutor(client)
            result = systemctl(
                "is-active", "valkey-server@6379.service", check=False, executor=executor
            )
            assert result.returncode == 0
            assert result.stdout.strip() == "active"
        finally:
            client.close()

    def test_systemctl_remote_inactive(self, fake_ssh_server):
        """systemctl should report inactive service correctly."""
        from ots_containers.commands.service._helpers import systemctl

        fake_ssh_server.add_response("systemctl is-active", stdout="inactive\n", exit_code=3)

        client = fake_ssh_server.connect()
        try:
            executor = SSHExecutor(client)
            result = systemctl(
                "is-active", "valkey-server@6379.service", check=False, executor=executor
            )
            assert result.returncode == 3
            assert result.stdout.strip() == "inactive"
        finally:
            client.close()

    def test_is_service_active_remote(self, fake_ssh_server):
        """is_service_active with SSHExecutor should return True for active service."""
        from ots_containers.commands.service._helpers import is_service_active

        fake_ssh_server.add_response("systemctl is-active", stdout="active\n")

        client = fake_ssh_server.connect()
        try:
            executor = SSHExecutor(client)
            assert is_service_active("valkey-server@6379.service", executor=executor) is True
        finally:
            client.close()

    def test_is_service_enabled_remote(self, fake_ssh_server):
        """is_service_enabled with SSHExecutor should return True for enabled service."""
        from ots_containers.commands.service._helpers import is_service_enabled

        fake_ssh_server.add_response("systemctl is-enabled", stdout="enabled\n")

        client = fake_ssh_server.connect()
        try:
            executor = SSHExecutor(client)
            assert is_service_enabled("valkey-server@6379.service", executor=executor) is True
        finally:
            client.close()

    def test_is_service_enabled_remote_disabled(self, fake_ssh_server):
        """is_service_enabled should return False for disabled service."""
        from ots_containers.commands.service._helpers import is_service_enabled

        fake_ssh_server.add_response("systemctl is-enabled", stdout="disabled\n", exit_code=1)

        client = fake_ssh_server.connect()
        try:
            executor = SSHExecutor(client)
            assert is_service_enabled("valkey-server@6379.service", executor=executor) is False
        finally:
            client.close()


class TestServiceFileOpsRemoteViaSsh:
    """Test service _helpers file operations via SSH transport.

    These test the remote-aware file primitives (_file_exists, _read_text, etc.)
    through a real SSHExecutor -> FakeSSHServer path.
    """

    def test_file_exists_remote_found(self, fake_ssh_server):
        """_file_exists should return True when test -f succeeds."""
        from pathlib import Path

        from ots_containers.commands.service._helpers import _file_exists

        fake_ssh_server.add_response("test", stdout="")

        client = fake_ssh_server.connect()
        try:
            executor = SSHExecutor(client)
            assert _file_exists(Path("/etc/valkey/valkey.conf"), executor) is True
        finally:
            client.close()

    def test_file_exists_remote_not_found(self, fake_ssh_server):
        """_file_exists should return False when test -f fails."""
        from pathlib import Path

        from ots_containers.commands.service._helpers import _file_exists

        fake_ssh_server.add_response("test", exit_code=1)

        client = fake_ssh_server.connect()
        try:
            executor = SSHExecutor(client)
            assert _file_exists(Path("/nonexistent/file"), executor) is False
        finally:
            client.close()

    def test_read_text_remote(self, fake_ssh_server):
        """_read_text should return cat output from remote."""
        from pathlib import Path

        from ots_containers.commands.service._helpers import _read_text

        fake_ssh_server.add_response("cat", stdout="bind 127.0.0.1\nport 6379\n")

        client = fake_ssh_server.connect()
        try:
            executor = SSHExecutor(client)
            content = _read_text(Path("/etc/valkey/valkey.conf"), executor)
            assert "bind 127.0.0.1" in content
            assert "port 6379" in content
        finally:
            client.close()
