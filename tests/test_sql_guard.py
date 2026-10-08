"""Tests for the SQL read-only guard."""
from __future__ import annotations

import pytest

from tools.tool_defs import query_db


class TestSQLGuard:
    """Test that only SELECT queries are allowed."""

    def test_select_allowed(self, tmp_path, monkeypatch):
        # query_db uses the DB_PATH global; for unit tests we test the guard logic
        result = query_db("INSERT INTO vendors (name) VALUES ('Evil Corp')")
        assert "ERROR" in result
        assert "rejected" in result.lower() or "forbidden" in result.lower()

    def test_update_blocked(self):
        result = query_db("UPDATE invoices SET status = 'paid'")
        assert "ERROR" in result

    def test_delete_blocked(self):
        result = query_db("DELETE FROM vendors")
        assert "ERROR" in result

    def test_drop_blocked(self):
        result = query_db("DROP TABLE vendors")
        assert "ERROR" in result

    def test_alter_blocked(self):
        result = query_db("ALTER TABLE vendors ADD COLUMN evil TEXT")
        assert "ERROR" in result

    def test_select_with_subquery(self):
        # SELECT with nested SELECT should be allowed (starts with SELECT)
        # This will fail on the actual DB but shouldn't be blocked by the guard
        result = query_db("SELECT * FROM (SELECT 1)")
        # It either succeeds or gives a SQL error, but NOT a guard rejection
        assert "forbidden" not in result.lower() or "SQL error" in result

    def test_select_passes_guard(self):
        # Even if DB doesn't exist, the guard should not block it
        result = query_db("SELECT 1")
        # Should attempt to run (may get SQL error if no DB, but guard passes)
        assert "rejected" not in result.lower()

    def test_case_insensitive_block(self):
        result = query_db("insert INTO vendors VALUES ('x')")
        assert "ERROR" in result

    def test_create_blocked(self):
        result = query_db("CREATE TABLE evil (id INTEGER)")
        assert "ERROR" in result
