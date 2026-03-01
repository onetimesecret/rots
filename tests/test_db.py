# tests/test_db.py
"""Tests for deployment timeline database."""

import json
import sqlite3
from unittest.mock import MagicMock

import pytest

from ots_containers import db


class TestInitDb:
    """Test database initialization."""

    def test_init_db_creates_file(self, tmp_path):
        """init_db should create the database file."""
        db_path = tmp_path / "test.db"
        assert not db_path.exists()

        db.init_db(db_path)

        assert db_path.exists()

    def test_init_db_is_idempotent(self, tmp_path):
        """init_db should be safe to call multiple times."""
        db_path = tmp_path / "test.db"

        db.init_db(db_path)
        db.init_db(db_path)  # Should not raise

        assert db_path.exists()

    def test_init_db_creates_parent_dirs(self, tmp_path):
        """init_db should create parent directories."""
        db_path = tmp_path / "subdir" / "test.db"

        db.init_db(db_path)

        assert db_path.exists()

    def test_init_db_creates_deployments_table(self, tmp_path):
        """init_db should create the deployments table with the correct columns."""
        db_path = tmp_path / "test.db"
        db.init_db(db_path)

        conn = sqlite3.connect(db_path)
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        conn.close()

        assert "deployments" in tables
        assert "image_aliases" in tables
        assert "service_instances" in tables
        assert "service_actions" in tables

    def test_init_db_deployments_columns(self, tmp_path):
        """deployments table should have all required columns."""
        db_path = tmp_path / "test.db"
        db.init_db(db_path)

        conn = sqlite3.connect(db_path)
        columns = {row[1] for row in conn.execute("PRAGMA table_info(deployments)").fetchall()}
        conn.close()

        assert columns >= {"id", "timestamp", "port", "image", "tag", "action", "success", "notes"}

    def test_init_db_image_aliases_columns(self, tmp_path):
        """image_aliases table should have all required columns."""
        db_path = tmp_path / "test.db"
        db.init_db(db_path)

        conn = sqlite3.connect(db_path)
        columns = {row[1] for row in conn.execute("PRAGMA table_info(image_aliases)").fetchall()}
        conn.close()

        assert columns >= {"alias", "image", "tag", "set_at"}

    def test_init_db_creates_indexes(self, tmp_path):
        """init_db should create the expected indexes."""
        db_path = tmp_path / "test.db"
        db.init_db(db_path)

        conn = sqlite3.connect(db_path)
        indexes = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()
        }
        conn.close()

        assert "idx_deployments_timestamp" in indexes
        assert "idx_deployments_port" in indexes
        assert "idx_deployments_tag" in indexes


class TestGetConnection:
    """Test the get_connection context manager."""

    def test_get_connection_auto_initializes(self, tmp_path):
        """get_connection should initialize the DB if it does not exist yet."""
        db_path = tmp_path / "auto.db"
        assert not db_path.exists()

        with db.get_connection(db_path) as conn:
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }

        assert "deployments" in tables
        assert db_path.exists()

    def test_get_connection_sets_row_factory(self, tmp_path):
        """get_connection should configure row_factory for dict-like access."""
        db_path = tmp_path / "test.db"
        db.init_db(db_path)
        db.record_deployment(db_path, "img", "v1", "deploy")

        with db.get_connection(db_path) as conn:
            row = conn.execute("SELECT id, tag FROM deployments LIMIT 1").fetchone()

        # sqlite3.Row supports column-name access
        assert row["tag"] == "v1"
        assert row["id"] == 1


class TestRecordDeployment:
    """Test deployment recording."""

    def test_record_deployment_returns_id(self, tmp_path):
        """record_deployment should return the new deployment ID."""
        db_path = tmp_path / "test.db"

        deployment_id = db.record_deployment(
            db_path,
            image="ghcr.io/test/image",
            tag="v1.0.0",
            action="deploy",
            port=7043,
        )

        assert deployment_id == 1

    def test_record_deployment_increments_id(self, tmp_path):
        """Each deployment should get a new ID."""
        db_path = tmp_path / "test.db"

        id1 = db.record_deployment(db_path, "img", "v1", "deploy")
        id2 = db.record_deployment(db_path, "img", "v2", "deploy")
        id3 = db.record_deployment(db_path, "img", "v3", "deploy")

        assert id1 == 1
        assert id2 == 2
        assert id3 == 3

    def test_record_deployment_without_port(self, tmp_path):
        """record_deployment should work without a port (port defaults to None)."""
        db_path = tmp_path / "test.db"

        deployment_id = db.record_deployment(db_path, "img", "v1", "set-current")

        assert deployment_id == 1
        deployments = db.get_deployments(db_path)
        assert deployments[0].port is None

    def test_record_deployment_with_notes(self, tmp_path):
        """record_deployment should store notes when provided."""
        db_path = tmp_path / "test.db"

        db.record_deployment(db_path, "img", "v1", "deploy", notes="initial deploy")

        deployments = db.get_deployments(db_path)
        assert deployments[0].notes == "initial deploy"

    def test_record_deployment_without_notes(self, tmp_path):
        """record_deployment without notes should store None."""
        db_path = tmp_path / "test.db"

        db.record_deployment(db_path, "img", "v1", "deploy")

        deployments = db.get_deployments(db_path)
        assert deployments[0].notes is None

    def test_record_deployment_success_true(self, tmp_path):
        """record_deployment with success=True stores a truthy value."""
        db_path = tmp_path / "test.db"

        db.record_deployment(db_path, "img", "v1", "deploy", success=True)

        deployments = db.get_deployments(db_path)
        assert deployments[0].success is True

    def test_record_deployment_success_false(self, tmp_path):
        """record_deployment with success=False stores a falsy value."""
        db_path = tmp_path / "test.db"

        db.record_deployment(db_path, "img", "v1", "deploy", success=False)

        deployments = db.get_deployments(db_path)
        assert deployments[0].success is False

    def test_record_deployment_return_type_is_int(self, tmp_path):
        """record_deployment should return an int."""
        db_path = tmp_path / "test.db"

        result = db.record_deployment(db_path, "img", "v1", "deploy")

        assert isinstance(result, int)

    def test_record_deployment_stores_image_and_tag(self, tmp_path):
        """record_deployment should persist image and tag fields."""
        db_path = tmp_path / "test.db"

        db.record_deployment(db_path, "ghcr.io/org/app", "sha256-abc123", "deploy")

        deployments = db.get_deployments(db_path)
        assert deployments[0].image == "ghcr.io/org/app"
        assert deployments[0].tag == "sha256-abc123"

    def test_record_deployment_stores_action(self, tmp_path):
        """record_deployment should persist the action field."""
        db_path = tmp_path / "test.db"

        db.record_deployment(db_path, "img", "v1", "rollback")

        deployments = db.get_deployments(db_path)
        assert deployments[0].action == "rollback"

    def test_record_deployment_stores_port(self, tmp_path):
        """record_deployment should persist the port field."""
        db_path = tmp_path / "test.db"

        db.record_deployment(db_path, "img", "v1", "deploy", port=8080)

        deployments = db.get_deployments(db_path)
        assert deployments[0].port == 8080

    def test_record_deployment_timestamp_is_set(self, tmp_path):
        """record_deployment should populate the timestamp field."""
        db_path = tmp_path / "test.db"

        db.record_deployment(db_path, "img", "v1", "deploy")

        deployments = db.get_deployments(db_path)
        assert deployments[0].timestamp is not None
        assert len(deployments[0].timestamp) > 0


