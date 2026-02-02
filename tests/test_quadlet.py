# tests/test_quadlet.py
"""Tests for quadlet module - Podman quadlet file generation."""


class TestContainerTemplate:
    """Test web container quadlet template generation."""

    def test_write_web_template_creates_file(self, mocker, tmp_path):
        """write_web_template should create the container quadlet file."""
        mocker.patch("ots_containers.quadlet.systemd.daemon_reload")
        from ots_containers import quadlet
        from ots_containers.config import Config

        cfg = Config(
            web_template_path=tmp_path / "onetime-web@.container",
            var_dir=tmp_path / "var",
        )

        quadlet.write_web_template(cfg)

        assert cfg.web_template_path.exists()

    def test_write_web_template_includes_image(self, mocker, tmp_path, monkeypatch):
        """Container quadlet should include Image from config."""
        mocker.patch("ots_containers.quadlet.systemd.daemon_reload")
        monkeypatch.setenv("IMAGE", "myregistry/myimage")
        monkeypatch.setenv("TAG", "v1.0.0")

        from ots_containers import quadlet
        from ots_containers.config import Config

        cfg = Config(
            web_template_path=tmp_path / "onetime-web@.container",
            var_dir=tmp_path / "var",
        )

        quadlet.write_web_template(cfg)

        content = cfg.web_template_path.read_text()
        assert "Image=myregistry/myimage:v1.0.0" in content

    def test_write_web_template_uses_host_network(self, mocker, tmp_path):
        """Container quadlet should use host networking."""
        mocker.patch("ots_containers.quadlet.systemd.daemon_reload")
        from ots_containers import quadlet
        from ots_containers.config import Config

        cfg = Config(
            web_template_path=tmp_path / "onetime-web@.container",
            var_dir=tmp_path / "var",
        )

        quadlet.write_web_template(cfg)

        content = cfg.web_template_path.read_text()
        assert "Network=host" in content

    def test_write_web_template_sets_port_env_var(self, mocker, tmp_path):
        """Container quadlet should set PORT env var from instance."""
        mocker.patch("ots_containers.quadlet.systemd.daemon_reload")
        from ots_containers import quadlet
        from ots_containers.config import Config

        cfg = Config(
            web_template_path=tmp_path / "onetime-web@.container",
            var_dir=tmp_path / "var",
        )

        quadlet.write_web_template(cfg)

        content = cfg.web_template_path.read_text()
        assert "Environment=PORT=%i" in content

    def test_write_web_template_includes_environment_file(self, mocker, tmp_path):
        """Container quadlet should reference shared environment file."""
        mocker.patch("ots_containers.quadlet.systemd.daemon_reload")
        from ots_containers import quadlet
        from ots_containers.config import Config

        cfg = Config(
            web_template_path=tmp_path / "onetime-web@.container",
            var_dir=tmp_path / "var",
        )

        quadlet.write_web_template(cfg)

        content = cfg.web_template_path.read_text()
        # Uses fixed path for infrastructure config (not per-instance)
        assert "EnvironmentFile=/etc/default/onetimesecret" in content

    def test_write_web_template_includes_syslog_tag(self, mocker, tmp_path):
        """Container quadlet should include syslog tag for unified log filtering."""
        mocker.patch("ots_containers.quadlet.systemd.daemon_reload")
        from ots_containers import quadlet
        from ots_containers.config import Config

        cfg = Config(
            web_template_path=tmp_path / "onetime-web@.container",
            var_dir=tmp_path / "var",
        )

        quadlet.write_web_template(cfg)

        content = cfg.web_template_path.read_text()
        # Syslog tag allows: journalctl -t onetime-web-7043 -f
        assert "PodmanArgs=--log-opt tag=onetime-web-%i" in content

    def test_write_web_template_includes_volumes(self, mocker, tmp_path):
        """Container quadlet should mount config directory and static assets."""
        mocker.patch("ots_containers.quadlet.systemd.daemon_reload")
        from ots_containers import quadlet
        from ots_containers.config import Config

        config_dir = tmp_path / "etc"
        cfg = Config(
            web_template_path=tmp_path / "onetime-web@.container",
            config_dir=config_dir,
            var_dir=tmp_path / "var",
        )

        quadlet.write_web_template(cfg)

        content = cfg.web_template_path.read_text()
        # Mounts entire config directory (not just config.yaml)
        assert f"Volume={config_dir}:/app/etc:ro" in content
        assert "Volume=static_assets:/app/public:ro" in content

    def test_write_web_template_includes_podman_secrets_from_env_file(self, mocker, tmp_path):
        """Container quadlet should include Secret= directives from env file."""
        mocker.patch("ots_containers.quadlet.systemd.daemon_reload")
        from ots_containers import quadlet
        from ots_containers.config import Config

        # Create an env file with SECRET_VARIABLE_NAMES
        env_file = tmp_path / "onetimesecret.env"
        env_file.write_text(
            "SECRET_VARIABLE_NAMES=API_KEY,DB_PASSWORD\n"
            "_API_KEY=ots_api_key\n"
            "_DB_PASSWORD=ots_db_password\n"
        )

        cfg = Config(
            web_template_path=tmp_path / "onetime-web@.container",
            var_dir=tmp_path / "var",
        )

        quadlet.write_web_template(cfg, env_file_path=env_file)

        content = cfg.web_template_path.read_text()
        # Secrets generated from env file's SECRET_VARIABLE_NAMES
        assert "Secret=ots_api_key,type=env,target=API_KEY" in content
        assert "Secret=ots_db_password,type=env,target=DB_PASSWORD" in content

    def test_write_web_template_no_env_file_shows_comment(self, mocker, tmp_path):
        """Container quadlet should show comment when no env file exists."""
        mocker.patch("ots_containers.quadlet.systemd.daemon_reload")
        from ots_containers import quadlet
        from ots_containers.config import Config

        cfg = Config(
            web_template_path=tmp_path / "onetime-web@.container",
            var_dir=tmp_path / "var",
        )

        # Pass a non-existent env file path
        quadlet.write_web_template(cfg, env_file_path=tmp_path / "nonexistent.env")

        content = cfg.web_template_path.read_text()
        assert "No secrets configured" in content

    def test_write_web_template_no_secret_names_shows_comment(self, mocker, tmp_path):
        """Container quadlet should show comment when no SECRET_VARIABLE_NAMES."""
        mocker.patch("ots_containers.quadlet.systemd.daemon_reload")
        from ots_containers import quadlet
        from ots_containers.config import Config

        # Create an env file without SECRET_VARIABLE_NAMES
        env_file = tmp_path / "onetimesecret.env"
        env_file.write_text("REDIS_URL=redis://localhost\n")

        cfg = Config(
            web_template_path=tmp_path / "onetime-web@.container",
            var_dir=tmp_path / "var",
        )

        quadlet.write_web_template(cfg, env_file_path=env_file)

        content = cfg.web_template_path.read_text()
        assert "No secrets configured" in content

    def test_write_web_template_includes_systemd_dependencies(self, mocker, tmp_path):
        """Container quadlet should have proper systemd dependencies."""
        mocker.patch("ots_containers.quadlet.systemd.daemon_reload")
        from ots_containers import quadlet
        from ots_containers.config import Config

        cfg = Config(
            web_template_path=tmp_path / "onetime-web@.container",
            var_dir=tmp_path / "var",
        )

        quadlet.write_web_template(cfg)

        content = cfg.web_template_path.read_text()
        assert "After=local-fs.target network-online.target" in content
        assert "Wants=network-online.target" in content
        assert "WantedBy=multi-user.target" in content

    def test_write_web_template_creates_parent_dirs(self, mocker, tmp_path):
        """write_web_template should create parent directories if needed."""
        mocker.patch("ots_containers.quadlet.systemd.daemon_reload")
        from ots_containers import quadlet
        from ots_containers.config import Config

        nested_path = tmp_path / "subdir" / "onetime-web@.container"
        cfg = Config(
            web_template_path=nested_path,
            var_dir=tmp_path / "var",
        )

        quadlet.write_web_template(cfg)

        assert nested_path.exists()

    def test_write_web_template_reloads_daemon(self, mocker, tmp_path):
        """write_web_template should reload systemd daemon after writing."""
        mock_reload = mocker.patch("ots_containers.quadlet.systemd.daemon_reload")
        from ots_containers import quadlet
        from ots_containers.config import Config

        cfg = Config(
            web_template_path=tmp_path / "onetime-web@.container",
            var_dir=tmp_path / "var",
        )

        quadlet.write_web_template(cfg)

        mock_reload.assert_called_once()


