# tests/test_environment_file.py
"""Tests for environment_file module - env file parsing and secret management."""

from unittest.mock import MagicMock


class TestEnvVarToSecretName:
    """Test conversion between env var names and podman secret names."""

    def test_converts_to_lowercase_with_prefix(self):
        """Should convert UPPER_CASE to ots_lower_case."""
        from ots_containers.environment_file import env_var_to_secret_name

        assert env_var_to_secret_name("STRIPE_API_KEY") == "ots_stripe_api_key"
        assert env_var_to_secret_name("SECRET") == "ots_secret"
        assert env_var_to_secret_name("DB_PASSWORD") == "ots_db_password"


class TestSecretNameToEnvVar:
    """Test conversion from podman secret names back to env var names."""

    def test_converts_to_uppercase_without_prefix(self):
        """Should convert ots_lower_case to UPPER_CASE."""
        from ots_containers.environment_file import secret_name_to_env_var

        assert secret_name_to_env_var("ots_stripe_api_key") == "STRIPE_API_KEY"
        assert secret_name_to_env_var("ots_secret") == "SECRET"

    def test_handles_names_without_prefix(self):
        """Should handle names that don't have ots_ prefix."""
        from ots_containers.environment_file import secret_name_to_env_var

        assert secret_name_to_env_var("some_name") == "SOME_NAME"


class TestParseSecretVariableNames:
    """Test parsing SECRET_VARIABLE_NAMES values."""

    def test_comma_separated(self):
        """Should parse comma-separated list."""
        from ots_containers.environment_file import parse_secret_variable_names

        result = parse_secret_variable_names("VAR1,VAR2,VAR3")
        assert result == ["VAR1", "VAR2", "VAR3"]

    def test_space_separated_quoted(self):
        """Should parse space-separated quoted list."""
        from ots_containers.environment_file import parse_secret_variable_names

        result = parse_secret_variable_names('"VAR1 VAR2 VAR3"')
        assert result == ["VAR1", "VAR2", "VAR3"]

    def test_colon_separated_quoted(self):
        """Should parse colon-separated quoted list."""
        from ots_containers.environment_file import parse_secret_variable_names

        result = parse_secret_variable_names('"VAR1:VAR2:VAR3"')
        assert result == ["VAR1", "VAR2", "VAR3"]

    def test_single_value(self):
        """Should handle single value."""
        from ots_containers.environment_file import parse_secret_variable_names

        result = parse_secret_variable_names("SINGLE_VAR")
        assert result == ["SINGLE_VAR"]

    def test_empty_value(self):
        """Should return empty list for empty value."""
        from ots_containers.environment_file import parse_secret_variable_names

        assert parse_secret_variable_names("") == []
        assert parse_secret_variable_names("  ") == []

    def test_strips_whitespace(self):
        """Should strip whitespace from values."""
        from ots_containers.environment_file import parse_secret_variable_names

        result = parse_secret_variable_names("VAR1, VAR2 , VAR3")
        assert result == ["VAR1", "VAR2", "VAR3"]


