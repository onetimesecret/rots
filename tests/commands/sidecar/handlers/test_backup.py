# tests/commands/sidecar/handlers/test_backup.py

"""Tests for the ``backup.install`` and ``backup.uninstall`` handlers.

These handlers provision systemd ``.service`` + ``.timer`` units and rclone
config fragments on the db sidecar. They do NOT touch any real network
service — they write unit files and shell out to ``systemctl`` /
``systemd-analyze``. Per docs/testing.md and the task brief, real
``/etc/systemd/system`` must never be written to and the real systemd
daemon must never be reloaded.

Testing strategy:

* :data:`backup.SYSTEMD_DIR` and :data:`backup.RCLONE_FRAGMENTS_DIR` are
  monkeypatched to tmp_path subdirectories per test (see ``backup_dirs``
  fixture).
* ``subprocess.run`` is patched to a stateful dispatcher
  (``_FakeSystemdWorld``) that routes on argv tokens:
  ``systemd-analyze calendar``, ``systemctl daemon-reload``,
  ``systemctl enable --now``, ``systemctl disable --now``,
  ``systemctl is-enabled``, ``systemctl is-active``. The dispatcher is
  stateful so that ``enable --now`` flips a timer's recorded state, which
  subsequent ``is-enabled`` / ``is-active`` probes read back. This models
  the spec's "changed iff any file rewrote OR the timer was not already
  enabled+active" idempotency rule without tying tests to a particular
  call order.

Assertions deliberately avoid peeking at rendered unit-file text — the
spec treats ``_render_service`` / ``_render_timer`` as impl-agent
territory. Tests assert file existence, mode, mtime stability on
idempotent re-runs, and recorded subprocess calls.
"""

from __future__ import annotations

import os
import stat
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

import rots.commands.sidecar.handlers.backup as backup_mod
from rots.commands.sidecar.handlers.backup import (
    ALLOWED_PROFILES,
    handle_install,
    handle_uninstall,
)

pytestmark = pytest.mark.quick


# --- fake systemd / systemd-analyze world --------------------------------


# Schedules the fake `systemd-analyze calendar` treats as valid.
_VALID_SCHEDULES: frozenset[str] = frozenset(
    {
        "daily",
        "hourly",
        "weekly",
        "*-*-* 03:30:00",
        "Mon *-*-* 02:00:00",
        "*:0/15",
    }
)


@dataclass
class _Call:
    """One recorded invocation of the patched ``subprocess.run``."""

    argv: tuple[str, ...]
    kwargs: dict[str, Any] = field(default_factory=dict)


