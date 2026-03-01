# tests/commands/test_assets.py
"""Tests for assets command."""

import pytest


class TestAssetsCommandImports:
    """Verify assets command module imports correctly."""

    def test_assets_app_exists(self):
        """Assets app should be importable."""
        from ots_containers.commands import assets

        assert assets.app is not None

    def test_sync_function_exists(self):
        """sync command should be defined."""
        from ots_containers.commands import assets

        assert hasattr(assets, "sync")
        assert callable(assets.sync)


class TestAssetsSyncCommand:
    """Test assets sync command execution."""

    def test_sync_proceeds_without_validation(self, mocker):
        """sync should call assets_module.update without config validation."""
        from ots_containers.commands import assets

        mock_config = mocker.MagicMock()
        mocker.patch("ots_containers.commands.assets.Config", return_value=mock_config)
        mock_update = mocker.patch("ots_containers.commands.assets.assets_module.update")

        assets.sync()

        from unittest.mock import ANY

        mock_update.assert_called_once_with(mock_config, create_volume=False, executor=ANY)

    def test_sync_calls_assets_update(self, mocker):
        """sync should call assets_module.update."""
        from ots_containers.commands import assets

        mock_config = mocker.MagicMock()
        mocker.patch("ots_containers.commands.assets.Config", return_value=mock_config)
        mock_update = mocker.patch("ots_containers.commands.assets.assets_module.update")

        assets.sync()

        from unittest.mock import ANY

        mock_update.assert_called_once_with(mock_config, create_volume=False, executor=ANY)

    def test_sync_with_create_volume(self, mocker):
        """sync --create-volume should pass flag to update."""
        from ots_containers.commands import assets

        mock_config = mocker.MagicMock()
        mocker.patch("ots_containers.commands.assets.Config", return_value=mock_config)
        mock_update = mocker.patch("ots_containers.commands.assets.assets_module.update")

        assets.sync(create_volume=True)

        from unittest.mock import ANY

        mock_update.assert_called_once_with(mock_config, create_volume=True, executor=ANY)


class TestAssetsSyncHostContext:
    """Verify that assets sync passes host_var through to get_executor."""

    def test_sync_passes_host_var_to_get_executor(self, mocker):
        """sync should read context.host_var and pass it to get_executor(host=...)."""
        from ots_containers import context
        from ots_containers.commands import assets

        mock_config = mocker.MagicMock()
        mocker.patch("ots_containers.commands.assets.Config", return_value=mock_config)
        mocker.patch("ots_containers.commands.assets.assets_module.update")

        # Simulate --host flag having set a hostname in the context var
        token = context.host_var.set("eu1.example.com")
        try:
            assets.sync()
            mock_config.get_executor.assert_called_once_with(host="eu1.example.com")
        finally:
            context.host_var.reset(token)

    def test_sync_passes_none_host_when_no_host_flag(self, mocker):
        """sync should pass host=None when no --host flag was given."""
        from ots_containers.commands import assets

        mock_config = mocker.MagicMock()
        mocker.patch("ots_containers.commands.assets.Config", return_value=mock_config)
        mocker.patch("ots_containers.commands.assets.assets_module.update")

        # Default: no --host flag, host_var default is None
        assets.sync()

        mock_config.get_executor.assert_called_once_with(host=None)


class TestAssetsSyncHelp:
    """Test assets sync help output."""

    def test_assets_sync_help(self, capsys):
        """assets sync --help should work."""
        from ots_containers.cli import app

        with pytest.raises(SystemExit) as exc_info:
            app(["assets", "sync", "--help"])
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "create-volume" in captured.out.lower() or "sync" in captured.out.lower()
