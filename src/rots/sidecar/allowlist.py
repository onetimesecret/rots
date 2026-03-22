# src/rots/sidecar/allowlist.py

"""Config key denylist for staged configuration updates.

The sidecar can configure almost everything. Only a small set of
critical secrets are forbidden from remote modification - these
must be managed via podman secrets or direct host access.
"""

from __future__ import annotations

import fnmatch
from collections.abc import Iterable

# Keys that are explicitly forbidden - bare essentials only.
# These are too sensitive for remote configuration via sidecar.
# See .env.reference for full documentation.
FORBIDDEN_KEYS: frozenset[str] = frozenset(
    {
        # Root secret + derived/independent secrets (HKDF chain)
        "SECRET",  # Root secret - all others derive from this
        # Database connections
        "REDIS_URL",
        "VALKEY_URL",
        "AUTH_DATABASE_URL",
        "AUTH_DATABASE_URL_MIGRATIONS",
        "RABBITMQ_URL",
        # Colonel (admin) access
        "COLONEL",
    }
)

# Patterns for forbidden keys (use * wildcard)
FORBIDDEN_PATTERNS: tuple[str, ...] = (
    # None currently - explicit keys are sufficient
)


def is_key_allowed(key: str) -> bool:
    """Check if a configuration key is allowed to be staged.

    Uses a denylist approach - everything is allowed except
    explicitly forbidden keys.

    Args:
        key: The configuration key name (e.g., "DOMAIN")

    Returns:
        True if the key can be modified via config.stage
    """
    # Normalize key
    key = key.strip().upper()

    # Check forbidden list
    if key in FORBIDDEN_KEYS:
        return False

    # Check forbidden patterns
    for pattern in FORBIDDEN_PATTERNS:
        if fnmatch.fnmatch(key, pattern):
            return False

    # Everything else is allowed
    return True


def filter_allowed_keys(keys: Iterable[str]) -> tuple[list[str], list[str]]:
    """Partition keys into allowed and rejected lists.

    Args:
        keys: Iterable of configuration key names

    Returns:
        Tuple of (allowed_keys, rejected_keys)
    """
    allowed: list[str] = []
    rejected: list[str] = []

    for key in keys:
        if is_key_allowed(key):
            allowed.append(key)
        else:
            rejected.append(key)

    return allowed, rejected


def validate_config_update(
    updates: dict[str, str],
) -> tuple[dict[str, str], list[str]]:
    """Validate a config update dict, filtering out disallowed keys.

    Args:
        updates: Dict of key=value pairs to stage

    Returns:
        Tuple of (valid_updates, rejected_keys)
    """
    valid: dict[str, str] = {}
    rejected: list[str] = []

    for key, value in updates.items():
        if is_key_allowed(key):
            valid[key] = value
        else:
            rejected.append(key)

    return valid, rejected


def list_forbidden_keys() -> list[str]:
    """Return a sorted list of all forbidden keys."""
    return sorted(FORBIDDEN_KEYS)


def list_forbidden_patterns() -> list[str]:
    """Return a list of forbidden key patterns.

    Patterns use fnmatch-style wildcards.
    """
    return list(FORBIDDEN_PATTERNS)
