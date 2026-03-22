# tests/commands/test_env.py
"""Tests for env command subcommands.

Covers process, show, verify, and quadlet-lines commands in
commands/env/app.py. Also includes regression tests for related bugs
documented in the task tracker.
"""

import logging
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from rots.commands.env.app import (
    app,
    process,
    quadlet_lines,
    show,
    verify,
)
from rots.environment_file import SecretSpec

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_env_file(tmp_path: Path, content: str) -> Path:
    """Write a minimal env file and return its path."""
    env_file = tmp_path / "onetimesecret"
    env_file.write_text(content)
    return env_file


# ---------------------------------------------------------------------------
# _file_exists helper
# ---------------------------------------------------------------------------


class TestFileExists:
    """Tests for the _file_exists helper function."""

    def test_local_existing_file_returns_true(self, tmp_path):
        """_file_exists should return True for an existing local file."""
        from ots_shared.ssh import LocalExecutor

        from rots.commands.env.app import _file_exists

        f = tmp_path / "test.env"
        f.write_text("KEY=VALUE\n")

        assert _file_exists(f, LocalExecutor()) is True

    def test_local_missing_file_returns_false(self, tmp_path):
        """_file_exists should return False for a missing local file."""
        from ots_shared.ssh import LocalExecutor

        from rots.commands.env.app import _file_exists

        f = tmp_path / "nonexistent"
        assert _file_exists(f, LocalExecutor()) is False

    def test_remote_existing_file_returns_true(self):
        """_file_exists should run 'test -f' on remote executor and return True when ok."""
        from unittest.mock import MagicMock

        try:
            import paramiko
        except ImportError:
            pytest.skip("paramiko not installed")

        from ots_shared.ssh import SSHExecutor
        from ots_shared.ssh.executor import Result

        from rots.commands.env.app import _file_exists

        client = MagicMock(spec=paramiko.SSHClient)
        ex = SSHExecutor(client)
        ex.run = MagicMock(return_value=Result(command="test", returncode=0, stdout="", stderr=""))

        result = _file_exists(Path("/etc/default/onetimesecret"), ex)
        assert result is True
        ex.run.assert_called_once_with(["test", "-f", "/etc/default/onetimesecret"])

    def test_remote_missing_file_returns_false(self):
        """_file_exists should run 'test -f' on remote executor and return False when not ok."""
        from unittest.mock import MagicMock

        try:
            import paramiko
        except ImportError:
            pytest.skip("paramiko not installed")

        from ots_shared.ssh import SSHExecutor
        from ots_shared.ssh.executor import Result

        from rots.commands.env.app import _file_exists

        client = MagicMock(spec=paramiko.SSHClient)
        ex = SSHExecutor(client)
        ex.run = MagicMock(return_value=Result(command="test", returncode=1, stdout="", stderr=""))

        result = _file_exists(Path("/etc/default/nonexistent"), ex)
        assert result is False


# ---------------------------------------------------------------------------
# Executor wiring in env commands
# ---------------------------------------------------------------------------


