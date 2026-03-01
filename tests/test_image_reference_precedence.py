# tests/test_image_reference_precedence.py
"""Tests for image reference precedence chain.

Verifies the precedence order:
  1. Positional reference arg (parsed image + tag) -- highest
  2. --tag flag -- tag only override
  3. IMAGE / TAG env vars -- via Config defaults
  4. @current / @rollback DB alias -- via cfg.resolve_image_tag()
  5. DEFAULT_IMAGE / DEFAULT_TAG -- lowest
"""

import dataclasses

import pytest

from ots_containers.config import DEFAULT_IMAGE, DEFAULT_TAG, Config, parse_image_reference


def _apply_reference_overrides(cfg, reference=None, tag_flag=None):
    """Apply the standard command-level override pattern from the design doc.

    This mirrors the pattern used in commands like deploy, shell, etc:
        ref_image, ref_tag = parse_image_reference(reference) if reference else (None, None)
        override_tag = ref_tag or tag_flag
        if ref_image or override_tag:
            cfg = dataclasses.replace(
                cfg, image=ref_image or cfg.image, tag=override_tag or cfg.tag
            )
    """
    ref_image, ref_tag = parse_image_reference(reference) if reference else (None, None)
    override_tag = ref_tag or tag_flag
    if ref_image or override_tag:
        cfg = dataclasses.replace(
            cfg,
            image=ref_image or cfg.image,
            tag=override_tag or cfg.tag,
        )
    return cfg


class TestTagPrecedence:
    """Verify tag precedence: positional ref > --tag flag > TAG env > DEFAULT_TAG."""

    def test_default_tag_when_nothing_set(self, monkeypatch):
        """With no overrides, tag should be DEFAULT_TAG."""
        monkeypatch.delenv("TAG", raising=False)
        monkeypatch.delenv("IMAGE", raising=False)
        cfg = Config()
        cfg = _apply_reference_overrides(cfg)
        assert cfg.tag == DEFAULT_TAG

    def test_tag_env_overrides_default(self, monkeypatch):
        """TAG env var should override DEFAULT_TAG."""
        monkeypatch.setenv("TAG", "v0.23.0")
        monkeypatch.delenv("IMAGE", raising=False)
        cfg = Config()
        cfg = _apply_reference_overrides(cfg)
        assert cfg.tag == "v0.23.0"

    def test_tag_flag_overrides_env(self, monkeypatch):
        """--tag flag should override TAG env var."""
        monkeypatch.setenv("TAG", "v0.23.0")
        monkeypatch.delenv("IMAGE", raising=False)
        cfg = Config()
        cfg = _apply_reference_overrides(cfg, tag_flag="v0.24.0")
        assert cfg.tag == "v0.24.0"

    def test_positional_ref_tag_overrides_flag(self, monkeypatch):
        """Positional ref tag should override --tag flag."""
        monkeypatch.setenv("TAG", "v0.23.0")
        monkeypatch.delenv("IMAGE", raising=False)
        cfg = Config()
        cfg = _apply_reference_overrides(
            cfg,
            reference="ghcr.io/org/app:v0.25.0",
            tag_flag="v0.24.0",
        )
        assert cfg.tag == "v0.25.0"

    def test_positional_ref_tag_overrides_env(self, monkeypatch):
        """Positional ref tag should override TAG env var."""
        monkeypatch.setenv("TAG", "v0.23.0")
        monkeypatch.delenv("IMAGE", raising=False)
        cfg = Config()
        cfg = _apply_reference_overrides(cfg, reference="app:v0.25.0")
        assert cfg.tag == "v0.25.0"

    def test_tag_flag_overrides_default(self, monkeypatch):
        """--tag flag should override DEFAULT_TAG when no env is set."""
        monkeypatch.delenv("TAG", raising=False)
        monkeypatch.delenv("IMAGE", raising=False)
        cfg = Config()
        cfg = _apply_reference_overrides(cfg, tag_flag="v0.24.0")
        assert cfg.tag == "v0.24.0"


