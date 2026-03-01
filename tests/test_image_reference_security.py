# tests/test_image_reference_security.py
"""Security tests for image reference handling.

Verifies that shell metacharacters, path traversal, newline injection,
and other malicious inputs are rejected by Config.validate() and the
regex patterns in config.py.
"""

import pytest

from ots_containers.config import (
    CPU_QUOTA_RE,
    IMAGE_RE,
    MEMORY_MAX_RE,
    REGISTRY_RE,
    SYSTEMD_UNIT_RE,
    TAG_RE,
    Config,
)


class TestTagRegexSecurity:
    """TAG_RE should reject dangerous inputs."""

    @pytest.mark.parametrize(
        "bad_tag",
        [
            "",
            " ",
            "v1.0; rm -rf /",
            "tag\nnewline",
            "tag\x00null",
            "$(whoami)",
            "`id`",
            "tag|pipe",
            "tag&background",
            "tag>redirect",
            "tag<redirect",
            "../../../etc/passwd",
            "a" * 200,  # exceeds 128 char limit
        ],
    )
    def test_tag_rejects_shell_metacharacters(self, bad_tag):
        assert not TAG_RE.match(bad_tag), f"TAG_RE should reject {bad_tag!r}"

    @pytest.mark.parametrize(
        "good_tag",
        [
            "v0.24.0",
            "latest",
            "sha-abc1234",
            "my_tag.1-rc2",
            "@current",
            "@rollback",
        ],
    )
    def test_tag_accepts_valid_tags(self, good_tag):
        assert TAG_RE.match(good_tag), f"TAG_RE should accept {good_tag!r}"


class TestImageRegexSecurity:
    """IMAGE_RE should reject dangerous inputs."""

    @pytest.mark.parametrize(
        "bad_image",
        [
            "",
            " ",
            "image; rm -rf /",
            "image\nnewline",
            "image\x00null",
            "$(whoami)/image",
            "`id`/image",
            "image|pipe",
            "image&background",
            "../../../etc/passwd",
            "registry/../../../image",
            "image with spaces",
            "a" * 300,  # exceeds 255 char limit
        ],
    )
    def test_image_rejects_shell_metacharacters(self, bad_image):
        assert not IMAGE_RE.match(bad_image), f"IMAGE_RE should reject {bad_image!r}"

    def test_image_rejects_path_traversal(self):
        """Double-dot sequences should be rejected by negative lookahead."""
        assert not IMAGE_RE.match("registry/../image")
        assert not IMAGE_RE.match("../image")
        assert not IMAGE_RE.match("image/../../etc/passwd")

    @pytest.mark.parametrize(
        "good_image",
        [
            "onetimesecret",
            "ghcr.io/onetimesecret/onetimesecret",
            "registry.example.com/org/image",
            "localhost/myimage",
            "my-registry.io/my-org/my-image",
        ],
    )
    def test_image_accepts_valid_references(self, good_image):
        assert IMAGE_RE.match(good_image), f"IMAGE_RE should accept {good_image!r}"


class TestMemoryMaxRegexSecurity:
    """MEMORY_MAX_RE should reject injection attempts."""

    @pytest.mark.parametrize(
        "bad_value",
        [
            "",
            "1G\nExecStart=/bin/sh",
            "1G; rm -rf /",
            "$(whoami)",
            "1G 2G",
            "-1G",
            "1G\x00",
        ],
    )
    def test_memory_max_rejects_injection(self, bad_value):
        assert not MEMORY_MAX_RE.match(bad_value), f"MEMORY_MAX_RE should reject {bad_value!r}"

    @pytest.mark.parametrize(
        "good_value",
        [
            "512M",
            "1G",
            "2048",
            "infinity",
            "256K",
            "4T",
        ],
    )
    def test_memory_max_accepts_valid_values(self, good_value):
        assert MEMORY_MAX_RE.match(good_value), f"MEMORY_MAX_RE should accept {good_value!r}"


