# tests/test_cli.py
"""Tests for CLI structure and invocation following Cyclopts conventions."""

import pytest

from rots.cli import app


class TestPackageVersion:
    """Test __version__ fallback in src/rots/__init__.py."""

    def test_version_fallback_when_package_not_installed(self, monkeypatch):
        """__version__ should be '0.0.0+dev' when package metadata is unavailable."""
        import importlib
        import importlib.metadata
        import sys
        from importlib.metadata import PackageNotFoundError

        # Patch version() to raise PackageNotFoundError so the except branch executes
        monkeypatch.setattr(
            importlib.metadata,
            "version",
            lambda name: (_ for _ in ()).throw(PackageNotFoundError(name)),
        )

        # Remove cached module so reload executes __init__ fresh
        monkeypatch.delitem(sys.modules, "rots", raising=False)
        import rots as pkg

        assert pkg.__version__ == "0.0.0+dev"

        # Re-register the real module so subsequent tests are unaffected
        importlib.reload(pkg)


class TestCLIStructure:
    """Test CLI app structure and help output."""

    def test_app_has_version(self):
        """App should expose version."""
        import re

        assert app.version is not None
        version = app.version if isinstance(app.version, str) else str(app.version)

        assert re.match(r"^\d+\.\d+\.\d+$", version)

    def test_app_has_help(self):
        """App should have help text."""
        assert app.help is not None
        assert "Podman" in app.help or "OTS" in app.help

    def test_help_exits_zero(self, capsys):
        """--help should exit with code 0."""
        with pytest.raises(SystemExit) as exc_info:
            app(["--help"])
        assert exc_info.value.code == 0

    def test_version_exits_zero(self, capsys):
        """--version should exit with code 0."""
        with pytest.raises(SystemExit) as exc_info:
            app(["--version"])
        assert exc_info.value.code == 0

    def test_version_output(self, capsys):
        """--version should print version string."""
        from rots import __version__

        with pytest.raises(SystemExit):
            app(["--version"])
        captured = capsys.readouterr()
        assert __version__ in captured.out


class TestCLISubcommands:
    """Test CLI subcommand routing."""

    def test_instance_subcommand_exists(self, capsys):
        """instance subcommand should exist."""
        with pytest.raises(SystemExit) as exc_info:
            app(["instance", "--help"])
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "instance" in captured.out.lower() or "deploy" in captured.out.lower()

    def test_assets_subcommand_exists(self, capsys):
        """assets subcommand should exist."""
        with pytest.raises(SystemExit) as exc_info:
            app(["assets", "--help"])
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "assets" in captured.out.lower() or "sync" in captured.out.lower()

    def test_invalid_subcommand_fails(self):
        """Invalid subcommand should fail."""
        with pytest.raises(SystemExit) as exc_info:
            app(["nonexistent"])
        assert exc_info.value.code != 0


class TestAssetsSync:
    """Test assets sync command invocation."""

    def test_assets_sync_help(self, capsys):
        """assets sync --help should show create-volume option."""
        with pytest.raises(SystemExit) as exc_info:
            app(["assets", "sync", "--help"])
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "create-volume" in captured.out.lower() or "volume" in captured.out.lower()

    def test_assets_sync_proceeds_without_validation(self, mocker):
        """assets sync should proceed without config validation (config is optional)."""
        mock_config = mocker.MagicMock()
        mocker.patch("rots.commands.assets.Config", return_value=mock_config)
        mock_update = mocker.patch("rots.commands.assets.assets_module.update")

        with pytest.raises(SystemExit) as exc_info:
            app(["assets", "sync"])

        assert exc_info.value.code == 0
        from unittest.mock import ANY

        mock_update.assert_called_once_with(mock_config, create_volume=False, executor=ANY)