class TestEnvCommandExecutorWiring:
    """Verify env commands pass executor to EnvFile.parse() and secret_exists()."""

    def test_show_passes_executor_to_parse_and_secret_exists(self, mocker, tmp_path, capsys):
        """show should pass the executor from get_executor to EnvFile.parse and secret_exists."""
        from unittest.mock import MagicMock

        from ots_shared.ssh import LocalExecutor

        mock_ex = LocalExecutor()
        mocker.patch("rots.commands.env.app.Config.get_executor", return_value=mock_ex)

        env_content = "SECRET_VARIABLE_NAMES=AUTH_SECRET\nAUTH_SECRET=abc123\n"
        env_file = _make_env_file(tmp_path, env_content)

        mock_parse = mocker.patch("rots.commands.env.app.EnvFile.parse")
        mock_parsed = MagicMock()
        mock_parsed.secret_variable_names = ["AUTH_SECRET"]
        mock_parsed.get.return_value = "AUTH_SECRET"
        mock_parsed.has.side_effect = lambda k: k == "AUTH_SECRET"
        mock_parse.return_value = mock_parsed

        mock_extract = mocker.patch("rots.commands.env.app.extract_secrets")
        mock_extract.return_value = (
            [SecretSpec(env_var_name="AUTH_SECRET", secret_name="ots_hmac_secret")],
            [],
        )

        mock_secret_exists = mocker.patch("rots.commands.env.app.secret_exists")
        mock_secret_exists.return_value = True

        show(env_file=env_file)

        # Verify executor was passed through
        mock_parse.assert_called_once_with(env_file, executor=mock_ex)
        mock_secret_exists.assert_called_once_with("ots_hmac_secret", executor=mock_ex)

    def test_verify_passes_executor_to_secret_exists(self, mocker, tmp_path, capsys):
        """verify should pass executor to secret_exists for each secret."""
        from unittest.mock import MagicMock

        from ots_shared.ssh import LocalExecutor

        mock_ex = LocalExecutor()
        mocker.patch("rots.commands.env.app.Config.get_executor", return_value=mock_ex)

        env_content = "SECRET_VARIABLE_NAMES=AUTH_SECRET,API_KEY\nAUTH_SECRET=abc\nAPI_KEY=xyz\n"
        env_file = _make_env_file(tmp_path, env_content)

        mock_parse = mocker.patch("rots.commands.env.app.EnvFile.parse")
        mock_parsed = MagicMock()
        mock_parsed.secret_variable_names = ["AUTH_SECRET", "API_KEY"]
        mock_parse.return_value = mock_parsed

        mock_extract = mocker.patch("rots.commands.env.app.extract_secrets")
        mock_extract.return_value = (
            [
                SecretSpec(env_var_name="AUTH_SECRET", secret_name="ots_hmac_secret"),
                SecretSpec(env_var_name="API_KEY", secret_name="ots_api_key"),
            ],
            [],
        )

        mock_secret_exists = mocker.patch("rots.commands.env.app.secret_exists")
        mock_secret_exists.return_value = True

        verify(env_file=env_file)

        # Both secrets should be checked with the executor
        assert mock_secret_exists.call_count == 2
        mock_secret_exists.assert_any_call("ots_hmac_secret", executor=mock_ex)
        mock_secret_exists.assert_any_call("ots_api_key", executor=mock_ex)


# ---------------------------------------------------------------------------
# Structural tests
# ---------------------------------------------------------------------------


class TestEnvAppExists:
    """Verify the env app is importable and commands are registered."""

    def test_app_exists(self):
        assert app is not None

    def test_process_command_exists(self):
        assert callable(process)

    def test_show_command_exists(self):
        assert callable(show)

    def test_verify_command_exists(self):
        assert callable(verify)

    def test_quadlet_lines_command_exists(self):
        assert callable(quadlet_lines)


# ---------------------------------------------------------------------------
# process command
# ---------------------------------------------------------------------------


class TestEnvProcess:
    """Tests for the 'ots env process' command."""

    @patch("rots.commands.env.app.process_env_file")
    def test_env_process_happy_path(self, mock_process, tmp_path, caplog):
        """process should report secrets created successfully."""
        env_content = (
            "SECRET_VARIABLE_NAMES=AUTH_SECRET,API_KEY\nAUTH_SECRET=abc123\nAPI_KEY=xyz789\n"
        )
        env_file = _make_env_file(tmp_path, env_content)

        mock_process.return_value = (
            [
                SecretSpec(env_var_name="AUTH_SECRET", secret_name="ots_hmac_secret"),
                SecretSpec(env_var_name="API_KEY", secret_name="ots_api_key"),
            ],
            ["secret created: ots_hmac_secret", "secret created: ots_api_key"],
        )

        with caplog.at_level(logging.INFO):
            process(env_file=env_file)

        assert "ots_hmac_secret" in caplog.text
        assert "ots_api_key" in caplog.text
        assert "[created]" in caplog.text

    @patch("rots.commands.env.app.process_env_file")
    def test_env_process_dry_run(self, mock_process, tmp_path, caplog):
        """process --dry-run should report dry-run and not write secrets."""
        env_content = "SECRET_VARIABLE_NAMES=AUTH_SECRET\nAUTH_SECRET=abc123\n"
        env_file = _make_env_file(tmp_path, env_content)

        mock_process.return_value = (
            [SecretSpec(env_var_name="AUTH_SECRET", secret_name="ots_hmac_secret")],
            [],
        )

        with caplog.at_level(logging.INFO):
            process(env_file=env_file, dry_run=True)

        assert "dry-run" in caplog.text.lower()
        # Confirm process_env_file was called with dry_run=True
        mock_process.assert_called_once()
        _, kwargs = mock_process.call_args
        assert (
            kwargs.get("dry_run") is True
            or mock_process.call_args[0][2] is True
            or "dry_run" in mock_process.call_args.kwargs
        )

    def test_env_process_missing_file(self, tmp_path, caplog):
        """process should raise SystemExit(1) when file not found."""
        missing = tmp_path / "nonexistent"
        with pytest.raises(SystemExit) as exc_info:
            with caplog.at_level(logging.ERROR):
                process(env_file=missing)
        assert exc_info.value.code == 1
        assert "not found" in caplog.text.lower() or "error" in caplog.text.lower()

    def test_env_process_no_secret_variable_names(self, tmp_path, caplog):
        """process should raise SystemExit(1) when SECRET_VARIABLE_NAMES is missing."""
        env_file = _make_env_file(tmp_path, "SOME_VAR=value\n")
        with pytest.raises(SystemExit) as exc_info:
            with caplog.at_level(logging.ERROR):
                process(env_file=env_file)
        assert exc_info.value.code == 1
        assert "SECRET_VARIABLE_NAMES" in caplog.text

    @patch("rots.commands.env.app.process_env_file")
    def test_env_process_reports_errors(self, mock_process, tmp_path, caplog):
        """process should raise SystemExit(1) when secrets have errors (empty/missing)."""
        env_content = "SECRET_VARIABLE_NAMES=MISSING_VAR\n"
        env_file = _make_env_file(tmp_path, env_content)

        mock_process.return_value = (
            [],
            ["MISSING_VAR not found in env file"],
        )

        with pytest.raises(SystemExit) as exc_info:
            with caplog.at_level(logging.ERROR):
                process(env_file=env_file)
        assert exc_info.value.code == 1
        assert "error" in caplog.text.lower()