class TestGetDeployments:
    """Test deployment history retrieval."""

    def test_get_deployments_empty_db(self, tmp_path):
        """get_deployments on an empty database should return an empty list."""
        db_path = tmp_path / "test.db"
        db.init_db(db_path)

        deployments = db.get_deployments(db_path)

        assert deployments == []

    def test_get_deployments_returns_list(self, tmp_path):
        """get_deployments should return a list of Deployment objects."""
        db_path = tmp_path / "test.db"
        db.record_deployment(db_path, "img", "v1", "deploy", port=7043)

        deployments = db.get_deployments(db_path)

        assert len(deployments) == 1
        assert deployments[0].image == "img"
        assert deployments[0].tag == "v1"
        assert deployments[0].port == 7043

    def test_get_deployments_returns_deployment_objects(self, tmp_path):
        """get_deployments should return Deployment dataclass instances."""
        db_path = tmp_path / "test.db"
        db.record_deployment(db_path, "img", "v1", "deploy")

        deployments = db.get_deployments(db_path)

        assert isinstance(deployments[0], db.Deployment)

    def test_get_deployments_respects_limit(self, tmp_path):
        """get_deployments should respect the limit parameter."""
        db_path = tmp_path / "test.db"
        for i in range(10):
            db.record_deployment(db_path, "img", f"v{i}", "deploy")

        deployments = db.get_deployments(db_path, limit=5)

        assert len(deployments) == 5

    def test_get_deployments_filters_by_port(self, tmp_path):
        """get_deployments should filter by port when specified."""
        db_path = tmp_path / "test.db"
        db.record_deployment(db_path, "img", "v1", "deploy", port=7043)
        db.record_deployment(db_path, "img", "v2", "deploy", port=7044)
        db.record_deployment(db_path, "img", "v3", "deploy", port=7043)

        deployments = db.get_deployments(db_path, port=7043)

        assert len(deployments) == 2
        assert all(d.port == 7043 for d in deployments)

    def test_get_deployments_filter_by_port_no_matches(self, tmp_path):
        """get_deployments with a port that has no records should return empty list."""
        db_path = tmp_path / "test.db"
        db.record_deployment(db_path, "img", "v1", "deploy", port=7043)

        deployments = db.get_deployments(db_path, port=9999)

        assert deployments == []

    def test_get_deployments_filters_by_action_like(self, tmp_path):
        """get_deployments should filter by action using SQL LIKE pattern."""
        db_path = tmp_path / "test.db"
        db.record_deployment(db_path, "img", "v1", "deploy")
        db.record_deployment(db_path, "img", "v2", "redeploy")
        db.record_deployment(db_path, "img", "v3", "rollback")

        # Match actions that end with "deploy"
        deployments = db.get_deployments(db_path, action_like="%deploy")

        assert len(deployments) == 2
        actions = {d.action for d in deployments}
        assert actions == {"deploy", "redeploy"}

    def test_get_deployments_filters_by_action_exact(self, tmp_path):
        """get_deployments action_like with no wildcard acts as exact match."""
        db_path = tmp_path / "test.db"
        db.record_deployment(db_path, "img", "v1", "deploy")
        db.record_deployment(db_path, "img", "v2", "redeploy")
        db.record_deployment(db_path, "img", "v3", "rollback")

        deployments = db.get_deployments(db_path, action_like="rollback")

        assert len(deployments) == 1
        assert deployments[0].action == "rollback"

    def test_get_deployments_filters_by_notes_like(self, tmp_path):
        """get_deployments should filter by notes using SQL LIKE pattern."""
        db_path = tmp_path / "test.db"
        db.record_deployment(db_path, "img", "v1", "deploy", notes="worker_id=1 region=us")
        db.record_deployment(db_path, "img", "v2", "deploy", notes="worker_id=2 region=eu")
        db.record_deployment(db_path, "img", "v3", "deploy", notes="scheduled maintenance")

        deployments = db.get_deployments(db_path, notes_like="%worker_id=%")

        assert len(deployments) == 2
        for d in deployments:
            assert "worker_id=" in (d.notes or "")

    def test_get_deployments_combined_filters(self, tmp_path):
        """get_deployments should AND all provided filters together."""
        db_path = tmp_path / "test.db"
        db.record_deployment(db_path, "img", "v1", "deploy", port=7043, notes="batch=1")
        db.record_deployment(db_path, "img", "v2", "deploy", port=7044, notes="batch=1")
        db.record_deployment(db_path, "img", "v3", "redeploy", port=7043, notes="batch=2")

        deployments = db.get_deployments(db_path, port=7043, action_like="deploy")

        assert len(deployments) == 1
        assert deployments[0].tag == "v1"

    def test_get_deployments_ordered_newest_first(self, tmp_path):
        """get_deployments should return records ordered by timestamp DESC.

        To avoid depending on SQLite's second-level precision, we seed explicit
        timestamps directly and verify the ORDER BY contract.
        """
        import sqlite3

        db_path = tmp_path / "test.db"
        db.init_db(db_path)

        _sql = (
            "INSERT INTO deployments (timestamp, image, tag, action, success)"
            " VALUES (?, 'img', ?, 'deploy', 1)"
        )
        conn = sqlite3.connect(db_path)
        conn.execute(_sql, ("2026-01-01 10:00:00", "v1"))
        conn.execute(_sql, ("2026-01-01 11:00:00", "v2"))
        conn.execute(_sql, ("2026-01-01 12:00:00", "v3"))
        conn.commit()
        conn.close()

        deployments = db.get_deployments(db_path)

        # v3 has the latest timestamp so it should come first
        assert deployments[0].tag == "v3"
        assert deployments[1].tag == "v2"
        assert deployments[2].tag == "v1"

    def test_get_deployments_default_limit_fifty(self, tmp_path):
        """get_deployments default limit should return at most 50 records."""
        db_path = tmp_path / "test.db"
        for i in range(60):
            db.record_deployment(db_path, "img", f"v{i}", "deploy")

        deployments = db.get_deployments(db_path)

        assert len(deployments) == 50

    def test_get_deployments_multiple_records_all_fields(self, tmp_path):
        """get_deployments should correctly map all columns for multiple rows."""
        db_path = tmp_path / "test.db"
        db.record_deployment(
            db_path, "ghcr.io/org/app", "v1.2.3", "deploy", port=7043, success=False, notes="test"
        )

        deployments = db.get_deployments(db_path)
        d = deployments[0]

        assert d.image == "ghcr.io/org/app"
        assert d.tag == "v1.2.3"
        assert d.action == "deploy"
        assert d.port == 7043
        assert d.success is False
        assert d.notes == "test"
        assert d.id == 1


class TestImageAliases:
    """Test image alias management."""

    def test_set_alias_creates_alias(self, tmp_path):
        """set_alias should create a new alias."""
        db_path = tmp_path / "test.db"

        db.set_alias(db_path, "CURRENT", "img", "v1.0.0")

        alias = db.get_alias(db_path, "CURRENT")
        assert alias is not None
        assert alias.image == "img"
        assert alias.tag == "v1.0.0"

    def test_set_alias_updates_existing(self, tmp_path):
        """set_alias should update an existing alias."""
        db_path = tmp_path / "test.db"

        db.set_alias(db_path, "CURRENT", "img", "v1.0.0")
        db.set_alias(db_path, "CURRENT", "img", "v2.0.0")

        alias = db.get_alias(db_path, "CURRENT")
        assert alias is not None
        assert alias.tag == "v2.0.0"

    def test_get_alias_returns_none_if_not_found(self, tmp_path):
        """get_alias should return None if alias doesn't exist."""
        db_path = tmp_path / "test.db"
        db.init_db(db_path)

        alias = db.get_alias(db_path, "NONEXISTENT")

        assert alias is None

    def test_get_all_aliases(self, tmp_path):
        """get_all_aliases should return all aliases."""
        db_path = tmp_path / "test.db"
        db.set_alias(db_path, "CURRENT", "img", "v1")
        db.set_alias(db_path, "ROLLBACK", "img", "v0")

        aliases = db.get_all_aliases(db_path)

        assert len(aliases) == 2

    def test_set_alias_normalizes_to_uppercase(self, tmp_path):
        """set_alias should store the alias name uppercased."""
        db_path = tmp_path / "test.db"

        db.set_alias(db_path, "current", "img", "v1")

        alias = db.get_alias(db_path, "CURRENT")
        assert alias is not None
        assert alias.alias == "CURRENT"

    def test_get_alias_case_insensitive_lookup(self, tmp_path):
        """get_alias should find an alias regardless of input case."""
        db_path = tmp_path / "test.db"

        db.set_alias(db_path, "CURRENT", "img", "v1")

        alias_lower = db.get_alias(db_path, "current")
        alias_upper = db.get_alias(db_path, "CURRENT")

        assert alias_lower is not None
        assert alias_upper is not None
        assert alias_lower.tag == alias_upper.tag

    def test_get_alias_returns_image_alias_object(self, tmp_path):
        """get_alias should return an ImageAlias dataclass instance."""
        db_path = tmp_path / "test.db"
        db.set_alias(db_path, "CURRENT", "img", "v1")

        alias = db.get_alias(db_path, "CURRENT")

        assert isinstance(alias, db.ImageAlias)

    def test_get_all_aliases_empty(self, tmp_path):
        """get_all_aliases on an empty DB should return an empty list."""
        db_path = tmp_path / "test.db"
        db.init_db(db_path)

        aliases = db.get_all_aliases(db_path)

        assert aliases == []

    def test_get_all_aliases_ordered_by_alias(self, tmp_path):
        """get_all_aliases should return aliases ordered alphabetically by alias."""
        db_path = tmp_path / "test.db"
        db.set_alias(db_path, "ROLLBACK", "img", "v0")
        db.set_alias(db_path, "CURRENT", "img", "v1")

        aliases = db.get_all_aliases(db_path)

        names = [a.alias for a in aliases]
        assert names == sorted(names)

    def test_set_alias_updates_set_at(self, tmp_path):
        """set_alias should update set_at on conflict."""
        db_path = tmp_path / "test.db"
        db.set_alias(db_path, "CURRENT", "img", "v1")
        first = db.get_alias(db_path, "CURRENT")

        db.set_alias(db_path, "CURRENT", "img", "v2")
        second = db.get_alias(db_path, "CURRENT")

        # set_at is a datetime string; both should be non-empty
        assert first is not None and first.set_at
        assert second is not None and second.set_at


