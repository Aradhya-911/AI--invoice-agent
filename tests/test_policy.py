"""Tests for the policy engine."""
from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pytest

from agent.policy import (
    check_field_max,
    check_must_exist,
    check_must_not_exist,
    check_policy,
    PolicyViolation,
)


def _setup_test_db(tmp_path: Path) -> Path:
    """Create a test database with sample data."""
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE vendors (id INTEGER PRIMARY KEY, name TEXT UNIQUE);
        CREATE TABLE invoices (id INTEGER PRIMARY KEY, number TEXT UNIQUE);
        INSERT INTO vendors (name) VALUES ('Acme Supplies');
        INSERT INTO vendors (name) VALUES ('Beta Corp');
        INSERT INTO invoices (number) VALUES ('INV-0001');
    """)
    conn.commit()
    conn.close()
    return db_path


class TestFieldMax:
    def test_under_limit(self):
        rule = {"field": "amount", "max": 50000, "message": "Over limit"}
        result = check_field_max(rule, {"amount": "42000"})
        assert result is None

    def test_over_limit(self):
        rule = {"field": "amount", "max": 50000, "message": "Over limit"}
        result = check_field_max(rule, {"amount": "75000"})
        assert result is not None
        assert result.rule_type == "field_max"

    def test_exactly_at_limit(self):
        rule = {"field": "amount", "max": 50000, "message": "Over limit"}
        result = check_field_max(rule, {"amount": "50000"})
        assert result is None

    def test_with_currency_symbols(self):
        rule = {"field": "amount", "max": 50000, "message": "Over limit"}
        result = check_field_max(rule, {"amount": "₹75,000.00"})
        assert result is not None

    def test_empty_field(self):
        rule = {"field": "amount", "max": 50000, "message": "Over limit"}
        result = check_field_max(rule, {"amount": ""})
        assert result is None

    def test_missing_field(self):
        rule = {"field": "amount", "max": 50000, "message": "Over limit"}
        result = check_field_max(rule, {})
        assert result is None


class TestMustExist:
    def test_vendor_exists(self, tmp_path):
        db_path = _setup_test_db(tmp_path)
        rule = {"field": "vendor", "sql": "SELECT 1 FROM vendors WHERE name = :value", "message": "Not found"}
        result = check_must_exist(rule, {"vendor": "Acme Supplies"}, db_path=db_path)
        assert result is None

    def test_vendor_not_exists(self, tmp_path):
        db_path = _setup_test_db(tmp_path)
        rule = {"field": "vendor", "sql": "SELECT 1 FROM vendors WHERE name = :value", "message": "'{value}' not found"}
        result = check_must_exist(rule, {"vendor": "Nonexistent Corp"}, db_path=db_path)
        assert result is not None
        assert "Nonexistent Corp" in result.message


class TestMustNotExist:
    def test_invoice_not_exists(self, tmp_path):
        db_path = _setup_test_db(tmp_path)
        rule = {"field": "number", "sql": "SELECT 1 FROM invoices WHERE number = :value", "message": "Already exists"}
        result = check_must_not_exist(rule, {"number": "INV-9999"}, db_path=db_path)
        assert result is None

    def test_invoice_exists(self, tmp_path):
        db_path = _setup_test_db(tmp_path)
        rule = {"field": "number", "sql": "SELECT 1 FROM invoices WHERE number = :value", "message": "'{value}' already exists"}
        result = check_must_not_exist(rule, {"number": "INV-0001"}, db_path=db_path)
        assert result is not None


class TestCheckPolicy:
    def test_all_pass(self, tmp_path):
        db_path = _setup_test_db(tmp_path)
        rules = [
            {"type": "field_max", "field": "amount", "max": 50000, "message": "Over limit"},
            {"type": "must_exist", "field": "vendor", "sql": "SELECT 1 FROM vendors WHERE name = :value", "message": "Not found"},
        ]
        result = check_policy({"amount": "42000", "vendor": "Acme Supplies"}, rules, db_path=db_path)
        assert result.allowed is True
        assert len(result.violations) == 0

    def test_amount_blocked(self, tmp_path):
        db_path = _setup_test_db(tmp_path)
        rules = [
            {"type": "field_max", "field": "amount", "max": 50000, "message": "Over limit"},
        ]
        result = check_policy({"amount": "60000"}, rules, db_path=db_path)
        assert result.allowed is False
        assert len(result.violations) == 1

    def test_multiple_violations(self, tmp_path):
        db_path = _setup_test_db(tmp_path)
        rules = [
            {"type": "field_max", "field": "amount", "max": 50000, "message": "Over limit"},
            {"type": "must_exist", "field": "vendor", "sql": "SELECT 1 FROM vendors WHERE name = :value", "message": "Not found"},
        ]
        result = check_policy({"amount": "60000", "vendor": "FakeCo"}, rules, db_path=db_path)
        assert result.allowed is False
        assert len(result.violations) == 2
