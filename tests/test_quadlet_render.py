# tests/test_quadlet_render.py

"""Tests for render_*_template and _build_fmt_vars functions in quadlet.py.

These functions were added for dry-run support in the deploy and redeploy commands.
They render quadlet template content without writing to disk.
"""

import pytest

pytestmark = pytest.mark.quick


def _make_cfg(mocker, tmp_path, image="ghcr.io/test/image", tag="v1.0.0", registry=None):
    """Return a minimal Config mock for render tests."""
    from rots.config import Config

    cfg = mocker.MagicMock(spec=Config)
    cfg.existing_config_files = []
    cfg.memory_max = None
    cfg.cpu_quota = None
    cfg.valkey_service = None
    cfg.registry = registry
    cfg.config_dir = tmp_path / "etc"
    cfg.resolved_image_with_tag.return_value = f"{image}:{tag}"
    return cfg


class TestRenderWebTemplate:
    """Tests for render_web_template (dry-run, no disk I/O)."""

    def test_returns_non_empty_string(self, mocker, tmp_path):
        """render_web_template should return a non-empty string."""
        from rots import quadlet

        cfg = _make_cfg(mocker, tmp_path)
        result = quadlet.render_web_template(cfg, force=True)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_includes_image(self, mocker, tmp_path):
        """render_web_template should substitute image:tag into the template."""
        from rots import quadlet

        cfg = _make_cfg(mocker, tmp_path, image="my.registry/myapp", tag="v2.5.0")
        result = quadlet.render_web_template(cfg, force=True)
        assert "Image=my.registry/myapp:v2.5.0" in result

    def test_contains_network_host(self, mocker, tmp_path):
        """render_web_template output should use host network."""
        from rots import quadlet

        cfg = _make_cfg(mocker, tmp_path)
        result = quadlet.render_web_template(cfg, force=True)
        assert "Network=host" in result

    def test_no_disk_write(self, mocker, tmp_path):
        """render_web_template must not write any file or call daemon_reload."""
        from rots import quadlet

        cfg = _make_cfg(mocker, tmp_path)
        mock_daemon = mocker.patch("rots.quadlet.systemd.daemon_reload")
        quadlet.render_web_template(cfg, force=True)
        mock_daemon.assert_not_called()
        # No files should be created in tmp_path
        assert not list(tmp_path.iterdir())

    def test_accepts_env_file_path_none(self, mocker, tmp_path):
        """render_web_template should accept env_file_path=None without error."""
        from rots import quadlet

        cfg = _make_cfg(mocker, tmp_path)
        # env_file_path=None falls back to DEFAULT_ENV_FILE; patch it to nonexistent
        mocker.patch("rots.quadlet.DEFAULT_ENV_FILE", tmp_path / "noenv")
        result = quadlet.render_web_template(cfg, env_file_path=None, force=True)
        assert isinstance(result, str)

    def test_output_changes_with_image_tag(self, mocker, tmp_path):
        """render_web_template output must change when image/tag changes."""
        from rots import quadlet

        cfg_a = _make_cfg(mocker, tmp_path, tag="v1.0.0")
        cfg_b = _make_cfg(mocker, tmp_path, tag="v2.0.0")

        result_a = quadlet.render_web_template(cfg_a, force=True)
        result_b = quadlet.render_web_template(cfg_b, force=True)

        assert result_a != result_b
        assert "v1.0.0" in result_a
        assert "v2.0.0" in result_b

    def test_with_valkey_service(self, mocker, tmp_path):
        """render_web_template should include valkey dependency lines when configured."""
        from rots import quadlet

        cfg = _make_cfg(mocker, tmp_path)
        cfg.valkey_service = "valkey-server@6379.service"

        result = quadlet.render_web_template(cfg, force=True)
        assert "valkey-server@6379.service" in result

    def test_no_valkey_by_default(self, mocker, tmp_path):
        """render_web_template should not include valkey when not configured."""
        from rots import quadlet

        cfg = _make_cfg(mocker, tmp_path)
        cfg.valkey_service = None

        result = quadlet.render_web_template(cfg, force=True)
        assert "valkey-server" not in result


