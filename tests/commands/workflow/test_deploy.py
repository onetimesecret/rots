# tests/commands/workflow/test_deploy.py

"""Tests for rots workflow deploy command.

Covers:
- Host resolution from positional args and --hosts-file
- Dry-run output (text and JSON)
- JSON output format for execution
- Exit codes: EXIT_SUCCESS (0), EXIT_PARTIAL (2), EXIT_FAILURE (1)
- --continue-on-failure flag (FailureMode.CONTINUE_ALL)
- --delay and --timeout options
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from rots.commands.common import EXIT_FAILURE, EXIT_PARTIAL, EXIT_PRECOND, EXIT_SUCCESS

pytestmark = pytest.mark.quick


class TestDeployHostResolution:
    """Tests for deploy command host resolution logic."""

    def test_uses_positional_hosts(self, tmp_path, monkeypatch, make_mock_plan):
        """Uses hosts provided as positional arguments."""
        from rots.commands.workflow.app import deploy

        monkeypatch.chdir(tmp_path)

        with patch("rots.commands.workflow.app.create_plan") as mock_plan:
            mock_plan.return_value = make_mock_plan(host_count=2, step_count=4)
            with pytest.raises(SystemExit) as exc_info:
                deploy(
                    hosts=("host1", "host2"),
                    tag="v1.0.0",
                    dry_run=True,
                )

        assert exc_info.value.code == EXIT_SUCCESS

        mock_plan.assert_called_once()
        call_kwargs = mock_plan.call_args[1]
        assert call_kwargs["hosts"] == ["host1", "host2"]
        assert call_kwargs["image_tag"] == "v1.0.0"

    def test_uses_hosts_file(self, tmp_path, monkeypatch, make_mock_plan):
        """Uses hosts from --hosts-file."""
        from rots.commands.workflow.app import deploy

        hosts_file = tmp_path / "hosts.txt"
        hosts_file.write_text("file-host1\nfile-host2\n")
        monkeypatch.chdir(tmp_path)

        with patch("rots.commands.workflow.app.create_plan") as mock_plan:
            mock_plan.return_value = make_mock_plan(host_count=2, step_count=4)
            with pytest.raises(SystemExit) as exc_info:
                deploy(
                    tag="v1.0.0",
                    hosts_file=hosts_file,
                    dry_run=True,
                )

        assert exc_info.value.code == EXIT_SUCCESS

        mock_plan.assert_called_once()
        call_kwargs = mock_plan.call_args[1]
        assert call_kwargs["hosts"] == ["file-host1", "file-host2"]

    def test_uses_discovered_hosts_file(self, tmp_path, monkeypatch, make_mock_plan):
        """Uses hosts from discovered .otsinfra-hosts.txt."""
        from rots.commands.workflow.app import deploy

        hosts_file = tmp_path / ".otsinfra-hosts.txt"
        hosts_file.write_text("discovered-host1\ndiscovered-host2\n")

        # Create git boundary
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        monkeypatch.chdir(tmp_path)

        with patch("rots.commands.workflow.app.create_plan") as mock_plan:
            mock_plan.return_value = make_mock_plan(host_count=2, step_count=4)
            with pytest.raises(SystemExit) as exc_info:
                deploy(
                    tag="v1.0.0",
                    dry_run=True,
                )

        assert exc_info.value.code == EXIT_SUCCESS

        mock_plan.assert_called_once()
        call_kwargs = mock_plan.call_args[1]
        assert call_kwargs["hosts"] == ["discovered-host1", "discovered-host2"]


class TestDeployPlanOptions:
    """Tests for deploy command plan configuration options."""

    def test_default_port(self, tmp_path, monkeypatch, make_mock_plan):
        """Default port is 7043."""
        from rots.commands.workflow.app import deploy

        monkeypatch.chdir(tmp_path)

        with patch("rots.commands.workflow.app.create_plan") as mock_plan:
            mock_plan.return_value = make_mock_plan(host_count=1, step_count=2)
            with pytest.raises(SystemExit):
                deploy(
                    hosts=("host1",),
                    tag="v1.0.0",
                    dry_run=True,
                )

        call_kwargs = mock_plan.call_args[1]
        assert call_kwargs["port"] == 7043

    def test_custom_port(self, tmp_path, monkeypatch, make_mock_plan):
        """Custom port is passed to create_plan."""
        from rots.commands.workflow.app import deploy

        monkeypatch.chdir(tmp_path)

        with patch("rots.commands.workflow.app.create_plan") as mock_plan:
            mock_plan.return_value = make_mock_plan(host_count=1, step_count=2)
            with pytest.raises(SystemExit):
                deploy(
                    hosts=("host1",),
                    tag="v1.0.0",
                    port=8080,
                    dry_run=True,
                )

        call_kwargs = mock_plan.call_args[1]
        assert call_kwargs["port"] == 8080

    def test_default_failure_mode_stop_on_first(self, tmp_path, monkeypatch, make_mock_plan):
        """Default failure mode is STOP_ON_FIRST."""
        from rots.commands.workflow.app import deploy
        from rots.deploy import FailureMode

        monkeypatch.chdir(tmp_path)

        with patch("rots.commands.workflow.app.create_plan") as mock_plan:
            mock_plan.return_value = make_mock_plan(host_count=1, step_count=2)
            with pytest.raises(SystemExit):
                deploy(
                    hosts=("host1",),
                    tag="v1.0.0",
                    dry_run=True,
                )

        call_kwargs = mock_plan.call_args[1]
        assert call_kwargs["failure_mode"] == FailureMode.STOP_ON_FIRST

    def test_continue_on_failure_flag(self, tmp_path, monkeypatch, make_mock_plan):
        """--continue-on-failure sets CONTINUE_ALL failure mode."""
        from rots.commands.workflow.app import deploy
        from rots.deploy import FailureMode

        monkeypatch.chdir(tmp_path)

        with patch("rots.commands.workflow.app.create_plan") as mock_plan:
            mock_plan.return_value = make_mock_plan(host_count=2, step_count=4)
            with pytest.raises(SystemExit):
                deploy(
                    hosts=("host1", "host2"),
                    tag="v1.0.0",
                    continue_on_failure=True,
                    dry_run=True,
                )

        call_kwargs = mock_plan.call_args[1]
        assert call_kwargs["failure_mode"] == FailureMode.CONTINUE_ALL

    def test_delay_option(self, tmp_path, monkeypatch, make_mock_plan):
        """--delay option is passed to create_plan."""
        from rots.commands.workflow.app import deploy

        monkeypatch.chdir(tmp_path)

        with patch("rots.commands.workflow.app.create_plan") as mock_plan:
            mock_plan.return_value = make_mock_plan(host_count=1, step_count=2)
            with pytest.raises(SystemExit):
                deploy(
                    hosts=("host1",),
                    tag="v1.0.0",
                    delay=10.0,
                    dry_run=True,
                )

        call_kwargs = mock_plan.call_args[1]
        assert call_kwargs["delay"] == 10.0

    def test_timeout_option(self, tmp_path, monkeypatch, make_mock_plan):
        """--timeout option is passed to create_plan for both pull and redeploy."""
        from rots.commands.workflow.app import deploy

        monkeypatch.chdir(tmp_path)

        with patch("rots.commands.workflow.app.create_plan") as mock_plan:
            mock_plan.return_value = make_mock_plan(host_count=1, step_count=2)
            with pytest.raises(SystemExit):
                deploy(
                    hosts=("host1",),
                    tag="v1.0.0",
                    timeout=300,
                    dry_run=True,
                )

        call_kwargs = mock_plan.call_args[1]
        assert call_kwargs["pull_timeout"] == 300.0
        assert call_kwargs["redeploy_timeout"] == 300.0


class TestDeployDryRun:
    """Tests for deploy command dry-run mode."""

    def test_dry_run_does_not_execute(self, tmp_path, monkeypatch, make_mock_plan):
        """Dry run shows plan without executing."""
        from rots.commands.workflow.app import deploy

        monkeypatch.chdir(tmp_path)

        with patch("rots.commands.workflow.app.create_plan") as mock_plan:
            mock_plan.return_value = make_mock_plan(
                image_tag="v1.0.0",
                host_count=1,
                step_count=2,
            )
            with patch("rots.deploy.orchestrator.execute") as mock_exec:
                with pytest.raises(SystemExit) as exc_info:
                    deploy(
                        hosts=("host1",),
                        tag="v1.0.0",
                        dry_run=True,
                    )

        assert exc_info.value.code == EXIT_SUCCESS
        mock_exec.assert_not_called()

    def test_dry_run_json_output(self, tmp_path, monkeypatch, capsys):
        """Dry run JSON output includes plan details."""
        from rots.commands.workflow.app import deploy

        monkeypatch.chdir(tmp_path)

        mock_step = MagicMock()
        mock_step.host_id = "host1"
        mock_step.command = "rots.image.pull"
        mock_step.payload = {"args": ["--tag", "v1.0.0"]}
        mock_step.timeout = 120.0

        with patch("rots.commands.workflow.app.create_plan") as mock_plan:
            mock_plan.return_value = MagicMock(
                image_tag="v1.0.0",
                host_count=1,
                step_count=2,
                failure_mode=MagicMock(value="stop"),
                delay=5.0,
                steps=[mock_step],
            )
            with pytest.raises(SystemExit) as exc_info:
                deploy(
                    hosts=("host1",),
                    tag="v1.0.0",
                    dry_run=True,
                    json_output=True,
                )

        assert exc_info.value.code == EXIT_SUCCESS

        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert output["action"] == "plan"
        assert output["image_tag"] == "v1.0.0"
        assert output["host_count"] == 1
        assert output["step_count"] == 2
        assert output["failure_mode"] == "stop"
        assert "steps" in output
        assert len(output["steps"]) == 1


class TestDeployErrors:
    """Tests for deploy command error handling."""

    def test_error_when_no_hosts(self, tmp_path, monkeypatch):
        """Errors when no hosts can be resolved."""
        from rots.commands.workflow.app import deploy

        # Create git boundary (no hosts file)
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        monkeypatch.chdir(tmp_path)

        with pytest.raises(SystemExit) as exc_info:
            deploy(tag="v1.0.0")

        assert exc_info.value.code == EXIT_PRECOND

    def test_error_when_hosts_file_not_found(self, tmp_path, monkeypatch):
        """Errors when --hosts-file doesn't exist."""
        from rots.commands.workflow.app import deploy

        monkeypatch.chdir(tmp_path)
        nonexistent = tmp_path / "does-not-exist.txt"

        with pytest.raises(SystemExit) as exc_info:
            deploy(tag="v1.0.0", hosts_file=nonexistent)

        assert exc_info.value.code == EXIT_PRECOND

    def test_error_json_output(self, tmp_path, monkeypatch, capsys):
        """Errors are output as JSON when --json is set."""
        from rots.commands.workflow.app import deploy

        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        monkeypatch.chdir(tmp_path)

        with pytest.raises(SystemExit) as exc_info:
            deploy(tag="v1.0.0", json_output=True)

        assert exc_info.value.code == EXIT_PRECOND

        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert "error" in output
        assert output["exit_code"] == EXIT_PRECOND


