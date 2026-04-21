# tests/commands/sidecar/handlers/test_secrets.py

"""Tests for the ``secrets.deliver`` handler.

This file is the worked-example test suite downstream impl+test agents copy
when writing postgres/valkey/backup tests. Notice:

* Filesystem-touching tests use ``tmp_path``, **not** real paths, per
  ``docs/testing.md``.
* The allowlist constant is patched so the handler treats the tmp env file
  as the canonical target.
* Both direct-call and via-dispatch paths are exercised.
* The ``in_process_bus`` fixture shows how a cross-host RPC is modelled —
  a test publishes ``secrets.deliver`` at ``web-host`` and the real
  handler runs against tmp_path.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

import rots.commands.sidecar.handlers.secrets as secrets_mod
from rots.commands.sidecar.handlers.secrets import (
    ALLOWED_NAMES,
    handle_secrets_deliver,
)

pytestmark = pytest.mark.quick


# --- fixtures -------------------------------------------------------------


@pytest.fixture
def env_path(
    fake_env_file: tuple[Path, object],
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    """Point the allowlist at a tmp env file and return its path."""
    path, _seed = fake_env_file
    monkeypatch.setattr(secrets_mod, "ALLOWED_ENV_FILE", str(path))
    return path


# --- allowlist ------------------------------------------------------------


class TestAllowlist:
    """Allowlist gates must reject anything off-list before filesystem work."""

    def test_missing_name(self, env_path: Path):
        result = handle_secrets_deliver({"value": "v"})
        assert result.success is False
        assert "name" in (result.error or "")
        assert not env_path.exists()

    def test_empty_name(self, env_path: Path):
        result = handle_secrets_deliver({"name": "", "value": "v"})
        assert result.success is False
        assert not env_path.exists()

    def test_non_string_name(self, env_path: Path):
        result = handle_secrets_deliver({"name": 42, "value": "v"})
        assert result.success is False
        assert not env_path.exists()

    def test_rejects_unknown_name(self, env_path: Path):
        result = handle_secrets_deliver({"name": "ARBITRARY_KEY", "value": "v"})
        assert result.success is False
        assert "ARBITRARY_KEY" in (result.error or "")
        assert not env_path.exists()

    def test_missing_value(self, env_path: Path):
        result = handle_secrets_deliver({"name": "PG_PASSWORD"})
        assert result.success is False
        assert "value" in (result.error or "")

    def test_non_string_value(self, env_path: Path):
        result = handle_secrets_deliver({"name": "PG_PASSWORD", "value": 1234})
        assert result.success is False

    def test_rejects_non_allowlisted_env_file(self, env_path: Path, tmp_path: Path):
        other = tmp_path / "somewhere-else"
        result = handle_secrets_deliver(
            {"name": "PG_PASSWORD", "value": "v", "env_file": str(other)}
        )
        assert result.success is False
        assert not other.exists()
        assert not env_path.exists()

    @pytest.mark.parametrize("name", sorted(ALLOWED_NAMES))
    def test_accepts_allowlisted_names(self, env_path: Path, name: str):
        result = handle_secrets_deliver({"name": name, "value": "secret"})
        assert result.success is True, result.error
        assert result.data["changed"] is True
        assert result.data["written"] is True
        body = env_path.read_text().strip()
        # Bare or double-quoted both acceptable; value must be present.
        assert body in {f"{name}=secret", f'{name}="secret"'}


# --- write + mode --------------------------------------------------------


class TestWriteBehaviour:
    """Write path produces a file with the right content, mode, and shape."""

    def test_creates_file_with_content(self, env_path: Path):
        result = handle_secrets_deliver({"name": "PG_PASSWORD", "value": "abcdef123456"})
        assert result.success is True
        assert env_path.exists()
        body = env_path.read_text()
        # Value quoted because it contains no shell specials but uses digits;
        # in our formatter a bare-safe value stays bare. Accept both shapes.
        assert "PG_PASSWORD=" in body
        assert "abcdef123456" in body

    def test_file_mode_0640(self, env_path: Path):
        handle_secrets_deliver({"name": "PG_PASSWORD", "value": "v"})
        mode = stat.S_IMODE(os.stat(env_path).st_mode)
        assert mode == 0o640

    def test_reports_changed_true_on_create(self, env_path: Path):
        result = handle_secrets_deliver({"name": "PG_PASSWORD", "value": "v"})
        assert result.data == {
            "written": True,
            "path": str(env_path),
            "changed": True,
        }


# --- idempotency ---------------------------------------------------------


class TestIdempotency:
    """Second call with the same value must not mutate the file."""

    def test_second_call_same_value_is_noop(self, env_path: Path):
        first = handle_secrets_deliver({"name": "PG_PASSWORD", "value": "v"})
        assert first.data["changed"] is True
        mtime_after_first = os.stat(env_path).st_mtime_ns

        # Sleep isn't necessary — we assert mtime did not advance by
        # checking the exact ns value after the no-op.
        second = handle_secrets_deliver({"name": "PG_PASSWORD", "value": "v"})
        assert second.success is True
        assert second.data == {
            "written": False,
            "path": str(env_path),
            "changed": False,
        }
        assert os.stat(env_path).st_mtime_ns == mtime_after_first

    def test_changed_value_rewrites(self, env_path: Path):
        handle_secrets_deliver({"name": "PG_PASSWORD", "value": "old"})
        result = handle_secrets_deliver({"name": "PG_PASSWORD", "value": "new"})
        assert result.data["changed"] is True
        assert "new" in env_path.read_text()
        assert "old" not in env_path.read_text()


# --- preservation of unrelated keys --------------------------------------


class TestPreservation:
    """Other keys, comments, and ordering survive a write."""

    def test_preserves_unrelated_keys_and_ordering(
        self,
        env_path: Path,
        fake_env_file: tuple[Path, object],
    ):
        _, seed = fake_env_file
        original = "# Header comment\nFIRST=alpha\nSECOND=beta\n\n# Section\nTHIRD=gamma\n"
        seed(original)  # type: ignore[operator]

        handle_secrets_deliver({"name": "PG_PASSWORD", "value": "vv"})
        body = env_path.read_text()

        # All original lines survive with ordering.
        for expected in [
            "# Header comment",
            "FIRST=alpha",
            "SECOND=beta",
            "# Section",
            "THIRD=gamma",
        ]:
            assert expected in body

        # Appended at the end (file existed previously; PG_PASSWORD is new).
        lines = body.splitlines()
        assert lines[-1].startswith("PG_PASSWORD=")
        # The original THIRD=gamma must appear earlier than the appended line.
        assert lines.index("THIRD=gamma") < lines.index(lines[-1])

    def test_in_place_replacement_preserves_position(
        self,
        env_path: Path,
        fake_env_file: tuple[Path, object],
    ):
        _, seed = fake_env_file
        seed("FIRST=alpha\nPG_PASSWORD=old\nLAST=omega\n")  # type: ignore[operator]

        handle_secrets_deliver({"name": "PG_PASSWORD", "value": "brand_new"})
        lines = env_path.read_text().splitlines()
        assert lines[0] == "FIRST=alpha"
        assert lines[1].startswith("PG_PASSWORD=")
        assert "brand_new" in lines[1]
        assert lines[2] == "LAST=omega"


# --- symlink defence -----------------------------------------------------


class TestSymlinkRejection:
    """A symlinked target must be refused before any write."""

    def test_rejects_symlink_target(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        real = tmp_path / "real"
        real.write_text("FIRST=alpha\n")

        link = tmp_path / "link"
        os.symlink(real, link)

        monkeypatch.setattr(secrets_mod, "ALLOWED_ENV_FILE", str(link))

        result = handle_secrets_deliver(
            {"name": "PG_PASSWORD", "value": "v", "env_file": str(link)}
        )
        assert result.success is False
        assert "symbolic link" in (result.error or "").lower()

        # Real file is untouched.
        assert real.read_text() == "FIRST=alpha\n"


# --- atomic write --------------------------------------------------------


class TestAtomicity:
    """A crashed write must not leave a corrupt env file."""

    def test_os_rename_failure_leaves_original_intact(
        self,
        env_path: Path,
        fake_env_file: tuple[Path, object],
        monkeypatch: pytest.MonkeyPatch,
    ):
        _, seed = fake_env_file
        seed("PG_PASSWORD=old_value\n")  # type: ignore[operator]
        original_content = env_path.read_text()

        def boom(src: str, dst: str) -> None:
            raise OSError("disk full")

        monkeypatch.setattr(os, "rename", boom)

        result = handle_secrets_deliver({"name": "PG_PASSWORD", "value": "new_value"})
        assert result.success is False

        # Original file content preserved.
        assert env_path.read_text() == original_content

        # No leftover tempfile in the directory.
        leftovers = [p.name for p in env_path.parent.iterdir() if p.name.startswith(".")]
        assert leftovers == []


# --- logging (redaction) -------------------------------------------------


class TestLogging:
    """The secret value must never appear in logs."""

    def test_info_log_excludes_value(
        self,
        env_path: Path,
        caplog: pytest.LogCaptureFixture,
    ):
        caplog.set_level("DEBUG", logger="rots.commands.sidecar.handlers.secrets")

        secret = "super-secret-do-not-log-me"
        handle_secrets_deliver({"name": "PG_PASSWORD", "value": secret})

        for record in caplog.records:
            assert secret not in record.getMessage()
            assert secret not in str(record.args)


# --- dispatch integration -----------------------------------------------


class TestDispatchIntegration:
    """The handler is reachable through the central dispatcher by command name."""

    def test_dispatch_routes_secrets_deliver(
        self,
        env_path: Path,
    ):
        from rots.sidecar.commands import _import_handlers, dispatch

        _import_handlers()
        result = dispatch(
            "secrets.deliver",
            {"name": "PG_PASSWORD", "value": "from-dispatch"},
        )
        assert result.success is True
        assert result.data["changed"] is True
        assert "from-dispatch" in env_path.read_text()


# --- cross-host via in-process bus --------------------------------------


class TestInProcessBus:
    """A db-host publishing secrets.deliver at web-host runs the handler."""

    def test_publish_via_bus_writes_to_tmp(
        self,
        env_path: Path,
        in_process_bus,  # fixture from conftest
    ):
        result = in_process_bus.publish(
            "web-host",
            "secrets.deliver",
            {"name": "VALKEY_PASSWORD", "value": "routed-through-bus"},
            timeout=5.0,
        )
        assert result.success is True
        assert result.data["changed"] is True
        assert "routed-through-bus" in env_path.read_text()


# --- owner/group preservation across atomic rename ----------------------


class TestOwnerGroupPreservation:
    """After ``os.rename``, the handler must restore the pre-existing uid/gid.

    Rationale: the tempfile is created by the sidecar process (typically
    root). A naive rename would inherit root:root on the replaced file,
    dropping the ``onetimesecret`` group bit that the container user
    relies on via ``EnvironmentFile=``. The handler calls
    ``os.chown(path, lst.st_uid, lst.st_gid)`` after rename — this class
    proves that path is exercised and is best-effort.
    """

    def test_preserves_uid_gid_on_update(
        self,
        env_path: Path,
        fake_env_file: tuple[Path, object],
    ):
        # Seed a pre-existing file owned by the current uid/gid. We use
        # the process's own ids so no EPERM can occur on the later chown
        # call — non-root test runs can't chown to arbitrary ids. The
        # point isn't *which* ids are set, it's that the handler
        # re-applied whatever was there before.
        _, seed = fake_env_file
        seed("PG_PASSWORD=old_value\n")  # type: ignore[operator]
        pre_uid = os.stat(env_path).st_uid
        pre_gid = os.stat(env_path).st_gid

        result = handle_secrets_deliver({"name": "PG_PASSWORD", "value": "new_value"})
        assert result.success is True, result.error
        assert result.data["changed"] is True

        post = os.stat(env_path)
        assert post.st_uid == pre_uid
        assert post.st_gid == pre_gid

    def test_chown_called_with_original_uid_gid(
        self,
        env_path: Path,
        fake_env_file: tuple[Path, object],
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Record the chown call to prove the preserved values flow through.

        Patches ``os.chown`` at the secrets module's namespace (matches the
        ``os.rename`` patching pattern used for atomicity) and captures
        ``(path, uid, gid)``. This is the direct witness that the chown
        restoration runs with the pre-rename stat values, not whatever
        ``os.stat`` would return after rename.
        """
        _, seed = fake_env_file
        seed("PG_PASSWORD=old\n")  # type: ignore[operator]
        pre_uid = os.stat(env_path).st_uid
        pre_gid = os.stat(env_path).st_gid

        calls: list[tuple[str, int, int]] = []

        def recording_chown(path: str, uid: int, gid: int) -> None:
            calls.append((str(path), uid, gid))

        monkeypatch.setattr(secrets_mod.os, "chown", recording_chown)

        handle_secrets_deliver({"name": "PG_PASSWORD", "value": "brand_new"})

        assert len(calls) == 1
        recorded_path, recorded_uid, recorded_gid = calls[0]
        assert recorded_path == str(env_path)
        assert recorded_uid == pre_uid
        assert recorded_gid == pre_gid

    def test_fresh_write_skips_chown(
        self,
        env_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """No prior file → no chown call. The guard is ``if lst is not None``.

        Fresh-write already works (other tests exercise it); this test
        specifically proves the chown-preservation call is *not* made
        when there's no prior owner to copy. Failing to skip would mean
        chown'ing to whatever ``os.stat`` captured before, which on a
        missing file would raise.
        """
        # Sanity: env_path really does not exist yet.
        assert not env_path.exists()

        calls: list[tuple[str, int, int]] = []

        def recording_chown(path: str, uid: int, gid: int) -> None:
            calls.append((str(path), uid, gid))

        monkeypatch.setattr(secrets_mod.os, "chown", recording_chown)

        result = handle_secrets_deliver({"name": "PG_PASSWORD", "value": "v"})
        assert result.success is True, result.error
        assert env_path.exists()
        # Fresh file: no preservation call is made.
        assert calls == []

    def test_chown_permission_error_logged_not_fatal(
        self,
        env_path: Path,
        fake_env_file: tuple[Path, object],
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ):
        """A chown that raises PermissionError must not fail the handler.

        Best-effort semantics: the new file is already in place after
        ``os.rename`` — losing the chown step is a degraded state, not a
        corrupt one. Spec says log a warning and return success.
        """
        _, seed = fake_env_file
        seed("PG_PASSWORD=old\n")  # type: ignore[operator]

        def raising_chown(path: str, uid: int, gid: int) -> None:
            raise PermissionError("operation not permitted")

        monkeypatch.setattr(secrets_mod.os, "chown", raising_chown)
        caplog.set_level("WARNING", logger="rots.commands.sidecar.handlers.secrets")

        result = handle_secrets_deliver({"name": "PG_PASSWORD", "value": "new"})

        # Handler still succeeds — rename already landed.
        assert result.success is True, result.error
        assert result.data["changed"] is True
        # File content actually updated.
        assert "new" in env_path.read_text()

        # A WARNING-level record from the secrets logger was emitted.
        warnings = [
            r
            for r in caplog.records
            if r.name == "rots.commands.sidecar.handlers.secrets" and r.levelname == "WARNING"
        ]
        assert warnings, f"Expected a WARNING log, got: {caplog.records}"