class TestGetCurrentAndRollbackImage:
    """Test get_current_image and get_rollback_image helpers."""

    def test_get_current_image_none_when_not_set(self, tmp_path):
        """get_current_image should return None when CURRENT alias not set."""
        db_path = tmp_path / "test.db"
        db.init_db(db_path)

        result = db.get_current_image(db_path)

        assert result is None

    def test_get_current_image_returns_tuple(self, tmp_path):
        """get_current_image should return (image, tag) tuple."""
        db_path = tmp_path / "test.db"
        db.set_alias(db_path, "CURRENT", "ghcr.io/org/app", "v3.0.0")

        result = db.get_current_image(db_path)

        assert result == ("ghcr.io/org/app", "v3.0.0")

    def test_get_rollback_image_none_when_not_set(self, tmp_path):
        """get_rollback_image should return None when ROLLBACK alias not set."""
        db_path = tmp_path / "test.db"
        db.init_db(db_path)

        result = db.get_rollback_image(db_path)

        assert result is None

    def test_get_rollback_image_returns_tuple(self, tmp_path):
        """get_rollback_image should return (image, tag) tuple."""
        db_path = tmp_path / "test.db"
        db.set_alias(db_path, "ROLLBACK", "ghcr.io/org/app", "v2.0.0")

        result = db.get_rollback_image(db_path)

        assert result == ("ghcr.io/org/app", "v2.0.0")


class TestSetCurrent:
    """Test set_current functionality."""

    def test_set_current_first_time(self, tmp_path):
        """set_current should set CURRENT when no previous exists."""
        db_path = tmp_path / "test.db"

        previous = db.set_current(db_path, "img", "v1.0.0")

        assert previous is None
        current = db.get_current_image(db_path)
        assert current == ("img", "v1.0.0")

    def test_set_current_moves_previous_to_rollback(self, tmp_path):
        """set_current should move previous CURRENT to ROLLBACK."""
        db_path = tmp_path / "test.db"

        db.set_current(db_path, "img", "v1.0.0")
        previous = db.set_current(db_path, "img", "v2.0.0")

        assert previous == "v1.0.0"
        rollback = db.get_rollback_image(db_path)
        assert rollback == ("img", "v1.0.0")

    def test_set_current_records_deployment_action(self, tmp_path):
        """set_current should record a 'set-current' action in the timeline."""
        db_path = tmp_path / "test.db"

        db.set_current(db_path, "img", "v1.0.0")

        deployments = db.get_deployments(db_path)
        assert any(d.action == "set-current" for d in deployments)

    def test_set_current_notes_initial(self, tmp_path):
        """set_current on first call should note 'Initial current'."""
        db_path = tmp_path / "test.db"

        db.set_current(db_path, "img", "v1.0.0")

        deployments = db.get_deployments(db_path, action_like="set-current")
        assert deployments[0].notes == "Initial current"

    def test_set_current_notes_previous_tag(self, tmp_path):
        """set_current on update should note the previous tag in at least one record."""
        db_path = tmp_path / "test.db"

        db.set_current(db_path, "img", "v1.0.0")
        db.set_current(db_path, "img", "v2.0.0")

        deployments = db.get_deployments(db_path, action_like="set-current")
        # One of the records should mention the previous tag
        notes = [d.notes or "" for d in deployments]
        assert any("v1.0.0" in n for n in notes)

    def test_set_current_returns_previous_tag_string(self, tmp_path):
        """set_current should return the previous tag string, not a tuple."""
        db_path = tmp_path / "test.db"
        db.set_current(db_path, "img", "v1.0.0")

        result = db.set_current(db_path, "img", "v2.0.0")

        assert result == "v1.0.0"
        assert isinstance(result, str)


class TestRollback:
    """Test rollback functionality."""

    def test_rollback_returns_none_when_no_history(self, tmp_path):
        """rollback should return None when no previous deployment."""
        db_path = tmp_path / "test.db"
        db.init_db(db_path)

        result = db.rollback(db_path)

        assert result is None

    def test_rollback_returns_none_with_single_deployment(self, tmp_path):
        """rollback should return None when only one deployment exists."""
        db_path = tmp_path / "test.db"

        # Only one deployment - can't roll back
        db.record_deployment(db_path, "img", "v1", "deploy", port=7043)
        db.set_alias(db_path, "CURRENT", "img", "v1")

        result = db.rollback(db_path)

        assert result is None

    def test_rollback_returns_previous_deployment(self, tmp_path):
        """rollback should return the previous deployment."""
        db_path = tmp_path / "test.db"

        # Create deployment history with two DIFFERENT tags
        # First deployment
        db.record_deployment(db_path, "img", "v1", "deploy", port=7043)
        db.set_alias(db_path, "CURRENT", "img", "v1")

        # Second deployment with different tag
        db.record_deployment(db_path, "img", "v2", "deploy", port=7043)
        db.set_alias(db_path, "CURRENT", "img", "v2")
        db.set_alias(db_path, "ROLLBACK", "img", "v1")

        # Rollback should go from v2 to v1
        result = db.rollback(db_path)

        # Result is the tag we rolled back TO
        assert result == ("img", "v1")

    def test_rollback_updates_aliases(self, tmp_path):
        """rollback should update CURRENT and ROLLBACK aliases."""
        db_path = tmp_path / "test.db"

        # Create deployment history
        db.record_deployment(db_path, "img", "v1", "deploy")
        db.set_alias(db_path, "CURRENT", "img", "v1")

        db.record_deployment(db_path, "img", "v2", "deploy")
        db.set_alias(db_path, "CURRENT", "img", "v2")
        db.set_alias(db_path, "ROLLBACK", "img", "v1")

        db.rollback(db_path)

        current = db.get_current_image(db_path)
        rollback = db.get_rollback_image(db_path)

        # After rollback: v1 is current, v2 is rollback
        assert current == ("img", "v1")
        assert rollback == ("img", "v2")

    def test_rollback_records_rollback_action(self, tmp_path):
        """rollback should record a 'rollback' entry in the timeline."""
        db_path = tmp_path / "test.db"

        db.record_deployment(db_path, "img", "v1", "deploy")
        db.set_alias(db_path, "CURRENT", "img", "v1")
        db.record_deployment(db_path, "img", "v2", "deploy")
        db.set_alias(db_path, "CURRENT", "img", "v2")

        db.rollback(db_path)

        deployments = db.get_deployments(db_path, action_like="rollback")
        assert len(deployments) == 1
        assert deployments[0].action == "rollback"

    def test_rollback_ignores_failed_deployments(self, tmp_path):
        """rollback should only consider successful deployments."""
        db_path = tmp_path / "test.db"

        db.record_deployment(db_path, "img", "v1", "deploy", success=True)
        db.record_deployment(db_path, "img", "v2", "deploy", success=False)
        db.set_alias(db_path, "CURRENT", "img", "v1")

        # Only one successful deploy — rollback should be unavailable
        result = db.rollback(db_path)

        assert result is None


