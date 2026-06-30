# tests/test_quadlet.py

"""Tests for quadlet module - Podman quadlet file generation."""

from unittest.mock import MagicMock

import pytest

pytestmark = pytest.mark.quick


class TestContainerTemplate:
    """Test web container quadlet template generation."""

    def test_write_web_template_creates_file(self, mocker, tmp_path):
        """write_web_template should create the container quadlet file."""
        mocker.patch("rots.quadlet.systemd.daemon_reload")
        from rots import quadlet
        from rots.config import Config

        cfg = Config(
            web_template_path=tmp_path / "onetime-web@.container",
            var_dir=tmp_path / "var",
        )

        quadlet.write_web_template(cfg, force=True)

        assert cfg.web_template_path.exists()

    def test_write_web_template_includes_image(self, mocker, tmp_path, monkeypatch):
        """Container quadlet should include Image from config."""
        mocker.patch("rots.quadlet.systemd.daemon_reload")
        monkeypatch.setenv("IMAGE", "myregistry/myimage")
        monkeypatch.setenv("TAG", "v1.0.0")

        from rots import quadlet
        from rots.config import Config

        cfg = Config(
            web_template_path=tmp_path / "onetime-web@.container",
            var_dir=tmp_path / "var",
        )

        quadlet.write_web_template(cfg, force=True)

        content = cfg.web_template_path.read_text()
        assert "Image=myregistry/myimage:v1.0.0" in content

    def test_write_web_template_uses_host_network(self, mocker, tmp_path):
        """Container quadlet should use host networking."""
        mocker.patch("rots.quadlet.systemd.daemon_reload")
        from rots import quadlet
        from rots.config import Config

        cfg = Config(
            web_template_path=tmp_path / "onetime-web@.container",
            var_dir=tmp_path / "var",
        )

        quadlet.write_web_template(cfg, force=True)

        content = cfg.web_template_path.read_text()
        assert "Network=host" in content

    def test_write_web_template_sets_port_env_var(self, mocker, tmp_path):
        """Container quadlet should set PORT env var from instance."""
        mocker.patch("rots.quadlet.systemd.daemon_reload")
        from rots import quadlet
        from rots.config import Config

        cfg = Config(
            web_template_path=tmp_path / "onetime-web@.container",
            var_dir=tmp_path / "var",
        )

        quadlet.write_web_template(cfg, force=True)

        content = cfg.web_template_path.read_text()
        assert "Environment=PORT=%i" in content

    def test_write_web_template_includes_environment_file(self, mocker, tmp_path):
        """Container quadlet should reference shared environment file."""
        mocker.patch("rots.quadlet.systemd.daemon_reload")
        from rots import quadlet
        from rots.config import Config

        cfg = Config(
            web_template_path=tmp_path / "onetime-web@.container",
            var_dir=tmp_path / "var",
        )

        quadlet.write_web_template(cfg, force=True)

        content = cfg.web_template_path.read_text()
        # Uses fixed path for infrastructure config (not per-instance)
        assert "EnvironmentFile=/etc/default/onetimesecret" in content
        # Regression guard: Quadlet [Container] EnvironmentFile= has no "-"
        # optional syntax; a dash is taken as a literal path and fails the
        # unit (podman exit 125). The .local override must never be emitted
        # with a leading dash.
        assert "EnvironmentFile=-" not in content

    def test_write_web_template_includes_syslog_tag(self, mocker, tmp_path):
        """Container quadlet should include syslog tag for unified log filtering."""
        mocker.patch("rots.quadlet.systemd.daemon_reload")
        from rots import quadlet
        from rots.config import Config

        cfg = Config(
            web_template_path=tmp_path / "onetime-web@.container",
            var_dir=tmp_path / "var",
        )

        quadlet.write_web_template(cfg, force=True)

        content = cfg.web_template_path.read_text()
        # Syslog tag allows: journalctl -t onetime-web-7043 -f
        assert "PodmanArgs=--log-opt tag=onetime-web-%i" in content

    def test_write_web_template_includes_volumes(self, mocker, tmp_path):
        """Container quadlet should mount per-file config volumes and static assets."""
        mocker.patch("rots.quadlet.systemd.daemon_reload")
        from rots import quadlet
        from rots.config import Config

        config_dir = tmp_path / "etc"
        config_dir.mkdir()
        (config_dir / "config.yaml").touch()
        (config_dir / "auth.yaml").touch()
        (config_dir / "logging.yaml").touch()

        cfg = Config(
            web_template_path=tmp_path / "onetime-web@.container",
            config_dir=config_dir,
            var_dir=tmp_path / "var",
        )

        quadlet.write_web_template(cfg, force=True)

        content = cfg.web_template_path.read_text()
        # Per-file volume mounts for each config file
        assert f"Volume={config_dir}/config.yaml:/app/etc/config.yaml:ro" in content
        assert f"Volume={config_dir}/auth.yaml:/app/etc/auth.yaml:ro" in content
        assert f"Volume={config_dir}/logging.yaml:/app/etc/logging.yaml:ro" in content
        assert "Volume=static_assets:/app/public:ro" in content

    def test_write_web_template_secrets_section_comment(self, mocker, tmp_path):
        """Container quadlet should include EnvironmentFile layered pattern comment for secrets."""
        mocker.patch("rots.quadlet.systemd.daemon_reload")
        from rots import quadlet
        from rots.config import Config

        cfg = Config(
            web_template_path=tmp_path / "onetime-web@.container",
            var_dir=tmp_path / "var",
        )

        quadlet.write_web_template(cfg, force=True)

        content = cfg.web_template_path.read_text()
        # Secrets are now loaded via EnvironmentFile layered pattern
        assert "Secrets loaded via EnvironmentFile layered pattern" in content

    def test_write_web_template_includes_systemd_dependencies(self, mocker, tmp_path):
        """Container quadlet should have proper systemd dependencies."""
        mocker.patch("rots.quadlet.systemd.daemon_reload")
        from rots import quadlet
        from rots.config import Config

        cfg = Config(
            web_template_path=tmp_path / "onetime-web@.container",
            var_dir=tmp_path / "var",
        )

        quadlet.write_web_template(cfg, force=True)

        content = cfg.web_template_path.read_text()
        assert "After=local-fs.target network-online.target" in content
        assert "Wants=network-online.target" in content
        assert "WantedBy=multi-user.target" in content

    def test_write_web_template_includes_timeout_stop_sec(self, mocker, tmp_path):
        """Web quadlet should have TimeoutStopSec for connection draining."""
        mocker.patch("rots.quadlet.systemd.daemon_reload")
        from rots import quadlet
        from rots.config import Config

        cfg = Config(
            web_template_path=tmp_path / "onetime-web@.container",
            var_dir=tmp_path / "var",
        )

        quadlet.write_web_template(cfg, force=True)

        content = cfg.web_template_path.read_text()
        assert "TimeoutStopSec=30" in content

    def test_write_web_template_valkey_dependency_when_configured(self, mocker, tmp_path):
        """write_web_template should add After= and Wants= for Valkey when configured."""
        mocker.patch("rots.quadlet.systemd.daemon_reload")
        from rots import quadlet
        from rots.config import Config

        cfg = Config(
            web_template_path=tmp_path / "onetime-web@.container",
            var_dir=tmp_path / "var",
        )
        cfg.valkey_service = "valkey-server@6379.service"

        quadlet.write_web_template(cfg, force=True)

        content = cfg.web_template_path.read_text()
        assert "After=local-fs.target network-online.target valkey-server@6379.service" in content
        assert "Wants=valkey-server@6379.service" in content

    def test_write_web_template_no_valkey_dependency_by_default(self, mocker, tmp_path):
        """write_web_template should not add Valkey dependency when OTS_VALKEY_SERVICE is unset."""
        mocker.patch("rots.quadlet.systemd.daemon_reload")
        from rots import quadlet
        from rots.config import Config

        cfg = Config(
            web_template_path=tmp_path / "onetime-web@.container",
            var_dir=tmp_path / "var",
        )
        assert cfg.valkey_service is None

        quadlet.write_web_template(cfg, force=True)

        content = cfg.web_template_path.read_text()
        assert "valkey-server" not in content

    def test_write_web_template_resource_limits_when_configured(self, mocker, tmp_path):
        """write_web_template should include MemoryMax and CPUQuota when set."""
        mocker.patch("rots.quadlet.systemd.daemon_reload")
        from rots import quadlet
        from rots.config import Config

        cfg = Config(
            web_template_path=tmp_path / "onetime-web@.container",
            var_dir=tmp_path / "var",
        )
        cfg.memory_max = "1G"
        cfg.cpu_quota = "80%"

        quadlet.write_web_template(cfg, force=True)

        content = cfg.web_template_path.read_text()
        assert "MemoryMax=1G" in content
        assert "CPUQuota=80%" in content

    def test_write_web_template_no_resource_limits_by_default(self, mocker, tmp_path):
        """write_web_template should not add resource limits when not configured."""
        mocker.patch("rots.quadlet.systemd.daemon_reload")
        from rots import quadlet
        from rots.config import Config

        cfg = Config(
            web_template_path=tmp_path / "onetime-web@.container",
            var_dir=tmp_path / "var",
        )
        assert cfg.memory_max is None
        assert cfg.cpu_quota is None

        quadlet.write_web_template(cfg, force=True)

        content = cfg.web_template_path.read_text()
        assert "MemoryMax=" not in content
        assert "CPUQuota=" not in content

    def test_write_web_template_creates_parent_dirs(self, mocker, tmp_path):
        """write_web_template should create parent directories if needed."""
        mocker.patch("rots.quadlet.systemd.daemon_reload")
        from rots import quadlet
        from rots.config import Config

        nested_path = tmp_path / "subdir" / "onetime-web@.container"
        cfg = Config(
            web_template_path=nested_path,
            var_dir=tmp_path / "var",
        )

        quadlet.write_web_template(cfg, force=True)

        assert nested_path.exists()

    def test_write_web_template_reloads_daemon(self, mocker, tmp_path):
        """write_web_template should reload systemd daemon after writing."""
        mock_reload = mocker.patch("rots.quadlet.systemd.daemon_reload")
        from rots import quadlet
        from rots.config import Config

        cfg = Config(
            web_template_path=tmp_path / "onetime-web@.container",
            var_dir=tmp_path / "var",
        )

        quadlet.write_web_template(cfg, force=True)

        mock_reload.assert_called_once()

    def test_write_web_template_no_config_shows_defaults_comment(self, mocker, tmp_path):
        """Container quadlet should show defaults comment when no config files exist."""
        mocker.patch("rots.quadlet.systemd.daemon_reload")
        from rots import quadlet
        from rots.config import Config

        cfg = Config(
            web_template_path=tmp_path / "onetime-web@.container",
            config_dir=tmp_path / "nonexistent_config",
            var_dir=tmp_path / "var",
        )

        quadlet.write_web_template(cfg, force=True)

        content = cfg.web_template_path.read_text()
        assert "built-in defaults" in content
        # No per-file config Volume lines
        for line in content.splitlines():
            if "Volume=" in line and "/app/etc" in line:
                assert False, f"Unexpected config volume line: {line}"


