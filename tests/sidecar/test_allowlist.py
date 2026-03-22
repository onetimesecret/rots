# tests/sidecar/test_allowlist.py

"""Tests for src/rots/sidecar/allowlist.py

Covers:
- is_key_allowed with exact matches, pattern matches, and forbidden keys
- filter_allowed_keys partitioning
- validate_config_update dict filtering
- list_allowed_keys and list_allowed_patterns
"""

from rots.sidecar.allowlist import (
    ALLOWED_KEYS,
    ALLOWED_PATTERNS,
    FORBIDDEN_KEYS,
    filter_allowed_keys,
    is_key_allowed,
    list_allowed_keys,
    list_allowed_patterns,
    validate_config_update,
)


class TestIsKeyAllowed:
    """Tests for is_key_allowed function."""

    # Exact match tests
    def test_exact_match_allowed(self):
        """Keys in ALLOWED_KEYS are allowed."""
        assert is_key_allowed("REDIS_URL") is True
        assert is_key_allowed("SECRET_KEY") is True
        assert is_key_allowed("DOMAIN") is True
        assert is_key_allowed("SSL_ENABLED") is True

    def test_exact_match_case_insensitive(self):
        """Key matching is case-insensitive (normalized to uppercase)."""
        assert is_key_allowed("redis_url") is True
        assert is_key_allowed("Redis_Url") is True
        assert is_key_allowed("REDIS_URL") is True

    def test_exact_match_with_whitespace(self):
        """Whitespace is stripped from keys."""
        assert is_key_allowed("  REDIS_URL  ") is True
        assert is_key_allowed("\tDOMAIN\n") is True

    # Pattern match tests
    def test_pattern_match_smtp(self):
        """SMTP_* pattern matches SMTP-prefixed keys."""
        assert is_key_allowed("SMTP_HOST") is True
        assert is_key_allowed("SMTP_PORT") is True
        assert is_key_allowed("SMTP_USERNAME") is True
        assert is_key_allowed("SMTP_PASSWORD") is True
        assert is_key_allowed("SMTP_AUTH") is True
        assert is_key_allowed("SMTP_TLS") is True

    def test_pattern_match_stripe(self):
        """STRIPE_* pattern matches Stripe-prefixed keys."""
        assert is_key_allowed("STRIPE_API_KEY") is True
        assert is_key_allowed("STRIPE_WEBHOOK_SIGNING_SECRET") is True
        assert is_key_allowed("STRIPE_TEST_KEY") is True
        assert is_key_allowed("STRIPE_PUBLISHABLE_KEY") is True

    def test_pattern_match_sentry(self):
        """SENTRY_* pattern matches Sentry-prefixed keys."""
        assert is_key_allowed("SENTRY_DSN") is True
        assert is_key_allowed("SENTRY_ENVIRONMENT") is True
        assert is_key_allowed("SENTRY_TRACES_SAMPLE_RATE") is True

    def test_pattern_match_vite(self):
        """VITE_* pattern matches Vite frontend variables."""
        assert is_key_allowed("VITE_API_URL") is True
        assert is_key_allowed("VITE_APP_NAME") is True

    # Forbidden keys tests
    def test_forbidden_keys_rejected(self):
        """Keys in FORBIDDEN_KEYS are not allowed even if they match patterns."""
        assert is_key_allowed("SECRET") is False
        assert is_key_allowed("SESSION_SECRET") is False
        assert is_key_allowed("AUTH_SECRET") is False
        assert is_key_allowed("COLONEL") is False

    def test_forbidden_overrides_allowed(self):
        """Forbidden check happens before pattern matching."""
        # These might match patterns but are forbidden
        assert is_key_allowed("AUTH_DATABASE_URL") is False
        assert is_key_allowed("GITHUB_CLIENT_SECRET") is False

    # Not allowed tests
    def test_unknown_keys_rejected(self):
        """Keys not in allowlist or patterns are rejected."""
        assert is_key_allowed("RANDOM_KEY") is False
        assert is_key_allowed("DATABASE_URL") is False
        assert is_key_allowed("AWS_ACCESS_KEY") is False
        assert is_key_allowed("MY_CUSTOM_VAR") is False

    def test_partial_pattern_match_rejected(self):
        """Partial matches don't count - full pattern must match."""
        # These don't start with the pattern prefix
        assert is_key_allowed("X_SMTP_HOST") is False
        assert is_key_allowed("MY_STRIPE_KEY") is False


