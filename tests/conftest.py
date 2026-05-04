# tests/conftest.py

"""Shared fixtures for test isolation from host environment.

Prevents tests from shelling out to system binaries (podman, systemctl)
that may not exist on dev machines. Tests that need specific secret
behavior should override with their own mocker.patch().
"""

# Re-export handler fixtures (postgres_service, valkey_service,
# in_process_bus, fake_env_file, postgres_db, valkey_acl_prefix) so sibling
# test directories (notably ``tests/commands/env``) can consume them without
# duplicating fixture code. Pytest 9 makes ``pytest_plugins`` outside the
# rootdir conftest a hard error, so the declaration lives here. Fixtures
# are lazy — the podman-backed ones only start containers when a test
# requests them.
pytest_plugins = ["tests.commands.sidecar.handlers._fixtures"]


# The _mock_secret_exists fixture was removed when SECRET_VARIABLE_NAMES
# probing was replaced by drop-in environment directories. The quadlet module
# no longer calls secret_exists() during template generation.
