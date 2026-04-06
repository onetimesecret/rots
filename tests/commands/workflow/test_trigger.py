# tests/commands/workflow/test_trigger.py

"""Tests for rots workflow trigger command.

Covers:
- Host resolution from CLI args, manifest, and discovery
- Dry-run output (text and JSON)
- JSON output format for execution
- Exit codes: EXIT_SUCCESS (0), EXIT_PARTIAL (2), EXIT_FAILURE (1)
- --continue-on-failure flag (FailureMode.CONTINUE_ALL)
- --delay and --timeout options
- Error handling for missing hosts/manifest
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from rots.commands.common import EXIT_FAILURE, EXIT_PARTIAL, EXIT_PRECOND, EXIT_SUCCESS

pytestmark = pytest.mark.quick


class TestTriggerHostResolution:
    """Tests for trigger command host resolution logic."""

    def test_uses_explicit_hosts(self, tmp_path, monkeypatch, make_mock_plan):
        """Uses hosts provided via --hosts flag."""
        from rots.commands.workflow.app import trigger

        monkeypatch.chdir(tmp_path)

        with patch("rots.commands.workflow.app.create_plan") as mock_plan:
            mock_plan.return_value = make_mock_plan(host_count=2, step_count=4)
            with patch("rots.deploy.orchestrator.execute") as mock_exec:
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

    def test_uses_manifest_file(self, tmp_path, monkeypatch, make_mock_plan):
        """Uses hosts from --manifest file."""
        from rots.commands.workflow.app import trigger

        manifest_file = tmp_path / "deploy.yaml"
        manifest_file.write_text("hosts:\n  - manifest-host1\n  - manifest-host2\nport: 8080\n")
        monkeypatch.chdir(tmp_path)

        with patch("rots.commands.workflow.app.create_plan") as mock_plan:
            mock_plan.return_value = make_mock_plan(host_count=2, step_count=4)
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

    def test_discovers_manifest(self, tmp_path, monkeypatch, make_mock_plan):
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
            mock_plan.return_value = make_mock_plan(host_count=1, step_count=2)
            with pytest.raises(SystemExit) as exc_info:
                trigger(tag="v1.0.0", dry_run=True)

        assert exc_info.value.code == 0

        mock_plan.assert_called_once()
        call_kwargs = mock_plan.call_args[1]
        assert call_kwargs["hosts"] == ["discovered-host"]
        assert call_kwargs["port"] == 9000

    def test_falls_back_to_hosts_file(self, tmp_path, monkeypatch, make_mock_plan):
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
            mock_plan.return_value = make_mock_plan(host_count=2, step_count=4)
            with pytest.raises(SystemExit) as exc_info:
                trigger(tag="v1.0.0", dry_run=True)

        assert exc_info.value.code == 0

        mock_plan.assert_called_once()
        call_kwargs = mock_plan.call_args[1]
        assert call_kwargs["hosts"] == ["fallback-host1", "fallback-host2"]

    def test_port_override_from_cli(self, tmp_path, monkeypatch, make_mock_plan):
        """--port flag overrides manifest port."""
        from rots.commands.workflow.app import trigger

        manifest_file = tmp_path / ".ots-deploy.yaml"
        manifest_file.write_text("hosts:\n  - host1\nport: 9000\n")
        monkeypatch.chdir(tmp_path)

        with patch("rots.commands.workflow.app.create_plan") as mock_plan:
            mock_plan.return_value = make_mock_plan(host_count=1, step_count=2)
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

        assert exc_info.value.code == EXIT_PRECOND

    def test_error_when_manifest_not_found(self, tmp_path, monkeypatch, capsys):
        """Errors when --manifest file doesn't exist."""
        from rots.commands.workflow.app import trigger

        monkeypatch.chdir(tmp_path)
        nonexistent = tmp_path / "does-not-exist.yaml"

        with pytest.raises(SystemExit) as exc_info:
            trigger(tag="v1.0.0", manifest=nonexistent)

        assert exc_info.value.code == EXIT_PRECOND

    def test_error_json_output(self, tmp_path, monkeypatch, capsys):
        """Errors are output as JSON when --json is set."""
        from rots.commands.workflow.app import trigger

        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        monkeypatch.chdir(tmp_path)

        with pytest.raises(SystemExit) as exc_info:
            trigger(tag="v1.0.0", json_output=True)

        assert exc_info.value.code == EXIT_PRECOND

        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert "error" in output
        assert output["exit_code"] == EXIT_PRECOND

    def test_error_message_does_not_mention_hosts_file_flag(self, tmp_path, monkeypatch, capsys):
        """Error message should mention trigger-specific flags, not --hosts-file."""
        from rots.commands.workflow.app import trigger

        # Create git boundary (no hosts file, no manifest)
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        monkeypatch.chdir(tmp_path)

        with pytest.raises(SystemExit):
            trigger(tag="v1.0.0", json_output=True)

        captured = capsys.readouterr()
        output = json.loads(captured.out)

        # Error message should NOT mention --hosts-file (which is not a valid trigger flag)
        assert "--hosts-file" not in output["error"]
        # Should mention valid trigger options
        assert "--hosts" in output["error"] or "--manifest" in output["error"]


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

            with patch("rots.deploy.orchestrator.execute") as mock_exec:
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


