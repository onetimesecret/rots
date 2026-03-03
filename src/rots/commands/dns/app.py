# src/rots/commands/dns/app.py

"""DNS record management commands via dns-lexicon.

Manages DNS records for OTS instances using provider APIs (Cloudflare,
Route53, DigitalOcean, etc.) through the dns-lexicon library. Tracks
record state in the local SQLite database for audit and quick lookup.
"""

from __future__ import annotations

import json
import logging
from typing import Annotated

import cyclopts

from ..common import EXIT_FAILURE, EXIT_PRECOND, DryRun, JsonOutput, Yes
from ._helpers import (
    PROVIDER_ENV_HINTS,
    DnsClient,
    detect_provider,
    get_public_ip,
    parse_hostname,
)

logger = logging.getLogger(__name__)

app = cyclopts.App(
    name="dns",
    help="Manage DNS records via provider API",
)

# Type aliases for cyclopts annotations
Hostname = Annotated[
    str,
    cyclopts.Parameter(help="Fully qualified hostname (e.g. example.onetime.dev)"),
]

IpAddress = Annotated[
    str | None,
    cyclopts.Parameter(
        name=["--ip", "-a"],
        help="IP address (auto-detected if not given)",
    ),
]

RecordType = Annotated[
    str,
    cyclopts.Parameter(
        name=["--type", "-t"],
        help="DNS record type",
    ),
]

Ttl = Annotated[
    int,
    cyclopts.Parameter(
        name=["--ttl"],
        help="TTL in seconds",
    ),
]

Provider = Annotated[
    str | None,
    cyclopts.Parameter(
        name=["--provider", "-p"],
        help="DNS provider (auto-detected from env vars if not given)",
    ),
]


def _get_executor():
    """Resolve executor from context. Returns None for local."""
    from rots import context
    from rots.config import Config

    cfg = Config()
    host = context.host_var.get(None)
    if host is None:
        return None
    return cfg.get_executor(host=host)


def _db_path():
    """Resolve the database path from config."""
    from rots.config import Config

    cfg = Config()
    return cfg.db_path


def _provider_hint_message() -> str:
    """Build a help message listing known provider env vars."""
    lines = ["Set one of the following environment variables:"]
    for provider, env_vars in PROVIDER_ENV_HINTS.items():
        lines.append(f"  {provider}: {', '.join(env_vars)}")
    lines.append("Or set LEXICON_PROVIDER explicitly.")
    return "\n".join(lines)


def _resolve_provider(explicit: str | None) -> str | None:
    """Return provider from explicit flag or auto-detection."""
    if explicit:
        return explicit
    return detect_provider()


def _resolve_ip(explicit: str | None) -> str | None:
    """Return IP from explicit flag or auto-detection."""
    if explicit:
        return explicit
    return get_public_ip()


@app.default
def list_records(json_output: JsonOutput = False):
    """List all managed DNS records.

    Shows records tracked in the local database (dns_current table).

    Examples:
        rots dns
        rots dns list
        rots dns --json
    """
    from rots.db import get_all_dns_current, init_db

    db = _db_path()
    ex = _get_executor()
    init_db(db, executor=ex)

    records = get_all_dns_current(db, executor=ex)

    if json_output:
        data = [
            {
                "hostname": r.hostname,
                "type": r.record_type,
                "value": r.value,
                "ttl": r.ttl,
                "provider": r.provider,
                "updated_at": r.updated_at,
            }
            for r in records
        ]
        print(json.dumps(data, indent=2))
        return

    if not records:
        print("No DNS records found.")
        print()
        print("Add one with: rots dns add <hostname>")
        return

    print("DNS records:")
    print("-" * 90)
    print(f"{'HOSTNAME':<30} {'TYPE':<6} {'VALUE':<20} {'TTL':<6} {'PROVIDER':<12} {'UPDATED':<20}")
    print("-" * 90)

    for r in records:
        ttl_str = str(r.ttl) if r.ttl is not None else "-"
        print(
            f"{r.hostname:<30} {r.record_type:<6} {r.value:<20} "
            f"{ttl_str:<6} {r.provider:<12} {r.updated_at:<20}"
        )