class TestCpuQuotaRegexSecurity:
    """CPU_QUOTA_RE should reject injection attempts."""

    @pytest.mark.parametrize(
        "bad_value",
        [
            "",
            "80%\nExecStart=/bin/sh",
            "80%; rm -rf /",
            "$(whoami)%",
            "80",  # missing percent
            "-80%",
            "80% 90%",
            "999999%",  # exceeds 5-digit limit
        ],
    )
    def test_cpu_quota_rejects_injection(self, bad_value):
        assert not CPU_QUOTA_RE.match(bad_value), f"CPU_QUOTA_RE should reject {bad_value!r}"

    @pytest.mark.parametrize(
        "good_value",
        [
            "80%",
            "150%",
            "100%",
            "1%",
            "99999%",
        ],
    )
    def test_cpu_quota_accepts_valid_values(self, good_value):
        assert CPU_QUOTA_RE.match(good_value), f"CPU_QUOTA_RE should accept {good_value!r}"


class TestSystemdUnitRegexSecurity:
    """SYSTEMD_UNIT_RE should reject injection attempts."""

    @pytest.mark.parametrize(
        "bad_value",
        [
            "",
            "unit\nExecStart=/bin/sh",
            "unit; rm -rf /",
            "$(whoami).service",
            "unit with spaces",
            "unit\x00null",
        ],
    )
    def test_systemd_unit_rejects_injection(self, bad_value):
        assert not SYSTEMD_UNIT_RE.match(bad_value), f"SYSTEMD_UNIT_RE should reject {bad_value!r}"

    @pytest.mark.parametrize(
        "good_value",
        [
            "valkey-server@6379.service",
            "redis.service",
            "my-unit@instance.service",
        ],
    )
    def test_systemd_unit_accepts_valid_names(self, good_value):
        assert SYSTEMD_UNIT_RE.match(good_value), f"SYSTEMD_UNIT_RE should accept {good_value!r}"


