# src/rots/commands/sidecar/handlers/valkey.py

"""Valkey provisioning handlers (``valkey.*``).

These handlers run on the **db sidecar** (``roles={"db"}``). They manage
application ACL users and reload the ACL file after external edits.

Auth model
----------
The sidecar authenticates to local valkey as the fixed bootstrap user
``bootstrap`` (provisioned by upstream cloud-init, lots #41, which also
disables the ``default`` user). The bootstrap token is sealed into the
systemd credstore and mounted into the sidecar unit's runtime credential
directory via ``LoadCredentialEncrypted=valkey-bootstrap-token:…``.
systemd decrypts the token at service start and exposes it at
``$CREDENTIALS_DIRECTORY/valkey-bootstrap-token``; this handler reads
that path per-invocation (no caching, so rotations are picked up).
Callers running outside a systemd unit (tests, interactive debugging)
have no ``CREDENTIALS_DIRECTORY`` set and fall through to unauthenticated
loopback. The on-disk ``/etc/valkey/users.acl`` is root/valkey-owned
mode 0640 and is never read by this handler. All commands are issued
through ``valkey-cli`` to ``127.0.0.1:6379``; no TCP credentials ship
over the wire.

Cross-host delivery
-------------------
Generated tokens travel to the web peer via ``secrets.deliver`` on the
``peer_id`` sidecar. Same seam as postgres handlers:
``_get_rpc_client()`` returns an :class:`RpcClient` that tests can
monkeypatch.

ACL file layout
---------------
Valkey's ACL config lives at :data:`ACL_FILE`. ``ACL SETUSER`` is natively
idempotent *for the rules it spells out*, but persistence across restarts
requires ``ACL SAVE``. Implementations MUST call ``ACL SAVE`` after
every mutating operation.

ACL GETUSER parsing
-------------------
``valkey-cli`` returns ``ACL GETUSER`` in a flat line-oriented form with
known field names alternating with their values. Valkey 8.x emits:

* ``flags``, ``passwords``, ``selectors`` as arrays (one entry per line;
  an empty array is rendered as a single blank line).
* ``commands``, ``keys``, ``channels`` as scalars (one line, possibly
  blank).

:func:`_parse_acl_getuser` walks lines using the known-field map to know
whether the next lines are array elements or a scalar, stopping when
``selectors`` is reached (nested selector content is not needed for
idempotency).

Rule comparison
---------------
ACL rules are order-sensitive: ``+@all -@admin`` grants all commands
except the admin category, while ``-@admin +@all`` grants all commands
(the second ``+@all`` supersedes the earlier ``-@admin``). The stored
canonical form surfaced by ``ACL GETUSER`` preserves this order,
normalised as follows:

* A fresh user starts with no commands; Valkey prepends an implicit
  ``-@all`` to the stored ``commands`` string unless ``+@all`` /
  ``allcommands`` was specified **first** in the rule list.
* ``allcommands`` / ``allkeys`` / ``nocommands`` / ``resetkeys`` are
  spelled-out in stored form as ``+@all`` / ``~*`` / ``-@all`` / (cleared).
* Command names are lowercased (``+GET`` → ``+get``); category tags
  keep their case (``+@READ`` remains uppercase — but in practice callers
  always write categories lowercase).

:func:`_normalise_input_rules` applies the above transforms to produce
an **ordered list** of expected ``command_tokens`` and an ordered list
of ``key_tokens``. The handler compares these lists to the stored
``commands`` / ``keys`` from ``ACL GETUSER`` (also split on whitespace
into ordered lists). Equality means the caller asked for the same rules
in the same order as last time.

Limitation (documented, accepted): Valkey may collapse certain
adjacent rules when they are fully superseded (e.g. ``+get +@all`` is
stored as just ``+@all`` — ``+get`` was redundant). In those rare
cases our normalisation would disagree with the stored form, triggering
an unnecessary token rotation. Operators should not pass redundant
rules; the token-rotation "over-change" is the safe failure mode.
"""

from __future__ import annotations

import logging
import os
import secrets
import subprocess
from pathlib import Path
from typing import Any

from rots.sidecar.commands import Command, CommandResult, register_handler