class TestDefaultCommand:
    """Test the _default command (no-args fallback)."""

    def test_no_args_shows_help(self, capsys):
        """Running with no subcommand should print help."""
        from rots.cli import _default

        _default()
        captured = capsys.readouterr()
        # Help output contains subcommand names
        assert "instance" in captured.out.lower() or "version" in captured.out.lower()

    def test_app_invoked_with_no_args_prints_help(self, capsys):
        """app([]) should display help (exits 0 via cyclopts)."""
        with pytest.raises(SystemExit) as exc_info:
            app([])
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert len(captured.out) > 0


class TestConfigureLogging:
    """Test _configure_logging helper."""

    def test_verbose_suppresses_urllib3(self):
        """_configure_logging(True) should suppress urllib3 logger."""
        import logging

        from rots.cli import _configure_logging

        _configure_logging(True)
        assert logging.getLogger("urllib3").level == logging.WARNING
        # Restore root logger level for test isolation
        logging.getLogger().setLevel(logging.WARNING)

    def test_non_verbose_configures_warning(self):
        """_configure_logging(False) should not raise."""
        import logging

        from rots.cli import _configure_logging

        _configure_logging(False)
        assert logging.getLogger("urllib3").level == logging.WARNING


class TestPsCommand:
    """Test the ps command."""

    def test_ps_calls_podman_ps(self, mocker):
        """ps command should invoke Podman(executor=ex).ps with onetime filter."""
        from unittest.mock import MagicMock

        from rots.cli import ps

        mocker.patch("rots.config.Config.__init__", return_value=None)
        mocker.patch("rots.config.Config.get_executor", return_value=MagicMock())

        mock_podman_cls = mocker.patch("rots.podman.Podman")
        mock_p = MagicMock()
        mock_podman_cls.return_value = mock_p

        ps()

        mock_podman_cls.assert_called_once()
        mock_p.ps.assert_called_once()
        call_kwargs = mock_p.ps.call_args
        assert "onetime" in str(call_kwargs)

    def test_ps_help(self, capsys):
        """ps --help should exit 0 and mention containers."""
        with pytest.raises(SystemExit) as exc_info:
            app(["ps", "--help"])
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "container" in captured.out.lower() or "podman" in captured.out.lower()


class TestVersionCommand:
    """Test the version command."""

    def test_version_prints_package_version(self, capsys):
        """version command should print the package version."""
        from rots import __version__
        from rots.cli import version

        version()
        captured = capsys.readouterr()
        assert __version__ in captured.out

    def test_version_with_git_commit(self, mocker, capsys):
        """version command includes git commit if git is available."""
        from rots.cli import version

        mock_result = mocker.MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "abc1234\n"
        mocker.patch("subprocess.run", return_value=mock_result)

        version()
        captured = capsys.readouterr()
        assert "abc1234" in captured.out

    def test_version_without_git(self, mocker, capsys):
        """version command handles missing git gracefully."""
        from rots import __version__
        from rots.cli import version

        mocker.patch("subprocess.run", side_effect=FileNotFoundError("git not found"))
        version()
        captured = capsys.readouterr()
        assert __version__ in captured.out

    def test_version_git_nonzero_returncode(self, mocker, capsys):
        """version command handles git failure (non-zero exit) gracefully."""
        from rots import __version__
        from rots.cli import version

        mock_result = mocker.MagicMock()
        mock_result.returncode = 128
        mock_result.stdout = ""
        mocker.patch("subprocess.run", return_value=mock_result)

        version()
        captured = capsys.readouterr()
        assert __version__ in captured.out
        assert "git commit" not in captured.out