class TestTriggerPlanOptions:
    """Tests for trigger command plan configuration options."""

    def test_default_failure_mode_stop_on_first(self, tmp_path, monkeypatch, make_mock_plan):
        """Default failure mode is STOP_ON_FIRST."""
        from rots.commands.workflow.app import trigger
        from rots.deploy import FailureMode

        monkeypatch.chdir(tmp_path)

        with patch("rots.commands.workflow.app.create_plan") as mock_plan:
            mock_plan.return_value = make_mock_plan(host_count=1, step_count=2)
            with pytest.raises(SystemExit):
                trigger(
                    tag="v1.0.0",
                    hosts=("host1",),
                    dry_run=True,
                )

        call_kwargs = mock_plan.call_args[1]
        assert call_kwargs["failure_mode"] == FailureMode.STOP_ON_FIRST

    def test_continue_on_failure_flag(self, tmp_path, monkeypatch, make_mock_plan):
        """--continue-on-failure sets CONTINUE_ALL failure mode."""
        from rots.commands.workflow.app import trigger
        from rots.deploy import FailureMode

        monkeypatch.chdir(tmp_path)

        with patch("rots.commands.workflow.app.create_plan") as mock_plan:
            mock_plan.return_value = make_mock_plan(host_count=2, step_count=4)
            with pytest.raises(SystemExit):
                trigger(
                    tag="v1.0.0",
                    hosts=("host1", "host2"),
                    continue_on_failure=True,
                    dry_run=True,
                )

        call_kwargs = mock_plan.call_args[1]
        assert call_kwargs["failure_mode"] == FailureMode.CONTINUE_ALL

    def test_delay_option(self, tmp_path, monkeypatch, make_mock_plan):
        """--delay option is passed to create_plan."""
        from rots.commands.workflow.app import trigger

        monkeypatch.chdir(tmp_path)

        with patch("rots.commands.workflow.app.create_plan") as mock_plan:
            mock_plan.return_value = make_mock_plan(host_count=1, step_count=2)
            with pytest.raises(SystemExit):
                trigger(
                    tag="v1.0.0",
                    hosts=("host1",),
                    delay=15.0,
                    dry_run=True,
                )

        call_kwargs = mock_plan.call_args[1]
        assert call_kwargs["delay"] == 15.0

    def test_timeout_option(self, tmp_path, monkeypatch, make_mock_plan):
        """--timeout option is passed to create_plan for both pull and redeploy."""
        from rots.commands.workflow.app import trigger

        monkeypatch.chdir(tmp_path)

        with patch("rots.commands.workflow.app.create_plan") as mock_plan:
            mock_plan.return_value = make_mock_plan(host_count=1, step_count=2)
            with pytest.raises(SystemExit):
                trigger(
                    tag="v1.0.0",
                    hosts=("host1",),
                    timeout=180,
                    dry_run=True,
                )

        call_kwargs = mock_plan.call_args[1]
        assert call_kwargs["pull_timeout"] == 180.0
        assert call_kwargs["redeploy_timeout"] == 180.0


