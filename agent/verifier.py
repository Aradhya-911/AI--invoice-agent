"""Verifier – checks agent claims against the database independently.

This module is completely independent of the agent loop and browser.
It reads SQLite directly to verify that the agent's claimed outcomes
match reality.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "company.db"


@dataclass
class ClaimResult:
    """Result of verifying a single claim."""
    claim: dict
    passed: bool
    detail: str


@dataclass
class VerificationResult:
    """Overall verification result."""
    passed: bool
    results: list[ClaimResult] = field(default_factory=list)
    summary_valid: bool = True

    def to_dict(self) -> dict:
        """Serialize for saving to verification.json."""
        return {
            "passed": self.passed,
            "summary_valid": self.summary_valid,
            "claims": [
                {
                    "claim": r.claim,
                    "passed": r.passed,
                    "detail": r.detail,
                }
                for r in self.results
            ],
        }


def verify_claim(claim: dict, db_path: Path | None = None) -> ClaimResult:
    """Verify a single claim against the database.

    A claim looks like:
        {
            "table": "invoices",
            "where": {"number": "INV-1001"},
            "expect": {"amount": 42000, "due_date": "2026-10-12"}
        }

    Args:
        claim: The claim dict with table, where, and expect keys.
        db_path: Optional DB path override for testing.

    Returns:
        ClaimResult with pass/fail and details.
    """
    dp = db_path or DB_PATH
    table = claim.get("table", "")
    where = claim.get("where", {})
    expect = claim.get("expect", {})

    if not table or not where:
        return ClaimResult(claim=claim, passed=False, detail="Claim missing 'table' or 'where'.")

    # Build SELECT query
    where_clauses = [f'"{k}" = ?' for k in where]
    where_vals = list(where.values())
    sql = f'SELECT * FROM "{table}" WHERE {" AND ".join(where_clauses)}'

    conn = sqlite3.connect(str(dp))
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(sql, where_vals).fetchone()
    except sqlite3.Error as e:
        return ClaimResult(claim=claim, passed=False, detail=f"SQL error: {e}")
    finally:
        conn.close()

    if not row:
        return ClaimResult(
            claim=claim,
            passed=False,
            detail=f"No row found in '{table}' matching {where}.",
        )

    # Check expected values
    row_dict = dict(row)
    mismatches = []
    for col, expected_val in expect.items():
        actual_val = row_dict.get(col)
        if actual_val is None and col not in row_dict:
            mismatches.append(f"Column '{col}' not found in table.")
            continue
        # Flexible comparison: handle type differences
        if not _values_match(actual_val, expected_val):
            mismatches.append(
                f"Column '{col}': expected {expected_val!r}, got {actual_val!r}."
            )

    if mismatches:
        return ClaimResult(
            claim=claim,
            passed=False,
            detail="Mismatches: " + "; ".join(mismatches),
        )

    return ClaimResult(claim=claim, passed=True, detail="All expected values match.")


def _values_match(actual: Any, expected: Any) -> bool:
    """Compare values flexibly (handle int/float, string coercion)."""
    if actual == expected:
        return True
    # Numeric comparison with tolerance
    try:
        return abs(float(actual) - float(expected)) < 0.01
    except (ValueError, TypeError):
        pass
    # String comparison
    return str(actual).strip() == str(expected).strip()


def verify_claims(
    claims: list[dict],
    db_path: Path | None = None,
) -> VerificationResult:
    """Verify all claims and return an overall result.

    Args:
        claims: List of claim dicts.
        db_path: Optional DB path override for testing.

    Returns:
        VerificationResult with individual and overall results.
    """
    if not claims:
        return VerificationResult(passed=True, results=[], summary_valid=True)

    results = [verify_claim(c, db_path=db_path) for c in claims]
    all_passed = all(r.passed for r in results)

    return VerificationResult(passed=all_passed, results=results)


def save_verification(result: VerificationResult, run_dir: Path) -> Path:
    """Save verification result to the run directory."""
    path = run_dir / "verification.json"
    with open(path, "w") as f:
        json.dump(result.to_dict(), f, indent=2)
    return path
