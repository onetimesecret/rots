# tests/deploy/test_manifest.py

"""Tests for src/rots/deploy/manifest.py

Covers:
- DeployManifest.from_dict validation
- DeployManifest.from_file YAML parsing
- DeployManifest.discover walk-up discovery
- find_manifest_file walk-up discovery
"""

import pytest

from rots.deploy.manifest import (
    DeployManifest,
    ManifestError,
    find_manifest_file,
)


class TestDeployManifestFromDict:
    """Tests for DeployManifest.from_dict validation."""

    def test_minimal_valid_manifest(self):
        """Parses manifest with only required 'hosts' key."""
        data = {"hosts": ["host1", "host2"]}

        result = DeployManifest.from_dict(data)

        assert result.hosts == ["host1", "host2"]
        assert result.port == 7043  # default

    def test_full_manifest(self):
        """Parses manifest with all keys."""
        data = {"hosts": ["host1"], "port": 8080}

        result = DeployManifest.from_dict(data)

        assert result.hosts == ["host1"]
        assert result.port == 8080

    def test_rejects_non_dict(self):
        """Rejects non-dict data."""
        with pytest.raises(ManifestError, match="must be a YAML mapping"):
            DeployManifest.from_dict(["host1", "host2"])

    def test_rejects_unknown_keys(self):
        """Rejects manifest with unknown keys."""
        data = {"hosts": ["host1"], "unknown_key": "value"}

        with pytest.raises(ManifestError, match="Unknown keys.*unknown_key"):
            DeployManifest.from_dict(data)

    def test_requires_hosts_key(self):
        """Rejects manifest without 'hosts' key."""
        data = {"port": 8080}

        with pytest.raises(ManifestError, match="missing required 'hosts'"):
            DeployManifest.from_dict(data)

    def test_hosts_must_be_list(self):
        """Rejects manifest where 'hosts' is not a list."""
        data = {"hosts": "single-host"}

        with pytest.raises(ManifestError, match="'hosts' must be a list"):
            DeployManifest.from_dict(data)

    def test_port_must_be_int(self):
        """Rejects manifest where 'port' is not an integer."""
        data = {"hosts": ["host1"], "port": "8080"}

        with pytest.raises(ManifestError, match="'port' must be an integer"):
            DeployManifest.from_dict(data)

    def test_rejects_empty_hosts(self):
        """Rejects manifest with empty hosts list."""
        data = {"hosts": []}

        with pytest.raises(ManifestError, match="at least one host"):
            DeployManifest.from_dict(data)

    def test_rejects_invalid_host_entries(self):
        """Rejects manifest with invalid host entries."""
        data = {"hosts": ["host1", "", "host3"]}

        with pytest.raises(ManifestError, match="Invalid host entry"):
            DeployManifest.from_dict(data)

    def test_rejects_non_string_hosts(self):
        """Rejects manifest with non-string host entries."""
        data = {"hosts": ["host1", 123, "host3"]}

        with pytest.raises(ManifestError, match="Invalid host entry"):
            DeployManifest.from_dict(data)


class TestDeployManifestFromFile:
    """Tests for DeployManifest.from_file YAML parsing."""

    def test_loads_valid_yaml(self, tmp_path):
        """Loads valid YAML manifest file."""
        manifest_file = tmp_path / ".ots-deploy.yaml"
        manifest_file.write_text("hosts:\n  - host1\n  - host2\nport: 9000\n")

        result = DeployManifest.from_file(manifest_file)

        assert result.hosts == ["host1", "host2"]
        assert result.port == 9000
        assert result.source == manifest_file

    def test_raises_file_not_found(self, tmp_path):
        """Raises FileNotFoundError for missing file."""
        nonexistent = tmp_path / "does-not-exist.yaml"

        with pytest.raises(FileNotFoundError, match="not found"):
            DeployManifest.from_file(nonexistent)

    def test_raises_on_invalid_yaml(self, tmp_path):
        """Raises ManifestError on invalid YAML syntax."""
        manifest_file = tmp_path / ".ots-deploy.yaml"
        manifest_file.write_text("hosts:\n  - host1\n  bad indentation")

        with pytest.raises(ManifestError, match="Invalid YAML"):
            DeployManifest.from_file(manifest_file)

    def test_raises_on_schema_violation(self, tmp_path):
        """Raises ManifestError when YAML content violates schema."""
        manifest_file = tmp_path / ".ots-deploy.yaml"
        manifest_file.write_text("hosts: not-a-list\n")

        with pytest.raises(ManifestError, match="'hosts' must be a list"):
            DeployManifest.from_file(manifest_file)


