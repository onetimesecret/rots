# tests/commands/host/test_manifest.py

"""Tests for manifest parsing and resolution."""

from pathlib import Path

import pytest

from rots.commands.host._manifest import (
    ManifestEntry,
    default_manifest,
    parse_manifest,
    resolve_manifest,
)


class TestParseManifest:
    def test_basic_manifest(self, tmp_path):
        manifest = tmp_path / "manifest.conf"
        manifest.write_text(
            "config.yaml     /etc/onetimesecret/config.yaml\n"
            "auth.yaml       /etc/onetimesecret/auth.yaml\n"
            ".env            /etc/default/onetimesecret\n"
        )
        entries = parse_manifest(manifest)
        assert len(entries) == 3
        assert entries[0] == ManifestEntry("config.yaml", Path("/etc/onetimesecret/config.yaml"))
        assert entries[1] == ManifestEntry("auth.yaml", Path("/etc/onetimesecret/auth.yaml"))
        assert entries[2] == ManifestEntry(".env", Path("/etc/default/onetimesecret"))

    def test_comments_and_blank_lines(self, tmp_path):
        manifest = tmp_path / "manifest.conf"
        manifest.write_text(
            "# This is a comment\n"
            "\n"
            "config.yaml /etc/onetimesecret/config.yaml\n"
            "  # Another comment\n"
            "\n"
        )
        entries = parse_manifest(manifest)
        assert len(entries) == 1

    def test_malformed_line_raises(self, tmp_path):
        manifest = tmp_path / "manifest.conf"
        manifest.write_text("config.yaml\n")
        with pytest.raises(ValueError, match="expected"):
            parse_manifest(manifest)

    def test_file_not_found_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            parse_manifest(tmp_path / "nonexistent.conf")

    def test_extra_whitespace(self, tmp_path):
        manifest = tmp_path / "manifest.conf"
        manifest.write_text("  config.yaml   /etc/onetimesecret/config.yaml  \n")
        entries = parse_manifest(manifest)
        assert len(entries) == 1
        assert entries[0].local_name == "config.yaml"


class TestDefaultManifest:
    def test_includes_config_files(self):
        entries = default_manifest()
        names = [e.local_name for e in entries]
        assert "config.yaml" in names
        assert "auth.yaml" in names
        assert "logging.yaml" in names
        assert "billing.yaml" in names
        assert "Caddyfile.template" in names
        assert "puma.rb" in names

    def test_includes_env(self):
        entries = default_manifest()
        names = [e.local_name for e in entries]
        assert ".env" in names

    def test_includes_valkey_conf(self):
        entries = default_manifest()
        names = [e.local_name for e in entries]
        assert "valkey.conf" in names

    def test_valkey_conf_maps_to_etc_valkey(self):
        entries = default_manifest()
        valkey_entry = next(e for e in entries if e.local_name == "valkey.conf")
        assert str(valkey_entry.remote_path) == "/etc/valkey/valkey.conf"

    def test_includes_caddyfile_template(self):
        entries = default_manifest()
        names = [e.local_name for e in entries]
        assert "Caddyfile.template" in names

    def test_env_maps_to_etc_default(self):
        entries = default_manifest()
        env_entry = next(e for e in entries if e.local_name == ".env")
        assert str(env_entry.remote_path) == "/etc/default/onetimesecret"


class TestResolveManifest:
    def test_uses_manifest_when_present(self, tmp_path):
        manifest = tmp_path / "manifest.conf"
        manifest.write_text("custom.yaml /etc/custom/custom.yaml\n")
        entries = resolve_manifest(tmp_path)
        assert len(entries) == 1
        assert entries[0].local_name == "custom.yaml"

    def test_falls_back_to_defaults(self, tmp_path):
        entries = resolve_manifest(tmp_path)
        # Should return default manifest entries
        assert len(entries) > 0
        names = [e.local_name for e in entries]
        assert "config.yaml" in names
