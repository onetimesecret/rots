# tests/commands/test_db_commands.py
"""Tests for db CLI commands (backup, restore, info, deployments)."""

import json

import pytest

from ots_containers import db as db_module


class TestDeploymentsCommand:
    """Tests for the 'ots db deployments' command."""

    def test_deployments_exits_when_db_missing(self, tmp_path, mocker, capsys):
        """Should exit with code 1 when database does not exist."""
        from ots_containers.commands.db import deployments

        mocker.patch(
            "ots_containers.commands.db.Config",
            return_value=mocker.Mock(db_path=tmp_path / "missing.db"),
        )

        with pytest.raises(SystemExit) as exc_info:
            deployments()

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "not found" in captured.out

    def test_deployments_exits_when_db_missing_json(self, tmp_path, mocker, capsys):
        """Should output JSON error when db is missing and --json is requested."""
        from ots_containers.commands.db import deployments

        mocker.patch(
            "ots_containers.commands.db.Config",
            return_value=mocker.Mock(db_path=tmp_path / "missing.db"),
        )

        with pytest.raises(SystemExit) as exc_info:
            deployments(json_output=True)

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["success"] is False
        assert "error" in data

    def test_deployments_empty_db_prints_no_history(self, tmp_path, mocker, capsys):
        """Should print a 'no history' message when deployments table is empty."""
        from ots_containers.commands.db import deployments

        db_path = tmp_path / "deploy.db"
        db_module.init_db(db_path)

        mocker.patch(
            "ots_containers.commands.db.Config",
            return_value=mocker.Mock(db_path=db_path),
        )

        deployments()

        captured = capsys.readouterr()
        assert "No deployment history" in captured.out

    def test_deployments_empty_db_with_web_filter(self, tmp_path, mocker, capsys):
        """Should mention the port when filtering by web and no records exist."""
        from ots_containers.commands.db import deployments

        db_path = tmp_path / "deploy.db"
        db_module.init_db(db_path)

        mocker.patch(
            "ots_containers.commands.db.Config",
            return_value=mocker.Mock(db_path=db_path),
        )

        deployments(web=7043)

        captured = capsys.readouterr()
        assert "7043" in captured.out

    def test_deployments_shows_records(self, tmp_path, mocker, capsys):
        """Should display deployment records in tabular form."""
        from ots_containers.commands.db import deployments

        db_path = tmp_path / "deploy.db"
        db_module.init_db(db_path)
        db_module.record_deployment(
            db_path,
            image="registry.example.com/ots",
            tag="v1.2.3",
            action="deploy",
            port=7043,
            success=True,
        )

        mocker.patch(
            "ots_containers.commands.db.Config",
            return_value=mocker.Mock(db_path=db_path),
        )

        deployments()

        captured = capsys.readouterr()
        assert "v1.2.3" in captured.out
        assert "deploy" in captured.out
        assert "yes" in captured.out

    def test_deployments_json_output(self, tmp_path, mocker, capsys):
        """Should output valid JSON list when --json is specified."""
        from ots_containers.commands.db import deployments

        db_path = tmp_path / "deploy.db"
        db_module.init_db(db_path)
        db_module.record_deployment(
            db_path,
            image="registry.example.com/ots",
            tag="v1.2.3",
            action="deploy",
            port=7043,
            success=True,
        )

        mocker.patch(
            "ots_containers.commands.db.Config",
            return_value=mocker.Mock(db_path=db_path),
        )

        deployments(json_output=True)

        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["tag"] == "v1.2.3"
        assert data[0]["action"] == "deploy"
        assert data[0]["port"] == 7043
        assert data[0]["success"] is True

    def test_deployments_filters_by_web_port(self, tmp_path, mocker, capsys):
        """Should only show records for the specified port when --web is given."""
        from ots_containers.commands.db import deployments

        db_path = tmp_path / "deploy.db"
        db_module.init_db(db_path)
        db_module.record_deployment(
            db_path,
            image="img",
            tag="v1",
            action="deploy",
            port=7043,
        )
        db_module.record_deployment(
            db_path,
            image="img",
            tag="v2",
            action="deploy",
            port=7044,
        )

        mocker.patch(
            "ots_containers.commands.db.Config",
            return_value=mocker.Mock(db_path=db_path),
        )

        deployments(web=7043, json_output=True)

        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert all(r["port"] == 7043 for r in data)
        assert len(data) == 1
        assert data[0]["tag"] == "v1"

    def test_deployments_respects_limit(self, tmp_path, mocker, capsys):
        """Should respect the --limit parameter."""
        from ots_containers.commands.db import deployments

        db_path = tmp_path / "deploy.db"
        db_module.init_db(db_path)
        for i in range(10):
            db_module.record_deployment(
                db_path,
                image="img",
                tag=f"v{i}",
                action="deploy",
                port=7043,
            )

        mocker.patch(
            "ots_containers.commands.db.Config",
            return_value=mocker.Mock(db_path=db_path),
        )

        deployments(limit=3, json_output=True)

        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert len(data) == 3

    def test_deployments_failed_shows_no(self, tmp_path, mocker, capsys):
        """Should show 'NO' for failed deployments in tabular output."""
        from ots_containers.commands.db import deployments

        db_path = tmp_path / "deploy.db"
        db_module.init_db(db_path)
        db_module.record_deployment(
            db_path,
            image="img",
            tag="v1",
            action="deploy",
            port=7043,
            success=False,
        )

        mocker.patch(
            "ots_containers.commands.db.Config",
            return_value=mocker.Mock(db_path=db_path),
        )

        deployments()

        captured = capsys.readouterr()
        assert "NO" in captured.out


