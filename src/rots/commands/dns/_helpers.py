# src/rots/commands/dns/_helpers.py

"""Helper functions for DNS record management via dns-lexicon."""

from __future__ import annotations

import logging
import os
import urllib.error
import urllib.request

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Provider environment variable hints
# ---------------------------------------------------------------------------

# Native env vars recommended by each provider's own API docs.
# These are what operators actually set — NOT lexicon-specific names.
PROVIDER_ENV_HINTS: dict[str, list[str]] = {
    "cloudflare": ["CLOUDFLARE_API_TOKEN"],
    "route53": ["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"],
    "digitalocean": ["DO_AUTH_TOKEN"],
    "gandi": ["GANDI_API_KEY"],
    "godaddy": ["GODADDY_API_KEY", "GODADDY_API_SECRET"],
    "hetzner": ["HETZNER_DNS_TOKEN"],
    "linode": ["LINODE_API_TOKEN"],
    "namecheap": ["NAMECHEAP_API_KEY"],
    "porkbun": ["PORKBUN_API_KEY", "PORKBUN_SECRET_KEY"],
    "vultr": ["VULTR_API_KEY"],
    "dnsimple": ["DNSIMPLE_TOKEN"],
}

# Map native env vars → lexicon env vars.
# Lexicon reads LEXICON_{PROVIDER}_{OPTION} where OPTION maps to
# the CLI --auth-* flags (e.g. --auth-token → AUTH_TOKEN).
_NATIVE_TO_LEXICON: dict[str, str] = {
    "CLOUDFLARE_API_TOKEN": "LEXICON_CLOUDFLARE_AUTH_TOKEN",
    "AWS_ACCESS_KEY_ID": "LEXICON_ROUTE53_AUTH_ACCESS_KEY",
    "AWS_SECRET_ACCESS_KEY": "LEXICON_ROUTE53_AUTH_ACCESS_SECRET",
    "DO_AUTH_TOKEN": "LEXICON_DIGITALOCEAN_AUTH_TOKEN",
    "GANDI_API_KEY": "LEXICON_GANDI_AUTH_TOKEN",
    "GODADDY_API_KEY": "LEXICON_GODADDY_AUTH_KEY",
    "GODADDY_API_SECRET": "LEXICON_GODADDY_AUTH_SECRET",
    "HETZNER_DNS_TOKEN": "LEXICON_HETZNER_AUTH_TOKEN",
    "LINODE_API_TOKEN": "LEXICON_LINODE_AUTH_TOKEN",
    "NAMECHEAP_API_KEY": "LEXICON_NAMECHEAP_AUTH_TOKEN",
    "PORKBUN_API_KEY": "LEXICON_PORKBUN_AUTH_KEY",
    "PORKBUN_SECRET_KEY": "LEXICON_PORKBUN_AUTH_SECRET",
    "VULTR_API_KEY": "LEXICON_VULTR_AUTH_TOKEN",
    "DNSIMPLE_TOKEN": "LEXICON_DNSIMPLE_AUTH_TOKEN",
}


def _bridge_env_vars() -> None:
    """Copy native provider env vars to lexicon's expected names.

    This lets operators set ``CLOUDFLARE_API_TOKEN`` (per Cloudflare's
    own docs) instead of ``LEXICON_CLOUDFLARE_AUTH_TOKEN``.  Existing
    lexicon env vars are not overwritten.
    """
    for native, lexicon_key in _NATIVE_TO_LEXICON.items():
        val = os.environ.get(native)
        if val and not os.environ.get(lexicon_key):
            os.environ[lexicon_key] = val


# ---------------------------------------------------------------------------
# Public IP detection
# ---------------------------------------------------------------------------


