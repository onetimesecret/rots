# tests/sidecar/test_allowlist.py

"""Tests for src/rots/sidecar/allowlist.py

Covers:
- is_key_allowed with denylist approach (everything allowed except forbidden)
- filter_allowed_keys partitioning
- validate_config_update dict filtering
- list_forbidden_keys and list_forbidden_patterns
"""

from rots.sidecar.allowlist import (
    FORBIDDEN_KEYS,
    FORBIDDEN_PATTERNS,
    filter_allowed_keys,
    is_key_allowed,
    list_forbidden_keys,
    list_forbidden_patterns,
    validate_config_update,
)


class TestIsKeyAllowed:
    """Tests for is_key_allowed function."""

    # Allowed keys tests (denylist approach - most things are allowed)
    def test_general_keys_allowed(self):
        """Most configuration keys are allowed."""
        assert is_key_allowed("DOMAIN") is True
        assert is_key_allowed("SSL_ENABLED") is True
        assert is_key_allowed("HOST") is True
        assert is_key_allowed("PORT") is True

    def test_smtp_keys_allowed(self):
        """SMTP configuration keys are allowed."""
        assert is_key_allowed("SMTP_HOST") is True
        assert is_key_allowed("SMTP_PORT") is True
        assert is_key_allowed("SMTP_USERNAME") is True
        assert is_key_allowed("SMTP_PASSWORD") is True

    def test_stripe_keys_allowed(self):
        """Stripe configuration keys are allowed."""
        assert is_key_allowed("STRIPE_API_KEY") is True
        assert is_key_allowed("STRIPE_WEBHOOK_SIGNING_SECRET") is True
        assert is_key_allowed("STRIPE_PUBLISHABLE_KEY") is True

    def test_custom_keys_allowed(self):
        """Custom/unknown keys are allowed (denylist approach)."""
        assert is_key_allowed("MY_CUSTOM_VAR") is True
        assert is_key_allowed("RANDOM_KEY") is True
        assert is_key_allowed("NEW_FEATURE_FLAG") is True

    def test_case_insensitive(self):
        """Key matching is case-insensitive (normalized to uppercase)."""
        assert is_key_allowed("domain") is True
        assert is_key_allowed("Domain") is True
        assert is_key_allowed("DOMAIN") is True

    def test_whitespace_stripped(self):
        """Whitespace is stripped from keys."""
        assert is_key_allowed("  DOMAIN  ") is True
        assert is_key_allowed("\tHOST\n") is True

    # Forbidden keys tests
    def test_root_secret_forbidden(self):
        """SECRET (root encryption key) is forbidden."""
        assert is_key_allowed("SECRET") is False
        assert is_key_allowed("secret") is False

    def test_derived_secrets_allowed(self):
        """Derived secrets are allowed (they derive from SECRET which is forbidden)."""
        # These are derived via HKDF from SECRET - if SECRET can't change,
        # these can't be compromised. Allowing them enables key rotation.
        assert is_key_allowed("SESSION_SECRET") is True
        assert is_key_allowed("IDENTIFIER_SECRET") is True
        assert is_key_allowed("AUTH_SECRET") is True
        assert is_key_allowed("ARGON2_SECRET") is True
        assert is_key_allowed("FEDERATION_SECRET") is True

    def test_database_urls_forbidden(self):
        """Database connection URLs are forbidden."""
        assert is_key_allowed("REDIS_URL") is False
        assert is_key_allowed("VALKEY_URL") is False
        assert is_key_allowed("AUTH_DATABASE_URL") is False
        assert is_key_allowed("AUTH_DATABASE_URL_MIGRATIONS") is False

    def test_colonel_forbidden(self):
        """COLONEL (admin access) is forbidden."""
        assert is_key_allowed("COLONEL") is False

    def test_forbidden_case_insensitive(self):
        """Forbidden check is case-insensitive."""
        assert is_key_allowed("secret") is False
        assert is_key_allowed("Secret") is False
        assert is_key_allowed("colonel") is False
        assert is_key_allowed("Colonel") is False