class TestGetPreviousTags:
    """Test get_previous_tags functionality."""

    def test_get_previous_tags_returns_distinct(self, tmp_path):
        """get_previous_tags should return distinct image/tag pairs."""
        db_path = tmp_path / "test.db"

        db.record_deployment(db_path, "img", "v1", "deploy")
        db.record_deployment(db_path, "img", "v1", "redeploy")  # Same tag
        db.record_deployment(db_path, "img", "v2", "deploy")

        tags = db.get_previous_tags(db_path)

        # Should have 2 distinct tags
        tag_values = [t[1] for t in tags]
        assert "v1" in tag_values
        assert "v2" in tag_values

    def test_get_previous_tags_empty_db(self, tmp_path):
        """get_previous_tags on an empty DB should return empty list."""
        db_path = tmp_path / "test.db"
        db.init_db(db_path)

        tags = db.get_previous_tags(db_path)

        assert tags == []

    def test_get_previous_tags_respects_limit(self, tmp_path):
        """get_previous_tags should respect the limit parameter."""
        db_path = tmp_path / "test.db"
        for i in range(15):
            db.record_deployment(db_path, "img", f"v{i}", "deploy")

        tags = db.get_previous_tags(db_path, limit=5)

        assert len(tags) == 5

    def test_get_previous_tags_excludes_failed(self, tmp_path):
        """get_previous_tags should not include failed deployments."""
        db_path = tmp_path / "test.db"

        db.record_deployment(db_path, "img", "v1", "deploy", success=True)
        db.record_deployment(db_path, "img", "v2", "deploy", success=False)

        tags = db.get_previous_tags(db_path)
        tag_values = [t[1] for t in tags]

        assert "v1" in tag_values
        assert "v2" not in tag_values

    def test_get_previous_tags_excludes_rollback_actions(self, tmp_path):
        """get_previous_tags should exclude rollback actions from results."""
        db_path = tmp_path / "test.db"

        db.record_deployment(db_path, "img", "v1", "deploy", success=True)
        # Record a rollback action explicitly (not via db.rollback())
        db.record_deployment(db_path, "img", "v0", "rollback", success=True)

        tags = db.get_previous_tags(db_path)
        tag_values = [t[1] for t in tags]

        # v1 should be present (deploy action), v0 only via rollback — excluded
        assert "v1" in tag_values
        assert "v0" not in tag_values

    def test_get_previous_tags_returns_tuples(self, tmp_path):
        """get_previous_tags should return list of (image, tag, timestamp) tuples."""
        db_path = tmp_path / "test.db"
        db.record_deployment(db_path, "ghcr.io/org/app", "v1.0.0", "deploy")

        tags = db.get_previous_tags(db_path)

        assert len(tags) == 1
        image, tag, timestamp = tags[0]
        assert image == "ghcr.io/org/app"
        assert tag == "v1.0.0"
        assert timestamp is not None

    def test_get_previous_tags_ordered_most_recent_first(self, tmp_path):
        """get_previous_tags should order by last_used DESC.

        Uses explicit timestamps to avoid same-second precision issues.
        """
        import sqlite3

        db_path = tmp_path / "test.db"
        db.init_db(db_path)

        _sql = (
            "INSERT INTO deployments (timestamp, image, tag, action, success)"
            " VALUES (?, 'img', ?, 'deploy', 1)"
        )
        conn = sqlite3.connect(db_path)
        conn.execute(_sql, ("2026-01-01 10:00:00", "v1"))
        conn.execute(_sql, ("2026-01-01 11:00:00", "v2"))
        conn.commit()
        conn.close()

        tags = db.get_previous_tags(db_path)
        tag_values = [t[1] for t in tags]

        # v2 has a later timestamp and should come first
        assert tag_values[0] == "v2"
        assert tag_values[1] == "v1"


class TestServiceInstances:
    """Test service instance management functions."""

    def test_record_service_instance_returns_id(self, tmp_path):
        """record_service_instance should return the new instance ID."""
        db_path = tmp_path / "test.db"

        instance_id = db.record_service_instance(
            db_path,
            package="valkey",
            instance="6379",
            config_file="/etc/valkey/instances/6379.conf",
            data_dir="/var/lib/valkey/6379",
            port=6379,
        )

        assert isinstance(instance_id, int)
        assert instance_id >= 1

    def test_record_service_instance_without_port(self, tmp_path):
        """record_service_instance should work without port."""
        db_path = tmp_path / "test.db"

        instance_id = db.record_service_instance(
            db_path,
            package="valkey",
            instance="6379",
            config_file="/etc/valkey/instances/6379.conf",
            data_dir="/var/lib/valkey/6379",
        )

        assert instance_id >= 1
        svc = db.get_service_instance(db_path, "valkey", "6379")
        assert svc is not None
        assert svc.port is None

    def test_record_service_instance_upsert(self, tmp_path):
        """record_service_instance should update on conflict (package, instance)."""
        db_path = tmp_path / "test.db"

        db.record_service_instance(
            db_path, "valkey", "6379", "/old/config.conf", "/old/data", port=6379
        )
        db.record_service_instance(
            db_path, "valkey", "6379", "/new/config.conf", "/new/data", port=6380
        )

        svc = db.get_service_instance(db_path, "valkey", "6379")
        assert svc is not None
        assert svc.config_file == "/new/config.conf"
        assert svc.port == 6380

    def test_get_service_instance_not_found(self, tmp_path):
        """get_service_instance should return None for unknown package/instance."""
        db_path = tmp_path / "test.db"
        db.init_db(db_path)

        result = db.get_service_instance(db_path, "valkey", "9999")

        assert result is None

    def test_get_service_instance_returns_dataclass(self, tmp_path):
        """get_service_instance should return a ServiceInstance dataclass."""
        db_path = tmp_path / "test.db"
        db.record_service_instance(
            db_path, "redis", "6380", "/etc/redis/6380.conf", "/var/lib/redis/6380", port=6380
        )

        svc = db.get_service_instance(db_path, "redis", "6380")

        assert isinstance(svc, db.ServiceInstance)
        assert svc.package == "redis"
        assert svc.instance == "6380"
        assert svc.config_file == "/etc/redis/6380.conf"
        assert svc.data_dir == "/var/lib/redis/6380"
        assert svc.port == 6380

    def test_get_service_instances_all(self, tmp_path):
        """get_service_instances with no filter should return all instances."""
        db_path = tmp_path / "test.db"
        db.record_service_instance(db_path, "valkey", "6379", "/c1", "/d1")
        db.record_service_instance(db_path, "redis", "6380", "/c2", "/d2")

        instances = db.get_service_instances(db_path)

        assert len(instances) == 2

    def test_get_service_instances_filtered_by_package(self, tmp_path):
        """get_service_instances with package filter should narrow results."""
        db_path = tmp_path / "test.db"
        db.record_service_instance(db_path, "valkey", "6379", "/c1", "/d1")
        db.record_service_instance(db_path, "valkey", "6380", "/c2", "/d2")
        db.record_service_instance(db_path, "redis", "6381", "/c3", "/d3")

        instances = db.get_service_instances(db_path, package="valkey")

        assert len(instances) == 2
        assert all(i.package == "valkey" for i in instances)

    def test_get_service_instances_empty(self, tmp_path):
        """get_service_instances on an empty DB should return empty list."""
        db_path = tmp_path / "test.db"
        db.init_db(db_path)

        instances = db.get_service_instances(db_path)

        assert instances == []

    def test_delete_service_instance_returns_true(self, tmp_path):
        """delete_service_instance should return True when a row is deleted."""
        db_path = tmp_path / "test.db"
        db.record_service_instance(db_path, "valkey", "6379", "/c", "/d")

        deleted = db.delete_service_instance(db_path, "valkey", "6379")

        assert deleted is True
        assert db.get_service_instance(db_path, "valkey", "6379") is None

    def test_delete_service_instance_returns_false_when_missing(self, tmp_path):
        """delete_service_instance should return False when the row doesn't exist."""
        db_path = tmp_path / "test.db"
        db.init_db(db_path)

        deleted = db.delete_service_instance(db_path, "valkey", "9999")

        assert deleted is False

    def test_delete_service_instance_removes_only_target(self, tmp_path):
        """delete_service_instance should not affect other instance records."""
        db_path = tmp_path / "test.db"
        db.record_service_instance(db_path, "valkey", "6379", "/c1", "/d1")
        db.record_service_instance(db_path, "valkey", "6380", "/c2", "/d2")

        db.delete_service_instance(db_path, "valkey", "6379")

        remaining = db.get_service_instances(db_path)
        assert len(remaining) == 1
        assert remaining[0].instance == "6380"


