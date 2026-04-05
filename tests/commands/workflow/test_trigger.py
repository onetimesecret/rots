# tests/commands/workflow/test_trigger.py

"""Tests for rots workflow trigger command.

Covers:
- Host resolution from CLI args, manifest, and discovery
- Dry-run output
- JSON output format
- Error handling for missing hosts/manifest
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest


class TestTriggerHostResolution:
    """Tests for trigger command host resolution logic."""

    def test_uses_explicit_hosts(self, tmp_path, monkeypatch):
        """Uses hosts provided via --hosts flag."""
        from rots.commands.workflow.app import trigger

        monkeypatch.chdir(tmp_path)

        with patch("rots.commands.workflow.app.create_plan") as mock_plan:
            mock_plan.return_value = MagicMock(host_count=2, step_count=4)
            with patch("rots.commands.workflow.app.execute") as mock_exec:
                mock_exec.return_value = iter([])
                with pytest.raises(SystemExit) as exc_info:
                    trigger(
                        tag="v1.0.0",
                        hosts=("host1", "host2"),
                        dry_run=True,
                    )

        # Dry run exits with 0
        assert exc_info.value.code == 0

        # Plan was created with explicit hosts
        mock_plan.assert_called_once()
        call_kwargs = mock_plan.call_args[1]
        assert call_kwargs["hosts"] == ["host1", "host2"]
        assert call_kwargs["image_tag"] == "v1.0.0"

    def test_uses_manifest_file(self, tmp_path, monkeypatch):
        """Uses hosts from --manifest file."""
        from rots.commands.workflow.app import trigger

        manifest_file = tmp_path / "deploy.yaml"
        manifest_file.write_text("hosts:\n  - manifest-host1\n  - manifest-host2\nport: 8080\n")
        monkeypatch.chdir(tmp_path)

        with patch("rots.commands.workflow.app.create_plan") as mock_plan:
            mock_plan.return_value = MagicMock(host_count=2, step_count=4)
            with pytest.raises(SystemExit) as exc_info:
                trigger(
                    tag="v1.0.0",
                    manifest=manifest_file,
                    dry_run=True,
                )

        assert exc_info.value.code == 0

        mock_plan.assert_called_once()
        call_kwargs = mock_plan.call_args[1]
        assert call_kwargs["hosts"] == ["manifest-host1", "manifest-host2"]
        assert call_kwargs["port"] == 8080

    def test_discovers_manifest(self, tmp_path, monkeypatch):
        """Discovers .ots-deploy.yaml via walk-up."""
        from rots.commands.workflow.app import trigger

        # Create manifest at root
        manifest_file = tmp_path / ".ots-deploy.yaml"
        manifest_file.write_text("hosts:\n  - discovered-host\nport: 9000\n")

        # Create git boundary
        git_dir = tmp_path / ".git"
        git_dir.mkdir()

        # Change to subdirectory
        subdir = tmp_path / "project"
        subdir.mkdir()
        monkeypatch.chdir(subdir)

        with patch("rots.commands.workflow.app.create_plan") as mock_plan:
            mock_plan.return_value = MagicMock(host_count=1, step_count=2)
            with pytest.raises(SystemExit) as exc_info:
                trigger(tag="v1.0.0", dry_run=True)

        assert exc_info.value.code == 0

        mock_plan.assert_called_once()
        call_kwargs = mock_plan.call_args[1]
        assert call_kwargs["hosts"] == ["discovered-host"]
        assert call_kwargs["port"] == 9000

    def test_falls_back_to_hosts_file(self, tmp_path, monkeypatch):
        """Falls back to .otsinfra-hosts.txt when no manifest."""
        from rots.commands.workflow.app import trigger

        # Create hosts file (not manifest)
        hosts_file = tmp_path / ".otsinfra-hosts.txt"
        hosts_file.write_text("fallback-host1\nfallback-host2\n")

        # Create git boundary
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        monkeypatch.chdir(tmp_path)

        with patch("rots.commands.workflow.app.create_plan") as mock_plan:
            mock_plan.return_value = MagicMock(host_count=2, step_count=4)
            with pytest.raises(SystemExit) as exc_info:
                trigger(tag="v1.0.0", dry_run=True)

        assert exc_info.value.code == 0

        mock_plan.assert_called_once()
        call_kwargs = mock_plan.call_args[1]
        assert call_kwargs["hosts"] == ["fallback-host1", "fallback-host2"]

    def test_port_override_from_cli(self, tmp_path, monkeypatch):
        """--port flag overrides manifest port."""
        from rots.commands.workflow.app import trigger

        manifest_file = tmp_path / ".ots-deploy.yaml"
        manifest_file.write_text("hosts:\n  - host1\nport: 9000\n")
        monkeypatch.chdir(tmp_path)

        with patch("rots.commands.workflow.app.create_plan") as mock_plan:
            mock_plan.return_value = MagicMock(host_count=1, step_count=2)
            with pytest.raises(SystemExit):
                trigger(
                    tag="v1.0.0",
                    manifest=manifest_file,
                    port=7777,  # Override
                    dry_run=True,
                )

        call_kwargs = mock_plan.call_args[1]
        assert call_kwargs["port"] == 7777


class TestTriggerErrors:
    """Tests for trigger command error handling."""

    def test_error_when_no_hosts(self, tmp_path, monkeypatch, capsys):
        """Errors when no hosts can be resolved."""
        from rots.commands.workflow.app import trigger

        # Create git boundary (no hosts file, no manifest)
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        monkeypatch.chdir(tmp_path)

        with pytest.raises(SystemExit) as exc_info:
            trigger(tag="v1.0.0")

        # Precondition failure exit code (EXIT_PRECOND = 3)
        assert exc_info.value.code == 3

    def test_error_when_manifest_not_found(self, tmp_path, monkeypatch, capsys):
        """Errors when --manifest file doesn't exist."""
        from rots.commands.workflow.app import trigger

        monkeypatch.chdir(tmp_path)
        nonexistent = tmp_path / "does-not-exist.yaml"

        with pytest.raises(SystemExit) as exc_info:
            trigger(tag="v1.0.0", manifest=nonexistent)

        # Precondition failure exit code (EXIT_PRECOND = 3)
        assert exc_info.value.code == 3

    def test_error_json_output(self, tmp_path, monkeypatch, capsys):
        """Errors are output as JSON when --json is set."""
        from rots.commands.workflow.app import trigger

        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        monkeypatch.chdir(tmp_path)

        with pytest.raises(SystemExit) as exc_info:
            trigger(tag="v1.0.0", json_output=True)

        # Precondition failure exit code (EXIT_PRECOND = 3)
        assert exc_info.value.code == 3

        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert "error" in output
        assert output["exit_code"] == 3


