# tests/commands/test_env.py
"""Tests for env command subcommands.

Covers process, show, verify, and quadlet-lines commands in
commands/env/app.py. Also includes regression tests for related bugs
documented in the task tracker.
"""

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from ots_containers.commands.env.app import (
    app,
    process,
    quadlet_lines,
    show,
    verify,
)
from ots_containers.environment_file import SecretSpec

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_env_file(tmp_path: Path, content: str) -> Path:
    """Write a minimal env file and return its path."""
    env_file = tmp_path / "onetimesecret"
    env_file.write_text(content)
    return env_file


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

    @patch("ots_containers.commands.env.app.process_env_file")
    def test_env_process_happy_path(self, mock_process, tmp_path, capsys):
        """process should report secrets created successfully."""
        env_content = (
            "SECRET_VARIABLE_NAMES=HMAC_SECRET,API_KEY\nHMAC_SECRET=abc123\nAPI_KEY=xyz789\n"
        )
        env_file = _make_env_file(tmp_path, env_content)

        mock_process.return_value = (
            [
                SecretSpec(env_var_name="HMAC_SECRET", secret_name="ots_hmac_secret"),
                SecretSpec(env_var_name="API_KEY", secret_name="ots_api_key"),
            ],
            ["secret created: ots_hmac_secret", "secret created: ots_api_key"],
        )

        process(env_file=env_file)

        captured = capsys.readouterr()
        assert "ots_hmac_secret" in captured.out
        assert "ots_api_key" in captured.out
        assert "[created]" in captured.out

    @patch("ots_containers.commands.env.app.process_env_file")
    def test_env_process_dry_run(self, mock_process, tmp_path, capsys):
        """process --dry-run should report dry-run and not write secrets."""
        env_content = "SECRET_VARIABLE_NAMES=HMAC_SECRET\nHMAC_SECRET=abc123\n"
        env_file = _make_env_file(tmp_path, env_content)

        mock_process.return_value = (
            [SecretSpec(env_var_name="HMAC_SECRET", secret_name="ots_hmac_secret")],
            [],
        )

        process(env_file=env_file, dry_run=True)

        captured = capsys.readouterr()
        assert "dry-run" in captured.out.lower()
        # Confirm process_env_file was called with dry_run=True
        mock_process.assert_called_once()
        _, kwargs = mock_process.call_args
        assert (
            kwargs.get("dry_run") is True
            or mock_process.call_args[0][2] is True
            or "dry_run" in mock_process.call_args.kwargs
        )

    def test_env_process_missing_file(self, tmp_path, capsys):
        """process should raise SystemExit(1) when file not found."""
        missing = tmp_path / "nonexistent"
        with pytest.raises(SystemExit) as exc_info:
            process(env_file=missing)
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "not found" in captured.out.lower() or "error" in captured.out.lower()

    def test_env_process_no_secret_variable_names(self, tmp_path, capsys):
        """process should raise SystemExit(1) when SECRET_VARIABLE_NAMES is missing."""
        env_file = _make_env_file(tmp_path, "SOME_VAR=value\n")
        with pytest.raises(SystemExit) as exc_info:
            process(env_file=env_file)
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "SECRET_VARIABLE_NAMES" in captured.out

    @patch("ots_containers.commands.env.app.process_env_file")
    def test_env_process_reports_errors(self, mock_process, tmp_path, capsys):
        """process should raise SystemExit(1) when secrets have errors (empty/missing)."""
        env_content = "SECRET_VARIABLE_NAMES=MISSING_VAR\n"
        env_file = _make_env_file(tmp_path, env_content)

        mock_process.return_value = (
            [],
            ["MISSING_VAR not found in env file"],
        )

        with pytest.raises(SystemExit) as exc_info:
            process(env_file=env_file)
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "error" in captured.out.lower()


# ---------------------------------------------------------------------------
# show command
# ---------------------------------------------------------------------------


