# tests/commands/instance/test_health_and_ps.py

"""Tests for container health display and instances ps subcommand."""

import json

import pytest

from rots.commands import instance
from rots.config import Config
from rots.systemd import get_container_health_map


@pytest.fixture(autouse=True)
def mock_systemctl_available(mocker):
    """Mock shutil.which to report systemctl as available for all tests."""
    mocker.patch("shutil.which", return_value="/mock/bin/systemctl")


@pytest.fixture(autouse=True)
def _mock_get_executor(mocker):
    """Mock Config.get_executor to return None (local execution)."""
    mocker.patch.object(Config, "get_executor", return_value=None)


class TestGetContainerHealthMap:
    """Tests for get_container_health_map() parsing."""

    def _make_executor(self, mocker, podman_output):
        """Create a mock executor returning the given podman ps JSON."""
        mock_ex = mocker.Mock()
        mock_result = mocker.Mock()
        mock_result.ok = True
        mock_result.stdout = podman_output
        mock_ex.run.return_value = mock_result
        return mock_ex

    def test_parse_healthy_containers(self, mocker):
        """Should extract health from Status field."""
        output = json.dumps(
            [
                {
                    "Names": ["onetime-web@7043"],
                    "Status": "Up 3 days (healthy)",
                },
                {
                    "Names": ["onetime-worker@1"],
                    "Status": "Up 3 days (unhealthy)",
                },
            ]
        )
        ex = self._make_executor(mocker, output)
        result = get_container_health_map(executor=ex)

        assert result[("web", "7043")] == {"health": "healthy", "uptime": "Up 3 days"}
        assert result[("worker", "1")] == {"health": "unhealthy", "uptime": "Up 3 days"}

    def test_parse_scheduler_container(self, mocker):
        """Should parse scheduler container with @ naming."""
        output = json.dumps(
            [
                {
                    "Names": ["onetime-scheduler@main"],
                    "Status": "Up 2 hours (healthy)",
                },
            ]
        )
        ex = self._make_executor(mocker, output)
        result = get_container_health_map(executor=ex)

        assert result[("scheduler", "main")] == {"health": "healthy", "uptime": "Up 2 hours"}

    def test_container_without_healthcheck(self, mocker):
        """Containers with no health annotation should return empty health."""
        output = json.dumps(
            [
                {
                    "Names": ["onetime-web@7044"],
                    "Status": "Up 5 minutes",
                },
            ]
        )
        ex = self._make_executor(mocker, output)
        result = get_container_health_map(executor=ex)

        assert result[("web", "7044")] == {"health": "", "uptime": "Up 5 minutes"}

    def test_starting_health_state(self, mocker):
        """Should recognize 'starting' as a health state."""
        output = json.dumps(
            [
                {
                    "Names": ["onetime-web@7043"],
                    "Status": "Up 10 seconds (starting)",
                },
            ]
        )
        ex = self._make_executor(mocker, output)
        result = get_container_health_map(executor=ex)

        assert result[("web", "7043")]["health"] == "starting"

    def test_empty_output(self, mocker):
        """Should return empty dict on empty podman output."""
        ex = self._make_executor(mocker, "")
        result = get_container_health_map(executor=ex)
        assert result == {}

    def test_failed_command(self, mocker):
        """Should return empty dict when podman command fails."""
        mock_ex = mocker.Mock()
        mock_result = mocker.Mock()
        mock_result.ok = False
        mock_result.stdout = ""
        mock_ex.run.return_value = mock_result

        result = get_container_health_map(executor=mock_ex)
        assert result == {}

    def test_name_as_string(self, mocker):
        """Should handle Name as string (some podman versions)."""
        output = json.dumps(
            [
                {
                    "Name": "onetime-web@7043",
                    "Status": "Up 1 day (healthy)",
                },
            ]
        )
        ex = self._make_executor(mocker, output)
        result = get_container_health_map(executor=ex)

        assert ("web", "7043") in result

    def test_non_rots_ignored(self, mocker):
        """Should skip containers that don't match the naming pattern."""
        output = json.dumps(
            [
                {
                    "Names": ["systemd-some-other-container"],
                    "Status": "Up 1 day",
                },
                {
                    "Names": ["onetime-web@7043"],
                    "Status": "Up 1 day (healthy)",
                },
            ]
        )
        ex = self._make_executor(mocker, output)
        result = get_container_health_map(executor=ex)

        assert len(result) == 1
        assert ("web", "7043") in result