class TestTriggerDryRun:
    """Tests for trigger command dry-run mode."""

    def test_dry_run_shows_plan(self, tmp_path, monkeypatch, capsys):
        """Dry run shows plan without executing."""
        from rots.commands.workflow.app import trigger

        monkeypatch.chdir(tmp_path)

        with patch("rots.commands.workflow.app.create_plan") as mock_plan:
            # Create a mock plan with steps
            mock_step = MagicMock()
            mock_step.host_id = "host1"
            mock_step.command = "rots.image.pull"
            mock_step.payload = {"args": ["--tag", "v1.0.0"]}
            mock_step.timeout = 120.0

            plan = MagicMock()
            plan.image_tag = "v1.0.0"
            plan.host_count = 1
            plan.step_count = 2
            plan.failure_mode.value = "stop"
            plan.delay = 5.0
            plan.steps = [mock_step]
            mock_plan.return_value = plan

            with patch("rots.commands.workflow.app.execute") as mock_exec:
                with pytest.raises(SystemExit) as exc_info:
                    trigger(
                        tag="v1.0.0",
                        hosts=("host1",),
                        dry_run=True,
                        json_output=True,
                    )

        assert exc_info.value.code == 0

        # execute() should NOT be called in dry run
        mock_exec.assert_not_called()

        # Check JSON output
        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert output["action"] == "plan"
        assert output["image_tag"] == "v1.0.0"