class TestServiceActions:
    """Test service action audit trail functions."""

    def test_record_service_action_returns_id(self, tmp_path):
        """record_service_action should return the new action ID."""
        db_path = tmp_path / "test.db"

        action_id = db.record_service_action(db_path, "valkey", "6379", "init")

        assert isinstance(action_id, int)
        assert action_id >= 1

    def test_record_service_action_increments_id(self, tmp_path):
        """Each service action should get a new ID."""
        db_path = tmp_path / "test.db"

        id1 = db.record_service_action(db_path, "valkey", "6379", "start")
        id2 = db.record_service_action(db_path, "valkey", "6379", "stop")

        assert id2 > id1

    def test_record_service_action_success_false(self, tmp_path):
        """record_service_action should store success=False."""
        db_path = tmp_path / "test.db"

        db.record_service_action(
            db_path, "valkey", "6379", "start", success=False, notes="failed to bind"
        )

        actions = db.get_service_actions(db_path)
        assert actions[0].success is False
        assert actions[0].notes == "failed to bind"

    def test_get_service_actions_all(self, tmp_path):
        """get_service_actions with no filter should return all actions."""
        db_path = tmp_path / "test.db"
        db.record_service_action(db_path, "valkey", "6379", "start")
        db.record_service_action(db_path, "redis", "6380", "stop")

        actions = db.get_service_actions(db_path)

        assert len(actions) == 2

    def test_get_service_actions_filter_by_package(self, tmp_path):
        """get_service_actions with package filter should narrow results."""
        db_path = tmp_path / "test.db"
        db.record_service_action(db_path, "valkey", "6379", "start")
        db.record_service_action(db_path, "valkey", "6380", "start")
        db.record_service_action(db_path, "redis", "6381", "start")

        actions = db.get_service_actions(db_path, package="valkey")

        assert len(actions) == 2
        assert all(a.package == "valkey" for a in actions)

    def test_get_service_actions_filter_by_package_and_instance(self, tmp_path):
        """get_service_actions filtered by package+instance should be specific."""
        db_path = tmp_path / "test.db"
        db.record_service_action(db_path, "valkey", "6379", "start")
        db.record_service_action(db_path, "valkey", "6380", "start")

        actions = db.get_service_actions(db_path, package="valkey", instance="6379")

        assert len(actions) == 1
        assert actions[0].instance == "6379"

    def test_get_service_actions_empty(self, tmp_path):
        """get_service_actions on an empty DB should return empty list."""
        db_path = tmp_path / "test.db"
        db.init_db(db_path)

        actions = db.get_service_actions(db_path)

        assert actions == []

    def test_get_service_actions_respects_limit(self, tmp_path):
        """get_service_actions should respect the limit parameter."""
        db_path = tmp_path / "test.db"
        for _ in range(10):
            db.record_service_action(db_path, "valkey", "6379", "start")

        actions = db.get_service_actions(db_path, limit=3)

        assert len(actions) == 3

    def test_get_service_actions_returns_service_action_objects(self, tmp_path):
        """get_service_actions should return ServiceAction dataclass instances."""
        db_path = tmp_path / "test.db"
        db.record_service_action(db_path, "valkey", "6379", "init", notes="provisioned")

        actions = db.get_service_actions(db_path)
        a = actions[0]

        assert isinstance(a, db.ServiceAction)
        assert a.package == "valkey"
        assert a.instance == "6379"
        assert a.action == "init"
        assert a.notes == "provisioned"
        assert a.success is True

    def test_get_service_actions_ordered_newest_first(self, tmp_path):
        """get_service_actions should return records ordered by timestamp DESC.

        Uses explicit timestamps to avoid same-second precision issues.
        """
        import sqlite3

        db_path = tmp_path / "test.db"
        db.init_db(db_path)

        conn = sqlite3.connect(db_path)
        conn.execute(
            "INSERT INTO service_actions (timestamp, package, instance, action, success) "
            "VALUES (?, 'valkey', '6379', 'start', 1)",
            ("2026-01-01 10:00:00",),
        )
        conn.execute(
            "INSERT INTO service_actions (timestamp, package, instance, action, success) "
            "VALUES (?, 'valkey', '6379', 'stop', 1)",
            ("2026-01-01 11:00:00",),
        )
        conn.commit()
        conn.close()

        actions = db.get_service_actions(db_path)

        # 'stop' has a later timestamp and should come first
        assert actions[0].action == "stop"
        assert actions[1].action == "start"


def _make_ssh_executor(mocker):
    """Create a mock SSHExecutor that _is_remote() recognises as remote."""
    mock_ex = mocker.MagicMock()
    # Patch _is_remote to return True for this executor
    mocker.patch("ots_containers.db._is_remote", side_effect=lambda ex: ex is mock_ex)
    return mock_ex


def _make_remote_result(stdout="", returncode=0):
    """Build a mock Result for executor.run()."""
    result = MagicMock()
    result.ok = returncode == 0
    result.stdout = stdout
    result.stderr = ""
    result.returncode = returncode
    return result


class TestRollbackRemote:
    """Test db.rollback() with a remote executor."""

    def test_rollback_remote_queries_via_executor(self, mocker, tmp_path):
        """rollback() with remote executor should use _remote_query for the timeline query."""
        mock_ex = _make_ssh_executor(mocker)
        db_path = tmp_path / "test.db"

        # The timeline query returns two distinct deployments
        timeline_rows = json.dumps(
            [
                {"image": "img", "tag": "v2", "last_id": 2},
                {"image": "img", "tag": "v1", "last_id": 1},
            ]
        )
        # get_current_image -> get_alias -> _remote_query
        current_alias = json.dumps(
            [
                {"alias": "CURRENT", "image": "img", "tag": "v2", "set_at": "2026-01-01"},
            ]
        )

        # Track calls: first call is the timeline query, subsequent are from
        # get_current_image, set_alias (x2), record_deployment
        call_count = {"n": 0}
        query_results = [timeline_rows, current_alias]

        def mock_run(cmd, **kwargs):
            call_count["n"] += 1
            # SELECT queries use -json flag
            if "-json" in cmd:
                idx = min(call_count["n"] - 1, len(query_results) - 1)
                return _make_remote_result(stdout=query_results[idx])
            # Write queries (INSERT/UPDATE)
            return _make_remote_result()

        mock_ex.run.side_effect = mock_run

        result = db.rollback(db_path, executor=mock_ex)

        assert result == ("img", "v1")
        # Verify executor.run was called (timeline query + alias reads + alias writes + record)
        assert mock_ex.run.call_count >= 3

    def test_rollback_remote_returns_none_when_insufficient_history(self, mocker, tmp_path):
        """rollback() with remote executor returns None when < 2 distinct deployments."""
        mock_ex = _make_ssh_executor(mocker)
        db_path = tmp_path / "test.db"

        # Only one deployment in timeline
        mock_ex.run.return_value = _make_remote_result(
            stdout=json.dumps([{"image": "img", "tag": "v1", "last_id": 1}])
        )

        result = db.rollback(db_path, executor=mock_ex)

        assert result is None

    def test_rollback_local_still_works(self, tmp_path):
        """rollback() without executor should still work via local sqlite3."""
        db_path = tmp_path / "test.db"

        db.record_deployment(db_path, "img", "v1", "deploy")
        db.set_alias(db_path, "CURRENT", "img", "v1")
        db.record_deployment(db_path, "img", "v2", "deploy")
        db.set_alias(db_path, "CURRENT", "img", "v2")

        result = db.rollback(db_path)

        assert result == ("img", "v1")


class TestGetPreviousTagsRemote:
    """Test db.get_previous_tags() with a remote executor."""

    def test_get_previous_tags_remote_queries_via_executor(self, mocker, tmp_path):
        """get_previous_tags() with remote executor should use _remote_query."""
        mock_ex = _make_ssh_executor(mocker)
        db_path = tmp_path / "test.db"

        mock_ex.run.return_value = _make_remote_result(
            stdout=json.dumps(
                [
                    {"image": "img", "tag": "v2", "last_used": "2026-01-02 00:00:00"},
                    {"image": "img", "tag": "v1", "last_used": "2026-01-01 00:00:00"},
                ]
            )
        )

        tags = db.get_previous_tags(db_path, executor=mock_ex)

        assert len(tags) == 2
        assert tags[0] == ("img", "v2", "2026-01-02 00:00:00")
        assert tags[1] == ("img", "v1", "2026-01-01 00:00:00")
        # Verify the sqlite3 -json command was used
        call_args = mock_ex.run.call_args[0][0]
        assert "sqlite3" in call_args
        assert "-json" in call_args

    def test_get_previous_tags_remote_empty_result(self, mocker, tmp_path):
        """get_previous_tags() with remote executor returns empty list when no rows."""
        mock_ex = _make_ssh_executor(mocker)
        db_path = tmp_path / "test.db"

        mock_ex.run.return_value = _make_remote_result(stdout="")

        tags = db.get_previous_tags(db_path, executor=mock_ex)

        assert tags == []

    def test_get_previous_tags_remote_respects_limit(self, mocker, tmp_path):
        """get_previous_tags() with remote executor should pass limit in SQL."""
        mock_ex = _make_ssh_executor(mocker)
        db_path = tmp_path / "test.db"

        mock_ex.run.return_value = _make_remote_result(stdout="[]")

        db.get_previous_tags(db_path, limit=3, executor=mock_ex)

        # The LIMIT clause should contain 3
        call_args = mock_ex.run.call_args[0][0]
        sql = call_args[-1]  # Last arg is the SQL string
        assert "LIMIT 3" in sql

    def test_get_previous_tags_local_still_works(self, tmp_path):
        """get_previous_tags() without executor should still work via local sqlite3."""
        db_path = tmp_path / "test.db"
        db.record_deployment(db_path, "img", "v1", "deploy")
        db.record_deployment(db_path, "img", "v2", "deploy")

        tags = db.get_previous_tags(db_path)

        assert len(tags) == 2
        tag_values = [t[1] for t in tags]
        assert "v1" in tag_values
        assert "v2" in tag_values