class TestImagePrecedence:
    """Verify image precedence: positional ref > IMAGE env > DEFAULT_IMAGE."""

    def test_default_image_when_nothing_set(self, monkeypatch):
        """With no overrides, image should be DEFAULT_IMAGE."""
        monkeypatch.delenv("IMAGE", raising=False)
        monkeypatch.delenv("TAG", raising=False)
        cfg = Config()
        cfg = _apply_reference_overrides(cfg)
        assert cfg.image == DEFAULT_IMAGE

    def test_image_env_overrides_default(self, monkeypatch):
        """IMAGE env var should override DEFAULT_IMAGE."""
        monkeypatch.setenv("IMAGE", "docker.io/org/myapp")
        monkeypatch.delenv("TAG", raising=False)
        cfg = Config()
        cfg = _apply_reference_overrides(cfg)
        assert cfg.image == "docker.io/org/myapp"

    def test_positional_ref_image_overrides_env(self, monkeypatch):
        """Positional ref image should override IMAGE env var."""
        monkeypatch.setenv("IMAGE", "docker.io/org/myapp")
        monkeypatch.delenv("TAG", raising=False)
        cfg = Config()
        cfg = _apply_reference_overrides(cfg, reference="registry:5000/custom/img:v1")
        assert cfg.image == "registry:5000/custom/img"

    def test_positional_ref_image_overrides_default(self, monkeypatch):
        """Positional ref image should override DEFAULT_IMAGE."""
        monkeypatch.delenv("IMAGE", raising=False)
        monkeypatch.delenv("TAG", raising=False)
        cfg = Config()
        cfg = _apply_reference_overrides(cfg, reference="custom/image:v1")
        assert cfg.image == "custom/image"


class TestCombinedPrecedence:
    """Verify combined image+tag precedence with multiple override sources."""

    def test_positional_beats_all(self, monkeypatch):
        """Positional ref should override both IMAGE/TAG env and --tag flag."""
        monkeypatch.setenv("IMAGE", "docker.io/org/envimage")
        monkeypatch.setenv("TAG", "env-tag")
        cfg = Config()
        cfg = _apply_reference_overrides(
            cfg,
            reference="registry:5000/pos/image:pos-tag",
            tag_flag="flag-tag",
        )
        assert cfg.image == "registry:5000/pos/image"
        assert cfg.tag == "pos-tag"

    def test_flag_tag_with_env_image(self, monkeypatch):
        """--tag flag + IMAGE env: flag overrides TAG env, IMAGE env preserved."""
        monkeypatch.setenv("IMAGE", "docker.io/org/envimage")
        monkeypatch.setenv("TAG", "env-tag")
        cfg = Config()
        cfg = _apply_reference_overrides(cfg, tag_flag="flag-tag")
        assert cfg.image == "docker.io/org/envimage"
        assert cfg.tag == "flag-tag"

    def test_positional_image_only_preserves_flag_tag(self, monkeypatch):
        """Positional ref without tag uses --tag flag for tag."""
        monkeypatch.delenv("IMAGE", raising=False)
        monkeypatch.delenv("TAG", raising=False)
        cfg = Config()
        cfg = _apply_reference_overrides(
            cfg,
            reference="custom/image",
            tag_flag="flag-tag",
        )
        assert cfg.image == "custom/image"
        assert cfg.tag == "flag-tag"

    def test_positional_image_only_preserves_env_tag(self, monkeypatch):
        """Positional ref without tag preserves TAG env var."""
        monkeypatch.delenv("IMAGE", raising=False)
        monkeypatch.setenv("TAG", "env-tag")
        cfg = Config()
        # reference has no tag, no --tag flag
        cfg = _apply_reference_overrides(cfg, reference="custom/image")
        assert cfg.image == "custom/image"
        # No ref_tag and no flag, so cfg.tag stays as the env value
        assert cfg.tag == "env-tag"

    def test_no_overrides_preserves_env(self, monkeypatch):
        """No positional or flag should preserve IMAGE/TAG env vars."""
        monkeypatch.setenv("IMAGE", "docker.io/org/envimage")
        monkeypatch.setenv("TAG", "env-tag")
        cfg = Config()
        cfg = _apply_reference_overrides(cfg)
        assert cfg.image == "docker.io/org/envimage"
        assert cfg.tag == "env-tag"


