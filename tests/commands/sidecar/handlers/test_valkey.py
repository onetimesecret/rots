# tests/commands/sidecar/handlers/test_valkey.py

"""Tests for the ``valkey.*`` handlers.

Mirrors the shape of :mod:`test_secrets`: a class per concern, real
podman-backed valkey for state-mutating cases, :class:`InProcessRpcClient`
for cross-host delivery.

Redirecting ``valkey-cli`` at the test port
-------------------------------------------

Production invokes ``valkey-cli -h 127.0.0.1 -p 6379 ...`` against a
valkey bound inside the db sidecar. Our session-scoped fixture publishes
valkey on an ephemeral host port and the container image already ships
``valkey-cli``. Tests monkeypatch :data:`rots.commands.sidecar.handlers.valkey.VALKEY_CLI`
to ``("podman", "exec", <container>, "valkey-cli")`` so every subprocess
call runs inside the container against the loopback valkey. No host
``valkey-cli`` binary is required.

The spec reserves :data:`ACL_FILE` at ``/etc/valkey/users.acl`` for the
impl to read the bootstrap token out of. In the alpine image that path
does not exist, so we pre-seed a stub under ``tmp_path`` and monkeypatch
:data:`valkey.ACL_FILE` to point at it. The stub is written in the
standard ``user <name> on ><token> ...`` format so a line-based parser
can find a token even if it tries. When the tests talk to a no-auth
valkey the ``-a <token>`` argument (if the impl adds one) is ignored.

``aclfile`` must be pre-configured on the server
------------------------------------------------

The spec requires impl to call ``ACL SAVE`` / ``ACL LOAD``. Valkey 8.x
treats ``aclfile`` as immutable at runtime — ``CONFIG SET aclfile ...``
returns ``ERR CONFIG SET failed (redis-server might be configured to
refuse CONFIG SET for aclfile)``. The ``valkey_service`` session fixture
in :mod:`conftest` therefore starts the container with
``--aclfile /data/users.acl`` directly; tests neither need nor run a
``CONFIG SET aclfile`` step.

Auth handshake caveat
---------------------

If impl appends ``-a <token>`` to :data:`VALKEY_CLI`, the test container
currently has no ``requirepass`` and no user password, so ``valkey-cli``
may print ``WARNING: AUTH failed`` and still run the command (version-
dependent). If impl tests start failing with spurious AUTH errors, the
fix is to pass ``--requirepass`` on the ``valkey-server`` startup line
in the ``valkey_service`` conftest fixture and update
:func:`stub_acl_file` accordingly.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

import rots.commands.sidecar.handlers.secrets as secrets_mod
import rots.commands.sidecar.handlers.valkey as valkey_mod
from rots.commands.sidecar.handlers._transport import InProcessRpcClient
from rots.commands.sidecar.handlers.valkey import (
    handle_create_acl_user,
    handle_reload_acl,
)
from rots.sidecar.commands import CommandResult

pytestmark = [pytest.mark.integration, pytest.mark.slow]


# --- helpers --------------------------------------------------------------


def _run_cli(
    cli: tuple[str, ...], *args: str, check: bool = True
) -> subprocess.CompletedProcess[str]:
    """Invoke the redirected ``valkey-cli`` tuple and return CompletedProcess."""
    return subprocess.run(
        [*cli, *args],
        check=check,
        capture_output=True,
        text=True,
    )


def _acl_list(cli: tuple[str, ...]) -> list[str]:
    """Return ``ACL LIST`` output as a list of lines."""
    cp = _run_cli(cli, "ACL", "LIST")
    return [line for line in cp.stdout.splitlines() if line.strip()]


def _user_exists(cli: tuple[str, ...], name: str) -> bool:
    """True if ``ACL GETUSER <name>`` has output (Valkey returns empty on miss)."""
    cp = _run_cli(cli, "ACL", "GETUSER", name, check=False)
    return cp.returncode == 0 and bool(cp.stdout.strip())


def _acl_deluser(cli: tuple[str, ...], name: str) -> None:
    """Best-effort delete — swallow errors so teardown never masks a real failure."""
    _run_cli(cli, "ACL", "DELUSER", name, check=False)


# --- fixtures -------------------------------------------------------------


@pytest.fixture
def valkey_cli(valkey_service: dict[str, Any]) -> tuple[str, ...]:
    """``valkey-cli`` invocation targeting the test container via ``podman exec``.

    The spec says the real handler calls ``valkey-cli -h 127.0.0.1 -p 6379``
    against the sidecar's local valkey. In tests we route through
    ``podman exec`` because:

    * the image ships ``valkey-cli`` (host machine may not)
    * inside the container, loopback + default port is already correct
    * avoids any host firewall/port weirdness
    """
    if shutil.which("podman") is None:
        pytest.skip("podman not available on PATH")
    return ("podman", "exec", valkey_service["container"], "valkey-cli")


# The aclfile path inside the valkey container. ``/data`` is the default
# working dir for ``valkey:8-alpine`` and is writable by the valkey user.
# Kept in sync with the ``--aclfile`` startup flag in the ``valkey_service``
# conftest fixture.
_CONTAINER_ACL_FILE = "/data/users.acl"


@pytest.fixture
def patched_valkey_cli(
    valkey_cli: tuple[str, ...],
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[str, ...]:
    """Monkeypatch :data:`valkey.VALKEY_CLI` to the test-container invocation."""
    monkeypatch.setattr(valkey_mod, "VALKEY_CLI", valkey_cli)
    return valkey_cli


@pytest.fixture
def stub_acl_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Seed a fake ACL file under tmp and point :data:`valkey.ACL_FILE` at it.

    Required for impls that open :data:`ACL_FILE` per-call to pull the
    bootstrap user/token. The real file lives at ``/etc/valkey/users.acl``
    (root-owned, won't exist on dev machines). The stub format matches
    what ``ACL LIST`` writes and what ``ACL LOAD`` expects — useful for
    the reload-acl tests too.
    """
    acl_file = tmp_path / "users.acl"
    acl_file.write_text(
        # valkey's on-disk ACL format: one user per line.
        "user ots-bootstrap on >bootstraptok ~* &* +@all\nuser default on nopass ~* &* +@all\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(valkey_mod, "ACL_FILE", str(acl_file))
    return acl_file


@pytest.fixture
def env_path(
    fake_env_file: tuple[Path, Callable[[str], Path]],
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    """Redirect :data:`secrets.ALLOWED_ENV_FILE` at a tmp path and return it.

    Needed because ``secrets.deliver`` is the handler the bus dispatches
    to; its allowlist defaults to ``/etc/default/onetimesecret``.
    """
    path, _seed = fake_env_file
    monkeypatch.setattr(secrets_mod, "ALLOWED_ENV_FILE", str(path))
    return path


@pytest.fixture
def bus_routing(
    in_process_bus: InProcessRpcClient,
    monkeypatch: pytest.MonkeyPatch,
) -> InProcessRpcClient:
    """Wire :func:`valkey._get_rpc_client` to return the in-process bus."""
    monkeypatch.setattr(valkey_mod, "_get_rpc_client", lambda: in_process_bus)
    return in_process_bus


@pytest.fixture
def cleanup_acl(
    patched_valkey_cli: tuple[str, ...],
    valkey_acl_prefix: str,
) -> Iterator[Callable[[str], None]]:
    """Return a teardown-registering deleter for ACL usernames.

    Usage inside a test::

        cleanup_acl(f"{valkey_acl_prefix}_app")
        # later assertions ...

    Names collected here are deleted in the fixture finalizer. The
    session-scoped valkey service means we cannot rely on restart to
    drop state.
    """
    created: list[str] = []

    def register(name: str) -> None:
        created.append(name)

    yield register

    for name in created:
        _acl_deluser(patched_valkey_cli, name)


# --- handle_create_acl_user: happy path ----------------------------------


class TestCreateAclUserHappyPath:
    """First-time creation flows state + token through the bus."""

    def test_creates_user_delivers_token_returns_changed_true(
        self,
        patched_valkey_cli: tuple[str, ...],
        stub_acl_file: Path,
        bus_routing: InProcessRpcClient,
        env_path: Path,
        valkey_acl_prefix: str,
        cleanup_acl: Callable[[str], None],
    ):
        name = f"{valkey_acl_prefix}_app"
        cleanup_acl(name)

        result = handle_create_acl_user(
            {
                "name": name,
                "rules": ["on", "~*", "+@read"],
                "peer_id": "web-host",
            }
        )
        assert result.success is True, result.error
        assert result.data["changed"] is True
        assert result.data["delivered_to"] == "web-host"

        # Valkey knows about the user.
        assert _user_exists(patched_valkey_cli, name)

        # secrets.deliver landed in the env file.
        body = env_path.read_text()
        assert "VALKEY_PASSWORD=" in body

    def test_env_file_contains_token_value_not_empty(
        self,
        patched_valkey_cli: tuple[str, ...],
        stub_acl_file: Path,
        bus_routing: InProcessRpcClient,
        env_path: Path,
        valkey_acl_prefix: str,
        cleanup_acl: Callable[[str], None],
    ):
        name = f"{valkey_acl_prefix}_app"
        cleanup_acl(name)
        handle_create_acl_user(
            {"name": name, "rules": ["on", "~*", "+@read"], "peer_id": "web-host"}
        )
        # spec: secrets.token_urlsafe(40) -> >50 chars url-safe
        body = env_path.read_text()
        for line in body.splitlines():
            if line.startswith("VALKEY_PASSWORD="):
                value = line.split("=", 1)[1].strip().strip('"')
                assert len(value) >= 40
                break
        else:
            pytest.fail("VALKEY_PASSWORD not written to env file")


# --- handle_create_acl_user: idempotency ---------------------------------


class TestCreateAclUserIdempotency:
    """Second call with identical rules is a no-op; env + deliveries unchanged."""

    def test_same_rules_reports_unchanged_and_skips_delivery(
        self,
        patched_valkey_cli: tuple[str, ...],
        stub_acl_file: Path,
        in_process_bus: InProcessRpcClient,
        env_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        valkey_acl_prefix: str,
        cleanup_acl: Callable[[str], None],
    ):
        from rots.sidecar.commands import dispatch

        # Wrap web-host dispatch to count secrets.deliver invocations.
        deliver_calls: list[dict[str, Any]] = []

        def counting_dispatch(command: str, params: dict[str, Any]) -> CommandResult:
            if command == "secrets.deliver":
                deliver_calls.append(params)
            return dispatch(command, params)

        in_process_bus.register("web-host", counting_dispatch)
        monkeypatch.setattr(valkey_mod, "_get_rpc_client", lambda: in_process_bus)

        name = f"{valkey_acl_prefix}_app"
        cleanup_acl(name)
        params = {
            "name": name,
            "rules": ["on", "~*", "+@read"],
            "peer_id": "web-host",
        }

        first = handle_create_acl_user(params)
        assert first.success is True
        assert first.data["changed"] is True
        assert len(deliver_calls) == 1
        env_after_first = env_path.read_text()
        mtime_after_first = env_path.stat().st_mtime_ns

        second = handle_create_acl_user(params)
        assert second.success is True
        # spec: return early with changed=False and delivered_to=peer_id as echo
        assert second.data["changed"] is False
        assert second.data["delivered_to"] == "web-host"
        # No new token minted / delivered.
        assert len(deliver_calls) == 1
        # Env file content and mtime unchanged.
        assert env_path.read_text() == env_after_first
        assert env_path.stat().st_mtime_ns == mtime_after_first


# --- handle_create_acl_user: rule-set updates ----------------------------


class TestCreateAclUserRuleUpdate:
    """Changed rules rotate the token and deliver the new value."""

    def test_rules_change_triggers_new_token(
        self,
        patched_valkey_cli: tuple[str, ...],
        stub_acl_file: Path,
        in_process_bus: InProcessRpcClient,
        env_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        valkey_acl_prefix: str,
        cleanup_acl: Callable[[str], None],
    ):
        from rots.sidecar.commands import dispatch

        deliver_calls: list[dict[str, Any]] = []

        def counting_dispatch(command: str, params: dict[str, Any]) -> CommandResult:
            if command == "secrets.deliver":
                deliver_calls.append(params)
            return dispatch(command, params)

        in_process_bus.register("web-host", counting_dispatch)
        monkeypatch.setattr(valkey_mod, "_get_rpc_client", lambda: in_process_bus)

        name = f"{valkey_acl_prefix}_app"
        cleanup_acl(name)

        first = handle_create_acl_user(
            {"name": name, "rules": ["on", "~*", "+@read"], "peer_id": "web-host"}
        )
        assert first.data["changed"] is True
        token_a = deliver_calls[0]["value"]

        second = handle_create_acl_user(
            {"name": name, "rules": ["on", "~*", "+@read", "+@write"], "peer_id": "web-host"}
        )
        assert second.success is True
        assert second.data["changed"] is True
        assert len(deliver_calls) == 2
        token_b = deliver_calls[1]["value"]
        assert token_a != token_b
        # Env file now carries the new token.
        assert token_b in env_path.read_text()


# --- handle_create_acl_user: rule-order sensitivity ----------------------


class TestCreateAclUserRuleOrder:
    """Spec: impl MUST NOT sort/dedupe — different order is a different rule set."""

    def test_reordered_rules_treated_as_different(
        self,
        patched_valkey_cli: tuple[str, ...],
        stub_acl_file: Path,
        in_process_bus: InProcessRpcClient,
        env_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        valkey_acl_prefix: str,
        cleanup_acl: Callable[[str], None],
    ):
        from rots.sidecar.commands import dispatch

        deliver_calls: list[dict[str, Any]] = []

        def counting_dispatch(command: str, params: dict[str, Any]) -> CommandResult:
            if command == "secrets.deliver":
                deliver_calls.append(params)
            return dispatch(command, params)

        in_process_bus.register("web-host", counting_dispatch)
        monkeypatch.setattr(valkey_mod, "_get_rpc_client", lambda: in_process_bus)

        name = f"{valkey_acl_prefix}_app"
        cleanup_acl(name)

        first = handle_create_acl_user(
            {"name": name, "rules": ["on", "~*", "+get", "-@admin"], "peer_id": "web-host"}
        )
        assert first.data["changed"] is True

        # Same rule tokens, different order => must be detected as a change.
        second = handle_create_acl_user(
            {"name": name, "rules": ["on", "~*", "-@admin", "+get"], "peer_id": "web-host"}
        )
        assert second.success is True
        assert second.data["changed"] is True
        assert len(deliver_calls) == 2


# --- handle_create_acl_user: validation ---------------------------------


class TestCreateAclUserNameValidation:
    """Bad names are rejected before any valkey-cli call is issued."""

    @pytest.mark.parametrize(
        "bad_name",
        [
            "default",  # reserved by valkey
            "",  # empty
            "^bad",  # leading punctuation — violates ^[A-Za-z_]
            "has space",  # whitespace inside
            " leading",
            "trailing ",
            "1starts_with_digit",
        ],
    )
    def test_rejects_invalid_name(
        self,
        patched_valkey_cli: tuple[str, ...],
        stub_acl_file: Path,
        bus_routing: InProcessRpcClient,
        env_path: Path,
        bad_name: str,
    ):
        pre_users = set(_acl_list(patched_valkey_cli))

        result = handle_create_acl_user(
            {
                "name": bad_name,
                "rules": ["on", "~*", "+@read"],
                "peer_id": "web-host",
            }
        )
        assert result.success is False
        # No state mutation.
        post_users = set(_acl_list(patched_valkey_cli))
        assert pre_users == post_users
        # No secret delivered.
        assert not env_path.exists() or "VALKEY_PASSWORD" not in env_path.read_text()


class TestCreateAclUserRuleValidation:
    """Bad rules are rejected before any valkey-cli call is issued."""

    @pytest.mark.parametrize(
        "bad_rules",
        [
            [""],  # empty rule
            ["+@read", ""],  # mixed-in empty rule
            ["+has space"],  # whitespace inside a single rule
            ["on ~*"],  # two tokens smooshed into one string
            "not-a-list",  # not a list at all (string)
        ],
    )
    def test_rejects_invalid_rules(
        self,
        patched_valkey_cli: tuple[str, ...],
        stub_acl_file: Path,
        bus_routing: InProcessRpcClient,
        env_path: Path,
        valkey_acl_prefix: str,
        bad_rules: Any,
    ):
        name = f"{valkey_acl_prefix}_app"
        result = handle_create_acl_user({"name": name, "rules": bad_rules, "peer_id": "web-host"})
        assert result.success is False
        assert not _user_exists(patched_valkey_cli, name)
        assert not env_path.exists() or "VALKEY_PASSWORD" not in env_path.read_text()


# --- handle_create_acl_user: delivery failure / rollback ----------------


class TestCreateAclUserDeliveryFailure:
    """A failed secrets.deliver must roll back the ACL user."""

    def test_failing_dispatcher_triggers_rollback(
        self,
        patched_valkey_cli: tuple[str, ...],
        stub_acl_file: Path,
        in_process_bus: InProcessRpcClient,
        env_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        valkey_acl_prefix: str,
        cleanup_acl: Callable[[str], None],
    ):
        def failing_dispatch(command: str, params: dict[str, Any]) -> CommandResult:
            return CommandResult.fail("simulated delivery failure")

        in_process_bus.register("web-host", failing_dispatch)
        monkeypatch.setattr(valkey_mod, "_get_rpc_client", lambda: in_process_bus)

        name = f"{valkey_acl_prefix}_app"
        cleanup_acl(name)  # safety net

        result = handle_create_acl_user(
            {"name": name, "rules": ["on", "~*", "+@read"], "peer_id": "web-host"}
        )
        assert result.success is False
        # Rollback via ACL DELUSER must have removed the user.
        assert not _user_exists(patched_valkey_cli, name)

    def test_timeout_triggers_rollback(
        self,
        patched_valkey_cli: tuple[str, ...],
        stub_acl_file: Path,
        in_process_bus: InProcessRpcClient,
        monkeypatch: pytest.MonkeyPatch,
        valkey_acl_prefix: str,
        cleanup_acl: Callable[[str], None],
    ):
        """Spec: TimeoutError from RpcClient -> ACL DELUSER rollback + fail."""

        def timeout_dispatch(command: str, params: dict[str, Any]) -> CommandResult:
            raise TimeoutError("simulated rpc timeout")

        in_process_bus.register("web-host", timeout_dispatch)
        monkeypatch.setattr(valkey_mod, "_get_rpc_client", lambda: in_process_bus)

        name = f"{valkey_acl_prefix}_app"
        cleanup_acl(name)

        result = handle_create_acl_user(
            {"name": name, "rules": ["on", "~*", "+@read"], "peer_id": "web-host"}
        )
        assert result.success is False
        assert not _user_exists(patched_valkey_cli, name)


# --- handle_create_acl_user: ACL SAVE failure ---------------------------


class TestCreateAclUserAclSaveFailure:
    """Best-effort coverage: flagged as skip per task guidance."""

    @pytest.mark.skip(
        reason=(
            "Coaxing ACL SAVE into failing requires a read-only ACL mount or "
            "valkey config tweak the podman fixture does not expose. Task "
            "explicitly permits skipping this case; see spec."
        )
    )
    def test_acl_save_failure_surfaces(self):
        pass


# --- handle_reload_acl ---------------------------------------------------


class TestReloadAclHappyPath:
    """External edit of the ACL file flips changed=True on the next reload."""

    def test_reload_after_external_edit(
        self,
        valkey_service: dict[str, Any],
        patched_valkey_cli: tuple[str, ...],
        valkey_acl_prefix: str,
        cleanup_acl: Callable[[str], None],
        monkeypatch: pytest.MonkeyPatch,
    ):
        # aclfile is already configured at container startup (see the
        # ``valkey_service`` fixture). Point the handler's ACL_FILE
        # constant at the container-side path.
        monkeypatch.setattr(valkey_mod, "ACL_FILE", _CONTAINER_ACL_FILE)

        # Externally append a new user by writing directly into the
        # container-side ACL file.
        name = f"{valkey_acl_prefix}_ext"
        cleanup_acl(name)
        subprocess.run(
            [
                "podman",
                "exec",
                valkey_service["container"],
                "sh",
                "-c",
                f"echo 'user {name} on nopass ~* &* +@read' >> {_CONTAINER_ACL_FILE}",
            ],
            check=True,
            capture_output=True,
        )

        # Pre-condition: user not yet visible in the in-memory ACL.
        assert not _user_exists(patched_valkey_cli, name)

        result = handle_reload_acl({})
        assert result.success is True, result.error
        assert result.data["ok"] is True
        assert result.data["changed"] is True
        assert _user_exists(patched_valkey_cli, name)


class TestReloadAclIdempotency:
    """Second reload without external edits reports changed=False."""

    def test_repeated_reload_noop(
        self,
        patched_valkey_cli: tuple[str, ...],
        monkeypatch: pytest.MonkeyPatch,
    ):
        monkeypatch.setattr(valkey_mod, "ACL_FILE", _CONTAINER_ACL_FILE)

        first = handle_reload_acl({})
        assert first.success is True
        # First call may or may not flip changed — depends on whether the
        # current memory already matches the on-disk file. Exercise a
        # *second* call immediately and assert it is observably a no-op.
        second = handle_reload_acl({})
        assert second.success is True
        assert second.data["ok"] is True
        assert second.data["changed"] is False


class TestReloadAclInvalidFile:
    """Malformed ACL file must surface valkey's ERR text verbatim."""

    def test_bad_syntax_returns_fail_with_error(
        self,
        valkey_service: dict[str, Any],
        patched_valkey_cli: tuple[str, ...],
        monkeypatch: pytest.MonkeyPatch,
    ):
        monkeypatch.setattr(valkey_mod, "ACL_FILE", _CONTAINER_ACL_FILE)

        # Overwrite with nonsense that fails ACL LOAD parsing. Snapshot
        # the existing valid file first so we can restore it at teardown
        # (the session-scoped valkey service shares state across tests).
        snapshot = subprocess.run(
            ["podman", "exec", valkey_service["container"], "cat", _CONTAINER_ACL_FILE],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        subprocess.run(
            [
                "podman",
                "exec",
                valkey_service["container"],
                "sh",
                "-c",
                f"echo 'this is not a valid acl line at all' > {_CONTAINER_ACL_FILE}",
            ],
            check=True,
            capture_output=True,
        )

        try:
            result = handle_reload_acl({})
            assert result.success is False
            # Spec: "The error text from valkey is surfaced directly." Valkey
            # prefixes its ACL parse errors with "ERR".
            assert result.error is not None
            assert "ERR" in result.error or "problem" in result.error.lower()
        finally:
            # Restore the good ACL file so subsequent tests in the
            # session see a valid on-disk state.
            subprocess.run(
                [
                    "podman",
                    "exec",
                    "-i",
                    valkey_service["container"],
                    "sh",
                    "-c",
                    f"cat > {_CONTAINER_ACL_FILE}",
                ],
                input=snapshot,
                check=False,
                capture_output=True,
                text=True,
            )


class TestReloadAclIgnoresParams:
    """Spec: the handler ignores any params supplied."""

    @pytest.mark.parametrize(
        "params",
        [
            {},
            {"irrelevant": 1},
            {"name": "default", "rules": ["on"], "peer_id": "nobody"},
        ],
    )
    def test_params_are_ignored(
        self,
        patched_valkey_cli: tuple[str, ...],
        monkeypatch: pytest.MonkeyPatch,
        params: dict[str, Any],
    ):
        monkeypatch.setattr(valkey_mod, "ACL_FILE", _CONTAINER_ACL_FILE)

        result = handle_reload_acl(params)
        assert result.success is True
        assert result.data["ok"] is True