class TestBackupCommand:
    """Tests for the 'ots db backup' command."""

    def test_backup_exits_when_db_missing(self, tmp_path, mocker, capsys):
        """Should exit with code 1 when source db does not exist."""
        from ots_containers.commands.db import backup

        mocker.patch(
            "ots_containers.commands.db.Config",
            return_value=mocker.Mock(db_path=tmp_path / "missing.db"),
        )

        with pytest.raises(SystemExit) as exc_info:
            backup()

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "not found" in captured.out

    def test_backup_exits_when_db_missing_json(self, tmp_path, mocker, capsys):
        """Should emit JSON error when db is missing and --json is requested."""
        from ots_containers.commands.db import backup

        mocker.patch(
            "ots_containers.commands.db.Config",
            return_value=mocker.Mock(db_path=tmp_path / "missing.db"),
        )

        with pytest.raises(SystemExit) as exc_info:
            backup(json_output=True)

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["success"] is False

    def test_backup_creates_file_at_default_location(self, tmp_path, mocker, capsys):
        """Should create a timestamped backup next to the source db."""
        from ots_containers.commands.db import backup

        db_path = tmp_path / "deploy.db"
        db_module.init_db(db_path)

        mocker.patch(
            "ots_containers.commands.db.Config",
            return_value=mocker.Mock(db_path=db_path),
        )

        backup()

        captured = capsys.readouterr()
        assert "Backup created" in captured.out
        # Verify a backup file was actually created in tmp_path
        bak_files = list(tmp_path.glob("deploy.*.bak"))
        assert len(bak_files) == 1

    def test_backup_creates_file_at_explicit_dest(self, tmp_path, mocker, capsys):
        """Should create the backup at the path specified by dest."""
        from ots_containers.commands.db import backup

        db_path = tmp_path / "deploy.db"
        dest_path = tmp_path / "my_backup.db"
        db_module.init_db(db_path)

        mocker.patch(
            "ots_containers.commands.db.Config",
            return_value=mocker.Mock(db_path=db_path),
        )

        backup(dest=dest_path)

        assert dest_path.exists()
        captured = capsys.readouterr()
        assert "Backup created" in captured.out

    def test_backup_json_output(self, tmp_path, mocker, capsys):
        """Should emit valid JSON result when --json is specified."""
        from ots_containers.commands.db import backup

        db_path = tmp_path / "deploy.db"
        db_module.init_db(db_path)

        mocker.patch(
            "ots_containers.commands.db.Config",
            return_value=mocker.Mock(db_path=db_path),
        )

        backup(json_output=True)

        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["success"] is True
        assert "destination" in data
        assert data["size_bytes"] > 0

    def test_backup_creates_parent_dirs(self, tmp_path, mocker, capsys):
        """Should create parent directories for the destination if they don't exist."""
        from ots_containers.commands.db import backup

        db_path = tmp_path / "deploy.db"
        dest_path = tmp_path / "subdir" / "nested" / "backup.db"
        db_module.init_db(db_path)

        mocker.patch(
            "ots_containers.commands.db.Config",
            return_value=mocker.Mock(db_path=db_path),
        )

        backup(dest=dest_path)

        assert dest_path.exists()

    def test_backup_sqlite_error_exits(self, tmp_path, mocker, capsys):
        """Should exit with code 1 if the SQLite backup API raises an error."""
        import sqlite3

        from ots_containers.commands.db import backup

        db_path = tmp_path / "deploy.db"
        db_module.init_db(db_path)

        mocker.patch(
            "ots_containers.commands.db.Config",
            return_value=mocker.Mock(db_path=db_path),
        )
        mocker.patch(
            "sqlite3.connect",
            side_effect=sqlite3.Error("disk full"),
        )

        with pytest.raises(SystemExit) as exc_info:
            backup()

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "Backup failed" in captured.out

    def test_backup_sqlite_error_json(self, tmp_path, mocker, capsys):
        """Should emit JSON error when SQLite backup fails and --json is set."""
        import sqlite3

        from ots_containers.commands.db import backup

        db_path = tmp_path / "deploy.db"
        db_module.init_db(db_path)

        mocker.patch(
            "ots_containers.commands.db.Config",
            return_value=mocker.Mock(db_path=db_path),
        )
        mocker.patch(
            "sqlite3.connect",
            side_effect=sqlite3.Error("disk full"),
        )

        with pytest.raises(SystemExit) as exc_info:
            backup(json_output=True)

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["success"] is False