class TestWorkerTemplate:
    """Test worker container quadlet template generation."""

    def test_write_worker_template_creates_file(self, mocker, tmp_path):
        """write_worker_template should create the worker quadlet file."""
        mocker.patch("rots.quadlet.systemd.daemon_reload")
        from rots import quadlet
        from rots.config import Config

        cfg = Config(
            worker_template_path=tmp_path / "onetime-worker@.container",
            var_dir=tmp_path / "var",
        )

        quadlet.write_worker_template(cfg, force=True)

        assert cfg.worker_template_path.exists()

    def test_write_worker_template_includes_image(self, mocker, tmp_path, monkeypatch):
        """Worker quadlet should include Image from config."""
        mocker.patch("rots.quadlet.systemd.daemon_reload")
        monkeypatch.setenv("IMAGE", "myregistry/myimage")
        monkeypatch.setenv("TAG", "v1.0.0")

        from rots import quadlet
        from rots.config import Config

        cfg = Config(
            worker_template_path=tmp_path / "onetime-worker@.container",
            var_dir=tmp_path / "var",
        )

        quadlet.write_worker_template(cfg, force=True)

        content = cfg.worker_template_path.read_text()
        assert "Image=myregistry/myimage:v1.0.0" in content

    def test_write_worker_template_uses_host_network(self, mocker, tmp_path):
        """Worker quadlet should use host networking."""
        mocker.patch("rots.quadlet.systemd.daemon_reload")
        from rots import quadlet
        from rots.config import Config

        cfg = Config(
            worker_template_path=tmp_path / "onetime-worker@.container",
            var_dir=tmp_path / "var",
        )

        quadlet.write_worker_template(cfg, force=True)

        content = cfg.worker_template_path.read_text()
        assert "Network=host" in content

    def test_write_worker_template_includes_syslog_tag(self, mocker, tmp_path):
        """Worker quadlet should include syslog tag for unified log filtering."""
        mocker.patch("rots.quadlet.systemd.daemon_reload")
        from rots import quadlet
        from rots.config import Config

        cfg = Config(
            worker_template_path=tmp_path / "onetime-worker@.container",
            var_dir=tmp_path / "var",
        )

        quadlet.write_worker_template(cfg, force=True)

        content = cfg.worker_template_path.read_text()
        # Syslog tag allows: journalctl -t onetime-worker-1 -f
        assert "PodmanArgs=--log-opt tag=onetime-worker-%i" in content

    def test_write_worker_template_sets_worker_id_env_var(self, mocker, tmp_path):
        """Worker quadlet should set WORKER_ID env var from instance."""
        mocker.patch("rots.quadlet.systemd.daemon_reload")
        from rots import quadlet
        from rots.config import Config

        cfg = Config(
            worker_template_path=tmp_path / "onetime-worker@.container",
            var_dir=tmp_path / "var",
        )

        quadlet.write_worker_template(cfg, force=True)

        content = cfg.worker_template_path.read_text()
        assert "Environment=WORKER_ID=%i" in content

    def test_write_worker_template_has_worker_exec(self, mocker, tmp_path):
        """Worker quadlet should have worker-specific Exec directive."""
        mocker.patch("rots.quadlet.systemd.daemon_reload")
        from rots import quadlet
        from rots.config import Config

        cfg = Config(
            worker_template_path=tmp_path / "onetime-worker@.container",
            var_dir=tmp_path / "var",
        )

        quadlet.write_worker_template(cfg, force=True)

        content = cfg.worker_template_path.read_text()
        assert "Exec=bin/entrypoint.sh bin/ots worker" in content

    def test_write_worker_template_has_sneakers_health_check(self, mocker, tmp_path):
        """Worker quadlet should check for sneakers process."""
        mocker.patch("rots.quadlet.systemd.daemon_reload")
        from rots import quadlet
        from rots.config import Config

        cfg = Config(
            worker_template_path=tmp_path / "onetime-worker@.container",
            var_dir=tmp_path / "var",
        )

        quadlet.write_worker_template(cfg, force=True)

        content = cfg.worker_template_path.read_text()
        assert 'HealthCmd=pgrep -f "sneakers"' in content

    def test_write_worker_template_has_timeout_stop_sec(self, mocker, tmp_path):
        """Worker quadlet should have TimeoutStopSec for graceful shutdown."""
        mocker.patch("rots.quadlet.systemd.daemon_reload")
        from rots import quadlet
        from rots.config import Config

        cfg = Config(
            worker_template_path=tmp_path / "onetime-worker@.container",
            var_dir=tmp_path / "var",
        )

        quadlet.write_worker_template(cfg, force=True)

        content = cfg.worker_template_path.read_text()
        assert "TimeoutStopSec=90" in content

    def test_write_worker_template_no_static_assets_volume(self, mocker, tmp_path):
        """Worker quadlet should NOT mount static_assets volume."""
        mocker.patch("rots.quadlet.systemd.daemon_reload")
        from rots import quadlet
        from rots.config import Config

        cfg = Config(
            worker_template_path=tmp_path / "onetime-worker@.container",
            var_dir=tmp_path / "var",
        )

        quadlet.write_worker_template(cfg, force=True)

        content = cfg.worker_template_path.read_text()
        assert "static_assets" not in content

    def test_write_worker_template_includes_environment_file(self, mocker, tmp_path):
        """Worker quadlet should reference shared environment file."""
        mocker.patch("rots.quadlet.systemd.daemon_reload")
        from rots import quadlet
        from rots.config import Config

        cfg = Config(
            worker_template_path=tmp_path / "onetime-worker@.container",
            var_dir=tmp_path / "var",
        )

        quadlet.write_worker_template(cfg, force=True)

        content = cfg.worker_template_path.read_text()
        assert "EnvironmentFile=/etc/default/onetimesecret" in content
        # Regression guard: Quadlet [Container] EnvironmentFile= has no "-"
        # optional syntax; a dash is taken as a literal path and fails the
        # unit (podman exit 125). The .local override must never be emitted
        # with a leading dash.
        assert "EnvironmentFile=-" not in content

    def test_write_worker_template_includes_config_volume(self, mocker, tmp_path):
        """Worker quadlet should mount per-file config volumes."""
        mocker.patch("rots.quadlet.systemd.daemon_reload")
        from rots import quadlet
        from rots.config import Config

        config_dir = tmp_path / "etc"
        config_dir.mkdir()
        (config_dir / "config.yaml").touch()
        (config_dir / "auth.yaml").touch()
        (config_dir / "logging.yaml").touch()

        cfg = Config(
            worker_template_path=tmp_path / "onetime-worker@.container",
            config_dir=config_dir,
            var_dir=tmp_path / "var",
        )

        quadlet.write_worker_template(cfg, force=True)

        content = cfg.worker_template_path.read_text()
        assert f"Volume={config_dir}/config.yaml:/app/etc/config.yaml:ro" in content
        assert f"Volume={config_dir}/auth.yaml:/app/etc/auth.yaml:ro" in content
        assert f"Volume={config_dir}/logging.yaml:/app/etc/logging.yaml:ro" in content

    def test_write_worker_template_secrets_section_comment(self, mocker, tmp_path):
        """Worker quadlet should include EnvironmentFile layered pattern comment for secrets."""
        mocker.patch("rots.quadlet.systemd.daemon_reload")
        from rots import quadlet
        from rots.config import Config

        cfg = Config(
            worker_template_path=tmp_path / "onetime-worker@.container",
            var_dir=tmp_path / "var",
        )

        quadlet.write_worker_template(cfg, force=True)

        content = cfg.worker_template_path.read_text()
        assert "Secrets loaded via EnvironmentFile layered pattern" in content

    def test_write_worker_template_reloads_daemon(self, mocker, tmp_path):
        """write_worker_template should reload systemd daemon after writing."""
        mock_reload = mocker.patch("rots.quadlet.systemd.daemon_reload")
        from rots import quadlet
        from rots.config import Config

        cfg = Config(
            worker_template_path=tmp_path / "onetime-worker@.container",
            var_dir=tmp_path / "var",
        )

        quadlet.write_worker_template(cfg, force=True)

        mock_reload.assert_called_once()

    def test_write_worker_template_no_config_shows_defaults_comment(self, mocker, tmp_path):
        """Worker quadlet should show defaults comment when no config files exist."""
        mocker.patch("rots.quadlet.systemd.daemon_reload")
        from rots import quadlet
        from rots.config import Config

        cfg = Config(
            worker_template_path=tmp_path / "onetime-worker@.container",
            config_dir=tmp_path / "nonexistent_config",
            var_dir=tmp_path / "var",
        )

        quadlet.write_worker_template(cfg, force=True)

        content = cfg.worker_template_path.read_text()
        assert "built-in defaults" in content
        for line in content.splitlines():
            if "Volume=" in line and "/app/etc" in line:
                assert False, f"Unexpected config volume line: {line}"