@app.command
def add(
    hostname: Hostname,
    *,
    ip: IpAddress = None,
    type: RecordType = "A",
    ttl: Ttl = 300,
    provider: Provider = None,
    dry_run: DryRun = False,
    json_output: JsonOutput = False,
):
    """Create a DNS record for a hostname.

    Auto-detects public IP and DNS provider from environment if not
    specified explicitly.

    Examples:
        rots dns add example.onetime.dev
        rots dns add example.onetime.dev --ip 1.2.3.4
        rots dns add example.onetime.dev --provider cloudflare --ttl 600
        rots dns add example.onetime.dev --dry-run
    """
    from rots.db import get_dns_current, init_db, record_dns_action, upsert_dns_current

    db = _db_path()
    ex = _get_executor()
    init_db(db, executor=ex)

    # Check if record already exists
    existing = get_dns_current(db, hostname, executor=ex)
    if existing:
        print(
            f"Record already exists for {hostname}. Use 'rots dns update {hostname}' to modify it."
        )
        raise SystemExit(EXIT_PRECOND)

    # Resolve IP
    resolved_ip = _resolve_ip(ip)
    if not resolved_ip:
        print("Could not detect public IP. Specify with --ip")
        raise SystemExit(EXIT_PRECOND)

    # Resolve provider
    resolved_provider = _resolve_provider(provider)
    if not resolved_provider:
        print("No DNS provider detected.")
        print()
        print(_provider_hint_message())
        raise SystemExit(EXIT_PRECOND)

    base_domain, name = parse_hostname(hostname)

    if dry_run:
        print(f"[dry-run] Would create {type} record:")
        print(f"  Hostname: {hostname}")
        print(f"  Type:     {type}")
        print(f"  Value:    {resolved_ip}")
        print(f"  TTL:      {ttl}")
        print(f"  Provider: {resolved_provider}")
        print(f"  Domain:   {base_domain}")
        if name:
            print(f"  Name:     {name}")
        return

    # Create via lexicon
    client = DnsClient(resolved_provider, base_domain, ttl=ttl)
    success = client.add_record(type, name or hostname, resolved_ip)

    if not success:
        print(f"Failed to create DNS record for {hostname}")
        record_dns_action(
            db,
            hostname,
            type,
            resolved_ip,
            ttl,
            resolved_provider,
            "add",
            success=False,
            executor=ex,
        )
        raise SystemExit(EXIT_FAILURE)

    # Record in db
    upsert_dns_current(db, hostname, type, resolved_ip, ttl, resolved_provider, executor=ex)
    record_dns_action(
        db,
        hostname,
        type,
        resolved_ip,
        ttl,
        resolved_provider,
        "add",
        success=True,
        executor=ex,
    )

    if json_output:
        print(
            json.dumps(
                {
                    "hostname": hostname,
                    "type": type,
                    "value": resolved_ip,
                    "ttl": ttl,
                    "provider": resolved_provider,
                    "action": "add",
                },
                indent=2,
            )
        )
    else:
        print(f"Created {type} record: {hostname} -> {resolved_ip}")
        print(f"  TTL:      {ttl}s")
        print(f"  Provider: {resolved_provider}")


@app.command
def show(
    hostname: Hostname,
    *,
    json_output: JsonOutput = False,
):
    """Show details for a DNS record.

    Displays current state and recent history from the local database.

    Examples:
        rots dns show example.onetime.dev
        rots dns show example.onetime.dev --json
    """
    from rots.db import get_dns_current, get_dns_history, init_db

    db = _db_path()
    ex = _get_executor()
    init_db(db, executor=ex)

    current = get_dns_current(db, hostname, executor=ex)

    if not current:
        print(f"No DNS record found for {hostname}. Use 'rots dns add {hostname}' to create one.")
        return

    if json_output:
        history = get_dns_history(db, hostname, limit=10, executor=ex)
        data = {
            "current": {
                "hostname": current.hostname,
                "type": current.record_type,
                "value": current.value,
                "ttl": current.ttl,
                "provider": current.provider,
                "updated_at": current.updated_at,
            },
            "history": [
                {
                    "timestamp": r.timestamp,
                    "type": r.record_type,
                    "value": r.value,
                    "ttl": r.ttl,
                    "provider": r.provider,
                    "action": r.action,
                    "success": r.success,
                    "notes": r.notes,
                }
                for r in history
            ],
        }
        print(json.dumps(data, indent=2))
        return

    print(f"DNS record for {hostname}:")
    print(f"  Type:     {current.record_type}")
    print(f"  Value:    {current.value}")
    print(f"  TTL:      {current.ttl}s" if current.ttl is not None else "  TTL:      -")
    print(f"  Provider: {current.provider}")
    print(f"  Updated:  {current.updated_at}")

    # Show recent history
    history = get_dns_history(db, hostname, limit=10, executor=ex)
    if history:
        print()
        print("Recent history:")
        for r in history:
            status = "ok" if r.success else "FAIL"
            notes = f"  ({r.notes})" if r.notes else ""
            print(f"  {r.timestamp}  {r.action:<8} {r.record_type} -> {r.value}  [{status}]{notes}")

    print()
    print(f"To modify: rots dns update {hostname} --ip <new-ip>")