class TestEnvFile:
    """Test EnvFile class."""

    def test_parse_simple_file(self, tmp_path):
        """Should parse simple key=value pairs."""
        from ots_containers.environment_file import EnvFile

        env_file = tmp_path / "test.env"
        env_file.write_text("KEY1=value1\nKEY2=value2\n")

        parsed = EnvFile.parse(env_file)

        assert parsed.get("KEY1") == "value1"
        assert parsed.get("KEY2") == "value2"

    def test_parse_preserves_comments(self, tmp_path):
        """Should preserve comment lines."""
        from ots_containers.environment_file import EnvFile

        env_file = tmp_path / "test.env"
        env_file.write_text("# This is a comment\nKEY=value\n")

        parsed = EnvFile.parse(env_file)
        parsed.write()

        content = env_file.read_text()
        assert "# This is a comment" in content

    def test_parse_handles_quoted_values(self, tmp_path):
        """Should strip quotes from values."""
        from ots_containers.environment_file import EnvFile

        env_file = tmp_path / "test.env"
        env_file.write_text("KEY1=\"quoted value\"\nKEY2='single quoted'\n")

        parsed = EnvFile.parse(env_file)

        assert parsed.get("KEY1") == "quoted value"
        assert parsed.get("KEY2") == "single quoted"

    def test_parse_empty_values(self, tmp_path):
        """Should handle empty values."""
        from ots_containers.environment_file import EnvFile

        env_file = tmp_path / "test.env"
        env_file.write_text("KEY1=\nKEY2=value\n")

        parsed = EnvFile.parse(env_file)

        assert parsed.get("KEY1") == ""
        assert parsed.has("KEY1")

    def test_parse_nonexistent_file(self, tmp_path):
        """Should return empty EnvFile for nonexistent file."""
        from ots_containers.environment_file import EnvFile

        parsed = EnvFile.parse(tmp_path / "nonexistent.env")

        assert parsed.get("KEY") == ""
        assert not parsed.has("KEY")

    def test_secret_variable_names_property(self, tmp_path):
        """Should parse SECRET_VARIABLE_NAMES from file."""
        from ots_containers.environment_file import EnvFile

        env_file = tmp_path / "test.env"
        env_file.write_text("SECRET_VARIABLE_NAMES=VAR1,VAR2\nVAR1=secret1\n")

        parsed = EnvFile.parse(env_file)

        assert parsed.secret_variable_names == ["VAR1", "VAR2"]

    def test_set_and_remove(self, tmp_path):
        """Should allow setting and removing variables."""
        from ots_containers.environment_file import EnvFile

        env_file = tmp_path / "test.env"
        env_file.write_text("KEY1=value1\n")

        parsed = EnvFile.parse(env_file)
        parsed.set("KEY2", "value2")
        parsed.remove("KEY1")

        assert not parsed.has("KEY1")
        assert parsed.get("KEY2") == "value2"

    def test_write_roundtrip(self, tmp_path):
        """Should preserve content through parse/write cycle."""
        from ots_containers.environment_file import EnvFile

        env_file = tmp_path / "test.env"
        original = "# Comment\nKEY1=value1\n\nKEY2=value2\n"
        env_file.write_text(original)

        parsed = EnvFile.parse(env_file)
        parsed.write()

        # Should preserve structure (though exact whitespace may vary)
        content = env_file.read_text()
        assert "# Comment" in content
        assert "KEY1=value1" in content
        assert "KEY2=value2" in content

    def test_rename_preserves_position(self, tmp_path):
        """Should rename variable in place, preserving its position."""
        from ots_containers.environment_file import EnvFile

        env_file = tmp_path / "test.env"
        env_file.write_text("FIRST=1\nMIDDLE=2\nLAST=3\n")

        parsed = EnvFile.parse(env_file)
        result = parsed.rename("MIDDLE", "_MIDDLE", "new_value")
        parsed.write()

        assert result is True
        assert not parsed.has("MIDDLE")
        assert parsed.get("_MIDDLE") == "new_value"

        # Verify position is preserved
        content = env_file.read_text()
        lines = content.strip().split("\n")
        assert lines[0] == "FIRST=1"
        assert lines[1] == "_MIDDLE=new_value"
        assert lines[2] == "LAST=3"

    def test_rename_nonexistent_returns_false(self, tmp_path):
        """Should return False when renaming nonexistent key."""
        from ots_containers.environment_file import EnvFile

        env_file = tmp_path / "test.env"
        env_file.write_text("KEY=value\n")

        parsed = EnvFile.parse(env_file)
        result = parsed.rename("NONEXISTENT", "_NONEXISTENT", "value")

        assert result is False


class TestSecretSpec:
    """Test SecretSpec dataclass."""

    def test_from_env_var(self):
        """Should create SecretSpec from env var name."""
        from ots_containers.environment_file import SecretSpec

        spec = SecretSpec.from_env_var("STRIPE_API_KEY", "sk_live_xxx")

        assert spec.env_var_name == "STRIPE_API_KEY"
        assert spec.secret_name == "ots_stripe_api_key"
        assert spec.value == "sk_live_xxx"

    def test_quadlet_line(self):
        """Should generate correct Secret= line."""
        from ots_containers.environment_file import SecretSpec

        spec = SecretSpec.from_env_var("STRIPE_API_KEY")

        assert spec.quadlet_line == "Secret=ots_stripe_api_key,type=env,target=STRIPE_API_KEY"