class TestListInstancesWithHealth:
    """Tests for health info in instances list output."""

    def _mock_discovery(self, mocker, web_ports=None, workers=None, schedulers=None):
        """Mock instance discovery."""
        mocker.patch(
            "rots.commands.instance._helpers.systemd.discover_web_instances",
            return_value=web_ports or [],
        )
        mocker.patch(
            "rots.commands.instance._helpers.systemd.discover_worker_instances",
            return_value=workers or [],
        )
        mocker.patch(
            "rots.commands.instance._helpers.systemd.discover_scheduler_instances",
            return_value=schedulers or [],
        )

    def test_list_displays_healthy_status(self, mocker, capsys, tmp_path):
        """List should combine systemd status with container health."""
        self._mock_discovery(mocker, web_ports=[7043])
        mocker.patch(
            "rots.commands.instance.app.systemd.is_active",
            return_value="active",
        )
        mocker.patch(
            "rots.commands.instance.app.systemd.get_container_health_map",
            return_value={("web", "7043"): {"health": "healthy", "uptime": "Up 3 days"}},
        )

        mock_config = mocker.Mock()
        mock_config.db_path = tmp_path / "test.db"
        mock_config.get_executor.return_value = None
        mocker.patch("rots.commands.instance.app.Config", return_value=mock_config)
        mocker.patch("rots.commands.instance.app.db.get_deployments", return_value=[])

        instance.list_instances()

        captured = capsys.readouterr()
        assert "active (healthy)" in captured.out

    def test_list_displays_unhealthy_status(self, mocker, capsys, tmp_path):
        """List should show unhealthy status when container health is bad."""
        self._mock_discovery(mocker, workers=["1"])
        mocker.patch(
            "rots.commands.instance.app.systemd.is_active",
            return_value="active",
        )
        mocker.patch(
            "rots.commands.instance.app.systemd.get_container_health_map",
            return_value={("worker", "1"): {"health": "unhealthy", "uptime": "Up 3 days"}},
        )

        mock_config = mocker.Mock()
        mock_config.db_path = tmp_path / "test.db"
        mock_config.get_executor.return_value = None
        mocker.patch("rots.commands.instance.app.Config", return_value=mock_config)
        mocker.patch("rots.commands.instance.app.db.get_deployments", return_value=[])

        instance.list_instances()

        captured = capsys.readouterr()
        assert "active (unhealthy)" in captured.out

    def test_list_no_health_info(self, mocker, capsys, tmp_path):
        """When no health data, should show plain systemd status."""
        self._mock_discovery(mocker, web_ports=[7043])
        mocker.patch(
            "rots.commands.instance.app.systemd.is_active",
            return_value="active",
        )
        mocker.patch(
            "rots.commands.instance.app.systemd.get_container_health_map",
            return_value={},
        )

        mock_config = mocker.Mock()
        mock_config.db_path = tmp_path / "test.db"
        mock_config.get_executor.return_value = None
        mocker.patch("rots.commands.instance.app.Config", return_value=mock_config)
        mocker.patch("rots.commands.instance.app.db.get_deployments", return_value=[])

        instance.list_instances()

        captured = capsys.readouterr()
        # Should show "active" without parenthetical
        assert "active" in captured.out
        assert "(healthy)" not in captured.out
        assert "(unhealthy)" not in captured.out

    def test_list_json_includes_health(self, mocker, capsys, tmp_path):
        """JSON output should include health and uptime fields."""
        self._mock_discovery(mocker, web_ports=[7043])
        mocker.patch(
            "rots.commands.instance.app.systemd.is_active",
            return_value="active",
        )
        mocker.patch(
            "rots.commands.instance.app.systemd.get_container_health_map",
            return_value={("web", "7043"): {"health": "healthy", "uptime": "Up 3 days"}},
        )

        mock_config = mocker.Mock()
        mock_config.db_path = tmp_path / "test.db"
        mock_config.get_executor.return_value = None
        mocker.patch("rots.commands.instance.app.Config", return_value=mock_config)
        mocker.patch("rots.commands.instance.app.db.get_deployments", return_value=[])

        instance.list_instances(json_output=True)

        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data[0]["health"] == "healthy"
        assert data[0]["uptime"] == "Up 3 days"

    def test_list_json_empty_health(self, mocker, capsys, tmp_path):
        """JSON output should have empty health when no data available."""
        self._mock_discovery(mocker, web_ports=[7043])
        mocker.patch(
            "rots.commands.instance.app.systemd.is_active",
            return_value="active",
        )
        mocker.patch(
            "rots.commands.instance.app.systemd.get_container_health_map",
            return_value={},
        )

        mock_config = mocker.Mock()
        mock_config.db_path = tmp_path / "test.db"
        mock_config.get_executor.return_value = None
        mocker.patch("rots.commands.instance.app.Config", return_value=mock_config)
        mocker.patch("rots.commands.instance.app.db.get_deployments", return_value=[])

        instance.list_instances(json_output=True)

        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data[0]["health"] == ""
        assert data[0]["uptime"] == ""


