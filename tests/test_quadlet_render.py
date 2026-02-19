# tests/test_quadlet_render.py
"""Tests for render_*_template and _build_fmt_vars functions in quadlet.py.

These functions were added for dry-run support in the deploy and redeploy commands.
They render quadlet template content without writing to disk.
"""


def _make_cfg(mocker, tmp_path, image="ghcr.io/test/image", tag="v1.0.0"):
    """Return a minimal Config mock for render tests."""
    from ots_containers.config import Config

    cfg = mocker.MagicMock(spec=Config)
    cfg.existing_config_files = []
    cfg.memory_max = None
    cfg.cpu_quota = None
    cfg.valkey_service = None
    cfg.config_dir = tmp_path / "etc"
    cfg.resolved_image_with_tag = f"{image}:{tag}"
    return cfg


class TestRenderWebTemplate:
    """Tests for render_web_template (dry-run, no disk I/O)."""

    def test_returns_non_empty_string(self, mocker, tmp_path):
        """render_web_template should return a non-empty string."""
        from ots_containers import quadlet

        cfg = _make_cfg(mocker, tmp_path)
        result = quadlet.render_web_template(cfg, force=True)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_includes_image(self, mocker, tmp_path):
        """render_web_template should substitute image:tag into the template."""
        from ots_containers import quadlet

        cfg = _make_cfg(mocker, tmp_path, image="my.registry/myapp", tag="v2.5.0")
        result = quadlet.render_web_template(cfg, force=True)
        assert "Image=my.registry/myapp:v2.5.0" in result

    def test_contains_network_host(self, mocker, tmp_path):
        """render_web_template output should use host network."""
        from ots_containers import quadlet

        cfg = _make_cfg(mocker, tmp_path)
        result = quadlet.render_web_template(cfg, force=True)
        assert "Network=host" in result

    def test_no_disk_write(self, mocker, tmp_path):
        """render_web_template must not write any file or call daemon_reload."""
        from ots_containers import quadlet

        cfg = _make_cfg(mocker, tmp_path)
        mock_daemon = mocker.patch("ots_containers.quadlet.systemd.daemon_reload")
        quadlet.render_web_template(cfg, force=True)
        mock_daemon.assert_not_called()
        # No files should be created in tmp_path
        assert not list(tmp_path.iterdir())

    def test_accepts_env_file_path_none(self, mocker, tmp_path):
        """render_web_template should accept env_file_path=None without error."""
        from ots_containers import quadlet

        cfg = _make_cfg(mocker, tmp_path)
        # env_file_path=None falls back to DEFAULT_ENV_FILE; patch it to nonexistent
        mocker.patch("ots_containers.quadlet.DEFAULT_ENV_FILE", tmp_path / "noenv")
        result = quadlet.render_web_template(cfg, env_file_path=None, force=True)
        assert isinstance(result, str)

    def test_output_changes_with_image_tag(self, mocker, tmp_path):
        """render_web_template output must change when image/tag changes."""
        from ots_containers import quadlet

        cfg_a = _make_cfg(mocker, tmp_path, tag="v1.0.0")
        cfg_b = _make_cfg(mocker, tmp_path, tag="v2.0.0")

        result_a = quadlet.render_web_template(cfg_a, force=True)
        result_b = quadlet.render_web_template(cfg_b, force=True)

        assert result_a != result_b
        assert "v1.0.0" in result_a
        assert "v2.0.0" in result_b

    def test_with_valkey_service(self, mocker, tmp_path):
        """render_web_template should include valkey dependency lines when configured."""
        from ots_containers import quadlet

        cfg = _make_cfg(mocker, tmp_path)
        cfg.valkey_service = "valkey-server@6379.service"

        result = quadlet.render_web_template(cfg, force=True)
        assert "valkey-server@6379.service" in result

    def test_no_valkey_by_default(self, mocker, tmp_path):
        """render_web_template should not include valkey when not configured."""
        from ots_containers import quadlet

        cfg = _make_cfg(mocker, tmp_path)
        cfg.valkey_service = None

        result = quadlet.render_web_template(cfg, force=True)
        assert "valkey-server" not in result


class TestRenderWorkerTemplate:
    """Tests for render_worker_template (dry-run, no disk I/O)."""

    def test_returns_non_empty_string(self, mocker, tmp_path):
        """render_worker_template should return a non-empty string."""
        from ots_containers import quadlet

        cfg = _make_cfg(mocker, tmp_path)
        result = quadlet.render_worker_template(cfg, force=True)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_includes_image(self, mocker, tmp_path):
        """render_worker_template should substitute image:tag into the template."""
        from ots_containers import quadlet

        cfg = _make_cfg(mocker, tmp_path, image="my.registry/myapp", tag="v3.0.0")
        result = quadlet.render_worker_template(cfg, force=True)
        assert "Image=my.registry/myapp:v3.0.0" in result

    def test_no_disk_write(self, mocker, tmp_path):
        """render_worker_template must not write any file or call daemon_reload."""
        from ots_containers import quadlet

        cfg = _make_cfg(mocker, tmp_path)
        mock_daemon = mocker.patch("ots_containers.quadlet.systemd.daemon_reload")
        quadlet.render_worker_template(cfg, force=True)
        mock_daemon.assert_not_called()

    def test_output_changes_with_tag(self, mocker, tmp_path):
        """render_worker_template output must change when tag changes."""
        from ots_containers import quadlet

        cfg_a = _make_cfg(mocker, tmp_path, tag="v1.0.0")
        cfg_b = _make_cfg(mocker, tmp_path, tag="v1.1.0")

        result_a = quadlet.render_worker_template(cfg_a, force=True)
        result_b = quadlet.render_worker_template(cfg_b, force=True)

        assert result_a != result_b

    def test_contains_worker_entry_point(self, mocker, tmp_path):
        """render_worker_template output should contain the worker entry point."""
        from ots_containers import quadlet

        cfg = _make_cfg(mocker, tmp_path)
        result = quadlet.render_worker_template(cfg, force=True)
        assert "bin/ots worker" in result

    def test_force_true_without_env_file(self, mocker, tmp_path):
        """render_worker_template with force=True should not exit even without env file."""
        from ots_containers import quadlet

        cfg = _make_cfg(mocker, tmp_path)
        mocker.patch("ots_containers.quadlet.DEFAULT_ENV_FILE", tmp_path / "noenv")
        result = quadlet.render_worker_template(cfg, force=True)
        assert "No secrets configured" in result


