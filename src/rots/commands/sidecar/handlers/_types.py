# src/rots/commands/sidecar/handlers/_types.py

"""Shared payload shapes for two-phase provisioning handlers.

Every handler returns ``CommandResult.ok(data=...)`` where ``data`` is a dict
matching one of the TypedDicts in this module. The invariant that ties them
together is ``changed: bool`` — the operator command (``rots env bootstrap``)
relies on it to produce accurate idempotency receipts:

* ``changed=True``  — the handler mutated state (role created, ACL added,
  password rotated, env file rewritten, timer enabled, ...).
* ``changed=False`` — the desired state already held; no mutation performed.

Handlers must set ``changed`` truthfully. "Wrote a file with the same content"
is ``changed=False``. "Wrote a file with new content" is ``changed=True``.

TypedDicts are used (not dataclasses) because the wire format is JSON through
RabbitMQ and the dispatcher stores ``CommandResult.data`` as an ``Any`` dict.
Using TypedDict lets pyright check handler implementations without forcing
client code (``rots env bootstrap``) through a decode path.
"""

from __future__ import annotations

from typing import TypedDict


class SecretsDeliverData(TypedDict):
    """Return payload for ``secrets.deliver``.

    Attributes:
        written: ``True`` if the handler opened the env file for write (even
            when content was identical — a ``write-if-different`` handler may
            short-circuit before opening, in which case this is ``False``).
        path: Absolute path of the env file that was targeted. Always the
            value passed in via ``env_file`` (defaults to
            ``/etc/default/onetimesecret``).
        changed: ``True`` when the on-disk content changed as a result of
            this call. ``False`` on idempotent re-delivery with the same
            value.
    """

    written: bool
    path: str
    changed: bool


class PostgresBootstrapAppData(TypedDict):
    """Return payload for ``postgres.bootstrap_app``.

    Attributes:
        role: The application role name that exists after the call
            (created or already-present).
        database: The application database name that exists after the call.
        password_delivered_to: ``host_id`` of the web sidecar that received
            the generated password via ``secrets.deliver``. Echoed back so
            the operator can confirm delivery in its receipt.
        changed: ``True`` when the role or database was created, or when
            ``rotate_password`` semantics were triggered. ``False`` when
            everything already existed and no password was rotated.
    """

    role: str
    database: str
    password_delivered_to: str
    changed: bool


class PostgresAddHbaData(TypedDict):
    """Return payload for ``postgres.add_hba``.

    Attributes:
        reloaded: ``True`` when ``pg_ctl reload`` (or equivalent) was
            invoked because the file's contents changed. Mirrors
            ``changed`` — kept as a separate key so existing call-sites
            that read ``reloaded`` keep working.
        changed: ``True`` when the pg_hba.d drop-in was created or its
            contents updated. ``False`` on an identical re-apply.
    """

    reloaded: bool
    changed: bool


class PostgresRotatePasswordData(TypedDict):
    """Return payload for ``postgres.rotate_password``.

    Attributes:
        delivered_to: ``host_id`` of the sidecar that received the new
            password via ``secrets.deliver``.
        changed: Always ``True`` on success. ``rotate_password`` is not
            idempotent — every call mints a fresh password. Included for
            uniformity so the operator command can treat every data
            payload identically.
    """

    delivered_to: str
    changed: bool


class ValkeyCreateAclUserData(TypedDict):
    """Return payload for ``valkey.create_acl_user``.

    Attributes:
        delivered_to: ``host_id`` of the sidecar that received the
            generated token via ``secrets.deliver``.
        changed: ``True`` when the ACL user was created or updated (rules
            differ from the stored entry). ``False`` when the user already
            exists with matching rules and no token rotation occurred.
    """

    delivered_to: str
    changed: bool


class ValkeyReloadAclData(TypedDict):
    """Return payload for ``valkey.reload_acl``.

    Attributes:
        ok: Echo of success (always ``True`` on a successful call). Kept
            for backward-compat with the signature in the issue body.
        changed: ``True`` when reloading the ACL file produced observable
            differences (users added/removed/updated). ``False`` when the
            running ACL state was already consistent with the file.
    """

    ok: bool
    changed: bool


class BackupInstallData(TypedDict):
    """Return payload for ``backup.install``.

    Attributes:
        unit: Name of the generated ``.service`` unit (e.g.
            ``ots-backup-db-daily.service``).
        timer: Name of the generated ``.timer`` unit.
        changed: ``True`` when any of { service, timer, rclone fragment,
            enabled state } was created or updated on this call.
    """

    unit: str
    timer: str
    changed: bool


class BackupUninstallData(TypedDict):
    """Return payload for ``backup.uninstall``.

    Attributes:
        ok: Echo of success. Kept for backward-compat with the signature
            in the issue body.
        changed: ``True`` when units/fragments were actually removed from
            disk (and ``systemctl disable`` ran). ``False`` if the profile
            was already absent.
    """

    ok: bool
    changed: bool