class TestSchedulerTemplate:
    """Test scheduler container quadlet template generation."""

    def test_write_scheduler_template_creates_file(self, mocker, tmp_path):
        """write_scheduler_template should create the scheduler quadlet file."""
        mocker.patch("rots.quadlet.systemd.daemon_reload")
        from rots import quadlet
        from rots.config import Config

        cfg = Config(
            scheduler_template_path=tmp_path / "onetime-scheduler@.container",
            var_dir=tmp_path / "var",
        )

        quadlet.write_scheduler_template(cfg, force=True)

        assert cfg.scheduler_template_path.exists()

    def test_write_scheduler_template_includes_image(self, mocker, tmp_path, monkeypatch):
        """Scheduler quadlet should include Image from config."""
        mocker.patch("rots.quadlet.systemd.daemon_reload")
        monkeypatch.setenv("IMAGE", "myregistry/myimage")
        monkeypatch.setenv("TAG", "v1.0.0")

        from rots import quadlet
        from rots.config import Config

        cfg = Config(
            scheduler_template_path=tmp_path / "onetime-scheduler@.container",
            var_dir=tmp_path / "var",
        )

        quadlet.write_scheduler_template(cfg, force=True)

        content = cfg.scheduler_template_path.read_text()
        assert "Image=myregistry/myimage:v1.0.0" in content

    def test_write_scheduler_template_uses_host_network(self, mocker, tmp_path):
        """Scheduler quadlet should use host networking."""
        mocker.patch("rots.quadlet.systemd.daemon_reload")
        from rots import quadlet
        from rots.config import Config

        cfg = Config(
            scheduler_template_path=tmp_path / "onetime-scheduler@.container",
            var_dir=tmp_path / "var",
        )

        quadlet.write_scheduler_template(cfg, force=True)

        content = cfg.scheduler_template_path.read_text()
        assert "Network=host" in content

    def test_write_scheduler_template_includes_syslog_tag(self, mocker, tmp_path):
        """Scheduler quadlet should include syslog tag for unified log filtering."""
        mocker.patch("rots.quadlet.systemd.daemon_reload")
        from rots import quadlet
        from rots.config import Config

        cfg = Config(
            scheduler_template_path=tmp_path / "onetime-scheduler@.container",
            var_dir=tmp_path / "var",
        )

        quadlet.write_scheduler_template(cfg, force=True)

        content = cfg.scheduler_template_path.read_text()
        # Syslog tag allows: journalctl -t onetime-scheduler-main -f
        assert "PodmanArgs=--log-opt tag=onetime-scheduler-%i" in content

    def test_write_scheduler_template_sets_scheduler_id_env_var(self, mocker, tmp_path):
        """Scheduler quadlet should set SCHEDULER_ID env var from instance."""
        mocker.patch("rots.quadlet.systemd.daemon_reload")
        from rots import quadlet
        from rots.config import Config

        cfg = Config(
            scheduler_template_path=tmp_path / "onetime-scheduler@.container",
            var_dir=tmp_path / "var",
        )

        quadlet.write_scheduler_template(cfg, force=True)

        content = cfg.scheduler_template_path.read_text()
        assert "Environment=SCHEDULER_ID=%i" in content

    def test_write_scheduler_template_has_scheduler_entry_point(self, mocker, tmp_path):
        """Scheduler quadlet should have scheduler-specific entry point."""
        mocker.patch("rots.quadlet.systemd.daemon_reload")
        from rots import quadlet
        from rots.config import Config

        cfg = Config(
            scheduler_template_path=tmp_path / "onetime-scheduler@.container",
            var_dir=tmp_path / "var",
        )

        quadlet.write_scheduler_template(cfg, force=True)

        content = cfg.scheduler_template_path.read_text()
        assert "bin/entrypoint.sh bin/ots scheduler" in content

    def test_write_scheduler_template_has_scheduler_health_check(self, mocker, tmp_path):
        """Scheduler quadlet should check for scheduler process."""
        mocker.patch("rots.quadlet.systemd.daemon_reload")
        from rots import quadlet
        from rots.config import Config

        cfg = Config(
            scheduler_template_path=tmp_path / "onetime-scheduler@.container",
            var_dir=tmp_path / "var",
        )

        quadlet.write_scheduler_template(cfg, force=True)

        content = cfg.scheduler_template_path.read_text()
        assert 'pgrep -f "bin/ots scheduler"' in content

    def test_write_scheduler_template_has_timeout_stop_sec(self, mocker, tmp_path):
        """Scheduler quadlet should have TimeoutStopSec for graceful job completion."""
        mocker.patch("rots.quadlet.systemd.daemon_reload")
        from rots import quadlet
        from rots.config import Config

        cfg = Config(
            scheduler_template_path=tmp_path / "onetime-scheduler@.container",
            var_dir=tmp_path / "var",
        )

        quadlet.write_scheduler_template(cfg, force=True)

        content = cfg.scheduler_template_path.read_text()
        assert "TimeoutStopSec=60" in content

    def test_write_scheduler_template_no_static_assets_volume(self, mocker, tmp_path):
        """Scheduler quadlet should NOT mount static_assets volume."""
        mocker.patch("rots.quadlet.systemd.daemon_reload")
        from rots import quadlet
        from rots.config import Config

        cfg = Config(
            scheduler_template_path=tmp_path / "onetime-scheduler@.container",
            var_dir=tmp_path / "var",
        )

        quadlet.write_scheduler_template(cfg, force=True)

        content = cfg.scheduler_template_path.read_text()
        assert "static_assets" not in content

    def test_write_scheduler_template_no_port_env_var(self, mocker, tmp_path):
        """Scheduler quadlet should NOT have PORT env var (unlike web instances)."""
        mocker.patch("rots.quadlet.systemd.daemon_reload")
        from rots import quadlet
        from rots.config import Config

        cfg = Config(
            scheduler_template_path=tmp_path / "onetime-scheduler@.container",
            var_dir=tmp_path / "var",
        )

        quadlet.write_scheduler_template(cfg, force=True)

        content = cfg.scheduler_template_path.read_text()
        assert "Environment=PORT=" not in content

    def test_write_scheduler_template_includes_environment_file(self, mocker, tmp_path):
        """Scheduler quadlet should reference shared environment file."""
        mocker.patch("rots.quadlet.systemd.daemon_reload")
        from rots import quadlet
        from rots.config import Config

        cfg = Config(
            scheduler_template_path=tmp_path / "onetime-scheduler@.container",
            var_dir=tmp_path / "var",
        )

        quadlet.write_scheduler_template(cfg, force=True)

        content = cfg.scheduler_template_path.read_text()
        assert "EnvironmentFile=/etc/default/onetimesecret" in content
        # Regression guard: Quadlet [Container] EnvironmentFile= has no "-"
        # optional syntax; a dash is taken as a literal path and fails the
        # unit (podman exit 125). The .local override must never be emitted
        # with a leading dash.
        assert "EnvironmentFile=-" not in content

    def test_write_scheduler_template_includes_config_volume(self, mocker, tmp_path):
        """Scheduler quadlet should mount per-file config volumes."""
        mocker.patch("rots.quadlet.systemd.daemon_reload")
        from rots import quadlet
        from rots.config import Config

        config_dir = tmp_path / "etc"
        config_dir.mkdir()
        (config_dir / "config.yaml").touch()
        (config_dir / "auth.yaml").touch()
        (config_dir / "logging.yaml").touch()

        cfg = Config(
            scheduler_template_path=tmp_path / "onetime-scheduler@.container",
            config_dir=config_dir,
            var_dir=tmp_path / "var",
        )

        quadlet.write_scheduler_template(cfg, force=True)

        content = cfg.scheduler_template_path.read_text()
        assert f"Volume={config_dir}/config.yaml:/app/etc/config.yaml:ro" in content
        assert f"Volume={config_dir}/auth.yaml:/app/etc/auth.yaml:ro" in content
        assert f"Volume={config_dir}/logging.yaml:/app/etc/logging.yaml:ro" in content

    def test_write_scheduler_template_secrets_section_comment(self, mocker, tmp_path):
        """Scheduler quadlet should include EnvironmentFile layered pattern comment for secrets."""
        mocker.patch("rots.quadlet.systemd.daemon_reload")
        from rots import quadlet
        from rots.config import Config

        cfg = Config(
            scheduler_template_path=tmp_path / "onetime-scheduler@.container",
            var_dir=tmp_path / "var",
        )

        quadlet.write_scheduler_template(cfg, force=True)

        content = cfg.scheduler_template_path.read_text()
        assert "Secrets loaded via EnvironmentFile layered pattern" in content

    def test_write_scheduler_template_creates_parent_dirs(self, mocker, tmp_path):
        """write_scheduler_template should create parent directories if needed."""
        mocker.patch("rots.quadlet.systemd.daemon_reload")
        from rots import quadlet
        from rots.config import Config

        nested_path = tmp_path / "subdir" / "onetime-scheduler@.container"
        cfg = Config(
            scheduler_template_path=nested_path,
            var_dir=tmp_path / "var",
        )

        quadlet.write_scheduler_template(cfg, force=True)

        assert nested_path.exists()

    def test_write_scheduler_template_reloads_daemon(self, mocker, tmp_path):
        """write_scheduler_template should reload systemd daemon after writing."""
        mock_reload = mocker.patch("rots.quadlet.systemd.daemon_reload")
        from rots import quadlet
        from rots.config import Config

        cfg = Config(
            scheduler_template_path=tmp_path / "onetime-scheduler@.container",
            var_dir=tmp_path / "var",
        )

        quadlet.write_scheduler_template(cfg, force=True)

        mock_reload.assert_called_once()

    def test_write_scheduler_template_no_config_shows_defaults_comment(self, mocker, tmp_path):
        """Scheduler quadlet should show defaults comment when no config files exist."""
        mocker.patch("rots.quadlet.systemd.daemon_reload")
        from rots import quadlet
        from rots.config import Config

        cfg = Config(
            scheduler_template_path=tmp_path / "onetime-scheduler@.container",
            config_dir=tmp_path / "nonexistent_config",
            var_dir=tmp_path / "var",
        )

        quadlet.write_scheduler_template(cfg, force=True)

        content = cfg.scheduler_template_path.read_text()
        assert "built-in defaults" in content
        for line in content.splitlines():
            if "Volume=" in line and "/app/etc" in line:
                assert False, f"Unexpected config volume line: {line}"