class TestRenderWorkerTemplate:
    """Tests for render_worker_template (dry-run, no disk I/O)."""

    def test_returns_non_empty_string(self, mocker, tmp_path):
        """render_worker_template should return a non-empty string."""
        from rots import quadlet

        cfg = _make_cfg(mocker, tmp_path)
        result = quadlet.render_worker_template(cfg, force=True)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_includes_image(self, mocker, tmp_path):
        """render_worker_template should substitute image:tag into the template."""
        from rots import quadlet

        cfg = _make_cfg(mocker, tmp_path, image="my.registry/myapp", tag="v3.0.0")
        result = quadlet.render_worker_template(cfg, force=True)
        assert "Image=my.registry/myapp:v3.0.0" in result

    def test_no_disk_write(self, mocker, tmp_path):
        """render_worker_template must not write any file or call daemon_reload."""
        from rots import quadlet

        cfg = _make_cfg(mocker, tmp_path)
        mock_daemon = mocker.patch("rots.quadlet.systemd.daemon_reload")
        quadlet.render_worker_template(cfg, force=True)
        mock_daemon.assert_not_called()

    def test_output_changes_with_tag(self, mocker, tmp_path):
        """render_worker_template output must change when tag changes."""
        from rots import quadlet

        cfg_a = _make_cfg(mocker, tmp_path, tag="v1.0.0")
        cfg_b = _make_cfg(mocker, tmp_path, tag="v1.1.0")

        result_a = quadlet.render_worker_template(cfg_a, force=True)
        result_b = quadlet.render_worker_template(cfg_b, force=True)

        assert result_a != result_b

    def test_contains_worker_entry_point(self, mocker, tmp_path):
        """render_worker_template output should contain the worker entry point."""
        from rots import quadlet

        cfg = _make_cfg(mocker, tmp_path)
        result = quadlet.render_worker_template(cfg, force=True)
        assert "bin/ots worker" in result

    def test_force_true_without_env_file(self, mocker, tmp_path):
        """render_worker_template with force=True should not exit even without env file."""
        from rots import quadlet

        cfg = _make_cfg(mocker, tmp_path)
        mocker.patch("rots.quadlet.DEFAULT_ENV_FILE", tmp_path / "noenv")
        result = quadlet.render_worker_template(cfg, force=True)
        assert "No secrets configured" in result


class TestRenderSchedulerTemplate:
    """Tests for render_scheduler_template (dry-run, no disk I/O)."""

    def test_returns_non_empty_string(self, mocker, tmp_path):
        """render_scheduler_template should return a non-empty string."""
        from rots import quadlet

        cfg = _make_cfg(mocker, tmp_path)
        result = quadlet.render_scheduler_template(cfg, force=True)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_includes_image(self, mocker, tmp_path):
        """render_scheduler_template should substitute image:tag into the template."""
        from rots import quadlet

        cfg = _make_cfg(mocker, tmp_path, image="my.registry/myapp", tag="v4.0.0")
        result = quadlet.render_scheduler_template(cfg, force=True)
        assert "Image=my.registry/myapp:v4.0.0" in result

    def test_no_disk_write(self, mocker, tmp_path):
        """render_scheduler_template must not write any file or call daemon_reload."""
        from rots import quadlet

        cfg = _make_cfg(mocker, tmp_path)
        mock_daemon = mocker.patch("rots.quadlet.systemd.daemon_reload")
        quadlet.render_scheduler_template(cfg, force=True)
        mock_daemon.assert_not_called()

    def test_output_changes_with_tag(self, mocker, tmp_path):
        """render_scheduler_template output must change when tag changes."""
        from rots import quadlet

        cfg_a = _make_cfg(mocker, tmp_path, tag="v1.0.0")
        cfg_b = _make_cfg(mocker, tmp_path, tag="v2.0.0")

        result_a = quadlet.render_scheduler_template(cfg_a, force=True)
        result_b = quadlet.render_scheduler_template(cfg_b, force=True)

        assert result_a != result_b

    def test_contains_scheduler_entry_point(self, mocker, tmp_path):
        """render_scheduler_template output should contain the scheduler entry point."""
        from rots import quadlet

        cfg = _make_cfg(mocker, tmp_path)
        result = quadlet.render_scheduler_template(cfg, force=True)
        assert "bin/ots scheduler" in result

    def test_force_true_without_env_file(self, mocker, tmp_path):
        """render_scheduler_template with force=True should not exit without env file."""
        from rots import quadlet

        cfg = _make_cfg(mocker, tmp_path)
        mocker.patch("rots.quadlet.DEFAULT_ENV_FILE", tmp_path / "noenv")
        result = quadlet.render_scheduler_template(cfg, force=True)
        assert "No secrets configured" in result


