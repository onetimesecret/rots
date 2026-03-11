# tests/commands/image/test_build.py
"""Tests for image build command.

These tests verify the build command functionality for building
OTS container images from source with proper version detection
and podman buildx invocation.
"""

import json
import subprocess

import pytest

from rots.commands.image.app import _load_oci_build_config, build


class TestBuildVersionDetection:
    """Test version extraction from package.json."""

    def test_build_detects_version_from_package_json(self, mocker, tmp_path):
        """build should extract version from package.json."""
        # Create a mock project directory with package.json
        project_dir = tmp_path / "onetimesecret"
        project_dir.mkdir()

        package_json = project_dir / "package.json"
        package_json.write_text(json.dumps({"version": "0.25.0"}))

        containerfile = project_dir / "Containerfile"
        containerfile.write_text("FROM ruby:3.2\n")

        var_dir = tmp_path / "var"
        var_dir.mkdir()

        # Mock subprocess.run for git and podman
        def mock_run_factory(cmd, *args, **kwargs):
            if "git" in cmd:
                return subprocess.CompletedProcess(cmd, 0, stdout="abc12345\n", stderr="")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        mock_run = mocker.patch("subprocess.run", side_effect=mock_run_factory)

        # Mock Config
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
        mocker.patch("rots.commands.image.app.db.record_deployment")

        build(project_dir=project_dir, quiet=True)

        # Verify podman buildx was called with the version tag
        calls = mock_run.call_args_list
        buildx_calls = [c for c in calls if "buildx" in str(c)]
        assert len(buildx_calls) >= 1
        # Check that v0.25.0 is in the tag
        assert any("v0.25.0" in str(c) for c in buildx_calls)

    def test_build_uses_git_hash_for_dev_version(self, mocker, tmp_path):
        """build should use git commit hash for dev versions (0.0.0-rc0, etc.)."""
        # Create a mock project directory with dev version
        project_dir = tmp_path / "onetimesecret"
        project_dir.mkdir()

        package_json = project_dir / "package.json"
        package_json.write_text(json.dumps({"version": "0.0.0-rc0"}))

        containerfile = project_dir / "Containerfile"
        containerfile.write_text("FROM ruby:3.2\n")

        var_dir = tmp_path / "var"
        var_dir.mkdir()

        # Mock git rev-parse and status to return a commit hash
        git_hash = "abc12345"

        def mock_run_side_effect(cmd, *args, **kwargs):
            if "git" in cmd and "rev-parse" in cmd:
                return subprocess.CompletedProcess(cmd, 0, stdout=git_hash + "\n", stderr="")
            if "git" in cmd and "status" in cmd:
                return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")  # Clean
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        mock_run = mocker.patch("subprocess.run", side_effect=mock_run_side_effect)

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
        mocker.patch("rots.commands.image.app.db.record_deployment")

        build(project_dir=project_dir, quiet=True)

        # Verify the git hash was used in the tag
        calls = [str(call) for call in mock_run.call_args_list]
        # Should have git rev-parse, status, and buildx calls
        assert any("rev-parse" in c for c in calls)
        buildx_calls = [c for c in calls if "buildx" in c]
        assert len(buildx_calls) >= 1
        assert any(git_hash in c for c in buildx_calls)


class TestBuildValidation:
    """Test project directory validation."""

    def test_build_validates_project_directory_missing_containerfile(self, mocker, tmp_path):
        """build should error when Containerfile is missing."""
        project_dir = tmp_path / "onetimesecret"
        project_dir.mkdir()

        # Create package.json but no Containerfile
        package_json = project_dir / "package.json"
        package_json.write_text(json.dumps({"version": "0.25.0"}))

        with pytest.raises(SystemExit) as exc:
            build(project_dir=project_dir)
        assert "No Containerfile or Dockerfile" in str(exc.value)

    def test_build_validates_project_directory_missing_package_json(self, mocker, tmp_path):
        """build should error when package.json is missing."""
        project_dir = tmp_path / "onetimesecret"
        project_dir.mkdir()

        # Create Containerfile but no package.json
        containerfile = project_dir / "Containerfile"
        containerfile.write_text("FROM ruby:3.2\n")

        with pytest.raises(SystemExit) as exc:
            build(project_dir=project_dir)
        assert "No package.json" in str(exc.value)

    def test_build_validates_project_directory_not_exists(self, mocker, tmp_path):
        """build should error when project directory doesn't exist."""
        project_dir = tmp_path / "nonexistent"

        with pytest.raises(SystemExit) as exc:
            build(project_dir=project_dir)
        assert "not found" in str(exc.value)