from ._transport import RpcClient
from ._types import ValkeyCreateAclUserData, ValkeyReloadAclData

__all__ = [
    "ACL_FILE",
    "VALKEY_CLI",
    "RpcClient",
    "ValkeyCreateAclUserData",
    "ValkeyReloadAclData",
    "handle_create_acl_user",
    "handle_reload_acl",
]

logger = logging.getLogger(__name__)

# ACL file path on Debian/Ubuntu valkey package. Implementations MUST NOT
# accept an override — this is fixed by the package.
ACL_FILE: str = "/etc/valkey/users.acl"

# valkey-cli invocation. :func:`_run_valkey_cli` extends this with
# ``--user <bootstrap>`` and ``REDISCLI_AUTH=<token>`` (both sourced from
# the systemd credstore) when available.
VALKEY_CLI: tuple[str, ...] = ("valkey-cli", "-h", "127.0.0.1", "-p", "6379")

# Bootstrap auth is delivered via the systemd credstore. lots #41 seals
# the token into the sidecar unit with
# ``LoadCredentialEncrypted=valkey-bootstrap-token:…``; systemd decrypts
# at service start and points ``$CREDENTIALS_DIRECTORY`` at the runtime
# credential directory. The username is a fixed literal provisioned in
# ``/etc/valkey/users.acl`` by the same cloud-init run.
_BOOTSTRAP_USER = "bootstrap"
_CREDENTIAL_NAME = "valkey-bootstrap-token"

# Timeout for valkey-cli subprocess calls. Short — a healthy local daemon
# answers in milliseconds.
_VALKEY_CLI_TIMEOUT = 10

# Name validation: same identifier shape as Postgres roles. Valkey also rejects
# the literal ``default`` (reserved for the bootstrap user), handled separately.
_NAME_MAX_LEN = 64

# Known ACL GETUSER field names and whether the value is an array (True) or
# scalar (False). Order of insertion matches Valkey 8.x output order; the
# parser uses membership to decide when a new field begins, not this order.
_GETUSER_ARRAY_FIELDS: frozenset[str] = frozenset({"flags", "passwords", "selectors"})
_GETUSER_SCALAR_FIELDS: frozenset[str] = frozenset({"commands", "keys", "channels"})
_GETUSER_ALL_FIELDS: frozenset[str] = _GETUSER_ARRAY_FIELDS | _GETUSER_SCALAR_FIELDS


def _get_rpc_client() -> RpcClient:
    """Return an :class:`RpcClient`. Test seam — see postgres.py docstring."""
    from ._transport import RabbitMqRpcClient

    return RabbitMqRpcClient()


# --- helpers ---------------------------------------------------------------


def _is_valid_name(name: str) -> bool:
    """Validate ACL username shape.

    Matches ``^[A-Za-z_][A-Za-z0-9_]{0,63}$``. ``default`` is rejected at
    the handler level with a clearer error (not here).
    """
    if not name or len(name) > _NAME_MAX_LEN:
        return False
    first = name[0]
    if not (first.isalpha() or first == "_"):
        return False
    for c in name[1:]:
        if not (c.isalnum() or c == "_"):
            return False
    return True


def _load_bootstrap_auth() -> tuple[str, str] | None:
    """Return ``(user, plaintext_token)`` from the systemd credstore, or ``None``.

    systemd sets ``$CREDENTIALS_DIRECTORY`` for services started with
    ``LoadCredential*=`` directives. The sidecar unit (wired by lots #41)
    mounts the sealed bootstrap token as ``valkey-bootstrap-token`` inside
    that directory. Callers running outside a systemd unit (tests,
    interactive debugging) have no ``CREDENTIALS_DIRECTORY`` set and fall
    through to unauthenticated loopback — safe in test fixtures where
    valkey's ``default on nopass`` user still applies.

    Called per-invocation (no caching) so a rotated token is picked up on
    the next RPC without restarting the sidecar.
    """
    creds_dir = os.environ.get("CREDENTIALS_DIRECTORY")
    if not creds_dir:
        return None
    token_path = Path(creds_dir) / _CREDENTIAL_NAME
    try:
        token = token_path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        logger.debug("valkey: cannot read credstore token at %s: %s", token_path, exc)
        return None
    if not token:
        return None
    return (_BOOTSTRAP_USER, token)


