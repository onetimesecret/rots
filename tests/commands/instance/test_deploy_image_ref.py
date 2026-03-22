# tests/commands/instance/test_deploy_image_ref.py
"""Integration tests for deploy/redeploy image reference handling.

Verifies the precedence chain:
  positional reference > --tag flag > TAG env > @current alias > DEFAULT_TAG
"""

from rots.commands import instance
from rots.config import DEFAULT_IMAGE


def _make_mock_config(mocker, tmp_path, image=DEFAULT_IMAGE, tag="@current"):
    """Create a mock Config that works with deploy's full call chain."""
    mock_config = mocker.MagicMock()
    mock_config.image = image
    mock_config.tag = tag
    mock_config.config_dir = mocker.MagicMock()
    mock_config.config_yaml = mocker.MagicMock()
    mock_config.var_dir = mocker.MagicMock()
    mock_config.web_template_path = mocker.MagicMock()
    mock_config.worker_template_path = mocker.MagicMock()
    mock_config.scheduler_template_path = mocker.MagicMock()
    mock_config.db_path = tmp_path / "test.db"
    mock_config.existing_config_files = []
    mock_config.has_custom_config = False
    mock_config.resolve_image_tag.return_value = (
        image,
        tag.lstrip("@") if tag.startswith("@") else tag,
    )
    mock_config.get_existing_config_files.return_value = []
    return mock_config


def _setup_deploy_mocks(mocker, tmp_path, **config_kwargs):
    """Set up all mocks needed for deploy to succeed, returning (mock_config, replace_calls)."""
    mock_config = _make_mock_config(mocker, tmp_path, **config_kwargs)
    mocker.patch("rots.commands.instance.app.Config", return_value=mock_config)
    mocker.patch("rots.commands.instance.app.assets.update")
    mocker.patch("rots.commands.instance.app.quadlet.write_web_template")
    mocker.patch("rots.commands.instance.app.quadlet.write_worker_template")
    mocker.patch("rots.commands.instance.app.quadlet.write_scheduler_template")
    mocker.patch("rots.commands.instance.app.systemd.start")
    mocker.patch("rots.commands.instance.app.db.record_deployment")

    # Track dataclasses.replace calls to verify image/tag overrides
    replace_calls = []

    def tracking_replace(obj, **kwargs):
        replace_calls.append(kwargs)
        # Apply the kwargs to the mock directly and return it
        for k, v in kwargs.items():
            if not k.startswith("_"):
                setattr(obj, k, v)
        # Update resolve_image_tag return value to reflect new image/tag
        new_image = kwargs.get("image", obj.image)
        new_tag = kwargs.get("tag", obj.tag)
        obj.resolve_image_tag.return_value = (new_image, new_tag)
        return obj

    mocker.patch("rots.commands.instance.app.dataclasses.replace", side_effect=tracking_replace)

    return mock_config, replace_calls


def _setup_redeploy_mocks(mocker, tmp_path, **config_kwargs):
    """Set up all mocks needed for redeploy to succeed."""
    mock_config, replace_calls = _setup_deploy_mocks(mocker, tmp_path, **config_kwargs)
    # Redeploy needs resolve_identifiers to find running instances
    mocker.patch(
        "rots.commands.instance.app.resolve_identifiers",
        side_effect=lambda ids, itype, running_only=False, executor=None: (
            {itype: list(ids)} if ids else {}
        ),
    )
    mocker.patch("rots.commands.instance.app.systemd.container_exists", return_value=True)
    mocker.patch("rots.commands.instance.app.systemd.recreate")
    return mock_config, replace_calls