class TestConfigValidate:
    """Config.validate() should catch all invalid inputs."""

    def test_validate_rejects_bad_tag(self, monkeypatch):
        monkeypatch.delenv("IMAGE", raising=False)
        monkeypatch.delenv("TAG", raising=False)
        monkeypatch.delenv("MEMORY_MAX", raising=False)
        monkeypatch.delenv("CPU_QUOTA", raising=False)
        monkeypatch.delenv("OTS_VALKEY_SERVICE", raising=False)
        monkeypatch.delenv("OTS_REGISTRY", raising=False)
        import dataclasses

        cfg = Config()
        with pytest.raises(ValueError, match="Invalid tag"):
            dataclasses.replace(cfg, tag="$(whoami)")

    def test_validate_rejects_bad_image(self, monkeypatch):
        monkeypatch.delenv("IMAGE", raising=False)
        monkeypatch.delenv("TAG", raising=False)
        monkeypatch.delenv("MEMORY_MAX", raising=False)
        monkeypatch.delenv("CPU_QUOTA", raising=False)
        monkeypatch.delenv("OTS_VALKEY_SERVICE", raising=False)
        monkeypatch.delenv("OTS_REGISTRY", raising=False)
        import dataclasses

        cfg = Config()
        with pytest.raises(ValueError, match="Invalid image"):
            dataclasses.replace(cfg, image="../../../etc/passwd")

    def test_validate_rejects_bad_memory_max(self, monkeypatch):
        monkeypatch.delenv("IMAGE", raising=False)
        monkeypatch.delenv("TAG", raising=False)
        monkeypatch.delenv("MEMORY_MAX", raising=False)
        monkeypatch.delenv("CPU_QUOTA", raising=False)
        monkeypatch.delenv("OTS_VALKEY_SERVICE", raising=False)
        monkeypatch.delenv("OTS_REGISTRY", raising=False)
        import dataclasses

        cfg = Config()
        with pytest.raises(ValueError, match="Invalid MEMORY_MAX"):
            dataclasses.replace(cfg, memory_max="1G\nExecStart=/bin/sh")

    def test_validate_rejects_bad_cpu_quota(self, monkeypatch):
        monkeypatch.delenv("IMAGE", raising=False)
        monkeypatch.delenv("TAG", raising=False)
        monkeypatch.delenv("MEMORY_MAX", raising=False)
        monkeypatch.delenv("CPU_QUOTA", raising=False)
        monkeypatch.delenv("OTS_VALKEY_SERVICE", raising=False)
        monkeypatch.delenv("OTS_REGISTRY", raising=False)
        import dataclasses

        cfg = Config()
        with pytest.raises(ValueError, match="Invalid CPU_QUOTA"):
            dataclasses.replace(cfg, cpu_quota="80%; rm -rf /")

    def test_validate_rejects_bad_valkey_service(self, monkeypatch):
        monkeypatch.delenv("IMAGE", raising=False)
        monkeypatch.delenv("TAG", raising=False)
        monkeypatch.delenv("MEMORY_MAX", raising=False)
        monkeypatch.delenv("CPU_QUOTA", raising=False)
        monkeypatch.delenv("OTS_VALKEY_SERVICE", raising=False)
        monkeypatch.delenv("OTS_REGISTRY", raising=False)
        import dataclasses

        cfg = Config()
        with pytest.raises(ValueError, match="Invalid OTS_VALKEY_SERVICE"):
            dataclasses.replace(cfg, valkey_service="unit\nExecStart=/bin/sh")

    def test_validate_passes_for_defaults(self, monkeypatch):
        """Default config values should pass validation."""
        monkeypatch.delenv("IMAGE", raising=False)
        monkeypatch.delenv("TAG", raising=False)
        monkeypatch.delenv("MEMORY_MAX", raising=False)
        monkeypatch.delenv("CPU_QUOTA", raising=False)
        monkeypatch.delenv("OTS_VALKEY_SERVICE", raising=False)
        monkeypatch.delenv("OTS_REGISTRY", raising=False)
        cfg = Config()
        # Should not raise
        cfg.validate()

    def test_validate_rejects_bad_registry(self, monkeypatch):
        """OTS_REGISTRY with path traversal should be rejected at construction time."""
        monkeypatch.setenv("OTS_REGISTRY", "registry/../evil")
        monkeypatch.delenv("IMAGE", raising=False)
        monkeypatch.delenv("TAG", raising=False)
        monkeypatch.delenv("MEMORY_MAX", raising=False)
        monkeypatch.delenv("CPU_QUOTA", raising=False)
        monkeypatch.delenv("OTS_VALKEY_SERVICE", raising=False)
        with pytest.raises(ValueError, match="Invalid OTS_REGISTRY"):
            Config()

    def test_validate_is_called_on_construction(self, monkeypatch):
        """validate() is called in __post_init__ -- bad values are rejected at construction time."""
        monkeypatch.setenv("TAG", "$(whoami)")
        monkeypatch.delenv("IMAGE", raising=False)
        monkeypatch.delenv("MEMORY_MAX", raising=False)
        monkeypatch.delenv("CPU_QUOTA", raising=False)
        monkeypatch.delenv("OTS_VALKEY_SERVICE", raising=False)
        monkeypatch.delenv("OTS_REGISTRY", raising=False)
        with pytest.raises(ValueError, match="Invalid tag"):
            Config()


class TestRegistryRegexSecurity:
    """REGISTRY_RE must reject path traversal and shell metacharacters."""

    @pytest.mark.parametrize(
        "bad_registry",
        [
            "registry/../evil",
            "../registry",
            "a..b/image",
            "registry;rm -rf /",
            "registry$(whoami)",
            "registry\nevil",
            " leading-space",
            "",
        ],
    )
    def test_rejects_bad_registries(self, bad_registry):
        assert not REGISTRY_RE.match(bad_registry), f"Should reject: {bad_registry!r}"

    @pytest.mark.parametrize(
        "good_registry",
        [
            "registry.example.com",
            "registry:5000",
            "ghcr.io/org",
            "localhost:5000/path",
        ],
    )
    def test_accepts_valid_registries(self, good_registry):
        assert REGISTRY_RE.match(good_registry), f"Should accept: {good_registry!r}"