@dataclass
class _FakeSystemdWorld:
    """Stateful fake for ``subprocess.run``.

    Models just enough of systemd for the backup handler's idempotency
    contract:

    * A per-timer ``enabled_and_active`` flag.
    * ``systemctl enable --now <timer>`` flips it on.
    * ``systemctl disable --now <timer>`` flips it off, and returns
      non-zero if the timer was never enabled (spec says the handler
      catches+ignores that).
    * ``systemctl is-enabled`` / ``is-active`` probes read it back.
    * ``systemd-analyze calendar <expr>`` returns 0 iff ``expr`` is in
      :data:`_VALID_SCHEDULES`.
    * ``systemctl daemon-reload`` is always a no-op success.
    """

    calls: list[_Call] = field(default_factory=list)
    timer_enabled: dict[str, bool] = field(default_factory=dict)
    # When set, any call to daemon-reload raises CalledProcessError; used
    # by the "enable failure" scenario — not daemon-reload specifically,
    # but the same hook exists for enable.
    fail_on: set[str] = field(default_factory=set)

    # --- drive function for mocker.patch --------------------------------
    def run(self, argv, *args, **kwargs):
        # Normalise argv to a tuple of strings. subprocess.run accepts
        # either a list or a single string; the backup handler will use
        # a list of strings for systemctl/systemd-analyze.
        if isinstance(argv, list | tuple):
            argv_t = tuple(str(x) for x in argv)
        else:
            argv_t = (str(argv),)
        self.calls.append(_Call(argv=argv_t, kwargs=dict(kwargs)))

        exe = argv_t[0] if argv_t else ""

        # Match on the *suffix* of the executable so both "systemctl" and
        # "/usr/bin/systemctl" work.
        def _is(name: str) -> bool:
            return exe == name or exe.endswith("/" + name)

        if _is("systemd-analyze"):
            return self._handle_analyze(argv_t, kwargs)
        if _is("systemctl"):
            return self._handle_systemctl(argv_t, kwargs)

        # Any other subprocess.run call the handler might make is a
        # spec violation for this module — fail loudly.
        raise AssertionError(f"Unexpected subprocess.run call: {argv_t!r}")

    # --- systemd-analyze ------------------------------------------------
    def _handle_analyze(self, argv: tuple[str, ...], kwargs: dict[str, Any]):
        # Expected shape: ("systemd-analyze", "calendar", "<expr>")
        # Be lenient about additional flags — grab the last positional arg
        # as the schedule expression.
        if "calendar" not in argv:
            return self._completed(argv, kwargs, 0, "", "")
        schedule = argv[-1]
        if schedule in _VALID_SCHEDULES:
            return self._completed(argv, kwargs, 0, f"Normalized form: {schedule}\n", "")
        return self._completed(
            argv,
            kwargs,
            1,
            "",
            f"Failed to parse calendar specification '{schedule}': Invalid value\n",
        )

    # --- systemctl ------------------------------------------------------
    def _handle_systemctl(self, argv: tuple[str, ...], kwargs: dict[str, Any]):
        # Extract the verb + any unit argument. systemctl argv tends to
        # look like ("systemctl", "enable", "--now", "ots-backup-x.timer")
        # or ("systemctl", "is-enabled", "ots-backup-x.timer") etc.
        verb = argv[1] if len(argv) > 1 else ""
        # Locate the unit argument (first token that ends with a known
        # unit suffix).
        unit = next(
            (a for a in argv[2:] if a.endswith((".timer", ".service"))),
            None,
        )

        if verb == "daemon-reload":
            return self._completed(argv, kwargs, 0, "", "")

        if verb == "enable":
            # "enable --now <timer>"
            assert unit is not None, f"enable missing unit arg: {argv!r}"
            if "enable" in self.fail_on:
                return self._completed(argv, kwargs, 1, "", "enable failed\n")
            self.timer_enabled[unit] = True
            return self._completed(argv, kwargs, 0, "", "")

        if verb == "disable":
            assert unit is not None, f"disable missing unit arg: {argv!r}"
            was_enabled = self.timer_enabled.pop(unit, False)
            if not was_enabled:
                # Mirrors the real systemctl behaviour when a unit is
                # not loaded: non-zero exit, message on stderr. The
                # spec says the handler catches and ignores this.
                return self._completed(
                    argv,
                    kwargs,
                    1,
                    "",
                    f"Failed to disable unit: Unit {unit} not loaded.\n",
                )
            return self._completed(argv, kwargs, 0, "", "")

        if verb == "is-enabled":
            assert unit is not None
            enabled = self.timer_enabled.get(unit, False)
            return self._completed(
                argv,
                kwargs,
                0 if enabled else 1,
                "enabled\n" if enabled else "disabled\n",
                "",
            )

        if verb == "is-active":
            assert unit is not None
            active = self.timer_enabled.get(unit, False)
            return self._completed(
                argv,
                kwargs,
                0 if active else 3,
                "active\n" if active else "inactive\n",
                "",
            )

        # Unknown systemctl verb used by the handler — test will fail.
        raise AssertionError(f"Unexpected systemctl verb: {argv!r}")

    # --- CompletedProcess helper --------------------------------------
    def _completed(
        self,
        argv: tuple[str, ...],
        kwargs: dict[str, Any],
        returncode: int,
        stdout: str,
        stderr: str,
    ) -> subprocess.CompletedProcess:
        # Respect the check= kwarg like real subprocess.run.
        check = bool(kwargs.get("check", False))
        if check and returncode != 0:
            raise subprocess.CalledProcessError(
                returncode,
                list(argv),
                output=stdout,
                stderr=stderr,
            )
        # Respect capture_output / text kwargs superficially — return
        # str stdout/stderr which is what both text=True and
        # universal_newlines=True handlers expect.
        return subprocess.CompletedProcess(
            args=list(argv),
            returncode=returncode,
            stdout=stdout,
            stderr=stderr,
        )

    # --- convenience lookups for assertions -----------------------------
    def argv_list(self) -> list[tuple[str, ...]]:
        return [c.argv for c in self.calls]

    def verbs(self) -> list[str]:
        """Return a flat list of (tool, action) strings for quick checks.

        Example: ``["systemd-analyze calendar", "systemctl daemon-reload",
        "systemctl enable --now"]``.
        """
        out: list[str] = []
        for argv in self.argv_list():
            if not argv:
                continue
            exe = argv[0].rsplit("/", 1)[-1]
            if exe == "systemd-analyze":
                out.append(f"systemd-analyze {argv[1]}" if len(argv) > 1 else "systemd-analyze")
            elif exe == "systemctl":
                verb = argv[1] if len(argv) > 1 else ""
                if verb in {"enable", "disable"} and "--now" in argv:
                    out.append(f"systemctl {verb} --now")
                else:
                    out.append(f"systemctl {verb}")
            else:
                out.append(" ".join(argv))
        return out

    def daemon_reload_count(self) -> int:
        return sum(1 for v in self.verbs() if v == "systemctl daemon-reload")

    def enable_now_count(self) -> int:
        return sum(1 for v in self.verbs() if v == "systemctl enable --now")

    def disable_now_count(self) -> int:
        return sum(1 for v in self.verbs() if v == "systemctl disable --now")


