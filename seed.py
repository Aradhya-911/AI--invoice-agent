#!/usr/bin/env python3
"""Seed the company database and inbox for a given test scenario.

Usage: python seed.py --scenario <clean|duplicate|over_threshold|form_error|flaky>
"""
import argparse
import json
import os
import sqlite3
from datetime import date, timedelta
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "company.db"
INBOX = ROOT / "inbox"


def _reset_db() -> None:
    """Drop and recreate all tables."""
    if DB_PATH.exists():
        DB_PATH.unlink()
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    c.executescript("""
        CREATE TABLE vendors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE
        );
        CREATE TABLE purchase_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            po_number TEXT NOT NULL UNIQUE,
            vendor TEXT NOT NULL,
            amount REAL NOT NULL
        );
        CREATE TABLE invoices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            vendor TEXT NOT NULL,
            number TEXT NOT NULL UNIQUE,
            amount REAL NOT NULL,
            due_date TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'unpaid'
        );
    """)
    # Seed vendors
    vendors = ["Acme Supplies", "Beta Corp", "Gamma Industries", "Delta Services"]
    for v in vendors:
        c.execute("INSERT INTO vendors (name) VALUES (?)", (v,))
    # Seed purchase orders
    pos = [
        ("PO-2001", "Acme Supplies", 42000.0),
        ("PO-2002", "Beta Corp", 18500.0),
        ("PO-2003", "Gamma Industries", 75000.0),
        ("PO-2004", "Delta Services", 9200.0),
    ]
    for po_num, vendor, amt in pos:
        c.execute("INSERT INTO purchase_orders (po_number, vendor, amount) VALUES (?, ?, ?)",
                  (po_num, vendor, amt))
    # Seed initial invoices
    today = date.today()
    sample_invoices = [
        ("Acme Supplies", "INV-1000", 25000.0, (today + timedelta(days=3)).isoformat(), "unpaid"),
        ("Beta Corp", "INV-2000", 18500.0, (today + timedelta(days=5)).isoformat(), "unpaid"),
        ("Delta Services", "INV-4000", 9200.0, (today + timedelta(days=20)).isoformat(), "unpaid"),
    ]
    for v, num, amt, due, stat in sample_invoices:
        c.execute("INSERT INTO invoices (vendor, number, amount, due_date, status) VALUES (?, ?, ?, ?, ?)",
                  (v, num, amt, due, stat))
    conn.commit()
    conn.close()


def _make_pdf(filename: str, vendor: str, inv_number: str,
              amount: str, due_date: str, currency_symbol: str = "\u20b9") -> None:
    """Generate a fake invoice PDF in inbox/."""
    INBOX.mkdir(exist_ok=True)
    filepath = INBOX / filename
    c = canvas.Canvas(str(filepath), pagesize=A4)
    w, h = A4
    c.setFont("Helvetica-Bold", 20)
    c.drawString(50, h - 60, "INVOICE")
    c.setFont("Helvetica", 12)
    y = h - 100
    lines = [
        f"Vendor: {vendor}",
        f"Invoice Number: {inv_number}",
        f"Amount: {currency_symbol}{amount}",
        f"Due Date: {due_date}",
        "",
        "Payment Terms: Net 30",
        "Please remit payment by the due date.",
    ]
    for line in lines:
        c.drawString(50, y, line)
        y -= 20
    c.save()


def _write_emails(emails: list[dict]) -> None:
    """Write emails.json to inbox/."""
    INBOX.mkdir(exist_ok=True)
    with open(INBOX / "emails.json", "w") as f:
        json.dump(emails, f, indent=2)


def _clear_inbox() -> None:
    """Remove all files from inbox/."""
    if INBOX.exists():
        for p in INBOX.iterdir():
            p.unlink()
    INBOX.mkdir(exist_ok=True)


