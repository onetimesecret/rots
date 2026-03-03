# tests/commands/dns/test_helpers.py
"""Tests for DNS helper functions."""

import os
from unittest.mock import MagicMock, patch

import pytest

from rots.commands.dns._helpers import (
    DnsClient,
    _bridge_env_vars,
    detect_provider,
    get_public_ip,
    parse_hostname,
)


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

    def test_detect_provider_explicit_uppercased(self, monkeypatch):
        """LEXICON_PROVIDER value is lowercased before being returned."""
        monkeypatch.setenv("LEXICON_PROVIDER", "Cloudflare")
        assert detect_provider() == "cloudflare"

    def test_detect_provider_explicit_all_caps(self, monkeypatch):
        """LEXICON_PROVIDER in all-caps is lowercased correctly."""
        monkeypatch.setenv("LEXICON_PROVIDER", "ROUTE53")
        assert detect_provider() == "route53"


class TestBridgeEnvVars:
    """Test that _bridge_env_vars maps native provider env vars to lexicon names."""

    def test_copies_native_to_lexicon(self, monkeypatch):
        """Native CLOUDFLARE_API_TOKEN is copied to LEXICON_CLOUDFLARE_AUTH_TOKEN."""
        monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "my-cf-token")
        monkeypatch.delenv("LEXICON_CLOUDFLARE_AUTH_TOKEN", raising=False)

        _bridge_env_vars()

        assert os.environ.get("LEXICON_CLOUDFLARE_AUTH_TOKEN") == "my-cf-token"

    def test_does_not_overwrite_existing_lexicon_var(self, monkeypatch):
        """Pre-existing LEXICON_* vars are not overwritten by the native equivalent."""
        monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "native-token")
        monkeypatch.setenv("LEXICON_CLOUDFLARE_AUTH_TOKEN", "already-set")

        _bridge_env_vars()

        assert os.environ.get("LEXICON_CLOUDFLARE_AUTH_TOKEN") == "already-set"

    def test_copies_multiple_native_vars(self, monkeypatch):
        """Multi-key providers (e.g. GoDaddy) have both native vars bridged."""
        monkeypatch.setenv("GODADDY_API_KEY", "gd-key")
        monkeypatch.setenv("GODADDY_API_SECRET", "gd-secret")
        monkeypatch.delenv("LEXICON_GODADDY_AUTH_KEY", raising=False)
        monkeypatch.delenv("LEXICON_GODADDY_AUTH_SECRET", raising=False)

        _bridge_env_vars()

        assert os.environ.get("LEXICON_GODADDY_AUTH_KEY") == "gd-key"
        assert os.environ.get("LEXICON_GODADDY_AUTH_SECRET") == "gd-secret"

    def test_skips_absent_native_var(self, monkeypatch):
        """When the native var is absent, the lexicon var is not created."""
        monkeypatch.delenv("DO_AUTH_TOKEN", raising=False)
        monkeypatch.delenv("LEXICON_DIGITALOCEAN_AUTH_TOKEN", raising=False)

        _bridge_env_vars()

        assert os.environ.get("LEXICON_DIGITALOCEAN_AUTH_TOKEN") is None


class TestParseHostnameTrailingDot:
    """Test that parse_hostname strips a trailing dot before splitting."""

    def test_trailing_dot_stripped(self):
        """FQDN with trailing dot is handled the same as without."""
        assert parse_hostname("example.onetime.dev.") == ("onetime.dev", "example")

    def test_bare_domain_trailing_dot(self):
        """Bare domain with trailing dot returns empty prefix."""
        assert parse_hostname("onetime.dev.") == ("onetime.dev", "")

    def test_too_short_after_strip(self):
        """A single label with trailing dot still raises ValueError."""
        with pytest.raises(ValueError, match="at least two labels"):
            parse_hostname("localhost.")


class TestDnsClient:
    """Test DnsClient wraps lexicon's Client correctly.

    lexicon is an optional runtime dependency that is not installed in the
    test environment.  Each test injects fake ``lexicon.client`` and
    ``lexicon.config`` modules into ``sys.modules`` for the duration of the
    test, then removes them so subsequent tests start clean.
    """

    @pytest.fixture()
    def fake_lexicon(self, monkeypatch):
        """Inject minimal lexicon stubs into sys.modules.

        Yields a dict with ``mock_ops`` (the object returned inside the
        ``with Client(...) as ops:`` block) so tests can inspect calls.
        """
        import sys
        import types

        mock_ops = MagicMock()

        # Build a context-manager-compatible Client mock.
        mock_client_instance = MagicMock()
        mock_client_instance.__enter__ = MagicMock(return_value=mock_ops)
        mock_client_instance.__exit__ = MagicMock(return_value=False)

        mock_client_cls = MagicMock(return_value=mock_client_instance)

        # Stub lexicon.client
        fake_client_mod = types.ModuleType("lexicon.client")
        fake_client_mod.Client = mock_client_cls  # type: ignore[attr-defined]

        # Stub lexicon.config — ConfigResolver().with_env().with_dict(...)
        # must return something without erroring.
        fake_resolver = MagicMock()
        fake_resolver.with_env.return_value = fake_resolver
        fake_resolver.with_dict.return_value = fake_resolver

        fake_config_cls = MagicMock(return_value=fake_resolver)
        fake_config_mod = types.ModuleType("lexicon.config")
        fake_config_mod.ConfigResolver = fake_config_cls  # type: ignore[attr-defined]

        # Stub the parent lexicon package too (needed for sub-module resolution)
        fake_lexicon_pkg = types.ModuleType("lexicon")

        monkeypatch.setitem(sys.modules, "lexicon", fake_lexicon_pkg)
        monkeypatch.setitem(sys.modules, "lexicon.client", fake_client_mod)
        monkeypatch.setitem(sys.modules, "lexicon.config", fake_config_mod)

        yield {
            "mock_ops": mock_ops,
            "mock_client_cls": mock_client_cls,
        }

    def test_add_record_calls_create_record(self, fake_lexicon, monkeypatch):
        """add_record delegates to lexicon Client.create_record with correct args."""
        monkeypatch.delenv("CLOUDFLARE_API_TOKEN", raising=False)
        monkeypatch.delenv("LEXICON_CLOUDFLARE_AUTH_TOKEN", raising=False)

        client = DnsClient("cloudflare", "onetime.dev", ttl=300)
        result = client.add_record("A", "example", "1.2.3.4")

        assert result is True
        fake_lexicon["mock_ops"].create_record.assert_called_once_with("A", "example", "1.2.3.4")

    def test_add_record_returns_false_on_exception(self, fake_lexicon, monkeypatch):
        """add_record catches exceptions from lexicon and returns False."""
        monkeypatch.delenv("CLOUDFLARE_API_TOKEN", raising=False)
        fake_lexicon["mock_ops"].create_record.side_effect = RuntimeError("API error")

        client = DnsClient("cloudflare", "onetime.dev")
        result = client.add_record("A", "example", "1.2.3.4")

        assert result is False

    def test_constructor_calls_bridge_env_vars(self, monkeypatch):
        """DnsClient.__init__ triggers _bridge_env_vars to map native env vars.

        This test does not need to call into lexicon; it only checks the
        side-effect that happens during construction.
        """
        monkeypatch.setenv("HETZNER_DNS_TOKEN", "htz-token")
        monkeypatch.delenv("LEXICON_HETZNER_AUTH_TOKEN", raising=False)

        DnsClient("hetzner", "onetime.dev")

        assert os.environ.get("LEXICON_HETZNER_AUTH_TOKEN") == "htz-token"
