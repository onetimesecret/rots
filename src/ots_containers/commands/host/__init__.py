# src/ots_containers/commands/host/__init__.py
"""Host configuration management — push, diff, pull config files via rsync/SSH."""

from .app import app

__all__ = ["app"]
