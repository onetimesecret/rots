# tests/commands/host/test_app.py

"""Tests for host command .otsinfra.env integration and executor routing."""

from unittest.mock import MagicMock, patch

import pytest
from ots_shared.ssh.executor import Result

from ots_containers.commands.host.app import (
    _get_executor,
    _resolve_config_dir,
    _resolve_ssh_host,
    diff,
    init_env,
    pull,
    status,
)


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


class TestGetExecutor:
    """Tests for _get_executor routing through Config.get_executor."""

    def test_routes_through_config_get_executor(self, mocker):
        """_get_executor should call Config.get_executor with context host."""
        from ots_containers import context

        mock_executor = MagicMock()
        mock_config = MagicMock()
        mock_config.get_executor.return_value = mock_executor
        mocker.patch("ots_containers.config.Config", return_value=mock_config)

        token = context.host_var.set("test-host")
        try:
            result = _get_executor()
            assert result is mock_executor
            mock_config.get_executor.assert_called_once_with(host="test-host")
        finally:
            context.host_var.reset(token)

    def test_passes_none_when_no_host(self, mocker):
        """_get_executor should pass host=None when context has no host."""
        from ots_containers import context

        mock_executor = MagicMock()
        mock_config = MagicMock()
        mock_config.get_executor.return_value = mock_executor
        mocker.patch("ots_containers.config.Config", return_value=mock_config)

        token = context.host_var.set(None)
        try:
            result = _get_executor()
            assert result is mock_executor
            mock_config.get_executor.assert_called_once_with(host=None)
        finally:
            context.host_var.reset(token)


class TestDiffCommand:
    """Tests for host diff command executor routing."""

    def _setup_manifest(self, tmp_path, files=None):
        """Create a config dir with manifest and files for testing."""
        config_dir = tmp_path / "config-v0.24"
        config_dir.mkdir()

        if files is None:
            files = {"config.yaml": "local: content\n"}

        for name, content in files.items():
            (config_dir / name).write_text(content)

        # Create manifest
        manifest_lines = []
        for name in files:
            manifest_lines.append(f"{name} /etc/onetimesecret/{name}")
        (config_dir / "manifest.conf").write_text("\n".join(manifest_lines) + "\n")

        return config_dir

    @patch("ots_containers.commands.host.app._get_executor")
    @patch("ots_containers.commands.host.app._resolve_ssh_host")
    def test_diff_calls_executor_cat_with_timeout(self, mock_host, mock_get_ex, tmp_path):
        """diff should call executor.run(['cat', ...]) with timeout=30."""
        mock_host.return_value = "test-host"
        mock_executor = MagicMock()
        mock_executor.run.return_value = Result(
            command="cat /etc/onetimesecret/config.yaml",
            returncode=0,
            stdout="local: content\n",
            stderr="",
        )
        mock_get_ex.return_value = mock_executor

        config_dir = self._setup_manifest(tmp_path)
        diff(config_dir=config_dir)

        # Verify executor.run was called with cat and timeout
        mock_executor.run.assert_called_once()
        call_args = mock_executor.run.call_args
        assert call_args[0][0] == ["cat", "/etc/onetimesecret/config.yaml"]
        assert call_args[1]["timeout"] == 30

    @patch("ots_containers.commands.host.app._get_executor")
    @patch("ots_containers.commands.host.app._resolve_ssh_host")
    def test_diff_shows_new_file_when_remote_missing(
        self, mock_host, mock_get_ex, tmp_path, capsys
    ):
        """diff should show file as new when cat returns non-zero (not found)."""
        mock_host.return_value = "test-host"
        mock_executor = MagicMock()
        mock_executor.run.return_value = Result(
            command="cat /etc/onetimesecret/config.yaml",
            returncode=1,
            stdout="",
            stderr="No such file",
        )
        mock_get_ex.return_value = mock_executor

        config_dir = self._setup_manifest(tmp_path)
        diff(config_dir=config_dir)

        captured = capsys.readouterr()
        assert "not found" in captured.out

    @patch("ots_containers.commands.host.app._get_executor")
    @patch("ots_containers.commands.host.app._resolve_ssh_host")
    def test_diff_shows_identical_when_same(self, mock_host, mock_get_ex, tmp_path, capsys):
        """diff should show [identical] when local and remote match."""
        mock_host.return_value = "test-host"
        mock_executor = MagicMock()
        mock_executor.run.return_value = Result(
            command="cat /etc/onetimesecret/config.yaml",
            returncode=0,
            stdout="local: content\n",
            stderr="",
        )
        mock_get_ex.return_value = mock_executor

        config_dir = self._setup_manifest(tmp_path)
        diff(config_dir=config_dir)

        captured = capsys.readouterr()
        assert "identical" in captured.out