class TestGetConfigVolumesSection:
    """Test get_config_volumes_section function."""

    def test_no_files_returns_defaults_comment(self, tmp_path):
        """Should return defaults comment when no config files exist."""
        from rots.config import Config
        from rots.quadlet import get_config_volumes_section

        cfg = Config(
            config_dir=tmp_path / "nonexistent_dir",
            var_dir=tmp_path / "var",
        )

        result = get_config_volumes_section(cfg)
        assert "built-in defaults" in result
        assert "Volume=" not in result

    def test_all_files_returns_volume_lines(self, tmp_path):
        """Should return Volume lines for all 3 config files when present."""
        from rots.config import Config
        from rots.quadlet import get_config_volumes_section

        config_dir = tmp_path / "etc"
        config_dir.mkdir()
        (config_dir / "config.yaml").touch()
        (config_dir / "auth.yaml").touch()
        (config_dir / "logging.yaml").touch()

        cfg = Config(
            config_dir=config_dir,
            var_dir=tmp_path / "var",
        )

        result = get_config_volumes_section(cfg)
        assert f"Volume={config_dir}/config.yaml:/app/etc/config.yaml:ro" in result
        assert f"Volume={config_dir}/auth.yaml:/app/etc/auth.yaml:ro" in result
        assert f"Volume={config_dir}/logging.yaml:/app/etc/logging.yaml:ro" in result

    def test_subset_of_files(self, tmp_path):
        """Should return Volume lines only for files that exist."""
        from rots.config import Config
        from rots.quadlet import get_config_volumes_section

        config_dir = tmp_path / "etc"
        config_dir.mkdir()
        (config_dir / "config.yaml").touch()
        (config_dir / "auth.yaml").touch()
        # logging.yaml intentionally not created

        cfg = Config(
            config_dir=config_dir,
            var_dir=tmp_path / "var",
        )

        result = get_config_volumes_section(cfg)
        assert f"Volume={config_dir}/config.yaml:/app/etc/config.yaml:ro" in result
        assert f"Volume={config_dir}/auth.yaml:/app/etc/auth.yaml:ro" in result
        assert "logging.yaml" not in result