class TestBuildPodmanInvocation:
    """Test podman buildx command building."""

    def test_build_runs_podman_buildx(self, mocker, tmp_path):
        """build should invoke podman buildx build with correct arguments."""
        project_dir = tmp_path / "onetimesecret"
        project_dir.mkdir()

        package_json = project_dir / "package.json"
        package_json.write_text(json.dumps({"version": "0.25.0"}))

        containerfile = project_dir / "Containerfile"
        containerfile.write_text("FROM ruby:3.2\n")

        var_dir = tmp_path / "var"
        var_dir.mkdir()

        def mock_run_factory(cmd, *args, **kwargs):
            if "git" in cmd:
                return subprocess.CompletedProcess(cmd, 0, stdout="abc12345\n", stderr="")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        mock_run = mocker.patch("subprocess.run", side_effect=mock_run_factory)

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
        mocker.patch("rots.commands.image.app.db.record_deployment")

        build(project_dir=project_dir, quiet=True)

        mock_run.assert_called()
        call_args = mock_run.call_args[0][0]

        # Should use podman buildx build
        assert "podman" in call_args
        assert "buildx" in call_args
        assert "build" in call_args

    def test_build_with_push_requires_registry(self, mocker, tmp_path, caplog):
        """build --push without registry should error."""
        import logging

        project_dir = tmp_path / "onetimesecret"
        project_dir.mkdir()

        package_json = project_dir / "package.json"
        package_json.write_text(json.dumps({"version": "0.25.0"}))

        containerfile = project_dir / "Containerfile"
        containerfile.write_text("FROM ruby:3.2\n")

        var_dir = tmp_path / "var"
        var_dir.mkdir()

        def mock_run_factory(cmd, *args, **kwargs):
            if "git" in cmd:
                return subprocess.CompletedProcess(cmd, 0, stdout="abc12345\n", stderr="")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        mocker.patch("subprocess.run", side_effect=mock_run_factory)

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

        with pytest.raises(SystemExit) as exc:
            with caplog.at_level(logging.ERROR):
                build(project_dir=project_dir, push=True)
        assert exc.value.code == 1
        assert "--push requires --registry" in caplog.text

    def test_build_with_push_and_registry(self, mocker, tmp_path):
        """build --push with registry should tag and push the image."""
        project_dir = tmp_path / "onetimesecret"
        project_dir.mkdir()

        package_json = project_dir / "package.json"
        package_json.write_text(json.dumps({"version": "0.25.0"}))

        containerfile = project_dir / "Containerfile"
        containerfile.write_text("FROM ruby:3.2\n")

        var_dir = tmp_path / "var"
        var_dir.mkdir()

        def mock_run_factory(cmd, *args, **kwargs):
            if "git" in cmd:
                return subprocess.CompletedProcess(cmd, 0, stdout="abc12345\n", stderr="")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        mock_run = mocker.patch("subprocess.run", side_effect=mock_run_factory)

        mocker.patch(
            "rots.commands.image.app.Config",
            return_value=mocker.Mock(
                db_path=var_dir / "deployments.db",
                image="ghcr.io/onetimesecret/onetimesecret",
                registry="registry.example.com",
                registry_auth_file=tmp_path / "auth.json",
                get_executor=lambda host=None: None,
            ),
        )
        mocker.patch("rots.commands.image.app.db.record_deployment")

        build(project_dir=project_dir, push=True, quiet=True)

        # Verify podman tag and push were called
        calls = [str(call) for call in mock_run.call_args_list]
        assert any("tag" in c for c in calls)
        assert any("push" in c for c in calls)

    def test_build_custom_platform(self, mocker, tmp_path):
        """build --platform should override default platform."""
        project_dir = tmp_path / "onetimesecret"
        project_dir.mkdir()

        package_json = project_dir / "package.json"
        package_json.write_text(json.dumps({"version": "0.25.0"}))

        containerfile = project_dir / "Containerfile"
        containerfile.write_text("FROM ruby:3.2\n")

        var_dir = tmp_path / "var"
        var_dir.mkdir()

        def mock_run_factory(cmd, *args, **kwargs):
            if "git" in cmd:
                return subprocess.CompletedProcess(cmd, 0, stdout="abc12345\n", stderr="")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        mock_run = mocker.patch("subprocess.run", side_effect=mock_run_factory)

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
        mocker.patch("rots.commands.image.app.db.record_deployment")

        build(project_dir=project_dir, platform="linux/arm64", quiet=True)

        mock_run.assert_called()
        call_args = mock_run.call_args[0][0]

        # Should include custom platform
        assert "--platform" in call_args
        platform_idx = call_args.index("--platform")
        assert call_args[platform_idx + 1] == "linux/arm64"

    def test_build_custom_tag(self, mocker, tmp_path):
        """build --tag should override version-based tag."""
        project_dir = tmp_path / "onetimesecret"
        project_dir.mkdir()

        package_json = project_dir / "package.json"
        package_json.write_text(json.dumps({"version": "0.25.0"}))

        containerfile = project_dir / "Containerfile"
        containerfile.write_text("FROM ruby:3.2\n")

        var_dir = tmp_path / "var"
        var_dir.mkdir()

        def mock_run_factory(cmd, *args, **kwargs):
            if "git" in cmd:
                return subprocess.CompletedProcess(cmd, 0, stdout="abc12345\n", stderr="")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        mock_run = mocker.patch("subprocess.run", side_effect=mock_run_factory)

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
        mocker.patch("rots.commands.image.app.db.record_deployment")

        build(project_dir=project_dir, tag="custom-tag", quiet=True)

        mock_run.assert_called()
        call_args = mock_run.call_args[0][0]

        # Should use custom tag instead of version
        assert "custom-tag" in " ".join(call_args)
        # Should NOT use the package.json version
        assert "v0.25.0" not in " ".join(call_args)


