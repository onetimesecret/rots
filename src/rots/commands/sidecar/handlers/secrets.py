# src/rots/commands/sidecar/handlers/secrets.py

"""``secrets.deliver`` — hardened write-if-different of application secrets
into the OTS env file.

This handler is the web sidecar's slice of the two-phase provisioning flow
(issue #55). The db sidecar generates application passwords, then publishes
``secrets.deliver`` at the web peer so the secret lands in
``/etc/default/onetimesecret`` without ever touching a log, shell history,
or intermediate file on the db side.

Hardening:

* Allowlisted ``env_file`` — only ``/etc/default/onetimesecret``.
* Allowlisted ``name`` — only ``PG_PASSWORD`` and ``VALKEY_PASSWORD``.
* ``O_NOFOLLOW`` on the target path (and ``lstat`` pre-check) — a symlinked
  env file is rejected before anything is written.
* Atomic replace via ``os.rename`` of a same-directory tempfile — a crash
  mid-write leaves the original file intact.
* Mode ``0640`` — readable by the service user (via group), not world.
* The secret value is never logged. Only the ``name`` and ``path``.

Role: registered for ``{"db", "web"}`` — the db sidecar also runs it so a
single-host test setup can exercise the flow end-to-end without a second
host.
"""

from __future__ import annotations

import logging
import os
import stat
import tempfile
from pathlib import Path
from typing import Any

from rots.sidecar.commands import Command, CommandResult, register_handler

from ._types import SecretsDeliverData

logger = logging.getLogger(__name__)

# Hardened allowlist. Expand here (and in ALLOWED_NAMES) only after a threat
# model review — broadening this surface is the first thing an attacker will
# try to exploit if they find an RPC publish path.
ALLOWED_ENV_FILE: str = "/etc/default/onetimesecret"
ALLOWED_NAMES: frozenset[str] = frozenset({"PG_PASSWORD", "VALKEY_PASSWORD"})

# File mode for the env file — readable by owner, readable by group (so the
# rootless container user in the `onetimesecret` group can load it), not
# world-readable. Matches what `rots env process` sets today.
ENV_FILE_MODE = 0o640


@register_handler(Command.SECRETS_DELIVER, roles={"db", "web"})
def handle_secrets_deliver(params: dict[str, Any]) -> CommandResult:
    """Write a named secret into the OTS env file, atomically and idempotently.

    Params:
        name (str): Which env variable to set. Must be a member of
            :data:`ALLOWED_NAMES`. Unknown names are rejected before any
            filesystem work.
        value (str): The secret value. Not logged; only its presence is
            checked. Empty-string values are accepted (treated as a
            deliberate clear) — caller is expected to validate upstream.
        env_file (str, optional): Target env file. Must equal
            :data:`ALLOWED_ENV_FILE`. Defaults to that value. Anything else
            is rejected.

    Returns:
        :class:`CommandResult` with ``data`` matching
        :class:`SecretsDeliverData`.

        * On idempotent no-op (value already matches):
          ``{"written": False, "path": ..., "changed": False}``
        * On mutation:
          ``{"written": True, "path": ..., "changed": True}``

    Rejection cases (all return ``CommandResult.fail(...)``):

    * ``name`` missing, or not in :data:`ALLOWED_NAMES`
    * ``value`` missing (``None``) — empty string is accepted
    * ``env_file`` not equal to :data:`ALLOWED_ENV_FILE`
    * Target path is a symlink (``lstat`` test), defeating a TOCTOU redirect
    * ``OSError`` raised anywhere during the atomic write — the on-disk
      file is left intact (``os.rename`` is atomic on POSIX within a dir).
    """
    # --- allowlist gates --------------------------------------------------
    name = params.get("name")
    if not isinstance(name, str) or not name:
        return CommandResult.fail("Missing or empty 'name' parameter")
    if name not in ALLOWED_NAMES:
        return CommandResult.fail(
            f"Rejected secret name: {name!r}. Allowed names: {sorted(ALLOWED_NAMES)}"
        )

    if "value" not in params:
        return CommandResult.fail("Missing 'value' parameter")
    value = params["value"]
    if not isinstance(value, str):
        return CommandResult.fail(f"'value' must be a string, got {type(value).__name__}")

    env_file = params.get("env_file", ALLOWED_ENV_FILE)
    if env_file != ALLOWED_ENV_FILE:
        return CommandResult.fail(
            f"Rejected env_file: {env_file!r}. Only {ALLOWED_ENV_FILE!r} is permitted."
        )

    path = Path(env_file)

    # --- symlink defence --------------------------------------------------
    # If the path exists as a symlink, refuse. We deliberately do not resolve
    # and proceed — a symlink is a hijack vector (attacker with write access
    # to the parent dir could have repointed it).
    try:
        lst = os.lstat(path)
    except FileNotFoundError:
        lst = None
    except OSError as exc:
        return CommandResult.fail(f"Failed to stat {env_file}: {exc}")

    if lst is not None and stat.S_ISLNK(lst.st_mode):
        return CommandResult.fail(f"Refusing to write: {env_file} is a symbolic link")

    # --- read current content (NOFOLLOW on the real open) -----------------
    current_lines: list[str] = []
    if lst is not None:
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        except OSError as exc:
            return CommandResult.fail(f"Failed to open {env_file}: {exc}")
        try:
            with os.fdopen(fd, "r", encoding="utf-8") as f:
                current_lines = f.read().splitlines(keepends=True)
        except OSError as exc:
            return CommandResult.fail(f"Failed to read {env_file}: {exc}")

    # --- compute updated content ------------------------------------------
    new_lines, changed = _set_env_key(current_lines, name, value)

    if not changed:
        # No-op: content matches. Do NOT rewrite — preserve mtime so external
        # watchers (systemd path units, etc.) do not see a spurious change.
        logger.info("secrets.deliver no-op: %s already set in %s", name, env_file)
        return CommandResult.ok(SecretsDeliverData(written=False, path=str(path), changed=False))

    # --- atomic write-if-different ----------------------------------------
    # Tempfile in the same directory so os.rename is atomic. delete=False so
    # we can rename it into place; on exception we clean up manually.
    new_body = "".join(new_lines)
    parent = path.parent
    try:
        parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return CommandResult.fail(f"Failed to ensure parent dir {parent}: {exc}")

    tmp_fd: int | None = None
    tmp_name: str | None = None
    try:
        tmp_fd, tmp_name = tempfile.mkstemp(
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=str(parent),
        )
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            tmp_fd = None  # fdopen owns it now — do not double-close
            f.write(new_body)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp_name, ENV_FILE_MODE)
        os.rename(tmp_name, path)
        tmp_name = None  # renamed, no cleanup needed
        # Preserve pre-existing owner/group. The tempfile was created by the
        # sidecar process (likely root); without this restore the onetimesecret
        # group bit that the container user depends on would be dropped.
        if lst is not None:
            try:
                os.chown(path, lst.st_uid, lst.st_gid)
            except OSError as exc:
                logger.warning(
                    "secrets.deliver: failed to restore owner/group on %s: %s",
                    env_file,
                    exc,
                )
    except OSError as exc:
        return CommandResult.fail(f"Atomic write failed for {env_file}: {exc}")
    finally:
        if tmp_fd is not None:
            try:
                os.close(tmp_fd)
            except OSError:
                pass
        if tmp_name is not None:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass

    logger.info(
        "secrets.deliver wrote %s into %s (mode=0%o)",
        name,
        env_file,
        ENV_FILE_MODE,
    )
    return CommandResult.ok(SecretsDeliverData(written=True, path=str(path), changed=True))