class TestGetEnvFilesSection:
    """Tests for get_env_files_section() -- the [Container] EnvironmentFile= lines.

    Regression coverage for the podman exit-125 bug: Quadlet [Container]
    EnvironmentFile= does not honor systemd's "-" optional prefix, so the
    optional .local override must be emitted only when it exists, never with a
    leading dash.
    """

    def test_baseline_always_emitted_no_dash(self, tmp_path, mocker):
        """Baseline is always present; no entry ever carries a leading dash."""
        from rots import quadlet
        from rots.config import Config

        # Force the override to be absent regardless of the host machine.
        mocker.patch.object(quadlet, "LOCAL_ENV_FILE", tmp_path / "absent.local")

        result = quadlet.get_env_files_section(Config())
        assert "EnvironmentFile=/etc/default/onetimesecret" in result
        assert "EnvironmentFile=-" not in result
        assert "onetimesecret.local" not in result

    def test_override_emitted_without_dash_when_present(self, tmp_path, mocker):
        """When .local exists on the host, it is added as a plain (dash-free) entry."""
        from rots import quadlet
        from rots.config import Config

        local = tmp_path / "onetimesecret.local"
        local.write_text("FOO=bar\n")
        mocker.patch.object(quadlet, "LOCAL_ENV_FILE", local)

        result = quadlet.get_env_files_section(Config())
        assert "EnvironmentFile=/etc/default/onetimesecret" in result
        assert f"EnvironmentFile={local}" in result
        assert "EnvironmentFile=-" not in result

    def test_render_mode_emits_baseline_only(self, tmp_path, mocker):
        """Render mode (no host I/O) emits baseline only, never probing the override."""
        from rots import quadlet
        from rots.config import Config

        # Even if a like-named file exists locally, render mode must not probe it.
        local = tmp_path / "onetimesecret.local"
        local.write_text("FOO=bar\n")
        mocker.patch.object(quadlet, "LOCAL_ENV_FILE", local)

        result = quadlet.get_env_files_section(Config(), render_mode=True)
        assert "EnvironmentFile=/etc/default/onetimesecret" in result
        assert f"EnvironmentFile={local}" not in result
        assert "EnvironmentFile=-" not in result