class TestBuildDefaultBehavior:
    """Test default behavior and sensible defaults."""

    def test_build_uses_current_directory_by_default(self, mocker, tmp_path, monkeypatch):
        """build with no project_dir should use current working directory."""
        # Set up the current directory as a valid project
        project_dir = tmp_path / "onetimesecret"
        project_dir.mkdir()

        package_json = project_dir / "package.json"
        package_json.write_text(json.dumps({"version": "0.25.0"}))

        containerfile = project_dir / "Containerfile"
        containerfile.write_text("FROM ruby:3.2\n")

        var_dir = tmp_path / "var"
        var_dir.mkdir()

        # Change to the project directory
        monkeypatch.chdir(project_dir)

        def mock_run_factory(cmd, *args, **kwargs):
            if "git" in cmd:
                return subprocess.CompletedProcess(cmd, 0, stdout="abc12345\n", stderr="")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        mock_run = mocker.patch("subprocess.run", side_effect=mock_run_factory)

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
        mocker.patch("rots.commands.image.app.db.record_deployment")

        # Call without project_dir argument
        build(quiet=True)

        mock_run.assert_called()

    def test_build_default_platform_multi_arch(self, mocker, tmp_path):
        """build should default to linux/amd64,linux/arm64 platforms."""
        project_dir = tmp_path / "onetimesecret"
        project_dir.mkdir()

        package_json = project_dir / "package.json"
        package_json.write_text(json.dumps({"version": "0.25.0"}))

        containerfile = project_dir / "Containerfile"
        containerfile.write_text("FROM ruby:3.2\n")

        var_dir = tmp_path / "var"
        var_dir.mkdir()

        def mock_run_factory(cmd, *args, **kwargs):
            if "git" in cmd:
                return subprocess.CompletedProcess(cmd, 0, stdout="abc12345\n", stderr="")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        mock_run = mocker.patch("subprocess.run", side_effect=mock_run_factory)

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
        mocker.patch("rots.commands.image.app.db.record_deployment")

        build(project_dir=project_dir, quiet=True)

        mock_run.assert_called()
        call_args = mock_run.call_args[0][0]

        # Should include default multi-arch platform
        assert "--platform" in call_args
        platform_idx = call_args.index("--platform")
        assert "linux/amd64" in call_args[platform_idx + 1]
        assert "linux/arm64" in call_args[platform_idx + 1]

    def test_build_uses_local_image_name(self, mocker, tmp_path):
        """build should use onetimesecret:{tag} as local image name."""
        project_dir = tmp_path / "onetimesecret"
        project_dir.mkdir()

        package_json = project_dir / "package.json"
        package_json.write_text(json.dumps({"version": "0.25.0"}))

        containerfile = project_dir / "Containerfile"
        containerfile.write_text("FROM ruby:3.2\n")

        var_dir = tmp_path / "var"
        var_dir.mkdir()

        def mock_run_factory(cmd, *args, **kwargs):
            if "git" in cmd:
                return subprocess.CompletedProcess(cmd, 0, stdout="abc12345\n", stderr="")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        mock_run = mocker.patch("subprocess.run", side_effect=mock_run_factory)

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
        mocker.patch("rots.commands.image.app.db.record_deployment")

        build(project_dir=project_dir, quiet=True)

        mock_run.assert_called()
        call_args = " ".join(mock_run.call_args[0][0])

        # Should include local image name with version tag
        assert "onetimesecret:v0.25.0" in call_args


class TestBuildErrorHandling:
    """Test error handling during build process."""

    def test_build_handles_podman_failure(self, mocker, tmp_path, caplog):
        """build should handle podman build failures gracefully."""
        import logging

        project_dir = tmp_path / "onetimesecret"
        project_dir.mkdir()

        package_json = project_dir / "package.json"
        package_json.write_text(json.dumps({"version": "0.25.0"}))

        containerfile = project_dir / "Containerfile"
        containerfile.write_text("FROM ruby:3.2\n")

        var_dir = tmp_path / "var"
        var_dir.mkdir()

        mock_run = mocker.patch("subprocess.run")
        mock_run.side_effect = subprocess.CalledProcessError(1, "podman")

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
        mocker.patch("rots.commands.image.app.db.record_deployment")

        with pytest.raises(SystemExit) as exc:
            with caplog.at_level(logging.ERROR):
                build(project_dir=project_dir)
        assert exc.value.code == 1
        assert "Build failed" in caplog.text

    def test_build_handles_invalid_package_json(self, mocker, tmp_path):
        """build should handle malformed package.json gracefully."""
        project_dir = tmp_path / "onetimesecret"
        project_dir.mkdir()

        package_json = project_dir / "package.json"
        package_json.write_text("{ invalid json }")

        containerfile = project_dir / "Containerfile"
        containerfile.write_text("FROM ruby:3.2\n")

        with pytest.raises(SystemExit) as exc:
            build(project_dir=project_dir)
        assert "Invalid package.json" in str(exc.value)

    def test_build_handles_missing_version_in_package_json(self, mocker, tmp_path):
        """build should handle package.json without version field."""
        project_dir = tmp_path / "onetimesecret"
        project_dir.mkdir()

        package_json = project_dir / "package.json"
        package_json.write_text(json.dumps({"name": "onetimesecret"}))

        containerfile = project_dir / "Containerfile"
        containerfile.write_text("FROM ruby:3.2\n")

        with pytest.raises(SystemExit) as exc:
            build(project_dir=project_dir)
        assert "No 'version' field" in str(exc.value)


