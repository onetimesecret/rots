# tests/test_assets.py
"""Tests for assets module."""

import subprocess
from unittest.mock import MagicMock

import pytest

from rots import assets
from rots.assets import TEMP_CONTAINER_NAME
from rots.config import Config


def _ok(args, **extra):
    """Helper to create a successful CompletedProcess."""
    return subprocess.CompletedProcess(args=args, returncode=0, **extra)


class TestAssetsUpdate:
    """Test the assets.update function."""

    @pytest.fixture(autouse=True)
    def _skip_podman_check(self, mocker):
        """Bypass require_podman since podman is not available on macOS dev machines."""
        mocker.patch("rots.assets.require_podman")

    def test_update_raises_user_friendly_error_on_volume_mount_failure(self, mocker):
        """Volume mount failure should raise SystemExit with helpful message.

        Reproduces:
            $ rots assets sync
            # Should show: "Failed to mount volume 'static_assets': <reason>"
            # Not a raw Python traceback
        """
        mock_run = mocker.patch("subprocess.run")

        mock_run.side_effect = [
            # volume.create succeeds
            _ok(["podman", "volume", "create", "static_assets"]),
            # volume.mount fails
            subprocess.CalledProcessError(
                returncode=1,
                cmd=["podman", "volume", "mount", "static_assets"],
                output="",
                stderr="Error: volume static_assets does not exist",
            ),
        ]

        cfg = mocker.MagicMock(spec=Config)

        with pytest.raises(SystemExit) as exc_info:
            assets.update(cfg, create_volume=True)

        # Should contain helpful context, not just exit code
        error_msg = str(exc_info.value)
        assert "volume" in error_msg.lower() or "mount" in error_msg.lower()

    def test_update_volume_mount_returns_empty_path(self, mocker):
        """Test behavior when volume mount returns empty stdout."""
        mock_run = mocker.patch("subprocess.run")

        mock_run.side_effect = [
            # volume.create succeeds
            _ok(["podman", "volume", "create", "static_assets"]),
            # volume.mount returns empty path
            _ok(["podman", "volume", "mount", "static_assets"], stdout=""),
            # image.exists succeeds
            _ok(["podman", "image", "exists"]),
            # pre-cleanup rm (no leftover container)
            _ok(["podman", "rm", TEMP_CONTAINER_NAME]),
            # podman.create succeeds
            _ok(["podman", "create", "image:tag"], stdout="abc123\n"),
            # podman.cp succeeds
            _ok(["podman", "cp", "abc123:/app/public/.", "."]),
            # podman.rm succeeds
            _ok(["podman", "rm", "abc123"]),
        ]

        cfg = mocker.MagicMock(spec=Config)

        assets.update(cfg, create_volume=True)

        # Verify podman.create was called with --name and --image-volume flags
        create_call = mock_run.call_args_list[4]
        cmd = create_call[0][0]
        assert "--name" in cmd
        assert TEMP_CONTAINER_NAME in cmd
        assert "--image-volume" in cmd
        assert "ignore" in cmd

    def test_update_without_create_volume(self, mocker, tmp_path):
        """Test update skips volume creation when create_volume=False."""
        mock_run = mocker.patch("subprocess.run")

        fake_volume_path = tmp_path / "volume_data"
        fake_volume_path.mkdir()

        mock_run.side_effect = [
            # volume.mount succeeds (no volume.create call)
            _ok(
                ["podman", "volume", "mount", "static_assets"],
                stdout=f"{fake_volume_path}\n",
            ),
            # image.exists succeeds
            _ok(["podman", "image", "exists"]),
            # pre-cleanup rm
            _ok(["podman", "rm", TEMP_CONTAINER_NAME]),
            # podman.create succeeds
            _ok(["podman", "create", "image:tag"], stdout="abc123\n"),
            # podman.cp succeeds
            _ok(["podman", "cp", "abc123:/app/public/.", str(fake_volume_path)]),
            # podman.rm succeeds
            _ok(["podman", "rm", "abc123"]),
        ]

        cfg = mocker.MagicMock(spec=Config)

        assets.update(cfg, create_volume=False)

        # First call should be volume.mount, not volume.create
        first_call = mock_run.call_args_list[0]
        assert "mount" in first_call[0][0]
        assert "create" not in first_call[0][0]

    def test_update_cleans_up_container_on_cp_failure(self, mocker, tmp_path):
        """Test container is removed even when cp fails."""
        mock_run = mocker.patch("subprocess.run")

        fake_volume_path = tmp_path / "volume_data"
        fake_volume_path.mkdir()

        mock_run.side_effect = [
            # volume.mount succeeds
            _ok(
                ["podman", "volume", "mount", "static_assets"],
                stdout=f"{fake_volume_path}\n",
            ),
            # image.exists succeeds
            _ok(["podman", "image", "exists"]),
            # pre-cleanup rm
            _ok(["podman", "rm", TEMP_CONTAINER_NAME]),
            # podman.create succeeds
            _ok(["podman", "create", "image:tag"], stdout="container123\n"),
            # podman.cp fails
            subprocess.CalledProcessError(
                returncode=125,
                cmd=["podman", "cp", "container123:/app/public/.", "..."],
                stderr="Error: no such container",
            ),
            # podman.rm should still be called (cleanup)
            _ok(["podman", "rm", "container123"]),
        ]

        cfg = mocker.MagicMock(spec=Config)

        with pytest.raises(subprocess.CalledProcessError):
            assets.update(cfg, create_volume=False)

        # Verify rm was called for cleanup despite cp failure
        rm_call = mock_run.call_args_list[5]
        assert "rm" in rm_call[0][0]
        assert "container123" in rm_call[0][0]

    def test_update_idempotent_when_volume_exists(self, mocker, tmp_path):
        """Deploy succeeds even when static_assets volume already exists.

        Reproduces the original bug: podman create fails with
        "volume with name static_assets already exists" when the image
        has a VOLUME directive and the named volume already exists.

        The fix uses --image-volume=ignore to prevent podman from
        auto-creating volumes for image VOLUME directives.
        """
        mock_run = mocker.patch("subprocess.run")

        fake_volume_path = tmp_path / "volume_data"
        fake_volume_path.mkdir()
        # Create manifest so we exercise the success path
        manifest_dir = fake_volume_path / "web" / "dist" / ".vite"
        manifest_dir.mkdir(parents=True)
        (manifest_dir / "manifest.json").write_text("{}")

        mock_run.side_effect = [
            # volume.create tolerates existing volume (check=False)
            _ok(["podman", "volume", "create", "static_assets"]),
            # volume.mount succeeds (volume already exists)
            _ok(
                ["podman", "volume", "mount", "static_assets"],
                stdout=f"{fake_volume_path}\n",
            ),
            # image.exists succeeds
            _ok(["podman", "image", "exists"]),
            # pre-cleanup rm (no leftover container)
            _ok(["podman", "rm", TEMP_CONTAINER_NAME]),
            # podman.create succeeds with --image-volume=ignore
            _ok(
                [
                    "podman",
                    "create",
                    "--name",
                    TEMP_CONTAINER_NAME,
                    "--image-volume",
                    "ignore",
                    "image:v1",
                ],
                stdout="abc123\n",
            ),
            # podman.cp succeeds
            _ok(["podman", "cp", "abc123:/app/public/.", str(fake_volume_path)]),
            # podman.rm succeeds
            _ok(["podman", "rm", "abc123"]),
        ]

        cfg = mocker.MagicMock(spec=Config)

        # Should not raise — this is the idempotent re-deploy scenario
        assets.update(cfg, create_volume=True)

        # Verify the create call includes --image-volume ignore
        create_call = mock_run.call_args_list[4]
        cmd = create_call[0][0]
        assert "--image-volume" in cmd
        assert "ignore" in cmd
        assert "--name" in cmd

    def test_update_pre_cleans_leftover_temp_container(self, mocker, tmp_path):
        """Pre-cleanup removes leftover container from interrupted previous run."""
        mock_run = mocker.patch("subprocess.run")

        fake_volume_path = tmp_path / "volume_data"
        fake_volume_path.mkdir()

        mock_run.side_effect = [
            # volume.mount succeeds
            _ok(
                ["podman", "volume", "mount", "static_assets"],
                stdout=f"{fake_volume_path}\n",
            ),
            # image.exists succeeds
            _ok(["podman", "image", "exists"]),
            # pre-cleanup rm succeeds (leftover container existed)
            _ok(["podman", "rm", TEMP_CONTAINER_NAME]),
            # podman.create succeeds
            _ok(["podman", "create", "image:tag"], stdout="newcontainer\n"),
            # podman.cp succeeds
            _ok(["podman", "cp", "newcontainer:/app/public/.", str(fake_volume_path)]),
            # podman.rm succeeds
            _ok(["podman", "rm", "newcontainer"]),
        ]

        cfg = mocker.MagicMock(spec=Config)

        assets.update(cfg, create_volume=False)

        # Third call (index 2) should be the pre-cleanup rm
        pre_cleanup = mock_run.call_args_list[2]
        cmd = pre_cleanup[0][0]
        assert "rm" in cmd
        assert TEMP_CONTAINER_NAME in cmd

    def test_update_create_failure_shows_friendly_error(self, mocker, tmp_path):
        """Container create failure should raise SystemExit with helpful message."""
        mock_run = mocker.patch("subprocess.run")

        fake_volume_path = tmp_path / "volume_data"
        fake_volume_path.mkdir()

        mock_run.side_effect = [
            # volume.mount succeeds
            _ok(
                ["podman", "volume", "mount", "static_assets"],
                stdout=f"{fake_volume_path}\n",
            ),
            # image.exists succeeds (image is present but create still fails)
            _ok(["podman", "image", "exists"]),
            # pre-cleanup rm
            _ok(["podman", "rm", TEMP_CONTAINER_NAME]),
            # podman.create fails
            subprocess.CalledProcessError(
                returncode=125,
                cmd=["podman", "create"],
                stderr="Error: image not found",
            ),
        ]

        cfg = mocker.MagicMock(spec=Config)
        cfg.resolved_image_with_tag.return_value = "ghcr.io/onetimesecret/onetimesecret:v0.23.3"

        with pytest.raises(SystemExit) as exc_info:
            assets.update(cfg, create_volume=False)

        error_msg = str(exc_info.value)
        assert "temporary container" in error_msg.lower() or "failed" in error_msg.lower()

    def test_update_image_not_found_with_alias_tag(self, mocker, tmp_path):
        """Missing image with alias tag should suggest set-current."""
        mock_run = mocker.patch("subprocess.run")

        fake_volume_path = tmp_path / "volume_data"
        fake_volume_path.mkdir()

        mock_run.side_effect = [
            # volume.mount succeeds
            _ok(
                ["podman", "volume", "mount", "static_assets"],
                stdout=f"{fake_volume_path}\n",
            ),
            # image.exists fails — image not found locally
            subprocess.CompletedProcess(args=["podman", "image", "exists"], returncode=1),
        ]

        cfg = mocker.MagicMock(spec=Config)
        cfg.tag = "current"
        cfg.resolved_image_with_tag.return_value = "registry.example.com/app:current"

        with pytest.raises(SystemExit) as exc_info:
            assets.update(cfg, create_volume=False)

        error_msg = str(exc_info.value)
        assert "set-current" in error_msg

    def test_update_image_not_found_with_explicit_tag(self, mocker, tmp_path):
        """Missing image with explicit tag should suggest pulling."""
        mock_run = mocker.patch("subprocess.run")

        fake_volume_path = tmp_path / "volume_data"
        fake_volume_path.mkdir()

        mock_run.side_effect = [
            # volume.mount succeeds
            _ok(
                ["podman", "volume", "mount", "static_assets"],
                stdout=f"{fake_volume_path}\n",
            ),
            # image.exists fails — image not found locally
            subprocess.CompletedProcess(args=["podman", "image", "exists"], returncode=1),
        ]

        cfg = mocker.MagicMock(spec=Config)
        cfg.tag = "v0.23.3"
        cfg.resolved_image_with_tag.return_value = "registry.example.com/app:v0.23.3"

        with pytest.raises(SystemExit) as exc_info:
            assets.update(cfg, create_volume=False)

        error_msg = str(exc_info.value)
        assert "not found locally" in error_msg
        assert "pull" in error_msg.lower()