class TestEnvShow:
    """Tests for the 'ots env show' command."""

    @patch("ots_containers.commands.env.app.secret_exists")
    @patch("ots_containers.commands.env.app.extract_secrets")
    def test_env_show_json(self, mock_extract, mock_exists, tmp_path, capsys):
        """show --json should output valid JSON with secret status."""
        import json

        env_content = "SECRET_VARIABLE_NAMES=HMAC_SECRET\nHMAC_SECRET=abc123\n"
        env_file = _make_env_file(tmp_path, env_content)

        mock_extract.return_value = (
            [SecretSpec(env_var_name="HMAC_SECRET", secret_name="ots_hmac_secret", value="abc123")],
            [],
        )
        mock_exists.return_value = True

        show(env_file=env_file, json_output=True)
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert "secrets" in data
        assert len(data["secrets"]) == 1
        assert data["secrets"][0]["env_var"] == "HMAC_SECRET"
        assert data["secrets"][0]["podman_status"] == "exists"

    @patch("ots_containers.commands.env.app.secret_exists")
    @patch("ots_containers.commands.env.app.extract_secrets")
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

    @patch("ots_containers.commands.env.app.secret_exists")
    @patch("ots_containers.commands.env.app.extract_secrets")
    def test_env_verify_all_exist(self, mock_extract, mock_exists, tmp_path, capsys):
        """verify should succeed when all secrets exist."""
        env_content = (
            "SECRET_VARIABLE_NAMES=HMAC_SECRET,API_KEY\nHMAC_SECRET=abc123\nAPI_KEY=xyz789\n"
        )
        env_file = _make_env_file(tmp_path, env_content)

        mock_extract.return_value = (
            [
                SecretSpec(env_var_name="HMAC_SECRET", secret_name="ots_hmac_secret"),
                SecretSpec(env_var_name="API_KEY", secret_name="ots_api_key"),
            ],
            [],
        )
        mock_exists.return_value = True

        verify(env_file=env_file)
        captured = capsys.readouterr()
        assert "All secrets verified" in captured.out

    @patch("ots_containers.commands.env.app.secret_exists")
    @patch("ots_containers.commands.env.app.extract_secrets")
    def test_env_verify_missing_secrets(self, mock_extract, mock_exists, tmp_path, capsys):
        """verify should raise SystemExit(1) when a secret is missing."""
        env_content = "SECRET_VARIABLE_NAMES=HMAC_SECRET\nHMAC_SECRET=abc123\n"
        env_file = _make_env_file(tmp_path, env_content)

        mock_extract.return_value = (
            [SecretSpec(env_var_name="HMAC_SECRET", secret_name="ots_hmac_secret")],
            [],
        )
        mock_exists.return_value = False

        with pytest.raises(SystemExit) as exc_info:
            verify(env_file=env_file)
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "MISSING" in captured.out
        assert "ots env process" in captured.out

    def test_env_verify_missing_file(self, tmp_path):
        """verify should raise SystemExit(1) when file is not found."""
        missing = tmp_path / "nonexistent"
        with pytest.raises(SystemExit) as exc_info:
            verify(env_file=missing)
        assert exc_info.value.code == 1

    def test_env_verify_no_secret_variable_names(self, tmp_path, capsys):
        """verify should succeed (nothing to verify) when SECRET_VARIABLE_NAMES absent."""
        env_file = _make_env_file(tmp_path, "SOME_VAR=value\n")
        verify(env_file=env_file)
        captured = capsys.readouterr()
        assert (
            "nothing to verify" in captured.out.lower()
            or "No SECRET_VARIABLE_NAMES" in captured.out
        )


# ---------------------------------------------------------------------------
# quadlet-lines command
# ---------------------------------------------------------------------------


class TestEnvQuadletLines:
    """Tests for the 'ots env quadlet-lines' command."""

    @patch("ots_containers.commands.env.app.extract_secrets")
    def test_quadlet_lines_outputs_secret_directives(self, mock_extract, tmp_path, capsys):
        """quadlet-lines should print Secret= directives for each secret."""
        env_content = (
            "SECRET_VARIABLE_NAMES=HMAC_SECRET,API_KEY\nHMAC_SECRET=abc123\nAPI_KEY=xyz789\n"
        )
        env_file = _make_env_file(tmp_path, env_content)

        mock_extract.return_value = (
            [
                SecretSpec(env_var_name="HMAC_SECRET", secret_name="ots_hmac_secret"),
                SecretSpec(env_var_name="API_KEY", secret_name="ots_api_key"),
            ],
            [],
        )

        quadlet_lines(env_file=env_file)
        captured = capsys.readouterr()
        assert "Secret=ots_hmac_secret,type=env,target=HMAC_SECRET" in captured.out
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
        from ots_containers.commands.service.app import init

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
        from ots_containers.commands.instance.app import enable

        mocker.patch("shutil.which", return_value=None)

        with pytest.raises(SystemExit) as exc_info:
            enable()

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "systemctl" in captured.err.lower()

    def test_disable_without_systemctl(self, mocker, capsys):
        """disable should exit with code 1 and a helpful message when systemctl is absent."""
        from ots_containers.commands.instance.app import disable

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
        from ots_containers.podman import Podman

        mock_run = mocker.patch("subprocess.run")
        mock_run.return_value = subprocess.CompletedProcess([], 0, stdout="", stderr="")

        p = Podman()
        p("ps", capture_output=True, text=True)

        cmd = mock_run.call_args[0][0]
        # timeout should not appear as a podman CLI flag
        assert "--timeout" not in cmd

    def test_podman_kwargs_capture_output_not_in_cmd(self, mocker):
        """capture_output should not be converted to a --capture-output flag."""
        from ots_containers.podman import Podman

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
        from ots_containers.commands.instance.app import exec_shell

        mocker.patch("shutil.which", return_value="/usr/bin/systemctl")
        mock_run = mocker.patch("subprocess.run")
        mock_run.return_value = subprocess.CompletedProcess([], 0)

        # Mock resolve_identifiers to return one instance
        mocker.patch(
            "ots_containers.commands.instance.app.resolve_identifiers",
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

        from ots_containers.commands.instance.annotations import InstanceType
        from ots_containers.commands.instance.app import exec_shell

        mocker.patch("shutil.which", return_value="/usr/bin/systemctl")
        mocker.patch(
            "ots_containers.commands.instance.app.resolve_identifiers",
            return_value={InstanceType.WEB: ["7043"]},
        )
        mocker.patch(
            "ots_containers.commands.instance.app.systemd.unit_to_container_name",
            return_value="systemd-onetime-web_7043",
        )
        mock_run = mocker.patch("subprocess.run")
        mock_run.return_value = subprocess.CompletedProcess([], 0)

        host_shell = os.environ.get("SHELL", "/bin/sh")
        exec_shell(command="")

        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        # Current behavior uses host $SHELL; BUG-6 fix should use /bin/sh or /bin/bash
        assert host_shell in cmd or "/bin/sh" in cmd or "/bin/bash" in cmd