class TestDoctorCommand:
    """Test the doctor command.

    doctor() imports Config, EnvFile, secret_exists, DEFAULT_ENV_FILE locally
    inside the function body. Patch at their source modules:
      - rots.config.Config
      - rots.quadlet.DEFAULT_ENV_FILE
      - rots.environment_file.EnvFile
      - rots.environment_file.secret_exists
    """

    def _make_cfg_mock(self, mocker, tmp_path):
        """Config mock with real tmp_path directories and LocalExecutor."""
        from ots_shared.ssh import LocalExecutor

        cfg_mock = mocker.MagicMock()
        cfg_mock.config_dir = tmp_path / "onetimesecret"
        cfg_mock.config_dir.mkdir()
        cfg_mock.config_yaml = cfg_mock.config_dir / "config.yaml"
        cfg_mock.config_yaml.touch()
        cfg_mock.var_dir = tmp_path / "var"
        cfg_mock.var_dir.mkdir()
        cfg_mock.web_template_path = tmp_path / "onetime-web@.container"
        cfg_mock.web_template_path.touch()
        cfg_mock.get_executor.return_value = LocalExecutor()
        return cfg_mock

    def test_doctor_all_pass(self, mocker, tmp_path, capsys):
        """When all checks pass, doctor exits 0 and prints success."""
        mocker.patch("shutil.which", return_value="/usr/bin/systemctl")
        mocker.patch("os.access", return_value=True)

        cfg_mock = self._make_cfg_mock(mocker, tmp_path)
        mocker.patch("rots.config.Config", return_value=cfg_mock)

        env_file = tmp_path / "onetimesecret_env"
        env_file.write_text("SECRET_VARIABLE_NAMES=HMAC_SECRET\nHMAC_SECRET=abc\n")
        mocker.patch("rots.quadlet.DEFAULT_ENV_FILE", env_file)

        parsed_mock = mocker.MagicMock()
        parsed_mock.secret_variable_names = ["HMAC_SECRET"]
        mocker.patch("rots.environment_file.EnvFile.parse", return_value=parsed_mock)
        mocker.patch("rots.environment_file.secret_exists", return_value=True)

        running_result = mocker.MagicMock()
        running_result.returncode = 0
        running_result.stdout = "onetime-web@7043.service loaded active running\n"
        running_result.stderr = ""
        caddy_result = mocker.MagicMock()
        caddy_result.returncode = 0
        caddy_result.stdout = "active\n"
        caddy_result.stderr = ""
        mocker.patch("subprocess.run", side_effect=[running_result, caddy_result])

        from rots.cli import doctor

        doctor()
        captured = capsys.readouterr()
        assert "All checks passed" in captured.out

    def test_doctor_missing_systemctl(self, mocker, tmp_path, capsys):
        """Missing systemctl should cause doctor to exit 1."""
        mocker.patch("shutil.which", return_value=None)
        mocker.patch("os.access", return_value=False)

        cfg_mock = mocker.MagicMock()
        cfg_mock.config_dir = tmp_path / "missing_config"
        cfg_mock.var_dir = tmp_path / "missing_var"
        cfg_mock.web_template_path = tmp_path / "missing.container"
        mocker.patch("rots.config.Config", return_value=cfg_mock)

        no_env = tmp_path / "noenv"
        mocker.patch("rots.quadlet.DEFAULT_ENV_FILE", no_env)

        from rots.cli import doctor

        with pytest.raises(SystemExit) as exc_info:
            doctor()
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "FAIL" in captured.out

    def test_doctor_missing_env_file(self, mocker, tmp_path, capsys):
        """Missing env file should fail that check."""
        mocker.patch("shutil.which", return_value="/usr/bin/systemctl")
        mocker.patch("os.access", return_value=False)

        cfg_mock = mocker.MagicMock()
        cfg_mock.config_dir = tmp_path / "missing"
        cfg_mock.var_dir = tmp_path / "missing_var"
        cfg_mock.web_template_path = tmp_path / "missing.container"
        mocker.patch("rots.config.Config", return_value=cfg_mock)

        no_env = tmp_path / "noenv"
        mocker.patch("rots.quadlet.DEFAULT_ENV_FILE", no_env)

        running_result = mocker.MagicMock()
        running_result.stdout = ""
        caddy_result = mocker.MagicMock()
        caddy_result.stdout = "inactive\n"
        mocker.patch("subprocess.run", side_effect=[running_result, caddy_result])

        from rots.cli import doctor

        with pytest.raises(SystemExit) as exc_info:
            doctor()
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "exists" in captured.out

    def test_doctor_secrets_not_configured(self, mocker, tmp_path, capsys):
        """Env file exists but no secret names configured fails secrets check."""
        mocker.patch("shutil.which", return_value="/usr/bin/systemctl")
        mocker.patch("os.access", return_value=True)

        cfg_mock = self._make_cfg_mock(mocker, tmp_path)
        mocker.patch("rots.config.Config", return_value=cfg_mock)

        env_file = tmp_path / "env"
        env_file.touch()
        mocker.patch("rots.quadlet.DEFAULT_ENV_FILE", env_file)

        parsed_mock = mocker.MagicMock()
        parsed_mock.secret_variable_names = []  # no secrets declared
        mocker.patch("rots.environment_file.EnvFile.parse", return_value=parsed_mock)

        running_result = mocker.MagicMock()
        running_result.stdout = "onetime-web@7043.service active\n"
        caddy_result = mocker.MagicMock()
        caddy_result.stdout = "active\n"
        mocker.patch("subprocess.run", side_effect=[running_result, caddy_result])

        from rots.cli import doctor

        with pytest.raises(SystemExit) as exc_info:
            doctor()
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "SECRET_VARIABLE_NAMES" in captured.out

    def test_doctor_secrets_parse_error(self, mocker, tmp_path, capsys):
        """EnvFile parse error is handled gracefully."""
        mocker.patch("shutil.which", return_value="/usr/bin/systemctl")
        mocker.patch("os.access", return_value=True)

        cfg_mock = self._make_cfg_mock(mocker, tmp_path)
        mocker.patch("rots.config.Config", return_value=cfg_mock)

        env_file = tmp_path / "env"
        env_file.touch()
        mocker.patch("rots.quadlet.DEFAULT_ENV_FILE", env_file)
        mocker.patch(
            "rots.environment_file.EnvFile.parse",
            side_effect=ValueError("bad format"),
        )

        running_result = mocker.MagicMock()
        running_result.stdout = ""
        caddy_result = mocker.MagicMock()
        caddy_result.stdout = "inactive\n"
        mocker.patch("subprocess.run", side_effect=[running_result, caddy_result])

        from rots.cli import doctor

        with pytest.raises(SystemExit) as exc_info:
            doctor()
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "parse error" in captured.out

    def test_doctor_help(self, capsys):
        """doctor --help should exit 0."""
        with pytest.raises(SystemExit) as exc_info:
            app(["doctor", "--help"])
        assert exc_info.value.code == 0

    def test_doctor_systemctl_query_exception(self, mocker, tmp_path, capsys):
        """Timeout during systemctl query is handled gracefully.

        LocalExecutor.run catches subprocess.TimeoutExpired and returns a
        Result with returncode=124 and ok=False.  doctor() treats non-ok
        results as "systemctl query failed".
        """
        import subprocess

        mocker.patch("shutil.which", return_value="/usr/bin/systemctl")
        mocker.patch("os.access", return_value=True)

        cfg_mock = self._make_cfg_mock(mocker, tmp_path)
        mocker.patch("rots.config.Config", return_value=cfg_mock)

        env_file = tmp_path / "env"
        env_file.touch()
        mocker.patch("rots.quadlet.DEFAULT_ENV_FILE", env_file)

        parsed_mock = mocker.MagicMock()
        parsed_mock.secret_variable_names = ["SECRET"]
        mocker.patch("rots.environment_file.EnvFile.parse", return_value=parsed_mock)
        mocker.patch("rots.environment_file.secret_exists", return_value=True)

        # subprocess.TimeoutExpired is caught by LocalExecutor.run → Result(returncode=124)
        mocker.patch(
            "subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="systemctl", timeout=10),
        )

        from rots.cli import doctor

        with pytest.raises(SystemExit) as exc_info:
            doctor()
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "systemctl query failed" in captured.out