class TestDeployExecution:
    """Tests for deploy command execution and exit codes."""

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

    def test_all_succeed_exit_success(self, tmp_path, monkeypatch, capsys, make_mock_plan):
        """EXIT_SUCCESS (0) when all steps succeed."""
        from rots.commands.workflow.app import deploy

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
                    deploy(
                        hosts=("host1",),
                        tag="v1.0.0",
                    )

        assert exc_info.value.code == EXIT_SUCCESS

    def test_some_fail_exit_partial(self, tmp_path, monkeypatch, capsys, make_mock_plan):
        """EXIT_PARTIAL (2) when some steps fail but at least one succeeds."""
        from rots.commands.workflow.app import deploy

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
                    deploy(
                        hosts=("host1", "host2"),
                        tag="v1.0.0",
                    )

        assert exc_info.value.code == EXIT_PARTIAL

    def test_all_fail_exit_failure(self, tmp_path, monkeypatch, capsys, make_mock_plan):
        """EXIT_FAILURE (1) when all steps fail."""
        from rots.commands.workflow.app import deploy

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
                    deploy(
                        hosts=("host1",),
                        tag="v1.0.0",
                    )

        assert exc_info.value.code == EXIT_FAILURE

    def test_unexpected_exception_exit_failure(self, tmp_path, monkeypatch, capsys, make_mock_plan):
        """EXIT_FAILURE (1) on unexpected exception during execution."""
        from rots.commands.workflow.app import deploy

        monkeypatch.chdir(tmp_path)

        with patch("rots.commands.workflow.app.create_plan") as mock_plan:
            mock_plan.return_value = make_mock_plan(host_count=1, step_count=2)
            with patch("rots.deploy.orchestrator.execute") as mock_exec:
                mock_exec.side_effect = RuntimeError("Connection lost")
                with pytest.raises(SystemExit) as exc_info:
                    deploy(
                        hosts=("host1",),
                        tag="v1.0.0",
                    )

        assert exc_info.value.code == EXIT_FAILURE