class TestIsProcessedSecretEntry:
    """Test detection of already-processed secret entries."""

    def test_detects_processed_entry(self):
        """Should detect _VARNAME=ots_varname pattern."""
        from ots_containers.environment_file import is_processed_secret_entry

        assert is_processed_secret_entry("_STRIPE_API_KEY", "ots_stripe_api_key")
        assert is_processed_secret_entry("_SECRET", "ots_secret")

    def test_rejects_unprocessed_entry(self):
        """Should reject entries without underscore prefix."""
        from ots_containers.environment_file import is_processed_secret_entry

        assert not is_processed_secret_entry("STRIPE_API_KEY", "sk_live_xxx")
        assert not is_processed_secret_entry("SECRET", "mysecret")

    def test_rejects_wrong_value(self):
        """Should reject entries where value doesn't match expected secret name."""
        from ots_containers.environment_file import is_processed_secret_entry

        assert not is_processed_secret_entry("_STRIPE_API_KEY", "wrong_value")


class TestExtractSecrets:
    """Test secret extraction from env files."""

    def test_extracts_unprocessed_secrets(self, tmp_path):
        """Should identify secrets that need processing."""
        from ots_containers.environment_file import EnvFile, extract_secrets

        env_file = tmp_path / "test.env"
        env_file.write_text(
            "SECRET_VARIABLE_NAMES=API_KEY,PASSWORD\nAPI_KEY=secret_value\nPASSWORD=pass123\n"
        )

        parsed = EnvFile.parse(env_file)
        secrets, _ = extract_secrets(parsed)

        assert len(secrets) == 2
        assert secrets[0].env_var_name == "API_KEY"
        assert secrets[0].value == "secret_value"
        assert secrets[1].env_var_name == "PASSWORD"
        assert secrets[1].value == "pass123"

    def test_recognizes_processed_secrets(self, tmp_path):
        """Should recognize already-processed secrets."""
        from ots_containers.environment_file import EnvFile, extract_secrets

        env_file = tmp_path / "test.env"
        env_file.write_text("SECRET_VARIABLE_NAMES=API_KEY\n_API_KEY=ots_api_key\n")

        parsed = EnvFile.parse(env_file)
        secrets, _ = extract_secrets(parsed)

        assert len(secrets) == 1
        assert secrets[0].env_var_name == "API_KEY"
        assert secrets[0].value is None  # Already processed, no value to extract

    def test_missing_variable_not_in_secrets_list(self, tmp_path):
        """Should NOT include missing variables in the secrets list.

        When SECRET_VARIABLE_NAMES lists a variable that doesn't exist in the
        env file at all (neither as VARNAME=value nor _VARNAME=secret_name),
        it should be excluded from secrets to avoid referencing non-existent
        podman secrets in the quadlet template.
        """
        from ots_containers.environment_file import EnvFile, extract_secrets

        env_file = tmp_path / "test.env"
        env_file.write_text("SECRET_VARIABLE_NAMES=MISSING_VAR\n")

        parsed = EnvFile.parse(env_file)
        secrets, messages = extract_secrets(parsed)

        assert len(secrets) == 0  # Missing variable should NOT be in secrets
        assert any("not found" in msg for msg in messages)

    def test_mixed_processed_unprocessed_and_missing(self, tmp_path):
        """Should handle a mix of processed, unprocessed, and missing secrets.

        Given SECRET_VARIABLE_NAMES with three vars:
        - PROCESSED_KEY: already processed (_PROCESSED_KEY=ots_processed_key)
        - RAW_KEY: unprocessed (RAW_KEY=raw_value)
        - GHOST_KEY: not in the file at all

        The secrets list should contain only the first two. The messages
        should contain a warning for the missing GHOST_KEY.
        """
        from ots_containers.environment_file import EnvFile, extract_secrets

        env_file = tmp_path / "test.env"
        env_file.write_text(
            "SECRET_VARIABLE_NAMES=PROCESSED_KEY,RAW_KEY,GHOST_KEY\n"
            "_PROCESSED_KEY=ots_processed_key\n"
            "RAW_KEY=raw_value\n"
        )

        parsed = EnvFile.parse(env_file)
        secrets, messages = extract_secrets(parsed)

        # Only the processed and unprocessed entries should appear
        assert len(secrets) == 2
        secret_names = [s.env_var_name for s in secrets]
        assert "PROCESSED_KEY" in secret_names
        assert "RAW_KEY" in secret_names
        assert "GHOST_KEY" not in secret_names

        # The processed one should have no value (already handled)
        processed = next(s for s in secrets if s.env_var_name == "PROCESSED_KEY")
        assert processed.value is None

        # The unprocessed one should carry its value
        raw = next(s for s in secrets if s.env_var_name == "RAW_KEY")
        assert raw.value == "raw_value"

        # Warning for the missing variable
        assert any("GHOST_KEY" in msg and "not found" in msg for msg in messages)