class TestPsCommand:
    """Tests for instances ps subcommand."""

    def test_ps_function_exists(self):
        """ps command should be importable from instance module."""
        assert hasattr(instance, "ps")
        assert callable(instance.ps)

    def test_ps_runs_podman_for_all(self, mocker):
        """ps with no type filter should use broad name filter."""
        mock_config = mocker.Mock()
        mock_config.get_executor.return_value = None
        mocker.patch("rots.commands.instance.app.Config", return_value=mock_config)

        mock_podman_instance = mocker.Mock()
        mock_podman_ps = mocker.Mock()
        mock_podman_instance.ps = mock_podman_ps
        mocker.patch(
            "rots.commands.instance.app.Podman",
            return_value=mock_podman_instance,
        )

        instance.ps()

        mock_podman_ps.assert_called_once()
        call_kwargs = mock_podman_ps.call_args
        assert call_kwargs.kwargs["filter"] == "name=onetime-"

    def test_ps_filters_by_web(self, mocker):
        """ps --web should filter to web containers."""
        mock_config = mocker.Mock()
        mock_config.get_executor.return_value = None
        mocker.patch("rots.commands.instance.app.Config", return_value=mock_config)

        mock_podman_instance = mocker.Mock()
        mock_podman_ps = mocker.Mock()
        mock_podman_instance.ps = mock_podman_ps
        mocker.patch(
            "rots.commands.instance.app.Podman",
            return_value=mock_podman_instance,
        )

        instance.ps(web="")

        call_kwargs = mock_podman_ps.call_args
        assert call_kwargs.kwargs["filter"] == "name=onetime-web@"

    def test_ps_filters_by_scheduler(self, mocker):
        """ps --scheduler should filter to scheduler containers."""
        mock_config = mocker.Mock()
        mock_config.get_executor.return_value = None
        mocker.patch("rots.commands.instance.app.Config", return_value=mock_config)

        mock_podman_instance = mocker.Mock()
        mock_podman_ps = mocker.Mock()
        mock_podman_instance.ps = mock_podman_ps
        mocker.patch(
            "rots.commands.instance.app.Podman",
            return_value=mock_podman_instance,
        )

        instance.ps(scheduler="")

        call_kwargs = mock_podman_ps.call_args
        assert call_kwargs.kwargs["filter"] == "name=onetime-scheduler@"

    def test_ps_filters_by_worker(self, mocker):
        """ps --worker should filter to worker containers."""
        mock_config = mocker.Mock()
        mock_config.get_executor.return_value = None
        mocker.patch("rots.commands.instance.app.Config", return_value=mock_config)

        mock_podman_instance = mocker.Mock()
        mock_podman_ps = mocker.Mock()
        mock_podman_instance.ps = mock_podman_ps
        mocker.patch(
            "rots.commands.instance.app.Podman",
            return_value=mock_podman_instance,
        )

        instance.ps(worker="")

        call_kwargs = mock_podman_ps.call_args
        assert call_kwargs.kwargs["filter"] == "name=onetime-worker@"

    def test_ps_uses_table_format(self, mocker):
        """ps should use table format with expected columns."""
        mock_config = mocker.Mock()
        mock_config.get_executor.return_value = None
        mocker.patch("rots.commands.instance.app.Config", return_value=mock_config)

        mock_podman_instance = mocker.Mock()
        mock_podman_ps = mocker.Mock()
        mock_podman_instance.ps = mock_podman_ps
        mocker.patch(
            "rots.commands.instance.app.Podman",
            return_value=mock_podman_instance,
        )

        instance.ps()

        call_kwargs = mock_podman_ps.call_args
        fmt = call_kwargs.kwargs["format"]
        assert "{{.Status}}" in fmt
        assert "{{.Names}}" in fmt
        assert "{{.Image}}" in fmt
