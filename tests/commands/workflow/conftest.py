# packages/rots/tests/commands/workflow/conftest.py

"""Fixtures for workflow command tests."""

from unittest.mock import MagicMock

import pytest


@pytest.fixture
def make_mock_plan():
    """Factory fixture for creating mock DeployPlan objects.

    Creates MagicMock instances with all attributes needed by display_plan()
    and other reporting utilities.

    Usage:
        def test_something(make_mock_plan):
            plan = make_mock_plan(host_count=2, step_count=4)
    """

    def _make_plan(
        host_count: int = 1,
        step_count: int = 2,
        image_tag: str = "v1.0.0",
        delay: float = 5.0,
        failure_mode_value: str = "stop",
        steps: list | None = None,
    ) -> MagicMock:
        """Create a mock plan with specified attributes.

        Args:
            host_count: Number of hosts in the plan.
            step_count: Number of steps in the plan.
            image_tag: Image tag being deployed.
            delay: Delay between steps in seconds.
            failure_mode_value: The .value of the failure mode enum.
            steps: List of mock steps (defaults to empty list).

        Returns:
            MagicMock configured to look like a DeployPlan.
        """
        mock_plan = MagicMock()
        mock_plan.host_count = host_count
        mock_plan.step_count = step_count
        mock_plan.image_tag = image_tag
        mock_plan.delay = delay
        mock_plan.failure_mode.value = failure_mode_value
        mock_plan.steps = steps if steps is not None else []
        return mock_plan

    return _make_plan