class TestFilterAllowedKeys:
    """Tests for filter_allowed_keys function."""

    def test_partitions_keys(self):
        """Returns tuple of (allowed, rejected) keys."""
        keys = ["DOMAIN", "SECRET", "HOST", "COLONEL"]

        allowed, rejected = filter_allowed_keys(keys)

        assert "DOMAIN" in allowed
        assert "HOST" in allowed
        assert "SECRET" in rejected
        assert "COLONEL" in rejected

    def test_empty_input(self):
        """Handles empty input list."""
        allowed, rejected = filter_allowed_keys([])

        assert allowed == []
        assert rejected == []

    def test_all_allowed(self):
        """When all keys are allowed, rejected is empty."""
        keys = ["DOMAIN", "HOST", "SMTP_HOST", "STRIPE_KEY"]

        allowed, rejected = filter_allowed_keys(keys)

        assert len(allowed) == 4
        assert rejected == []

    def test_all_rejected(self):
        """When all keys are forbidden, allowed is empty."""
        keys = ["SECRET", "COLONEL", "REDIS_URL"]

        allowed, rejected = filter_allowed_keys(keys)

        assert allowed == []
        assert len(rejected) == 3

    def test_preserves_order(self):
        """Keys are returned in input order."""
        keys = ["HOST", "DOMAIN", "PORT"]

        allowed, rejected = filter_allowed_keys(keys)

        assert allowed == ["HOST", "DOMAIN", "PORT"]


class TestValidateConfigUpdate:
    """Tests for validate_config_update function."""

    def test_filters_dict_values(self):
        """Returns valid updates dict and rejected keys list."""
        updates = {
            "DOMAIN": "example.com",
            "SECRET": "should_be_rejected",
            "HOST": "0.0.0.0",
        }

        valid, rejected = validate_config_update(updates)

        assert "DOMAIN" in valid
        assert valid["DOMAIN"] == "example.com"
        assert "HOST" in valid
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
        """When all updates forbidden, valid dict is empty."""
        updates = {
            "SECRET": "value1",
            "COLONEL": "value2",
        }

        valid, rejected = validate_config_update(updates)

        assert valid == {}
        assert len(rejected) == 2


class TestListFunctions:
    """Tests for list_forbidden_keys and list_forbidden_patterns."""

    def test_list_forbidden_keys_sorted(self):
        """list_forbidden_keys returns sorted list."""
        keys = list_forbidden_keys()

        assert isinstance(keys, list)
        assert keys == sorted(keys)
        assert "SECRET" in keys
        assert "COLONEL" in keys

    def test_list_forbidden_keys_matches_constant(self):
        """list_forbidden_keys matches FORBIDDEN_KEYS constant."""
        keys = list_forbidden_keys()

        assert set(keys) == FORBIDDEN_KEYS

    def test_list_forbidden_patterns(self):
        """list_forbidden_patterns returns pattern list."""
        patterns = list_forbidden_patterns()

        assert isinstance(patterns, list)
        # Currently empty, but should be a list
        assert patterns == list(FORBIDDEN_PATTERNS)


class TestForbiddenConstants:
    """Tests for forbidden constants integrity."""

    def test_forbidden_keys_minimal(self):
        """FORBIDDEN_KEYS contains only essential secrets."""
        # Should be a small set (secrets + db connections + colonel)
        assert len(FORBIDDEN_KEYS) <= 15

    def test_forbidden_keys_exist(self):
        """FORBIDDEN_KEYS contains expected dangerous keys."""
        # Root secret only (derived secrets are allowed - they derive from this)
        assert "SECRET" in FORBIDDEN_KEYS
        # Database connections
        assert "REDIS_URL" in FORBIDDEN_KEYS
        assert "VALKEY_URL" in FORBIDDEN_KEYS
        assert "AUTH_DATABASE_URL" in FORBIDDEN_KEYS
        # Admin
        assert "COLONEL" in FORBIDDEN_KEYS

    def test_patterns_valid_if_present(self):
        """Any forbidden patterns should end with wildcard."""
        for pattern in FORBIDDEN_PATTERNS:
            assert pattern.endswith("*"), f"Pattern missing wildcard: {pattern}"