def seed_clean() -> None:
    """Clean scenario: a single valid invoice from Acme Supplies."""
    _reset_db()
    _clear_inbox()
    due = (date.today() + timedelta(days=5)).isoformat()
    _make_pdf("acme_inv_1001.pdf", "Acme Supplies", "INV-1001", "42,000.00", due)
    _write_emails([
        {
            "id": 1,
            "sender": "billing@acmesupplies.com",
            "subject": "Invoice INV-1001 from Acme Supplies",
            "date": date.today().isoformat(),
            "attachment": "acme_inv_1001.pdf",
            "body": f"Please find attached invoice INV-1001 for \u20b942,000.00 due on {due}."
        }
    ])


def seed_duplicate() -> None:
    """Duplicate scenario: invoice INV-1001 already exists in DB."""
    _reset_db()
    _clear_inbox()
    due = (date.today() + timedelta(days=5)).isoformat()
    # Pre-insert the invoice so it already exists
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute(
        "INSERT INTO invoices (vendor, number, amount, due_date, status) VALUES (?, ?, ?, ?, ?)",
        ("Acme Supplies", "INV-1001", 42000.0, due, "unpaid")
    )
    conn.commit()
    conn.close()
    _make_pdf("acme_inv_1001.pdf", "Acme Supplies", "INV-1001", "42,000.00", due)
    _write_emails([
        {
            "id": 1,
            "sender": "billing@acmesupplies.com",
            "subject": "Invoice INV-1001 from Acme Supplies",
            "date": date.today().isoformat(),
            "attachment": "acme_inv_1001.pdf",
            "body": f"Please find attached invoice INV-1001 for \u20b942,000.00 due on {due}."
        }
    ])


def seed_over_threshold() -> None:
    """Over-threshold scenario: invoice amount exceeds \u20b950,000 policy limit."""
    _reset_db()
    _clear_inbox()
    due = (date.today() + timedelta(days=10)).isoformat()
    _make_pdf("gamma_inv_3001.pdf", "Gamma Industries", "INV-3001", "75,000.00", due)
    _write_emails([
        {
            "id": 1,
            "sender": "accounts@gammaindustries.com",
            "subject": "Invoice INV-3001 from Gamma Industries",
            "date": date.today().isoformat(),
            "attachment": "gamma_inv_3001.pdf",
            "body": f"Please find attached invoice INV-3001 for \u20b975,000.00 due on {due}."
        }
    ])


def seed_form_error() -> None:
    """Form-error scenario: PDF has currency-formatted amount with \u20b9 symbol.
    Agent must strip the symbol/commas before submitting the form."""
    _reset_db()
    _clear_inbox()
    due = (date.today() + timedelta(days=7)).isoformat()
    # The PDF has the amount with the rupee symbol and commas
    _make_pdf("beta_inv_2001.pdf", "Beta Corp", "INV-2001", "18,500.00", due,
              currency_symbol="\u20b9")
    _write_emails([
        {
            "id": 1,
            "sender": "finance@betacorp.com",
            "subject": "Invoice INV-2001 from Beta Corp",
            "date": date.today().isoformat(),
            "attachment": "beta_inv_2001.pdf",
            "body": f"Please find attached invoice INV-2001 for \u20b918,500.00 due on {due}."
        }
    ])


def seed_flaky() -> None:
    """Flaky scenario: same as clean, but FLAKY=1 env triggers a 503 on first POST."""
    seed_clean()  # Same data, flakiness is controlled by env var


SCENARIOS = {
    "clean": seed_clean,
    "duplicate": seed_duplicate,
    "over_threshold": seed_over_threshold,
    "form_error": seed_form_error,
    "flaky": seed_flaky,
}


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed company DB and inbox.")
    parser.add_argument("--scenario", required=True, choices=SCENARIOS.keys())
    args = parser.parse_args()
    SCENARIOS[args.scenario]()
    print(f"[OK] Seeded scenario: {args.scenario}")
    print(f"  DB: {DB_PATH}")
    print(f"  Inbox: {INBOX}")


if __name__ == "__main__":
    main()
