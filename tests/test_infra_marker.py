# tests/test_infra_marker.py

"""Tests for ``rots.infra_marker`` — ``.otsinfra.yaml`` parsing + validation.

The bootstrap command (`rots env bootstrap`) consumes ``envs.<env>`` out of
the marker file. These tests focus on the parsing seams — especially the
``web.ip`` field which must be a real IP address (v4 or v6) with no CR/LF
injection, because that value flows directly into ``pg_hba.conf``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from rots.infra_marker import (
    InfraMarkerError,
    load_env_config_from_file,
)

pytestmark = pytest.mark.quick


# --- helpers -------------------------------------------------------------


def _write_marker(
    path: Path,
    *,
    env: str = "eu-demo",
    db_host_id: str = "eu-demo-db",
    web_host_id: str = "eu-demo-web",
    web_ip: str = "10.0.0.5",
    app_name: str = "onetimesecret",
    app_owner: str = "onetimesecret",
    valkey_rules: tuple[str, ...] = ("+@read", "+@write"),
) -> Path:
    """Write a minimal valid ``.otsinfra.yaml`` to *path* and return it.

    Uses hand-built YAML (no yaml dump) so IP strings carrying ``\\n`` and
    other adversarial payloads round-trip intact. The ``web.ip`` value is
    emitted as a double-quoted scalar so embedded newlines are preserved
    by PyYAML's parser.
    """
    # Escape the value for a YAML double-quoted scalar. PyYAML's own emitter
    # would quote/escape differently; we do it by hand so a test can inject
    # exactly the bytes it wants.
    escaped_ip = web_ip.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    rules_block = "\n".join(f'      - "{r}"' for r in valkey_rules)
    text = (
        "envs:\n"
        f"  {env}:\n"
        "    db:\n"
        f"      host_id: {db_host_id}\n"
        "    web:\n"
        f"      host_id: {web_host_id}\n"
        f'      ip: "{escaped_ip}"\n'
        "    app:\n"
        f"      name: {app_name}\n"
        f"      owner_role: {app_owner}\n"
        "    valkey:\n"
        "      rules:\n"
        f"{rules_block}\n"
    )
    path.write_text(text, encoding="utf-8")
    return path


# --- happy path ----------------------------------------------------------


class TestWebIpValid:
    """Valid IPv4 / IPv6 strings load without error."""

    def test_ipv4_loads(self, tmp_path: Path):
        marker = _write_marker(tmp_path / ".otsinfra.yaml", web_ip="10.0.0.5")
        cfg = load_env_config_from_file("eu-demo", marker)
        assert cfg.web.ip == "10.0.0.5"

    def test_ipv6_loads(self, tmp_path: Path):
        marker = _write_marker(tmp_path / ".otsinfra.yaml", web_ip="2001:db8::1")
        cfg = load_env_config_from_file("eu-demo", marker)
        assert cfg.web.ip == "2001:db8::1"

    def test_ipv4_loopback_loads(self, tmp_path: Path):
        # Loopback is a legal IPv4 address; accepted. Policy (rejecting
        # loopback as a web peer) is a caller concern, not parser concern.
        marker = _write_marker(tmp_path / ".otsinfra.yaml", web_ip="127.0.0.1")
        cfg = load_env_config_from_file("eu-demo", marker)
        assert cfg.web.ip == "127.0.0.1"


# --- rejection: newline injection ---------------------------------------


class TestWebIpNewlineInjection:
    """CR/LF in ``web.ip`` is refused — the value flows into pg_hba.conf.

    A newline here would let the caller append arbitrary pg_hba rules
    (e.g. a blanket ``host all all 0.0.0.0/0 trust``). The validator
    rejects before ``ipaddress.ip_address()`` even runs.
    """

    def test_lf_injection_rejected(self, tmp_path: Path):
        marker = _write_marker(
            tmp_path / ".otsinfra.yaml",
            web_ip="10.0.0.5\nhost all all 0.0.0.0/0 trust",
        )
        with pytest.raises(InfraMarkerError) as excinfo:
            load_env_config_from_file("eu-demo", marker)
        assert "web.ip" in str(excinfo.value)

    def test_cr_injection_rejected(self, tmp_path: Path):
        # Bare CR in addition to LF — POSIX-style injectors sometimes
        # use \r\n. Must also fail.
        marker = _write_marker(
            tmp_path / ".otsinfra.yaml",
            web_ip="10.0.0.5\r\nhost all all 0.0.0.0/0 trust",
        )
        with pytest.raises(InfraMarkerError) as excinfo:
            load_env_config_from_file("eu-demo", marker)
        assert "web.ip" in str(excinfo.value)


# --- rejection: not an IP address ---------------------------------------


class TestWebIpNotAnIp:
    """Free-form strings that are not valid addresses are refused."""

    def test_garbage_text_rejected(self, tmp_path: Path):
        marker = _write_marker(tmp_path / ".otsinfra.yaml", web_ip="not-an-ip")
        with pytest.raises(InfraMarkerError) as excinfo:
            load_env_config_from_file("eu-demo", marker)
        assert "web.ip" in str(excinfo.value)

    def test_hostname_rejected(self, tmp_path: Path):
        # A DNS name is a common mistake and must fail — the value is
        # consumed literally by pg_hba which does its own name resolution
        # policy; the bootstrap command constrains to IPs only.
        marker = _write_marker(tmp_path / ".otsinfra.yaml", web_ip="example.com")
        with pytest.raises(InfraMarkerError) as excinfo:
            load_env_config_from_file("eu-demo", marker)
        assert "web.ip" in str(excinfo.value)

    def test_cidr_block_rejected(self, tmp_path: Path):
        # A CIDR expression contains ``/``; ``ipaddress.ip_address()``
        # rejects it (use ip_network() for that). Good — pg_hba builds
        # the /32 itself.
        marker = _write_marker(tmp_path / ".otsinfra.yaml", web_ip="10.0.0.5/32")
        with pytest.raises(InfraMarkerError) as excinfo:
            load_env_config_from_file("eu-demo", marker)
        assert "web.ip" in str(excinfo.value)

    def test_malformed_ipv4_rejected(self, tmp_path: Path):
        marker = _write_marker(tmp_path / ".otsinfra.yaml", web_ip="999.0.0.1")
        with pytest.raises(InfraMarkerError) as excinfo:
            load_env_config_from_file("eu-demo", marker)
        assert "web.ip" in str(excinfo.value)


# --- rejection: empty / missing -----------------------------------------


class TestWebIpEmpty:
    """Empty string fails the ``_require_str`` pre-check; error still mentions web.ip.

    The exact message wording is owned by ``_require_str`` ("must be a
    non-empty string"), but the dotted path it builds includes ``web.ip``,
    so callers can still surface which field was bad.
    """

    def test_empty_string_rejected(self, tmp_path: Path):
        # Empty-string YAML scalar: ``ip: ""``.
        marker = _write_marker(tmp_path / ".otsinfra.yaml", web_ip="")
        with pytest.raises(InfraMarkerError) as excinfo:
            load_env_config_from_file("eu-demo", marker)
        # Path must be identifiable regardless of exact message.
        assert "web.ip" in str(excinfo.value)

    def test_whitespace_only_rejected(self, tmp_path: Path):
        # Pure whitespace fails the ``not value.strip()`` guard in
        # ``_require_str`` — same ``web.ip`` dotted path in the error.
        marker = _write_marker(tmp_path / ".otsinfra.yaml", web_ip="   ")
        with pytest.raises(InfraMarkerError) as excinfo:
            load_env_config_from_file("eu-demo", marker)
        assert "web.ip" in str(excinfo.value)
