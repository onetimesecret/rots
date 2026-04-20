# tests/commands/host/test_rsync.py

"""Tests for rsync detection and command building."""

import logging
from pathlib import Path
from unittest.mock import patch

import pytest

from rots.commands.host._rsync import (
    RsyncInfo,
    build_rsync_file_cmd,
    detect_rsync,
    warn_if_macos_rsync,
)


class TestRsyncInfo:
    def test_supports_checksum_v3(self):
        info = RsyncInfo(
            path="/usr/bin/rsync",
            version="3.2.7",
            major=3,
            minor=2,
            is_openrsync=False,
        )
        assert info.supports_checksum is True

    def test_no_checksum_v2(self):
        info = RsyncInfo(
            path="/usr/bin/rsync",
            version="2.6.9",
            major=2,
            minor=6,
            is_openrsync=False,
        )
        assert info.supports_checksum is False

    def test_no_checksum_openrsync(self):
        info = RsyncInfo(
            path="/usr/bin/rsync",
            version="3.2.7",
            major=3,
            minor=2,
            is_openrsync=True,
        )
        assert info.supports_checksum is False


class TestDetectRsync:
    def test_rsync_path_env_override(self, monkeypatch, tmp_path):
        fake_rsync = tmp_path / "rsync"
        fake_rsync.write_text("#!/bin/sh\necho 'rsync  version 3.2.7  protocol version 31'")
        fake_rsync.chmod(0o755)
        monkeypatch.setenv("RSYNC_PATH", str(fake_rsync))

        with patch("subprocess.run") as mock_run:
            mock_run.return_value.stdout = "rsync  version 3.2.7  protocol version 31"
            mock_run.return_value.stderr = ""
            info = detect_rsync()
            assert info.path == str(fake_rsync)
            assert info.major == 3
            assert info.minor == 2

    def test_rsync_path_nonexistent(self, monkeypatch):
        monkeypatch.setenv("RSYNC_PATH", "/nonexistent/rsync")
        with pytest.raises(SystemExit, match="does not exist"):
            detect_rsync()

    def test_detects_openrsync(self, monkeypatch):
        monkeypatch.delenv("RSYNC_PATH", raising=False)
        with patch("shutil.which", return_value="/usr/bin/rsync"):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value.stdout = (
                    "openrsync: protocol version 20, rsync version 2.6.9 compat"
                )
                mock_run.return_value.stderr = ""
                info = detect_rsync()
                assert info.is_openrsync is True
                assert info.major == 2


class TestWarnIfMacosRsync:
    def test_warns_for_openrsync(self, caplog):
        info = RsyncInfo(
            path="/usr/bin/rsync",
            version="2.6.9",
            major=2,
            minor=6,
            is_openrsync=True,
        )
        with caplog.at_level(logging.WARNING):
            warn_if_macos_rsync(info)
        assert "unreliable" in caplog.text
        assert "RSYNC_PATH" in caplog.text

    def test_no_warn_for_v3(self, caplog):
        info = RsyncInfo(
            path="/opt/homebrew/bin/rsync",
            version="3.2.7",
            major=3,
            minor=2,
            is_openrsync=False,
        )
        with caplog.at_level(logging.WARNING):
            warn_if_macos_rsync(info)
        assert caplog.text == ""


class TestBuildRsyncFileCmd:
    def test_dry_run_with_checksum(self):
        info = RsyncInfo(
            path="/usr/bin/rsync",
            version="3.2.7",
            major=3,
            minor=2,
            is_openrsync=False,
        )
        cmd = build_rsync_file_cmd(
            rsync_info=info,
            local_file=Path("/local/config.yaml"),
            ssh_host="prod-us1",
            remote_path="/etc/onetimesecret/config.yaml",
            dry_run=True,
            backup=True,
        )
        assert cmd[0] == "/usr/bin/rsync"
        assert "-avz" in cmd
        assert "--checksum" in cmd
        assert "--dry-run" in cmd
        assert "--itemize-changes" in cmd
        assert "--backup" in cmd
        assert any(".bak" in s for s in cmd)
        assert cmd[-1] == "prod-us1:/etc/onetimesecret/config.yaml"

    def test_apply_without_checksum(self):
        info = RsyncInfo(
            path="/usr/bin/rsync",
            version="2.6.9",
            major=2,
            minor=6,
            is_openrsync=True,
        )
        cmd = build_rsync_file_cmd(
            rsync_info=info,
            local_file=Path("/local/.env"),
            ssh_host="prod-eu1",
            remote_path="/etc/default/onetimesecret",
            dry_run=False,
            backup=False,
        )
        assert "--checksum" not in cmd
        assert "--dry-run" not in cmd
        assert "--backup" not in cmd
