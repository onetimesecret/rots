# tests/commands/sidecar/handlers/test_role_gating.py

"""Tests for the role-based handler filtering on ``@register_handler``.

The role gate lives on the ``@register_handler(roles={...})`` decorator.
At registration time every handler declares which sidecar roles should
expose it. At startup, ``_import_handlers(role=...)`` filters the active
dispatcher so a web sidecar only sees web-applicable commands.

These tests avoid importlib reloads (which have ugly knock-on effects on
other tests that patch the ``secrets`` module object). Instead they
assert:

* Every handler has a declared role set after ``_import_handlers()``.
* The expected role sets match the spec.
* ``register_handler`` validates its ``roles`` argument.
* ``_import_handlers`` validates its ``role`` argument.
"""

from __future__ import annotations

import pytest

from rots.sidecar.commands import (
    ALL_ROLES,
    DEFAULT_ROLES,
    Command,
    _handler_roles,
    _import_handlers,
    register_handler,
)

pytestmark = pytest.mark.quick


class TestRoleMetadata:
    """Every handler module registers with the expected role set."""

    def setup_method(self) -> None:
        # Populate the registry on demand. Safe to call repeatedly —
        # module imports are idempotent.
        _import_handlers()

    def test_secrets_deliver_is_db_and_web(self):
        assert _handler_roles[Command.SECRETS_DELIVER] == frozenset({"db", "web"})

    @pytest.mark.parametrize(
        "cmd",
        [
            Command.POSTGRES_BOOTSTRAP_APP,
            Command.POSTGRES_ADD_HBA,
            Command.POSTGRES_ROTATE_PASSWORD,
            Command.VALKEY_CREATE_ACL_USER,
            Command.VALKEY_RELOAD_ACL,
            Command.BACKUP_INSTALL,
            Command.BACKUP_UNINSTALL,
        ],
    )
    def test_db_only_handlers_declare_db_role(self, cmd: Command):
        assert _handler_roles[cmd] == frozenset({"db"})

    @pytest.mark.parametrize(
        "cmd",
        [
            Command.RESTART_WEB,
            Command.CONFIG_STAGE,
            Command.HEALTH,
            Command.DISCOVER_PING,
        ],
    )
    def test_legacy_handlers_default_to_all_roles(self, cmd: Command):
        """Legacy handlers (registered without explicit roles) get DEFAULT_ROLES."""
        assert _handler_roles[cmd] == DEFAULT_ROLES


class TestRegisterHandlerValidation:
    """``register_handler`` rejects unknown roles at decoration time."""

    def test_rejects_unknown_role(self):
        with pytest.raises(ValueError, match="Unknown role"):
            # Attempt to register a bogus role — the decorator factory
            # raises immediately, before ever being applied to a function.
            register_handler(Command.HEALTH, roles={"saboteur"})

    def test_accepts_known_roles(self):
        # No exception — the returned decorator is callable.
        deco_db = register_handler(Command.HEALTH, roles={"db"})
        deco_web = register_handler(Command.HEALTH, roles={"web"})
        deco_both = register_handler(Command.HEALTH, roles={"db", "web"})
        assert callable(deco_db)
        assert callable(deco_web)
        assert callable(deco_both)


class TestImportHandlersValidation:
    """``_import_handlers`` rejects unknown roles."""

    def test_unknown_role(self):
        with pytest.raises(ValueError, match="Unknown sidecar role"):
            _import_handlers(role="saboteur")


class TestAllRolesConstant:
    """``ALL_ROLES`` enumerates exactly the supported sidecar roles."""

    def test_all_roles_is_db_and_web(self):
        assert ALL_ROLES == frozenset({"db", "web"})

    def test_default_roles_matches_all(self):
        assert DEFAULT_ROLES == ALL_ROLES