class TestBuildVariants:
    """Test building image variants (lite, s6)."""

    def test_build_lite_variant_with_custom_dockerfile(self, mocker, tmp_path):
        """build -f docker/variants/lite.dockerfile --suffix -lite should work."""
        project_dir = tmp_path / "onetimesecret"
        project_dir.mkdir()

        package_json = project_dir / "package.json"
        package_json.write_text(json.dumps({"version": "0.25.0"}))

        # Create the lite dockerfile in a subdirectory
        docker_dir = project_dir / "docker" / "variants"
        docker_dir.mkdir(parents=True)
        lite_dockerfile = docker_dir / "lite.dockerfile"
        lite_dockerfile.write_text("FROM ruby:3.2-slim\n")

        var_dir = tmp_path / "var"
        var_dir.mkdir()

        def mock_run_factory(cmd, *args, **kwargs):
            if "git" in cmd:
                return subprocess.CompletedProcess(cmd, 0, stdout="abc12345\n", stderr="")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        mock_run = mocker.patch("subprocess.run", side_effect=mock_run_factory)

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
        mocker.patch("rots.commands.image.app.db.record_deployment")

        build(
            project_dir=project_dir,
            dockerfile="docker/variants/lite.dockerfile",
            suffix="-lite",
            quiet=True,
        )

        mock_run.assert_called()
        call_args = mock_run.call_args[0][0]

        # Should include --file flag
        assert "--file" in call_args
        file_idx = call_args.index("--file")
        assert "lite.dockerfile" in call_args[file_idx + 1]

        # Should use -lite suffix in image name
        assert "--tag" in call_args
        tag_idx = call_args.index("--tag")
        assert "onetimesecret-lite:" in call_args[tag_idx + 1]

    def test_build_s6_variant_with_target(self, mocker, tmp_path):
        """build --target final-s6 --suffix -s6 should work."""
        project_dir = tmp_path / "onetimesecret"
        project_dir.mkdir()

        package_json = project_dir / "package.json"
        package_json.write_text(json.dumps({"version": "0.25.0"}))

        # Main Dockerfile with multi-stage
        dockerfile = project_dir / "Dockerfile"
        dockerfile.write_text("FROM ruby:3.2 AS base\nFROM base AS final-s6\n")

        var_dir = tmp_path / "var"
        var_dir.mkdir()

        def mock_run_factory(cmd, *args, **kwargs):
            if "git" in cmd:
                return subprocess.CompletedProcess(cmd, 0, stdout="abc12345\n", stderr="")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        mock_run = mocker.patch("subprocess.run", side_effect=mock_run_factory)

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
        mocker.patch("rots.commands.image.app.db.record_deployment")

        build(
            project_dir=project_dir,
            target="final-s6",
            suffix="-s6",
            quiet=True,
        )

        mock_run.assert_called()
        call_args = mock_run.call_args[0][0]

        # Should include --target flag
        assert "--target" in call_args
        target_idx = call_args.index("--target")
        assert call_args[target_idx + 1] == "final-s6"

        # Should use -s6 suffix in image name
        assert "--tag" in call_args
        tag_idx = call_args.index("--tag")
        assert "onetimesecret-s6:" in call_args[tag_idx + 1]

    def test_build_custom_dockerfile_not_found(self, mocker, tmp_path):
        """build with non-existent dockerfile should error."""
        project_dir = tmp_path / "onetimesecret"
        project_dir.mkdir()

        package_json = project_dir / "package.json"
        package_json.write_text(json.dumps({"version": "0.25.0"}))

        # No Dockerfile created

        with pytest.raises(SystemExit) as exc:
            build(
                project_dir=project_dir,
                dockerfile="nonexistent.dockerfile",
            )
        assert "Dockerfile not found" in str(exc.value)

    def test_build_variant_push_with_suffix(self, mocker, tmp_path):
        """build --push with suffix should push suffixed image name."""
        project_dir = tmp_path / "onetimesecret"
        project_dir.mkdir()

        package_json = project_dir / "package.json"
        package_json.write_text(json.dumps({"version": "0.25.0"}))

        dockerfile = project_dir / "Dockerfile"
        dockerfile.write_text("FROM ruby:3.2\n")

        var_dir = tmp_path / "var"
        var_dir.mkdir()

        def mock_run_factory(cmd, *args, **kwargs):
            if "git" in cmd:
                return subprocess.CompletedProcess(cmd, 0, stdout="abc12345\n", stderr="")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        mock_run = mocker.patch("subprocess.run", side_effect=mock_run_factory)

        mocker.patch(
            "rots.commands.image.app.Config",
            return_value=mocker.Mock(
                db_path=var_dir / "deployments.db",
                image="ghcr.io/onetimesecret/onetimesecret",
                registry="registry.example.com",
                registry_auth_file=tmp_path / "auth.json",
                get_executor=lambda host=None: None,
            ),
        )
        mocker.patch("rots.commands.image.app.db.record_deployment")

        build(
            project_dir=project_dir,
            suffix="-lite",
            push=True,
            quiet=True,
        )

        # Verify the push command uses suffixed image name
        calls = [str(call) for call in mock_run.call_args_list]
        push_calls = [c for c in calls if "push" in c]
        assert len(push_calls) >= 1
        assert any("onetimesecret-lite" in c for c in push_calls)