# --- env-file round-trip helper ------------------------------------------


def _set_env_key(
    lines: list[str],
    name: str,
    value: str,
) -> tuple[list[str], bool]:
    """Return (new_lines, changed) after applying ``name=value`` to ``lines``.

    Behaviour:

    * If ``name`` already appears as a non-commented assignment and the
      existing value (with surrounding quotes stripped) equals ``value``,
      nothing changes and ``changed=False``.
    * If ``name`` already appears with a different value, the line is
      replaced in place with ``name=value``. Line ending is preserved if
      the original had one; otherwise a trailing newline is added.
    * If ``name`` does not appear, a new ``name=value`` line is appended at
      the end of the file. A trailing newline is ensured.
    * Comments and other keys are preserved byte-for-byte.

    The quoting of the emitted ``name=value`` line follows a conservative
    rule: if ``value`` contains whitespace, ``#``, or quotes, it is
    double-quoted with existing double-quotes escaped. Otherwise it is
    written bare. This matches shell-source semantics used by systemd's
    ``EnvironmentFile=``.
    """
    formatted = _format_assignment(name, value)
    prefix = f"{name}="

    found = False
    new_lines: list[str] = []
    for line in lines:
        stripped = line.lstrip()
        if stripped.startswith("#") or "=" not in stripped:
            new_lines.append(line)
            continue
        key, _, rest = stripped.partition("=")
        if key.strip() != name:
            new_lines.append(line)
            continue
        # Found the assignment. Compare values.
        existing_value = _parse_value(rest.rstrip("\n"))
        if existing_value == value:
            new_lines.append(line)
            found = True
            continue
        # Replace, preserve trailing newline if present.
        trailing = "\n" if line.endswith("\n") else ""
        new_lines.append(formatted + trailing)
        found = True

    if found:
        # Whether changed depends on whether any replacement happened; compare
        # outputs.
        return new_lines, new_lines != lines

    # Append. Ensure a trailing newline on the prior last line first so we
    # never produce "OTHER=valNEW=val\n".
    if new_lines and not new_lines[-1].endswith("\n"):
        new_lines[-1] = new_lines[-1] + "\n"
    new_lines.append(formatted + "\n")
    _ = prefix  # kept for clarity / future use
    return new_lines, True


def _format_assignment(name: str, value: str) -> str:
    """Render a ``name=value`` line with conservative quoting."""
    needs_quote = any(c.isspace() for c in value) or any(
        c in value for c in ('"', "'", "#", "=", "$", "\\", "`")
    )
    if not needs_quote and value != "":
        return f"{name}={value}"
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'{name}="{escaped}"'


def _parse_value(raw: str) -> str:
    """Strip surrounding single or double quotes from an env-file value.

    Does not interpret backslash escapes — the one-sided inverse of
    :func:`_format_assignment`. Good enough for the comparison in
    :func:`_set_env_key` because we round-trip our own output, and any
    value that came from our writer uses only double quotes with ``\\"``
    escapes.
    """
    v = raw.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in ('"', "'"):
        inner = v[1:-1]
        if v[0] == '"':
            inner = inner.replace('\\"', '"').replace("\\\\", "\\")
        return inner
    return v