# --- fixtures -------------------------------------------------------------


@pytest.fixture
def systemd_dir(tmp_path: Path) -> Path:
    d = tmp_path / "etc" / "systemd" / "system"
    d.mkdir(parents=True, exist_ok=True)
    return d


@pytest.fixture
def rclone_dir(tmp_path: Path) -> Path:
    d = tmp_path / "etc" / "rclone" / "conf.d"
    d.mkdir(parents=True, exist_ok=True)
    return d


@pytest.fixture
def backup_dirs(
    systemd_dir: Path,
    rclone_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Path, Path]:
    """Redirect the module constants to tmp_path subdirectories.

    Returns ``(systemd_dir, rclone_dir)``.
    """
    monkeypatch.setattr(backup_mod, "SYSTEMD_DIR", str(systemd_dir))
    monkeypatch.setattr(backup_mod, "RCLONE_FRAGMENTS_DIR", str(rclone_dir))
    return systemd_dir, rclone_dir


@pytest.fixture
def fake_world(monkeypatch: pytest.MonkeyPatch) -> _FakeSystemdWorld:
    """Patch ``subprocess.run`` with the stateful systemd fake.

    Patches at two import sites:

    * ``subprocess.run`` globally — works if impl does
      ``import subprocess; subprocess.run(...)``.
    * ``rots.commands.sidecar.handlers.backup.subprocess.run`` — works
      for the same usage but is a no-op if the impl used
      ``from subprocess import run`` (in which case the global patch
      above is what actually intercepts).

    Either style the impl picks is covered.
    """
    world = _FakeSystemdWorld()
    monkeypatch.setattr(subprocess, "run", world.run)
    # Module-local patch (best-effort; only applies if handler imported
    # subprocess as a module). If the attribute doesn't exist on the
    # handler module we let the global patch do the work.
    if hasattr(backup_mod, "subprocess"):
        monkeypatch.setattr(backup_mod.subprocess, "run", world.run)
    return world


# --- helpers -------------------------------------------------------------


def _unit_names(profile: str) -> tuple[str, str, str]:
    """Return ``(service_name, timer_name, rclone_fragment_name)``."""
    service = f"ots-backup-{profile}.service"
    timer = f"ots-backup-{profile}.timer"
    fragment = f"{profile}.conf"
    return service, timer, fragment