class TestRecordDeploymentRemote:
    """Test db.record_deployment() with a remote executor."""

    def test_record_deployment_remote_uses_remote_execute(self, mocker, tmp_path):
        """record_deployment() with remote executor should call _remote_execute."""
        mock_ex = _make_ssh_executor(mocker)
        db_path = tmp_path / "test.db"

        mock_ex.run.return_value = _make_remote_result()

        result = db.record_deployment(
            db_path, "img", "v1.0.0", "deploy", port=7043, executor=mock_ex
        )

        # Remote record_deployment returns 0 (no lastrowid from CLI)
        assert result == 0
        # Verify sqlite3 was called (INSERT)
        mock_ex.run.assert_called_once()
        call_args = mock_ex.run.call_args[0][0]
        assert "sqlite3" in call_args
        assert "INSERT" in call_args[-1]

    def test_record_deployment_remote_includes_all_fields(self, mocker, tmp_path):
        """record_deployment() remote should pass all fields in the SQL."""
        mock_ex = _make_ssh_executor(mocker)
        db_path = tmp_path / "test.db"

        mock_ex.run.return_value = _make_remote_result()

        db.record_deployment(
            db_path,
            "ghcr.io/org/app",
            "v2.0.0",
            "redeploy",
            port=8080,
            success=False,
            notes="test note",
            executor=mock_ex,
        )

        call_args = mock_ex.run.call_args[0][0]
        sql = call_args[-1]
        assert "ghcr.io/org/app" in sql
        assert "v2.0.0" in sql
        assert "redeploy" in sql
        assert "8080" in sql
        assert "test note" in sql

    def test_record_deployment_local_still_works(self, tmp_path):
        """record_deployment() without executor should still work locally."""
        db_path = tmp_path / "test.db"

        deploy_id = db.record_deployment(db_path, "img", "v1", "deploy")

        assert deploy_id == 1
        deployments = db.get_deployments(db_path)
        assert len(deployments) == 1


class TestGetDeploymentsRemote:
    """Test db.get_deployments() with a remote executor."""

    def test_get_deployments_remote_queries_via_executor(self, mocker, tmp_path):
        """get_deployments() with remote executor should use _remote_query."""
        mock_ex = _make_ssh_executor(mocker)
        db_path = tmp_path / "test.db"

        mock_ex.run.return_value = _make_remote_result(
            stdout=json.dumps(
                [
                    {
                        "id": 1,
                        "timestamp": "2026-01-01 12:00:00",
                        "port": 7043,
                        "image": "img",
                        "tag": "v1",
                        "action": "deploy",
                        "success": 1,
                        "notes": None,
                    },
                ]
            )
        )

        deployments = db.get_deployments(db_path, executor=mock_ex)

        assert len(deployments) == 1
        assert deployments[0].image == "img"
        assert deployments[0].tag == "v1"
        assert deployments[0].port == 7043
        assert deployments[0].action == "deploy"
        # Verify sqlite3 -json was used
        call_args = mock_ex.run.call_args[0][0]
        assert "sqlite3" in call_args
        assert "-json" in call_args

    def test_get_deployments_remote_empty_result(self, mocker, tmp_path):
        """get_deployments() with remote executor returns empty list when no rows."""
        mock_ex = _make_ssh_executor(mocker)
        db_path = tmp_path / "test.db"

        mock_ex.run.return_value = _make_remote_result(stdout="")

        deployments = db.get_deployments(db_path, executor=mock_ex)

        assert deployments == []

    def test_get_deployments_remote_respects_limit(self, mocker, tmp_path):
        """get_deployments() with remote executor should pass limit in SQL."""
        mock_ex = _make_ssh_executor(mocker)
        db_path = tmp_path / "test.db"

        mock_ex.run.return_value = _make_remote_result(stdout="[]")

        db.get_deployments(db_path, limit=5, executor=mock_ex)

        call_args = mock_ex.run.call_args[0][0]
        sql = call_args[-1]
        assert "LIMIT 5" in sql

    def test_get_deployments_remote_filters_by_port(self, mocker, tmp_path):
        """get_deployments() with remote executor should filter by port in SQL."""
        mock_ex = _make_ssh_executor(mocker)
        db_path = tmp_path / "test.db"

        mock_ex.run.return_value = _make_remote_result(stdout="[]")

        db.get_deployments(db_path, port=7043, executor=mock_ex)

        call_args = mock_ex.run.call_args[0][0]
        sql = call_args[-1]
        assert "port = 7043" in sql


class TestSetAliasRemote:
    """Test db.set_alias() with a remote executor."""

    def test_set_alias_remote_uses_remote_execute(self, mocker, tmp_path):
        """set_alias() with remote executor should call _remote_execute."""
        mock_ex = _make_ssh_executor(mocker)
        db_path = tmp_path / "test.db"

        mock_ex.run.return_value = _make_remote_result()

        db.set_alias(db_path, "CURRENT", "img", "v1.0.0", executor=mock_ex)

        mock_ex.run.assert_called_once()
        call_args = mock_ex.run.call_args[0][0]
        assert "sqlite3" in call_args
        sql = call_args[-1]
        assert "INSERT" in sql
        assert "CURRENT" in sql

    def test_set_alias_remote_normalizes_to_uppercase(self, mocker, tmp_path):
        """set_alias() with remote executor should uppercase the alias."""
        mock_ex = _make_ssh_executor(mocker)
        db_path = tmp_path / "test.db"

        mock_ex.run.return_value = _make_remote_result()

        db.set_alias(db_path, "current", "img", "v1", executor=mock_ex)

        call_args = mock_ex.run.call_args[0][0]
        sql = call_args[-1]
        assert "'CURRENT'" in sql


class TestGetAliasRemote:
    """Test db.get_alias() with a remote executor."""

    def test_get_alias_remote_returns_alias(self, mocker, tmp_path):
        """get_alias() with remote executor should return an ImageAlias."""
        mock_ex = _make_ssh_executor(mocker)
        db_path = tmp_path / "test.db"

        mock_ex.run.return_value = _make_remote_result(
            stdout=json.dumps(
                [
                    {
                        "alias": "CURRENT",
                        "image": "img",
                        "tag": "v2.0.0",
                        "set_at": "2026-01-15 10:00:00",
                    }
                ]
            )
        )

        alias = db.get_alias(db_path, "CURRENT", executor=mock_ex)

        assert alias is not None
        assert isinstance(alias, db.ImageAlias)
        assert alias.alias == "CURRENT"
        assert alias.image == "img"
        assert alias.tag == "v2.0.0"

    def test_get_alias_remote_returns_none_when_not_found(self, mocker, tmp_path):
        """get_alias() with remote executor returns None when alias not set."""
        mock_ex = _make_ssh_executor(mocker)
        db_path = tmp_path / "test.db"

        mock_ex.run.return_value = _make_remote_result(stdout="")

        alias = db.get_alias(db_path, "NONEXISTENT", executor=mock_ex)

        assert alias is None


class TestGetAllAliasesRemote:
    """Test db.get_all_aliases() with a remote executor."""

    def test_get_all_aliases_remote_returns_list(self, mocker, tmp_path):
        """get_all_aliases() with remote executor should return a list of ImageAlias."""
        mock_ex = _make_ssh_executor(mocker)
        db_path = tmp_path / "test.db"

        mock_ex.run.return_value = _make_remote_result(
            stdout=json.dumps(
                [
                    {"alias": "CURRENT", "image": "img", "tag": "v2", "set_at": "2026-01-15"},
                    {"alias": "ROLLBACK", "image": "img", "tag": "v1", "set_at": "2026-01-14"},
                ]
            )
        )

        aliases = db.get_all_aliases(db_path, executor=mock_ex)

        assert len(aliases) == 2
        assert all(isinstance(a, db.ImageAlias) for a in aliases)

    def test_get_all_aliases_remote_empty(self, mocker, tmp_path):
        """get_all_aliases() with remote executor returns empty list when no aliases."""
        mock_ex = _make_ssh_executor(mocker)
        db_path = tmp_path / "test.db"

        mock_ex.run.return_value = _make_remote_result(stdout="")

        aliases = db.get_all_aliases(db_path, executor=mock_ex)

        assert aliases == []


