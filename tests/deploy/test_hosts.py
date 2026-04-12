# packages/rots/tests/deploy/test_hosts.py

"""Tests for src/rots/deploy/hosts.py

Covers:
- load_hosts_file parsing
- find_hosts_file walk-up discovery
- resolve_hosts resolution order and deduplication
"""

import pytest

from rots.deploy.hosts import find_hosts_file, load_hosts_file, resolve_hosts


class TestLoadHostsFile:
    """Tests for load_hosts_file parsing."""

    def test_simple_hosts(self, tmp_path):
        """Parse simple hosts file with one host per line."""
        hosts_file = tmp_path / ".otsinfra-hosts.txt"
        hosts_file.write_text("host1\nhost2\nhost3\n")

        result = load_hosts_file(hosts_file)

        assert result == ["host1", "host2", "host3"]

    def test_ignores_comments(self, tmp_path):
        """Lines starting with # are ignored."""
        hosts_file = tmp_path / ".otsinfra-hosts.txt"
        hosts_file.write_text("# Production hosts\nhost1\n# host2 is down\nhost3\n")

        result = load_hosts_file(hosts_file)

        assert result == ["host1", "host3"]

    def test_ignores_blank_lines(self, tmp_path):
        """Blank lines are ignored."""
        hosts_file = tmp_path / ".otsinfra-hosts.txt"
        hosts_file.write_text("host1\n\nhost2\n   \nhost3\n")

        result = load_hosts_file(hosts_file)

        assert result == ["host1", "host2", "host3"]

    def test_strips_whitespace(self, tmp_path):
        """Whitespace around host names is stripped."""
        hosts_file = tmp_path / ".otsinfra-hosts.txt"
        hosts_file.write_text("  host1  \n\thost2\t\n")

        result = load_hosts_file(hosts_file)

        assert result == ["host1", "host2"]

    def test_empty_file(self, tmp_path):
        """Empty file returns empty list."""
        hosts_file = tmp_path / ".otsinfra-hosts.txt"
        hosts_file.write_text("")

        result = load_hosts_file(hosts_file)

        assert result == []


class TestFindHostsFile:
    """Tests for find_hosts_file walk-up discovery."""

    def test_finds_file_in_start_directory(self, tmp_path):
        """Finds .otsinfra-hosts.txt in the start directory."""
        hosts_file = tmp_path / ".otsinfra-hosts.txt"
        hosts_file.write_text("host1\n")

        result = find_hosts_file(start=tmp_path)

        assert result == hosts_file

    def test_walks_up_to_find_file(self, tmp_path):
        """Walks up directory tree to find file."""
        # Create nested structure
        subdir = tmp_path / "level1" / "level2"
        subdir.mkdir(parents=True)

        # Put hosts file at root
        hosts_file = tmp_path / ".otsinfra-hosts.txt"
        hosts_file.write_text("host1\n")

        result = find_hosts_file(start=subdir)

        assert result == hosts_file

    def test_stops_at_git_boundary(self, tmp_path):
        """Stops walking when .git directory is found."""
        # Create structure:
        # tmp_path/
        #   .otsinfra-hosts.txt  <- should NOT be found
        #   repo/
        #     .git/
        #     project/           <- start here

        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()
        git_dir = repo_dir / ".git"
        git_dir.mkdir()
        project_dir = repo_dir / "project"
        project_dir.mkdir()

        # Put hosts file ABOVE the .git directory (should not be found)
        hosts_file = tmp_path / ".otsinfra-hosts.txt"
        hosts_file.write_text("host1\n")

        # Start from project dir, walk up, hit .git boundary at repo/
        result = find_hosts_file(start=project_dir)

        # Should not find the file because .git boundary stops the walk
        assert result is None

    def test_returns_none_when_not_found(self, tmp_path):
        """Returns None when no hosts file exists."""
        # Create a .git boundary so we don't walk up infinitely
        git_dir = tmp_path / ".git"
        git_dir.mkdir()

        result = find_hosts_file(start=tmp_path)

        assert result is None


class TestResolveHosts:
    """Tests for resolve_hosts resolution order."""

    def test_explicit_hosts_only(self, tmp_path, monkeypatch):
        """Explicit hosts from CLI are used when provided."""
        monkeypatch.chdir(tmp_path)

        result = resolve_hosts(("host1", "host2"))

        assert result == ["host1", "host2"]

    def test_hosts_from_file(self, tmp_path, monkeypatch):
        """--hosts-file is used when provided."""
        hosts_file = tmp_path / "my-hosts.txt"
        hosts_file.write_text("host1\nhost2\n")
        monkeypatch.chdir(tmp_path)

        result = resolve_hosts((), hosts_file=hosts_file)

        assert result == ["host1", "host2"]

    def test_walk_up_discovery(self, tmp_path, monkeypatch):
        """Discovers .otsinfra-hosts.txt when no explicit sources."""
        # Create hosts file
        hosts_file = tmp_path / ".otsinfra-hosts.txt"
        hosts_file.write_text("discovered-host1\ndiscovered-host2\n")

        # Create .git boundary
        git_dir = tmp_path / ".git"
        git_dir.mkdir()

        # Change to subdir
        subdir = tmp_path / "project"
        subdir.mkdir()
        monkeypatch.chdir(subdir)

        result = resolve_hosts(())

        assert result == ["discovered-host1", "discovered-host2"]

    def test_deduplicates_hosts(self, tmp_path, monkeypatch):
        """Duplicate hosts are removed, preserving order."""
        hosts_file = tmp_path / "hosts.txt"
        hosts_file.write_text("host2\nhost3\n")
        monkeypatch.chdir(tmp_path)

        # Explicit hosts include duplicates with file
        result = resolve_hosts(("host1", "host2"), hosts_file=hosts_file)

        assert result == ["host1", "host2", "host3"]

    def test_raises_when_no_hosts(self, tmp_path, monkeypatch):
        """Raises ValueError when no hosts can be resolved."""
        # Create .git boundary (no hosts file)
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        monkeypatch.chdir(tmp_path)

        with pytest.raises(ValueError, match="No hosts specified"):
            resolve_hosts(())

    def test_raises_when_hosts_file_not_found(self, tmp_path, monkeypatch):
        """Raises ValueError when --hosts-file doesn't exist."""
        monkeypatch.chdir(tmp_path)
        nonexistent = tmp_path / "does-not-exist.txt"

        with pytest.raises(ValueError, match="Hosts file not found"):
            resolve_hosts((), hosts_file=nonexistent)
