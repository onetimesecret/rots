# tests/commands/instance/test_run_image_ref.py
"""Integration tests for the run command's image reference handling.

Verifies --tag override, resolve_image_tag fallback, and IMAGE/TAG
env var precedence through the run command.
"""

from unittest.mock import Mock

from ots_containers.commands import instance
from ots_containers.config import Config


def _setup_run_mocks(mocker, tmp_path, **config_overrides):
    """Set up standard mocks for run command tests.

    Returns (mock_config, mock_executor).
    """
    mock_executor = mocker.MagicMock()
    mock_executor.run_stream.return_value = 0
    mock_executor.run.return_value = mocker.MagicMock(stdout="abc123deadbeef\n", ok=True)

    image = config_overrides.get("image", "ghcr.io/onetimesecret/onetimesecret")
    tag = config_overrides.get("tag", "v0.23.0")

    cfg = Config(image=image, tag=tag)
    cfg.resolve_image_tag = Mock(
        return_value=config_overrides.get("resolve_image_tag", (image, tag))
    )
    cfg.get_executor = Mock(return_value=mock_executor)

    mocker.patch(
        "ots_containers.commands.instance.app.Config",
        lambda: cfg,
    )
    mocker.patch(
        "ots_containers.commands.instance.app.quadlet.DEFAULT_ENV_FILE",
        tmp_path / "nonexistent",
    )

    # Track dataclasses.replace calls; apply kwargs to same cfg and re-attach mocks
    def tracking_replace(obj, **kwargs):
        for k, v in kwargs.items():
            object.__setattr__(obj, k, v)
        new_image = kwargs.get("image", obj.image)
        new_tag = kwargs.get("tag", obj.tag)
        obj.resolve_image_tag = Mock(return_value=(new_image, new_tag))
        obj.get_executor = Mock(return_value=mock_executor)
        return obj

    mocker.patch(
        "ots_containers.commands.instance.app.dataclasses.replace",
        side_effect=tracking_replace,
    )

    return cfg, mock_executor


class TestRunTagOverride:
    """Test --tag flag overrides default resolution."""

    def test_tag_flag_uses_specified_tag(self, mocker, tmp_path):
        """run --tag v0.19.0 should use that tag directly."""
        mock_config, mock_executor = _setup_run_mocks(mocker, tmp_path, tag="v0.23.0")

        instance.run(port=7143, tag="v0.19.0", quiet=True)

        cmd = mock_executor.run_stream.call_args[0][0]
        full_image = cmd[-1]
        assert "v0.19.0" in full_image
        assert "ghcr.io/onetimesecret/onetimesecret" in full_image

    def test_tag_flag_skips_resolve(self, mocker, tmp_path):
        """run --tag sets the tag via replace; resolve_image_tag passes it through."""
        mock_config, mock_executor = _setup_run_mocks(mocker, tmp_path)

        instance.run(port=7143, tag="v0.19.0", quiet=True)

        # resolve_image_tag IS called (always called in the unified flow),
        # but the concrete tag passes through (not an alias).
        cmd = mock_executor.run_stream.call_args[0][0]
        full_image = cmd[-1]
        assert "v0.19.0" in full_image

    def test_tag_flag_uses_cfg_image(self, mocker, tmp_path):
        """run --tag uses cfg.image (from IMAGE env) as the image portion."""
        mock_config, mock_executor = _setup_run_mocks(
            mocker, tmp_path, image="custom-registry.io/myapp"
        )

        instance.run(port=7143, tag="v1.0", quiet=True)

        cmd = mock_executor.run_stream.call_args[0][0]
        full_image = cmd[-1]
        assert full_image == "custom-registry.io/myapp:v1.0"


class TestRunResolveImageTag:
    """Test resolve_image_tag() fallback when no --tag given."""

    def test_no_tag_calls_resolve(self, mocker, tmp_path):
        """run without --tag should call resolve_image_tag()."""
        mock_config, mock_executor = _setup_run_mocks(
            mocker,
            tmp_path,
            resolve_image_tag=(
                "ghcr.io/onetimesecret/onetimesecret",
                "v0.24.0",
            ),
        )

        instance.run(port=7143, quiet=True)

        mock_config.resolve_image_tag.assert_called_once()

    def test_no_tag_uses_resolved_tag(self, mocker, tmp_path):
        """run without --tag uses the tag from resolve_image_tag()."""
        mock_config, mock_executor = _setup_run_mocks(
            mocker,
            tmp_path,
            resolve_image_tag=(
                "ghcr.io/onetimesecret/onetimesecret",
                "v0.24.0",
            ),
        )

        instance.run(port=7143, quiet=True)

        cmd = mock_executor.run_stream.call_args[0][0]
        full_image = cmd[-1]
        assert "v0.24.0" in full_image

    def test_no_tag_uses_resolved_image(self, mocker, tmp_path):
        """run without --tag uses the image from resolve_image_tag()."""
        mock_config, mock_executor = _setup_run_mocks(
            mocker,
            tmp_path,
            resolve_image_tag=(
                "custom-registry.io/custom-image",
                "v1.0",
            ),
        )

        instance.run(port=7143, quiet=True)

        cmd = mock_executor.run_stream.call_args[0][0]
        full_image = cmd[-1]
        assert full_image == "custom-registry.io/custom-image:v1.0"


