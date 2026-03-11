# tests/commands/image/test_app.py
"""Tests for image app commands."""

import logging

import pytest


class TestImageAppImports:
    """Test image app structure."""

    def test_image_app_exists(self):
        """Test image app is defined."""
        from rots.commands.image.app import app

        assert app is not None

    def test_rm_function_exists(self):
        """Test rm function is defined."""
        from rots.commands.image.app import rm

        assert rm is not None

    def test_prune_function_exists(self):
        """Test prune function is defined."""
        from rots.commands.image.app import prune

        assert prune is not None

    def test_ls_function_exists(self):
        """Test ls (list) function is defined."""
        from rots.commands.image.app import ls

        assert ls is not None


class TestRmCommand:
    """Test rm command."""

    def test_rm_no_tags_exits(self):
        """Should exit if no tags provided."""
        from rots.commands.image.app import rm

        with pytest.raises(SystemExit) as exc_info:
            rm(tags=(), yes=True)

        assert exc_info.value.code == 1

    def test_rm_aborts_without_confirmation(self, mocker, capsys):
        """Should abort if user doesn't confirm."""
        from rots.commands.image.app import rm

        mocker.patch("builtins.input", return_value="n")

        rm(tags=("v0.22.0",), yes=False)

        captured = capsys.readouterr()
        assert "Aborted" in captured.out

    def test_rm_removes_image_with_yes(self, mocker, caplog, tmp_path):
        """Should remove image when --yes is provided."""
        from rots.commands.image.app import rm

        mocker.patch(
            "rots.config.Config.db_path",
            new_callable=mocker.PropertyMock,
            return_value=tmp_path / "deployments.db",
        )
        mock_run = mocker.patch(
            "rots.podman.subprocess.run",
            return_value=mocker.MagicMock(returncode=0, stdout="", stderr=""),
        )

        with caplog.at_level(logging.INFO):
            rm(tags=("v0.22.0",), yes=True)

        mock_run.assert_called()
        assert "Removed" in caplog.text

    def test_rm_tries_multiple_patterns(self, mocker, caplog, tmp_path):
        """Should try multiple image patterns."""
        from rots.commands.image.app import rm

        mocker.patch(
            "rots.config.Config.db_path",
            new_callable=mocker.PropertyMock,
            return_value=tmp_path / "deployments.db",
        )

        call_count = 0

        def mock_subprocess_run(*_args, **_kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                return mocker.MagicMock(returncode=1, stdout="", stderr="not found")
            return mocker.MagicMock(returncode=0, stdout="", stderr="")

        mocker.patch(
            "rots.podman.subprocess.run",
            side_effect=mock_subprocess_run,
        )

        with caplog.at_level(logging.INFO):
            rm(tags=("v0.22.0",), yes=True)

        assert call_count == 3  # Tried 3 patterns before success
        assert "Removed" in caplog.text

    def test_rm_reports_not_found(self, mocker, caplog, tmp_path):
        """Should report when image not found."""
        from rots.commands.image.app import rm

        mocker.patch(
            "rots.config.Config.db_path",
            new_callable=mocker.PropertyMock,
            return_value=tmp_path / "deployments.db",
        )
        mocker.patch(
            "rots.podman.subprocess.run",
            return_value=mocker.MagicMock(returncode=1, stdout="", stderr="not found"),
        )

        with caplog.at_level(logging.INFO):
            rm(tags=("nonexistent",), yes=True)

        assert "Image not found" in caplog.text

    def test_rm_with_force(self, mocker, tmp_path):
        """Should pass force flag to podman."""
        from rots.commands.image.app import rm

        mocker.patch(
            "rots.config.Config.db_path",
            new_callable=mocker.PropertyMock,
            return_value=tmp_path / "deployments.db",
        )
        mock_run = mocker.patch(
            "rots.podman.subprocess.run",
            return_value=mocker.MagicMock(returncode=0, stdout="", stderr=""),
        )

        rm(tags=("v0.22.0",), force=True, yes=True)

        # Check force was passed to at least one call
        calls = mock_run.call_args_list
        assert any("--force" in str(call) for call in calls)


class TestPruneCommand:
    """Test prune command."""

    def test_prune_aborts_without_confirmation(self, mocker, capsys):
        """Should abort if user doesn't confirm."""
        from rots.commands.image.app import prune

        mocker.patch("builtins.input", return_value="n")

        prune(yes=False)

        captured = capsys.readouterr()
        assert "Aborted" in captured.out

    def test_prune_calls_podman(self, mocker, capsys):
        """Should call podman image prune."""
        from rots.commands.image.app import prune

        # Mock subprocess.run since the podman wrapper calls it
        mock_run = mocker.patch(
            "rots.podman.subprocess.run",
            return_value=mocker.MagicMock(stdout="removed images", returncode=0),
        )

        prune(yes=True)

        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert "podman" in cmd
        assert "image" in cmd
        assert "prune" in cmd

        captured = capsys.readouterr()
        assert "Pruned" in captured.out

    def test_prune_with_all_flag(self, mocker, capsys):
        """Should pass all flag to podman."""
        from rots.commands.image.app import prune

        # Mock subprocess.run since the podman wrapper calls it
        mock_run = mocker.patch(
            "rots.podman.subprocess.run",
            return_value=mocker.MagicMock(stdout="removed images", returncode=0),
        )

        prune(all_images=True, yes=True)

        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert "--all" in cmd

        captured = capsys.readouterr()
        assert "Pruned" in captured.out

    def test_prune_failure_exits(self, mocker):
        """Should exit on prune failure."""
        from rots.commands.image.app import prune

        mocker.patch(
            "rots.podman.subprocess.run",
            side_effect=Exception("prune failed"),
        )

        with pytest.raises(SystemExit) as exc_info:
            prune(yes=True)

        assert exc_info.value.code == 1

    def test_prune_prompts_different_for_all(self, mocker, capsys):
        """Should show different prompt for --all."""
        from rots.commands.image.app import prune

        mocker.patch("builtins.input", return_value="n")

        prune(all_images=True, yes=False)

        captured = capsys.readouterr()
        assert "all unused images" in captured.out

        prune(all_images=False, yes=False)

        captured = capsys.readouterr()
        assert "dangling" in captured.out


class TestLsCommand:
    """Test ls (list) command."""

    def test_ls_calls_podman(self, mocker, capsys):
        """Should call podman image list."""
        from rots.commands.image.app import ls

        mocker.patch(
            "rots.podman.subprocess.run",
            return_value=mocker.MagicMock(
                stdout="REPOSITORY:TAG  ID  SIZE  CREATED\nonetimesecret:v1  abc  100MB  1 day"
            ),
        )

        ls(all_tags=False, json_output=False)

        captured = capsys.readouterr()
        assert "Local images:" in captured.out

    def test_ls_with_json_output(self, mocker, capsys):
        """Should output JSON when --json flag is used."""
        from rots.commands.image.app import ls

        mocker.patch(
            "rots.podman.subprocess.run",
            return_value=mocker.MagicMock(stdout='[{"Names": ["onetimesecret:v1"], "Id": "abc"}]'),
        )

        ls(all_tags=False, json_output=True)

        captured = capsys.readouterr()
        assert "onetimesecret" in captured.out

    def test_ls_with_all_tags(self, mocker, capsys):
        """Should show all images when --all flag is used."""
        from rots.commands.image.app import ls

        mocker.patch(
            "rots.podman.subprocess.run",
            return_value=mocker.MagicMock(
                stdout="REPOSITORY:TAG  ID  SIZE  CREATED\nother:v1  def  50MB  1 day"
            ),
        )

        ls(all_tags=True, json_output=False)

        captured = capsys.readouterr()
        assert "Local images:" in captured.out

    def test_ls_json_filters_by_custom_image_basename(self, mocker, monkeypatch, capsys):
        """ls --json with custom IMAGE should filter by image basename, not hardcoded name."""
        import json

        from rots.commands.image.app import ls

        monkeypatch.setenv("IMAGE", "custom.registry.io/myapp")

        # Provide JSON with myapp entries and other entries
        podman_output = json.dumps(
            [
                {"Names": ["custom.registry.io/myapp:v1.0"], "Id": "aaa"},
                {"Names": ["myapp:v1.0"], "Id": "bbb"},
                {"Names": ["other-image:latest"], "Id": "ccc"},
                {"Names": ["docker.io/library/nginx:latest"], "Id": "ddd"},
            ]
        )
        mocker.patch(
            "rots.podman.subprocess.run",
            return_value=mocker.MagicMock(stdout=podman_output),
        )

        ls(all_tags=False, json_output=True)

        captured = capsys.readouterr()
        result = json.loads(captured.out)
        # Should include both entries with "myapp" in Names
        assert len(result) == 2
        ids = [img["Id"] for img in result]
        assert "aaa" in ids  # custom.registry.io/myapp:v1.0
        assert "bbb" in ids  # myapp:v1.0
        # Should NOT include other-image or nginx
        assert "ccc" not in ids
        assert "ddd" not in ids


class TestPullEnvVarResolution:
    """Test that pull correctly resolves IMAGE and TAG from env vars.

    These tests let Config() construct for real so the env var -> config -> command
    resolution pipeline is tested end-to-end. Only external side effects
    (podman subprocess calls, SQLite database operations) are mocked.
    """

    @pytest.fixture(autouse=True)
    def _clear_env(self, monkeypatch):
        """Remove IMAGE and TAG env vars so tests start clean."""
        monkeypatch.delenv("IMAGE", raising=False)
        monkeypatch.delenv("TAG", raising=False)

    def _mock_externals(self, mocker, tmp_path):
        """Mock podman subprocess and db calls, return the mocks.

        Uses tmp_path for db_path so Config.db_path resolution does not
        touch the real filesystem.
        """
        mock_run = mocker.patch(
            "rots.podman.subprocess.run",
            return_value=mocker.MagicMock(stdout="", returncode=0),
        )
        mock_record = mocker.patch("rots.commands.image.app.db.record_deployment")
        mock_set_current = mocker.patch("rots.commands.image.app.db.set_current")
        mocker.patch(
            "rots.commands.image.app.db.get_current_image",
            return_value=None,
        )

        # Point db_path to tmp_path so the real filesystem is never consulted
        mocker.patch(
            "rots.config.Config.db_path",
            new_callable=mocker.PropertyMock,
            return_value=tmp_path / "deployments.db",
        )

        return mock_run, mock_record, mock_set_current

    def test_pull_uses_image_env_var(self, mocker, monkeypatch, tmp_path):
        """Scenario 1: IMAGE env var should be passed through to podman.pull."""
        from rots.commands.image.app import pull

        monkeypatch.setenv("IMAGE", "custom.registry.io/myorg/myapp")
        monkeypatch.setenv("TAG", "v1.0.0")

        mock_run, _, _ = self._mock_externals(mocker, tmp_path)

        pull()

        # The podman subprocess should receive the custom image
        cmd = mock_run.call_args[0][0]
        full_ref = " ".join(cmd)
        assert "custom.registry.io/myorg/myapp:v1.0.0" in full_ref

    def test_pull_uses_tag_env_var(self, mocker, monkeypatch, tmp_path):
        """Scenario 2: TAG env var (no --tag flag) should be used for the pull."""
        from rots.commands.image.app import pull

        monkeypatch.setenv("TAG", "v2.5.0")

        mock_run, _, _ = self._mock_externals(mocker, tmp_path)

        pull()

        cmd = mock_run.call_args[0][0]
        full_ref = " ".join(cmd)
        # Default image with the env-var tag
        assert "ghcr.io/onetimesecret/onetimesecret:v2.5.0" in full_ref

    def test_pull_uses_both_image_and_tag_env_vars(self, mocker, monkeypatch, tmp_path):
        """Scenario 3: Both IMAGE and TAG env vars produce the correct full reference."""
        from rots.commands.image.app import pull

        monkeypatch.setenv("IMAGE", "docker.io/onetimesecret/onetimesecret")
        monkeypatch.setenv("TAG", "v0.23.0-rc1")

        mock_run, mock_record, _ = self._mock_externals(mocker, tmp_path)

        pull()

        cmd = mock_run.call_args[0][0]
        full_ref = " ".join(cmd)
        assert "docker.io/onetimesecret/onetimesecret:v0.23.0-rc1" in full_ref

        # Also verify the db record uses the resolved values
        mock_record.assert_called()
        record_kwargs = mock_record.call_args
        assert record_kwargs.kwargs.get("image") == "docker.io/onetimesecret/onetimesecret"
        assert record_kwargs.kwargs.get("tag") == "v0.23.0-rc1"

    def test_pull_cli_image_overrides_env_var(self, mocker, monkeypatch, tmp_path):
        """Scenario 4: --image CLI flag takes precedence over IMAGE env var."""
        from rots.commands.image.app import pull

        monkeypatch.setenv("IMAGE", "env-var-image/should-not-be-used")
        monkeypatch.setenv("TAG", "v1.0.0")

        mock_run, _, _ = self._mock_externals(mocker, tmp_path)

        pull(image="cli-override-image/myapp")

        cmd = mock_run.call_args[0][0]
        full_ref = " ".join(cmd)
        assert "cli-override-image/myapp:v1.0.0" in full_ref
        assert "env-var-image" not in full_ref

    def test_pull_cli_tag_overrides_env_var(self, mocker, monkeypatch, tmp_path):
        """Scenario 5: --tag CLI flag takes precedence over TAG env var."""
        from rots.commands.image.app import pull

        monkeypatch.setenv("TAG", "env-tag-should-not-be-used")

        mock_run, _, _ = self._mock_externals(mocker, tmp_path)

        pull(tag="cli-tag-override")

        cmd = mock_run.call_args[0][0]
        full_ref = " ".join(cmd)
        assert "cli-tag-override" in full_ref
        assert "env-tag-should-not-be-used" not in full_ref


class TestSetCurrentEnvVarResolution:
    """Test that set-current correctly resolves IMAGE from env vars.

    Like the pull tests, Config() is constructed for real to verify
    the full env var resolution pipeline.
    """

    @pytest.fixture(autouse=True)
    def _clear_env(self, monkeypatch):
        """Remove IMAGE and TAG env vars so tests start clean."""
        monkeypatch.delenv("IMAGE", raising=False)
        monkeypatch.delenv("TAG", raising=False)

    def _mock_externals(self, mocker, tmp_path):
        """Mock db calls, db_path, and podman subprocess. Return set_current mock."""
        mock_set_current = mocker.patch(
            "rots.commands.image.app.db.set_current",
            return_value=None,
        )
        mocker.patch(
            "rots.commands.image.app.db.get_current_image",
            return_value=None,
        )
        mocker.patch(
            "rots.config.Config.db_path",
            new_callable=mocker.PropertyMock,
            return_value=tmp_path / "deployments.db",
        )
        # Mock podman subprocess for image inspect and tag calls
        mocker.patch(
            "rots.podman.subprocess.run",
            return_value=mocker.MagicMock(stdout="", returncode=0),
        )
        return mock_set_current

    def test_set_current_uses_image_env_var(self, mocker, monkeypatch, tmp_path):
        """Scenario 6: IMAGE env var flows through to db.set_current."""
        from rots.commands.image.app import set_current

        monkeypatch.setenv("IMAGE", "custom.registry.io/myorg/myapp")

        mock_set_current = self._mock_externals(mocker, tmp_path)

        set_current(tag="v3.0.0")

        mock_set_current.assert_called_once()
        call_args = mock_set_current.call_args
        # Positional: (db_path, image, tag)
        assert call_args[0][1] == "custom.registry.io/myorg/myapp"
        assert call_args[0][2] == "v3.0.0"

    def test_set_current_cli_image_overrides_env_var(self, mocker, monkeypatch, tmp_path):
        """Scenario 7: --image CLI flag takes precedence over IMAGE env var."""
        from rots.commands.image.app import set_current

        monkeypatch.setenv("IMAGE", "env-var-image/should-not-be-used")

        mock_set_current = self._mock_externals(mocker, tmp_path)

        set_current(tag="v3.0.0", image="cli-override/myapp")

        mock_set_current.assert_called_once()
        call_args = mock_set_current.call_args
        assert call_args[0][1] == "cli-override/myapp"
        assert "env-var-image" not in call_args[0][1]
        assert call_args[0][2] == "v3.0.0"


class TestSetCurrentPodmanTag:
    """Test that set-current tags images in the podman store.

    Verifies the podman tag calls that mirror alias state in the local
    image store, so operators can use raw podman commands if needed.
    """

    @pytest.fixture(autouse=True)
    def _clear_env(self, monkeypatch):
        """Remove IMAGE and TAG env vars so tests start clean."""
        monkeypatch.delenv("IMAGE", raising=False)
        monkeypatch.delenv("TAG", raising=False)

    def _mock_externals(self, mocker, tmp_path, current_image=None):
        """Mock db calls, db_path, and podman subprocess.

        Args:
            current_image: Tuple of (image, tag) to return from
                get_current_image, or None for no previous CURRENT.

        Returns the subprocess.run mock for asserting podman commands.
        """
        mocker.patch(
            "rots.commands.image.app.db.set_current",
            return_value=current_image[1] if current_image else None,
        )
        mocker.patch(
            "rots.commands.image.app.db.get_current_image",
            return_value=current_image,
        )
        mocker.patch(
            "rots.config.Config.db_path",
            new_callable=mocker.PropertyMock,
            return_value=tmp_path / "deployments.db",
        )
        mock_run = mocker.patch(
            "rots.podman.subprocess.run",
            return_value=mocker.MagicMock(stdout="", returncode=0),
        )
        return mock_run

    def test_set_current_tags_image_as_current(self, mocker, tmp_path, capsys):
        """set-current should tag the image as :current in podman."""
        from rots.commands.image.app import set_current

        mock_run = self._mock_externals(mocker, tmp_path)

        set_current(tag="v0.23.3")

        # First call: podman image inspect (verify exists)
        # Second call: podman tag ... :current
        calls = mock_run.call_args_list
        assert len(calls) == 2

        inspect_cmd = calls[0][0][0]
        assert "image" in inspect_cmd and "inspect" in inspect_cmd

        tag_cmd = calls[1][0][0]
        assert "tag" in tag_cmd
        tag_cmd_str = " ".join(tag_cmd)
        assert "onetimesecret:v0.23.3" in tag_cmd_str
        assert "onetimesecret:current" in tag_cmd_str

    def test_set_current_tags_previous_as_rollback(self, mocker, tmp_path, caplog):
        """set-current should tag the previous CURRENT as :rollback."""
        from rots.commands.image.app import set_current

        prev = ("ghcr.io/onetimesecret/onetimesecret", "v0.22.0")
        mock_run = self._mock_externals(mocker, tmp_path, current_image=prev)

        with caplog.at_level(logging.INFO):
            set_current(tag="v0.23.3")

        # Calls: inspect, tag :current, tag :rollback
        calls = mock_run.call_args_list
        assert len(calls) == 3

        rollback_tag_cmd = " ".join(calls[2][0][0])
        assert "onetimesecret:v0.22.0" in rollback_tag_cmd
        assert "onetimesecret:rollback" in rollback_tag_cmd

        assert "ROLLBACK set to previous: v0.22.0" in caplog.text

    def test_set_current_fails_if_image_not_local(self, mocker, tmp_path, caplog):
        """set-current should exit with error if image not found locally."""
        import subprocess

        from rots.commands.image.app import set_current

        mocker.patch(
            "rots.commands.image.app.db.get_current_image",
            return_value=None,
        )
        mocker.patch(
            "rots.config.Config.db_path",
            new_callable=mocker.PropertyMock,
            return_value=tmp_path / "deployments.db",
        )
        # Make podman image inspect fail (image not found)
        mocker.patch(
            "rots.podman.subprocess.run",
            side_effect=subprocess.CalledProcessError(125, "podman"),
        )
        mock_set_current = mocker.patch(
            "rots.commands.image.app.db.set_current",
        )

        with pytest.raises(SystemExit) as exc_info:
            with caplog.at_level(logging.ERROR):
                set_current(tag="v99.0.0")

        assert exc_info.value.code == 1
        assert "Image not found locally" in caplog.text
        assert "ots image pull --tag v99.0.0" in caplog.text
        # DB should NOT have been updated
        mock_set_current.assert_not_called()

    def test_set_current_fails_if_podman_tag_fails(self, mocker, tmp_path, caplog):
        """set-current should exit if podman tag fails (DB unchanged)."""
        import subprocess

        from rots.commands.image.app import set_current

        mocker.patch(
            "rots.commands.image.app.db.get_current_image",
            return_value=None,
        )
        mocker.patch(
            "rots.config.Config.db_path",
            new_callable=mocker.PropertyMock,
            return_value=tmp_path / "deployments.db",
        )
        mock_set_current = mocker.patch(
            "rots.commands.image.app.db.set_current",
        )

        call_count = 0

        def inspect_succeeds_tag_fails(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # podman image inspect succeeds
                return mocker.MagicMock(stdout="", returncode=0)
            # podman tag fails
            raise subprocess.CalledProcessError(125, "podman")

        mocker.patch(
            "rots.podman.subprocess.run",
            side_effect=inspect_succeeds_tag_fails,
        )

        with pytest.raises(SystemExit) as exc_info:
            with caplog.at_level(logging.ERROR):
                set_current(tag="v0.23.3")

        assert exc_info.value.code == 1
        assert "Failed to tag image in podman" in caplog.text
        mock_set_current.assert_not_called()


class TestRollbackPodmanTag:
    """Test that rollback updates podman tags."""

    @pytest.fixture(autouse=True)
    def _clear_env(self, monkeypatch):
        """Remove IMAGE and TAG env vars so tests start clean."""
        monkeypatch.delenv("IMAGE", raising=False)
        monkeypatch.delenv("TAG", raising=False)

    def test_rollback_tags_in_podman(self, mocker, tmp_path, caplog):
        """rollback should tag the new current and old current in podman."""
        from rots.commands.image.app import rollback

        image = "ghcr.io/onetimesecret/onetimesecret"
        mocker.patch(
            "rots.commands.image.app.db.get_current_image",
            return_value=(image, "v0.23.3"),
        )
        mocker.patch(
            "rots.commands.image.app.db.get_previous_tags",
            return_value=[
                (image, "v0.23.3", "2025-01-01"),
                (image, "v0.22.0", "2024-12-01"),
            ],
        )
        mocker.patch(
            "rots.commands.image.app.db.rollback",
            return_value=(image, "v0.22.0"),
        )
        mocker.patch(
            "rots.config.Config.db_path",
            new_callable=mocker.PropertyMock,
            return_value=tmp_path / "deployments.db",
        )
        mock_run = mocker.patch(
            "rots.podman.subprocess.run",
            return_value=mocker.MagicMock(stdout="", returncode=0),
        )

        with caplog.at_level(logging.INFO):
            rollback()

        calls = mock_run.call_args_list
        assert len(calls) == 2

        # First: tag new current
        current_tag_cmd = " ".join(calls[0][0][0])
        assert "onetimesecret:v0.22.0" in current_tag_cmd
        assert "onetimesecret:current" in current_tag_cmd

        # Second: tag old current as rollback
        rollback_tag_cmd = " ".join(calls[1][0][0])
        assert "onetimesecret:v0.23.3" in rollback_tag_cmd
        assert "onetimesecret:rollback" in rollback_tag_cmd

        assert "Rollback complete" in caplog.text

    def test_rollback_warns_on_podman_tag_failure(self, mocker, tmp_path, caplog):
        """rollback should warn but not abort if podman tag fails."""
        from rots.commands.image.app import rollback

        image = "ghcr.io/onetimesecret/onetimesecret"
        mocker.patch(
            "rots.commands.image.app.db.get_current_image",
            return_value=(image, "v0.23.3"),
        )
        mocker.patch(
            "rots.commands.image.app.db.get_previous_tags",
            return_value=[
                (image, "v0.23.3", "2025-01-01"),
                (image, "v0.22.0", "2024-12-01"),
            ],
        )
        mocker.patch(
            "rots.commands.image.app.db.rollback",
            return_value=(image, "v0.22.0"),
        )
        mocker.patch(
            "rots.config.Config.db_path",
            new_callable=mocker.PropertyMock,
            return_value=tmp_path / "deployments.db",
        )
        mocker.patch(
            "rots.podman.subprocess.run",
            side_effect=Exception("podman tag failed"),
        )

        # Should NOT raise — rollback continues despite tag failure
        with caplog.at_level(logging.INFO):
            rollback()

        assert "podman tag failed" in caplog.text
        assert "Rollback complete" in caplog.text

    def test_rollback_without_apply_prints_hint(self, mocker, tmp_path, caplog):
        """rollback without --apply should print 'To apply: ots instance redeploy'."""
        from rots.commands.image.app import rollback

        image = "ghcr.io/onetimesecret/onetimesecret"
        mocker.patch(
            "rots.commands.image.app.db.get_current_image",
            return_value=(image, "v0.23.3"),
        )
        mocker.patch(
            "rots.commands.image.app.db.get_previous_tags",
            return_value=[
                (image, "v0.23.3", "2025-01-01"),
                (image, "v0.22.0", "2024-12-01"),
            ],
        )
        mocker.patch(
            "rots.commands.image.app.db.rollback",
            return_value=(image, "v0.22.0"),
        )
        mocker.patch(
            "rots.config.Config.db_path",
            new_callable=mocker.PropertyMock,
            return_value=tmp_path / "deployments.db",
        )
        mocker.patch(
            "rots.podman.subprocess.run",
            return_value=mocker.MagicMock(stdout="", returncode=0),
        )

        with caplog.at_level(logging.INFO):
            rollback(apply=False)

        assert "To apply: ots instance redeploy" in caplog.text

    def test_rollback_with_apply_calls_redeploy(self, mocker, tmp_path, caplog):
        """rollback --apply should call redeploy after updating aliases."""

        from rots.commands.image.app import rollback

        image = "ghcr.io/onetimesecret/onetimesecret"
        mocker.patch(
            "rots.commands.image.app.db.get_current_image",
            return_value=(image, "v0.23.3"),
        )
        mocker.patch(
            "rots.commands.image.app.db.get_previous_tags",
            return_value=[
                (image, "v0.23.3", "2025-01-01"),
                (image, "v0.22.0", "2024-12-01"),
            ],
        )
        mocker.patch(
            "rots.commands.image.app.db.rollback",
            return_value=(image, "v0.22.0"),
        )
        mocker.patch(
            "rots.config.Config.db_path",
            new_callable=mocker.PropertyMock,
            return_value=tmp_path / "deployments.db",
        )
        mocker.patch(
            "rots.podman.subprocess.run",
            return_value=mocker.MagicMock(stdout="", returncode=0),
        )
        mock_redeploy = mocker.patch(
            "rots.commands.instance.app.redeploy",
        )

        with caplog.at_level(logging.INFO):
            rollback(apply=True, delay=0)

        # Should have called redeploy to apply the rollback
        mock_redeploy.assert_called_once_with(identifiers=(), delay=0)
        assert "Applying rollback" in caplog.text
        assert "To apply: ots instance redeploy" not in caplog.text

    def test_rollback_with_apply_does_not_print_hint(self, mocker, tmp_path, caplog):
        """rollback --apply should not print the manual 'To apply:' hint."""
        from rots.commands.image.app import rollback

        image = "ghcr.io/onetimesecret/onetimesecret"
        mocker.patch(
            "rots.commands.image.app.db.get_current_image",
            return_value=(image, "v0.23.3"),
        )
        mocker.patch(
            "rots.commands.image.app.db.get_previous_tags",
            return_value=[
                (image, "v0.23.3", "2025-01-01"),
                (image, "v0.22.0", "2024-12-01"),
            ],
        )
        mocker.patch(
            "rots.commands.image.app.db.rollback",
            return_value=(image, "v0.22.0"),
        )
        mocker.patch(
            "rots.config.Config.db_path",
            new_callable=mocker.PropertyMock,
            return_value=tmp_path / "deployments.db",
        )
        mocker.patch(
            "rots.podman.subprocess.run",
            return_value=mocker.MagicMock(stdout="", returncode=0),
        )
        mocker.patch("rots.commands.instance.app.redeploy")

        with caplog.at_level(logging.INFO):
            rollback(apply=True, delay=0)

        assert "To apply: ots instance redeploy" not in caplog.text


class TestPullPositionalReference:
    """Test that pull accepts a full image reference as positional arg.

    The reference is parsed into image and tag components. Named flags
    (--image, --tag) take precedence over the parsed reference.
    """

    @pytest.fixture(autouse=True)
    def _clear_env(self, monkeypatch):
        monkeypatch.delenv("IMAGE", raising=False)
        monkeypatch.delenv("TAG", raising=False)

    def _mock_externals(self, mocker, tmp_path):
        mock_run = mocker.patch(
            "rots.podman.subprocess.run",
            return_value=mocker.MagicMock(stdout="", returncode=0),
        )
        mocker.patch("rots.commands.image.app.db.record_deployment")
        mocker.patch("rots.commands.image.app.db.set_current")
        mocker.patch(
            "rots.commands.image.app.db.get_current_image",
            return_value=None,
        )
        mocker.patch(
            "rots.config.Config.db_path",
            new_callable=mocker.PropertyMock,
            return_value=tmp_path / "deployments.db",
        )
        return mock_run

    def test_pull_full_reference(self, mocker, tmp_path):
        """Full reference like registry.io/org/image:tag should work."""
        from rots.commands.image.app import pull

        mock_run = self._mock_externals(mocker, tmp_path)

        pull(reference="registry.example.com/org/image:v1.0")

        cmd = mock_run.call_args[0][0]
        full_ref = " ".join(cmd)
        assert "registry.example.com/org/image:v1.0" in full_ref

    def test_pull_reference_without_tag_falls_back_to_tag_flag(self, mocker, tmp_path):
        """Reference without colon should use --tag for the tag portion."""
        from rots.commands.image.app import pull

        mock_run = self._mock_externals(mocker, tmp_path)

        pull(reference="registry.example.com/org/image", tag="v2.0")

        cmd = mock_run.call_args[0][0]
        full_ref = " ".join(cmd)
        assert "registry.example.com/org/image:v2.0" in full_ref

    def test_pull_reference_without_tag_falls_back_to_env(self, mocker, monkeypatch, tmp_path):
        """Reference without tag and no --tag flag falls back to TAG env var."""
        from rots.commands.image.app import pull

        monkeypatch.setenv("TAG", "env-tag")
        mock_run = self._mock_externals(mocker, tmp_path)

        pull(reference="registry.example.com/org/image")

        cmd = mock_run.call_args[0][0]
        full_ref = " ".join(cmd)
        assert "registry.example.com/org/image:env-tag" in full_ref

    def test_pull_tag_flag_overrides_reference_tag(self, mocker, tmp_path):
        """--tag flag should override the tag parsed from the reference."""
        from rots.commands.image.app import pull

        mock_run = self._mock_externals(mocker, tmp_path)

        pull(reference="registry.example.com/org/image:ref-tag", tag="override-tag")

        cmd = mock_run.call_args[0][0]
        full_ref = " ".join(cmd)
        assert "registry.example.com/org/image:override-tag" in full_ref
        assert "ref-tag" not in full_ref

    def test_pull_image_flag_overrides_reference_image(self, mocker, tmp_path):
        """--image flag should override the image parsed from the reference."""
        from rots.commands.image.app import pull

        mock_run = self._mock_externals(mocker, tmp_path)

        pull(reference="registry.example.com/org/image:v1.0", image="other.io/myapp")

        cmd = mock_run.call_args[0][0]
        full_ref = " ".join(cmd)
        assert "other.io/myapp:v1.0" in full_ref
        assert "registry.example.com" not in full_ref

    def test_pull_both_flags_override_reference(self, mocker, tmp_path):
        """Both --image and --tag flags should fully override the reference."""
        from rots.commands.image.app import pull

        mock_run = self._mock_externals(mocker, tmp_path)

        pull(
            reference="registry.example.com/org/image:ref-tag",
            image="override.io/app",
            tag="override-tag",
        )

        cmd = mock_run.call_args[0][0]
        full_ref = " ".join(cmd)
        assert "override.io/app:override-tag" in full_ref

    def test_pull_reference_with_current_flag(self, mocker, tmp_path, capsys):
        """Full reference with --current should set the alias."""
        from rots.commands.image.app import pull

        mock_run = self._mock_externals(mocker, tmp_path)
        mock_set_current = mocker.patch(
            "rots.commands.image.app.db.set_current",
            return_value=None,
        )

        pull(reference="registry.example.com/org/image:v1.0", set_as_current=True)

        # Should have called pull + tag :current
        assert mock_run.call_count == 2
        mock_set_current.assert_called_once()

    def test_pull_no_reference_no_tag_rejects_sentinel(self, mocker, monkeypatch, tmp_path, caplog):
        """No reference, no --tag, empty TAG env var falls back to
        @current sentinel which pull rejects."""
        from rots.commands.image.app import pull

        monkeypatch.setenv("TAG", "")
        self._mock_externals(mocker, tmp_path)

        with pytest.raises(SystemExit) as exc_info:
            with caplog.at_level(logging.ERROR):
                pull()

        assert exc_info.value.code == 1
        assert "alias" in caplog.text.lower()

    def test_pull_reference_with_trailing_colon(self, mocker, monkeypatch, tmp_path):
        """Reference ending with colon should treat it as image-only (no tag)."""
        from rots.commands.image.app import pull

        monkeypatch.setenv("TAG", "fallback")
        mock_run = self._mock_externals(mocker, tmp_path)

        pull(reference="registry.example.com/org/image:")

        cmd = mock_run.call_args[0][0]
        full_ref = " ".join(cmd)
        assert "registry.example.com/org/image:fallback" in full_ref


class TestPullCurrentPodmanTag:
    """Test that pull --current tags the image in podman."""

    @pytest.fixture(autouse=True)
    def _clear_env(self, monkeypatch):
        """Remove IMAGE and TAG env vars so tests start clean."""
        monkeypatch.delenv("IMAGE", raising=False)
        monkeypatch.delenv("TAG", raising=False)

    def test_pull_current_tags_in_podman(self, mocker, monkeypatch, tmp_path, caplog):
        """pull --current should tag the pulled image as :current."""
        from rots.commands.image.app import pull

        monkeypatch.setenv("TAG", "v0.23.3")

        mock_run = mocker.patch(
            "rots.podman.subprocess.run",
            return_value=mocker.MagicMock(stdout="", returncode=0),
        )
        mocker.patch("rots.commands.image.app.db.record_deployment")
        mocker.patch(
            "rots.commands.image.app.db.set_current",
            return_value=None,
        )
        mocker.patch(
            "rots.commands.image.app.db.get_current_image",
            return_value=None,
        )
        mocker.patch(
            "rots.config.Config.db_path",
            new_callable=mocker.PropertyMock,
            return_value=tmp_path / "deployments.db",
        )

        with caplog.at_level(logging.INFO):
            pull(set_as_current=True)

        # Calls: podman pull, podman tag :current
        calls = mock_run.call_args_list
        assert len(calls) == 2

        pull_cmd = " ".join(calls[0][0][0])
        assert "pull" in pull_cmd

        tag_cmd = " ".join(calls[1][0][0])
        assert "tag" in tag_cmd
        assert "onetimesecret:v0.23.3" in tag_cmd
        assert "onetimesecret:current" in tag_cmd

        assert "Set CURRENT to v0.23.3" in caplog.text

    def test_pull_current_with_previous_tags_rollback(
        self,
        mocker,
        monkeypatch,
        tmp_path,
        capsys,
    ):
        """pull --current with existing CURRENT should also tag :rollback."""
        from rots.commands.image.app import pull

        monkeypatch.setenv("TAG", "v0.23.3")
        image = "ghcr.io/onetimesecret/onetimesecret"

        mock_run = mocker.patch(
            "rots.podman.subprocess.run",
            return_value=mocker.MagicMock(stdout="", returncode=0),
        )
        mocker.patch("rots.commands.image.app.db.record_deployment")
        mocker.patch(
            "rots.commands.image.app.db.set_current",
            return_value="v0.22.0",
        )
        mocker.patch(
            "rots.commands.image.app.db.get_current_image",
            return_value=(image, "v0.22.0"),
        )
        mocker.patch(
            "rots.config.Config.db_path",
            new_callable=mocker.PropertyMock,
            return_value=tmp_path / "deployments.db",
        )

        pull(set_as_current=True)

        # Calls: podman pull, podman tag :current, podman tag :rollback
        calls = mock_run.call_args_list
        assert len(calls) == 3

        current_tag_cmd = " ".join(calls[1][0][0])
        assert "onetimesecret:current" in current_tag_cmd

        rollback_tag_cmd = " ".join(calls[2][0][0])
        assert "onetimesecret:v0.22.0" in rollback_tag_cmd
        assert "onetimesecret:rollback" in rollback_tag_cmd


class TestPullPrivateRegistry:
    """Test that pull --private uses OTS_REGISTRY env var for private image path.

    These tests use a real Config() to verify the env var -> config -> command
    resolution pipeline end-to-end. Only external side effects are mocked.
    """

    @pytest.fixture(autouse=True)
    def _clear_env(self, monkeypatch):
        """Remove relevant env vars so tests start clean."""
        monkeypatch.delenv("IMAGE", raising=False)
        monkeypatch.delenv("TAG", raising=False)
        monkeypatch.delenv("OTS_REGISTRY", raising=False)

    def _mock_externals(self, mocker, tmp_path):
        """Mock podman subprocess and db calls, return the mocks."""
        mock_run = mocker.patch(
            "rots.podman.subprocess.run",
            return_value=mocker.MagicMock(stdout="", returncode=0),
        )
        mock_record = mocker.patch("rots.commands.image.app.db.record_deployment")
        mocker.patch("rots.commands.image.app.db.set_current")

        mocker.patch(
            "rots.config.Config.db_path",
            new_callable=mocker.PropertyMock,
            return_value=tmp_path / "deployments.db",
        )

        return mock_run, mock_record

    def test_pull_private_uses_registry_env_var(self, mocker, monkeypatch, tmp_path):
        """Scenario 16: pull --private with OTS_REGISTRY should use private image path."""
        from rots.commands.image.app import pull

        monkeypatch.setenv("OTS_REGISTRY", "registry.example.com")
        monkeypatch.setenv("TAG", "v1.0.0")

        mock_run, _ = self._mock_externals(mocker, tmp_path)

        pull(private=True)

        # The podman subprocess should receive the private registry image
        cmd = mock_run.call_args[0][0]
        full_ref = " ".join(cmd)
        assert "registry.example.com/onetimesecret:v1.0.0" in full_ref

    def test_pull_private_without_registry_exits(self, mocker, monkeypatch, tmp_path, caplog):
        """Scenario 17: pull --private without OTS_REGISTRY should exit with error."""
        from rots.commands.image.app import pull

        monkeypatch.setenv("TAG", "v1.0.0")
        # OTS_REGISTRY is NOT set

        self._mock_externals(mocker, tmp_path)

        with pytest.raises(SystemExit) as exc_info:
            with caplog.at_level(logging.ERROR):
                pull(private=True)

        assert exc_info.value.code == 1
        assert "OTS_REGISTRY" in caplog.text


class TestRegistryEnvVarResolution:
    """Test that login, list-remote, and push resolve OTS_REGISTRY from env var.

    These tests use a real Config() so the env var resolution pipeline is
    tested end-to-end. Only external calls (podman, skopeo, db) are mocked.
    """

    @pytest.fixture(autouse=True)
    def _clear_env(self, monkeypatch):
        """Remove relevant env vars so tests start clean."""
        monkeypatch.delenv("IMAGE", raising=False)
        monkeypatch.delenv("TAG", raising=False)
        monkeypatch.delenv("OTS_REGISTRY", raising=False)
        monkeypatch.delenv("OTS_REGISTRY_USER", raising=False)
        monkeypatch.delenv("OTS_REGISTRY_PASSWORD", raising=False)

    def test_login_uses_registry_env_var(self, mocker, monkeypatch, tmp_path, caplog):
        """Scenario 20: login with OTS_REGISTRY env var should resolve registry."""
        from rots.commands.image.app import login

        monkeypatch.setenv("OTS_REGISTRY", "registry.example.com")

        mocker.patch(
            "rots.config.Config.db_path",
            new_callable=mocker.PropertyMock,
            return_value=tmp_path / "deployments.db",
        )

        # Mock user input for credentials
        mocker.patch("builtins.input", return_value="testuser")
        mocker.patch("getpass.getpass", return_value="testpass")

        # Mock podman.login via subprocess
        mock_run = mocker.patch(
            "rots.podman.subprocess.run",
            return_value=mocker.MagicMock(stdout="Login Succeeded", returncode=0),
        )

        with caplog.at_level(logging.INFO):
            login()

        # Verify podman login was called with the registry from env var
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        full_cmd = " ".join(cmd)
        assert "registry.example.com" in full_cmd

        assert "registry.example.com" in caplog.text

    def test_list_remote_uses_registry_env_var(self, mocker, monkeypatch, tmp_path):
        """Scenario 21: list-remote with OTS_REGISTRY env var should resolve registry."""
        from rots.commands.image.app import list_remote

        monkeypatch.setenv("OTS_REGISTRY", "registry.example.com")

        mocker.patch(
            "rots.config.Config.db_path",
            new_callable=mocker.PropertyMock,
            return_value=tmp_path / "deployments.db",
        )

        # Mock skopeo being available
        mocker.patch("shutil.which", return_value=str(tmp_path / "skopeo"))

        # Mock subprocess.run for skopeo list-tags
        mock_run = mocker.patch(
            "subprocess.run",
            return_value=mocker.MagicMock(
                stdout='{"Tags": ["v1.0.0", "v2.0.0"]}',
                returncode=0,
            ),
        )

        list_remote()

        # Verify skopeo was called with the registry from env var
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        full_cmd = " ".join(cmd)
        assert "registry.example.com" in full_cmd
        assert "docker://registry.example.com/onetimesecret" in full_cmd

    def test_push_uses_registry_env_var(self, mocker, monkeypatch, tmp_path):
        """Scenario 22: push with OTS_REGISTRY env var should use registry for target."""
        from rots.commands.image.app import push

        monkeypatch.setenv("OTS_REGISTRY", "registry.example.com")

        mocker.patch(
            "rots.config.Config.db_path",
            new_callable=mocker.PropertyMock,
            return_value=tmp_path / "deployments.db",
        )

        # Mock podman.tag and podman.push via subprocess
        mock_run = mocker.patch(
            "rots.podman.subprocess.run",
            return_value=mocker.MagicMock(stdout="", returncode=0),
        )

        # Mock db.record_deployment
        mock_record = mocker.patch("rots.commands.image.app.db.record_deployment")

        push(tag="v1.0.0")

        # podman.tag is the first call, podman.push is the second
        assert mock_run.call_count == 2

        # Verify the tag command targets the registry
        tag_cmd = mock_run.call_args_list[0][0][0]
        tag_cmd_str = " ".join(tag_cmd)
        assert "registry.example.com/onetimesecret:v1.0.0" in tag_cmd_str

        # Verify the push command targets the registry
        push_cmd = mock_run.call_args_list[1][0][0]
        push_cmd_str = " ".join(push_cmd)
        assert "registry.example.com/onetimesecret:v1.0.0" in push_cmd_str

        # Verify db record uses the registry
        mock_record.assert_called_once()
        assert mock_record.call_args.kwargs["image"] == "registry.example.com/onetimesecret"
        assert mock_record.call_args.kwargs["tag"] == "v1.0.0"


class TestPushEnvVarResolution:
    """Test that push resolves TAG and IMAGE from env vars.

    push() reads TAG via cfg.tag and IMAGE via cfg.image when no CLI flags are
    given.  Only external side effects (podman subprocess, db) are mocked.
    """

    @pytest.fixture(autouse=True)
    def _clear_env(self, monkeypatch):
        """Remove relevant env vars so tests start clean."""
        monkeypatch.delenv("IMAGE", raising=False)
        monkeypatch.delenv("TAG", raising=False)
        monkeypatch.delenv("OTS_REGISTRY", raising=False)

    def _mock_externals(self, mocker, tmp_path):
        """Mock podman subprocess and db calls, return the subprocess mock."""
        mock_run = mocker.patch(
            "rots.podman.subprocess.run",
            return_value=mocker.MagicMock(stdout="", returncode=0),
        )
        mocker.patch("rots.commands.image.app.db.record_deployment")
        mocker.patch(
            "rots.config.Config.db_path",
            new_callable=mocker.PropertyMock,
            return_value=tmp_path / "deployments.db",
        )
        return mock_run

    def test_push_uses_tag_env_var_when_no_cli_flag(self, mocker, monkeypatch, tmp_path):
        """Scenario: TAG env var (no --tag flag) should be used as the image tag."""
        from rots.commands.image.app import push

        monkeypatch.setenv("TAG", "v1.2.3")
        monkeypatch.setenv("OTS_REGISTRY", "registry.example.com")

        mock_run = self._mock_externals(mocker, tmp_path)

        push()

        # podman.tag and podman.push are both called
        assert mock_run.call_count == 2
        tag_cmd = " ".join(mock_run.call_args_list[0][0][0])
        # Source should include the env-var tag
        assert "v1.2.3" in tag_cmd
        push_cmd = " ".join(mock_run.call_args_list[1][0][0])
        assert "v1.2.3" in push_cmd

    def test_push_derives_src_basename_from_image_env_var(self, mocker, monkeypatch, tmp_path):
        """Scenario: IMAGE env var constructs source_full and target_full correctly."""
        from rots.commands.image.app import push

        monkeypatch.setenv("IMAGE", "ghcr.io/myorg/myapp")
        monkeypatch.setenv("TAG", "v2.0.0")
        monkeypatch.setenv("OTS_REGISTRY", "registry.example.com")

        mock_run = self._mock_externals(mocker, tmp_path)

        push()

        assert mock_run.call_count == 2
        tag_cmd = " ".join(mock_run.call_args_list[0][0][0])
        # source_full = IMAGE:TAG
        assert "ghcr.io/myorg/myapp:v2.0.0" in tag_cmd
        # target_full = REGISTRY/basename:TAG  (basename of "ghcr.io/myorg/myapp" is "myapp")
        assert "registry.example.com/myapp:v2.0.0" in tag_cmd

    def test_push_strips_registry_prefix_from_image_env_var(self, mocker, monkeypatch, tmp_path):
        """Scenario: custom IMAGE env var with registry host produces correct target basename."""
        from rots.commands.image.app import push

        monkeypatch.setenv("IMAGE", "docker.io/myorg/myapp")
        monkeypatch.setenv("TAG", "v3.0.0")
        monkeypatch.setenv("OTS_REGISTRY", "myreg.example.com")

        mock_run = self._mock_externals(mocker, tmp_path)

        push()

        assert mock_run.call_count == 2
        tag_cmd = " ".join(mock_run.call_args_list[0][0][0])
        # basename of "docker.io/myorg/myapp" is "myapp"
        assert "myreg.example.com/myapp:v3.0.0" in tag_cmd
        # source should use the full IMAGE reference
        assert "docker.io/myorg/myapp:v3.0.0" in tag_cmd

    def test_push_missing_tag_exits_with_error(self, mocker, monkeypatch, tmp_path, caplog):
        """Scenario: push with no --tag and TAG env var unset falls back to @current sentinel.

        The @current sentinel is not a valid OCI tag, so the podman tag
        operation will fail.  Empty TAG env var is treated as unset,
        falling back to DEFAULT_TAG (@current).
        """
        from rots.commands.image.app import push

        # Empty TAG falls back to @current sentinel (not a real OCI tag)
        monkeypatch.setenv("TAG", "")
        monkeypatch.setenv("OTS_REGISTRY", "registry.example.com")
        mocker.patch(
            "rots.config.Config.db_path",
            new_callable=mocker.PropertyMock,
            return_value=tmp_path / "deployments.db",
        )

        # Mock subprocess.run to simulate podman tag failure with @current
        mocker.patch(
            "subprocess.run",
            side_effect=Exception("failed (exit 125): podman tag"),
        )

        with pytest.raises(SystemExit) as exc_info:
            with caplog.at_level(logging.ERROR):
                push()

        assert exc_info.value.code == 1
        assert "Failed to tag image" in caplog.text


class TestListRemoteImageResolution:
    """Tests for list_remote IMAGE env var and --image CLI flag resolution.

    list_remote defaults --image to None and resolves the image name from
    cfg.image.split("/")[-1] (the basename). Tests verify this behavior
    for custom IMAGE env vars, --image flag override, and the default case.
    """

    @pytest.fixture(autouse=True)
    def _clear_env(self, monkeypatch):
        """Remove IMAGE/OTS_REGISTRY env vars so tests start from a clean state."""
        monkeypatch.delenv("IMAGE", raising=False)
        monkeypatch.delenv("OTS_REGISTRY", raising=False)

    def _mock_skopeo_call(self, mocker, tmp_path, tags=None):
        """Mock shutil.which (skopeo present) and subprocess.run (skopeo result)."""
        mocker.patch("shutil.which", return_value=str(tmp_path / "skopeo"))
        mocker.patch(
            "rots.config.Config.db_path",
            new_callable=mocker.PropertyMock,
            return_value=tmp_path / "deployments.db",
        )
        mock_run = mocker.patch(
            "subprocess.run",
            return_value=mocker.MagicMock(
                stdout=f'{{"Tags": {tags or ["v1.0.0", "v2.0.0"]}}}',
                returncode=0,
            ),
        )
        return mock_run

    def test_list_remote_image_env_var_basename_passed_to_skopeo(
        self, mocker, monkeypatch, tmp_path
    ):
        """IMAGE=docker.io/myorg/myapp should pass 'myapp' as image basename to skopeo."""

        from rots.commands.image.app import list_remote

        monkeypatch.setenv("IMAGE", "docker.io/myorg/myapp")
        monkeypatch.setenv("OTS_REGISTRY", "registry.example.com")

        mocker.patch("shutil.which", return_value=str(tmp_path / "skopeo"))
        mocker.patch(
            "rots.config.Config.db_path",
            new_callable=mocker.PropertyMock,
            return_value=tmp_path / "deployments.db",
        )
        mock_subrun = mocker.patch(
            "subprocess.run",
            return_value=mocker.MagicMock(
                stdout='{"Tags": ["v1.0.0"]}',
                returncode=0,
            ),
        )

        list_remote(quiet=True)

        # Verify skopeo was called with "myapp" as the image basename
        mock_subrun.assert_called_once()
        cmd = mock_subrun.call_args[0][0]
        full_cmd = " ".join(cmd)
        assert "docker://registry.example.com/myapp" in full_cmd
        # Should NOT use "docker.io/myorg/myapp" directly
        assert "docker.io/myorg/myapp" not in full_cmd

    def test_list_remote_cli_image_flag_overrides_env_var(self, mocker, monkeypatch, tmp_path):
        """--image CLI flag should override IMAGE env var basename resolution."""
        from rots.commands.image.app import list_remote

        monkeypatch.setenv("IMAGE", "docker.io/myorg/myapp")
        monkeypatch.setenv("OTS_REGISTRY", "registry.example.com")

        mocker.patch("shutil.which", return_value=str(tmp_path / "skopeo"))
        mocker.patch(
            "rots.config.Config.db_path",
            new_callable=mocker.PropertyMock,
            return_value=tmp_path / "deployments.db",
        )
        mock_subrun = mocker.patch(
            "subprocess.run",
            return_value=mocker.MagicMock(
                stdout='{"Tags": ["v1.0.0"]}',
                returncode=0,
            ),
        )

        # --image overrides env var
        list_remote(image="custom-image", quiet=True)

        mock_subrun.assert_called_once()
        cmd = mock_subrun.call_args[0][0]
        full_cmd = " ".join(cmd)
        assert "docker://registry.example.com/custom-image" in full_cmd
        # Should NOT use "myapp" from IMAGE env var
        assert "myapp" not in full_cmd

    def test_list_remote_default_image_uses_onetimesecret_basename(
        self, mocker, monkeypatch, tmp_path, capsys
    ):
        """With no IMAGE env var, list_remote uses 'onetimesecret' as basename."""
        from rots.commands.image.app import list_remote

        # No IMAGE env var set (cleared by autouse fixture)
        monkeypatch.setenv("OTS_REGISTRY", "registry.example.com")

        mocker.patch("shutil.which", return_value=str(tmp_path / "skopeo"))
        mocker.patch(
            "rots.config.Config.db_path",
            new_callable=mocker.PropertyMock,
            return_value=tmp_path / "deployments.db",
        )
        mock_subrun = mocker.patch(
            "subprocess.run",
            return_value=mocker.MagicMock(
                stdout='{"Tags": ["v0.23.0"]}',
                returncode=0,
            ),
        )

        list_remote(quiet=True)

        mock_subrun.assert_called_once()
        cmd = mock_subrun.call_args[0][0]
        full_cmd = " ".join(cmd)
        # Default IMAGE env var produces 'onetimesecret' as basename
        assert "docker://registry.example.com/onetimesecret" in full_cmd


class TestRmImageBasenameDerivation:
    """Tests for rm command IMAGE env var basename derivation.

    rm() uses cfg.image.split("/")[-1] as image_basename for the first and
    third patterns in images_to_try (e.g. basename:tag, localhost/basename:tag).
    The second pattern uses the full cfg.image value.
    """

    @pytest.fixture(autouse=True)
    def _clear_env(self, monkeypatch):
        """Remove IMAGE/OTS_REGISTRY env vars before each test."""
        monkeypatch.delenv("IMAGE", raising=False)
        monkeypatch.delenv("OTS_REGISTRY", raising=False)

    def _extract_image_ref(self, cmd):
        """Extract the image reference from a podman rm command list.

        Command is like ["podman", "image", "rm", image_ref] or
        ["podman", "image", "rm", "--force", image_ref].
        The image ref is the last non-flag element.
        """
        for part in reversed(cmd):
            if not part.startswith("--"):
                return part
        return None

    def test_rm_custom_image_env_var_tries_basename_patterns(
        self, mocker, monkeypatch, tmp_path, capsys
    ):
        """IMAGE=docker.io/myorg/myapp tries 'myapp:<tag>', full image, 'localhost/myapp:<tag>'."""
        from rots.commands.image.app import rm

        monkeypatch.setenv("IMAGE", "docker.io/myorg/myapp")
        mocker.patch(
            "rots.config.Config.db_path",
            new_callable=mocker.PropertyMock,
            return_value=tmp_path / "deployments.db",
        )

        attempted_images = []

        def mock_subprocess_run(cmd, **kwargs):
            attempted_images.append(self._extract_image_ref(cmd))
            return mocker.MagicMock(returncode=1, stdout="", stderr="not found")

        mocker.patch("rots.podman.subprocess.run", side_effect=mock_subprocess_run)

        rm(tags=("v1.0.0",), yes=True)

        # Verify the three standard patterns were tried
        assert "myapp:v1.0.0" in attempted_images
        assert "docker.io/myorg/myapp:v1.0.0" in attempted_images
        assert "localhost/myapp:v1.0.0" in attempted_images

    def test_rm_default_image_tries_onetimesecret_patterns(
        self, mocker, monkeypatch, tmp_path, capsys
    ):
        """Default IMAGE (no env var) tries 'onetimesecret:<tag>' as basename."""
        from rots.commands.image.app import rm

        # No IMAGE env var - default is 'ghcr.io/onetimesecret/onetimesecret'
        mocker.patch(
            "rots.config.Config.db_path",
            new_callable=mocker.PropertyMock,
            return_value=tmp_path / "deployments.db",
        )

        attempted_images = []

        def mock_subprocess_run(cmd, **kwargs):
            attempted_images.append(self._extract_image_ref(cmd))
            return mocker.MagicMock(returncode=1, stdout="", stderr="not found")

        mocker.patch("rots.podman.subprocess.run", side_effect=mock_subprocess_run)

        rm(tags=("v0.23.0",), yes=True)

        # Default IMAGE basename is 'onetimesecret'
        assert "onetimesecret:v0.23.0" in attempted_images
        assert "localhost/onetimesecret:v0.23.0" in attempted_images

    def test_rm_with_private_image_adds_fourth_pattern(self, mocker, monkeypatch, tmp_path, capsys):
        """rm with OTS_REGISTRY set includes private registry as fourth pattern."""
        from rots.commands.image.app import rm

        monkeypatch.setenv("IMAGE", "ghcr.io/onetimesecret/onetimesecret")
        monkeypatch.setenv("OTS_REGISTRY", "registry.example.com")
        mocker.patch(
            "rots.config.Config.db_path",
            new_callable=mocker.PropertyMock,
            return_value=tmp_path / "deployments.db",
        )

        attempted_images = []

        def mock_subprocess_run(cmd, **kwargs):
            attempted_images.append(self._extract_image_ref(cmd))
            return mocker.MagicMock(returncode=1, stdout="", stderr="not found")

        mocker.patch("rots.podman.subprocess.run", side_effect=mock_subprocess_run)

        rm(tags=("v0.23.0",), yes=True)

        # Should have 4 patterns: basename, full, localhost/basename, private
        assert len(attempted_images) == 4
        # Fourth pattern is the private registry image
        assert any("registry.example.com" in img for img in attempted_images)

    def test_rm_succeeds_on_first_matching_pattern(self, mocker, monkeypatch, tmp_path, caplog):
        """rm should stop trying patterns once one succeeds."""
        from rots.commands.image.app import rm

        monkeypatch.setenv("IMAGE", "docker.io/myorg/myapp")
        mocker.patch(
            "rots.config.Config.db_path",
            new_callable=mocker.PropertyMock,
            return_value=tmp_path / "deployments.db",
        )

        attempted_images = []

        def mock_subprocess_run(cmd, **kwargs):
            # First call (myapp:v1.0.0) succeeds
            attempted_images.append(self._extract_image_ref(cmd))
            return mocker.MagicMock(returncode=0, stdout="", stderr="")

        mocker.patch("rots.podman.subprocess.run", side_effect=mock_subprocess_run)

        with caplog.at_level(logging.INFO):
            rm(tags=("v1.0.0",), yes=True)

        # Should stop after the first successful removal
        assert len(attempted_images) == 1
        assert attempted_images[0] == "myapp:v1.0.0"

        assert "Removed myapp:v1.0.0" in caplog.text