def get_public_ip() -> str | None:
    """Detect the current machine's public IPv4 address.

    Uses api.ipify.org which returns the IP as plain text.
    Returns None if detection fails.
    """
    try:
        with urllib.request.urlopen("https://api.ipify.org", timeout=5) as resp:
            ip = resp.read().decode("ascii").strip()
            logger.debug("Detected public IP: %s", ip)
            return ip
    except (urllib.error.URLError, OSError) as exc:
        logger.warning("Failed to detect public IP: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Domain parsing
# ---------------------------------------------------------------------------


def parse_hostname(hostname: str) -> tuple[str, str]:
    """Split a hostname into (base_domain, subdomain_prefix).

    The base domain is the last two labels (TLD + second-level domain).

    Examples:
        >>> parse_hostname("example.onetime.dev")
        ('onetime.dev', 'example')
        >>> parse_hostname("sub.example.onetime.dev")
        ('onetime.dev', 'sub.example')
        >>> parse_hostname("onetime.dev")
        ('onetime.dev', '')

    Args:
        hostname: Fully qualified hostname.

    Returns:
        Tuple of (base_domain, subdomain_prefix). The prefix is empty
        when the hostname *is* the base domain.

    Raises:
        ValueError: If the hostname has fewer than two labels.
    """
    parts = hostname.rstrip(".").split(".")
    if len(parts) < 2:
        raise ValueError(f"hostname must have at least two labels: {hostname!r}")

    base_domain = ".".join(parts[-2:])
    prefix = ".".join(parts[:-2])
    return base_domain, prefix


# ---------------------------------------------------------------------------
# Provider detection
# ---------------------------------------------------------------------------


def detect_provider() -> str | None:
    """Detect the DNS provider from environment variables.

    Checks ``LEXICON_PROVIDER`` first (explicit override), then probes
    for native provider env vars (e.g. ``CLOUDFLARE_API_TOKEN``,
    ``AWS_ACCESS_KEY_ID``) defined in :data:`PROVIDER_ENV_HINTS`.

    Returns:
        Provider name string (e.g. ``"cloudflare"``), or None.
    """
    explicit = os.environ.get("LEXICON_PROVIDER")
    if explicit:
        logger.debug("Provider set explicitly via LEXICON_PROVIDER=%s", explicit)
        return explicit.lower()

    for provider, env_vars in PROVIDER_ENV_HINTS.items():
        if any(os.environ.get(v) for v in env_vars):
            logger.debug("Detected provider %s from env vars", provider)
            return provider

    logger.debug("No DNS provider detected from environment")
    return None


# ---------------------------------------------------------------------------
# DNS operations wrapper
# ---------------------------------------------------------------------------


class DnsClient:
    """Thin wrapper around dns-lexicon for DNS record operations.

    Reads provider credentials from environment variables automatically
    via ``ConfigResolver.with_env()``.

    Args:
        provider: Lexicon provider name (e.g. ``"cloudflare"``).
        domain: Base domain to manage (e.g. ``"onetime.dev"``).
        ttl: Default TTL for new records, in seconds.
    """

    def __init__(self, provider: str, domain: str, ttl: int = 300) -> None:
        self.provider = provider
        self.domain = domain
        self.ttl = ttl
        _bridge_env_vars()

    def _make_config(self):
        """Build a lexicon ConfigResolver for provider auth."""
        from lexicon.config import ConfigResolver  # type: ignore[import-untyped]

        return (
            ConfigResolver()
            .with_env()
            .with_dict(
                {
                    "provider_name": self.provider,
                    "domain": self.domain,
                }
            )
        )

    def list_records(
        self,
        record_type: str,
        name: str | None = None,
    ) -> list[dict]:
        """List DNS records, optionally filtered by name.

        Args:
            record_type: Record type (``"A"``, ``"CNAME"``, etc.).
            name: Optional name filter.

        Returns:
            List of record dicts as returned by lexicon.
        """
        from lexicon.client import Client  # type: ignore[import-untyped]

        config = self._make_config()
        try:
            with Client(config) as ops:
                records = ops.list_records(
                    record_type,
                    name or None,
                    None,
                )
                return records if isinstance(records, list) else []
        except Exception:
            logger.exception(
                "Failed to list %s records for %s",
                record_type,
                self.domain,
            )
            return []

    def add_record(
        self,
        record_type: str,
        name: str,
        content: str,
    ) -> bool:
        """Create a DNS record.

        Returns:
            True on success, False on failure.
        """
        from lexicon.client import Client  # type: ignore[import-untyped]

        config = self._make_config()
        try:
            with Client(config) as ops:
                ops.create_record(record_type, name, content)
                logger.info(
                    "Created %s record: %s -> %s",
                    record_type,
                    name,
                    content,
                )
                return True
        except Exception:
            logger.exception(
                "Failed to create %s record %s -> %s",
                record_type,
                name,
                content,
            )
            return False

    def update_record(
        self,
        record_type: str,
        name: str,
        content: str,
    ) -> bool:
        """Update an existing DNS record.

        Finds existing records matching the type and name, then updates
        the first match. Falls back to create if none found.

        Returns:
            True on success, False on failure.
        """
        from lexicon.client import Client  # type: ignore[import-untyped]

        existing = self.list_records(record_type, name=name)
        if not existing:
            logger.info(
                "No existing %s record for %s, creating instead",
                record_type,
                name,
            )
            return self.add_record(record_type, name, content)

        identifier = existing[0].get("id")
        config = self._make_config()
        try:
            with Client(config) as ops:
                ops.update_record(
                    identifier,
                    record_type,
                    name,
                    content,
                )
                logger.info(
                    "Updated %s record: %s -> %s",
                    record_type,
                    name,
                    content,
                )
                return True
        except Exception:
            logger.exception(
                "Failed to update %s record %s -> %s",
                record_type,
                name,
                content,
            )
            return False

    def delete_record(
        self,
        record_type: str,
        name: str,
        content: str | None = None,
    ) -> bool:
        """Delete a DNS record.

        Args:
            record_type: Record type.
            name: Record name.
            content: Optional content filter. If None, deletes the
                first matching record by type and name.

        Returns:
            True on success, False on failure.
        """
        from lexicon.client import Client  # type: ignore[import-untyped]

        existing = self.list_records(record_type, name=name)
        if not existing:
            logger.warning(
                "No %s record found for %s to delete",
                record_type,
                name,
            )
            return False

        target = existing[0]
        if content:
            matches = [r for r in existing if r.get("content") == content]
            if not matches:
                logger.warning(
                    "No %s record for %s with content %s",
                    record_type,
                    name,
                    content,
                )
                return False
            target = matches[0]

        identifier = target.get("id")
        config = self._make_config()
        try:
            with Client(config) as ops:
                ops.delete_record(
                    identifier,
                    record_type,
                    name,
                    content or "",
                )
                logger.info(
                    "Deleted %s record: %s",
                    record_type,
                    name,
                )
                return True
        except Exception:
            logger.exception(
                "Failed to delete %s record %s",
                record_type,
                name,
            )
            return False