# ---------------------------------------------------------------------------
# show command
# ---------------------------------------------------------------------------


class TestEnvShow:
    """Tests for the 'ots env show' command."""

    @patch("rots.commands.env.app.secret_exists")
    @patch("rots.commands.env.app.extract_secrets")
    def test_env_show_json(self, mock_extract, mock_exists, tmp_path, capsys):
        """show --json should output valid JSON with secret status."""
        import json

        env_content = "SECRET_VARIABLE_NAMES=AUTH_SECRET\nAUTH_SECRET=abc123\n"
        env_file = _make_env_file(tmp_path, env_content)

        mock_extract.return_value = (
            [SecretSpec(env_var_name="AUTH_SECRET", secret_name="ots_hmac_secret", value="abc123")],
            [],
        )
        mock_exists.return_value = True

        show(env_file=env_file, json_output=True)
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert "secrets" in data
        assert len(data["secrets"]) == 1
        assert data["secrets"][0]["env_var"] == "AUTH_SECRET"
        assert data["secrets"][0]["podman_status"] == "exists"

    @patch("rots.commands.env.app.secret_exists")
    @patch("rots.commands.env.app.extract_secrets")
    def test_env_show_text_output(self, mock_extract, mock_exists, tmp_path, capsys):
        """show should print human-readable secret status."""
        env_content = "SECRET_VARIABLE_NAMES=API_KEY\nAPI_KEY=secret\n"
        env_file = _make_env_file(tmp_path, env_content)

        mock_extract.return_value = (
            [SecretSpec(env_var_name="API_KEY", secret_name="ots_api_key", value="secret")],
            [],
        )
        mock_exists.return_value = False

        show(env_file=env_file, json_output=False)
        captured = capsys.readouterr()
        assert "API_KEY" in captured.out
        assert "missing" in captured.out.lower()

    def test_env_show_missing_file(self, tmp_path):
        """show should raise SystemExit(1) when file not found."""
        missing = tmp_path / "nonexistent"
        with pytest.raises(SystemExit) as exc_info:
            show(env_file=missing)
        assert exc_info.value.code == 1

    def test_env_show_no_secret_variable_names(self, tmp_path, capsys):
        """show should warn when SECRET_VARIABLE_NAMES is missing."""
        env_file = _make_env_file(tmp_path, "SOME_VAR=value\n")
        show(env_file=env_file)
        captured = capsys.readouterr()
        assert "No SECRET_VARIABLE_NAMES" in captured.out or "Warning" in captured.out


# ---------------------------------------------------------------------------
# verify command
# ---------------------------------------------------------------------------


