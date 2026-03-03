# tests/commands/dns/test_helpers.py
"""Tests for DNS helper functions."""

from unittest.mock import patch

import pytest

from rots.commands.dns._helpers import detect_provider, get_public_ip, parse_hostname


class TestParseHostname:
    """Test hostname parsing into (base_domain, subdomain_prefix)."""

    def test_parse_hostname_simple(self):
        """Single subdomain: example.onetime.dev -> ('onetime.dev', 'example')."""
        assert parse_hostname("example.onetime.dev") == ("onetime.dev", "example")

    def test_parse_hostname_nested(self):
        """Nested subdomain: sub.example.onetime.dev -> ('onetime.dev', 'sub.example')."""
        assert parse_hostname("sub.example.onetime.dev") == ("onetime.dev", "sub.example")

    def test_parse_hostname_bare_domain(self):
        """Bare domain: onetime.dev -> ('onetime.dev', '')."""
        assert parse_hostname("onetime.dev") == ("onetime.dev", "")

    def test_parse_hostname_too_short(self):
        """Single label raises ValueError."""
        with pytest.raises(ValueError, match="at least two labels"):
            parse_hostname("localhost")


class TestGetPublicIp:
    """Test public IP detection with mocked HTTP."""

    def test_get_public_ip_success(self):
        """Successful response returns the IP string."""
        mock_resp = type(
            "Resp",
            (),
            {
                "read": lambda _: b"1.2.3.4",
                "__enter__": lambda s: s,
                "__exit__": lambda *_a: None,
            },
        )()
        _target = "rots.commands.dns._helpers.urllib.request.urlopen"
        with patch(_target, return_value=mock_resp):
            assert get_public_ip() == "1.2.3.4"

    def test_get_public_ip_failure(self):
        """Network error returns None."""
        _target = "rots.commands.dns._helpers.urllib.request.urlopen"
        with patch(_target, side_effect=OSError("no network")):
            assert get_public_ip() is None


class TestDetectProvider:
    """Test DNS provider detection from environment."""

    def test_detect_provider_explicit(self, monkeypatch):
        """LEXICON_PROVIDER env var takes precedence."""
        monkeypatch.setenv("LEXICON_PROVIDER", "route53")
        assert detect_provider() == "route53"

    def test_detect_provider_from_token(self, monkeypatch):
        """Native provider env var is detected."""
        monkeypatch.delenv("LEXICON_PROVIDER", raising=False)
        monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "tok_abc")
        assert detect_provider() == "cloudflare"

    def test_detect_provider_none(self, monkeypatch):
        """No relevant env vars returns None."""
        monkeypatch.delenv("LEXICON_PROVIDER", raising=False)
        # Clear all known native provider env vars
        for env_vars in [
            "CLOUDFLARE_API_TOKEN",
            "AWS_ACCESS_KEY_ID",
            "AWS_SECRET_ACCESS_KEY",
            "DO_AUTH_TOKEN",
            "GANDI_API_KEY",
            "GODADDY_API_KEY",
            "GODADDY_API_SECRET",
            "HETZNER_DNS_TOKEN",
            "LINODE_API_TOKEN",
            "NAMECHEAP_API_KEY",
            "PORKBUN_API_KEY",
            "PORKBUN_SECRET_KEY",
            "VULTR_API_KEY",
            "DNSIMPLE_TOKEN",
        ]:
            monkeypatch.delenv(env_vars, raising=False)
        assert detect_provider() is None