class TestConfigureLoggingLevels:
    """Test _configure_logging() sets log levels correctly.

    logging.basicConfig() is idempotent (no-op if handlers already exist).
    We mock basicConfig to inspect arguments and also directly test setLevel
    calls via the urllib3 logger which is always set explicitly.
    """

    def test_verbose_true_passes_debug_level_to_basicconfig(self, mocker):
        """_configure_logging(True) should call basicConfig with level=DEBUG."""
        import logging

        from rots.cli import _configure_logging

        mock_basic = mocker.patch("logging.basicConfig")
        _configure_logging(True)
        mock_basic.assert_called_once()
        call_kwargs = mock_basic.call_args[1]
        assert call_kwargs["level"] == logging.DEBUG

    def test_verbose_false_passes_info_level_to_basicconfig(self, mocker):
        """_configure_logging(False) should call basicConfig with level=INFO."""
        import logging

        from rots.cli import _configure_logging

        mock_basic = mocker.patch("logging.basicConfig")
        _configure_logging(False)
        mock_basic.assert_called_once()
        call_kwargs = mock_basic.call_args[1]
        assert call_kwargs["level"] == logging.INFO

    def test_verbose_true_suppresses_urllib3_logger(self):
        """_configure_logging(True) should keep urllib3 at WARNING even in verbose mode."""
        import logging

        from rots.cli import _configure_logging

        _configure_logging(True)
        assert logging.getLogger("urllib3").level == logging.WARNING
        # Restore
        logging.getLogger().setLevel(logging.WARNING)

    def test_verbose_false_suppresses_urllib3_logger(self):
        """_configure_logging(False) should keep urllib3 at WARNING."""
        import logging

        from rots.cli import _configure_logging

        _configure_logging(False)
        assert logging.getLogger("urllib3").level == logging.WARNING