class TestGetSecretsSection:
    """Test get_secrets_section returns EnvironmentFile layered pattern comment."""

    def test_returns_drop_in_comment(self):
        """get_secrets_section should return EnvironmentFile layered pattern comment."""
        from rots.quadlet import get_secrets_section

        result = get_secrets_section()
        assert "Secrets loaded via EnvironmentFile layered pattern" in result


class TestGetResourceLimitsSection:
    """Tests for get_resource_limits_section()."""

    def test_both_limits_set(self):
        """Should include MemoryMax and CPUQuota when both config fields are set."""
        from rots import quadlet
        from rots.config import Config

        cfg = Config(memory_max="1G", cpu_quota="80%")
        result = quadlet.get_resource_limits_section(cfg)
        assert "MemoryMax=1G" in result
        assert "CPUQuota=80%" in result

    def test_only_memory_max_set(self):
        """Should include only MemoryMax when cpu_quota is None."""
        from rots import quadlet
        from rots.config import Config

        cfg = Config(memory_max="512M", cpu_quota=None)
        result = quadlet.get_resource_limits_section(cfg)
        assert "MemoryMax=512M" in result
        assert "CPUQuota" not in result

    def test_only_cpu_quota_set(self):
        """Should include only CPUQuota when memory_max is None."""
        from rots import quadlet
        from rots.config import Config

        cfg = Config(memory_max=None, cpu_quota="50%")
        result = quadlet.get_resource_limits_section(cfg)
        assert "CPUQuota=50%" in result
        assert "MemoryMax" not in result

    def test_both_none_returns_empty_string(self):
        """Should return empty string when neither limit is configured."""
        from rots import quadlet
        from rots.config import Config

        cfg = Config(memory_max=None, cpu_quota=None)
        result = quadlet.get_resource_limits_section(cfg)
        assert result == ""

    def test_rejects_newline_memory_max(self):
        """Should reject MEMORY_MAX with newline injection (defense-in-depth)."""
        from unittest.mock import MagicMock

        from rots import quadlet

        # Use MagicMock to bypass Config.__post_init__ validation and test
        # the defense-in-depth layer in get_resource_limits_section() directly.
        cfg = MagicMock()
        cfg.memory_max = "1G\nExecStart=/bin/sh"
        cfg.cpu_quota = None
        with pytest.raises(ValueError, match="Invalid MEMORY_MAX"):
            quadlet.get_resource_limits_section(cfg)

    def test_rejects_newline_cpu_quota(self):
        """Should reject CPU_QUOTA with newline injection (defense-in-depth)."""
        from unittest.mock import MagicMock

        from rots import quadlet

        cfg = MagicMock()
        cfg.memory_max = None
        cfg.cpu_quota = "80%\nExecStart=/bin/sh"
        with pytest.raises(ValueError, match="Invalid CPU_QUOTA"):
            quadlet.get_resource_limits_section(cfg)

    def test_rejects_shell_metacharacters_memory_max(self):
        """Should reject MEMORY_MAX with shell metacharacters (defense-in-depth)."""
        from unittest.mock import MagicMock

        from rots import quadlet

        cfg = MagicMock()
        cfg.memory_max = "1G; rm -rf /"
        cfg.cpu_quota = None
        with pytest.raises(ValueError, match="Invalid MEMORY_MAX"):
            quadlet.get_resource_limits_section(cfg)

    def test_rejects_shell_metacharacters_cpu_quota(self):
        """Should reject CPU_QUOTA with shell metacharacters (defense-in-depth)."""
        from unittest.mock import MagicMock

        from rots import quadlet

        cfg = MagicMock()
        cfg.memory_max = None
        cfg.cpu_quota = "$(whoami)%"
        with pytest.raises(ValueError, match="Invalid CPU_QUOTA"):
            quadlet.get_resource_limits_section(cfg)

    def test_rejects_command_substitution_memory_max(self):
        """Should reject MEMORY_MAX with command substitution (defense-in-depth)."""
        from unittest.mock import MagicMock

        from rots import quadlet

        cfg = MagicMock()
        cfg.memory_max = "$(whoami)"
        cfg.cpu_quota = None
        with pytest.raises(ValueError, match="Invalid MEMORY_MAX"):
            quadlet.get_resource_limits_section(cfg)

    def test_write_web_template_includes_resource_limits(self, mocker, tmp_path, monkeypatch):
        """Written web quadlet should include MemoryMax= when memory_max is configured."""
        mocker.patch("rots.quadlet.systemd.daemon_reload")
        monkeypatch.delenv("MEMORY_MAX", raising=False)
        monkeypatch.delenv("CPU_QUOTA", raising=False)
        from rots import quadlet
        from rots.config import Config

        cfg = Config(
            web_template_path=tmp_path / "onetime-web@.container",
            var_dir=tmp_path / "var",
            memory_max="2G",
            cpu_quota="75%",
        )

        quadlet.write_web_template(cfg, force=True)

        content = cfg.web_template_path.read_text()
        assert "MemoryMax=2G" in content
        assert "CPUQuota=75%" in content

    def test_write_web_template_no_resource_limits_section_when_unset(
        self, mocker, tmp_path, monkeypatch
    ):
        """Written web quadlet should not contain MemoryMax/CPUQuota when not configured."""
        mocker.patch("rots.quadlet.systemd.daemon_reload")
        monkeypatch.delenv("MEMORY_MAX", raising=False)
        monkeypatch.delenv("CPU_QUOTA", raising=False)
        from rots import quadlet
        from rots.config import Config

        cfg = Config(
            web_template_path=tmp_path / "onetime-web@.container",
            var_dir=tmp_path / "var",
            memory_max=None,
            cpu_quota=None,
        )

        quadlet.write_web_template(cfg, force=True)

        content = cfg.web_template_path.read_text()
        assert "MemoryMax" not in content
        assert "CPUQuota" not in content


