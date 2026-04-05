# tests/deploy/test_orchestrator.py

"""Tests for src/rots/deploy/orchestrator.py

Covers:
- create_plan step generation
- execute generator behavior
- FailureMode handling
- Error handling and timeouts
"""

from unittest.mock import MagicMock, patch

from rots.deploy.orchestrator import (
    DeployStep,
    FailureMode,
    create_plan,
    execute,
    publish_and_wait,
)


class TestCreatePlan:
    """Tests for create_plan step generation."""

    def test_generates_two_steps_per_host(self):
        """Creates pull and redeploy steps for each host."""
        plan = create_plan(["host1", "host2"], "v0.24.1")

        assert plan.step_count == 4
        assert plan.host_count == 2

    def test_step_order_is_pull_then_redeploy(self):
        """Each host gets pull before redeploy."""
        plan = create_plan(["host1"], "v0.24.1")

        assert plan.steps[0].command == "rots.image.pull"
        assert plan.steps[1].command == "rots.instance.redeploy"

    def test_pull_step_has_correct_args(self):
        """Pull step includes --tag and --current flags."""
        plan = create_plan(["host1"], "v0.24.1")

        pull_step = plan.steps[0]
        assert pull_step.payload["args"] == ["--tag", "v0.24.1", "--current"]

    def test_redeploy_step_has_correct_args(self):
        """Redeploy step includes port."""
        plan = create_plan(["host1"], "v0.24.1", port=7044)

        redeploy_step = plan.steps[1]
        assert redeploy_step.payload["args"] == ["7044"]

    def test_default_failure_mode_is_stop_on_first(self):
        """Default failure mode should be STOP_ON_FIRST."""
        plan = create_plan(["host1"], "v0.24.1")

        assert plan.failure_mode == FailureMode.STOP_ON_FIRST

    def test_custom_failure_mode(self):
        """Custom failure mode is respected."""
        plan = create_plan(["host1"], "v0.24.1", failure_mode=FailureMode.CONTINUE_ALL)

        assert plan.failure_mode == FailureMode.CONTINUE_ALL

    def test_custom_timeouts(self):
        """Custom timeouts are applied to steps."""
        plan = create_plan(
            ["host1"],
            "v0.24.1",
            pull_timeout=180.0,
            redeploy_timeout=90.0,
        )

        assert plan.steps[0].timeout == 180.0  # pull
        assert plan.steps[1].timeout == 90.0  # redeploy

    def test_default_port(self):
        """Default port is 7043."""
        plan = create_plan(["host1"], "v0.24.1")

        redeploy_step = plan.steps[1]
        assert redeploy_step.payload["args"] == ["7043"]


class TestDeployStep:
    """Tests for DeployStep dataclass."""

    def test_description_with_args(self):
        """Description includes command and args."""
        step = DeployStep(
            host_id="host1",
            command="rots.image.pull",
            payload={"args": ["--tag", "v0.24.1"]},
        )

        assert step.description == "image.pull --tag v0.24.1"

    def test_description_without_args(self):
        """Description works without args."""
        step = DeployStep(
            host_id="host1",
            command="rots.doctor",
            payload={},
        )

        assert step.description == "doctor"


