"""Policy engine – enforces rules at form-submission time.

Rules are loaded from policy.yaml. Each rule is checked against the
form values the agent has filled before a submit-click is allowed.
This enforcement happens in the tool dispatcher, NOT in the prompt,
so the model cannot bypass it.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
POLICY_PATH = ROOT / "policy.yaml"
DB_PATH = ROOT / "company.db"


@dataclass
class PolicyViolation:
    """A single policy violation."""
    rule_type: str
    field: str
    message: str


@dataclass
class PolicyResult:
    """Result of checking all policy rules."""
    allowed: bool
    violations: list[PolicyViolation] = field(default_factory=list)

    @property
    def reason(self) -> str:
        return "; ".join(v.message for v in self.violations)


def load_rules(path: Path | None = None) -> list[dict]:
    """Load policy rules from YAML file."""
    p = path or POLICY_PATH
    if not p.exists():
        return []
    with open(p) as f:
        data = yaml.safe_load(f)
    return data.get("rules", [])


def check_field_max(rule: dict, form_values: dict[str, str]) -> PolicyViolation | None:
    """Check if a field value exceeds the maximum allowed."""
    field_name = rule["field"]
    value_str = form_values.get(field_name, "")
    if not value_str:
        return None
    try:
        value = float(str(value_str).replace(",", "").replace("₹", "").strip())
    except (ValueError, TypeError):
        return None
    max_val = rule["max"]
    if value > max_val:
        return PolicyViolation(
            rule_type="field_max",
            field=field_name,
            message=rule.get("message", f"Field '{field_name}' value {value} exceeds max {max_val}."),
        )
    return None


def check_must_exist(rule: dict, form_values: dict[str, str], db_path: Path | None = None) -> PolicyViolation | None:
    """Check that a referenced entity exists in the database."""
    field_name = rule["field"]
    value = form_values.get(field_name, "")
    if not value:
        return None
    dp = db_path or DB_PATH
    conn = sqlite3.connect(str(dp))
    try:
        sql = rule["sql"]
        row = conn.execute(sql, {"value": value}).fetchone()
        if not row:
            msg = rule.get("message", f"'{value}' not found.").replace("{value}", value)
            return PolicyViolation(rule_type="must_exist", field=field_name, message=msg)
    finally:
        conn.close()
    return None


def check_must_not_exist(rule: dict, form_values: dict[str, str], db_path: Path | None = None) -> PolicyViolation | None:
    """Check that a value does NOT already exist in the database (e.g., duplicate check)."""
    field_name = rule["field"]
    value = form_values.get(field_name, "")
    if not value:
        return None
    dp = db_path or DB_PATH
    conn = sqlite3.connect(str(dp))
    try:
        sql = rule["sql"]
        row = conn.execute(sql, {"value": value}).fetchone()
        if row:
            msg = rule.get("message", f"'{value}' already exists.").replace("{value}", value)
            return PolicyViolation(rule_type="must_not_exist", field=field_name, message=msg)
    finally:
        conn.close()
    return None


_CHECKERS = {
    "field_max": check_field_max,
    "must_exist": check_must_exist,
    "must_not_exist": check_must_not_exist,
}


def check_policy(
    form_values: dict[str, str],
    rules: list[dict] | None = None,
    db_path: Path | None = None,
) -> PolicyResult:
    """Check all policy rules against the given form values.

    Args:
        form_values: Dict of field name -> value currently in the form.
        rules: Policy rules (loaded from YAML if not provided).
        db_path: Optional override for the database path (for testing).

    Returns:
        PolicyResult indicating whether the submission is allowed.
    """
    if rules is None:
        rules = load_rules()

    violations: list[PolicyViolation] = []
    for rule in rules:
        rule_type = rule.get("type", "")
        checker = _CHECKERS.get(rule_type)
        if not checker:
            continue
        # Pass db_path for DB-based checks
        import inspect
        sig = inspect.signature(checker)
        if "db_path" in sig.parameters:
            violation = checker(rule, form_values, db_path=db_path)
        else:
            violation = checker(rule, form_values)
        if violation:
            violations.append(violation)

    return PolicyResult(
        allowed=len(violations) == 0,
        violations=violations,
    )
