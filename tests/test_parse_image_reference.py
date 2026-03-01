# tests/test_parse_image_reference.py
"""Tests for parse_image_reference().

Verifies the last-colon-after-last-slash rule handles registry ports,
digest references, and edge cases correctly.
"""

import pytest

from ots_containers.config import parse_image_reference


class TestBasicImageTag:
    """Basic image:tag splitting."""

    def test_simple_image_tag(self):
        assert parse_image_reference("myimage:v1.0") == ("myimage", "v1.0")

    def test_image_latest(self):
        assert parse_image_reference("myimage:latest") == ("myimage", "latest")

    def test_image_no_tag(self):
        assert parse_image_reference("myimage") == ("myimage", None)

    def test_image_with_dots_in_tag(self):
        assert parse_image_reference("app:v0.23.1") == ("app", "v0.23.1")

    def test_image_with_hyphens_in_tag(self):
        assert parse_image_reference("app:plop-2") == ("app", "plop-2")

    def test_image_with_underscore_in_tag(self):
        assert parse_image_reference("app:my_tag") == ("app", "my_tag")


class TestRegistryWithPort:
    """Registry:port should not be confused with image:tag."""

    def test_registry_port_org_image_tag(self):
        result = parse_image_reference("registry:5000/org/image:tag")
        assert result == ("registry:5000/org/image", "tag")

    def test_registry_port_image_tag(self):
        result = parse_image_reference("registry:5000/image:v2")
        assert result == ("registry:5000/image", "v2")

    def test_registry_port_image_no_tag(self):
        result = parse_image_reference("registry:5000/image")
        assert result == ("registry:5000/image", None)

    def test_registry_port_deep_path_tag(self):
        result = parse_image_reference("registry:5000/org/sub/image:latest")
        assert result == ("registry:5000/org/sub/image", "latest")

    def test_registry_port_deep_path_no_tag(self):
        result = parse_image_reference("registry:5000/org/sub/image")
        assert result == ("registry:5000/org/sub/image", None)

    def test_localhost_port(self):
        result = parse_image_reference("localhost:5000/myapp:v1")
        assert result == ("localhost:5000/myapp", "v1")


class TestFullyQualifiedImages:
    """Real-world GHCR/Docker Hub references."""

    def test_ghcr_image_tag(self):
        result = parse_image_reference("ghcr.io/onetimesecret/onetimesecret:v0.19.0")
        assert result == ("ghcr.io/onetimesecret/onetimesecret", "v0.19.0")

    def test_ghcr_image_no_tag(self):
        result = parse_image_reference("ghcr.io/onetimesecret/onetimesecret")
        assert result == ("ghcr.io/onetimesecret/onetimesecret", None)

    def test_dockerhub_library_image(self):
        result = parse_image_reference("docker.io/library/nginx:alpine")
        assert result == ("docker.io/library/nginx", "alpine")

    def test_dockerhub_user_image(self):
        result = parse_image_reference("docker.io/user/app:1.2.3")
        assert result == ("docker.io/user/app", "1.2.3")


class TestDigestReferences:
    """Digest (@sha256:...) references."""

    def test_image_sha256_digest(self):
        result = parse_image_reference("image@sha256:abc123def456")
        assert result == ("image", "@sha256:abc123def456")

    def test_registry_image_digest(self):
        result = parse_image_reference(
            "ghcr.io/onetimesecret/onetimesecret@sha256:abcdef1234567890"
        )
        assert result == (
            "ghcr.io/onetimesecret/onetimesecret",
            "@sha256:abcdef1234567890",
        )

    def test_digest_preserves_at_prefix(self):
        """Digest tag starts with @ so callers can distinguish from plain tags."""
        _, tag = parse_image_reference("img@sha256:abc")
        assert tag is not None
        assert tag.startswith("@")

    def test_registry_port_image_digest(self):
        result = parse_image_reference("registry:5000/app@sha256:dead")
        assert result == ("registry:5000/app", "@sha256:dead")


class TestEdgeCases:
    """Edge cases and error handling."""

    def test_empty_string_raises(self):
        with pytest.raises(ValueError, match="must not be empty"):
            parse_image_reference("")

    def test_colon_only_tag(self):
        """image: with empty tag -- colon is after last slash (no slash)."""
        result = parse_image_reference("image:")
        assert result == ("image", "")

    def test_single_component_no_colon(self):
        assert parse_image_reference("onetimesecret") == ("onetimesecret", None)

    def test_image_with_multiple_slashes_and_tag(self):
        result = parse_image_reference("a/b/c/d:tag")
        assert result == ("a/b/c/d", "tag")

    def test_image_with_multiple_slashes_no_tag(self):
        result = parse_image_reference("a/b/c/d")
        assert result == ("a/b/c/d", None)