@app.command
def update(
    hostname: Hostname,
    *,
    ip: IpAddress = None,
    type: RecordType = "A",
    ttl: Ttl = 300,
    provider: Provider = None,
    dry_run: DryRun = False,
    json_output: JsonOutput = False,
):
    """Update a DNS record (upsert: creates if not found).

    When updating an existing record, shows the old and new values.

    Examples:
        rots dns update example.onetime.dev --ip 5.6.7.8
        rots dns update example.onetime.dev --ttl 3600
        rots dns update example.onetime.dev --dry-run
    """
    from rots.db import get_dns_current, init_db, record_dns_action, upsert_dns_current

    db = _db_path()
    ex = _get_executor()
    init_db(db, executor=ex)

    # Resolve IP
    resolved_ip = _resolve_ip(ip)
    if not resolved_ip:
        print("Could not detect public IP. Specify with --ip")
        raise SystemExit(EXIT_PRECOND)

    # Resolve provider
    resolved_provider = _resolve_provider(provider)
    if not resolved_provider:
        print("No DNS provider detected.")
        print()
        print(_provider_hint_message())
        raise SystemExit(EXIT_PRECOND)

    existing = get_dns_current(db, hostname, executor=ex)
    base_domain, name = parse_hostname(hostname)

    if dry_run:
        action = "update" if existing else "create"
        print(f"[dry-run] Would {action} {type} record:")
        print(f"  Hostname: {hostname}")
        print(f"  Type:     {type}")
        print(f"  Value:    {resolved_ip}")
        print(f"  TTL:      {ttl}")
        print(f"  Provider: {resolved_provider}")
        if existing:
            print()
            print("Current values:")
            print(f"  Value:    {existing.value}")
            print(f"  TTL:      {existing.ttl}")
        return

    # Update or create via lexicon
    client = DnsClient(resolved_provider, base_domain, ttl=ttl)
    if existing:
        success = client.update_record(type, name or hostname, resolved_ip)
    else:
        success = client.add_record(type, name or hostname, resolved_ip)

    action = "update" if existing else "add"

    if not success:
        print(f"Failed to {action} DNS record for {hostname}")
        record_dns_action(
            db,
            hostname,
            type,
            resolved_ip,
            ttl,
            resolved_provider,
            action,
            success=False,
            executor=ex,
        )
        raise SystemExit(EXIT_FAILURE)

    # Record in db
    notes = None
    if existing and existing.value != resolved_ip:
        notes = f"Changed from {existing.value}"

    upsert_dns_current(db, hostname, type, resolved_ip, ttl, resolved_provider, executor=ex)
    record_dns_action(
        db,
        hostname,
        type,
        resolved_ip,
        ttl,
        resolved_provider,
        action,
        success=True,
        notes=notes,
        executor=ex,
    )

    if json_output:
        data = {
            "hostname": hostname,
            "type": type,
            "value": resolved_ip,
            "ttl": ttl,
            "provider": resolved_provider,
            "action": action,
        }
        if existing:
            data["previous_value"] = existing.value
        print(json.dumps(data, indent=2))
    else:
        if existing:
            print(f"Updated {type} record: {hostname}")
            if existing.value != resolved_ip:
                print(f"  Value: {existing.value} -> {resolved_ip}")
            if existing.ttl != ttl:
                print(f"  TTL:   {existing.ttl} -> {ttl}")
            if existing.provider != resolved_provider:
                print(f"  Provider: {existing.provider} -> {resolved_provider}")
        else:
            print(f"Created {type} record: {hostname} -> {resolved_ip}")
            print(f"  TTL:      {ttl}s")
            print(f"  Provider: {resolved_provider}")


@app.command
def remove(
    hostname: Hostname,
    *,
    yes: Yes = False,
    dry_run: DryRun = False,
):
    """Remove a DNS record.

    Deletes the record from the provider and removes it from local tracking.

    Examples:
        rots dns remove example.onetime.dev
        rots dns remove example.onetime.dev --yes
        rots dns remove example.onetime.dev --dry-run
    """
    from rots.db import (
        delete_dns_current,
        get_dns_current,
        init_db,
        record_dns_action,
    )

    db = _db_path()
    ex = _get_executor()
    init_db(db, executor=ex)

    existing = get_dns_current(db, hostname, executor=ex)
    if not existing:
        print(f"No DNS record found for {hostname}.")
        raise SystemExit(EXIT_PRECOND)

    if dry_run:
        print(f"[dry-run] Would remove {existing.record_type} record:")
        print(f"  Hostname: {hostname}")
        print(f"  Value:    {existing.value}")
        print(f"  Provider: {existing.provider}")
        return

    if not yes:
        response = input(
            f"Remove DNS record for {hostname} ({existing.record_type} -> {existing.value})? [y/N] "
        )
        if response.lower() not in ("y", "yes"):
            print("Aborted")
            return

    base_domain, name = parse_hostname(hostname)
    client = DnsClient(existing.provider, base_domain)
    success = client.delete_record(existing.record_type, name or hostname, existing.value)

    if not success:
        print(f"Failed to delete DNS record for {hostname}")
        record_dns_action(
            db,
            hostname,
            existing.record_type,
            existing.value,
            existing.ttl,
            existing.provider,
            "remove",
            success=False,
            executor=ex,
        )
        raise SystemExit(EXIT_FAILURE)

    # Remove from current tracking and record audit
    delete_dns_current(db, hostname, executor=ex)
    record_dns_action(
        db,
        hostname,
        existing.record_type,
        existing.value,
        existing.ttl,
        existing.provider,
        "remove",
        success=True,
        executor=ex,
    )

    print(f"Removed {existing.record_type} record: {hostname} ({existing.value})")
