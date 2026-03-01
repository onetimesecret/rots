# src/rots/commands/service/__init__.py
"""Service management for systemd template services (valkey, redis, etc.)."""

from .app import app

__all__ = ["app"]