class TestBuildFmtVars:
    """Tests for _build_fmt_vars internal helper."""

    def test_contains_required_keys(self, mocker, tmp_path):
        """_build_fmt_vars should produce a dict with all required template keys."""
        from rots import quadlet

        cfg = _make_cfg(mocker, tmp_path)
        # _build_fmt_vars no longer takes template as first arg
        result = quadlet._build_fmt_vars(cfg, None, force=True)
        assert "image" in result
        assert "secrets_section" in result
        assert "config_volumes_section" in result
        assert "resource_limits_section" in result

    def test_image_matches_cfg_no_registry(self, mocker, tmp_path):
        """_build_fmt_vars image should be FQIN when registry is not set."""
        from rots import quadlet

        cfg = _make_cfg(mocker, tmp_path)
        cfg.resolved_image_with_tag.return_value = "custom.registry/app:v9.9.9"
        result = quadlet._build_fmt_vars(cfg, None, force=True)
        assert result["image"] == "custom.registry/app:v9.9.9"

    def test_image_matches_cfg_with_registry(self, mocker, tmp_path):
        """_build_fmt_vars image should be onetime.image when registry is set."""
        from rots import quadlet

        cfg = _make_cfg(mocker, tmp_path, registry="registry.example.com")
        result = quadlet._build_fmt_vars(cfg, None, force=True)
        assert result["image"] == "onetime.image"

    def test_accepts_extra_vars(self, mocker, tmp_path):
        """_build_fmt_vars should merge extra_vars into the result."""
        from rots import quadlet

        cfg = _make_cfg(mocker, tmp_path)
        result = quadlet._build_fmt_vars(
            cfg,
            None,
            force=True,
            extra_vars={"valkey_after": "", "valkey_wants": ""},
        )
        assert "valkey_after" in result
        assert "valkey_wants" in result

    def test_extra_vars_override_defaults(self, mocker, tmp_path):
        """Extra vars should override any default keys of the same name."""
        from rots import quadlet

        cfg = _make_cfg(mocker, tmp_path)
        sentinel = "sentinel_value_xyz"
        result = quadlet._build_fmt_vars(
            cfg,
            None,
            force=True,
            extra_vars={"image": sentinel},
        )
        assert result["image"] == sentinel

    def test_no_extra_vars(self, mocker, tmp_path):
        """_build_fmt_vars with extra_vars=None should work without error."""
        from rots import quadlet

        cfg = _make_cfg(mocker, tmp_path)
        result = quadlet._build_fmt_vars(cfg, None, force=True, extra_vars=None)
        assert isinstance(result, dict)
        assert "image" in result


class TestAuthFileInTemplates:
    """Test AuthFile= directive in rendered templates when registry is configured."""

    def test_no_registry_no_authfile_web(self, mocker, tmp_path):
        """Without registry, web template should not contain AuthFile."""
        from rots import quadlet

        cfg = _make_cfg(mocker, tmp_path)
        result = quadlet.render_web_template(cfg, force=True)
        assert "AuthFile=" not in result

    def test_no_registry_no_authfile_worker(self, mocker, tmp_path):
        """Without registry, worker template should not contain AuthFile."""
        from rots import quadlet

        cfg = _make_cfg(mocker, tmp_path)
        result = quadlet.render_worker_template(cfg, force=True)
        assert "AuthFile=" not in result

    def test_no_registry_no_authfile_scheduler(self, mocker, tmp_path):
        """Without registry, scheduler template should not contain AuthFile."""
        from rots import quadlet

        cfg = _make_cfg(mocker, tmp_path)
        result = quadlet.render_scheduler_template(cfg, force=True)
        assert "AuthFile=" not in result

    def test_registry_uses_image_unit_web(self, mocker, tmp_path):
        """With registry, web .container should reference onetime.image, not AuthFile."""
        from pathlib import Path

        from rots import quadlet

        cfg = _make_cfg(mocker, tmp_path, registry="registry.example.com")
        cfg.get_registry_auth_file.return_value = Path("/etc/containers/auth.json")
        result = quadlet.render_web_template(cfg, force=True)
        assert "AuthFile=" not in result
        assert "Image=onetime.image" in result

    def test_registry_uses_image_unit_worker(self, mocker, tmp_path):
        """With registry, worker .container should reference onetime.image, not AuthFile."""
        from pathlib import Path

        from rots import quadlet

        cfg = _make_cfg(mocker, tmp_path, registry="registry.example.com")
        cfg.get_registry_auth_file.return_value = Path("/etc/containers/auth.json")
        result = quadlet.render_worker_template(cfg, force=True)
        assert "AuthFile=" not in result
        assert "Image=onetime.image" in result

    def test_registry_uses_image_unit_scheduler(self, mocker, tmp_path):
        """With registry, scheduler .container should reference onetime.image, not AuthFile."""
        from pathlib import Path

        from rots import quadlet

        cfg = _make_cfg(mocker, tmp_path, registry="registry.example.com")
        cfg.get_registry_auth_file.return_value = Path("/etc/containers/auth.json")
        result = quadlet.render_scheduler_template(cfg, force=True)
        assert "AuthFile=" not in result
        assert "Image=onetime.image" in result