class TestWorkerTemplate:
    """Test worker container quadlet template generation."""

    def test_write_worker_template_creates_file(self, mocker, tmp_path):
        """write_worker_template should create the worker quadlet file."""
        mocker.patch("ots_containers.quadlet.systemd.daemon_reload")
        from ots_containers import quadlet
        from ots_containers.config import Config

        cfg = Config(
            worker_template_path=tmp_path / "onetime-worker@.container",
            var_dir=tmp_path / "var",
        )

        quadlet.write_worker_template(cfg)

        assert cfg.worker_template_path.exists()

    def test_write_worker_template_includes_image(self, mocker, tmp_path, monkeypatch):
        """Worker quadlet should include Image from config."""
        mocker.patch("ots_containers.quadlet.systemd.daemon_reload")
        monkeypatch.setenv("IMAGE", "myregistry/myimage")
        monkeypatch.setenv("TAG", "v1.0.0")

        from ots_containers import quadlet
        from ots_containers.config import Config

        cfg = Config(
            worker_template_path=tmp_path / "onetime-worker@.container",
            var_dir=tmp_path / "var",
        )

        quadlet.write_worker_template(cfg)

        content = cfg.worker_template_path.read_text()
        assert "Image=myregistry/myimage:v1.0.0" in content

    def test_write_worker_template_uses_host_network(self, mocker, tmp_path):
        """Worker quadlet should use host networking."""
        mocker.patch("ots_containers.quadlet.systemd.daemon_reload")
        from ots_containers import quadlet
        from ots_containers.config import Config

        cfg = Config(
            worker_template_path=tmp_path / "onetime-worker@.container",
            var_dir=tmp_path / "var",
        )

        quadlet.write_worker_template(cfg)

        content = cfg.worker_template_path.read_text()
        assert "Network=host" in content

    def test_write_worker_template_includes_syslog_tag(self, mocker, tmp_path):
        """Worker quadlet should include syslog tag for unified log filtering."""
        mocker.patch("ots_containers.quadlet.systemd.daemon_reload")
        from ots_containers import quadlet
        from ots_containers.config import Config

        cfg = Config(
            worker_template_path=tmp_path / "onetime-worker@.container",
            var_dir=tmp_path / "var",
        )

        quadlet.write_worker_template(cfg)

        content = cfg.worker_template_path.read_text()
        # Syslog tag allows: journalctl -t onetime-worker-1 -f
        assert "PodmanArgs=--log-opt tag=onetime-worker-%i" in content

    def test_write_worker_template_sets_worker_id_env_var(self, mocker, tmp_path):
        """Worker quadlet should set WORKER_ID env var from instance."""
        mocker.patch("ots_containers.quadlet.systemd.daemon_reload")
        from ots_containers import quadlet
        from ots_containers.config import Config

        cfg = Config(
            worker_template_path=tmp_path / "onetime-worker@.container",
            var_dir=tmp_path / "var",
        )

        quadlet.write_worker_template(cfg)

        content = cfg.worker_template_path.read_text()
        assert "Environment=WORKER_ID=%i" in content

    def test_write_worker_template_has_worker_exec(self, mocker, tmp_path):
        """Worker quadlet should have worker-specific Exec directive."""
        mocker.patch("ots_containers.quadlet.systemd.daemon_reload")
        from ots_containers import quadlet
        from ots_containers.config import Config

        cfg = Config(
            worker_template_path=tmp_path / "onetime-worker@.container",
            var_dir=tmp_path / "var",
        )

        quadlet.write_worker_template(cfg)

        content = cfg.worker_template_path.read_text()
        assert "Exec=bin/entrypoint.sh bin/ots worker" in content

    def test_write_worker_template_has_sneakers_health_check(self, mocker, tmp_path):
        """Worker quadlet should check for sneakers process."""
        mocker.patch("ots_containers.quadlet.systemd.daemon_reload")
        from ots_containers import quadlet
        from ots_containers.config import Config

        cfg = Config(
            worker_template_path=tmp_path / "onetime-worker@.container",
            var_dir=tmp_path / "var",
        )

        quadlet.write_worker_template(cfg)

        content = cfg.worker_template_path.read_text()
        assert 'HealthCmd=pgrep -f "sneakers"' in content

    def test_write_worker_template_has_timeout_stop_sec(self, mocker, tmp_path):
        """Worker quadlet should have TimeoutStopSec for graceful shutdown."""
        mocker.patch("ots_containers.quadlet.systemd.daemon_reload")
        from ots_containers import quadlet
        from ots_containers.config import Config

        cfg = Config(
            worker_template_path=tmp_path / "onetime-worker@.container",
            var_dir=tmp_path / "var",
        )

        quadlet.write_worker_template(cfg)

        content = cfg.worker_template_path.read_text()
        assert "TimeoutStopSec=90" in content

    def test_write_worker_template_no_static_assets_volume(self, mocker, tmp_path):
        """Worker quadlet should NOT mount static_assets volume."""
        mocker.patch("ots_containers.quadlet.systemd.daemon_reload")
        from ots_containers import quadlet
        from ots_containers.config import Config

        cfg = Config(
            worker_template_path=tmp_path / "onetime-worker@.container",
            var_dir=tmp_path / "var",
        )

        quadlet.write_worker_template(cfg)

        content = cfg.worker_template_path.read_text()
        assert "static_assets" not in content

    def test_write_worker_template_includes_environment_file(self, mocker, tmp_path):
        """Worker quadlet should reference shared environment file."""
        mocker.patch("ots_containers.quadlet.systemd.daemon_reload")
        from ots_containers import quadlet
        from ots_containers.config import Config

        cfg = Config(
            worker_template_path=tmp_path / "onetime-worker@.container",
            var_dir=tmp_path / "var",
        )

        quadlet.write_worker_template(cfg)

        content = cfg.worker_template_path.read_text()
        assert "EnvironmentFile=/etc/default/onetimesecret" in content

    def test_write_worker_template_includes_config_volume(self, mocker, tmp_path):
        """Worker quadlet should mount config directory."""
        mocker.patch("ots_containers.quadlet.systemd.daemon_reload")
        from ots_containers import quadlet
        from ots_containers.config import Config

        config_dir = tmp_path / "etc"
        cfg = Config(
            worker_template_path=tmp_path / "onetime-worker@.container",
            config_dir=config_dir,
            var_dir=tmp_path / "var",
        )

        quadlet.write_worker_template(cfg)

        content = cfg.worker_template_path.read_text()
        assert f"Volume={config_dir}:/app/etc:ro" in content

    def test_write_worker_template_includes_secrets(self, mocker, tmp_path):
        """Worker quadlet should include Secret= directives from env file."""
        mocker.patch("ots_containers.quadlet.systemd.daemon_reload")
        from ots_containers import quadlet
        from ots_containers.config import Config

        # Create an env file with SECRET_VARIABLE_NAMES
        env_file = tmp_path / "onetimesecret.env"
        env_file.write_text(
            "SECRET_VARIABLE_NAMES=API_KEY,DB_PASSWORD\n"
            "_API_KEY=ots_api_key\n"
            "_DB_PASSWORD=ots_db_password\n"
        )

        cfg = Config(
            worker_template_path=tmp_path / "onetime-worker@.container",
            var_dir=tmp_path / "var",
        )

        quadlet.write_worker_template(cfg, env_file_path=env_file)

        content = cfg.worker_template_path.read_text()
        assert "Secret=ots_api_key,type=env,target=API_KEY" in content
        assert "Secret=ots_db_password,type=env,target=DB_PASSWORD" in content

    def test_write_worker_template_reloads_daemon(self, mocker, tmp_path):
        """write_worker_template should reload systemd daemon after writing."""
        mock_reload = mocker.patch("ots_containers.quadlet.systemd.daemon_reload")
        from ots_containers import quadlet
        from ots_containers.config import Config

        cfg = Config(
            worker_template_path=tmp_path / "onetime-worker@.container",
            var_dir=tmp_path / "var",
        )

        quadlet.write_worker_template(cfg)

        mock_reload.assert_called_once()