def _profile_files(systemd_dir: Path, rclone_dir: Path, profile: str) -> tuple[Path, Path, Path]:
    service, timer, fragment = _unit_names(profile)
    return (
        systemd_dir / service,
        systemd_dir / timer,
        rclone_dir / fragment,
    )


def _valid_params(**overrides: Any) -> dict[str, Any]:
    """Baseline params that should pass validation."""
    base: dict[str, Any] = {
        "profile": "db-daily",
        "target": "remote1:backups/db",
        "schedule": "daily",
    }
    base.update(overrides)
    return base


# --- happy path: install -------------------------------------------------


class TestInstallHappyPath:
    """First-run install creates three files, reloads, enables."""

    def test_returns_success_with_expected_data(
        self,
        backup_dirs: tuple[Path, Path],
        fake_world: _FakeSystemdWorld,
    ):
        result = handle_install(_valid_params())
        assert result.success is True, result.error
        assert result.data is not None
        assert result.data["unit"] == "ots-backup-db-daily.service"
        assert result.data["timer"] == "ots-backup-db-daily.timer"
        assert result.data["changed"] is True

    def test_writes_three_files(
        self,
        backup_dirs: tuple[Path, Path],
        fake_world: _FakeSystemdWorld,
    ):
        systemd_dir, rclone_dir = backup_dirs
        service, timer, fragment = _profile_files(systemd_dir, rclone_dir, "db-daily")

        handle_install(_valid_params())

        assert service.exists()
        assert timer.exists()
        assert fragment.exists()

    def test_file_modes(
        self,
        backup_dirs: tuple[Path, Path],
        fake_world: _FakeSystemdWorld,
    ):
        systemd_dir, rclone_dir = backup_dirs
        service, timer, fragment = _profile_files(systemd_dir, rclone_dir, "db-daily")

        handle_install(_valid_params())

        # Unit files are 0644 (readable by systemd).
        assert stat.S_IMODE(os.stat(service).st_mode) == 0o644
        assert stat.S_IMODE(os.stat(timer).st_mode) == 0o644
        # rclone fragment is 0640 — it carries credentials.
        assert stat.S_IMODE(os.stat(fragment).st_mode) == 0o640

    def test_calls_daemon_reload_and_enable(
        self,
        backup_dirs: tuple[Path, Path],
        fake_world: _FakeSystemdWorld,
    ):
        handle_install(_valid_params())
        assert fake_world.daemon_reload_count() == 1
        assert fake_world.enable_now_count() == 1

    def test_enable_targets_timer_not_service(
        self,
        backup_dirs: tuple[Path, Path],
        fake_world: _FakeSystemdWorld,
    ):
        handle_install(_valid_params())
        # Spec: `systemctl enable --now ots-backup-<profile>.timer`.
        enable_calls = [
            c.argv
            for c in fake_world.calls
            if len(c.argv) >= 2 and c.argv[0].endswith("systemctl") and c.argv[1] == "enable"
        ]
        assert len(enable_calls) == 1
        argv = enable_calls[0]
        assert "ots-backup-db-daily.timer" in argv
        assert "ots-backup-db-daily.service" not in argv

    def test_accepts_valkey_hourly_profile(
        self,
        backup_dirs: tuple[Path, Path],
        fake_world: _FakeSystemdWorld,
    ):
        systemd_dir, rclone_dir = backup_dirs
        service, timer, fragment = _profile_files(systemd_dir, rclone_dir, "valkey-hourly")

        result = handle_install(_valid_params(profile="valkey-hourly", schedule="hourly"))

        assert result.success is True, result.error
        assert service.exists()
        assert timer.exists()
        assert fragment.exists()


# --- idempotency ---------------------------------------------------------