class TestDataclassesReplaceImmutability:
    """Verify dataclasses.replace creates a new Config, not mutating the original."""

    def test_replace_does_not_mutate_original(self, monkeypatch):
        """dataclasses.replace should not modify the original Config."""
        monkeypatch.delenv("IMAGE", raising=False)
        monkeypatch.delenv("TAG", raising=False)
        cfg = Config()
        original_image = cfg.image
        original_tag = cfg.tag

        new_cfg = _apply_reference_overrides(cfg, reference="new/image:new-tag")

        assert cfg.image == original_image
        assert cfg.tag == original_tag
        assert new_cfg.image == "new/image"
        assert new_cfg.tag == "new-tag"

    def test_replace_preserves_other_fields(self, monkeypatch):
        """dataclasses.replace should preserve fields not being overridden."""
        monkeypatch.delenv("IMAGE", raising=False)
        monkeypatch.delenv("TAG", raising=False)
        cfg = Config()
        new_cfg = _apply_reference_overrides(cfg, reference="new/image:v1")

        assert new_cfg.config_dir == cfg.config_dir
        assert new_cfg.var_dir == cfg.var_dir
        assert new_cfg.web_template_path == cfg.web_template_path


class TestDigestPrecedence:
    """Verify digest references work correctly in the precedence chain."""

    def test_digest_ref_overrides_tag_flag(self, monkeypatch):
        """Digest in positional ref should override --tag flag."""
        monkeypatch.delenv("IMAGE", raising=False)
        monkeypatch.delenv("TAG", raising=False)
        cfg = Config()
        cfg = _apply_reference_overrides(
            cfg,
            reference="ghcr.io/org/app@sha256:abc123",
            tag_flag="v0.24.0",
        )
        assert cfg.image == "ghcr.io/org/app"
        assert cfg.tag == "@sha256:abc123"

    def test_digest_ref_overrides_env_tag(self, monkeypatch):
        """Digest in positional ref should override TAG env var."""
        monkeypatch.setenv("TAG", "env-tag")
        monkeypatch.delenv("IMAGE", raising=False)
        cfg = Config()
        cfg = _apply_reference_overrides(
            cfg,
            reference="app@sha256:deadbeef",
        )
        assert cfg.image == "app"
        assert cfg.tag == "@sha256:deadbeef"


@pytest.mark.parametrize(
    "reference,tag_flag,image_env,tag_env,expected_image,expected_tag",
    [
        # Nothing set -> defaults
        (None, None, None, None, DEFAULT_IMAGE, DEFAULT_TAG),
        # TAG env only
        (None, None, None, "v0.23.0", DEFAULT_IMAGE, "v0.23.0"),
        # IMAGE env only
        (None, None, "custom/img", None, "custom/img", DEFAULT_TAG),
        # Both env vars
        (None, None, "custom/img", "v0.23.0", "custom/img", "v0.23.0"),
        # --tag flag overrides TAG env
        (None, "flag-tag", None, "env-tag", DEFAULT_IMAGE, "flag-tag"),
        # --tag flag overrides TAG env, IMAGE env preserved
        (None, "flag-tag", "custom/img", "env-tag", "custom/img", "flag-tag"),
        # Positional ref with tag overrides everything
        (
            "registry:5000/pos/img:pos-tag",
            "flag-tag",
            "env/img",
            "env-tag",
            "registry:5000/pos/img",
            "pos-tag",
        ),
        # Positional ref image-only, --tag flag for tag
        (
            "registry:5000/pos/img",
            "flag-tag",
            "env/img",
            "env-tag",
            "registry:5000/pos/img",
            "flag-tag",
        ),
        # Positional ref image-only, no --tag flag, TAG env preserved
        ("pos/img", None, "env/img", "env-tag", "pos/img", "env-tag"),
        # Digest ref overrides tag flag
        ("app@sha256:abc", "flag-tag", None, None, "app", "@sha256:abc"),
    ],
    ids=[
        "all-defaults",
        "tag-env-only",
        "image-env-only",
        "both-env-vars",
        "flag-overrides-env-tag",
        "flag-overrides-env-tag-with-image-env",
        "positional-overrides-all",
        "positional-image-flag-tag",
        "positional-image-env-tag",
        "digest-overrides-flag",
    ],
)
def test_precedence_matrix(
    monkeypatch,
    reference,
    tag_flag,
    image_env,
    tag_env,
    expected_image,
    expected_tag,
):
    """Parametrized precedence matrix covering all override combinations."""
    if image_env:
        monkeypatch.setenv("IMAGE", image_env)
    else:
        monkeypatch.delenv("IMAGE", raising=False)
    if tag_env:
        monkeypatch.setenv("TAG", tag_env)
    else:
        monkeypatch.delenv("TAG", raising=False)

    cfg = Config()
    cfg = _apply_reference_overrides(cfg, reference=reference, tag_flag=tag_flag)
    assert cfg.image == expected_image
    assert cfg.tag == expected_tag