class TestTriggerExecution:
    """Tests for trigger command execution and exit codes."""

    def _make_result(self, host_id: str, command: str, success: bool, error: str | None = None):
        """Create a mock StepResult."""
        mock_step = MagicMock()
        mock_step.host_id = host_id
        mock_step.command = command
        mock_step.description = f"{command.split('.')[-1]} --tag v1.0.0"

        result = MagicMock()
        result.host_id = host_id
        result.step = mock_step
        result.success = success
        result.error = error
        result.duration_ms = 100.0
        return result

    def test_all_succeed_exit_success(self, tmp_path, monkeypatch, make_mock_plan):
        """EXIT_SUCCESS (0) when all steps succeed."""
        from rots.commands.workflow.app import trigger

        monkeypatch.chdir(tmp_path)

        with patch("rots.commands.workflow.app.create_plan") as mock_plan:
            mock_plan.return_value = make_mock_plan(host_count=1, step_count=2)
            with patch("rots.deploy.orchestrator.execute") as mock_exec:
                mock_exec.return_value = iter(
                    [
                        self._make_result("host1", "rots.image.pull", True),
                        self._make_result("host1", "rots.instance.redeploy", True),
                    ]
                )
                with pytest.raises(SystemExit) as exc_info:
                    trigger(
                        tag="v1.0.0",
                        hosts=("host1",),
                    )

        assert exc_info.value.code == EXIT_SUCCESS

    def test_some_fail_exit_partial(self, tmp_path, monkeypatch, make_mock_plan):
        """EXIT_PARTIAL (2) when some steps fail but at least one succeeds."""
        from rots.commands.workflow.app import trigger

        monkeypatch.chdir(tmp_path)

        with patch("rots.commands.workflow.app.create_plan") as mock_plan:
            mock_plan.return_value = make_mock_plan(host_count=2, step_count=4)
            with patch("rots.deploy.orchestrator.execute") as mock_exec:
                mock_exec.return_value = iter(
                    [
                        self._make_result("host1", "rots.image.pull", True),
                        self._make_result("host1", "rots.instance.redeploy", True),
                        self._make_result("host2", "rots.image.pull", True),
                        self._make_result("host2", "rots.instance.redeploy", False, "timeout"),
                    ]
                )
                with pytest.raises(SystemExit) as exc_info:
                    trigger(
                        tag="v1.0.0",
                        hosts=("host1", "host2"),
                    )

        assert exc_info.value.code == EXIT_PARTIAL

    def test_all_fail_exit_failure(self, tmp_path, monkeypatch, make_mock_plan):
        """EXIT_FAILURE (1) when all steps fail."""
        from rots.commands.workflow.app import trigger

        monkeypatch.chdir(tmp_path)

        with patch("rots.commands.workflow.app.create_plan") as mock_plan:
            mock_plan.return_value = make_mock_plan(host_count=1, step_count=2)
            with patch("rots.deploy.orchestrator.execute") as mock_exec:
                mock_exec.return_value = iter(
                    [
                        self._make_result("host1", "rots.image.pull", False, "connection refused"),
                    ]
                )
                with pytest.raises(SystemExit) as exc_info:
                    trigger(
                        tag="v1.0.0",
                        hosts=("host1",),
                    )

        assert exc_info.value.code == EXIT_FAILURE

    def test_unexpected_exception_exit_failure(self, tmp_path, monkeypatch, make_mock_plan):
        """EXIT_FAILURE (1) on unexpected exception during execution."""
        from rots.commands.workflow.app import trigger

        monkeypatch.chdir(tmp_path)

        with patch("rots.commands.workflow.app.create_plan") as mock_plan:
            mock_plan.return_value = make_mock_plan(host_count=1, step_count=2)
            with patch("rots.deploy.orchestrator.execute") as mock_exec:
                mock_exec.side_effect = RuntimeError("Connection lost")
                with pytest.raises(SystemExit) as exc_info:
                    trigger(
                        tag="v1.0.0",
                        hosts=("host1",),
                    )

        assert exc_info.value.code == EXIT_FAILURE


