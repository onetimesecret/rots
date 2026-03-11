# tests/commands/image/test_image_ref_parsing.py
"""Integration tests for pull command's image reference parsing.

Verifies pull uses parse_image_reference() correctly for:
- Registry ports (not confused with image:tag)
- Digest references (@sha256:...)
- Sentinel/alias rejection (@current, @rollback)
- Precedence: CLI flags > positional reference > env vars
"""

import pytest

from rots.commands.image.app import pull


def _setup_pull_mocks(
    mocker, tmp_path, *, image="ghcr.io/onetimesecret/onetimesecret", tag="@current", registry=None
):
    """Set up mocks for pull command tests.

    Returns (mock_config, mock_podman) for call inspection.
    """
    mock_config = mocker.MagicMock()
    mock_config.image = image
    mock_config.tag = tag
    mock_config.db_path = tmp_path / "deployments.db"
    mock_config.registry = registry
    mock_config.private_image = f"{registry}/onetimesecret" if registry else None
    mock_config.registry_auth_file = tmp_path / "auth.json"
    mock_config.get_executor.return_value = None

    mocker.patch("rots.commands.image.app.Config", return_value=mock_config)

    mock_podman = mocker.MagicMock()
    mocker.patch("rots.commands.image.app.Podman", return_value=mock_podman)
    mocker.patch("rots.commands.image.app.db.record_deployment")
    mocker.patch("rots.commands.image.app.db.set_alias")

    return mock_config, mock_podman


class TestPullParseRegistryPort:
    """Verify pull correctly handles registry:port references."""

    def test_pull_registry_port_image_tag(self, mocker, tmp_path):
        """pull registry:5000/org/image:v1 should split on last colon after last slash."""
        _mock_config, mock_podman = _setup_pull_mocks(mocker, tmp_path)

        pull(reference="registry:5000/org/image:v1", quiet=True)

        mock_podman.pull.assert_called_once()
        called_image = mock_podman.pull.call_args[0][0]
        assert called_image == "registry:5000/org/image:v1"

    def test_pull_registry_port_no_tag_uses_flag(self, mocker, tmp_path):
        """pull registry:5000/image --tag v2 should use the flag tag."""
        _mock_config, mock_podman = _setup_pull_mocks(mocker, tmp_path)

        pull(reference="registry:5000/image", tag="v2", quiet=True)

        mock_podman.pull.assert_called_once()
        called_image = mock_podman.pull.call_args[0][0]
        assert called_image == "registry:5000/image:v2"

    def test_pull_localhost_port(self, mocker, tmp_path):
        """pull localhost:5000/myapp:latest should work correctly."""
        _mock_config, mock_podman = _setup_pull_mocks(mocker, tmp_path)

        pull(reference="localhost:5000/myapp:latest", quiet=True)

        mock_podman.pull.assert_called_once()
        called_image = mock_podman.pull.call_args[0][0]
        assert called_image == "localhost:5000/myapp:latest"


class TestPullDigestReferences:
    """Verify pull handles digest references (@sha256:...)."""

    def test_pull_digest_reference(self, mocker, tmp_path):
        """pull image@sha256:abc should use the digest as the tag."""
        _mock_config, mock_podman = _setup_pull_mocks(mocker, tmp_path)

        pull(reference="ghcr.io/org/app@sha256:abc123def", quiet=True)

        mock_podman.pull.assert_called_once()
        called_image = mock_podman.pull.call_args[0][0]
        assert called_image == "ghcr.io/org/app:@sha256:abc123def"

    def test_pull_tag_flag_overrides_digest(self, mocker, tmp_path):
        """--tag flag should override digest from positional ref in pull command.

        Pull command uses: resolved_tag = tag or ref_tag (flags beat positional).
        """
        _mock_config, mock_podman = _setup_pull_mocks(mocker, tmp_path)

        pull(reference="app@sha256:deadbeef", tag="v1.0", quiet=True)

        mock_podman.pull.assert_called_once()
        called_image = mock_podman.pull.call_args[0][0]
        # --tag flag beats digest from positional in pull command
        assert called_image == "app:v1.0"

    def test_pull_digest_used_when_no_tag_flag(self, mocker, tmp_path):
        """Digest in positional ref should be used when no --tag flag."""
        _mock_config, mock_podman = _setup_pull_mocks(mocker, tmp_path)

        pull(reference="app@sha256:deadbeef", quiet=True)

        mock_podman.pull.assert_called_once()
        called_image = mock_podman.pull.call_args[0][0]
        assert "@sha256:deadbeef" in called_image


