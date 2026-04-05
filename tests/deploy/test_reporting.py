# tests/deploy/test_reporting.py

"""Tests for rots.deploy.reporting module.

Covers:
- display_plan() for text and JSON output
- format_results() for text and JSON output
- determine_exit_code() for all exit code scenarios
- result_to_dict() for StepResult serialization
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from rots.deploy.reporting import (
    EXIT_FAILURE,
    EXIT_PARTIAL,
    EXIT_SUCCESS,
    determine_exit_code,
    display_plan,
    format_results,
    result_to_dict,
)

pytestmark = pytest.mark.quick


class TestDisplayPlan:
    """Tests for display_plan function."""

    def _make_mock_plan(
        self,
        image_tag: str = "v1.0.0",
        host_count: int = 2,
        step_count: int = 4,
        delay: float = 5.0,
        failure_mode_value: str = "stop",
        steps: list | None = None,
    ) -> MagicMock:
        """Create a mock DeployPlan."""
        plan = MagicMock()
        plan.image_tag = image_tag
        plan.host_count = host_count
        plan.step_count = step_count
        plan.delay = delay
        plan.failure_mode.value = failure_mode_value
        plan.steps = steps if steps is not None else []
        return plan

    def _make_mock_step(
        self,
        host_id: str = "host1",
        command: str = "rots.image.pull",
        args: list | None = None,
        timeout: float = 120.0,
    ) -> MagicMock:
        """Create a mock DeployStep."""
        step = MagicMock()
        step.host_id = host_id
        step.command = command
        step.payload = {"args": args or ["--tag", "v1.0.0"]}
        step.timeout = timeout
        step.description = f"{command.replace('rots.', '')} {' '.join(args or [])}"
        return step

    def test_text_format_includes_plan_details(self):
        """Text format includes image tag, counts, and delay."""
        plan = self._make_mock_plan(
            image_tag="v2.0.0",
            host_count=3,
            step_count=6,
            delay=10.0,
            failure_mode_value="continue",
        )

        output = display_plan(plan, format="text")

        assert "v2.0.0" in output
        assert "Hosts: 3" in output
        assert "Steps: 6" in output
        assert "Delay: 10.0s" in output
        assert "continue" in output

    def test_text_format_lists_steps(self):
        """Text format lists each step with number and description."""
        steps = [
            self._make_mock_step("host1", "rots.image.pull"),
            self._make_mock_step("host1", "rots.instance.redeploy"),
        ]
        plan = self._make_mock_plan(steps=steps)

        output = display_plan(plan, format="text")

        assert "[1]" in output
        assert "[2]" in output
        assert "host1" in output

    def test_json_format_structure(self):
        """JSON format has correct structure."""
        steps = [
            self._make_mock_step("host1", "rots.image.pull", ["--tag", "v1.0.0"]),
        ]
        plan = self._make_mock_plan(
            image_tag="v1.0.0",
            host_count=1,
            step_count=2,
            delay=5.0,
            failure_mode_value="stop",
            steps=steps,
        )

        output = display_plan(plan, format="json")
        data = json.loads(output)

        assert data["action"] == "plan"
        assert data["image_tag"] == "v1.0.0"
        assert data["host_count"] == 1
        assert data["step_count"] == 2
        assert data["failure_mode"] == "stop"
        assert data["delay"] == 5.0
        assert "steps" in data
        assert len(data["steps"]) == 1

    def test_json_step_includes_required_fields(self):
        """JSON step includes host_id, command, args, timeout."""
        steps = [
            self._make_mock_step("host1", "rots.image.pull", ["--tag", "v1.0.0"], timeout=180.0),
        ]
        plan = self._make_mock_plan(steps=steps)

        output = display_plan(plan, format="json")
        data = json.loads(output)
        step = data["steps"][0]

        assert step["host_id"] == "host1"
        assert step["command"] == "rots.image.pull"
        assert step["args"] == ["--tag", "v1.0.0"]
        assert step["timeout"] == 180.0

    def test_text_format_empty_steps(self):
        """Text format handles plan with zero steps."""
        plan = self._make_mock_plan(
            step_count=0,
            steps=[],
        )

        output = display_plan(plan, format="text")

        assert "Steps: 0" in output
        # Should not contain any numbered step lines
        assert "[1]" not in output

    def test_json_format_empty_steps(self):
        """JSON format handles plan with zero steps."""
        plan = self._make_mock_plan(
            step_count=0,
            steps=[],
        )

        output = display_plan(plan, format="json")
        data = json.loads(output)

        assert data["step_count"] == 0
        assert data["steps"] == []

    def test_json_step_missing_args_key(self):
        """JSON step handles payload without args key."""
        step = MagicMock()
        step.host_id = "host1"
        step.command = "rots.image.pull"
        step.payload = {}  # No args key
        step.timeout = 120.0
        step.description = "image.pull"

        plan = self._make_mock_plan(steps=[step])

        output = display_plan(plan, format="json")
        data = json.loads(output)

        # Should default to empty list
        assert data["steps"][0]["args"] == []

    def test_text_format_multiple_hosts(self):
        """Text format lists steps for multiple hosts."""
        steps = [
            self._make_mock_step("host-alpha", "rots.image.pull"),
            self._make_mock_step("host-beta", "rots.image.pull"),
            self._make_mock_step("host-alpha", "rots.instance.redeploy"),
            self._make_mock_step("host-beta", "rots.instance.redeploy"),
        ]
        plan = self._make_mock_plan(host_count=2, step_count=4, steps=steps)

        output = display_plan(plan, format="text")

        assert "host-alpha" in output
        assert "host-beta" in output
        assert "[1]" in output
        assert "[4]" in output


class TestFormatResults:
    """Tests for format_results function."""

    def _make_mock_plan(self, host_count: int = 1, step_count: int = 2) -> MagicMock:
        """Create a mock DeployPlan."""
        plan = MagicMock()
        plan.image_tag = "v1.0.0"
        plan.host_count = host_count
        plan.step_count = step_count
        return plan

    def _make_mock_result(
        self,
        host_id: str = "host1",
        command: str = "rots.image.pull",
        success: bool = True,
        error: str | None = None,
        duration_ms: int = 1000,
    ) -> MagicMock:
        """Create a mock StepResult."""
        step = MagicMock()
        step.command = command

        result = MagicMock()
        result.host_id = host_id
        result.step = step
        result.success = success
        result.error = error
        result.duration_ms = duration_ms
        return result

    def test_text_format_summary(self):
        """Text format shows summary with succeeded/failed counts."""
        results = [
            self._make_mock_result(success=True),
            self._make_mock_result(success=False, error="timeout"),
        ]
        plan = self._make_mock_plan()

        output = format_results(results, plan, format="text")

        assert "1/2" in output
        assert "succeeded" in output
        assert "1 failed" in output

    def test_text_format_all_success(self):
        """Text format shows all succeeded."""
        results = [
            self._make_mock_result(success=True),
            self._make_mock_result(success=True),
        ]
        plan = self._make_mock_plan()

        output = format_results(results, plan, format="text")

        assert "2/2" in output
        assert "0 failed" in output

    def test_json_format_structure(self):
        """JSON format has correct structure."""
        results = [
            self._make_mock_result("host1", "rots.image.pull", True, None, 1500),
            self._make_mock_result("host1", "rots.instance.redeploy", False, "timeout", 500),
        ]
        plan = self._make_mock_plan(host_count=1, step_count=2)

        output = format_results(results, plan, format="json", action="deploy")
        data = json.loads(output)

        assert data["action"] == "deploy"
        assert data["image_tag"] == "v1.0.0"
        assert data["total_hosts"] == 1
        assert data["total_steps"] == 2
        assert data["executed_steps"] == 2
        assert data["succeeded"] == 1
        assert data["failed"] == 1
        assert data["total_duration_ms"] == 2000
        assert len(data["results"]) == 2

    def test_json_format_custom_action(self):
        """JSON format respects custom action name."""
        results = [self._make_mock_result(success=True)]
        plan = self._make_mock_plan()

        output = format_results(results, plan, format="json", action="trigger")
        data = json.loads(output)

        assert data["action"] == "trigger"

    def test_text_format_empty_results(self):
        """Text format handles empty results list."""
        results = []
        plan = self._make_mock_plan()

        output = format_results(results, plan, format="text")

        assert "0/0" in output
        assert "0 failed" in output

    def test_json_format_empty_results(self):
        """JSON format handles empty results list."""
        results = []
        plan = self._make_mock_plan(host_count=2, step_count=4)

        output = format_results(results, plan, format="json", action="deploy")
        data = json.loads(output)

        assert data["total_hosts"] == 2
        assert data["total_steps"] == 4
        assert data["executed_steps"] == 0
        assert data["succeeded"] == 0
        assert data["failed"] == 0
        assert data["total_duration_ms"] == 0
        assert data["results"] == []

    def test_json_format_results_structure(self):
        """JSON format results array has correct structure from result_to_dict."""
        results = [
            self._make_mock_result("host1", "rots.image.pull", True, None, 1000),
            self._make_mock_result("host2", "rots.image.pull", False, "timeout", 2000),
        ]
        plan = self._make_mock_plan(host_count=2, step_count=2)

        output = format_results(results, plan, format="json")
        data = json.loads(output)

        # Verify each result has expected fields
        for r in data["results"]:
            assert "host_id" in r
            assert "command" in r
            assert "success" in r
            assert "error" in r
            assert "duration_ms" in r

        # Verify specific values
        assert data["results"][0]["success"] is True
        assert data["results"][0]["error"] is None
        assert data["results"][1]["success"] is False
        assert data["results"][1]["error"] == "timeout"

    def test_text_format_all_failed(self):
        """Text format shows all failed."""
        results = [
            self._make_mock_result(success=False, error="error1"),
            self._make_mock_result(success=False, error="error2"),
        ]
        plan = self._make_mock_plan()

        output = format_results(results, plan, format="text")

        assert "0/2" in output
        assert "2 failed" in output

    def test_json_format_large_duration(self):
        """JSON format handles large duration values."""
        results = [
            self._make_mock_result(duration_ms=300000),  # 5 minutes
            self._make_mock_result(duration_ms=600000),  # 10 minutes
        ]
        plan = self._make_mock_plan()

        output = format_results(results, plan, format="json")
        data = json.loads(output)

        assert data["total_duration_ms"] == 900000

    def test_json_format_same_host_multiple_steps(self):
        """JSON format correctly reports when all steps are on same host."""
        results = [
            self._make_mock_result("host1", "rots.image.pull", True, None, 1000),
            self._make_mock_result("host1", "rots.instance.redeploy", True, None, 2000),
            self._make_mock_result("host1", "rots.instance.status", True, None, 500),
        ]
        plan = self._make_mock_plan(host_count=1, step_count=3)

        output = format_results(results, plan, format="json")
        data = json.loads(output)

        assert data["total_hosts"] == 1
        assert data["executed_steps"] == 3
        # All results should have same host_id
        assert all(r["host_id"] == "host1" for r in data["results"])


class TestDetermineExitCode:
    """Tests for determine_exit_code function."""

    def _make_result(self, success: bool) -> MagicMock:
        """Create a mock StepResult."""
        result = MagicMock()
        result.success = success
        return result

    def test_all_success_returns_exit_success(self):
        """All successful results return EXIT_SUCCESS (0)."""
        results = [
            self._make_result(True),
            self._make_result(True),
            self._make_result(True),
        ]

        assert determine_exit_code(results) == EXIT_SUCCESS
        assert determine_exit_code(results) == 0

    def test_all_failure_returns_exit_failure(self):
        """All failed results return EXIT_FAILURE (1)."""
        results = [
            self._make_result(False),
            self._make_result(False),
        ]

        assert determine_exit_code(results) == EXIT_FAILURE
        assert determine_exit_code(results) == 1

    def test_partial_success_returns_exit_partial(self):
        """Mixed results return EXIT_PARTIAL (2)."""
        results = [
            self._make_result(True),
            self._make_result(False),
            self._make_result(True),
        ]

        assert determine_exit_code(results) == EXIT_PARTIAL
        assert determine_exit_code(results) == 2

    def test_empty_results_returns_exit_failure(self):
        """Empty results list returns EXIT_FAILURE (1)."""
        assert determine_exit_code([]) == EXIT_FAILURE

    def test_single_success(self):
        """Single successful result returns EXIT_SUCCESS."""
        results = [self._make_result(True)]
        assert determine_exit_code(results) == EXIT_SUCCESS

    def test_single_failure(self):
        """Single failed result returns EXIT_FAILURE."""
        results = [self._make_result(False)]
        assert determine_exit_code(results) == EXIT_FAILURE


class TestResultToDict:
    """Tests for result_to_dict function."""

    def test_includes_all_fields(self):
        """Result dict includes all required fields."""
        step = MagicMock()
        step.command = "rots.image.pull"

        result = MagicMock()
        result.host_id = "test-host"
        result.step = step
        result.success = True
        result.error = None
        result.duration_ms = 1234

        d = result_to_dict(result)

        assert d["host_id"] == "test-host"
        assert d["command"] == "rots.image.pull"
        assert d["success"] is True
        assert d["error"] is None
        assert d["duration_ms"] == 1234

    def test_includes_error_on_failure(self):
        """Result dict includes error message on failure."""
        step = MagicMock()
        step.command = "rots.instance.redeploy"

        result = MagicMock()
        result.host_id = "test-host"
        result.step = step
        result.success = False
        result.error = "connection timeout"
        result.duration_ms = 5000

        d = result_to_dict(result)

        assert d["success"] is False
        assert d["error"] == "connection timeout"

    def test_zero_duration(self):
        """Result dict handles zero duration."""
        step = MagicMock()
        step.command = "rots.instance.status"

        result = MagicMock()
        result.host_id = "test-host"
        result.step = step
        result.success = True
        result.error = None
        result.duration_ms = 0

        d = result_to_dict(result)

        assert d["duration_ms"] == 0

    def test_multiline_error_message(self):
        """Result dict preserves multiline error messages."""
        step = MagicMock()
        step.command = "rots.instance.redeploy"

        result = MagicMock()
        result.host_id = "test-host"
        result.step = step
        result.success = False
        result.error = "connection failed\nretry exhausted\ntimeout after 30s"
        result.duration_ms = 30000

        d = result_to_dict(result)

        assert "\n" in d["error"]
        assert "retry exhausted" in d["error"]
