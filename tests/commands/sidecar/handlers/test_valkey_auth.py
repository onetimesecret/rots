# tests/commands/sidecar/handlers/test_valkey_auth.py

"""Unit tests for valkey bootstrap-auth wiring.

The auth path is tested separately from the main :mod:`test_valkey` suite
because it does not need a running valkey — only the credstore-reading
seam and the subprocess invocation shape. Keeping these tests out of the
podman-backed fixture lets them run in every environment, quickly.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

import rots.commands.sidecar.handlers.valkey as valkey_mod
from rots.commands.sidecar.handlers.valkey import (
    _BOOTSTRAP_USER,
    _CREDENTIAL_NAME,
    VALKEY_CLI,
    _load_bootstrap_auth,
    _run_valkey_cli,
)


class TestLoadBootstrapAuth:
    """Direct coverage of the credstore reader."""

    def test_returns_none_when_credentials_directory_unset(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("CREDENTIALS_DIRECTORY", raising=False)
        assert _load_bootstrap_auth() is None

    def test_returns_none_when_credentials_directory_empty_string(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CREDENTIALS_DIRECTORY", "")
        assert _load_bootstrap_auth() is None

    def test_returns_none_when_credential_file_missing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(tmp_path))
        # tmp_path exists but does not contain the credential file.
        assert _load_bootstrap_auth() is None

    def test_returns_none_when_credential_file_empty(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (tmp_path / _CREDENTIAL_NAME).write_text("", encoding="utf-8")
        monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(tmp_path))
        assert _load_bootstrap_auth() is None

    def test_returns_none_when_credential_file_only_whitespace(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (tmp_path / _CREDENTIAL_NAME).write_text("   \n\t\n", encoding="utf-8")
        monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(tmp_path))
        assert _load_bootstrap_auth() is None

    def test_returns_user_and_token_when_credential_file_present(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (tmp_path / _CREDENTIAL_NAME).write_text("s3cret-tok\n", encoding="utf-8")
        monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(tmp_path))
        assert _load_bootstrap_auth() == (_BOOTSTRAP_USER, "s3cret-tok")

    def test_strips_surrounding_whitespace_from_token(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (tmp_path / _CREDENTIAL_NAME).write_text("  tok-with-pad  \n", encoding="utf-8")
        monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(tmp_path))
        assert _load_bootstrap_auth() == (_BOOTSTRAP_USER, "tok-with-pad")

    def test_never_reads_acl_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """ACL_FILE is valkey-server's input, not this handler's — guard against regression."""
        monkeypatch.delenv("CREDENTIALS_DIRECTORY", raising=False)
        # Seed ACL_FILE with a plausible-looking bootstrap entry. If the
        # implementation ever falls back to parsing it, this test flips.
        fake_acl = tmp_path / "users.acl"
        fake_acl.write_text("user bootstrap on >should-not-be-read ~*\n", encoding="utf-8")
        monkeypatch.setattr(valkey_mod, "ACL_FILE", str(fake_acl))
        assert _load_bootstrap_auth() is None


class TestRunValkeyCliAuth:
    """Coverage of argv/env shape produced by ``_run_valkey_cli``."""

    @pytest.fixture
    def capture_run(self, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
        """Monkeypatch subprocess.run to capture args/kwargs and return a fake result."""
        captured: dict[str, Any] = {}

        def _fake_run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
            captured["args"] = args
            captured["kwargs"] = kwargs
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

        monkeypatch.setattr(valkey_mod.subprocess, "run", _fake_run)
        return captured

    def test_unauthenticated_when_credstore_absent(
        self, capture_run: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("CREDENTIALS_DIRECTORY", raising=False)
        _run_valkey_cli("PING")
        assert tuple(capture_run["args"]) == (*VALKEY_CLI, "PING")
        assert capture_run["kwargs"]["env"] is None

    def test_adds_user_argv_and_redis_auth_env_when_credstore_present(
        self,
        capture_run: dict[str, Any],
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        (tmp_path / _CREDENTIAL_NAME).write_text("tok-42\n", encoding="utf-8")
        monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(tmp_path))

        _run_valkey_cli("PING")

        args = list(capture_run["args"])
        kwargs = capture_run["kwargs"]
        # --user bootstrap appears, followed by the command tail.
        assert "--user" in args
        assert args[args.index("--user") + 1] == _BOOTSTRAP_USER
        assert args[-1] == "PING"
        # Token is in env, not argv. This is the whole point of using
        # REDISCLI_AUTH over -a: keep it out of /proc/<pid>/cmdline.
        assert "tok-42" not in args
        assert kwargs["env"] is not None
        assert kwargs["env"].get("REDISCLI_AUTH") == "tok-42"

    def test_subprocess_kwargs_preserve_check_capture_timeout(
        self, capture_run: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("CREDENTIALS_DIRECTORY", raising=False)
        _run_valkey_cli("PING")
        kwargs = capture_run["kwargs"]
        assert kwargs["check"] is True
        assert kwargs["capture_output"] is True
        assert kwargs["text"] is True
        assert isinstance(kwargs["timeout"], int)
