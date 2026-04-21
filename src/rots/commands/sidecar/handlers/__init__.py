# src/rots/commands/sidecar/handlers/__init__.py

"""Two-phase provisioning RPC handlers (issue #55).

These handlers extend the sidecar command vocabulary with verbs that move
provisioning work out of cloud-init and into the sidecar. They live under
``rots.commands.sidecar.handlers`` (not ``rots.sidecar.handlers_*``) because
they share types (``_types``) and transport (``_transport``) with the operator
command ``rots env bootstrap`` that drives them.

Registration still lands in the single dispatcher at
``rots.sidecar.commands._handlers``. The import that triggers
``@register_handler`` decorators happens from
``rots.sidecar.commands._import_handlers``.

Modules:
    postgres  — postgres.bootstrap_app / add_hba / rotate_password (role: db)
    valkey    — valkey.create_acl_user / reload_acl (role: db)
    backup    — backup.install / uninstall (role: db)
    secrets   — secrets.deliver (role: db + web)

Helpers:
    _types     — shared payload shapes (changed: bool on every return)
    _transport — RpcClient protocol + production/in-process implementations
"""

from __future__ import annotations
