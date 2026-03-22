# src/rots/sidecar/allowlist.py

"""Config key allowlist for staged configuration updates.

Defines which configuration keys can be modified via the sidecar's
config.stage command. Keys not in the allowlist are rejected.
"""

from __future__ import annotations

import fnmatch
from collections.abc import Iterable

# Exact keys that are allowed
ALLOWED_KEYS: frozenset[str] = frozenset(
    {
        # Core application
        "REDIS_URL",
        "SECRET_KEY",
        "DOMAIN",
        "HOST",
        "SSL_ENABLED",
        "SSL",
        # Authentication
        "AUTH_ENABLED",
        "AUTH_SIGNUP",
        "AUTH_SIGNIN",
        "AUTH_AUTOVERIFY",
        "AUTHENTICATION_MODE",
        # Feature flags
        "REGIONS_ENABLED",
        "JURISDICTION",
        "DOMAINS_ENABLED",
        "DEFAULT_DOMAIN",
        "I18N_ENABLED",
        "I18N_DEFAULT_LOCALE",
        "JOBS_ENABLED",
        # RabbitMQ
        "RABBITMQ_URL",
        # Monitoring
        "DIAGNOSTICS_ENABLED",
        "SENTRY_DSN_BACKEND",
        "SENTRY_DSN_FRONTEND",
        "SENTRY_SAMPLE_RATE",
        # Server
        "PORT",
        "SERVER_TYPE",
        # Email (non-sensitive)
        "EMAIL_FROM",
        "EMAIL_SUBJECT_PREFIX",
        "EMAILER_MODE",
        "EMAILER_REGION",
        "FROM_EMAIL",
        "VERIFIER_EMAIL",
        "VERIFIER_DOMAIN",
    }
)

# Patterns for keys that match a prefix (use * wildcard)
ALLOWED_PATTERNS: tuple[str, ...] = (
    "SMTP_*",  # SMTP_HOST, SMTP_PORT, SMTP_USERNAME, SMTP_PASSWORD, SMTP_AUTH, SMTP_TLS
    "STRIPE_*",  # STRIPE_API_KEY, STRIPE_WEBHOOK_SIGNING_SECRET, STRIPE_TEST_*
    "SENTRY_*",  # All Sentry configuration
    "VITE_*",  # Frontend build variables
)

# Keys that are explicitly forbidden (even if they match patterns)
FORBIDDEN_KEYS: frozenset[str] = frozenset(
    {
        # Core secrets should use podman secrets, not env vars
        "SECRET",
        "SESSION_SECRET",
        "AUTH_SECRET",
        # OAuth tokens
        "CLAUDE_CODE_OAUTH_TOKEN",
        "GITHUB_CLIENT_SECRET",
        # Database credentials (should be managed separately)
        "AUTH_DATABASE_URL",
        "AUTH_DATABASE_URL_MIGRATIONS",
        # Colonel (admin) access
        "COLONEL",
    }
)


def is_key_allowed(key: str) -> bool:
    """Check if a configuration key is allowed to be staged.

    Args:
        key: The configuration key name (e.g., "REDIS_URL")

    Returns:
        True if the key can be modified via config.stage
    """
    # Normalize key
    key = key.strip().upper()

    # Check forbidden list first
    if key in FORBIDDEN_KEYS:
        return False

    # Check exact match
    if key in ALLOWED_KEYS:
        return True

    # Check patterns
    for pattern in ALLOWED_PATTERNS:
        if fnmatch.fnmatch(key, pattern):
            return True

    return False


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


def list_allowed_keys() -> list[str]:
    """Return a sorted list of all explicitly allowed keys.

    Does not expand patterns - use for documentation.
    """
    return sorted(ALLOWED_KEYS)


def list_allowed_patterns() -> list[str]:
    """Return a list of allowed key patterns.

    Patterns use fnmatch-style wildcards (e.g., "SMTP_*").
    """
    return list(ALLOWED_PATTERNS)