class TestInstallIdempotency:
    """Re-running with identical inputs must not mutate state."""

    def test_rerun_reports_changed_false(
        self,
        backup_dirs: tuple[Path, Path],
        fake_world: _FakeSystemdWorld,
    ):
        first = handle_install(_valid_params())
        assert first.data["changed"] is True

        second = handle_install(_valid_params())
        assert second.success is True, second.error
        assert second.data["changed"] is False

    def test_rerun_does_not_rewrite_files(
        self,
        backup_dirs: tuple[Path, Path],
        fake_world: _FakeSystemdWorld,
    ):
        systemd_dir, rclone_dir = backup_dirs
        service, timer, fragment = _profile_files(systemd_dir, rclone_dir, "db-daily")

        handle_install(_valid_params())
        # Snapshot mtimes after the first call.
        service_mtime = os.stat(service).st_mtime_ns
        timer_mtime = os.stat(timer).st_mtime_ns
        fragment_mtime = os.stat(fragment).st_mtime_ns

        handle_install(_valid_params())

        # No file should have been rewritten.
        assert os.stat(service).st_mtime_ns == service_mtime
        assert os.stat(timer).st_mtime_ns == timer_mtime
        assert os.stat(fragment).st_mtime_ns == fragment_mtime

    def test_rerun_skips_daemon_reload(
        self,
        backup_dirs: tuple[Path, Path],
        fake_world: _FakeSystemdWorld,
    ):
        handle_install(_valid_params())
        # Snapshot call counts after the first install.
        reloads_after_first = fake_world.daemon_reload_count()
        assert reloads_after_first == 1

        handle_install(_valid_params())

        # Second run: no file changed, so daemon-reload must NOT run
        # again. Spec: "If any file changed, systemctl daemon-reload".
        assert fake_world.daemon_reload_count() == reloads_after_first


# --- config change (schedule only) --------------------------------------


class TestInstallConfigChange:
    """Changing only the schedule rewrites only the timer."""

    def test_schedule_change_reports_changed_true(
        self,
        backup_dirs: tuple[Path, Path],
        fake_world: _FakeSystemdWorld,
    ):
        handle_install(_valid_params(schedule="daily"))
        result = handle_install(_valid_params(schedule="hourly"))
        assert result.success is True, result.error
        assert result.data["changed"] is True

    def test_schedule_change_rewrites_only_timer(
        self,
        backup_dirs: tuple[Path, Path],
        fake_world: _FakeSystemdWorld,
    ):
        systemd_dir, rclone_dir = backup_dirs
        service, timer, fragment = _profile_files(systemd_dir, rclone_dir, "db-daily")

        handle_install(_valid_params(schedule="daily"))
        service_mtime = os.stat(service).st_mtime_ns
        timer_mtime = os.stat(timer).st_mtime_ns
        fragment_mtime = os.stat(fragment).st_mtime_ns

        handle_install(_valid_params(schedule="hourly"))

        # Service and rclone fragment are inputs-equivalent; their
        # mtimes must not advance.
        assert os.stat(service).st_mtime_ns == service_mtime
        assert os.stat(fragment).st_mtime_ns == fragment_mtime
        # Timer content is schedule-dependent; its mtime must advance.
        assert os.stat(timer).st_mtime_ns != timer_mtime

    def test_schedule_change_calls_daemon_reload(
        self,
        backup_dirs: tuple[Path, Path],
        fake_world: _FakeSystemdWorld,
    ):
        handle_install(_valid_params(schedule="daily"))
        reloads_before = fake_world.daemon_reload_count()

        handle_install(_valid_params(schedule="hourly"))

        # At least one more daemon-reload — the timer file changed.
        assert fake_world.daemon_reload_count() == reloads_before + 1


# --- validation failures ------------------------------------------------