class TestPullCommand:
    """Tests for host pull command executor routing."""

    def _setup_manifest(self, tmp_path, files=None):
        """Create a config dir with manifest for testing."""
        config_dir = tmp_path / "config-v0.24"
        config_dir.mkdir()

        if files is None:
            files = ["config.yaml"]

        manifest_lines = []
        for name in files:
            manifest_lines.append(f"{name} /etc/onetimesecret/{name}")
        (config_dir / "manifest.conf").write_text("\n".join(manifest_lines) + "\n")

        return config_dir

    @patch("ots_containers.commands.host.app._get_executor")
    @patch("ots_containers.commands.host.app._resolve_ssh_host")
    def test_pull_calls_executor_cat_with_timeout(self, mock_host, mock_get_ex, tmp_path):
        """pull should call executor.run(['cat', ...]) with timeout=30."""
        mock_host.return_value = "test-host"
        mock_executor = MagicMock()
        mock_executor.run.return_value = Result(
            command="cat /etc/onetimesecret/config.yaml",
            returncode=0,
            stdout="remote: content\n",
            stderr="",
        )
        mock_get_ex.return_value = mock_executor

        config_dir = self._setup_manifest(tmp_path)
        pull(config_dir=config_dir, apply=True)

        # Verify executor.run was called with cat and timeout
        mock_executor.run.assert_called_once()
        call_args = mock_executor.run.call_args
        assert call_args[0][0] == ["cat", "/etc/onetimesecret/config.yaml"]
        assert call_args[1]["timeout"] == 30

    @patch("ots_containers.commands.host.app._get_executor")
    @patch("ots_containers.commands.host.app._resolve_ssh_host")
    def test_pull_apply_writes_file(self, mock_host, mock_get_ex, tmp_path):
        """pull --apply should write remote content to local file."""
        mock_host.return_value = "test-host"
        mock_executor = MagicMock()
        mock_executor.run.return_value = Result(
            command="cat /etc/onetimesecret/config.yaml",
            returncode=0,
            stdout="remote: pulled content\n",
            stderr="",
        )
        mock_get_ex.return_value = mock_executor

        config_dir = self._setup_manifest(tmp_path)
        pull(config_dir=config_dir, apply=True)

        local_file = config_dir / "config.yaml"
        assert local_file.exists()
        assert local_file.read_text() == "remote: pulled content\n"

    @patch("ots_containers.commands.host.app._get_executor")
    @patch("ots_containers.commands.host.app._resolve_ssh_host")
    def test_pull_skips_missing_remote_file(self, mock_host, mock_get_ex, tmp_path, capsys):
        """pull should skip files that don't exist on remote."""
        mock_host.return_value = "test-host"
        mock_executor = MagicMock()
        mock_executor.run.return_value = Result(
            command="cat /etc/onetimesecret/config.yaml",
            returncode=1,
            stdout="",
            stderr="No such file",
        )
        mock_get_ex.return_value = mock_executor

        config_dir = self._setup_manifest(tmp_path)
        pull(config_dir=config_dir, apply=True)

        captured = capsys.readouterr()
        assert "skip" in captured.out
        assert not (config_dir / "config.yaml").exists()


class TestStatusCommand:
    """Tests for host status command executor routing."""

    def _setup_manifest(self, tmp_path, create_local=True):
        """Create config dir with manifest for testing."""
        config_dir = tmp_path / "config-v0.24"
        config_dir.mkdir()

        manifest = "config.yaml /etc/onetimesecret/config.yaml\n"
        (config_dir / "manifest.conf").write_text(manifest)

        if create_local:
            (config_dir / "config.yaml").write_text("content\n")

        return config_dir

    @patch("ots_containers.commands.host.app._get_executor")
    @patch("ots_containers.commands.host.app._resolve_ssh_host")
    def test_status_calls_executor_test_with_timeout(self, mock_host, mock_get_ex, tmp_path):
        """status should call executor.run(['test', '-f', ...]) with timeout=10."""
        mock_host.return_value = "test-host"
        mock_executor = MagicMock()
        mock_executor.run.return_value = Result(
            command="test -f /etc/onetimesecret/config.yaml",
            returncode=0,
            stdout="",
            stderr="",
        )
        mock_get_ex.return_value = mock_executor

        config_dir = self._setup_manifest(tmp_path)
        status(config_dir=config_dir)

        mock_executor.run.assert_called_once()
        call_args = mock_executor.run.call_args
        assert call_args[0][0] == ["test", "-f", "/etc/onetimesecret/config.yaml"]
        assert call_args[1]["timeout"] == 10

    @patch("ots_containers.commands.host.app._get_executor")
    @patch("ots_containers.commands.host.app._resolve_ssh_host")
    def test_status_shows_yes_when_remote_exists(self, mock_host, mock_get_ex, tmp_path, capsys):
        """status should show 'yes' for remote when test -f returns 0."""
        mock_host.return_value = "test-host"
        mock_executor = MagicMock()
        mock_executor.run.return_value = Result(
            command="test -f ...",
            returncode=0,
            stdout="",
            stderr="",
        )
        mock_get_ex.return_value = mock_executor

        config_dir = self._setup_manifest(tmp_path, create_local=True)
        status(config_dir=config_dir)

        captured = capsys.readouterr()
        # Both local and remote should show 'yes'
        lines = captured.out.strip().split("\n")
        data_line = [l for l in lines if "config.yaml" in l and "---" not in l]
        assert len(data_line) == 1
        assert "yes" in data_line[0]

    @patch("ots_containers.commands.host.app._get_executor")
    @patch("ots_containers.commands.host.app._resolve_ssh_host")
    def test_status_shows_no_when_remote_missing(self, mock_host, mock_get_ex, tmp_path, capsys):
        """status should show 'no' for remote when test -f returns non-zero."""
        mock_host.return_value = "test-host"
        mock_executor = MagicMock()
        mock_executor.run.return_value = Result(
            command="test -f ...",
            returncode=1,
            stdout="",
            stderr="",
        )
        mock_get_ex.return_value = mock_executor

        config_dir = self._setup_manifest(tmp_path, create_local=True)
        status(config_dir=config_dir)

        captured = capsys.readouterr()
        lines = captured.out.strip().split("\n")
        data_line = [l for l in lines if "config.yaml" in l and "---" not in l]
        assert len(data_line) == 1
        # Should have 'yes' for local and 'no' for remote
        assert "yes" in data_line[0]
        assert "no" in data_line[0]