def _run_valkey_cli(*args: str) -> subprocess.CompletedProcess[str]:
    """Run ``valkey-cli`` with the supplied args. Arguments are passed through
    as a tuple — never shell-interpolated.

    ``check=True`` will raise ``CalledProcessError`` on non-zero exit (the
    connection-refused case). A logical error (``ERR ...``) still comes back
    with exit 0; callers MUST inspect stdout.
    """
    auth = _load_bootstrap_auth()
    cli_args: tuple[str, ...] = VALKEY_CLI
    env: dict[str, str] | None = None
    if auth is not None:
        user, token = auth
        cli_args = cli_args + ("--user", user)
        # Pass the token via REDISCLI_AUTH so it stays out of argv (ps(1)).
        env = os.environ.copy()
        env["REDISCLI_AUTH"] = token
    else:
        logger.debug("valkey: no bootstrap auth available; running without --user/-a")

    return subprocess.run(
        cli_args + args,
        check=True,
        capture_output=True,
        text=True,
        timeout=_VALKEY_CLI_TIMEOUT,
        env=env,
    )


def _is_err_response(stdout: str) -> bool:
    """Return True when ``valkey-cli`` printed a server error on stdout.

    valkey-cli exits 0 on logical errors — the only reliable signal is the
    ``ERR `` prefix on stdout (``WRONGTYPE``, ``NOAUTH``, etc. start with
    an all-caps code followed by space; this covers the subset we care
    about in this handler).
    """
    stripped = stdout.lstrip()
    return stripped.startswith("ERR ") or stripped.startswith("NOAUTH ")


def _parse_acl_getuser(stdout: str) -> dict[str, Any] | None:
    """Parse the flat ``ACL GETUSER`` output into a dict.

    Returns ``None`` when the user does not exist (valkey-cli emits an
    empty response). Otherwise returns a dict with keys matching
    :data:`_GETUSER_ALL_FIELDS` and whatever shape is documented in the
    module docstring.

    This does NOT parse ``selectors`` contents — we stop at the
    ``selectors`` field name. The top-level ``commands`` + ``keys`` are
    sufficient for the idempotency guard.
    """
    if not stdout.strip():
        return None

    # splitlines() discards the terminator. We keep empty lines because they
    # are the scalar-empty and empty-array markers.
    lines = stdout.splitlines()
    result: dict[str, Any] = {}
    i = 0
    n = len(lines)
    while i < n:
        key = lines[i]
        if key not in _GETUSER_ALL_FIELDS:
            # Unknown token at top level — skip. Defensive: newer Valkey
            # versions could add fields; we ignore them rather than crashing.
            i += 1
            continue

        if key == "selectors":
            # Stop here; we don't need selector contents for idempotency.
            result.setdefault("selectors", [])
            break

        if key in _GETUSER_SCALAR_FIELDS:
            # Next line is the scalar value (may be empty).
            value = lines[i + 1] if i + 1 < n else ""
            result[key] = value
            i += 2
            continue

        # Array field: consume lines until we hit another known field name
        # or run out of input. Empty arrays surface as a single blank line
        # (one consumed, zero elements captured).
        entries: list[str] = []
        i += 1
        while i < n and lines[i] not in _GETUSER_ALL_FIELDS:
            if lines[i] != "" or entries:
                # Skip the leading blank that represents an empty array,
                # but keep blanks that appear inside non-empty arrays
                # (defensive — shouldn't happen in practice).
                entries.append(lines[i])
            i += 1
        result[key] = entries

    return result


