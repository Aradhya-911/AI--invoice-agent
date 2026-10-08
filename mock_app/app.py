#!/usr/bin/env python3
"""Mock company web application for the AI agent to interact with.

Serves vendor, PO, and invoice pages. Supports FLAKY=1 env var
to simulate a 503 on the first POST to /invoices/new.
"""
import os
import sqlite3
from pathlib import Path

from flask import Flask, g, render_template, request, redirect, url_for, abort

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "company.db"

app = Flask(__name__)
app.config["DB_PATH"] = str(DB_PATH)

# Track flaky state in-process
_flaky_triggered = False


def get_db() -> sqlite3.Connection:
    """Get a database connection for the current request."""
    if "db" not in g:
        g.db = sqlite3.connect(app.config["DB_PATH"])
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(exception: BaseException | None = None) -> None:
    db = g.pop("db", None)
    if db is not None:
        db.close()


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/vendors")
def vendors():
    db = get_db()
    rows = db.execute("SELECT * FROM vendors ORDER BY name").fetchall()
    return render_template("vendors.html", vendors=rows)


@app.route("/vendors/new", methods=["GET", "POST"])
def vendors_new():
    db = get_db()
    if request.method == "GET":
        return render_template("vendor_form.html", error=None, form={})

    name = request.form.get("name", "").strip()
    if not name:
        return render_template("vendor_form.html", error="Vendor name is required.", form={}), 400

    existing = db.execute("SELECT 1 FROM vendors WHERE name = ?", (name,)).fetchone()
    if existing:
        return render_template("vendor_form.html", error=f"Vendor '{name}' already exists.", form={"name": name}), 400

    db.execute("INSERT INTO vendors (name) VALUES (?)", (name,))
    db.commit()
    return redirect(url_for("vendors"))


@app.route("/pos")
def purchase_orders():
    db = get_db()
    rows = db.execute("SELECT * FROM purchase_orders ORDER BY id").fetchall()
    return render_template("pos.html", pos=rows)


@app.route("/invoices")
def invoices_list():
    db = get_db()
    rows = db.execute("SELECT * FROM invoices ORDER BY id DESC").fetchall()
    return render_template("invoices.html", invoices=rows)


@app.route("/invoices/new", methods=["GET", "POST"])
def invoices_new():
    global _flaky_triggered
    db = get_db()
    vendors = db.execute("SELECT name FROM vendors ORDER BY name").fetchall()
    
    if request.method == "GET":
        return render_template("invoice_form.html", vendors=vendors, error=None, form={})
    
    # POST
    # Check FLAKY env var
    if os.environ.get("FLAKY") == "1" and not _flaky_triggered:
        _flaky_triggered = True
        return render_template("error_503.html"), 503
    
    form = {
        "vendor": request.form.get("vendor", "").strip(),
        "number": request.form.get("number", "").strip(),
        "amount": request.form.get("amount", "").strip(),
        "due_date": request.form.get("due_date", "").strip(),
    }
    
    # Validate amount: must be a plain number (no symbols, no commas)
    try:
        amount_val = float(form["amount"])
    except (ValueError, TypeError):
        error = f"Invalid amount: '{form['amount']}'. Amount must be a plain number (e.g., 42000 or 42000.00). Do not include currency symbols or commas."
        return render_template("invoice_form.html", vendors=vendors, error=error, form=form), 400
    
    # Validate other fields
    if not form["vendor"]:
        return render_template("invoice_form.html", vendors=vendors, error="Vendor is required.", form=form), 400
    if not form["number"]:
        return render_template("invoice_form.html", vendors=vendors, error="Invoice number is required.", form=form), 400
    if not form["due_date"]:
        return render_template("invoice_form.html", vendors=vendors, error="Due date is required.", form=form), 400
    
    # Check for duplicate
    existing = db.execute("SELECT 1 FROM invoices WHERE number = ?", (form["number"],)).fetchone()
    if existing:
        return render_template("invoice_form.html", vendors=vendors,
                             error=f"Invoice {form['number']} already exists.", form=form), 400
    
    db.execute(
        "INSERT INTO invoices (vendor, number, amount, due_date, status) VALUES (?, ?, ?, ?, 'unpaid')",
        (form["vendor"], form["number"], amount_val, form["due_date"])
    )
    db.commit()
    return redirect(url_for("invoices_list"))


@app.route("/invoices/<int:invoice_id>")
def invoice_detail(invoice_id: int):
    db = get_db()
    row = db.execute("SELECT * FROM invoices WHERE id = ?", (invoice_id,)).fetchone()
    if not row:
        abort(404)
    return render_template("invoice_detail.html", invoice=row)


@app.route("/invoices/<int:invoice_id>/mark_paid", methods=["POST"])
def mark_paid(invoice_id: int):
    db = get_db()
    row = db.execute("SELECT * FROM invoices WHERE id = ?", (invoice_id,)).fetchone()
    if not row:
        abort(404)
    db.execute("UPDATE invoices SET status = 'paid' WHERE id = ?", (invoice_id,))
    db.commit()
    return redirect(url_for("invoice_detail", invoice_id=invoice_id))


def reset_flaky_state() -> None:
    """Reset the flaky trigger state (used by test runner between scenarios)."""
    global _flaky_triggered
    _flaky_triggered = False


if __name__ == "__main__":
    port = int(os.environ.get("FLASK_PORT", 5001))
    app.run(host="127.0.0.1", port=port, debug=False)