class TestInstallValidation:
    """Validation failures fail early, before any filesystem or subprocess work."""

    def test_unknown_profile_fails(
        self,
        backup_dirs: tuple[Path, Path],
        fake_world: _FakeSystemdWorld,
    ):
        systemd_dir, rclone_dir = backup_dirs
        result = handle_install(_valid_params(profile="nonexistent"))
        assert result.success is False
        assert "nonexistent" in (result.error or "") or "profile" in (result.error or "").lower()

        # No subprocess call — validation rejects before any shell-out.
        assert fake_world.calls == []
        # No files written either.
        assert list(systemd_dir.iterdir()) == []
        assert list(rclone_dir.iterdir()) == []

    def test_missing_profile_fails(
        self,
        backup_dirs: tuple[Path, Path],
        fake_world: _FakeSystemdWorld,
    ):
        params = _valid_params()
        params.pop("profile")
        result = handle_install(params)
        assert result.success is False
        assert fake_world.calls == []

    def test_profile_not_in_allowlist_even_if_spelled_close(
        self,
        backup_dirs: tuple[Path, Path],
        fake_world: _FakeSystemdWorld,
    ):
        # Typo profiles (close enough to fool a dumb substring check)
        # must still fail.
        result = handle_install(_valid_params(profile="db_daily"))
        assert result.success is False
        assert fake_world.calls == []

    def test_invalid_schedule_rejected_by_analyze(
        self,
        backup_dirs: tuple[Path, Path],
        fake_world: _FakeSystemdWorld,
    ):
        systemd_dir, rclone_dir = backup_dirs
        # "never" is not in _VALID_SCHEDULES so the fake
        # systemd-analyze returns non-zero.
        result = handle_install(_valid_params(schedule="never"))
        assert result.success is False

        # systemd-analyze calendar may have been invoked to validate;
        # no enable / daemon-reload after that rejection.
        assert fake_world.enable_now_count() == 0
        assert fake_world.daemon_reload_count() == 0

        # No files written.
        assert list(systemd_dir.iterdir()) == []
        assert list(rclone_dir.iterdir()) == []

    def test_empty_schedule_rejected(
        self,
        backup_dirs: tuple[Path, Path],
        fake_world: _FakeSystemdWorld,
    ):
        result = handle_install(_valid_params(schedule=""))
        assert result.success is False

    def test_whitespace_only_schedule_rejected(
        self,
        backup_dirs: tuple[Path, Path],
        fake_world: _FakeSystemdWorld,
    ):
        result = handle_install(_valid_params(schedule="   "))
        assert result.success is False

    def test_empty_target_rejected(
        self,
        backup_dirs: tuple[Path, Path],
        fake_world: _FakeSystemdWorld,
    ):
        systemd_dir, rclone_dir = backup_dirs
        result = handle_install(_valid_params(target=""))
        assert result.success is False
        assert list(systemd_dir.iterdir()) == []
        assert list(rclone_dir.iterdir()) == []

    def test_target_with_newline_rejected(
        self,
        backup_dirs: tuple[Path, Path],
        fake_world: _FakeSystemdWorld,
    ):
        # Newline in remote name (colon-style). Spec regex
        # ^[A-Za-z0-9_-]+$ excludes \n.
        systemd_dir, rclone_dir = backup_dirs
        result = handle_install(_valid_params(target="rem\note:path"))
        assert result.success is False
        assert list(systemd_dir.iterdir()) == []
        assert list(rclone_dir.iterdir()) == []

    def test_target_with_invalid_remote_chars_rejected(
        self,
        backup_dirs: tuple[Path, Path],
        fake_world: _FakeSystemdWorld,
    ):
        # Spaces not allowed in colon-style remote name.
        result = handle_install(_valid_params(target="bad remote:path"))
        assert result.success is False

    def test_missing_target_rejected(
        self,
        backup_dirs: tuple[Path, Path],
        fake_world: _FakeSystemdWorld,
    ):
        params = _valid_params()
        params.pop("target")
        result = handle_install(params)
        assert result.success is False
        assert fake_world.calls == []


# --- OS errors during atomic write --------------------------------------