def _normalise_input_rules(rules: list[str]) -> tuple[list[str], list[str]]:
    """Reduce input ``rules`` to ``(command_tokens, key_tokens)`` as ordered lists.

    Mirrors Valkey's canonical normalisation so the result can be
    compared to the stored ``commands`` / ``keys`` from ``ACL GETUSER``
    via list equality. See the module docstring's "Rule comparison"
    section for the full specification, including the documented
    collapse-of-redundant-rules limitation.
    """
    cmd_tokens: list[str] = []
    key_tokens: list[str] = []
    first_cmd_is_plus_all = False

    for raw in rules:
        # ``rules`` tokens are validated elsewhere to be non-empty and
        # whitespace-free; we still skip the empty-string case defensively.
        if not raw:
            continue

        if raw == "allcommands":
            if not cmd_tokens:
                first_cmd_is_plus_all = True
            cmd_tokens.append("+@all")
            continue
        if raw == "nocommands":
            cmd_tokens.append("-@all")
            continue
        if raw == "allkeys":
            key_tokens.append("~*")
            continue
        if raw == "resetkeys":
            key_tokens.clear()
            continue

        if raw.startswith(("+", "-")):
            # Category rules (``+@read`` / ``-@admin``) keep their case;
            # plain commands (``+GET``) are lowercased by Valkey.
            if len(raw) >= 2 and raw[1] == "@":
                normalised = raw
            else:
                normalised = raw[0] + raw[1:].lower()
            if not cmd_tokens and normalised == "+@all":
                first_cmd_is_plus_all = True
            cmd_tokens.append(normalised)
            continue

        if raw.startswith("~") or raw.startswith("%"):
            key_tokens.append(raw)
            continue

        # Auth / selector / unknown tokens (``on``, ``off``, ``>pw``,
        # ``<pw``, ``#hash``, ``!hash``, ``nopass``, ``resetpass``,
        # ``(...)``) don't participate in the commands/keys comparison.
        continue

    # Apply Valkey's implicit ``-@all``: a fresh user starts with no
    # commands, so unless the very first command-shaping rule was
    # ``+@all`` / ``allcommands``, Valkey renders the stored form with
    # a leading ``-@all``. Only add when there are command-shaping
    # rules — a user with zero ``+X``/``-X`` tokens has an empty
    # ``commands`` field in GETUSER.
    if cmd_tokens and not first_cmd_is_plus_all:
        cmd_tokens.insert(0, "-@all")

    return cmd_tokens, key_tokens


def _stored_rule_tokens(getuser: dict[str, Any]) -> tuple[list[str], list[str]]:
    """Extract ``(command_tokens, key_tokens)`` from a parsed GETUSER dict."""
    commands_raw = getuser.get("commands", "")
    keys_raw = getuser.get("keys", "")
    commands_str = commands_raw if isinstance(commands_raw, str) else ""
    keys_str = keys_raw if isinstance(keys_raw, str) else ""
    return commands_str.split(), keys_str.split()