# =============================================================================
# Remote executor tests
# =============================================================================


def _make_ssh_executor(mocker):
    """Create a mock SSHExecutor that _is_remote() recognises as remote."""
    mock_ex = mocker.MagicMock()
    mocker.patch(
        "rots.assets._is_remote",
        side_effect=lambda ex: ex is mock_ex,
    )
    return mock_ex


def _make_remote_result(stdout="", returncode=0):
    result = MagicMock()
    result.ok = returncode == 0
    result.stdout = stdout
    result.stderr = ""
    result.returncode = returncode
    return result


class TestAssetsUpdateRemote:
    """Test assets.update() with remote executor."""

    def test_creates_podman_with_executor(self, mocker):
        """Should construct Podman(executor=ex) for remote operations."""
        mock_ex = _make_ssh_executor(mocker)
        mock_podman_cls = mocker.patch("rots.assets.Podman")
        mock_require = mocker.patch("rots.assets.require_podman")

        mock_p = MagicMock()
        mock_podman_cls.return_value = mock_p

        # volume.mount returns a path
        mock_mount_result = MagicMock()
        mock_mount_result.stdout = "/var/lib/containers/storage/volumes/static_assets/_data\n"
        mock_p.volume.mount.return_value = mock_mount_result

        # image.exists returns ok
        mock_p.image.exists.return_value = _make_remote_result(returncode=0)

        # create returns container id
        mock_p.create.return_value = _make_remote_result(stdout="abc123\n")

        # cp succeeds
        mock_p.cp.return_value = _make_remote_result()

        # manifest check
        mock_ex.run.return_value = _make_remote_result(returncode=0)

        # rm succeeds
        mock_p.rm.return_value = _make_remote_result()

        cfg = MagicMock(spec=Config)
        cfg.resolved_image_with_tag.return_value = "ghcr.io/ots:latest"
        cfg.tag = "latest"

        assets.update(cfg, executor=mock_ex)

        # Podman should be constructed with executor
        mock_podman_cls.assert_called_once_with(executor=mock_ex)
        # require_podman should receive executor
        mock_require.assert_called_once_with(executor=mock_ex)