class TestRenderImageTemplate:
    """Tests for render_image_template (companion .image unit)."""

    def test_returns_content_with_image_section(self, mocker, tmp_path):
        """render_image_template should return content with an [Image] section."""
        from pathlib import Path

        from rots import quadlet

        cfg = _make_cfg(mocker, tmp_path, registry="registry.example.com")
        cfg.get_registry_auth_file.return_value = Path("/etc/containers/auth.json")
        result = quadlet.render_image_template(cfg)
        assert "[Image]" in result

    def test_includes_fqin_and_authfile(self, mocker, tmp_path):
        """render_image_template should include Image= with FQIN and AuthFile=."""
        from pathlib import Path

        from rots import quadlet

        cfg = _make_cfg(
            mocker,
            tmp_path,
            image="registry.example.com/app",
            tag="v2.0.0",
            registry="registry.example.com",
        )
        cfg.get_registry_auth_file.return_value = Path("/etc/containers/auth.json")
        result = quadlet.render_image_template(cfg)
        assert "Image=registry.example.com/app:v2.0.0" in result
        assert "AuthFile=/etc/containers/auth.json" in result

    def test_no_disk_write(self, mocker, tmp_path):
        """render_image_template must not write any file or call daemon_reload."""
        from pathlib import Path

        from rots import quadlet

        cfg = _make_cfg(mocker, tmp_path, registry="registry.example.com")
        cfg.get_registry_auth_file.return_value = Path("/etc/containers/auth.json")
        mock_daemon = mocker.patch("rots.quadlet.systemd.daemon_reload")
        quadlet.render_image_template(cfg)
        mock_daemon.assert_not_called()


class TestNoRegistryDirectFQIN:
    """When registry is NOT set, .container Image= should use the direct FQIN."""

    def test_web_template_uses_fqin_without_registry(self, mocker, tmp_path):
        """render_web_template should use direct FQIN when registry is not set."""
        from rots import quadlet

        cfg = _make_cfg(mocker, tmp_path, image="ghcr.io/onetimesecret/onetimesecret", tag="v1.2.3")
        result = quadlet.render_web_template(cfg, force=True)
        assert "Image=ghcr.io/onetimesecret/onetimesecret:v1.2.3" in result
        assert "onetime.image" not in result


# Issue #67: --render / --config-source contract.
# These constants name the six known config files probed when --config-source is set.
# They mirror rots.config.CONFIG_FILES but are spelled here to keep the test
# self-documenting against the contract.
KNOWN_CONFIG_FILES = (
    "config.yaml",
    "auth.yaml",
    "logging.yaml",
    "billing.yaml",
    "Caddyfile.template",
    "puma.rb",
)


def _make_render_cfg(mocker, tmp_path, *, registry=None):
    """Build a Config mock suitable for render-mode (config_source) tests.

    Distinct from ``_make_cfg`` only in that ``get_existing_config_files`` is
    pre-stubbed with a sensible default. Tests that exercise the executor-based
    fall-through override this stub explicitly.
    """
    cfg = _make_cfg(mocker, tmp_path, registry=registry)
    cfg.get_existing_config_files.return_value = []
    return cfg


