# tests/test_image_build.py
"""Tests for image build command and helper functions."""

import json
import subprocess

import pytest

from rots.commands.image.app import (
    _determine_build_tag,
    _get_git_hash,
    _is_dev_version,
    _read_package_version,
    _validate_project_dir,
)


class TestIsDevVersion:
    """Test development version detection."""

    def test_zero_version_is_dev(self):
        """0.0.0 should be detected as dev version."""
        assert _is_dev_version("0.0.0") is True

    def test_zero_version_with_suffix_is_dev(self):
        """0.0.0-rc0 should be detected as dev version."""
        assert _is_dev_version("0.0.0-rc0") is True

    def test_rc0_suffix_is_dev(self):
        """Any version ending with -rc0 should be dev."""
        assert _is_dev_version("1.2.3-rc0") is True

    def test_dev_suffix_is_dev(self):
        """Any version ending with -dev should be dev."""
        assert _is_dev_version("0.23.0-dev") is True

    def test_alpha_suffix_is_dev(self):
        """Any version ending with -alpha should be dev."""
        assert _is_dev_version("0.23.0-alpha") is True

    def test_beta_suffix_is_dev(self):
        """Any version ending with -beta should be dev."""
        assert _is_dev_version("0.23.0-beta") is True

    def test_release_version_is_not_dev(self):
        """Normal release versions should not be dev."""
        assert _is_dev_version("0.23.0") is False

    def test_rc1_is_not_dev(self):
        """Release candidates > 0 are not dev versions."""
        assert _is_dev_version("0.23.0-rc1") is False

    def test_patch_version_is_not_dev(self):
        """Patch versions should not be dev."""
        assert _is_dev_version("0.23.1") is False


class TestReadPackageVersion:
    """Test package.json version reading."""

    def test_reads_version_from_package_json(self, tmp_path):
        """Should read version field from package.json."""
        package_json = tmp_path / "package.json"
        package_json.write_text(json.dumps({"name": "test", "version": "0.23.0"}))

        version = _read_package_version(tmp_path)
        assert version == "0.23.0"

    def test_exits_when_package_json_missing(self, tmp_path):
        """Should exit if package.json doesn't exist."""
        with pytest.raises(SystemExit) as exc:
            _read_package_version(tmp_path)
        assert "package.json not found" in str(exc.value)

    def test_exits_when_version_missing(self, tmp_path):
        """Should exit if version field is missing."""
        package_json = tmp_path / "package.json"
        package_json.write_text(json.dumps({"name": "test"}))

        with pytest.raises(SystemExit) as exc:
            _read_package_version(tmp_path)
        assert "No 'version' field" in str(exc.value)

    def test_exits_on_invalid_json(self, tmp_path):
        """Should exit if package.json is invalid JSON."""
        package_json = tmp_path / "package.json"
        package_json.write_text("not valid json")

        with pytest.raises(SystemExit) as exc:
            _read_package_version(tmp_path)
        assert "Invalid package.json" in str(exc.value)


class TestGetGitHash:
    """Test git hash retrieval with dirty indicator."""

    def test_gets_short_hash_clean(self, mocker, tmp_path):
        """Should return 8-character git hash for clean working tree."""
        mock_run = mocker.patch("subprocess.run")
        # First call: git rev-parse, Second call: git status (clean)
        mock_run.side_effect = [
            subprocess.CompletedProcess([], 0, stdout="a1b2c3d4\n", stderr=""),
            subprocess.CompletedProcess([], 0, stdout="", stderr=""),  # Clean
        ]

        result = _get_git_hash(tmp_path)

        assert result == "a1b2c3d4"
        assert mock_run.call_count == 2

    def test_gets_hash_with_dirty_indicator(self, mocker, tmp_path):
        """Should return git hash with * suffix when working tree is dirty."""
        mock_run = mocker.patch("subprocess.run")
        # First call: git rev-parse, Second call: git status (dirty)
        mock_run.side_effect = [
            subprocess.CompletedProcess([], 0, stdout="a1b2c3d4\n", stderr=""),
            subprocess.CompletedProcess([], 0, stdout="M file.txt\n", stderr=""),  # Dirty
        ]

        result = _get_git_hash(tmp_path)

        assert result == "a1b2c3d4*"

    def test_exits_on_git_failure(self, mocker, tmp_path):
        """Should exit with error message on git failure."""
        mock_run = mocker.patch("subprocess.run")
        mock_run.side_effect = subprocess.CalledProcessError(
            returncode=128,
            cmd=["git", "rev-parse"],
            stderr="fatal: not a git repository",
        )

        with pytest.raises(SystemExit) as exc:
            _get_git_hash(tmp_path)
        assert "Failed to get git hash" in str(exc.value)


class TestValidateProjectDir:
    """Test project directory validation."""

    def test_validates_with_containerfile(self, tmp_path):
        """Should pass validation with Containerfile and package.json."""
        (tmp_path / "Containerfile").touch()
        (tmp_path / "package.json").write_text('{"version": "1.0.0"}')

        # Should not raise
        _validate_project_dir(tmp_path)

    def test_validates_with_dockerfile(self, tmp_path):
        """Should pass validation with Dockerfile and package.json."""
        (tmp_path / "Dockerfile").touch()
        (tmp_path / "package.json").write_text('{"version": "1.0.0"}')

        # Should not raise
        _validate_project_dir(tmp_path)

    def test_exits_if_directory_missing(self, tmp_path):
        """Should exit if directory doesn't exist."""
        nonexistent = tmp_path / "nonexistent"

        with pytest.raises(SystemExit) as exc:
            _validate_project_dir(nonexistent)
        assert "not found" in str(exc.value)

    def test_exits_if_no_containerfile(self, tmp_path):
        """Should exit if no Containerfile or Dockerfile."""
        (tmp_path / "package.json").write_text('{"version": "1.0.0"}')

        with pytest.raises(SystemExit) as exc:
            _validate_project_dir(tmp_path)
        assert "No Containerfile or Dockerfile" in str(exc.value)

    def test_exits_if_no_package_json(self, tmp_path):
        """Should exit if no package.json."""
        (tmp_path / "Containerfile").touch()

        with pytest.raises(SystemExit) as exc:
            _validate_project_dir(tmp_path)
        assert "No package.json" in str(exc.value)