class TestRestoreCommand:
    """Tests for the 'ots db restore' command."""

    def _make_valid_backup(self, path):
        """Create a valid backup db with required tables at path."""
        db_module.init_db(path)
        db_module.record_deployment(path, image="img", tag="v1", action="deploy")
        return path

    def test_restore_exits_when_src_missing(self, tmp_path, mocker, capsys):
        """Should exit with code 1 when the backup file does not exist."""
        from ots_containers.commands.db import restore

        src = tmp_path / "missing_backup.db"
        mocker.patch(
            "ots_containers.commands.db.Config",
            return_value=mocker.Mock(db_path=tmp_path / "live.db"),
        )

        with pytest.raises(SystemExit) as exc_info:
            restore(src=src, yes=True)

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "not found" in captured.out

    def test_restore_exits_when_src_missing_json(self, tmp_path, mocker, capsys):
        """Should emit JSON error when backup file is missing with --json."""
        from ots_containers.commands.db import restore

        src = tmp_path / "missing_backup.db"
        mocker.patch(
            "ots_containers.commands.db.Config",
            return_value=mocker.Mock(db_path=tmp_path / "live.db"),
        )

        with pytest.raises(SystemExit) as exc_info:
            restore(src=src, yes=True, json_output=True)

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["success"] is False

    def test_restore_exits_on_missing_tables(self, tmp_path, mocker, capsys):
        """Should exit with code 1 if backup lacks required tables."""
        import sqlite3

        from ots_containers.commands.db import restore

        # Create a valid SQLite file but without the required tables
        src = tmp_path / "bad_backup.db"
        conn = sqlite3.connect(src)
        conn.execute("CREATE TABLE other (x INTEGER)")
        conn.commit()
        conn.close()

        mocker.patch(
            "ots_containers.commands.db.Config",
            return_value=mocker.Mock(db_path=tmp_path / "live.db"),
        )

        with pytest.raises(SystemExit) as exc_info:
            restore(src=src, yes=True)

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "missing required tables" in captured.out

    def test_restore_exits_on_missing_tables_json(self, tmp_path, mocker, capsys):
        """Should emit JSON error when backup is missing tables and --json is set."""
        import sqlite3

        from ots_containers.commands.db import restore

        src = tmp_path / "bad_backup.db"
        conn = sqlite3.connect(src)
        conn.execute("CREATE TABLE other (x INTEGER)")
        conn.commit()
        conn.close()

        mocker.patch(
            "ots_containers.commands.db.Config",
            return_value=mocker.Mock(db_path=tmp_path / "live.db"),
        )

        with pytest.raises(SystemExit) as exc_info:
            restore(src=src, yes=True, json_output=True)

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["success"] is False
        assert "missing" in data["error"].lower()

    def test_restore_succeeds_with_yes_flag(self, tmp_path, mocker, capsys):
        """Should restore when --yes is given and skip confirmation."""
        from ots_containers.commands.db import restore

        src = tmp_path / "backup.db"
        live_db = tmp_path / "live.db"
        self._make_valid_backup(src)

        mocker.patch(
            "ots_containers.commands.db.Config",
            return_value=mocker.Mock(db_path=live_db),
        )

        restore(src=src, yes=True)

        assert live_db.exists()
        captured = capsys.readouterr()
        assert "Restored" in captured.out

    def test_restore_json_output_on_success(self, tmp_path, mocker, capsys):
        """Should emit JSON result on success when --json is specified."""
        from ots_containers.commands.db import restore

        src = tmp_path / "backup.db"
        live_db = tmp_path / "live.db"
        self._make_valid_backup(src)

        mocker.patch(
            "ots_containers.commands.db.Config",
            return_value=mocker.Mock(db_path=live_db),
        )

        restore(src=src, yes=True, json_output=True)

        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["success"] is True
        assert str(src) == data["source"]

    def test_restore_aborts_on_no_confirmation(self, tmp_path, mocker, capsys):
        """Should print Aborted and return if user declines confirmation."""
        from ots_containers.commands.db import restore

        src = tmp_path / "backup.db"
        live_db = tmp_path / "live.db"
        self._make_valid_backup(src)

        mocker.patch(
            "ots_containers.commands.db.Config",
            return_value=mocker.Mock(db_path=live_db),
        )
        mocker.patch("builtins.input", return_value="n")

        restore(src=src, yes=False)

        captured = capsys.readouterr()
        assert "Aborted" in captured.out
        assert not live_db.exists()

    def test_restore_accepts_y_confirmation(self, tmp_path, mocker, capsys):
        """Should proceed when user types 'y' at confirmation prompt."""
        from ots_containers.commands.db import restore

        src = tmp_path / "backup.db"
        live_db = tmp_path / "live.db"
        self._make_valid_backup(src)

        mocker.patch(
            "ots_containers.commands.db.Config",
            return_value=mocker.Mock(db_path=live_db),
        )
        mocker.patch("builtins.input", return_value="y")

        restore(src=src, yes=False)

        assert live_db.exists()
        captured = capsys.readouterr()
        assert "Restored" in captured.out

    def test_restore_creates_pre_restore_backup(self, tmp_path, mocker, capsys):
        """Should create a pre-restore backup of the live DB before replacing it."""
        from ots_containers.commands.db import restore

        src = tmp_path / "backup.db"
        live_db = tmp_path / "live.db"
        self._make_valid_backup(src)
        # Create an existing live db
        db_module.init_db(live_db)

        mocker.patch(
            "ots_containers.commands.db.Config",
            return_value=mocker.Mock(db_path=live_db),
        )

        restore(src=src, yes=True)

        captured = capsys.readouterr()
        assert "Pre-restore backup" in captured.out
        # A .bak file should exist next to the live db
        bak_files = list(tmp_path.glob("live.*.bak"))
        assert len(bak_files) == 1

    def test_restore_json_includes_pre_restore_backup(self, tmp_path, mocker, capsys):
        """JSON output should include pre_restore_backup path when a live db existed."""
        from ots_containers.commands.db import restore

        src = tmp_path / "backup.db"
        live_db = tmp_path / "live.db"
        self._make_valid_backup(src)
        db_module.init_db(live_db)

        mocker.patch(
            "ots_containers.commands.db.Config",
            return_value=mocker.Mock(db_path=live_db),
        )

        restore(src=src, yes=True, json_output=True)

        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["success"] is True
        assert data["pre_restore_backup"] is not None

    def test_restore_json_pre_restore_null_when_no_live_db(self, tmp_path, mocker, capsys):
        """JSON pre_restore_backup should be null when no live db existed."""
        from ots_containers.commands.db import restore

        src = tmp_path / "backup.db"
        live_db = tmp_path / "nonexistent_live.db"
        self._make_valid_backup(src)

        mocker.patch(
            "ots_containers.commands.db.Config",
            return_value=mocker.Mock(db_path=live_db),
        )

        restore(src=src, yes=True, json_output=True)

        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["success"] is True
        assert data["pre_restore_backup"] is None