class TestRenderSchedulerTemplate:
    """Tests for render_scheduler_template (dry-run, no disk I/O)."""

    def test_returns_non_empty_string(self, mocker, tmp_path):
        """render_scheduler_template should return a non-empty string."""
        from ots_containers import quadlet

        cfg = _make_cfg(mocker, tmp_path)
        result = quadlet.render_scheduler_template(cfg, force=True)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_includes_image(self, mocker, tmp_path):
        """render_scheduler_template should substitute image:tag into the template."""
        from ots_containers import quadlet

        cfg = _make_cfg(mocker, tmp_path, image="my.registry/myapp", tag="v4.0.0")
        result = quadlet.render_scheduler_template(cfg, force=True)
        assert "Image=my.registry/myapp:v4.0.0" in result

    def test_no_disk_write(self, mocker, tmp_path):
        """render_scheduler_template must not write any file or call daemon_reload."""
        from ots_containers import quadlet

        cfg = _make_cfg(mocker, tmp_path)
        mock_daemon = mocker.patch("ots_containers.quadlet.systemd.daemon_reload")
        quadlet.render_scheduler_template(cfg, force=True)
        mock_daemon.assert_not_called()

    def test_output_changes_with_tag(self, mocker, tmp_path):
        """render_scheduler_template output must change when tag changes."""
        from ots_containers import quadlet

        cfg_a = _make_cfg(mocker, tmp_path, tag="v1.0.0")
        cfg_b = _make_cfg(mocker, tmp_path, tag="v2.0.0")

        result_a = quadlet.render_scheduler_template(cfg_a, force=True)
        result_b = quadlet.render_scheduler_template(cfg_b, force=True)

        assert result_a != result_b

    def test_contains_scheduler_entry_point(self, mocker, tmp_path):
        """render_scheduler_template output should contain the scheduler entry point."""
        from ots_containers import quadlet

        cfg = _make_cfg(mocker, tmp_path)
        result = quadlet.render_scheduler_template(cfg, force=True)
        assert "bin/ots scheduler" in result

    def test_force_true_without_env_file(self, mocker, tmp_path):
        """render_scheduler_template with force=True should not exit without env file."""
        from ots_containers import quadlet

        cfg = _make_cfg(mocker, tmp_path)
        mocker.patch("ots_containers.quadlet.DEFAULT_ENV_FILE", tmp_path / "noenv")
        result = quadlet.render_scheduler_template(cfg, force=True)
        assert "No secrets configured" in result


class TestBuildFmtVars:
    """Tests for _build_fmt_vars internal helper."""

    def test_contains_required_keys(self, mocker, tmp_path):
        """_build_fmt_vars should produce a dict with all required template keys."""
        from ots_containers import quadlet

        cfg = _make_cfg(mocker, tmp_path)
        # Use WORKER_TEMPLATE which needs no extra_vars
        result = quadlet._build_fmt_vars(quadlet.WORKER_TEMPLATE, cfg, None, force=True)
        assert "image" in result
        assert "secrets_section" in result
        assert "config_volumes_section" in result
        assert "resource_limits_section" in result

    def test_image_matches_cfg(self, mocker, tmp_path):
        """_build_fmt_vars image value should come from cfg.resolved_image_with_tag."""
        from ots_containers import quadlet

        cfg = _make_cfg(mocker, tmp_path)
        cfg.resolved_image_with_tag = "custom.registry/app:v9.9.9"
        result = quadlet._build_fmt_vars(quadlet.WORKER_TEMPLATE, cfg, None, force=True)
        assert result["image"] == "custom.registry/app:v9.9.9"

    def test_accepts_extra_vars(self, mocker, tmp_path):
        """_build_fmt_vars should merge extra_vars into the result."""
        from ots_containers import quadlet

        cfg = _make_cfg(mocker, tmp_path)
        result = quadlet._build_fmt_vars(
            quadlet.WEB_TEMPLATE,
            cfg,
            None,
            force=True,
            extra_vars={"valkey_after": "", "valkey_wants": ""},
        )
        assert "valkey_after" in result
        assert "valkey_wants" in result

    def test_extra_vars_override_defaults(self, mocker, tmp_path):
        """Extra vars should override any default keys of the same name."""
        from ots_containers import quadlet

        cfg = _make_cfg(mocker, tmp_path)
        sentinel = "sentinel_value_xyz"
        result = quadlet._build_fmt_vars(
            quadlet.WORKER_TEMPLATE,
            cfg,
            None,
            force=True,
            extra_vars={"image": sentinel},
        )
        assert result["image"] == sentinel

    def test_no_extra_vars(self, mocker, tmp_path):
        """_build_fmt_vars with extra_vars=None should work without error."""
        from ots_containers import quadlet

        cfg = _make_cfg(mocker, tmp_path)
        result = quadlet._build_fmt_vars(
            quadlet.WORKER_TEMPLATE, cfg, None, force=True, extra_vars=None
        )
        assert isinstance(result, dict)
        assert "image" in result