class TestFilterAllowedKeys:
    """Tests for filter_allowed_keys function."""

    def test_partitions_keys(self):
        """Returns tuple of (allowed, rejected) keys."""
        keys = ["REDIS_URL", "SECRET", "DOMAIN", "UNKNOWN"]

        allowed, rejected = filter_allowed_keys(keys)

        assert "REDIS_URL" in allowed
        assert "DOMAIN" in allowed
        assert "SECRET" in rejected
        assert "UNKNOWN" in rejected

    def test_empty_input(self):
        """Handles empty input list."""
        allowed, rejected = filter_allowed_keys([])

        assert allowed == []
        assert rejected == []

    def test_all_allowed(self):
        """When all keys are allowed, rejected is empty."""
        keys = ["REDIS_URL", "DOMAIN", "SMTP_HOST"]

        allowed, rejected = filter_allowed_keys(keys)

        assert len(allowed) == 3
        assert rejected == []

    def test_all_rejected(self):
        """When all keys are rejected, allowed is empty."""
        keys = ["SECRET", "COLONEL", "RANDOM_VAR"]

        allowed, rejected = filter_allowed_keys(keys)

        assert allowed == []
        assert len(rejected) == 3

    def test_preserves_order(self):
        """Keys are returned in input order."""
        keys = ["DOMAIN", "REDIS_URL", "HOST"]

        allowed, rejected = filter_allowed_keys(keys)

        assert allowed == ["DOMAIN", "REDIS_URL", "HOST"]


class TestValidateConfigUpdate:
    """Tests for validate_config_update function."""

    def test_filters_dict_values(self):
        """Returns valid updates dict and rejected keys list."""
        updates = {
            "REDIS_URL": "redis://localhost:6379",
            "SECRET": "should_be_rejected",
            "DOMAIN": "example.com",
        }

        valid, rejected = validate_config_update(updates)

        assert "REDIS_URL" in valid
        assert valid["REDIS_URL"] == "redis://localhost:6379"
        assert "DOMAIN" in valid
        assert "SECRET" not in valid
        assert "SECRET" in rejected

    def test_preserves_values(self):
        """Values are preserved correctly in valid dict."""
        updates = {
            "SMTP_HOST": "smtp.example.com",
            "SMTP_PORT": "587",
            "SSL_ENABLED": "true",
        }

        valid, rejected = validate_config_update(updates)

        assert valid["SMTP_HOST"] == "smtp.example.com"
        assert valid["SMTP_PORT"] == "587"
        assert valid["SSL_ENABLED"] == "true"
        assert rejected == []

    def test_empty_dict(self):
        """Handles empty update dict."""
        valid, rejected = validate_config_update({})

        assert valid == {}
        assert rejected == []

    def test_all_rejected_updates(self):
        """When all updates rejected, valid dict is empty."""
        updates = {
            "SECRET": "value1",
            "COLONEL": "value2",
        }

        valid, rejected = validate_config_update(updates)

        assert valid == {}
        assert len(rejected) == 2


class TestListFunctions:
    """Tests for list_allowed_keys and list_allowed_patterns."""

    def test_list_allowed_keys_sorted(self):
        """list_allowed_keys returns sorted list of exact keys."""
        keys = list_allowed_keys()

        assert isinstance(keys, list)
        assert keys == sorted(keys)
        assert "REDIS_URL" in keys
        assert "DOMAIN" in keys

    def test_list_allowed_keys_matches_constant(self):
        """list_allowed_keys matches ALLOWED_KEYS constant."""
        keys = list_allowed_keys()

        assert set(keys) == ALLOWED_KEYS

    def test_list_allowed_patterns(self):
        """list_allowed_patterns returns pattern list."""
        patterns = list_allowed_patterns()

        assert isinstance(patterns, list)
        assert "SMTP_*" in patterns
        assert "STRIPE_*" in patterns

    def test_list_allowed_patterns_matches_constant(self):
        """list_allowed_patterns matches ALLOWED_PATTERNS constant."""
        patterns = list_allowed_patterns()

        assert patterns == list(ALLOWED_PATTERNS)


class TestAllowlistConstants:
    """Tests for allowlist constants integrity."""

    def test_allowed_keys_not_in_forbidden(self):
        """No overlap between ALLOWED_KEYS and FORBIDDEN_KEYS."""
        overlap = ALLOWED_KEYS & FORBIDDEN_KEYS
        assert overlap == set(), f"Overlapping keys: {overlap}"

    def test_forbidden_keys_exist(self):
        """FORBIDDEN_KEYS contains expected dangerous keys."""
        assert "SECRET" in FORBIDDEN_KEYS
        assert "COLONEL" in FORBIDDEN_KEYS

    def test_patterns_are_valid(self):
        """All patterns end with wildcard."""
        for pattern in ALLOWED_PATTERNS:
            assert pattern.endswith("*"), f"Pattern missing wildcard: {pattern}"