class TestGetConfigVolumesSectionWithConfigSource:
    """get_config_volumes_section behaviour under issue #67's config_source contract."""

    def test_all_six_files_present_emits_six_volume_lines(self, mocker, tmp_path):
        """All six known config files in config_source -> one Volume= line each."""
        from rots import quadlet

        cfg = _make_render_cfg(mocker, tmp_path)
        src = tmp_path / "src"
        src.mkdir()
        for fname in KNOWN_CONFIG_FILES:
            (src / fname).touch()

        result = quadlet.get_config_volumes_section(cfg, config_source=src)

        volume_lines = [ln for ln in result.splitlines() if ln.startswith("Volume=")]
        assert len(volume_lines) == 6
        # Each Volume= line must reference the correct container path and ro mode.
        for fname in KNOWN_CONFIG_FILES:
            assert any(f":/app/etc/{fname}:ro" in ln for ln in volume_lines), (
                f"missing Volume= line for {fname}: {volume_lines}"
            )

    def test_subset_present_emits_only_existing(self, mocker, tmp_path):
        """Only files that actually exist in config_source produce Volume= lines."""
        from rots import quadlet

        cfg = _make_render_cfg(mocker, tmp_path)
        src = tmp_path / "src"
        src.mkdir()
        (src / "config.yaml").touch()
        (src / "auth.yaml").touch()

        result = quadlet.get_config_volumes_section(cfg, config_source=src)

        volume_lines = [ln for ln in result.splitlines() if ln.startswith("Volume=")]
        assert len(volume_lines) == 2
        assert any(":/app/etc/config.yaml:ro" in ln for ln in volume_lines)
        assert any(":/app/etc/auth.yaml:ro" in ln for ln in volume_lines)
        # Other known files must NOT appear.
        for fname in ("logging.yaml", "billing.yaml", "Caddyfile.template", "puma.rb"):
            assert all(f":/app/etc/{fname}:ro" not in ln for ln in volume_lines)

    def test_empty_directory_emits_no_volume_lines(self, mocker, tmp_path):
        """Empty config_source directory -> no Volume= lines."""
        from rots import quadlet

        cfg = _make_render_cfg(mocker, tmp_path)
        src = tmp_path / "src"
        src.mkdir()

        result = quadlet.get_config_volumes_section(cfg, config_source=src)

        volume_lines = [ln for ln in result.splitlines() if ln.startswith("Volume=")]
        assert volume_lines == []

    def test_config_source_none_executor_none_no_volume_lines(self, mocker, tmp_path):
        """config_source=None and executor=None -> no Volume= lines (host probing returns empty)."""
        from rots import quadlet

        cfg = _make_render_cfg(mocker, tmp_path)
        # Defensive: explicit empty result so the test does not depend on
        # whatever the host filesystem looks like.
        cfg.get_existing_config_files.return_value = []
        cfg.existing_config_files = []

        result = quadlet.get_config_volumes_section(cfg, config_source=None, executor=None)

        volume_lines = [ln for ln in result.splitlines() if ln.startswith("Volume=")]
        assert volume_lines == []

    def test_executor_based_fall_through_still_works(self, mocker, tmp_path):
        """With config_source=None and executor probing, Volume= lines still emit."""
        from rots import quadlet

        cfg = _make_render_cfg(mocker, tmp_path)
        # Simulate a remote host returning all six files via the executor.
        # cfg.get_existing_config_files is the integration point we mock here;
        # the underlying test-f probing is exercised in config tests.
        host_paths = [cfg.config_dir / fname for fname in KNOWN_CONFIG_FILES]
        cfg.get_existing_config_files.return_value = host_paths

        # Pass a non-None sentinel for executor; the function should hand it
        # to cfg.get_existing_config_files. The exact executor type does not
        # matter — we only assert on the rendered output.
        executor_sentinel = mocker.MagicMock()

        result = quadlet.get_config_volumes_section(
            cfg, executor=executor_sentinel, config_source=None
        )

        volume_lines = [ln for ln in result.splitlines() if ln.startswith("Volume=")]
        assert len(volume_lines) == 6
        cfg.get_existing_config_files.assert_called_once_with(executor=executor_sentinel)