# --- .oci-build.json aware build tests ---


def _make_project(tmp_path, *, oci_config=None, version="0.25.0"):
    """Helper: create minimal project dir with optional .oci-build.json."""
    project_dir = tmp_path / "onetimesecret"
    project_dir.mkdir()

    (project_dir / "package.json").write_text(json.dumps({"version": version}))
    (project_dir / "Containerfile").write_text("FROM ruby:3.2\n")

    if oci_config is not None:
        (project_dir / ".oci-build.json").write_text(json.dumps(oci_config))

    return project_dir


def _mock_build_env(mocker, tmp_path):
    """Helper: set up common mocks for build tests. Returns mock_run."""
    var_dir = tmp_path / "var"
    var_dir.mkdir(exist_ok=True)

    def mock_run_factory(cmd, *args, **kwargs):
        if "git" in cmd:
            return subprocess.CompletedProcess(cmd, 0, stdout="abc12345\n", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    mock_run = mocker.patch("subprocess.run", side_effect=mock_run_factory)

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
    mocker.patch("rots.commands.image.app.db.record_deployment")

    return mock_run


class TestOciBuildConfig:
    """Test .oci-build.json loading."""

    def test_load_returns_config_when_present(self, tmp_path):
        config_data = {"image_name": "onetimesecret", "variants": []}
        project_dir = _make_project(tmp_path, oci_config=config_data)
        result = _load_oci_build_config(project_dir)
        assert result == config_data

    def test_load_returns_none_when_absent(self, tmp_path):
        project_dir = _make_project(tmp_path)
        result = _load_oci_build_config(project_dir)
        assert result is None

    def test_load_raises_on_invalid_json(self, tmp_path):
        project_dir = _make_project(tmp_path)
        (project_dir / ".oci-build.json").write_text("{ invalid json }")
        with pytest.raises(json.JSONDecodeError):
            _load_oci_build_config(project_dir)


class TestBuildWithBase:
    """Test that base image is built first and --build-context is injected."""

    def test_base_built_before_variant(self, mocker, tmp_path):
        oci_config = {
            "image_name": "onetimesecret",
            "platforms": ["linux/amd64"],
            "base": {"dockerfile": "docker/Dockerfile.base"},
            "variants": [
                {"suffix": "", "dockerfile": "Dockerfile", "target": "final"},
            ],
        }
        project_dir = _make_project(tmp_path, oci_config=oci_config)

        # Create the base dockerfile
        (project_dir / "docker").mkdir()
        (project_dir / "docker" / "Dockerfile.base").write_text("FROM ruby:3.2\n")

        mock_run = _mock_build_env(mocker, tmp_path)

        build(project_dir=project_dir, quiet=True)

        # Collect all buildx calls
        buildx_calls = [
            c for c in mock_run.call_args_list if "buildx" in str(c) and "build" in str(c)
        ]
        assert len(buildx_calls) == 2  # base + 1 variant

        # First buildx call should be the base (uses Dockerfile.base)
        base_call = str(buildx_calls[0])
        assert "Dockerfile.base" in base_call
        assert "ots-base:" in base_call

        # Second buildx call should have --build-context
        variant_call = str(buildx_calls[1])
        assert "--build-context" in variant_call
        assert "base=container-image://ots-base:" in variant_call

    def test_build_context_uses_container_image_protocol(self, mocker, tmp_path):
        oci_config = {
            "image_name": "onetimesecret",
            "base": {"dockerfile": "docker/Dockerfile.base"},
            "variants": [
                {"suffix": "", "dockerfile": "Dockerfile"},
            ],
        }
        project_dir = _make_project(tmp_path, oci_config=oci_config)
        (project_dir / "docker").mkdir()
        (project_dir / "docker" / "Dockerfile.base").write_text("FROM ruby:3.2\n")

        mock_run = _mock_build_env(mocker, tmp_path)

        build(project_dir=project_dir, quiet=True)

        # Find the variant buildx call
        buildx_calls = [
            c for c in mock_run.call_args_list if "buildx" in str(c) and "build" in str(c)
        ]
        variant_call_args = buildx_calls[-1][0][0]  # The cmd list

        # Find --build-context value
        idx = variant_call_args.index("--build-context")
        ctx_value = variant_call_args[idx + 1]
        assert ctx_value.startswith("base=container-image://")


class TestBuildAllVariants:
    """Test building all variants when no CLI variant flags are given."""

    def test_all_variants_built_in_order(self, mocker, tmp_path):
        oci_config = {
            "image_name": "onetimesecret",
            "variants": [
                {"suffix": "", "dockerfile": "Dockerfile", "target": "final"},
                {"suffix": "-lite", "dockerfile": "docker/lite.dockerfile"},
            ],
        }
        project_dir = _make_project(tmp_path, oci_config=oci_config)
        (project_dir / "docker").mkdir()
        (project_dir / "docker" / "lite.dockerfile").write_text("FROM ruby:3.2-slim\n")

        mock_run = _mock_build_env(mocker, tmp_path)

        build(project_dir=project_dir, quiet=True)

        buildx_calls = [
            c for c in mock_run.call_args_list if "buildx" in str(c) and "build" in str(c)
        ]
        assert len(buildx_calls) == 2

        # First variant: main (no suffix)
        assert "onetimesecret:v0.25.0" in str(buildx_calls[0])
        # Second variant: lite
        assert "onetimesecret-lite:v0.25.0" in str(buildx_calls[1])

    def test_records_deployment_for_each_variant(self, mocker, tmp_path):
        oci_config = {
            "image_name": "onetimesecret",
            "variants": [
                {"suffix": "", "dockerfile": "Dockerfile"},
                {"suffix": "-lite", "dockerfile": "docker/lite.dockerfile"},
            ],
        }
        project_dir = _make_project(tmp_path, oci_config=oci_config)
        (project_dir / "docker").mkdir()
        (project_dir / "docker" / "lite.dockerfile").write_text("FROM ruby:3.2-slim\n")

        _mock_build_env(mocker, tmp_path)
        mock_record = mocker.patch("rots.commands.image.app.db.record_deployment")

        build(project_dir=project_dir, quiet=True)

        # Should record once per variant
        assert mock_record.call_count == 2
        recorded_images = [call.kwargs["image"] for call in mock_record.call_args_list]
        assert "onetimesecret" in recorded_images
        assert "onetimesecret-lite" in recorded_images


class TestBuildSingleVariantFromConfig:
    """Test building a single variant via --suffix matching."""

    def test_suffix_matches_config_variant(self, mocker, tmp_path):
        oci_config = {
            "image_name": "onetimesecret",
            "variants": [
                {"suffix": "", "dockerfile": "Dockerfile"},
                {"suffix": "-lite", "dockerfile": "docker/lite.dockerfile"},
            ],
        }
        project_dir = _make_project(tmp_path, oci_config=oci_config)
        (project_dir / "docker").mkdir()
        (project_dir / "docker" / "lite.dockerfile").write_text("FROM ruby:3.2-slim\n")

        mock_run = _mock_build_env(mocker, tmp_path)

        build(project_dir=project_dir, suffix="-lite", quiet=True)

        buildx_calls = [
            c for c in mock_run.call_args_list if "buildx" in str(c) and "build" in str(c)
        ]
        # Only 1 variant should be built (no base since no base config)
        assert len(buildx_calls) == 1
        assert "onetimesecret-lite:v0.25.0" in str(buildx_calls[0])

    def test_empty_suffix_matches_main_variant(self, mocker, tmp_path):
        oci_config = {
            "image_name": "onetimesecret",
            "variants": [
                {"suffix": "", "dockerfile": "Dockerfile"},
                {"suffix": "-lite", "dockerfile": "docker/lite.dockerfile"},
            ],
        }
        project_dir = _make_project(tmp_path, oci_config=oci_config)

        mock_run = _mock_build_env(mocker, tmp_path)

        build(project_dir=project_dir, suffix="", quiet=True)

        buildx_calls = [
            c for c in mock_run.call_args_list if "buildx" in str(c) and "build" in str(c)
        ]
        assert len(buildx_calls) == 1
        assert "onetimesecret:v0.25.0" in str(buildx_calls[0])


class TestBuildCustomSuffixWithBase:
    """Test custom suffix still gets base context injection."""

    def test_unmatched_suffix_uses_cli_flags_with_base(self, mocker, tmp_path):
        oci_config = {
            "image_name": "onetimesecret",
            "base": {"dockerfile": "docker/Dockerfile.base"},
            "variants": [
                {"suffix": "", "dockerfile": "Dockerfile"},
            ],
        }
        project_dir = _make_project(tmp_path, oci_config=oci_config)
        (project_dir / "docker").mkdir()
        (project_dir / "docker" / "Dockerfile.base").write_text("FROM ruby:3.2\n")

        custom_df = project_dir / "docker" / "custom.dockerfile"
        custom_df.write_text("FROM base\n")

        mock_run = _mock_build_env(mocker, tmp_path)

        build(
            project_dir=project_dir,
            suffix="_custom",
            dockerfile="docker/custom.dockerfile",
            quiet=True,
        )

        buildx_calls = [
            c for c in mock_run.call_args_list if "buildx" in str(c) and "build" in str(c)
        ]
        # base + custom variant
        assert len(buildx_calls) == 2

        # Custom variant should have base context and custom suffix
        variant_call = str(buildx_calls[1])
        assert "onetimesecret_custom:v0.25.0" in variant_call
        assert "--build-context" in variant_call
        assert "base=container-image://ots-base:" in variant_call


class TestBuildBaseCleanup:
    """Test that base image is cleaned up after build."""

    def test_base_removed_after_successful_build(self, mocker, tmp_path):
        oci_config = {
            "image_name": "onetimesecret",
            "base": {"dockerfile": "docker/Dockerfile.base"},
            "variants": [
                {"suffix": "", "dockerfile": "Dockerfile"},
            ],
        }
        project_dir = _make_project(tmp_path, oci_config=oci_config)
        (project_dir / "docker").mkdir()
        (project_dir / "docker" / "Dockerfile.base").write_text("FROM ruby:3.2\n")

        mock_run = _mock_build_env(mocker, tmp_path)

        build(project_dir=project_dir, quiet=True)

        # Find the rmi call for the base image
        rmi_calls = [c for c in mock_run.call_args_list if "rmi" in str(c)]
        assert len(rmi_calls) == 1
        assert "ots-base:" in str(rmi_calls[0])

    def test_base_removed_even_on_variant_failure(self, mocker, tmp_path, capsys):
        oci_config = {
            "image_name": "onetimesecret",
            "base": {"dockerfile": "docker/Dockerfile.base"},
            "variants": [
                {"suffix": "", "dockerfile": "Dockerfile"},
            ],
        }
        project_dir = _make_project(tmp_path, oci_config=oci_config)
        (project_dir / "docker").mkdir()
        (project_dir / "docker" / "Dockerfile.base").write_text("FROM ruby:3.2\n")

        call_count = {"n": 0}

        def mock_run_factory(cmd, *args, **kwargs):
            if "git" in cmd:
                return subprocess.CompletedProcess(cmd, 0, stdout="abc12345\n", stderr="")
            if "buildx" in cmd and "build" in cmd:
                call_count["n"] += 1
                if call_count["n"] == 2:
                    # Variant build fails
                    raise subprocess.CalledProcessError(1, cmd)
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        mock_run = mocker.patch("subprocess.run", side_effect=mock_run_factory)

        var_dir = tmp_path / "var"
        var_dir.mkdir(exist_ok=True)
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
        mocker.patch("rots.commands.image.app.db.record_deployment")

        with pytest.raises(SystemExit):
            build(project_dir=project_dir, quiet=True)

        # Base cleanup should still have been called
        rmi_calls = [c for c in mock_run.call_args_list if "rmi" in str(c)]
        assert len(rmi_calls) == 1
        assert "ots-base:" in str(rmi_calls[0])


class TestBuildInterVariantContexts:
    """Test inter-variant build context dependencies."""

    def test_lite_gets_main_as_build_context(self, mocker, tmp_path):
        oci_config = {
            "image_name": "onetimesecret",
            "variants": [
                {"suffix": "", "dockerfile": "Dockerfile"},
                {
                    "suffix": "-lite",
                    "dockerfile": "docker/lite.dockerfile",
                    "build_context": {"main": "target:"},
                },
            ],
        }
        project_dir = _make_project(tmp_path, oci_config=oci_config)
        (project_dir / "docker").mkdir()
        (project_dir / "docker" / "lite.dockerfile").write_text("FROM ruby:3.2-slim\n")

        mock_run = _mock_build_env(mocker, tmp_path)

        build(project_dir=project_dir, quiet=True)

        buildx_calls = [
            c for c in mock_run.call_args_list if "buildx" in str(c) and "build" in str(c)
        ]
        assert len(buildx_calls) == 2

        # The lite variant should have --build-context main=container-image://onetimesecret:v0.25.0
        lite_call = str(buildx_calls[1])
        assert "--build-context" in lite_call
        assert "main=container-image://onetimesecret:v0.25.0" in lite_call


class TestBuildConfigPlatformResolution:
    """Test platform resolution from .oci-build.json."""

    def test_config_platform_used_when_cli_is_default(self, mocker, tmp_path):
        oci_config = {
            "image_name": "onetimesecret",
            "platforms": ["linux/arm64"],
            "variants": [
                {"suffix": "", "dockerfile": "Dockerfile"},
            ],
        }
        project_dir = _make_project(tmp_path, oci_config=oci_config)
        mock_run = _mock_build_env(mocker, tmp_path)

        # Don't pass --platform, so it uses the default
        build(project_dir=project_dir, quiet=True)

        buildx_calls = [
            c for c in mock_run.call_args_list if "buildx" in str(c) and "build" in str(c)
        ]
        # Should use config platform
        variant_call_args = buildx_calls[0][0][0]
        platform_idx = variant_call_args.index("--platform")
        assert variant_call_args[platform_idx + 1] == "linux/arm64"

    def test_cli_platform_overrides_config(self, mocker, tmp_path):
        oci_config = {
            "image_name": "onetimesecret",
            "platforms": ["linux/arm64"],
            "variants": [
                {"suffix": "", "dockerfile": "Dockerfile"},
            ],
        }
        project_dir = _make_project(tmp_path, oci_config=oci_config)
        mock_run = _mock_build_env(mocker, tmp_path)

        build(project_dir=project_dir, platform="linux/amd64", quiet=True)

        buildx_calls = [
            c for c in mock_run.call_args_list if "buildx" in str(c) and "build" in str(c)
        ]
        variant_call_args = buildx_calls[0][0][0]
        platform_idx = variant_call_args.index("--platform")
        assert variant_call_args[platform_idx + 1] == "linux/amd64"


class TestBuildLegacyPathUnchanged:
    """Ensure builds without .oci-build.json still work identically."""

    def test_no_config_uses_legacy_path(self, mocker, tmp_path):
        project_dir = _make_project(tmp_path)  # No oci_config
        mock_run = _mock_build_env(mocker, tmp_path)

        build(project_dir=project_dir, quiet=True)

        buildx_calls = [
            c for c in mock_run.call_args_list if "buildx" in str(c) and "build" in str(c)
        ]
        assert len(buildx_calls) == 1
        assert "onetimesecret:v0.25.0" in str(buildx_calls[0])
        # No --build-context should be present
        assert "--build-context" not in str(buildx_calls[0])


class TestBuildImageNameFallback:
    """Test that build commands derive image_name from cfg.image when not specified."""

    def _mock_build_env_with_image(self, mocker, tmp_path, image="ghcr.io/org/myimage"):
        """Like _mock_build_env but sets cfg.image to a custom value."""
        var_dir = tmp_path / "var"
        var_dir.mkdir(exist_ok=True)

        def mock_run_factory(cmd, *args, **kwargs):
            if "git" in cmd:
                return subprocess.CompletedProcess(cmd, 0, stdout="abc12345\n", stderr="")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        mock_run = mocker.patch("subprocess.run", side_effect=mock_run_factory)

        mocker.patch(
            "rots.commands.image.app.Config",
            return_value=mocker.Mock(
                db_path=var_dir / "deployments.db",
                registry=None,
                registry_auth_file=tmp_path / "auth.json",
                image=image,
                get_executor=lambda host=None: None,
            ),
        )
        mocker.patch("rots.commands.image.app.db.record_deployment")

        return mock_run

    def test_oci_config_without_image_name_uses_cfg_basename(self, mocker, tmp_path):
        """When .oci-build.json omits image_name, build should use cfg.image basename."""
        oci_config = {
            # No "image_name" key
            "platforms": ["linux/amd64"],
            "variants": [
                {"suffix": "", "dockerfile": "Containerfile", "target": "final"},
            ],
        }
        project_dir = _make_project(tmp_path, oci_config=oci_config)

        mock_run = self._mock_build_env_with_image(mocker, tmp_path, image="ghcr.io/org/myimage")

        build(project_dir=project_dir, quiet=True)

        buildx_calls = [
            c for c in mock_run.call_args_list if "buildx" in str(c) and "build" in str(c)
        ]
        assert len(buildx_calls) >= 1
        # Should use "myimage" (basename of cfg.image), not "onetimesecret"
        call_str = str(buildx_calls[0])
        assert "myimage:" in call_str
        assert "onetimesecret:" not in call_str

    def test_legacy_build_uses_cfg_image_basename(self, mocker, tmp_path):
        """Legacy build (no .oci-build.json) should use cfg.image basename."""
        project_dir = _make_project(tmp_path)  # No oci_config

        mock_run = self._mock_build_env_with_image(mocker, tmp_path, image="ghcr.io/org/customapp")

        build(project_dir=project_dir, quiet=True)

        buildx_calls = [
            c for c in mock_run.call_args_list if "buildx" in str(c) and "build" in str(c)
        ]
        assert len(buildx_calls) == 1
        call_str = str(buildx_calls[0])
        assert "customapp:v0.25.0" in call_str
        assert "onetimesecret:" not in call_str

    def test_oci_config_with_image_name_uses_config_value(self, mocker, tmp_path):
        """When .oci-build.json has image_name, build should use that, not cfg.image."""
        oci_config = {
            "image_name": "explicit-name",
            "platforms": ["linux/amd64"],
            "variants": [
                {"suffix": "", "dockerfile": "Containerfile", "target": "final"},
            ],
        }
        project_dir = _make_project(tmp_path, oci_config=oci_config)

        mock_run = self._mock_build_env_with_image(mocker, tmp_path, image="ghcr.io/org/myimage")

        build(project_dir=project_dir, quiet=True)

        buildx_calls = [
            c for c in mock_run.call_args_list if "buildx" in str(c) and "build" in str(c)
        ]
        assert len(buildx_calls) >= 1
        call_str = str(buildx_calls[0])
        # Should use "explicit-name" from .oci-build.json, not "myimage" from cfg
        assert "explicit-name:" in call_str