class TestRunImageEnvVars:
    """Test IMAGE/TAG env var precedence via Config mock."""

    def test_image_env_var_reflected_in_config(self, mocker, tmp_path):
        """IMAGE env var flows through cfg.image."""
        mock_config, mock_executor = _setup_run_mocks(
            mocker,
            tmp_path,
            image="custom.io/env-image",
            tag="env-tag",
        )

        instance.run(port=7143, tag="env-tag", quiet=True)

        cmd = mock_executor.run_stream.call_args[0][0]
        full_image = cmd[-1]
        assert full_image == "custom.io/env-image:env-tag"

    def test_tag_flag_overrides_env_tag(self, mocker, tmp_path):
        """--tag flag takes precedence over TAG env var (in cfg.tag)."""
        mock_config, mock_executor = _setup_run_mocks(
            mocker,
            tmp_path,
            tag="from-env",
        )

        instance.run(port=7143, tag="from-flag", quiet=True)

        cmd = mock_executor.run_stream.call_args[0][0]
        full_image = cmd[-1]
        assert "from-flag" in full_image
        assert "from-env" not in full_image


class TestRunPositionalReference:
    """run() accepts positional image reference."""

    def test_reference_overrides_image_and_tag(self, mocker, tmp_path):
        """run with positional reference should override both image and tag."""
        _mock_config, mock_executor = _setup_run_mocks(mocker, tmp_path)

        instance.run(reference="custom/image:v2.0", port=7143, quiet=True)

        cmd = mock_executor.run_stream.call_args[0][0]
        full_image = cmd[-1]
        assert full_image == "custom/image:v2.0"

    def test_reference_image_only(self, mocker, tmp_path):
        """run with positional reference (no tag) should override image only."""
        _mock_config, mock_executor = _setup_run_mocks(mocker, tmp_path)

        instance.run(reference="custom/image", port=7143, quiet=True)

        cmd = mock_executor.run_stream.call_args[0][0]
        full_image = cmd[-1]
        assert "custom/image" in full_image

    def test_reference_tag_beats_flag_tag(self, mocker, tmp_path):
        """Positional ref tag takes precedence over --tag flag."""
        _mock_config, mock_executor = _setup_run_mocks(mocker, tmp_path)

        instance.run(reference="img:ref-tag", port=7143, tag="flag-tag", quiet=True)

        cmd = mock_executor.run_stream.call_args[0][0]
        full_image = cmd[-1]
        assert "ref-tag" in full_image
        assert "flag-tag" not in full_image

    def test_reference_with_registry_port(self, mocker, tmp_path):
        """run with registry:port/image:tag should parse correctly."""
        _mock_config, mock_executor = _setup_run_mocks(mocker, tmp_path)

        instance.run(reference="registry:5000/org/image:v1.0", port=7143, quiet=True)

        cmd = mock_executor.run_stream.call_args[0][0]
        full_image = cmd[-1]
        assert full_image == "registry:5000/org/image:v1.0"


class TestRunDetachedMode:
    """Detach mode uses ex.run() instead of ex.run_stream()."""

    def test_detached_uses_run_not_stream(self, mocker, tmp_path):
        """run --detach should use executor.run() not run_stream()."""
        mock_config, mock_executor = _setup_run_mocks(mocker, tmp_path)

        instance.run(port=7143, detach=True, quiet=True)

        mock_executor.run.assert_called_once()
        mock_executor.run_stream.assert_not_called()

    def test_foreground_uses_stream(self, mocker, tmp_path):
        """run without --detach should use executor.run_stream()."""
        mock_config, mock_executor = _setup_run_mocks(mocker, tmp_path)

        instance.run(port=7143, quiet=True)

        mock_executor.run_stream.assert_called_once()
