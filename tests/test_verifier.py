"""Tests for the verifier."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from agent.verifier import verify_claim, verify_claims, ClaimResult


def _setup_db(tmp_path: Path) -> Path:
    """Create a test database with sample data."""
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE invoices (
            id INTEGER PRIMARY KEY,
            vendor TEXT,
            number TEXT UNIQUE,
            amount REAL,
            due_date TEXT,
            status TEXT DEFAULT 'unpaid'
        );
        INSERT INTO invoices (vendor, number, amount, due_date, status)
            VALUES ('Acme Supplies', 'INV-1001', 42000.0, '2026-10-12', 'unpaid');
        INSERT INTO invoices (vendor, number, amount, due_date, status)
            VALUES ('Beta Corp', 'INV-2001', 18500.0, '2026-10-15', 'paid');
    """)
    conn.commit()
    conn.close()
    return db_path


class TestVerifyClaim:
    def test_matching_claim(self, tmp_path):
        db = _setup_db(tmp_path)
        claim = {
            "table": "invoices",
            "where": {"number": "INV-1001"},
            "expect": {"amount": 42000, "due_date": "2026-10-12"},
        }
        result = verify_claim(claim, db_path=db)
        assert result.passed is True

    def test_wrong_amount(self, tmp_path):
        db = _setup_db(tmp_path)
        claim = {
            "table": "invoices",
            "where": {"number": "INV-1001"},
            "expect": {"amount": 99999},
        }
        result = verify_claim(claim, db_path=db)
        assert result.passed is False
        assert "amount" in result.detail.lower()

    def test_missing_row(self, tmp_path):
        db = _setup_db(tmp_path)
        claim = {
            "table": "invoices",
            "where": {"number": "INV-NONEXISTENT"},
            "expect": {"amount": 1},
        }
        result = verify_claim(claim, db_path=db)
        assert result.passed is False
        assert "No row found" in result.detail

    def test_missing_table_key(self):
        claim = {"where": {"id": 1}, "expect": {"amount": 1}}
        result = verify_claim(claim)
        assert result.passed is False
        assert "missing" in result.detail.lower()

    def test_float_int_match(self, tmp_path):
        """Verify that 42000.0 matches 42000 (int)."""
        db = _setup_db(tmp_path)
        claim = {
            "table": "invoices",
            "where": {"number": "INV-1001"},
            "expect": {"amount": 42000},  # int, DB has 42000.0
        }
        result = verify_claim(claim, db_path=db)
        assert result.passed is True

    def test_status_check(self, tmp_path):
        db = _setup_db(tmp_path)
        claim = {
            "table": "invoices",
            "where": {"number": "INV-2001"},
            "expect": {"status": "paid"},
        }
        result = verify_claim(claim, db_path=db)
        assert result.passed is True


class TestVerifyClaims:
    def test_all_pass(self, tmp_path):
        db = _setup_db(tmp_path)
        claims = [
            {"table": "invoices", "where": {"number": "INV-1001"}, "expect": {"amount": 42000}},
            {"table": "invoices", "where": {"number": "INV-2001"}, "expect": {"status": "paid"}},
        ]
        result = verify_claims(claims, db_path=db)
        assert result.passed is True
        assert len(result.results) == 2

    def test_one_fails(self, tmp_path):
        db = _setup_db(tmp_path)
        claims = [
            {"table": "invoices", "where": {"number": "INV-1001"}, "expect": {"amount": 42000}},
            {"table": "invoices", "where": {"number": "INV-1001"}, "expect": {"amount": 99999}},
        ]
        result = verify_claims(claims, db_path=db)
        assert result.passed is False

    def test_empty_claims(self):
        result = verify_claims([])
        assert result.passed is True