class TestProcessEnvFile:
    """Test full env file processing."""

    def test_dry_run_doesnt_modify(self, tmp_path):
        """Dry run should not modify the file."""
        from ots_containers.environment_file import EnvFile, process_env_file

        env_file = tmp_path / "test.env"
        original = "SECRET_VARIABLE_NAMES=API_KEY\nAPI_KEY=secret\n"
        env_file.write_text(original)

        parsed = EnvFile.parse(env_file)
        process_env_file(parsed, create_secrets=False, dry_run=True)

        assert env_file.read_text() == original

    def test_transforms_entries(self, tmp_path):
        """Should transform VARNAME=value to _VARNAME=ots_varname in place."""
        from ots_containers.environment_file import EnvFile, process_env_file

        env_file = tmp_path / "test.env"
        env_file.write_text(
            "SECRET_VARIABLE_NAMES=API_KEY\nBEFORE=keep\nAPI_KEY=secret\nAFTER=also_keep\n"
        )

        parsed = EnvFile.parse(env_file)
        process_env_file(parsed, create_secrets=False, dry_run=False)

        content = env_file.read_text()
        assert "API_KEY=secret" not in content
        assert "_API_KEY=ots_api_key" in content

        # Verify position is preserved (transformed entry stays in same location)
        lines = content.strip().split("\n")
        assert lines[1] == "BEFORE=keep"
        assert lines[2] == "_API_KEY=ots_api_key"
        assert lines[3] == "AFTER=also_keep"

    def test_no_changes_when_already_processed(self, tmp_path):
        """Should report no changes when secrets already processed."""
        from ots_containers.environment_file import EnvFile, process_env_file

        env_file = tmp_path / "test.env"
        env_file.write_text("SECRET_VARIABLE_NAMES=API_KEY\n_API_KEY=ots_api_key\n")

        parsed = EnvFile.parse(env_file)
        _, messages = process_env_file(parsed, create_secrets=False, dry_run=False)

        assert any("No changes needed" in msg for msg in messages)

    def test_reports_updated_when_changes_made(self, tmp_path):
        """Should report updated when changes were made."""
        from ots_containers.environment_file import EnvFile, process_env_file

        env_file = tmp_path / "test.env"
        env_file.write_text("SECRET_VARIABLE_NAMES=API_KEY\nAPI_KEY=secret\n")

        parsed = EnvFile.parse(env_file)
        _, messages = process_env_file(parsed, create_secrets=False, dry_run=False)

        assert any("Updated environment file" in msg for msg in messages)


class TestGenerateQuadletSecretLines:
    """Test quadlet Secret= line generation."""

    def test_generates_correct_format(self):
        """Should generate Secret= lines in correct format."""
        from ots_containers.environment_file import (
            SecretSpec,
            generate_quadlet_secret_lines,
        )

        secrets = [
            SecretSpec.from_env_var("API_KEY"),
            SecretSpec.from_env_var("PASSWORD"),
        ]

        result = generate_quadlet_secret_lines(secrets)

        assert "Secret=ots_api_key,type=env,target=API_KEY" in result
        assert "Secret=ots_password,type=env,target=PASSWORD" in result

    def test_empty_secrets_returns_empty_string(self):
        """Should return empty string (not None or whitespace) for no secrets."""
        from ots_containers.environment_file import generate_quadlet_secret_lines

        result = generate_quadlet_secret_lines([])

        assert result == ""
        assert isinstance(result, str)
        assert not result  # falsy