class TestDetermineBuildTag:
    """Test build tag determination logic."""

    def test_uses_override_tag_when_provided(self, tmp_path):
        """Should use override tag as-is when provided."""
        (tmp_path / "package.json").write_text('{"version": "0.23.0"}')

        tag = _determine_build_tag(tmp_path, "my-custom-tag")
        assert tag == "my-custom-tag"

    def test_formats_release_version(self, tmp_path):
        """Should format release version as v{version}."""
        (tmp_path / "package.json").write_text('{"version": "0.23.0"}')

        tag = _determine_build_tag(tmp_path, None)
        assert tag == "v0.23.0"

    def test_appends_hash_for_dev_version(self, mocker, tmp_path):
        """Should append git hash for dev versions."""
        (tmp_path / "package.json").write_text('{"version": "0.0.0-rc0"}')

        mock_run = mocker.patch("subprocess.run")
        # First call: git rev-parse, Second call: git status (clean)
        mock_run.side_effect = [
            subprocess.CompletedProcess([], 0, stdout="deadbeef\n", stderr=""),
            subprocess.CompletedProcess([], 0, stdout="", stderr=""),  # Clean
        ]

        tag = _determine_build_tag(tmp_path, None)
        assert tag == "v0.0.0-rc0-deadbeef"


class TestBuildCommand:
    """Test the build command invocation."""

    def test_build_help_exits_zero(self, capsys):
        """ots image build --help should exit with code 0."""
        from rots.cli import app

        with pytest.raises(SystemExit) as exc_info:
            app(["image", "build", "--help"])
        assert exc_info.value.code == 0

    def test_build_help_shows_options(self, capsys):
        """ots image build --help should show all options."""
        from rots.cli import app

        with pytest.raises(SystemExit):
            app(["image", "build", "--help"])
        captured = capsys.readouterr()

        assert "--project-dir" in captured.out or "-d" in captured.out
        assert "--platform" in captured.out
        assert "--push" in captured.out
        assert "--registry" in captured.out or "-r" in captured.out
        assert "--tag" in captured.out or "-t" in captured.out
        assert "--quiet" in captured.out or "-q" in captured.out

    def test_build_validates_project_dir(self, mocker, tmp_path):
        """Build should validate project directory before building."""
        from rots.cli import app

        # tmp_path has no Containerfile or package.json
        with pytest.raises(SystemExit) as exc:
            app(["image", "build", "--project-dir", str(tmp_path)])
        assert "No Containerfile or Dockerfile" in str(exc.value)

    def test_build_executes_podman_buildx(self, mocker, tmp_path):
        """Build should call podman buildx build with correct args."""
        # Set up valid project structure
        (tmp_path / "Containerfile").touch()
        (tmp_path / "package.json").write_text('{"version": "0.23.0"}')

        # Mock var_dir to use tmp_path for database
        var_dir = tmp_path / "var"
        var_dir.mkdir()

        # Mock subprocess.run for both podman and git
        mock_run = mocker.patch("subprocess.run")
        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="",
            stderr="",
        )

        # Mock Config to use tmp_path for database
        mocker.patch(
            "rots.commands.image.app.Config",
            return_value=mocker.Mock(
                db_path=var_dir / "deployments.db",
                image="ghcr.io/onetimesecret/onetimesecret",
                registry=None,
                registry_auth_file=tmp_path / "auth.json",
                get_executor=lambda host=None: None,
            ),
        )

        # Mock db.record_deployment to avoid actual database operations
        mocker.patch("rots.commands.image.app.db.record_deployment")

        from rots.cli import app

        # cyclopts calls sys.exit(0) on success
        with pytest.raises(SystemExit) as exc:
            app(["image", "build", "--project-dir", str(tmp_path), "--quiet"])
        assert exc.value.code == 0

        # Verify podman buildx build was called
        calls = mock_run.call_args_list
        build_call = [c for c in calls if "buildx" in str(c)]
        assert len(build_call) >= 1

    def test_build_push_requires_registry(self, mocker, tmp_path, caplog):
        """Build with --push should require registry."""
        import logging

        # Set up valid project structure
        (tmp_path / "Containerfile").touch()
        (tmp_path / "package.json").write_text('{"version": "0.23.0"}')

        var_dir = tmp_path / "var"
        var_dir.mkdir()

        # Mock subprocess.run for build success
        mock_run = mocker.patch("subprocess.run")
        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="",
            stderr="",
        )

        # Mock Config without registry
        mocker.patch(
            "rots.commands.image.app.Config",
            return_value=mocker.Mock(
                db_path=var_dir / "deployments.db",
                image="ghcr.io/onetimesecret/onetimesecret",
                registry=None,  # No registry configured
                registry_auth_file=tmp_path / "auth.json",
                get_executor=lambda host=None: None,
            ),
        )

        mocker.patch("rots.commands.image.app.db.record_deployment")

        from rots.cli import app

        with pytest.raises(SystemExit) as exc:
            with caplog.at_level(logging.ERROR):
                app(["image", "build", "--project-dir", str(tmp_path), "--push"])
        # Should exit with error code 1
        assert exc.value.code == 1
        # Error message now goes through logger
        assert "--push requires --registry" in caplog.text