class TestSetCurrentRemote:
    """Test db.set_current() with a remote executor."""

    def test_set_current_remote_first_time(self, mocker, tmp_path):
        """set_current() with remote executor should set CURRENT on first call."""
        mock_ex = _make_ssh_executor(mocker)
        db_path = tmp_path / "test.db"

        # First call: get_current_image -> get_alias -> _remote_query returns empty
        # Then: set_alias -> _remote_execute, record_deployment -> _remote_execute
        call_count = {"n": 0}

        def mock_run(cmd, **kwargs):
            call_count["n"] += 1
            if "-json" in cmd:
                # get_alias queries return empty (no previous CURRENT)
                return _make_remote_result(stdout="")
            # Write operations succeed
            return _make_remote_result()

        mock_ex.run.side_effect = mock_run

        previous = db.set_current(db_path, "img", "v1.0.0", executor=mock_ex)

        assert previous is None
        # Should have made multiple calls: get_alias + set_alias + record_deployment
        assert mock_ex.run.call_count >= 2

    def test_set_current_remote_moves_previous_to_rollback(self, mocker, tmp_path):
        """set_current() with remote executor should move previous CURRENT to ROLLBACK."""
        mock_ex = _make_ssh_executor(mocker)
        db_path = tmp_path / "test.db"

        call_count = {"n": 0}

        def mock_run(cmd, **kwargs):
            call_count["n"] += 1
            if "-json" in cmd:
                # get_alias returns existing CURRENT
                return _make_remote_result(
                    stdout=json.dumps(
                        [
                            {
                                "alias": "CURRENT",
                                "image": "img",
                                "tag": "v1.0.0",
                                "set_at": "2026-01-01",
                            }
                        ]
                    )
                )
            return _make_remote_result()

        mock_ex.run.side_effect = mock_run

        previous = db.set_current(db_path, "img", "v2.0.0", executor=mock_ex)

        assert previous == "v1.0.0"


class TestGetCurrentImageRemote:
    """Test db.get_current_image() with a remote executor."""

    def test_get_current_image_remote_returns_tuple(self, mocker, tmp_path):
        """get_current_image() with remote executor should return (image, tag)."""
        mock_ex = _make_ssh_executor(mocker)
        db_path = tmp_path / "test.db"

        mock_ex.run.return_value = _make_remote_result(
            stdout=json.dumps(
                [
                    {
                        "alias": "CURRENT",
                        "image": "ghcr.io/org/app",
                        "tag": "v3.0.0",
                        "set_at": "2026-01-20",
                    }
                ]
            )
        )

        result = db.get_current_image(db_path, executor=mock_ex)

        assert result == ("ghcr.io/org/app", "v3.0.0")

    def test_get_current_image_remote_returns_none_when_not_set(self, mocker, tmp_path):
        """get_current_image() with remote executor returns None when CURRENT not set."""
        mock_ex = _make_ssh_executor(mocker)
        db_path = tmp_path / "test.db"

        mock_ex.run.return_value = _make_remote_result(stdout="")

        result = db.get_current_image(db_path, executor=mock_ex)

        assert result is None


class TestGetRollbackImageRemote:
    """Test db.get_rollback_image() with a remote executor."""

    def test_get_rollback_image_remote_returns_tuple(self, mocker, tmp_path):
        """get_rollback_image() with remote executor should return (image, tag)."""
        mock_ex = _make_ssh_executor(mocker)
        db_path = tmp_path / "test.db"

        mock_ex.run.return_value = _make_remote_result(
            stdout=json.dumps(
                [
                    {
                        "alias": "ROLLBACK",
                        "image": "ghcr.io/org/app",
                        "tag": "v2.0.0",
                        "set_at": "2026-01-19",
                    }
                ]
            )
        )

        result = db.get_rollback_image(db_path, executor=mock_ex)

        assert result == ("ghcr.io/org/app", "v2.0.0")

    def test_get_rollback_image_remote_returns_none_when_not_set(self, mocker, tmp_path):
        """get_rollback_image() with remote executor returns None when ROLLBACK not set."""
        mock_ex = _make_ssh_executor(mocker)
        db_path = tmp_path / "test.db"

        mock_ex.run.return_value = _make_remote_result(stdout="")

        result = db.get_rollback_image(db_path, executor=mock_ex)

        assert result is None


class TestInitDbRemote:
    """Test db.init_db() with a remote executor."""

    def test_init_db_remote_sends_schema(self, mocker, tmp_path):
        """init_db() with remote executor should send full SCHEMA via sqlite3."""
        mock_ex = _make_ssh_executor(mocker)
        db_path = tmp_path / "test.db"

        mock_ex.run.return_value = _make_remote_result()

        db.init_db(db_path, executor=mock_ex)

        mock_ex.run.assert_called_once()
        call_args = mock_ex.run.call_args[0][0]
        assert "sqlite3" in call_args
        # Should contain the schema SQL
        assert "CREATE TABLE" in call_args[-1]


class TestGetConnectionGuard:
    """Test that get_connection raises on remote executor."""

    def test_get_connection_raises_on_remote_executor(self, mocker, tmp_path):
        """get_connection() should raise ValueError with a remote executor."""
        mock_ex = _make_ssh_executor(mocker)
        db_path = tmp_path / "test.db"

        with pytest.raises(ValueError, match="cannot be used with a remote executor"):
            with db.get_connection(db_path, executor=mock_ex):
                pass

    def test_get_connection_works_with_none_executor(self, tmp_path):
        """get_connection() should work normally with executor=None."""
        db_path = tmp_path / "test.db"

        with db.get_connection(db_path, executor=None) as conn:
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }

        assert "deployments" in tables


class TestRecordServiceInstanceRemote:
    """Test record_service_instance() with a remote executor."""

    def test_record_service_instance_remote(self, mocker, tmp_path):
        """record_service_instance() with remote executor should use _remote_execute."""
        mock_ex = _make_ssh_executor(mocker)
        db_path = tmp_path / "test.db"
        mock_ex.run.return_value = _make_remote_result()

        result = db.record_service_instance(
            db_path,
            "valkey",
            "6379",
            "/etc/valkey/6379.conf",
            "/var/lib/valkey/6379",
            port=6379,
            executor=mock_ex,
        )

        assert result == 0
        mock_ex.run.assert_called_once()
        call_args = mock_ex.run.call_args[0][0]
        assert "sqlite3" in call_args
        assert "INSERT" in call_args[-1]

    def test_record_service_instance_local_still_works(self, tmp_path):
        """record_service_instance() without executor should still work locally."""
        db_path = tmp_path / "test.db"

        instance_id = db.record_service_instance(
            db_path,
            "valkey",
            "6379",
            "/c",
            "/d",
            port=6379,
        )

        assert instance_id >= 1


class TestGetServiceInstanceRemote:
    """Test get_service_instance() with a remote executor."""

    def test_get_service_instance_remote_found(self, mocker, tmp_path):
        """get_service_instance() with remote executor should return ServiceInstance."""
        mock_ex = _make_ssh_executor(mocker)
        db_path = tmp_path / "test.db"

        mock_ex.run.return_value = _make_remote_result(
            stdout=json.dumps(
                [
                    {
                        "id": 1,
                        "package": "valkey",
                        "instance": "6379",
                        "config_file": "/etc/valkey/6379.conf",
                        "data_dir": "/var/lib/valkey/6379",
                        "port": 6379,
                        "created_at": "2026-01-01",
                        "updated_at": "2026-01-01",
                        "notes": None,
                    }
                ]
            )
        )

        svc = db.get_service_instance(db_path, "valkey", "6379", executor=mock_ex)

        assert svc is not None
        assert isinstance(svc, db.ServiceInstance)
        assert svc.package == "valkey"
        assert svc.port == 6379

    def test_get_service_instance_remote_not_found(self, mocker, tmp_path):
        """get_service_instance() with remote executor returns None when not found."""
        mock_ex = _make_ssh_executor(mocker)
        db_path = tmp_path / "test.db"
        mock_ex.run.return_value = _make_remote_result(stdout="")

        svc = db.get_service_instance(db_path, "valkey", "9999", executor=mock_ex)

        assert svc is None


class TestGetServiceInstancesRemote:
    """Test get_service_instances() with a remote executor."""

    def test_get_service_instances_remote(self, mocker, tmp_path):
        """get_service_instances() with remote executor returns list."""
        mock_ex = _make_ssh_executor(mocker)
        db_path = tmp_path / "test.db"

        mock_ex.run.return_value = _make_remote_result(
            stdout=json.dumps(
                [
                    {
                        "id": 1,
                        "package": "valkey",
                        "instance": "6379",
                        "config_file": "/c1",
                        "data_dir": "/d1",
                        "port": 6379,
                        "created_at": "2026-01-01",
                        "updated_at": "2026-01-01",
                        "notes": None,
                    },
                    {
                        "id": 2,
                        "package": "valkey",
                        "instance": "6380",
                        "config_file": "/c2",
                        "data_dir": "/d2",
                        "port": 6380,
                        "created_at": "2026-01-01",
                        "updated_at": "2026-01-01",
                        "notes": None,
                    },
                ]
            )
        )

        instances = db.get_service_instances(db_path, executor=mock_ex)

        assert len(instances) == 2
        assert all(isinstance(i, db.ServiceInstance) for i in instances)

    def test_get_service_instances_remote_empty(self, mocker, tmp_path):
        """get_service_instances() with remote executor returns empty list."""
        mock_ex = _make_ssh_executor(mocker)
        db_path = tmp_path / "test.db"
        mock_ex.run.return_value = _make_remote_result(stdout="")

        instances = db.get_service_instances(db_path, executor=mock_ex)

        assert instances == []