class TestGetSecretsFromEnvFile:
    """Test convenience function for getting secrets from env file."""

    def test_returns_secrets_without_modifying(self, tmp_path):
        """Should return secrets without modifying the file."""
        from ots_containers.environment_file import get_secrets_from_env_file

        env_file = tmp_path / "test.env"
        original = "SECRET_VARIABLE_NAMES=API_KEY\nAPI_KEY=secret\n"
        env_file.write_text(original)

        secrets = get_secrets_from_env_file(env_file)

        assert len(secrets) == 1
        assert secrets[0].env_var_name == "API_KEY"
        assert env_file.read_text() == original  # File unchanged


# =============================================================================
# Remote executor tests
# =============================================================================


def _make_ssh_executor(mocker):
    """Create a mock SSHExecutor that _is_remote() recognises as remote."""
    mock_ex = mocker.MagicMock()
    mocker.patch(
        "ots_containers.environment_file._is_remote",
        side_effect=lambda ex: ex is mock_ex,
    )
    return mock_ex


def _make_remote_result(stdout="", returncode=0):
    """Build a mock Result for executor.run()."""
    result = MagicMock()
    result.ok = returncode == 0
    result.stdout = stdout
    result.stderr = ""
    result.returncode = returncode
    return result


class TestEnvFileParseRemote:
    """Test EnvFile.parse() with remote executor."""

    def test_parse_reads_file_via_executor(self, mocker):
        from ots_containers.environment_file import EnvFile

        mock_ex = _make_ssh_executor(mocker)
        mock_ex.run.side_effect = [
            _make_remote_result(returncode=0),  # test -f
            _make_remote_result(stdout="HOST=example.com\nPORT=7043\n"),  # cat
        ]

        env_file = EnvFile.parse("/etc/onetimesecret/.env", executor=mock_ex)

        assert mock_ex.run.call_count == 2
        assert mock_ex.run.call_args_list[0][0][0] == ["test", "-f", "/etc/onetimesecret/.env"]
        assert mock_ex.run.call_args_list[1][0][0] == ["cat", "/etc/onetimesecret/.env"]
        assert env_file.get("HOST") == "example.com"
        assert env_file.get("PORT") == "7043"

    def test_parse_returns_empty_when_file_missing(self, mocker):
        from ots_containers.environment_file import EnvFile

        mock_ex = _make_ssh_executor(mocker)
        mock_ex.run.return_value = _make_remote_result(returncode=1)  # test -f fails

        env_file = EnvFile.parse("/nonexistent", executor=mock_ex)

        assert len(env_file.entries) == 0
        mock_ex.run.assert_called_once()


class TestEnvFileWriteRemote:
    """Test EnvFile.write() with remote executor."""

    def test_write_uses_tee_via_executor(self, mocker):
        from ots_containers.environment_file import EnvEntry, EnvFile

        mock_ex = _make_ssh_executor(mocker)
        mock_ex.run.return_value = _make_remote_result()

        env_file = EnvFile(
            path="/etc/onetimesecret/.env",
            entries=[
                EnvEntry(key="HOST", value="example.com", raw_line="HOST=example.com"),
            ],
            _variables={"HOST": "example.com"},
        )
        env_file.write(executor=mock_ex)

        mock_ex.run.assert_called_once()
        call_args = mock_ex.run.call_args
        assert call_args[0][0] == ["tee", "/etc/onetimesecret/.env"]
        assert call_args[1]["input"] == "HOST=example.com\n"


class TestSecretExistsRemote:
    """Test secret_exists() with remote executor."""

    def test_secret_exists_remote_true(self, mocker):
        from ots_containers.environment_file import secret_exists

        mock_ex = _make_ssh_executor(mocker)
        mock_ex.run.return_value = _make_remote_result(returncode=0)

        assert secret_exists("ots_api_key", executor=mock_ex) is True
        mock_ex.run.assert_called_once_with(
            ["podman", "secret", "exists", "ots_api_key"], timeout=10
        )

    def test_secret_exists_remote_false(self, mocker):
        from ots_containers.environment_file import secret_exists

        mock_ex = _make_ssh_executor(mocker)
        mock_ex.run.return_value = _make_remote_result(returncode=1)

        assert secret_exists("ots_missing", executor=mock_ex) is False