class TestSchedulerTemplate:
    """Test scheduler container quadlet template generation."""

    def test_write_scheduler_template_creates_file(self, mocker, tmp_path):
        """write_scheduler_template should create the scheduler quadlet file."""
        mocker.patch("ots_containers.quadlet.systemd.daemon_reload")
        from ots_containers import quadlet
        from ots_containers.config import Config

        cfg = Config(
            scheduler_template_path=tmp_path / "onetime-scheduler@.container",
            var_dir=tmp_path / "var",
        )

        quadlet.write_scheduler_template(cfg)

        assert cfg.scheduler_template_path.exists()

    def test_write_scheduler_template_includes_image(self, mocker, tmp_path, monkeypatch):
        """Scheduler quadlet should include Image from config."""
        mocker.patch("ots_containers.quadlet.systemd.daemon_reload")
        monkeypatch.setenv("IMAGE", "myregistry/myimage")
        monkeypatch.setenv("TAG", "v1.0.0")

        from ots_containers import quadlet
        from ots_containers.config import Config

        cfg = Config(
            scheduler_template_path=tmp_path / "onetime-scheduler@.container",
            var_dir=tmp_path / "var",
        )

        quadlet.write_scheduler_template(cfg)

        content = cfg.scheduler_template_path.read_text()
        assert "Image=myregistry/myimage:v1.0.0" in content

    def test_write_scheduler_template_uses_host_network(self, mocker, tmp_path):
        """Scheduler quadlet should use host networking."""
        mocker.patch("ots_containers.quadlet.systemd.daemon_reload")
        from ots_containers import quadlet
        from ots_containers.config import Config

        cfg = Config(
            scheduler_template_path=tmp_path / "onetime-scheduler@.container",
            var_dir=tmp_path / "var",
        )

        quadlet.write_scheduler_template(cfg)

        content = cfg.scheduler_template_path.read_text()
        assert "Network=host" in content

    def test_write_scheduler_template_includes_syslog_tag(self, mocker, tmp_path):
        """Scheduler quadlet should include syslog tag for unified log filtering."""
        mocker.patch("ots_containers.quadlet.systemd.daemon_reload")
        from ots_containers import quadlet
        from ots_containers.config import Config

        cfg = Config(
            scheduler_template_path=tmp_path / "onetime-scheduler@.container",
            var_dir=tmp_path / "var",
        )

        quadlet.write_scheduler_template(cfg)

        content = cfg.scheduler_template_path.read_text()
        # Syslog tag allows: journalctl -t onetime-scheduler-main -f
        assert "PodmanArgs=--log-opt tag=onetime-scheduler-%i" in content

    def test_write_scheduler_template_sets_scheduler_id_env_var(self, mocker, tmp_path):
        """Scheduler quadlet should set SCHEDULER_ID env var from instance."""
        mocker.patch("ots_containers.quadlet.systemd.daemon_reload")
        from ots_containers import quadlet
        from ots_containers.config import Config

        cfg = Config(
            scheduler_template_path=tmp_path / "onetime-scheduler@.container",
            var_dir=tmp_path / "var",
        )

        quadlet.write_scheduler_template(cfg)

        content = cfg.scheduler_template_path.read_text()
        assert "Environment=SCHEDULER_ID=%i" in content

    def test_write_scheduler_template_has_scheduler_entry_point(self, mocker, tmp_path):
        """Scheduler quadlet should have scheduler-specific entry point."""
        mocker.patch("ots_containers.quadlet.systemd.daemon_reload")
        from ots_containers import quadlet
        from ots_containers.config import Config

        cfg = Config(
            scheduler_template_path=tmp_path / "onetime-scheduler@.container",
            var_dir=tmp_path / "var",
        )

        quadlet.write_scheduler_template(cfg)

        content = cfg.scheduler_template_path.read_text()
        assert "bin/entrypoint.sh bin/ots scheduler" in content

    def test_write_scheduler_template_has_scheduler_health_check(self, mocker, tmp_path):
        """Scheduler quadlet should check for scheduler process."""
        mocker.patch("ots_containers.quadlet.systemd.daemon_reload")
        from ots_containers import quadlet
        from ots_containers.config import Config

        cfg = Config(
            scheduler_template_path=tmp_path / "onetime-scheduler@.container",
            var_dir=tmp_path / "var",
        )

        quadlet.write_scheduler_template(cfg)

        content = cfg.scheduler_template_path.read_text()
        assert 'pgrep -f "bin/ots scheduler"' in content

    def test_write_scheduler_template_has_timeout_stop_sec(self, mocker, tmp_path):
        """Scheduler quadlet should have TimeoutStopSec for graceful job completion."""
        mocker.patch("ots_containers.quadlet.systemd.daemon_reload")
        from ots_containers import quadlet
        from ots_containers.config import Config

        cfg = Config(
            scheduler_template_path=tmp_path / "onetime-scheduler@.container",
            var_dir=tmp_path / "var",
        )

        quadlet.write_scheduler_template(cfg)

        content = cfg.scheduler_template_path.read_text()
        assert "TimeoutStopSec=60" in content

    def test_write_scheduler_template_no_static_assets_volume(self, mocker, tmp_path):
        """Scheduler quadlet should NOT mount static_assets volume."""
        mocker.patch("ots_containers.quadlet.systemd.daemon_reload")
        from ots_containers import quadlet
        from ots_containers.config import Config

        cfg = Config(
            scheduler_template_path=tmp_path / "onetime-scheduler@.container",
            var_dir=tmp_path / "var",
        )

        quadlet.write_scheduler_template(cfg)

        content = cfg.scheduler_template_path.read_text()
        assert "static_assets" not in content

    def test_write_scheduler_template_no_port_env_var(self, mocker, tmp_path):
        """Scheduler quadlet should NOT have PORT env var (unlike web instances)."""
        mocker.patch("ots_containers.quadlet.systemd.daemon_reload")
        from ots_containers import quadlet
        from ots_containers.config import Config

        cfg = Config(
            scheduler_template_path=tmp_path / "onetime-scheduler@.container",
            var_dir=tmp_path / "var",
        )

        quadlet.write_scheduler_template(cfg)

        content = cfg.scheduler_template_path.read_text()
        assert "Environment=PORT=" not in content

    def test_write_scheduler_template_includes_environment_file(self, mocker, tmp_path):
        """Scheduler quadlet should reference shared environment file."""
        mocker.patch("ots_containers.quadlet.systemd.daemon_reload")
        from ots_containers import quadlet
        from ots_containers.config import Config

        cfg = Config(
            scheduler_template_path=tmp_path / "onetime-scheduler@.container",
            var_dir=tmp_path / "var",
        )

        quadlet.write_scheduler_template(cfg)

        content = cfg.scheduler_template_path.read_text()
        assert "EnvironmentFile=/etc/default/onetimesecret" in content

    def test_write_scheduler_template_includes_config_volume(self, mocker, tmp_path):
        """Scheduler quadlet should mount config directory."""
        mocker.patch("ots_containers.quadlet.systemd.daemon_reload")
        from ots_containers import quadlet
        from ots_containers.config import Config

        config_dir = tmp_path / "etc"
        cfg = Config(
            scheduler_template_path=tmp_path / "onetime-scheduler@.container",
            config_dir=config_dir,
            var_dir=tmp_path / "var",
        )

        quadlet.write_scheduler_template(cfg)

        content = cfg.scheduler_template_path.read_text()
        assert f"Volume={config_dir}:/app/etc:ro" in content

    def test_write_scheduler_template_includes_podman_secrets(self, mocker, tmp_path):
        """Scheduler quadlet should include Secret directives from env file."""
        mocker.patch("ots_containers.quadlet.systemd.daemon_reload")
        from ots_containers import quadlet
        from ots_containers.config import Config

        env_file = tmp_path / "onetimesecret.env"
        env_file.write_text(
            "SECRET_VARIABLE_NAMES=API_KEY,DB_PASSWORD\n"
            "_API_KEY=ots_api_key\n"
            "_DB_PASSWORD=ots_db_password\n"
        )

        cfg = Config(
            scheduler_template_path=tmp_path / "onetime-scheduler@.container",
            var_dir=tmp_path / "var",
        )

        quadlet.write_scheduler_template(cfg, env_file_path=env_file)

        content = cfg.scheduler_template_path.read_text()
        assert "Secret=ots_api_key,type=env,target=API_KEY" in content
        assert "Secret=ots_db_password,type=env,target=DB_PASSWORD" in content

    def test_write_scheduler_template_creates_parent_dirs(self, mocker, tmp_path):
        """write_scheduler_template should create parent directories if needed."""
        mocker.patch("ots_containers.quadlet.systemd.daemon_reload")
        from ots_containers import quadlet
        from ots_containers.config import Config

        nested_path = tmp_path / "subdir" / "onetime-scheduler@.container"
        cfg = Config(
            scheduler_template_path=nested_path,
            var_dir=tmp_path / "var",
        )

        quadlet.write_scheduler_template(cfg)

        assert nested_path.exists()

    def test_write_scheduler_template_reloads_daemon(self, mocker, tmp_path):
        """write_scheduler_template should reload systemd daemon after writing."""
        mock_reload = mocker.patch("ots_containers.quadlet.systemd.daemon_reload")
        from ots_containers import quadlet
        from ots_containers.config import Config

        cfg = Config(
            scheduler_template_path=tmp_path / "onetime-scheduler@.container",
            var_dir=tmp_path / "var",
        )

        quadlet.write_scheduler_template(cfg)

        mock_reload.assert_called_once()
