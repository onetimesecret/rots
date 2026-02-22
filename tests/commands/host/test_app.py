# tests/commands/host/test_app.py

"""Tests for host command .otsinfra.env integration."""

import pytest

from ots_containers.commands.host.app import _resolve_config_dir, _resolve_ssh_host, init_env


class TestResolveConfigDir:
    """Tests for config directory resolution in host commands."""

    def test_explicit_dir_returned(self, tmp_path):
        config_dir = tmp_path / "config-v0.24"
        config_dir.mkdir()
        result = _resolve_config_dir(config_dir)
        assert result == config_dir.resolve()

    def test_explicit_nonexistent_dir_exits(self, tmp_path):
        with pytest.raises(SystemExit):
            _resolve_config_dir(tmp_path / "nonexistent")

    def test_resolves_from_env_file(self, tmp_path, monkeypatch):
        env_file = tmp_path / ".otsinfra.env"
        env_file.write_text("OTS_TAG=v0.24\n")
        config_dir = tmp_path / "config-v0.24"
        config_dir.mkdir()
        monkeypatch.chdir(tmp_path)

        result = _resolve_config_dir(None)
        assert result == config_dir

    def test_exits_when_no_env_file(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        with pytest.raises(SystemExit):
            _resolve_config_dir(None)


class TestResolveSshHost:
    """Tests for SSH host resolution in host commands."""

    def test_from_env_var(self, monkeypatch):
        monkeypatch.setenv("OTS_HOST", "test-host")
        result = _resolve_ssh_host()
        assert result == "test-host"

    def test_from_env_file(self, tmp_path, monkeypatch):
        monkeypatch.delenv("OTS_HOST", raising=False)
        env_file = tmp_path / ".otsinfra.env"
        env_file.write_text("OTS_HOST=file-host\n")
        monkeypatch.chdir(tmp_path)

        result = _resolve_ssh_host()
        assert result == "file-host"

    def test_exits_when_no_host(self, tmp_path, monkeypatch):
        monkeypatch.delenv("OTS_HOST", raising=False)
        monkeypatch.chdir(tmp_path)
        with pytest.raises(SystemExit):
            _resolve_ssh_host()

    def test_context_host_takes_priority(self, monkeypatch):
        from ots_containers import context

        monkeypatch.setenv("OTS_HOST", "env-host")
        token = context.host_var.set("context-host")
        try:
            result = _resolve_ssh_host()
            assert result == "context-host"
        finally:
            context.host_var.reset(token)


class TestInitEnv:
    """Tests for host init command that scaffolds .otsinfra.env."""

    def test_creates_env_file(self, tmp_path):
        init_env(directory=tmp_path, host="prod-us1", tag="v0.24")
        env_file = tmp_path / ".otsinfra.env"
        assert env_file.exists()
        content = env_file.read_text()
        assert "OTS_HOST=prod-us1" in content
        assert "OTS_TAG=v0.24" in content

    def test_creates_in_cwd_when_no_directory(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        init_env(directory=None, host="test-host")
        assert (tmp_path / ".otsinfra.env").exists()

    def test_refuses_overwrite_without_force(self, tmp_path):
        env_file = tmp_path / ".otsinfra.env"
        env_file.write_text("OTS_HOST=old\n")
        with pytest.raises(SystemExit):
            init_env(directory=tmp_path, host="new-host")
        # Original content preserved
        assert "old" in env_file.read_text()

    def test_overwrites_with_force(self, tmp_path):
        env_file = tmp_path / ".otsinfra.env"
        env_file.write_text("OTS_HOST=old\n")
        init_env(directory=tmp_path, host="new-host", force=True)
        assert "OTS_HOST=new-host" in env_file.read_text()

    def test_exits_for_nonexistent_directory(self, tmp_path):
        with pytest.raises(SystemExit):
            init_env(directory=tmp_path / "nonexistent", host="test")

    def test_empty_values_still_creates(self, tmp_path):
        init_env(directory=tmp_path)
        env_file = tmp_path / ".otsinfra.env"
        assert env_file.exists()
        content = env_file.read_text()
        assert "OTS_HOST=" in content