class TestPullSentinelRejection:
    """Verify pull rejects @current/@rollback sentinel tags."""

    def test_pull_rejects_current_sentinel(self, mocker, tmp_path, capsys):
        """pull with @current tag (from env default) should fail."""
        _mock_config, mock_podman = _setup_pull_mocks(mocker, tmp_path, tag="@current")
        mocker.patch("rots.commands.image.app.db.get_alias", return_value=None)

        with pytest.raises(SystemExit) as exc_info:
            pull(quiet=True)

        assert exc_info.value.code == 1
        mock_podman.pull.assert_not_called()

    def test_pull_rejects_rollback_as_positional(self, mocker, tmp_path, capsys):
        """pull with rollback tag should fail."""
        _mock_config, mock_podman = _setup_pull_mocks(mocker, tmp_path, tag="v1.0")
        mocker.patch("rots.commands.image.app.db.get_alias", return_value=None)

        with pytest.raises(SystemExit) as exc_info:
            pull(tag="@rollback", quiet=True)

        assert exc_info.value.code == 1
        mock_podman.pull.assert_not_called()

    def test_pull_rejects_current_with_alias_hint(self, mocker, tmp_path, caplog):
        """pull @current with an existing alias should print a helpful hint."""
        import logging

        _mock_config, mock_podman = _setup_pull_mocks(mocker, tmp_path, tag="@current")

        alias_mock = mocker.MagicMock()
        alias_mock.image = "ghcr.io/org/app"
        alias_mock.tag = "v0.23.0"
        mocker.patch("rots.commands.image.app.db.get_alias", return_value=alias_mock)

        with pytest.raises(SystemExit):
            with caplog.at_level(logging.ERROR):
                pull(quiet=False)

        assert "v0.23.0" in caplog.text
        mock_podman.pull.assert_not_called()


class TestPullPrecedence:
    """Verify pull precedence: CLI flags > positional > env vars."""

    def test_image_flag_overrides_positional(self, mocker, tmp_path):
        """--image flag should override image from positional reference."""
        _mock_config, mock_podman = _setup_pull_mocks(mocker, tmp_path)

        pull(
            reference="positional/image:v1",
            image="flag/image",
            tag="v1",
            quiet=True,
        )

        called_image = mock_podman.pull.call_args[0][0]
        assert called_image == "flag/image:v1"

    def test_tag_flag_overrides_positional_tag(self, mocker, tmp_path):
        """--tag flag should override tag from positional reference."""
        _mock_config, mock_podman = _setup_pull_mocks(mocker, tmp_path)

        pull(
            reference="ghcr.io/org/app:pos-tag",
            tag="flag-tag",
            quiet=True,
        )

        called_image = mock_podman.pull.call_args[0][0]
        assert called_image == "ghcr.io/org/app:flag-tag"

    def test_positional_overrides_env_vars(self, mocker, tmp_path):
        """Positional reference should override IMAGE/TAG env vars (via Config)."""
        _mock_config, mock_podman = _setup_pull_mocks(
            mocker,
            tmp_path,
            image="env/image",
            tag="env-tag",
        )

        pull(reference="pos/image:pos-tag", quiet=True)

        called_image = mock_podman.pull.call_args[0][0]
        assert called_image == "pos/image:pos-tag"

    def test_env_vars_used_when_no_overrides(self, mocker, tmp_path):
        """When no positional or flags, IMAGE/TAG env vars should be used."""
        _mock_config, mock_podman = _setup_pull_mocks(
            mocker,
            tmp_path,
            image="env/image",
            tag="env-tag",
        )

        pull(quiet=True)

        called_image = mock_podman.pull.call_args[0][0]
        assert called_image == "env/image:env-tag"

    def test_positional_image_only_with_tag_from_env(self, mocker, tmp_path):
        """Positional ref without tag should use TAG from env."""
        _mock_config, mock_podman = _setup_pull_mocks(
            mocker,
            tmp_path,
            tag="env-tag",
        )

        pull(reference="custom/image", quiet=True)

        called_image = mock_podman.pull.call_args[0][0]
        assert called_image == "custom/image:env-tag"