class TestEnsurePodmanSecretRemote:
    """Test ensure_podman_secret() with remote executor."""

    def test_creates_new_secret_remotely(self, mocker):
        from ots_containers.environment_file import ensure_podman_secret

        mock_ex = _make_ssh_executor(mocker)
        mocker.patch("ots_containers.systemd.require_podman")
        mock_ex.run.side_effect = [
            _make_remote_result(returncode=1),  # secret doesn't exist
            _make_remote_result(returncode=0),  # secret create
        ]

        result = ensure_podman_secret("ots_key", "secret_val", executor=mock_ex)

        assert result == "created"
        assert mock_ex.run.call_count == 2
        # First call: podman secret exists (timeout=15)
        exists_call = mock_ex.run.call_args_list[0]
        assert exists_call[1]["timeout"] == 15, "secret exists should have timeout=15"
        # Second call is the create (timeout=30)
        create_call = mock_ex.run.call_args_list[1]
        assert create_call[0][0] == ["podman", "secret", "create", "ots_key", "-"]
        assert create_call[1]["input"] == "secret_val"
        assert create_call[1]["check"] is True
        assert create_call[1]["timeout"] == 30, "secret create should have timeout=30"

    def test_replaces_existing_secret_remotely(self, mocker):
        from ots_containers.environment_file import ensure_podman_secret

        mock_ex = _make_ssh_executor(mocker)
        mocker.patch("ots_containers.systemd.require_podman")
        mock_ex.run.side_effect = [
            _make_remote_result(returncode=0),  # secret exists
            _make_remote_result(returncode=0),  # secret rm
            _make_remote_result(returncode=0),  # secret create
        ]

        result = ensure_podman_secret("ots_key", "new_val", executor=mock_ex)

        assert result == "replaced"
        assert mock_ex.run.call_count == 3
        # Verify timeout kwargs on each call
        exists_call = mock_ex.run.call_args_list[0]
        assert exists_call[1]["timeout"] == 15, "secret exists should have timeout=15"
        rm_call = mock_ex.run.call_args_list[1]
        assert rm_call[0][0] == ["podman", "secret", "rm", "ots_key"]
        assert rm_call[1]["timeout"] == 15, "secret rm should have timeout=15"
        create_call = mock_ex.run.call_args_list[2]
        assert create_call[1]["timeout"] == 30, "secret create should have timeout=30"


class TestGetSecretsFromEnvFileRemote:
    """Test get_secrets_from_env_file() with remote executor."""

    def test_reads_remote_env_file(self, mocker):
        from ots_containers.environment_file import get_secrets_from_env_file

        mock_ex = _make_ssh_executor(mocker)
        mock_ex.run.side_effect = [
            _make_remote_result(returncode=0),  # test -f
            _make_remote_result(stdout="SECRET_VARIABLE_NAMES=API_KEY\nAPI_KEY=secret\n"),  # cat
        ]

        secrets = get_secrets_from_env_file("/remote/.env", executor=mock_ex)

        assert len(secrets) == 1
        assert secrets[0].env_var_name == "API_KEY"


class TestProcessEnvFileRemote:
    """Test process_env_file() threads executor correctly."""

    def test_threads_executor_to_ensure_and_write(self, mocker):
        from ots_containers.environment_file import EnvFile, process_env_file

        mock_ex = _make_ssh_executor(mocker)
        # Mock the two downstream functions to verify executor threading
        mock_ensure = mocker.patch(
            "ots_containers.environment_file.ensure_podman_secret",
            return_value="created",
        )
        mock_write = mocker.patch.object(EnvFile, "write")

        env_file = EnvFile.parse(  # local parse for test setup
            "/dev/null"
        )
        env_file.entries = []
        env_file._variables = {
            "SECRET_VARIABLE_NAMES": "API_KEY",
            "API_KEY": "secret_value",
        }
        # Add entries so extract_secrets finds them
        from ots_containers.environment_file import EnvEntry

        env_file.entries = [
            EnvEntry(
                key="SECRET_VARIABLE_NAMES",
                value="API_KEY",
                raw_line="SECRET_VARIABLE_NAMES=API_KEY",
            ),
            EnvEntry(key="API_KEY", value="secret_value", raw_line="API_KEY=secret_value"),
        ]

        process_env_file(env_file, executor=mock_ex)

        mock_ensure.assert_called_once_with("ots_api_key", "secret_value", executor=mock_ex)
        mock_write.assert_called_once_with(executor=mock_ex)