@register_handler(Command.VALKEY_CREATE_ACL_USER, roles={"db"})
def handle_create_acl_user(params: dict[str, Any]) -> CommandResult:
    """Create (or update) an ACL user, generate a token, deliver it.

    Params:
        name (str, required): ACL username. MUST match
            ``^[A-Za-z_][A-Za-z0-9_]{0,63}$``. Valkey rejects ``default``
            as a name — reject it here with a clearer error.
        rules (list[str], required): Valkey ACL rule strings, passed
            through to ``ACL SETUSER <name> <rules...> ><token>``.
            Implementations MUST NOT sort or dedupe — ACL rules are order-
            sensitive (``allcommands`` before ``-@admin`` differs from the
            reverse). Validate: every rule is a non-empty string, no rule
            contains whitespace inside itself (each rule is one token).
        peer_id (str, required): Web sidecar ``host_id`` to receive the
            generated token.

    Behaviour (idempotent on rules, always rotates on token):

    1. Validate name + rules.
    2. Check existing user: ``ACL GETUSER <name>``. Parse the response's
       ``commands`` and ``keys`` fields into a rule list.
    3. Compare current rules against ``rules``. If identical, DO NOT
       rotate the token — return early with ``changed=False`` and
       ``delivered_to=peer_id`` as an informational echo.
    4. If different (or the user does not exist), generate a fresh token
       (``secrets.token_urlsafe(40)``) and issue
       ``ACL SETUSER <name> on >token <rules...>`` (``on`` enables login).
    5. ``ACL SAVE`` to persist.
    6. Publish ``secrets.deliver`` at ``peer_id`` with
       ``{"name": "VALKEY_PASSWORD", "value": <token>}``. On failure, undo
       with ``ACL DELUSER <name>`` (best-effort) and return
       :class:`CommandResult.fail`.

    Idempotency guard: parsed-rule comparison between ``ACL GETUSER``
    output and ``rules``.

    Side effects (first run / changed rules):

    * Issues ``ACL SETUSER``.
    * Issues ``ACL SAVE``.
    * Publishes ``secrets.deliver`` at ``peer_id``.

    Side effects (re-run with same rules): none.

    Return data matches :class:`ValkeyCreateAclUserData`. ``changed`` is
    ``True`` iff rules differed and a new token was delivered.

    Error mapping:

    * Validation failure → :class:`CommandResult.fail`, no valkey-cli
      call runs.
    * Non-zero exit from ``valkey-cli`` → :class:`CommandResult.fail`
      (``"valkey-cli failed: <stderr>"``).
    * :class:`TimeoutError` from RpcClient → ``ACL DELUSER`` rollback +
      :class:`CommandResult.fail`.
    """
    # --- validate params --------------------------------------------------
    name = params.get("name")
    if not isinstance(name, str) or not name:
        return CommandResult.fail("Missing or empty 'name' parameter")
    if name == "default":
        return CommandResult.fail("Rejected ACL name 'default': reserved for the bootstrap user")
    if not _is_valid_name(name):
        return CommandResult.fail(
            f"Invalid ACL name: {name!r}. Must match ^[A-Za-z_][A-Za-z0-9_]{{0,63}}$"
        )

    rules = params.get("rules")
    if not isinstance(rules, list) or not rules:
        return CommandResult.fail("Missing or empty 'rules' parameter (list required)")
    for idx, rule in enumerate(rules):
        if not isinstance(rule, str) or not rule:
            return CommandResult.fail(f"rules[{idx}] must be a non-empty string; got {rule!r}")
        if any(c.isspace() for c in rule):
            return CommandResult.fail(
                f"rules[{idx}] must not contain whitespace (each rule is one token): {rule!r}"
            )

    peer_id = params.get("peer_id")
    if not isinstance(peer_id, str) or not peer_id:
        return CommandResult.fail("Missing or empty 'peer_id' parameter")

    # --- existence + rule comparison -------------------------------------
    try:
        getuser_proc = _run_valkey_cli("ACL", "GETUSER", name)
    except FileNotFoundError as exc:
        return CommandResult.fail(f"valkey-cli not found on PATH: {exc}")
    except subprocess.TimeoutExpired as exc:
        return CommandResult.fail(f"valkey-cli timed out running ACL GETUSER: {exc}")
    except subprocess.CalledProcessError as exc:
        return CommandResult.fail(f"valkey-cli failed: {exc.stderr.strip() or exc}")

    if _is_err_response(getuser_proc.stdout):
        return CommandResult.fail(f"valkey-cli ACL GETUSER returned: {getuser_proc.stdout.strip()}")

    stored = _parse_acl_getuser(getuser_proc.stdout)

    if stored is not None:
        input_cmds, input_keys = _normalise_input_rules(rules)
        stored_cmds, stored_keys = _stored_rule_tokens(stored)
        logger.debug(
            "valkey.create_acl_user idempotency check for %s: input_cmds=%s stored_cmds=%s "
            "input_keys=%s stored_keys=%s",
            name,
            input_cmds,
            stored_cmds,
            input_keys,
            stored_keys,
        )
        if input_cmds == stored_cmds and input_keys == stored_keys:
            logger.info("valkey.create_acl_user no-op for %s: rules match stored state", name)
            data: ValkeyCreateAclUserData = {
                "delivered_to": peer_id,
                "changed": False,
            }
            return CommandResult.ok(data)

    # --- apply: token + SETUSER + SAVE ----------------------------------
    token = secrets.token_urlsafe(40)
    # Rule layout: ``on >token <rules...>`` — matches the behaviour spec.
    # ``rules`` is validated above as list[str]; cast silences the pyright
    # unpack-of-Unknown complaint without changing runtime behaviour.
    rules_strs: list[str] = [str(r) for r in rules]
    try:
        setuser_proc = _run_valkey_cli(
            "ACL",
            "SETUSER",
            name,
            "on",
            f">{token}",
            *rules_strs,
        )
    except subprocess.TimeoutExpired as exc:
        return CommandResult.fail(f"valkey-cli timed out running ACL SETUSER: {exc}")
    except subprocess.CalledProcessError as exc:
        return CommandResult.fail(f"valkey-cli failed: {exc.stderr.strip() or exc}")

    if _is_err_response(setuser_proc.stdout):
        return CommandResult.fail(f"valkey-cli ACL SETUSER returned: {setuser_proc.stdout.strip()}")

    warnings: list[str] = []

    # ACL SAVE — persist. Failure is non-fatal but surfaces as a warning
    # per spec: "On failure of SAVE, still return success but add a warning
    # in CommandResult.warnings that the change is in-memory only."
    try:
        save_proc = _run_valkey_cli("ACL", "SAVE")
        if _is_err_response(save_proc.stdout):
            warnings.append(
                f"ACL SAVE failed; change is in-memory only: {save_proc.stdout.strip()}"
            )
    except subprocess.TimeoutExpired:
        warnings.append("ACL SAVE timed out; change is in-memory only")
    except subprocess.CalledProcessError as exc:
        warnings.append(f"ACL SAVE failed; change is in-memory only: {exc.stderr.strip() or exc}")

    # --- deliver token to web peer --------------------------------------
    # secrets.deliver is cross-host; wrap the rollback in best-effort so a
    # broker or target-handler failure leaves no orphaned user behind.
    client = _get_rpc_client()
    try:
        delivery = client.publish(
            peer_id,
            Command.SECRETS_DELIVER.value,
            {"name": "VALKEY_PASSWORD", "value": token},
        )
    except TimeoutError as exc:
        _rollback_deluser(name, warnings)
        logger.warning(
            "valkey.create_acl_user: secrets.deliver timed out for %s at peer %s",
            name,
            peer_id,
        )
        del exc  # token value may be in exc; avoid re-raising
        return CommandResult(
            success=False,
            error="secrets.deliver timed out",
            warnings=warnings,
        )
    except Exception as exc:  # noqa: BLE001 — transport errors vary
        _rollback_deluser(name, warnings)
        logger.warning(
            "valkey.create_acl_user: secrets.deliver raised for %s at peer %s: %s",
            name,
            peer_id,
            exc.__class__.__name__,
        )
        return CommandResult(
            success=False,
            error=f"secrets.deliver transport error: {exc.__class__.__name__}",
            warnings=warnings,
        )

    if not delivery.success:
        _rollback_deluser(name, warnings)
        logger.warning(
            "valkey.create_acl_user: secrets.deliver failed for %s at peer %s: %s",
            name,
            peer_id,
            delivery.error,
        )
        return CommandResult(
            success=False,
            error=f"secrets.deliver failed: {delivery.error}",
            warnings=warnings,
        )

    logger.info(
        "valkey.create_acl_user: user=%s rules_applied=%d delivered_to=%s",
        name,
        len(rules),
        peer_id,
    )
    data: ValkeyCreateAclUserData = {
        "delivered_to": peer_id,
        "changed": True,
    }
    return CommandResult(success=True, data=data, warnings=warnings)