class TestRenderTemplatesWithConfigSource:
    """render_*_template propagates config_source into the rendered output."""

    def test_web_template_emits_volume_lines_for_config_source_files(self, mocker, tmp_path):
        """render_web_template with config_source -> Volume= lines for files present there."""
        from rots import quadlet

        cfg = _make_render_cfg(mocker, tmp_path)
        src = tmp_path / "src"
        src.mkdir()
        (src / "config.yaml").touch()
        (src / "auth.yaml").touch()

        result = quadlet.render_web_template(cfg, force=True, config_source=src)

        assert "/app/etc/config.yaml:ro" in result
        assert "/app/etc/auth.yaml:ro" in result
        # Files NOT placed in src must not appear.
        assert "/app/etc/logging.yaml:ro" not in result

    def test_worker_template_emits_volume_lines_for_config_source_files(self, mocker, tmp_path):
        """render_worker_template with config_source -> Volume= lines."""
        from rots import quadlet

        cfg = _make_render_cfg(mocker, tmp_path)
        src = tmp_path / "src"
        src.mkdir()
        (src / "config.yaml").touch()

        result = quadlet.render_worker_template(cfg, force=True, config_source=src)

        assert "/app/etc/config.yaml:ro" in result

    def test_scheduler_template_emits_volume_lines_for_config_source_files(self, mocker, tmp_path):
        """render_scheduler_template with config_source -> Volume= lines."""
        from rots import quadlet

        cfg = _make_render_cfg(mocker, tmp_path)
        src = tmp_path / "src"
        src.mkdir()
        (src / "billing.yaml").touch()

        result = quadlet.render_scheduler_template(cfg, force=True, config_source=src)

        assert "/app/etc/billing.yaml:ro" in result

    def test_empty_config_source_yields_no_volume_lines_in_template(self, mocker, tmp_path):
        """Empty config_source dir -> rendered template has no Volume=...:/app/etc/ lines."""
        from rots import quadlet

        cfg = _make_render_cfg(mocker, tmp_path)
        src = tmp_path / "src"
        src.mkdir()

        result = quadlet.render_web_template(cfg, force=True, config_source=src)

        # Static asset volume mount is unaffected; only host config overrides
        # are gated by config_source.
        assert "/app/etc/" not in result

    def test_no_secret_lines_when_config_source_is_set(self, mocker, tmp_path):
        """Render mode signal: no Secret= lines when config_source is set.

        This test deliberately makes secrets *visible* (returns specs, says they exist)
        so a regression that re-enables secret emission under render would surface.
        """
        from rots import quadlet
        from rots.environment_file import SecretSpec

        cfg = _make_render_cfg(mocker, tmp_path)
        # Force the secret-existence check to say "yes" — autouse fixture
        # returns False, which alone would suppress Secret= lines.
        mocker.patch("rots.quadlet.secret_exists", return_value=True)
        # And make the env file probe return real-looking secrets.
        mocker.patch(
            "rots.quadlet.get_secrets_from_env_file",
            return_value=[
                SecretSpec(env_var_name="AUTH_SECRET", secret_name="ots_auth_secret"),
                SecretSpec(env_var_name="API_KEY", secret_name="ots_api_key"),
            ],
        )
        # Make the env file appear to exist so the secrets path is reachable
        # in non-render mode.
        env_file = tmp_path / "envfile"
        env_file.write_text("SECRET_VARIABLE_NAMES=AUTH_SECRET,API_KEY\n")
        mocker.patch("rots.quadlet.DEFAULT_ENV_FILE", env_file)

        src = tmp_path / "src"
        src.mkdir()
        (src / "config.yaml").touch()

        # render_mode=True is the documented signal; pass it alongside config_source
        # since the contract states no Secret= lines are emitted under --render.
        result_web = quadlet.render_web_template(
            cfg, force=True, config_source=src, render_mode=True
        )
        result_worker = quadlet.render_worker_template(
            cfg, force=True, config_source=src, render_mode=True
        )
        result_scheduler = quadlet.render_scheduler_template(
            cfg, force=True, config_source=src, render_mode=True
        )

        assert "Secret=" not in result_web
        assert "Secret=" not in result_worker
        assert "Secret=" not in result_scheduler