class TestInstallWriteFailures:
    """Write failures surface as fail() and leave no partial tempfiles."""

    def test_os_rename_failure_returns_fail(
        self,
        backup_dirs: tuple[Path, Path],
        fake_world: _FakeSystemdWorld,
        monkeypatch: pytest.MonkeyPatch,
    ):
        def boom(src: Any, dst: Any, *args: Any, **kwargs: Any) -> None:
            raise OSError("disk full")

        monkeypatch.setattr(os, "rename", boom)

        result = handle_install(_valid_params())
        assert result.success is False

    def test_os_rename_failure_leaves_no_tempfiles(
        self,
        backup_dirs: tuple[Path, Path],
        fake_world: _FakeSystemdWorld,
        monkeypatch: pytest.MonkeyPatch,
    ):
        systemd_dir, rclone_dir = backup_dirs

        def boom(src: Any, dst: Any, *args: Any, **kwargs: Any) -> None:
            raise OSError("disk full")

        monkeypatch.setattr(os, "rename", boom)
        handle_install(_valid_params())

        # Verify no leftover files in either directory. tempfile.NamedTemporaryFile
        # with delete=False would be cleaned up by the handler on rename failure;
        # any other half-open state is a bug.
        systemd_leftovers = list(systemd_dir.iterdir())
        rclone_leftovers = list(rclone_dir.iterdir())
        # The final unit/fragment file MUST NOT exist (rename never succeeded).
        service, timer, fragment = _profile_files(systemd_dir, rclone_dir, "db-daily")
        assert not service.exists()
        assert not timer.exists()
        assert not fragment.exists()
        # No stray hidden files either.
        assert all(not p.name.startswith(".") for p in systemd_leftovers), systemd_leftovers
        assert all(not p.name.startswith(".") for p in rclone_leftovers), rclone_leftovers


# --- systemctl enable failure --------------------------------------------


class TestInstallEnableFailure:
    """A non-zero systemctl enable returns fail(); spec says no auto-rollback."""

    def test_enable_failure_returns_fail(
        self,
        backup_dirs: tuple[Path, Path],
        fake_world: _FakeSystemdWorld,
    ):
        fake_world.fail_on.add("enable")
        result = handle_install(_valid_params())
        assert result.success is False

    def test_enable_failure_does_not_rollback_files(
        self,
        backup_dirs: tuple[Path, Path],
        fake_world: _FakeSystemdWorld,
    ):
        systemd_dir, rclone_dir = backup_dirs
        service, timer, fragment = _profile_files(systemd_dir, rclone_dir, "db-daily")

        fake_world.fail_on.add("enable")
        handle_install(_valid_params())

        # Per spec: "handler MUST NOT attempt automatic rollback".
        # The unit files were written before the failed enable call
        # and should remain on disk.
        assert service.exists()
        assert timer.exists()
        assert fragment.exists()


# --- uninstall -----------------------------------------------------------


class TestUninstallHappyPath:
    """Uninstalling a previously-installed profile cleans everything up."""

    def test_removes_files_after_install(
        self,
        backup_dirs: tuple[Path, Path],
        fake_world: _FakeSystemdWorld,
    ):
        systemd_dir, rclone_dir = backup_dirs
        service, timer, fragment = _profile_files(systemd_dir, rclone_dir, "db-daily")

        install = handle_install(_valid_params())
        assert install.success is True, install.error
        assert service.exists()
        assert timer.exists()
        assert fragment.exists()

        result = handle_uninstall({"profile": "db-daily"})
        assert result.success is True, result.error
        assert result.data is not None
        assert result.data["ok"] is True
        assert result.data["changed"] is True

        assert not service.exists()
        assert not timer.exists()
        assert not fragment.exists()

    def test_calls_disable_and_daemon_reload(
        self,
        backup_dirs: tuple[Path, Path],
        fake_world: _FakeSystemdWorld,
    ):
        handle_install(_valid_params())
        reloads_after_install = fake_world.daemon_reload_count()

        handle_uninstall({"profile": "db-daily"})

        assert fake_world.disable_now_count() == 1
        # Spec: "If any file actually existed before step 3, run
        # systemctl daemon-reload". An installed profile definitely
        # had files to remove, so one more reload.
        assert fake_world.daemon_reload_count() == reloads_after_install + 1

    def test_disable_targets_timer(
        self,
        backup_dirs: tuple[Path, Path],
        fake_world: _FakeSystemdWorld,
    ):
        handle_install(_valid_params())
        handle_uninstall({"profile": "db-daily"})

        disable_calls = [
            c.argv
            for c in fake_world.calls
            if len(c.argv) >= 2 and c.argv[0].endswith("systemctl") and c.argv[1] == "disable"
        ]
        assert len(disable_calls) == 1
        assert "ots-backup-db-daily.timer" in disable_calls[0]


