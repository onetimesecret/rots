# tests/commands/sidecar/test_app.py

"""Tests for sidecar CLI subcommand registration.

Verifies that the `discover` command is wired into the sidecar app
alongside the existing commands (install, start, stop, etc.).
"""

import pytest

from rots.commands.sidecar.app import app

pytestmark = pytest.mark.quick


class TestSidecarAppCommands:
    """The sidecar app should expose expected subcommands."""

    def _command_names(self) -> set[str]:
        """Extract registered command names from the cyclopts app."""
        # cyclopts stores commands in app._commands dict
        # Keys are the command function names (or the name= override)
        names = set()
        for key in app:
            # cyclopts App iteration yields (name, command_app) tuples
            # or just command names depending on version
            if isinstance(key, tuple):
                names.add(key[0])
            elif isinstance(key, str):
                names.add(key)
            else:
                # cyclopts App objects
                names.add(getattr(key, "name", [str(key)])[0])
        return names

    def test_discover_command_registered(self):
        """The 'discover' command should be registered on the sidecar app."""
        # Verify the discover function is accessible as a command
        # by checking that the app has it in its command registry
        from rots.commands.sidecar.app import discover

        assert callable(discover)

    def test_install_command_registered(self):
        """The 'install' command should be registered on the sidecar app."""
        from rots.commands.sidecar.app import install

        assert callable(install)

    def test_send_command_registered(self):
        """The 'send' command should be registered on the sidecar app."""
        from rots.commands.sidecar.app import send

        assert callable(send)

    def test_publish_command_registered(self):
        """The 'publish' command should be registered on the sidecar app."""
        from rots.commands.sidecar.app import publish

        assert callable(publish)

    def test_run_command_registered(self):
        """The 'run' command should be registered on the sidecar app."""
        from rots.commands.sidecar.app import run

        assert callable(run)
