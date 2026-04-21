# tests/conftest.py

"""Shared fixtures for test isolation from host environment.

Prevents tests from shelling out to system binaries (podman, systemctl)
that may not exist on dev machines. Tests that need specific secret
behavior should override with their own mocker.patch().
"""

import pytest

# Re-export handler fixtures (postgres_service, valkey_service,
# in_process_bus, fake_env_file, postgres_db, valkey_acl_prefix) so sibling
# test directories (notably ``tests/commands/env``) can consume them without
# duplicating fixture code. Pytest 9 makes ``pytest_plugins`` outside the
# rootdir conftest a hard error, so the declaration lives here. Fixtures
# are lazy — the podman-backed ones only start containers when a test
# requests them.
pytest_plugins = ["tests.commands.sidecar.handlers._fixtures"]


@pytest.fixture(autouse=True)
def _mock_secret_exists(mocker):
    """Prevent podman subprocess calls during template generation.

    quadlet.get_secrets_section() calls secret_exists() which runs
    `podman secret exists <name>` via subprocess. This fails on machines
    without podman installed (e.g., macOS dev environments).

    Returns False by default (no secrets exist). Tests that exercise
    secret behavior already provide their own mock with return_value=True
    or a side_effect — those override this autouse fixture.
    """
    return mocker.patch(
        "rots.quadlet.secret_exists",
        return_value=False,
    )