# =============================================================================
# Remote executor tests
# =============================================================================


def _make_ssh_executor(mocker):
    """Create a mock SSHExecutor that _is_remote() recognises as remote."""
    mock_ex = mocker.MagicMock()
    mocker.patch(
        "rots.quadlet._is_remote",
        side_effect=lambda ex: ex is mock_ex,
    )
    return mock_ex


def _make_remote_result(stdout="", returncode=0):
    result = MagicMock()
    result.ok = returncode == 0
    result.stdout = stdout
    result.stderr = ""
    result.returncode = returncode
    return result


class TestWriteTemplateRemote:
    """Test _write_template() with remote executor."""

    def test_writes_via_executor_mkdir_tee_and_daemon_reload(self, mocker, tmp_path):
        from rots import quadlet

        mock_ex = _make_ssh_executor(mocker)
        mock_ex.run.return_value = _make_remote_result()

        # Mock get_secrets_section and get_config_volumes_section to
        # avoid their own remote calls (tested separately)
        mocker.patch(
            "rots.quadlet.get_secrets_section",
            return_value="# no secrets",
        )
        mocker.patch(
            "rots.quadlet.get_config_volumes_section",
            return_value="# no config",
        )
        mock_reload = mocker.patch("rots.quadlet.systemd.daemon_reload")

        cfg = MagicMock()
        cfg.resolved_image_with_tag.return_value = "ghcr.io/ots:latest"
        cfg.config_dir = "/etc/onetimesecret"
        cfg.memory_max = None
        cfg.cpu_quota = None
        cfg.registry = "ghcr.io"
        cfg.web_template_path = tmp_path / "onetime-web@.container"

        path = tmp_path / "onetime-web@.container"
        quadlet._write_template("Image={image}\n", path, cfg, None, force=True, executor=mock_ex)

        # Should call mkdir -p and tee
        calls = [c[0][0] for c in mock_ex.run.call_args_list]
        assert ["mkdir", "-p", str(tmp_path)] in calls
        tee_call = [c for c in mock_ex.run.call_args_list if c[0][0][0] == "tee"]
        assert len(tee_call) == 1
        assert tee_call[0][0][0] == ["tee", str(path)]
        # With registry set, image resolves to onetime.image (companion .image unit)
        assert "Image=onetime.image" in tee_call[0][1]["input"]

        # Should NOT write to local filesystem
        assert not path.exists()

        # Should call daemon_reload with executor
        mock_reload.assert_called_once_with(executor=mock_ex)