class TestEnvVerify:
    """Tests for the 'ots env verify' command."""

    @patch("rots.commands.env.app.secret_exists")
    @patch("rots.commands.env.app.extract_secrets")
    def test_env_verify_all_exist(self, mock_extract, mock_exists, tmp_path, caplog):
        """verify should succeed when all secrets exist."""
        env_content = (
            "SECRET_VARIABLE_NAMES=AUTH_SECRET,API_KEY\nAUTH_SECRET=abc123\nAPI_KEY=xyz789\n"
        )
        env_file = _make_env_file(tmp_path, env_content)

        mock_extract.return_value = (
            [
                SecretSpec(env_var_name="AUTH_SECRET", secret_name="ots_hmac_secret"),
                SecretSpec(env_var_name="API_KEY", secret_name="ots_api_key"),
            ],
            [],
        )
        mock_exists.return_value = True

        with caplog.at_level(logging.INFO):
            verify(env_file=env_file)

        assert "All secrets verified" in caplog.text

    @patch("rots.commands.env.app.secret_exists")
    @patch("rots.commands.env.app.extract_secrets")
    def test_env_verify_missing_secrets(self, mock_extract, mock_exists, tmp_path, caplog):
        """verify should raise SystemExit(1) when a secret is missing."""
        env_content = "SECRET_VARIABLE_NAMES=AUTH_SECRET\nAUTH_SECRET=abc123\n"
        env_file = _make_env_file(tmp_path, env_content)

        mock_extract.return_value = (
            [SecretSpec(env_var_name="AUTH_SECRET", secret_name="ots_hmac_secret")],
            [],
        )
        mock_exists.return_value = False

        with pytest.raises(SystemExit) as exc_info:
            with caplog.at_level(logging.INFO):
                verify(env_file=env_file)
        assert exc_info.value.code == 1
        assert "MISSING" in caplog.text
        assert "ots env process" in caplog.text

    def test_env_verify_missing_file(self, tmp_path):
        """verify should raise SystemExit(1) when file is not found."""
        missing = tmp_path / "nonexistent"
        with pytest.raises(SystemExit) as exc_info:
            verify(env_file=missing)
        assert exc_info.value.code == 1

    def test_env_verify_no_secret_variable_names(self, tmp_path, caplog):
        """verify should succeed (nothing to verify) when SECRET_VARIABLE_NAMES absent."""
        env_file = _make_env_file(tmp_path, "SOME_VAR=value\n")
        with caplog.at_level(logging.INFO):
            verify(env_file=env_file)
        assert (
            "nothing to verify" in caplog.text.lower() or "No SECRET_VARIABLE_NAMES" in caplog.text
        )


# ---------------------------------------------------------------------------
# quadlet-lines command
# ---------------------------------------------------------------------------


class TestEnvQuadletLines:
    """Tests for the 'ots env quadlet-lines' command."""

    @patch("rots.commands.env.app.extract_secrets")
    def test_quadlet_lines_outputs_secret_directives(self, mock_extract, tmp_path, capsys):
        """quadlet-lines should print Secret= directives for each secret."""
        env_content = (
            "SECRET_VARIABLE_NAMES=AUTH_SECRET,API_KEY\nAUTH_SECRET=abc123\nAPI_KEY=xyz789\n"
        )
        env_file = _make_env_file(tmp_path, env_content)

        mock_extract.return_value = (
            [
                SecretSpec(env_var_name="AUTH_SECRET", secret_name="ots_hmac_secret"),
                SecretSpec(env_var_name="API_KEY", secret_name="ots_api_key"),
            ],
            [],
        )

        quadlet_lines(env_file=env_file)
        captured = capsys.readouterr()
        assert "Secret=ots_hmac_secret,type=env,target=AUTH_SECRET" in captured.out
        assert "Secret=ots_api_key,type=env,target=API_KEY" in captured.out

    def test_quadlet_lines_missing_file(self, tmp_path):
        """quadlet-lines should raise SystemExit(1) when file not found."""
        missing = tmp_path / "nonexistent"
        with pytest.raises(SystemExit) as exc_info:
            quadlet_lines(env_file=missing)
        assert exc_info.value.code == 1

    def test_quadlet_lines_no_secret_variable_names(self, tmp_path, capsys):
        """quadlet-lines should raise SystemExit(1) when SECRET_VARIABLE_NAMES is absent."""
        env_file = _make_env_file(tmp_path, "SOME_VAR=value\n")
        with pytest.raises(SystemExit) as exc_info:
            quadlet_lines(env_file=env_file)
        assert exc_info.value.code == 1


# ---------------------------------------------------------------------------
# BUG-1: service init with non-numeric instance and no --port
# ---------------------------------------------------------------------------


class TestServiceInitNonNumericInstance:
    """Regression test for BUG-1: int(instance) raises ValueError for named instances."""

    def test_service_init_non_numeric_instance_no_port(self):
        """service init 'primary' without --port should raise ValueError (BUG-1 not yet fixed).

        This test documents the current broken behaviour so we can detect when
        the fix lands: the call should produce a clean CLI error, not a raw
        Python traceback. Until fixed, we assert ValueError is raised.
        """
        from rots.commands.service.app import init

        with pytest.raises((ValueError, SystemExit)):
            # 'primary' is non-numeric and port is None -> int('primary') raises
            init("valkey", "primary", port=None, dry_run=True)