class TestDeployJsonOutput:
    """Tests for deploy command JSON output format."""

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
        from rots.commands.workflow.app import deploy

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
                    deploy(
                        hosts=("host1",),
                        tag="v1.0.0",
                        json_output=True,
                    )

        assert exc_info.value.code == EXIT_SUCCESS

        captured = capsys.readouterr()
        output = json.loads(captured.out)

        assert output["action"] == "deploy"
        assert output["image_tag"] == "v1.0.0"
        assert output["total_hosts"] == 1
        assert output["total_steps"] == 2
        assert output["executed_steps"] == 2
        assert output["succeeded"] == 2
        assert output["failed"] == 0
        assert "results" in output
        assert len(output["results"]) == 2

    def test_failure_json_output(self, tmp_path, monkeypatch, capsys, make_mock_plan):
        """JSON output contains error info on failure."""
        from rots.commands.workflow.app import deploy

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
                    deploy(
                        hosts=("host1",),
                        tag="v1.0.0",
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
        from rots.commands.workflow.app import deploy

        monkeypatch.chdir(tmp_path)

        def yield_then_raise():
            yield self._make_result("host1", "rots.image.pull", True)
            raise RuntimeError("Connection lost")

        with patch("rots.commands.workflow.app.create_plan") as mock_plan:
            mock_plan.return_value = make_mock_plan(host_count=2, step_count=4)
            with patch("rots.deploy.orchestrator.execute") as mock_exec:
                mock_exec.return_value = yield_then_raise()
                with pytest.raises(SystemExit) as exc_info:
                    deploy(
                        hosts=("host1", "host2"),
                        tag="v1.0.0",
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


class TestDeployResultDict:
    """Tests for result serialization."""

    def test_result_to_dict_fields(self, tmp_path, monkeypatch, capsys, make_mock_plan):
        """Result dict includes all required fields."""
        from rots.commands.workflow.app import deploy

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
                    deploy(
                        hosts=("host1",),
                        tag="v1.0.0",
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