class TestGetConfigVolumesSectionRemote:
    """Test get_config_volumes_section() with remote executor."""

    def test_delegates_to_config_get_existing_config_files(self, mocker):
        from pathlib import Path

        from rots.quadlet import get_config_volumes_section

        mock_ex = _make_ssh_executor(mocker)

        cfg = MagicMock()
        cfg.config_dir = Path("/etc/onetimesecret")
        # Simulate only config.yaml existing on remote host
        cfg.get_existing_config_files.return_value = [
            Path("/etc/onetimesecret/config.yaml"),
        ]

        result = get_config_volumes_section(cfg, executor=mock_ex)

        assert "Volume=/etc/onetimesecret/config.yaml:/app/etc/config.yaml:ro" in result
        assert "auth.yaml" not in result
        cfg.get_existing_config_files.assert_called_once_with(executor=mock_ex)


class TestValkeyDependenciesDefenseInDepth:
    """Defense-in-depth: _get_valkey_unit_dependencies re-validates valkey_service."""

    @pytest.mark.parametrize(
        "bad_service",
        [
            "valkey\nExecStart=/evil",
            "valkey;rm -rf /",
            "valkey$(whoami).service",
            " leading-space.service",
        ],
    )
    def test_rejects_injection(self, bad_service):
        from rots.config import Config
        from rots.quadlet import _get_valkey_unit_dependencies

        cfg = MagicMock(spec=Config)
        cfg.valkey_service = bad_service
        with pytest.raises(ValueError, match="Invalid valkey_service"):
            _get_valkey_unit_dependencies(cfg)

    def test_accepts_valid_service(self):
        from rots.config import Config
        from rots.quadlet import _get_valkey_unit_dependencies

        cfg = MagicMock(spec=Config)
        cfg.valkey_service = "valkey-server@6379.service"
        after, wants = _get_valkey_unit_dependencies(cfg)
        assert "valkey-server@6379.service" in after
        assert "valkey-server@6379.service" in wants