class TestUninstallNotInstalled:
    """Uninstalling a clean profile is a no-op that still runs `disable`."""

    def test_reports_changed_false_when_not_installed(
        self,
        backup_dirs: tuple[Path, Path],
        fake_world: _FakeSystemdWorld,
    ):
        result = handle_uninstall({"profile": "db-daily"})
        assert result.success is True, result.error
        assert result.data["changed"] is False
        assert result.data["ok"] is True

    def test_disable_still_invoked_when_not_installed(
        self,
        backup_dirs: tuple[Path, Path],
        fake_world: _FakeSystemdWorld,
    ):
        # Spec: "systemctl disable --now still runs (harmless) but
        # returns non-zero; catch and ignore."
        handle_uninstall({"profile": "db-daily"})
        assert fake_world.disable_now_count() == 1

    def test_no_daemon_reload_when_nothing_removed(
        self,
        backup_dirs: tuple[Path, Path],
        fake_world: _FakeSystemdWorld,
    ):
        handle_uninstall({"profile": "db-daily"})
        assert fake_world.daemon_reload_count() == 0

    def test_no_file_removals_attempted_when_nothing_exists(
        self,
        backup_dirs: tuple[Path, Path],
        fake_world: _FakeSystemdWorld,
    ):
        systemd_dir, rclone_dir = backup_dirs
        handle_uninstall({"profile": "db-daily"})
        # Directories still empty — nothing was created either.
        assert list(systemd_dir.iterdir()) == []
        assert list(rclone_dir.iterdir()) == []


class TestUninstallValidation:
    """Unknown profile fails before any systemctl call."""

    def test_unknown_profile_fails(
        self,
        backup_dirs: tuple[Path, Path],
        fake_world: _FakeSystemdWorld,
    ):
        result = handle_uninstall({"profile": "nonexistent"})
        assert result.success is False
        # No systemctl invocation — spec: "Unknown profile →
        # CommandResult.fail, no systemctl call."
        assert fake_world.calls == []

    def test_missing_profile_fails(
        self,
        backup_dirs: tuple[Path, Path],
        fake_world: _FakeSystemdWorld,
    ):
        result = handle_uninstall({})
        assert result.success is False
        assert fake_world.calls == []


class TestUninstallPartialState:
    """Partial leftover state (service present, timer absent) still cleans up."""

    def test_partial_state_is_removed_and_reported_as_changed(
        self,
        backup_dirs: tuple[Path, Path],
        fake_world: _FakeSystemdWorld,
    ):
        systemd_dir, rclone_dir = backup_dirs
        service, timer, fragment = _profile_files(systemd_dir, rclone_dir, "db-daily")

        # Simulate partial prior state: only the service file exists.
        service.write_text("[Service]\nExecStart=/bin/true\n", encoding="utf-8")
        os.chmod(service, 0o644)
        assert service.exists()
        assert not timer.exists()
        assert not fragment.exists()

        result = handle_uninstall({"profile": "db-daily"})
        assert result.success is True, result.error
        # At least one file existed → spec says changed=True and a
        # daemon-reload runs.
        assert result.data["changed"] is True
        assert not service.exists()
        assert fake_world.daemon_reload_count() == 1


# --- allowlist sanity ----------------------------------------------------


class TestAllowedProfilesExported:
    """The allowlist constant exposes exactly the expected profiles.

    Purely a guard against accidental widening of the allowlist — any
    new entry here implies new ``_render_*`` templates, so the test
    should be updated intentionally.
    """

    def test_allowlist_members(self):
        assert ALLOWED_PROFILES == frozenset({"db-daily", "valkey-hourly"})