class TestFindManifestFile:
    """Tests for find_manifest_file walk-up discovery."""

    def test_finds_file_in_start_directory(self, tmp_path):
        """Finds .ots-deploy.yaml in the start directory."""
        manifest_file = tmp_path / ".ots-deploy.yaml"
        manifest_file.write_text("hosts:\n  - host1\n")

        result = find_manifest_file(start=tmp_path)

        assert result == manifest_file

    def test_walks_up_to_find_file(self, tmp_path):
        """Walks up directory tree to find file."""
        # Create nested structure
        subdir = tmp_path / "level1" / "level2"
        subdir.mkdir(parents=True)

        # Put manifest at root
        manifest_file = tmp_path / ".ots-deploy.yaml"
        manifest_file.write_text("hosts:\n  - host1\n")

        result = find_manifest_file(start=subdir)

        assert result == manifest_file

    def test_stops_at_git_boundary(self, tmp_path):
        """Stops walking when .git directory is found."""
        # Create structure:
        # tmp_path/
        #   .ots-deploy.yaml  <- should NOT be found
        #   repo/
        #     .git/
        #     project/        <- start here

        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()
        git_dir = repo_dir / ".git"
        git_dir.mkdir()
        project_dir = repo_dir / "project"
        project_dir.mkdir()

        # Put manifest ABOVE the .git directory (should not be found)
        manifest_file = tmp_path / ".ots-deploy.yaml"
        manifest_file.write_text("hosts:\n  - host1\n")

        result = find_manifest_file(start=project_dir)

        assert result is None

    def test_returns_none_when_not_found(self, tmp_path):
        """Returns None when no manifest file exists."""
        # Create .git boundary so we don't walk up infinitely
        git_dir = tmp_path / ".git"
        git_dir.mkdir()

        result = find_manifest_file(start=tmp_path)

        assert result is None


class TestDeployManifestDiscover:
    """Tests for DeployManifest.discover class method."""

    def test_discovers_and_loads_manifest(self, tmp_path, monkeypatch):
        """Discovers and loads manifest file."""
        manifest_file = tmp_path / ".ots-deploy.yaml"
        manifest_file.write_text("hosts:\n  - discovered-host\nport: 9999\n")

        # Create .git boundary
        git_dir = tmp_path / ".git"
        git_dir.mkdir()

        # Change to subdir
        subdir = tmp_path / "project"
        subdir.mkdir()
        monkeypatch.chdir(subdir)

        result = DeployManifest.discover()

        assert result is not None
        assert result.hosts == ["discovered-host"]
        assert result.port == 9999
        assert result.source == manifest_file

    def test_returns_none_when_not_found(self, tmp_path, monkeypatch):
        """Returns None when no manifest can be discovered."""
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        monkeypatch.chdir(tmp_path)

        result = DeployManifest.discover()

        assert result is None

    def test_raises_on_invalid_discovered_manifest(self, tmp_path, monkeypatch):
        """Raises ManifestError when discovered manifest is invalid."""
        manifest_file = tmp_path / ".ots-deploy.yaml"
        manifest_file.write_text("hosts: not-a-list\n")  # Invalid

        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        monkeypatch.chdir(tmp_path)

        with pytest.raises(ManifestError, match="'hosts' must be a list"):
            DeployManifest.discover()