class TestDeployImageReference:
    """Test deploy command with image reference handling."""

    def test_deploy_no_reference_uses_config_defaults(self, mocker, tmp_path):
        """deploy without reference or tag should use Config defaults."""
        mock_config, replace_calls = _setup_deploy_mocks(mocker, tmp_path)

        instance.deploy(web="7043", quiet=True)

        # No dataclasses.replace should have been called
        assert len(replace_calls) == 0
        # resolve_image_tag should have been called (for @current alias resolution)
        mock_config.resolve_image_tag.assert_called_once()

    def test_deploy_positional_reference_overrides_config(self, mocker, tmp_path):
        """deploy with positional reference should override image and tag."""
        mock_config, replace_calls = _setup_deploy_mocks(mocker, tmp_path)

        instance.deploy(
            reference="ghcr.io/custom/image:v2.0.0",
            web="7043",
            quiet=True,
        )

        assert len(replace_calls) == 1
        assert replace_calls[0]["image"] == "ghcr.io/custom/image"
        assert replace_calls[0]["tag"] == "v2.0.0"

    def test_deploy_positional_reference_image_only(self, mocker, tmp_path):
        """deploy with positional reference (no tag) should override image only."""
        mock_config, replace_calls = _setup_deploy_mocks(mocker, tmp_path)

        instance.deploy(
            reference="ghcr.io/custom/image",
            web="7043",
            quiet=True,
        )

        assert len(replace_calls) == 1
        assert replace_calls[0]["image"] == "ghcr.io/custom/image"
        # tag should remain cfg.tag since ref has no tag
        assert replace_calls[0]["tag"] == mock_config.tag

    def test_deploy_tag_flag_overrides_config(self, mocker, tmp_path):
        """deploy with --tag flag should override tag only."""
        mock_config, replace_calls = _setup_deploy_mocks(mocker, tmp_path)

        instance.deploy(
            web="7043",
            tag="v0.24.0",
            quiet=True,
        )

        assert len(replace_calls) == 1
        assert replace_calls[0]["tag"] == "v0.24.0"
        # image should remain the default
        assert replace_calls[0]["image"] == mock_config.image

    def test_deploy_reference_tag_beats_flag_tag(self, mocker, tmp_path):
        """When both positional ref has tag and --tag flag given, ref tag wins."""
        mock_config, replace_calls = _setup_deploy_mocks(mocker, tmp_path)

        instance.deploy(
            reference="ghcr.io/custom/image:v3.0.0",
            web="7043",
            tag="v0.24.0",
            quiet=True,
        )

        assert len(replace_calls) == 1
        assert replace_calls[0]["image"] == "ghcr.io/custom/image"
        # Reference tag v3.0.0 should win over --tag v0.24.0
        assert replace_calls[0]["tag"] == "v3.0.0"

    def test_deploy_reference_no_tag_plus_flag_tag(self, mocker, tmp_path):
        """Reference without tag + --tag flag: use ref image + flag tag."""
        mock_config, replace_calls = _setup_deploy_mocks(mocker, tmp_path)

        instance.deploy(
            reference="ghcr.io/custom/image",
            web="7043",
            tag="v0.24.0",
            quiet=True,
        )

        assert len(replace_calls) == 1
        assert replace_calls[0]["image"] == "ghcr.io/custom/image"
        assert replace_calls[0]["tag"] == "v0.24.0"

    def test_deploy_registry_port_in_reference(self, mocker, tmp_path):
        """Reference with registry port should parse correctly."""
        mock_config, replace_calls = _setup_deploy_mocks(mocker, tmp_path)

        instance.deploy(
            reference="registry:5000/org/image:v1.0",
            web="7043",
            quiet=True,
        )

        assert len(replace_calls) == 1
        # Port colon should not be treated as tag separator
        assert replace_calls[0]["image"] == "registry:5000/org/image"
        assert replace_calls[0]["tag"] == "v1.0"

    def test_deploy_env_var_fallback(self, mocker, tmp_path, monkeypatch):
        """deploy without ref or tag should respect IMAGE/TAG env vars via Config."""
        monkeypatch.setenv("IMAGE", "ghcr.io/env/image")
        monkeypatch.setenv("TAG", "env-tag")
        mock_config = _make_mock_config(
            mocker,
            tmp_path,
            image="ghcr.io/env/image",
            tag="env-tag",
        )
        mocker.patch("rots.commands.instance.app.Config", return_value=mock_config)
        mocker.patch("rots.commands.instance.app.assets.update")
        mocker.patch("rots.commands.instance.app.quadlet.write_web_template")
        mocker.patch("rots.commands.instance.app.systemd.start")
        mocker.patch("rots.commands.instance.app.db.record_deployment")

        instance.deploy(web="7043", quiet=True)

        # Config should have used the env vars
        assert mock_config.image == "ghcr.io/env/image"
        assert mock_config.tag == "env-tag"


class TestRedeployImageReference:
    """Test redeploy command with image reference handling."""

    def test_redeploy_no_reference_uses_config_defaults(self, mocker, tmp_path):
        """redeploy without reference or tag should use Config defaults."""
        mock_config, replace_calls = _setup_redeploy_mocks(mocker, tmp_path)

        instance.redeploy(web="7043", quiet=True)

        assert len(replace_calls) == 0
        mock_config.resolve_image_tag.assert_called_once()

    def test_redeploy_positional_reference_overrides_config(self, mocker, tmp_path):
        """redeploy with positional reference should override image and tag."""
        mock_config, replace_calls = _setup_redeploy_mocks(mocker, tmp_path)

        instance.redeploy(
            reference="ghcr.io/custom/image:v2.0.0",
            web="7043",
            quiet=True,
        )

        assert len(replace_calls) == 1
        assert replace_calls[0]["image"] == "ghcr.io/custom/image"
        assert replace_calls[0]["tag"] == "v2.0.0"

    def test_redeploy_tag_flag_overrides_config(self, mocker, tmp_path):
        """redeploy with --tag should override tag only."""
        mock_config, replace_calls = _setup_redeploy_mocks(mocker, tmp_path)

        instance.redeploy(
            web="7043",
            tag="v0.24.0",
            quiet=True,
        )

        assert len(replace_calls) == 1
        assert replace_calls[0]["tag"] == "v0.24.0"
        assert replace_calls[0]["image"] == mock_config.image

    def test_redeploy_reference_tag_beats_flag_tag(self, mocker, tmp_path):
        """When both positional ref has tag and --tag flag given, ref tag wins."""
        mock_config, replace_calls = _setup_redeploy_mocks(mocker, tmp_path)

        instance.redeploy(
            reference="ghcr.io/custom/image:v3.0.0",
            web="7043",
            tag="v0.24.0",
            quiet=True,
        )

        assert len(replace_calls) == 1
        assert replace_calls[0]["tag"] == "v3.0.0"

    def test_redeploy_with_digest_reference(self, mocker, tmp_path):
        """redeploy with digest reference should pass through."""
        mock_config, replace_calls = _setup_redeploy_mocks(mocker, tmp_path)

        instance.redeploy(
            reference="ghcr.io/org/image@sha256:abc123def",
            web="7043",
            quiet=True,
        )

        assert len(replace_calls) == 1
        assert replace_calls[0]["image"] == "ghcr.io/org/image"
        assert replace_calls[0]["tag"] == "@sha256:abc123def"
