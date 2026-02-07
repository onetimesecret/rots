# tests/commands/image/test_app.py
"""Tests for image app commands."""

import pytest


class TestImageAppImports:
    """Test image app structure."""

    def test_image_app_exists(self):
        """Test image app is defined."""
        from ots_containers.commands.image.app import app

        assert app is not None

    def test_rm_function_exists(self):
        """Test rm function is defined."""
        from ots_containers.commands.image.app import rm

        assert rm is not None

    def test_prune_function_exists(self):
        """Test prune function is defined."""
        from ots_containers.commands.image.app import prune

        assert prune is not None

    def test_ls_function_exists(self):
        """Test ls (list) function is defined."""
        from ots_containers.commands.image.app import ls

        assert ls is not None


class TestRmCommand:
    """Test rm command."""

    def test_rm_no_tags_exits(self):
        """Should exit if no tags provided."""
        from ots_containers.commands.image.app import rm

        with pytest.raises(SystemExit) as exc_info:
            rm(tags=(), yes=True)

        assert exc_info.value.code == 1

    def test_rm_aborts_without_confirmation(self, mocker, capsys):
        """Should abort if user doesn't confirm."""
        from ots_containers.commands.image.app import rm

        mocker.patch("builtins.input", return_value="n")

        rm(tags=("v0.22.0",), yes=False)

        captured = capsys.readouterr()
        assert "Aborted" in captured.out

    def test_rm_removes_image_with_yes(self, mocker, capsys):
        """Should remove image when --yes is provided."""
        from ots_containers.commands.image.app import rm

        mock_rmi = mocker.patch(
            "ots_containers.commands.image.app.podman.rmi",
        )

        rm(tags=("v0.22.0",), yes=True)

        mock_rmi.assert_called()
        captured = capsys.readouterr()
        assert "Removed" in captured.out

    def test_rm_tries_multiple_patterns(self, mocker, capsys):
        """Should try multiple image patterns."""
        from ots_containers.commands.image.app import rm

        call_count = 0

        def mock_rmi_fail_twice(*_args, **_kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise Exception("Image not found")
            return mocker.MagicMock()

        mocker.patch(
            "ots_containers.commands.image.app.podman.rmi",
            side_effect=mock_rmi_fail_twice,
        )

        rm(tags=("v0.22.0",), yes=True)

        assert call_count == 3  # Tried 3 patterns before success
        captured = capsys.readouterr()
        assert "Removed" in captured.out

    def test_rm_reports_not_found(self, mocker, capsys):
        """Should report when image not found."""
        from ots_containers.commands.image.app import rm

        mocker.patch(
            "ots_containers.commands.image.app.podman.rmi",
            side_effect=Exception("not found"),
        )

        rm(tags=("nonexistent",), yes=True)

        captured = capsys.readouterr()
        assert "Image not found" in captured.out

    def test_rm_with_force(self, mocker):
        """Should pass force flag to podman."""
        from ots_containers.commands.image.app import rm

        mock_rmi = mocker.patch("ots_containers.commands.image.app.podman.rmi")

        rm(tags=("v0.22.0",), force=True, yes=True)

        # Check force was passed to at least one call
        calls = mock_rmi.call_args_list
        assert any("force" in str(call) for call in calls)


class TestPruneCommand:
    """Test prune command."""

    def test_prune_aborts_without_confirmation(self, mocker, capsys):
        """Should abort if user doesn't confirm."""
        from ots_containers.commands.image.app import prune

        mocker.patch("builtins.input", return_value="n")

        prune(yes=False)

        captured = capsys.readouterr()
        assert "Aborted" in captured.out

    def test_prune_calls_podman(self, mocker, capsys):
        """Should call podman image prune."""
        from ots_containers.commands.image.app import prune

        # Mock subprocess.run since the podman wrapper calls it
        mock_run = mocker.patch(
            "ots_containers.podman.subprocess.run",
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
        from ots_containers.commands.image.app import prune

        # Mock subprocess.run since the podman wrapper calls it
        mock_run = mocker.patch(
            "ots_containers.podman.subprocess.run",
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
        from ots_containers.commands.image.app import prune

        mocker.patch(
            "ots_containers.podman.subprocess.run",
            side_effect=Exception("prune failed"),
        )

        with pytest.raises(SystemExit) as exc_info:
            prune(yes=True)

        assert exc_info.value.code == 1

    def test_prune_prompts_different_for_all(self, mocker, capsys):
        """Should show different prompt for --all."""
        from ots_containers.commands.image.app import prune

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
        from ots_containers.commands.image.app import ls

        mocker.patch(
            "ots_containers.podman.subprocess.run",
            return_value=mocker.MagicMock(
                stdout="REPOSITORY:TAG  ID  SIZE  CREATED\nonetimesecret:v1  abc  100MB  1 day"
            ),
        )

        ls(all_tags=False, json_output=False)

        captured = capsys.readouterr()
        assert "Local images:" in captured.out

    def test_ls_with_json_output(self, mocker, capsys):
        """Should output JSON when --json flag is used."""
        from ots_containers.commands.image.app import ls

        mocker.patch(
            "ots_containers.podman.subprocess.run",
            return_value=mocker.MagicMock(stdout='[{"Names": ["onetimesecret:v1"], "Id": "abc"}]'),
        )

        ls(all_tags=False, json_output=True)

        captured = capsys.readouterr()
        assert "onetimesecret" in captured.out

    def test_ls_with_all_tags(self, mocker, capsys):
        """Should show all images when --all flag is used."""
        from ots_containers.commands.image.app import ls

        mocker.patch(
            "ots_containers.podman.subprocess.run",
            return_value=mocker.MagicMock(
                stdout="REPOSITORY:TAG  ID  SIZE  CREATED\nother:v1  def  50MB  1 day"
            ),
        )

        ls(all_tags=True, json_output=False)

        captured = capsys.readouterr()
        assert "Local images:" in captured.out


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
            "ots_containers.podman.subprocess.run",
            return_value=mocker.MagicMock(stdout="", returncode=0),
        )
        mock_record = mocker.patch("ots_containers.commands.image.app.db.record_deployment")
        mock_set_current = mocker.patch("ots_containers.commands.image.app.db.set_current")

        # Point db_path to tmp_path so the real filesystem is never consulted
        mocker.patch(
            "ots_containers.config.Config.db_path",
            new_callable=mocker.PropertyMock,
            return_value=tmp_path / "deployments.db",
        )

        return mock_run, mock_record, mock_set_current

    def test_pull_uses_image_env_var(self, mocker, monkeypatch, tmp_path):
        """Scenario 1: IMAGE env var should be passed through to podman.pull."""
        from ots_containers.commands.image.app import pull

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
        from ots_containers.commands.image.app import pull

        monkeypatch.setenv("TAG", "v2.5.0")

        mock_run, _, _ = self._mock_externals(mocker, tmp_path)

        pull()

        cmd = mock_run.call_args[0][0]
        full_ref = " ".join(cmd)
        # Default image with the env-var tag
        assert "ghcr.io/onetimesecret/onetimesecret:v2.5.0" in full_ref

    def test_pull_uses_both_image_and_tag_env_vars(self, mocker, monkeypatch, tmp_path):
        """Scenario 3: Both IMAGE and TAG env vars produce the correct full reference."""
        from ots_containers.commands.image.app import pull

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
        from ots_containers.commands.image.app import pull

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
        from ots_containers.commands.image.app import pull

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
        """Mock db calls and db_path, return the set_current mock."""
        mock_set_current = mocker.patch(
            "ots_containers.commands.image.app.db.set_current",
            return_value=None,
        )
        mocker.patch(
            "ots_containers.config.Config.db_path",
            new_callable=mocker.PropertyMock,
            return_value=tmp_path / "deployments.db",
        )
        return mock_set_current

    def test_set_current_uses_image_env_var(self, mocker, monkeypatch, tmp_path):
        """Scenario 6: IMAGE env var flows through to db.set_current."""
        from ots_containers.commands.image.app import set_current

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
        from ots_containers.commands.image.app import set_current

        monkeypatch.setenv("IMAGE", "env-var-image/should-not-be-used")

        mock_set_current = self._mock_externals(mocker, tmp_path)

        set_current(tag="v3.0.0", image="cli-override/myapp")

        mock_set_current.assert_called_once()
        call_args = mock_set_current.call_args
        assert call_args[0][1] == "cli-override/myapp"
        assert "env-var-image" not in call_args[0][1]
        assert call_args[0][2] == "v3.0.0"


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
            "ots_containers.podman.subprocess.run",
            return_value=mocker.MagicMock(stdout="", returncode=0),
        )
        mock_record = mocker.patch("ots_containers.commands.image.app.db.record_deployment")
        mocker.patch("ots_containers.commands.image.app.db.set_current")

        mocker.patch(
            "ots_containers.config.Config.db_path",
            new_callable=mocker.PropertyMock,
            return_value=tmp_path / "deployments.db",
        )

        return mock_run, mock_record

    def test_pull_private_uses_registry_env_var(self, mocker, monkeypatch, tmp_path):
        """Scenario 16: pull --private with OTS_REGISTRY should use private image path."""
        from ots_containers.commands.image.app import pull

        monkeypatch.setenv("OTS_REGISTRY", "registry.example.com")
        monkeypatch.setenv("TAG", "v1.0.0")

        mock_run, _ = self._mock_externals(mocker, tmp_path)

        pull(private=True)

        # The podman subprocess should receive the private registry image
        cmd = mock_run.call_args[0][0]
        full_ref = " ".join(cmd)
        assert "registry.example.com/onetimesecret:v1.0.0" in full_ref

    def test_pull_private_without_registry_exits(self, mocker, monkeypatch, tmp_path, capsys):
        """Scenario 17: pull --private without OTS_REGISTRY should exit with error."""
        from ots_containers.commands.image.app import pull

        monkeypatch.setenv("TAG", "v1.0.0")
        # OTS_REGISTRY is NOT set

        self._mock_externals(mocker, tmp_path)

        with pytest.raises(SystemExit) as exc_info:
            pull(private=True)

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "OTS_REGISTRY" in captured.out


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

    def test_login_uses_registry_env_var(self, mocker, monkeypatch, tmp_path, capsys):
        """Scenario 20: login with OTS_REGISTRY env var should resolve registry."""
        from ots_containers.commands.image.app import login

        monkeypatch.setenv("OTS_REGISTRY", "registry.example.com")

        mocker.patch(
            "ots_containers.config.Config.db_path",
            new_callable=mocker.PropertyMock,
            return_value=tmp_path / "deployments.db",
        )

        # Mock user input for credentials
        mocker.patch("builtins.input", return_value="testuser")
        mocker.patch("getpass.getpass", return_value="testpass")

        # Mock podman.login via subprocess
        mock_run = mocker.patch(
            "ots_containers.podman.subprocess.run",
            return_value=mocker.MagicMock(stdout="Login Succeeded", returncode=0),
        )

        login()

        # Verify podman login was called with the registry from env var
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        full_cmd = " ".join(cmd)
        assert "registry.example.com" in full_cmd

        captured = capsys.readouterr()
        assert "registry.example.com" in captured.out

    def test_list_remote_uses_registry_env_var(self, mocker, monkeypatch, tmp_path):
        """Scenario 21: list-remote with OTS_REGISTRY env var should resolve registry."""
        from ots_containers.commands.image.app import list_remote

        monkeypatch.setenv("OTS_REGISTRY", "registry.example.com")

        mocker.patch(
            "ots_containers.config.Config.db_path",
            new_callable=mocker.PropertyMock,
            return_value=tmp_path / "deployments.db",
        )

        # Mock skopeo being available
        mocker.patch("shutil.which", return_value="/usr/bin/skopeo")

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
        from ots_containers.commands.image.app import push

        monkeypatch.setenv("OTS_REGISTRY", "registry.example.com")

        mocker.patch(
            "ots_containers.config.Config.db_path",
            new_callable=mocker.PropertyMock,
            return_value=tmp_path / "deployments.db",
        )

        # Mock podman.tag and podman.push via subprocess
        mock_run = mocker.patch(
            "ots_containers.podman.subprocess.run",
            return_value=mocker.MagicMock(stdout="", returncode=0),
        )

        # Mock db.record_deployment
        mock_record = mocker.patch("ots_containers.commands.image.app.db.record_deployment")

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