class TestExecute:
    """Tests for execute generator behavior."""

    @patch("rots.deploy.orchestrator.publish_and_wait")
    @patch("rots.deploy.orchestrator.time.sleep")
    def test_yields_results_in_order(self, mock_sleep, mock_publish):
        """Results are yielded in step order."""
        mock_publish.return_value = {"success": True}

        plan = create_plan(["host1", "host2"], "v0.24.1", delay=0)
        results = list(execute(plan))

        assert len(results) == 4
        assert results[0].step.host_id == "host1"
        assert results[0].step.command == "rots.image.pull"
        assert results[1].step.host_id == "host1"
        assert results[1].step.command == "rots.instance.redeploy"
        assert results[2].step.host_id == "host2"
        assert results[3].step.host_id == "host2"

    @patch("rots.deploy.orchestrator.publish_and_wait")
    @patch("rots.deploy.orchestrator.time.sleep")
    def test_stops_on_first_failure_by_default(self, mock_sleep, mock_publish):
        """Stops iteration after first failure when STOP_ON_FIRST."""
        # First call succeeds, second fails
        mock_publish.side_effect = [
            {"success": True},
            {"success": False, "error": "deploy failed"},
        ]

        plan = create_plan(["host1"], "v0.24.1", delay=0)
        results = list(execute(plan))

        # Should stop after the failed redeploy
        assert len(results) == 2
        assert results[0].success is True
        assert results[1].success is False

    @patch("rots.deploy.orchestrator.publish_and_wait")
    @patch("rots.deploy.orchestrator.time.sleep")
    def test_continues_when_continue_all(self, mock_sleep, mock_publish):
        """Continues after failure when CONTINUE_ALL."""
        # All succeed except second call
        mock_publish.side_effect = [
            {"success": True},
            {"success": False, "error": "deploy failed"},
            {"success": True},
            {"success": True},
        ]

        plan = create_plan(
            ["host1", "host2"],
            "v0.24.1",
            delay=0,
            failure_mode=FailureMode.CONTINUE_ALL,
        )
        results = list(execute(plan))

        # Should continue to host2 even though host1 redeploy failed
        assert len(results) == 4

    @patch("rots.deploy.orchestrator.publish_and_wait")
    @patch("rots.deploy.orchestrator.time.sleep")
    def test_handles_timeout_error(self, mock_sleep, mock_publish):
        """TimeoutError is captured as failed result."""
        mock_publish.side_effect = TimeoutError("No response within 60s")

        plan = create_plan(["host1"], "v0.24.1", delay=0)
        results = list(execute(plan))

        assert len(results) == 1
        assert results[0].success is False
        assert "Timeout" in results[0].error

    @patch("rots.deploy.orchestrator.publish_and_wait")
    @patch("rots.deploy.orchestrator.time.sleep")
    def test_handles_unexpected_exception(self, mock_sleep, mock_publish):
        """Unexpected exceptions are captured as failed results."""
        mock_publish.side_effect = RuntimeError("Connection lost")

        plan = create_plan(["host1"], "v0.24.1", delay=0)
        results = list(execute(plan))

        assert len(results) == 1
        assert results[0].success is False
        assert "Unexpected error" in results[0].error

    @patch("rots.deploy.orchestrator.publish_and_wait")
    @patch("rots.deploy.orchestrator.time.sleep")
    def test_applies_delay_between_steps(self, mock_sleep, mock_publish):
        """Delay is applied between steps (not before first)."""
        mock_publish.return_value = {"success": True}

        plan = create_plan(["host1"], "v0.24.1", delay=5.0)
        list(execute(plan))

        # Delay should be called once (between step 1 and 2, not before step 1)
        assert mock_sleep.call_count == 1
        mock_sleep.assert_called_with(5.0)

    @patch("rots.deploy.orchestrator.publish_and_wait")
    @patch("rots.deploy.orchestrator.time.sleep")
    def test_records_duration(self, mock_sleep, mock_publish):
        """Duration is recorded for each step."""
        mock_publish.return_value = {"success": True}

        plan = create_plan(["host1"], "v0.24.1", delay=0)
        results = list(execute(plan))

        # All results should have non-negative duration
        for result in results:
            assert result.duration_ms >= 0


class TestPublishAndWait:
    """Tests for publish_and_wait wrapper."""

    @patch("rots.sidecar.rabbitmq.publish_command")
    @patch("rots.sidecar.rabbitmq.RabbitMQConfig")
    def test_passes_args_as_payload(self, mock_config_class, mock_publish):
        """CLI-style args are converted to payload."""
        mock_config = MagicMock()
        mock_config_class.from_environment.return_value = mock_config
        mock_publish.return_value = {"success": True}

        publish_and_wait(
            host_id="host1",
            command="rots.image.pull",
            args=["--tag", "v0.24.1"],
        )

        mock_publish.assert_called_once()
        call_kwargs = mock_publish.call_args[1]
        assert call_kwargs["payload"] == {"args": ["--tag", "v0.24.1"]}
        assert call_kwargs["target_host"] == "host1"

    @patch("rots.sidecar.rabbitmq.publish_command")
    @patch("rots.sidecar.rabbitmq.RabbitMQConfig")
    def test_payload_overrides_args(self, mock_config_class, mock_publish):
        """Explicit payload is used if provided."""
        mock_config = MagicMock()
        mock_config_class.from_environment.return_value = mock_config
        mock_publish.return_value = {"success": True}

        publish_and_wait(
            host_id="host1",
            command="rots.image.pull",
            args=["--tag", "v0.24.1"],
            payload={"args": ["--tag", "v0.25.0"], "timeout": 300},
        )

        call_kwargs = mock_publish.call_args[1]
        # Payload should contain the explicit payload, not the args
        assert call_kwargs["payload"]["args"] == ["--tag", "v0.25.0"]
        assert call_kwargs["payload"]["timeout"] == 300