class TestAppMeta:
    """Test _meta() entry point and --verbose flag handling.

    _meta is the registered handler for app.meta.default. It accepts a
    *tokens variadic arg plus --verbose flag, calls _configure_logging, then
    routes tokens to app(). Tests call _meta() directly to avoid the cyclopts
    meta dispatch layer, which requires a Rich Console at parse time.
    """

    def test_meta_verbose_true_calls_configure_logging_with_true(self, mocker):
        """_meta(*tokens, verbose=True) should call _configure_logging(True)."""
        from rots.cli import _meta

        mock_configure = mocker.patch("rots.cli._configure_logging")
        mocker.patch("rots.cli.app")

        _meta("version", verbose=True)

        mock_configure.assert_called_once_with(True)

    def test_meta_verbose_false_calls_configure_logging_with_false(self, mocker):
        """_meta(*tokens, verbose=False) should call _configure_logging(False)."""
        from rots.cli import _meta

        mock_configure = mocker.patch("rots.cli._configure_logging")
        mocker.patch("rots.cli.app")

        _meta("version", verbose=False)

        mock_configure.assert_called_once_with(False)

    def test_meta_routes_tokens_to_app(self, mocker):
        """_meta(*tokens) should pass tokens tuple to app()."""
        from rots.cli import _meta

        mocker.patch("rots.cli._configure_logging")
        mock_app = mocker.patch("rots.cli.app")

        _meta("version", verbose=False)

        mock_app.assert_called_once_with(("version",))

    def test_meta_default_verbose_is_false(self, mocker):
        """_meta() default: verbose=False, so _configure_logging(False) is used."""
        from rots.cli import _meta

        mock_configure = mocker.patch("rots.cli._configure_logging")
        mocker.patch("rots.cli.app")

        _meta()  # No verbose arg - defaults to False

        mock_configure.assert_called_once_with(False)

    def test_app_meta_with_no_args_shows_help(self, capsys):
        """app.meta with no args should show help output (may exit 0)."""
        from rots.cli import app

        # app.meta() with empty tokens routes to _default which shows help
        try:
            app.meta([])
        except SystemExit:
            pass  # Help output may exit 0 - that's fine

        captured = capsys.readouterr()
        # Help output should contain something meaningful
        assert "ots" in captured.out.lower() or "usage" in captured.out.lower() or captured.out