class TestTriggerJsonOutput:
    """Tests for trigger command JSON output format."""

    def _make_result(self, host_id: str, command: str, success: bool, error: str | None = None):
        """Create a mock StepResult."""
        mock_step = MagicMock()
        mock_step.host_id = host_id
        mock_step.command = command
        mock_step.description = f"{command.split('.')[-1]} --tag v1.0.0"

        result = MagicMock()
        result.host_id = host_id
        result.step = mock_step
        result.success = success
        result.error = error
        result.duration_ms = 150.0
        return result

    def test_success_json_output(self, tmp_path, monkeypatch, capsys, make_mock_plan):
        """JSON output contains correct fields on success."""
        from rots.commands.workflow.app import trigger

        monkeypatch.chdir(tmp_path)

        with patch("rots.commands.workflow.app.create_plan") as mock_plan:
            mock_plan.return_value = make_mock_plan(host_count=1, step_count=2)
            with patch("rots.deploy.orchestrator.execute") as mock_exec:
                mock_exec.return_value = iter(
                    [
                        self._make_result("host1", "rots.image.pull", True),
                        self._make_result("host1", "rots.instance.redeploy", True),
                    ]
                )
                with pytest.raises(SystemExit) as exc_info:
                    trigger(
                        tag="v1.0.0",
                        hosts=("host1",),
                        port=7043,
                        json_output=True,
                    )

        assert exc_info.value.code == EXIT_SUCCESS

        captured = capsys.readouterr()
        output = json.loads(captured.out)

        assert output["action"] == "trigger"
        assert output["image_tag"] == "v1.0.0"
        assert output["port"] == 7043
        assert output["total_hosts"] == 1
        assert output["total_steps"] == 2
        assert output["executed_steps"] == 2
        assert output["succeeded"] == 2
        assert output["failed"] == 0
        assert "results" in output
        assert len(output["results"]) == 2

    def test_failure_json_output(self, tmp_path, monkeypatch, capsys, make_mock_plan):
        """JSON output contains error info on failure."""
        from rots.commands.workflow.app import trigger

        monkeypatch.chdir(tmp_path)

        with patch("rots.commands.workflow.app.create_plan") as mock_plan:
            mock_plan.return_value = make_mock_plan(host_count=1, step_count=2)
            with patch("rots.deploy.orchestrator.execute") as mock_exec:
                mock_exec.return_value = iter(
                    [
                        self._make_result("host1", "rots.image.pull", False, "connection refused"),
                    ]
                )
                with pytest.raises(SystemExit) as exc_info:
                    trigger(
                        tag="v1.0.0",
                        hosts=("host1",),
                        json_output=True,
                    )

        assert exc_info.value.code == EXIT_FAILURE

        captured = capsys.readouterr()
        output = json.loads(captured.out)

        assert output["failed"] == 1
        assert output["results"][0]["success"] is False
        assert output["results"][0]["error"] == "connection refused"

    def test_exception_json_output(self, tmp_path, monkeypatch, capsys, make_mock_plan):
        """JSON output on unexpected exception includes error and partial results."""
        from rots.commands.workflow.app import trigger

        monkeypatch.chdir(tmp_path)

        def yield_then_raise():
            yield self._make_result("host1", "rots.image.pull", True)
            raise RuntimeError("Connection lost")

        with patch("rots.commands.workflow.app.create_plan") as mock_plan:
            mock_plan.return_value = make_mock_plan(host_count=2, step_count=4)
            with patch("rots.deploy.orchestrator.execute") as mock_exec:
                mock_exec.return_value = yield_then_raise()
                with pytest.raises(SystemExit) as exc_info:
                    trigger(
                        tag="v1.0.0",
                        hosts=("host1", "host2"),
                        json_output=True,
                    )

        assert exc_info.value.code == EXIT_FAILURE

        captured = capsys.readouterr()
        output = json.loads(captured.out)

        assert "error" in output
        assert "Connection lost" in output["error"]
        assert output["exit_code"] == EXIT_FAILURE
        assert "results" in output
        assert len(output["results"]) == 1  # Partial results captured


class TestTriggerResultDict:
    """Tests for result serialization in trigger command."""

    def test_result_to_dict_fields(self, tmp_path, monkeypatch, capsys, make_mock_plan):
        """Result dict includes all required fields."""
        from rots.commands.workflow.app import trigger

        monkeypatch.chdir(tmp_path)

        mock_step = MagicMock()
        mock_step.host_id = "host1"
        mock_step.command = "rots.image.pull"
        mock_step.description = "image.pull --tag v1.0.0"

        result = MagicMock()
        result.host_id = "host1"
        result.step = mock_step
        result.success = True
        result.error = None
        result.duration_ms = 1234.5

        with patch("rots.commands.workflow.app.create_plan") as mock_plan:
            mock_plan.return_value = make_mock_plan(host_count=1, step_count=1)
            with patch("rots.deploy.orchestrator.execute") as mock_exec:
                mock_exec.return_value = iter([result])
                with pytest.raises(SystemExit):
                    trigger(
                        tag="v1.0.0",
                        hosts=("host1",),
                        json_output=True,
                    )

        captured = capsys.readouterr()
        output = json.loads(captured.out)
        result_dict = output["results"][0]

        assert result_dict["host_id"] == "host1"
        assert result_dict["command"] == "rots.image.pull"
        assert result_dict["success"] is True
        assert result_dict["error"] is None
        assert result_dict["duration_ms"] == 1234.5