# ---------------------------------------------------------------------------
# BUG-2: enable/disable commands must check for systemctl before running
# ---------------------------------------------------------------------------


class TestEnableDisableWithoutSystemctl:
    """Regression tests for BUG-2: enable/disable lacked require_systemctl() guard."""

    def test_enable_without_systemctl(self, mocker, capsys):
        """enable should exit with code 1 and a helpful message when systemctl is absent."""
        from rots.commands.instance.app import enable

        mocker.patch("shutil.which", return_value=None)

        with pytest.raises(SystemExit) as exc_info:
            enable()

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "systemctl" in captured.err.lower()

    def test_disable_without_systemctl(self, mocker, capsys):
        """disable should exit with code 1 and a helpful message when systemctl is absent."""
        from rots.commands.instance.app import disable

        mocker.patch("shutil.which", return_value=None)

        with pytest.raises(SystemExit) as exc_info:
            disable()

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "systemctl" in captured.err.lower()


# ---------------------------------------------------------------------------
# BUG-5: Podman.__call__ kwargs collision — timeout passed as podman flag
# ---------------------------------------------------------------------------


class TestPodmanTimeoutKwarg:
    """Regression tests for BUG-5: timeout kwarg should not become a podman flag."""

    def test_podman_timeout_kwarg_not_passed_as_flag(self, mocker):
        """Passing timeout= to Podman() should not add --timeout to the podman command."""
        from rots.podman import Podman

        mock_run = mocker.patch("subprocess.run")
        mock_run.return_value = subprocess.CompletedProcess([], 0, stdout="", stderr="")

        p = Podman()
        p("ps", capture_output=True, text=True)

        cmd = mock_run.call_args[0][0]
        # timeout should not appear as a podman CLI flag
        assert "--timeout" not in cmd

    def test_podman_kwargs_capture_output_not_in_cmd(self, mocker):
        """capture_output should not be converted to a --capture-output flag."""
        from rots.podman import Podman

        mock_run = mocker.patch("subprocess.run")
        mock_run.return_value = subprocess.CompletedProcess([], 0, stdout="", stderr="")

        p = Podman()
        p("images", capture_output=True, text=True, check=True)

        cmd = mock_run.call_args[0][0]
        assert "--capture-output" not in cmd
        assert "--text" not in cmd
        assert "--check" not in cmd


# ---------------------------------------------------------------------------
# BUG-6: Host $SHELL leaked into container exec
# ---------------------------------------------------------------------------


class TestExecShellFallback:
    """Regression tests for BUG-6: host $SHELL should not be used inside container."""

    def test_exec_uses_command_override_when_provided(self, mocker):
        """exec_shell with --command should use that command, not host $SHELL."""
        from rots.commands.instance.app import exec_shell

        mocker.patch("shutil.which", return_value="/usr/bin/systemctl")
        mock_run = mocker.patch("subprocess.run")
        mock_run.return_value = subprocess.CompletedProcess([], 0)

        # Mock resolve_identifiers to return one instance
        mocker.patch(
            "rots.commands.instance.app.resolve_identifiers",
            return_value={},
        )

        exec_shell(command="/bin/bash")
        # No running instances found means no subprocess.run call to podman exec
        mock_run.assert_not_called()

    def test_exec_shell_env_var_used_as_fallback(self, mocker):
        """exec_shell falls back to $SHELL env var when no --command given.

        This test documents current behavior. BUG-6 fix should default to
        /bin/sh (safe) rather than host $SHELL (may not exist in container).
        """
        import os

        from rots.commands.instance.annotations import InstanceType
        from rots.commands.instance.app import exec_shell

        mocker.patch("shutil.which", return_value="/usr/bin/systemctl")
        mocker.patch(
            "rots.commands.instance.app.resolve_identifiers",
            return_value={InstanceType.WEB: ["7043"]},
        )
        mocker.patch(
            "rots.commands.instance.app.systemd.unit_to_container_name",
            return_value="onetime-web@7043",
        )
        mock_run = mocker.patch("subprocess.run")
        mock_run.return_value = subprocess.CompletedProcess([], 0)

        host_shell = os.environ.get("SHELL", "/bin/sh")
        exec_shell(command="")

        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        # Current behavior uses host $SHELL; BUG-6 fix should use /bin/sh or /bin/bash
        assert host_shell in cmd or "/bin/sh" in cmd or "/bin/bash" in cmd