def _rollback_deluser(name: str, warnings: list[str]) -> None:
    """Best-effort ``ACL DELUSER`` rollback.

    Per spec: ``ACL DELUSER`` rollback on delivery failure is best-effort;
    wrap in try/except and surface as warnings.
    """
    try:
        proc = _run_valkey_cli("ACL", "DELUSER", name)
        if _is_err_response(proc.stdout):
            warnings.append(f"Rollback ACL DELUSER failed: {proc.stdout.strip()}")
            return
        # Try to persist the rollback too — same forgiving policy.
        try:
            save_proc = _run_valkey_cli("ACL", "SAVE")
            if _is_err_response(save_proc.stdout):
                warnings.append(f"Rollback ACL SAVE failed: {save_proc.stdout.strip()}")
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            warnings.append(f"Rollback ACL SAVE failed: {exc}")
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
        warnings.append(f"Rollback ACL DELUSER failed: {exc}")


@register_handler(Command.VALKEY_RELOAD_ACL, roles={"db"})
def handle_reload_acl(params: dict[str, Any]) -> CommandResult:
    """Re-read :data:`ACL_FILE` after an external edit.

    Params:
        None. The handler ignores any params supplied.

    Behaviour:

    1. Before reloading, snapshot the current in-memory ACL via
       ``ACL LIST``. Compute a sha256 of the sorted output.
    2. Issue ``ACL LOAD``. If it returns an error (``ERR There is a
       problem with your ACLs ...``), return
       :class:`CommandResult.fail` with the error text. The in-memory
       state is unchanged in this case.
    3. Snapshot again. If the two hashes differ, ``changed=True``.

    Side effects: ``ACL LOAD`` always runs (this is the point of the
    command). Only observable state-change flips ``changed`` to ``True``.

    Return data matches :class:`ValkeyReloadAclData`. ``ok`` mirrors
    ``CommandResult.success``.

    Error mapping:

    * Non-zero exit from ``valkey-cli`` → :class:`CommandResult.fail`.
    * ACL syntax error (``ERR`` response) → :class:`CommandResult.fail`.
      The error text from valkey is surfaced directly.
    """
    del params  # handler ignores params — spec

    # --- pre-reload snapshot --------------------------------------------
    pre_hash, err = _snapshot_acl_hash()
    if err is not None:
        return CommandResult.fail(err)

    # --- ACL LOAD -------------------------------------------------------
    try:
        load_proc = _run_valkey_cli("ACL", "LOAD")
    except FileNotFoundError as exc:
        return CommandResult.fail(f"valkey-cli not found on PATH: {exc}")
    except subprocess.TimeoutExpired as exc:
        return CommandResult.fail(f"valkey-cli timed out running ACL LOAD: {exc}")
    except subprocess.CalledProcessError as exc:
        return CommandResult.fail(f"valkey-cli failed: {exc.stderr.strip() or exc}")

    # valkey-cli exits 0 on logical errors; the first line carries the
    # server's ERR text. Per spec: "if the first line is ERR ..., fail."
    stdout = load_proc.stdout
    first_line = stdout.splitlines()[0] if stdout.strip() else ""
    if first_line.startswith("ERR ") or first_line.startswith("NOAUTH "):
        return CommandResult.fail(f"ACL LOAD failed: {first_line}")

    # --- post-reload snapshot -------------------------------------------
    post_hash, err = _snapshot_acl_hash()
    if err is not None:
        return CommandResult.fail(err)

    changed = pre_hash != post_hash
    logger.info(
        "valkey.reload_acl: ACL LOAD ok changed=%s (pre=%s post=%s)",
        changed,
        pre_hash[:12] if pre_hash else "<empty>",
        post_hash[:12] if post_hash else "<empty>",
    )
    data: ValkeyReloadAclData = {
        "ok": True,
        "changed": changed,
    }
    return CommandResult.ok(data)


def _snapshot_acl_hash() -> tuple[str, str | None]:
    """Return ``(sha256_of_sorted_acl_list, error_message_or_None)``.

    On any ``valkey-cli`` failure, the second element is populated and the
    first is an empty string. The hash is computed over the **sorted**
    ``ACL LIST`` lines so ordering changes between snapshots do not
    produce spurious ``changed=True``.
    """
    import hashlib

    try:
        proc = _run_valkey_cli("ACL", "LIST")
    except FileNotFoundError as exc:
        return "", f"valkey-cli not found on PATH: {exc}"
    except subprocess.TimeoutExpired as exc:
        return "", f"valkey-cli timed out running ACL LIST: {exc}"
    except subprocess.CalledProcessError as exc:
        return "", f"valkey-cli failed: {exc.stderr.strip() or exc}"

    if _is_err_response(proc.stdout):
        return "", f"valkey-cli ACL LIST returned: {proc.stdout.strip()}"

    lines = sorted(line for line in proc.stdout.splitlines() if line)
    digest = hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()
    return digest, None