class TestDeleteServiceInstanceRemote:
    """Test delete_service_instance() with a remote executor."""

    def test_delete_service_instance_remote_found(self, mocker, tmp_path):
        """delete_service_instance() with remote executor returns True when exists."""
        mock_ex = _make_ssh_executor(mocker)
        db_path = tmp_path / "test.db"

        # First call: COUNT query returns 1; second: DELETE
        call_count = {"n": 0}

        def mock_run(cmd, **kwargs):
            call_count["n"] += 1
            if "-json" in cmd:
                return _make_remote_result(stdout=json.dumps([{"cnt": 1}]))
            return _make_remote_result()

        mock_ex.run.side_effect = mock_run

        deleted = db.delete_service_instance(db_path, "valkey", "6379", executor=mock_ex)

        assert deleted is True

    def test_delete_service_instance_remote_not_found(self, mocker, tmp_path):
        """delete_service_instance() with remote executor returns False when not exists."""
        mock_ex = _make_ssh_executor(mocker)
        db_path = tmp_path / "test.db"

        mock_ex.run.return_value = _make_remote_result(stdout=json.dumps([{"cnt": 0}]))

        deleted = db.delete_service_instance(db_path, "valkey", "9999", executor=mock_ex)

        assert deleted is False


class TestRecordServiceActionRemote:
    """Test record_service_action() with a remote executor."""

    def test_record_service_action_remote(self, mocker, tmp_path):
        """record_service_action() with remote executor should use _remote_execute."""
        mock_ex = _make_ssh_executor(mocker)
        db_path = tmp_path / "test.db"
        mock_ex.run.return_value = _make_remote_result()

        result = db.record_service_action(
            db_path,
            "valkey",
            "6379",
            "init",
            executor=mock_ex,
        )

        assert result == 0
        mock_ex.run.assert_called_once()
        sql = mock_ex.run.call_args[0][0][-1]
        assert "INSERT" in sql

    def test_record_service_action_local_still_works(self, tmp_path):
        """record_service_action() without executor should still work locally."""
        db_path = tmp_path / "test.db"

        action_id = db.record_service_action(db_path, "valkey", "6379", "start")

        assert action_id >= 1


class TestGetServiceActionsRemote:
    """Test get_service_actions() with a remote executor."""

    def test_get_service_actions_remote(self, mocker, tmp_path):
        """get_service_actions() with remote executor returns list."""
        mock_ex = _make_ssh_executor(mocker)
        db_path = tmp_path / "test.db"

        mock_ex.run.return_value = _make_remote_result(
            stdout=json.dumps(
                [
                    {
                        "id": 1,
                        "timestamp": "2026-01-01 12:00:00",
                        "package": "valkey",
                        "instance": "6379",
                        "action": "start",
                        "success": 1,
                        "notes": None,
                    },
                ]
            )
        )

        actions = db.get_service_actions(db_path, executor=mock_ex)

        assert len(actions) == 1
        assert isinstance(actions[0], db.ServiceAction)
        assert actions[0].package == "valkey"
        assert actions[0].action == "start"

    def test_get_service_actions_remote_filtered(self, mocker, tmp_path):
        """get_service_actions() with remote executor should filter by package."""
        mock_ex = _make_ssh_executor(mocker)
        db_path = tmp_path / "test.db"
        mock_ex.run.return_value = _make_remote_result(stdout="[]")

        db.get_service_actions(db_path, package="valkey", executor=mock_ex)

        sql = mock_ex.run.call_args[0][0][-1]
        assert "package = 'valkey'" in sql

    def test_get_service_actions_remote_empty(self, mocker, tmp_path):
        """get_service_actions() with remote executor returns empty list."""
        mock_ex = _make_ssh_executor(mocker)
        db_path = tmp_path / "test.db"
        mock_ex.run.return_value = _make_remote_result(stdout="")

        actions = db.get_service_actions(db_path, executor=mock_ex)

        assert actions == []


# ---------------------------------------------------------------------------
# Parametrized cross-executor tests for db operations
# ---------------------------------------------------------------------------


def _make_remote_executor_for_db(mocker, db_path):
    """Build a mock SSH executor wired up for db operations.

    Returns (executor, spy) where spy is the mock.run method for assertions.
    The mock patches _is_remote to return True for this executor.
    """
    mock_ex = MagicMock()
    mocker.patch("ots_containers.db._is_remote", side_effect=lambda ex: ex is mock_ex)
    mock_ex.run.return_value = _make_remote_result()
    return mock_ex


@pytest.fixture(
    params=["local", "remote"],
    ids=["local-executor", "remote-executor"],
)
def db_executor_pair(request, mocker, tmp_path):
    """Parametrized fixture for db operations across executor types.

    For local: executor is None (uses sqlite3 directly).
    For remote: executor is a mock SSHExecutor with _is_remote() returning True.

    Yields (db_path, executor, kind).
    """
    db_path = tmp_path / "test.db"
    if request.param == "local":
        return db_path, None, "local"
    else:
        mock_ex = _make_remote_executor_for_db(mocker, db_path)
        return db_path, mock_ex, "remote"


class TestCrossExecutorDbOperations:
    """Verify that db operations have consistent API contracts across executor types.

    Local operations use sqlite3 directly; remote operations use the sqlite3 CLI
    via executor.run(). These tests verify the API-level contract is the same.
    """

    def test_init_db_does_not_raise(self, db_executor_pair):
        """init_db should complete without error via both executors."""
        db_path, executor, kind = db_executor_pair

        # Should not raise
        db.init_db(db_path, executor=executor)

        if kind == "local":
            assert db_path.exists()

    def test_record_deployment_returns_int(self, db_executor_pair):
        """record_deployment should return an int via both executors."""
        db_path, executor, kind = db_executor_pair

        result = db.record_deployment(db_path, "img", "v1", "deploy", port=7043, executor=executor)

        assert isinstance(result, int)

    def test_set_alias_and_get_alias_roundtrip(self, db_executor_pair):
        """set_alias + get_alias should round-trip via both executors."""
        db_path, executor, kind = db_executor_pair

        if kind == "remote":
            # Wire the mock to return the alias we set
            call_count = {"n": 0}

            def mock_run(cmd, **kwargs):
                call_count["n"] += 1
                if "-json" in cmd:
                    return _make_remote_result(
                        stdout=json.dumps(
                            [
                                {
                                    "alias": "CURRENT",
                                    "image": "img",
                                    "tag": "v1",
                                    "set_at": "2026-01-01",
                                }
                            ]
                        )
                    )
                return _make_remote_result()

            executor.run.side_effect = mock_run

        db.set_alias(db_path, "CURRENT", "img", "v1", executor=executor)
        alias = db.get_alias(db_path, "CURRENT", executor=executor)

        assert alias is not None
        assert alias.alias == "CURRENT"
        assert alias.image == "img"
        assert alias.tag == "v1"
        assert isinstance(alias, db.ImageAlias)

    def test_get_deployments_returns_list(self, db_executor_pair):
        """get_deployments should return a list via both executors."""
        db_path, executor, kind = db_executor_pair

        if kind == "remote":
            executor.run.return_value = _make_remote_result(stdout="[]")

        result = db.get_deployments(db_path, executor=executor)

        assert isinstance(result, list)

    def test_record_service_action_returns_int(self, db_executor_pair):
        """record_service_action should return an int via both executors."""
        db_path, executor, kind = db_executor_pair

        result = db.record_service_action(db_path, "valkey", "6379", "start", executor=executor)

        assert isinstance(result, int)

    def test_get_service_instances_returns_list(self, db_executor_pair):
        """get_service_instances should return a list via both executors."""
        db_path, executor, kind = db_executor_pair

        if kind == "remote":
            executor.run.return_value = _make_remote_result(stdout="[]")

        result = db.get_service_instances(db_path, executor=executor)

        assert isinstance(result, list)

    def test_get_current_image_returns_none_when_empty(self, db_executor_pair):
        """get_current_image should return None when CURRENT not set."""
        db_path, executor, kind = db_executor_pair

        if kind == "remote":
            executor.run.return_value = _make_remote_result(stdout="")

        result = db.get_current_image(db_path, executor=executor)

        assert result is None

    def test_rollback_returns_none_when_insufficient_history(self, db_executor_pair):
        """rollback should return None when < 2 distinct deployments."""
        db_path, executor, kind = db_executor_pair

        if kind == "remote":
            executor.run.return_value = _make_remote_result(
                stdout=json.dumps([{"image": "img", "tag": "v1", "last_id": 1}])
            )

        result = db.rollback(db_path, executor=executor)

        assert result is None
