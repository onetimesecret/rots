# tests/commands/dns/test_db.py
"""Tests for DNS database functions."""

import pytest

from rots.db import (
    delete_dns_current,
    get_all_dns_current,
    get_dns_current,
    get_dns_history,
    init_db,
    record_dns_action,
    upsert_dns_current,
)


class TestDnsCurrent:
    """Test DNS current-state CRUD operations."""

    @pytest.fixture(autouse=True)
    def db_path(self, tmp_path):
        self.db_path = tmp_path / "test.db"
        init_db(self.db_path)
        return self.db_path

    def test_upsert_dns_current(self):
        """Insert then retrieve a DNS current record."""
        upsert_dns_current(self.db_path, "app.onetime.dev", "A", "1.2.3.4", 300, "cloudflare")
        rec = get_dns_current(self.db_path, "app.onetime.dev")
        assert rec is not None
        assert rec.hostname == "app.onetime.dev"
        assert rec.record_type == "A"
        assert rec.value == "1.2.3.4"
        assert rec.ttl == 300
        assert rec.provider == "cloudflare"

    def test_upsert_dns_current_update(self):
        """Upsert with new value updates the existing record."""
        upsert_dns_current(self.db_path, "app.onetime.dev", "A", "1.2.3.4", 300, "cloudflare")
        upsert_dns_current(self.db_path, "app.onetime.dev", "A", "5.6.7.8", 600, "cloudflare")
        rec = get_dns_current(self.db_path, "app.onetime.dev")
        assert rec is not None
        assert rec.value == "5.6.7.8"
        assert rec.ttl == 600

    def test_get_dns_current_not_found(self):
        """Non-existent hostname returns None."""
        assert get_dns_current(self.db_path, "missing.onetime.dev") is None

    def test_get_all_dns_current(self):
        """Insert multiple records, list all returns them sorted."""
        upsert_dns_current(self.db_path, "b.onetime.dev", "A", "1.1.1.1", 300, "cloudflare")
        upsert_dns_current(self.db_path, "a.onetime.dev", "A", "2.2.2.2", 300, "cloudflare")
        records = get_all_dns_current(self.db_path)
        assert len(records) == 2
        assert records[0].hostname == "a.onetime.dev"
        assert records[1].hostname == "b.onetime.dev"

    def test_delete_dns_current(self):
        """Delete an existing record returns True, record is gone."""
        upsert_dns_current(self.db_path, "app.onetime.dev", "A", "1.2.3.4", 300, "cloudflare")
        assert delete_dns_current(self.db_path, "app.onetime.dev") is True
        assert get_dns_current(self.db_path, "app.onetime.dev") is None

    def test_delete_dns_current_not_found(self):
        """Delete a non-existent record returns False."""
        assert delete_dns_current(self.db_path, "missing.onetime.dev") is False


class TestDnsHistory:
    """Test DNS action audit trail."""

    @pytest.fixture(autouse=True)
    def db_path(self, tmp_path):
        self.db_path = tmp_path / "test.db"
        init_db(self.db_path)
        return self.db_path

    def test_record_dns_action(self):
        """Record an action and retrieve it via history."""
        record_dns_action(
            self.db_path,
            hostname="app.onetime.dev",
            record_type="A",
            value="1.2.3.4",
            ttl=300,
            provider="cloudflare",
            action="create",
        )
        history = get_dns_history(self.db_path, "app.onetime.dev")
        assert len(history) == 1
        rec = history[0]
        assert rec.hostname == "app.onetime.dev"
        assert rec.record_type == "A"
        assert rec.value == "1.2.3.4"
        assert rec.action == "create"
        assert rec.success is True
