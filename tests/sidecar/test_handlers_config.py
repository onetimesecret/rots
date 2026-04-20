# tests/sidecar/test_handlers_config.py

"""Tests for src/rots/sidecar/handlers_config.py

Covers:
- handle_config_stage with allowlist validation
- handle_config_apply with backup/restore flow
- handle_config_discard
- handle_config_get with secret masking
- atomic file operations
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

from rots.sidecar.handlers_config import (
    BACKUP_SUFFIX,
    DEFAULT_ENV_PATH,
    _get_backup_path,
    _get_env_path,
    _get_staged_path,
    handle_config_apply,
    handle_config_discard,
    handle_config_get,
    handle_config_stage,
)


class TestHelperFunctions:
    """Tests for path helper functions."""

    def test_get_env_path_default(self):
        """Default env path is used when not specified."""
        path = _get_env_path({})
        assert path == DEFAULT_ENV_PATH

    def test_get_env_path_custom(self):
        """Custom env path can be provided."""
        path = _get_env_path({"env_path": "/custom/path/.env"})
        assert path == Path("/custom/path/.env")

    def test_get_staged_path(self):
        """Staged path has correct suffix."""
        env_path = Path("/etc/app/.env")
        staged = _get_staged_path(env_path)
        assert staged == Path("/etc/app/.env.staged")

    def test_get_backup_path_format(self):
        """Backup path includes timestamp."""
        env_path = Path("/etc/app/.env")
        backup = _get_backup_path(env_path)
        # Should match pattern like .env.20260321_123456.backup
        assert BACKUP_SUFFIX in str(backup)
        assert env_path.stem in str(backup)


class TestHandleConfigStage:
    """Tests for handle_config_stage."""

    def test_missing_updates_returns_error(self):
        """Missing updates parameter returns failure."""
        result = handle_config_stage({})
        assert result.success is False
        assert "Missing" in result.error

    def test_invalid_updates_type_returns_error(self):
        """Non-dict updates returns failure."""
        result = handle_config_stage({"updates": "not a dict"})
        assert result.success is False
        assert "invalid" in result.error.lower()

    @patch("rots.sidecar.handlers_config.validate_config_update")
    @patch("rots.sidecar.handlers_config.EnvFile")
    def test_all_keys_rejected_returns_error(self, mock_env_file, mock_validate):
        """All keys rejected by allowlist returns failure."""
        mock_validate.return_value = ({}, ["SECRET", "PASSWORD"])

        result = handle_config_stage({"updates": {"SECRET": "x", "PASSWORD": "y"}})

        assert result.success is False
        assert "Rejected keys" in result.error

    @patch("rots.sidecar.handlers_config.validate_config_update")
    @patch("rots.sidecar.handlers_config.EnvFile")
    def test_successful_stage(self, mock_env_file_cls, mock_validate, tmp_path):
        """Valid keys are staged successfully."""
        mock_validate.return_value = ({"REDIS_URL": "redis://new:6379"}, [])

        mock_staged = MagicMock()
        mock_staged.iter_variables.return_value = [("REDIS_URL", "redis://new:6379")]
        mock_env_file_cls.return_value = mock_staged

        with patch.object(Path, "exists", return_value=False):
            result = handle_config_stage(
                {
                    "updates": {"REDIS_URL": "redis://new:6379"},
                    "env_path": str(tmp_path / ".env"),
                }
            )

        assert result.success is True
        assert "REDIS_URL" in result.data["staged_keys"]
        mock_staged.set.assert_called_once_with("REDIS_URL", "redis://new:6379")
        mock_staged.write.assert_called_once()

    @patch("rots.sidecar.handlers_config.validate_config_update")
    @patch("rots.sidecar.handlers_config.EnvFile")
    def test_partial_rejection_adds_warning(self, mock_env_file_cls, mock_validate, tmp_path):
        """Some keys rejected adds warning but succeeds."""
        mock_validate.return_value = (
            {"REDIS_URL": "redis://new"},
            ["SECRET"],  # This one rejected
        )

        mock_staged = MagicMock()
        mock_staged.iter_variables.return_value = [("REDIS_URL", "redis://new")]
        mock_env_file_cls.return_value = mock_staged

        with patch.object(Path, "exists", return_value=False):
            result = handle_config_stage(
                {
                    "updates": {"REDIS_URL": "redis://new", "SECRET": "bad"},
                    "env_path": str(tmp_path / ".env"),
                }
            )

        assert result.success is True
        assert len(result.warnings) == 1
        assert "SECRET" in result.warnings[0]


class TestHandleConfigApply:
    """Tests for handle_config_apply."""

    def test_no_staged_file_returns_error(self, tmp_path):
        """No staged file returns failure."""
        env_path = tmp_path / ".env"
        env_path.touch()

        with patch("rots.sidecar.handlers_config._get_env_path", return_value=env_path):
            result = handle_config_apply({"env_path": str(env_path)})

        assert result.success is False
        assert "No staged configuration" in result.error

    def test_env_file_not_found_returns_error(self, tmp_path):
        """Missing env file returns failure."""
        env_path = tmp_path / ".env"
        staged_path = tmp_path / ".env.staged"
        staged_path.touch()

        result = handle_config_apply({"env_path": str(env_path)})

        assert result.success is False
        assert "not found" in result.error

    @patch("rots.sidecar.handlers_config._rolling_restart_after_config")
    @patch("rots.sidecar.handlers_config.shutil.copy2")
    @patch("rots.sidecar.handlers_config.EnvFile")
    def test_creates_backup(self, mock_env_file_cls, mock_copy, mock_restart, tmp_path):
        """Backup is created before applying changes."""
        env_path = tmp_path / ".env"
        env_path.write_text("EXISTING=value\n")
        staged_path = tmp_path / ".env.staged"
        staged_path.write_text("NEW_KEY=new_value\n")

        mock_staged = MagicMock()
        mock_staged.iter_variables.return_value = [("NEW_KEY", "new_value")]
        mock_current = MagicMock()

        # Mock write to create the tmp file, and mock Path.rename
        def mock_write(path):
            if path:
                path.touch()

        mock_current.write = mock_write
        mock_env_file_cls.parse.side_effect = [mock_staged, mock_current]
        mock_restart.return_value = {"success": True}

        # Patch Path.rename to avoid actual file operations
        with patch.object(Path, "rename"):
            result = handle_config_apply({"env_path": str(env_path)})

        assert result.success is True
        mock_copy.assert_called_once()
        # First arg of copy should be env_path
        assert Path(mock_copy.call_args[0][0]) == env_path
        # Second arg should be backup path
        backup_path = Path(mock_copy.call_args[0][1])
        assert BACKUP_SUFFIX in str(backup_path)

    @patch("rots.sidecar.handlers_config._rolling_restart_after_config")
    @patch("rots.sidecar.handlers_config.shutil.copy2")
    @patch("rots.sidecar.handlers_config.EnvFile")
    def test_skip_restart_option(self, mock_env_file_cls, mock_copy, mock_restart, tmp_path):
        """skip_restart=True skips rolling restart."""
        env_path = tmp_path / ".env"
        env_path.write_text("EXISTING=value\n")
        staged_path = tmp_path / ".env.staged"
        staged_path.write_text("NEW_KEY=new_value\n")

        mock_staged = MagicMock()
        mock_staged.iter_variables.return_value = [("NEW_KEY", "new_value")]
        mock_current = MagicMock()

        def mock_write(path):
            if path:
                path.touch()

        mock_current.write = mock_write
        mock_env_file_cls.parse.side_effect = [mock_staged, mock_current]

        with patch.object(Path, "rename"):
            result = handle_config_apply({"env_path": str(env_path), "skip_restart": True})

        assert result.success is True
        mock_restart.assert_not_called()


class TestHandleConfigDiscard:
    """Tests for handle_config_discard."""

    def test_no_staged_file_returns_ok(self, tmp_path):
        """No staged file returns success (idempotent)."""
        env_path = tmp_path / ".env"

        result = handle_config_discard({"env_path": str(env_path)})

        assert result.success is True
        assert "No staged configuration" in result.data["message"]

    def test_removes_staged_file(self, tmp_path):
        """Staged file is removed."""
        env_path = tmp_path / ".env"
        staged_path = tmp_path / ".env.staged"
        staged_path.write_text("KEY=value\n")

        with patch("rots.sidecar.handlers_config.EnvFile") as mock_env_file_cls:
            mock_staged = MagicMock()
            mock_staged.iter_variables.return_value = [("KEY", "value")]
            mock_env_file_cls.parse.return_value = mock_staged

            result = handle_config_discard({"env_path": str(env_path)})

        assert result.success is True
        assert "discarded" in result.data["message"]
        assert "KEY" in result.data["discarded_keys"]
        assert not staged_path.exists()


class TestHandleConfigGet:
    """Tests for handle_config_get with secret masking."""

    def test_file_not_found_returns_error(self, tmp_path):
        """Missing config file returns failure."""
        env_path = tmp_path / ".env"

        result = handle_config_get({"env_path": str(env_path)})

        assert result.success is False
        assert "not found" in result.error

    @patch("rots.sidecar.handlers_config.EnvFile")
    def test_returns_requested_keys(self, mock_env_file_cls, tmp_path):
        """Only requested keys are returned."""
        env_path = tmp_path / ".env"
        env_path.touch()

        mock_env = MagicMock()
        mock_env.secret_variable_names = []
        mock_env.has.side_effect = lambda k: k in ["REDIS_URL", "HOST"]
        mock_env.get.side_effect = lambda k: {
            "REDIS_URL": "redis://localhost",
            "HOST": "example.com",
        }.get(k)
        mock_env_file_cls.parse.return_value = mock_env

        result = handle_config_get(
            {
                "env_path": str(env_path),
                "keys": ["REDIS_URL", "MISSING_KEY"],
            }
        )

        assert result.success is True
        assert result.data["config"]["REDIS_URL"] == "redis://localhost"
        assert result.data["config"]["MISSING_KEY"] is None

    @patch("rots.sidecar.handlers_config.EnvFile")
    def test_masks_secret_keys(self, mock_env_file_cls, tmp_path):
        """Secret keys are masked as <secret>."""
        env_path = tmp_path / ".env"
        env_path.touch()

        mock_env = MagicMock()
        mock_env.secret_variable_names = ["API_KEY"]
        mock_env.has.return_value = True
        mock_env.get.return_value = "actual_secret_value"
        mock_env_file_cls.parse.return_value = mock_env

        result = handle_config_get(
            {
                "env_path": str(env_path),
                "keys": ["API_KEY"],
            }
        )

        assert result.success is True
        assert result.data["config"]["API_KEY"] == "<secret>"

    @patch("rots.sidecar.handlers_config.EnvFile")
    def test_excludes_secrets_from_full_listing(self, mock_env_file_cls, tmp_path):
        """Secrets are excluded when listing all keys."""
        env_path = tmp_path / ".env"
        env_path.touch()

        mock_env = MagicMock()
        mock_env.secret_variable_names = ["SECRET_KEY"]
        mock_env.iter_variables.return_value = [
            ("REDIS_URL", "redis://localhost"),
            ("SECRET_KEY", "do_not_show"),
            ("HOST", "example.com"),
        ]
        mock_env_file_cls.parse.return_value = mock_env

        result = handle_config_get({"env_path": str(env_path)})

        assert result.success is True
        assert "REDIS_URL" in result.data["config"]
        assert "HOST" in result.data["config"]
        assert "SECRET_KEY" not in result.data["config"]

    @patch("rots.sidecar.handlers_config.EnvFile")
    def test_include_staged_option(self, mock_env_file_cls, tmp_path):
        """include_staged=True shows staged changes."""
        env_path = tmp_path / ".env"
        env_path.touch()
        staged_path = tmp_path / ".env.staged"
        staged_path.touch()

        mock_env = MagicMock()
        mock_env.secret_variable_names = []
        mock_env.iter_variables.return_value = [("EXISTING", "value")]

        mock_staged = MagicMock()
        mock_staged.iter_variables.return_value = [("NEW_KEY", "staged_value")]

        mock_env_file_cls.parse.side_effect = [mock_env, mock_staged]

        result = handle_config_get(
            {
                "env_path": str(env_path),
                "include_staged": True,
            }
        )

        assert result.success is True
        assert "staged" in result.data
        assert result.data["staged"]["NEW_KEY"] == "staged_value"
