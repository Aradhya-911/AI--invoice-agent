"""Tool implementations for the agent.

Each tool function takes keyword arguments matching its schema and returns
a string observation. All exceptions are caught and returned as error
observations—they never crash the loop.
"""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any, Callable

from pypdf import PdfReader

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "company.db"
INBOX = ROOT / "inbox"

# --- Browser state (managed by the loop, injected via set_browser) ---
_browser_page = None
_run_dir: Path | None = None
_screenshot_counter = 0
_form_values: dict[str, str] = {}  # Tracks filled form values for policy checks


def set_browser(page: Any) -> None:
    """Inject the Playwright page object."""
    global _browser_page
    _browser_page = page


def set_run_dir(path: Path) -> None:
    """Set the run directory for saving screenshots."""
    global _run_dir
    _run_dir = path


def get_form_values() -> dict[str, str]:
    """Return the currently tracked form field values."""
    return dict(_form_values)


def clear_form_values() -> None:
    """Clear tracked form values (after navigation or submission)."""
    global _form_values
    _form_values = {}


# --- Scratchpad ---
_scratchpad: dict[str, str] = {}


def get_scratchpad() -> dict[str, str]:
    """Return current scratchpad contents."""
    return dict(_scratchpad)


def reset_scratchpad() -> None:
    """Clear the scratchpad (between runs)."""
    global _scratchpad
    _scratchpad = {}


# ===== Tool implementations =====


def list_inbox() -> str:
    """List all emails in the inbox."""
    emails_path = INBOX / "emails.json"
    if not emails_path.exists():
        return "No emails found in inbox."
    with open(emails_path) as f:
        emails = json.load(f)
    if not emails:
        return "Inbox is empty."
    lines = []
    for e in emails:
        lines.append(
            f"ID: {e['id']} | From: {e['sender']} | Subject: {e['subject']} "
            f"| Date: {e['date']} | Attachment: {e.get('attachment', 'none')}"
        )
    return "\n".join(lines)


def read_email(id: int) -> str:
    """Read the full content of an email by ID."""
    emails_path = INBOX / "emails.json"
    if not emails_path.exists():
        return "No emails found in inbox."
    with open(emails_path) as f:
        emails = json.load(f)
    for e in emails:
        if e["id"] == id:
            parts = [
                f"From: {e['sender']}",
                f"Subject: {e['subject']}",
                f"Date: {e['date']}",
                f"Attachment: {e.get('attachment', 'none')}",
                f"\n{e.get('body', '(no body)')}",
            ]
            return "\n".join(parts)
    return f"Email with ID {id} not found."


def read_pdf(filename: str) -> str:
    """Extract text from a PDF file in the inbox."""
    filepath = INBOX / filename
    if not filepath.exists():
        return f"File '{filename}' not found in inbox."
    try:
        reader = PdfReader(str(filepath))
        text_parts = []
        for page in reader.pages:
            text = page.extract_text()
            if text:
                text_parts.append(text)
        return "\n".join(text_parts) if text_parts else "(PDF contained no extractable text)"
    except Exception as e:
        return f"Error reading PDF '{filename}': {e}"


def query_db(sql: str) -> str:
    """Execute a read-only SQL query against the company database."""
    # Security: only allow SELECT statements
    cleaned = sql.strip().upper()
    if not cleaned.startswith("SELECT"):
        return "ERROR: Only SELECT queries are allowed. Query rejected."
    # Block dangerous patterns
    dangerous = ["INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE", "REPLACE", "ATTACH", "DETACH"]
    # Check for dangerous keywords outside of string literals (simple check)
    sql_no_strings = re.sub(r"'[^']*'", "", cleaned)
    sql_no_strings = re.sub(r'"[^"]*"', "", sql_no_strings)
    for keyword in dangerous:
        if re.search(rf'\b{keyword}\b', sql_no_strings):
            return f"ERROR: Query contains forbidden keyword '{keyword}'. Only SELECT queries are allowed."

    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(sql).fetchall()
        conn.close()
        if not rows:
            return "Query returned 0 rows."
        result = [dict(r) for r in rows]
        return json.dumps(result, indent=2, default=str)
    except sqlite3.Error as e:
        return f"SQL error: {e}"


def browser_open(url: str) -> str:
    """Open a URL in the browser."""
    if _browser_page is None:
        return "ERROR: Browser not initialized."
    try:
        clear_form_values()
        _browser_page.goto(url, wait_until="domcontentloaded", timeout=10000)
        return f"Navigated to {url}"
    except Exception as e:
        return f"Error navigating to {url}: {e}"


def browser_read_page() -> str:
    """Read the current browser page content and interactive elements."""
    if _browser_page is None:
        return "ERROR: Browser not initialized."
    try:
        # Get page text
        text = _browser_page.inner_text("body")
        # Truncate if too long
        if len(text) > 4000:
            text = text[:4000] + "\n... (truncated)"

        # Get interactive elements
        elements = []

        # Form inputs
        inputs = _browser_page.query_selector_all("input, textarea")
        for inp in inputs:
            inp_type = inp.get_attribute("type") or "text"
            name = inp.get_attribute("name") or ""
            id_attr = inp.get_attribute("id") or ""
            value = inp.get_attribute("value") or ""
            selector = f"#{id_attr}" if id_attr else f"input[name='{name}']" if name else None
            if selector:
                elements.append(f"INPUT [{inp_type}] name='{name}' id='{id_attr}' value='{value}' → selector: {selector}")

        # Selects
        selects = _browser_page.query_selector_all("select")
        for sel in selects:
            name = sel.get_attribute("name") or ""
            id_attr = sel.get_attribute("id") or ""
            selector = f"#{id_attr}" if id_attr else f"select[name='{name}']" if name else None
            # Get options
            options = sel.query_selector_all("option")
            option_texts = [o.inner_text() for o in options[:10]]
            if selector:
                elements.append(f"SELECT name='{name}' id='{id_attr}' options={option_texts} → selector: {selector}")

        # Buttons
        buttons = _browser_page.query_selector_all("button, input[type='submit']")
        for btn in buttons:
            text_btn = btn.inner_text() if btn.evaluate("el => el.tagName") == "BUTTON" else btn.get_attribute("value") or ""
            id_attr = btn.get_attribute("id") or ""
            btn_type = btn.get_attribute("type") or ""
            selector = f"#{id_attr}" if id_attr else f"button:has-text('{text_btn.strip()}')" if text_btn.strip() else None
            if selector:
                elements.append(f"BUTTON text='{text_btn.strip()}' id='{id_attr}' type='{btn_type}' → selector: {selector}")

        # Links
        links = _browser_page.query_selector_all("a[href]")
        for link in links[:20]:
            href = link.get_attribute("href") or ""
            link_text = link.inner_text().strip()
            if link_text and href:
                elements.append(f"LINK text='{link_text}' href='{href}'")

        result = f"=== Page Content ===\n{text}\n\n=== Interactive Elements ===\n"
        result += "\n".join(elements) if elements else "(no interactive elements found)"
        return result
    except Exception as e:
        return f"Error reading page: {e}"


def browser_fill(selector: str, value: str) -> str:
    """Fill a form field in the browser."""
    if _browser_page is None:
        return "ERROR: Browser not initialized."
    try:
        element = _browser_page.query_selector(selector)
        if not element:
            return f"ERROR: Element not found with selector '{selector}'."

        tag = element.evaluate("el => el.tagName").upper()
        if tag == "SELECT":
            _browser_page.select_option(selector, label=value)
        else:
            _browser_page.fill(selector, value)

        # Track the form value (use the name attribute or extract from selector)
        name = element.get_attribute("name") or selector
        _form_values[name] = value
        return f"Filled '{selector}' with '{value}'."
    except Exception as e:
        return f"Error filling '{selector}': {e}"


def browser_click(selector: str) -> str:
    """Click an element in the browser. Policy check happens in the dispatcher."""
    if _browser_page is None:
        return "ERROR: Browser not initialized."
    try:
        _browser_page.click(selector, timeout=5000)
        _browser_page.wait_for_load_state("domcontentloaded", timeout=5000)
        # After navigation, clear form values
        clear_form_values()
        return f"Clicked '{selector}'. Page loaded."
    except Exception as e:
        return f"Error clicking '{selector}': {e}"


def browser_screenshot(label: str) -> str:
    """Take a screenshot of the current page."""
    global _screenshot_counter
    if _browser_page is None:
        return "ERROR: Browser not initialized."
    if _run_dir is None:
        return "ERROR: Run directory not set."
    try:
        _screenshot_counter += 1
        safe_label = re.sub(r'[^a-zA-Z0-9_-]', '_', label)[:50]
        filename = f"{_screenshot_counter:03d}_{safe_label}.png"
        ss_dir = _run_dir / "screenshots"
        ss_dir.mkdir(exist_ok=True)
        path = ss_dir / filename
        _browser_page.screenshot(path=str(path))
        return f"Screenshot saved as '{filename}'."
    except Exception as e:
        return f"Error taking screenshot: {e}"


def remember(key: str, value: str) -> str:
    """Store a key-value pair in the scratchpad."""
    _scratchpad[key] = value
    return f"Remembered: {key} = {value}"


def ask_user(question: str, scripted_answer: str | None = None) -> str:
    """Ask the user a question. In automated mode, returns the scripted answer."""
    if scripted_answer is not None:
        return scripted_answer
    # Interactive mode: prompt user via terminal
    from rich.console import Console
    console = Console()
    console.print(f"\n[bold yellow]🤖 Agent asks:[/bold yellow] {question}")
    answer = input("Your answer: ").strip()
    return answer if answer else "(no answer provided)"


def finish(summary: str, claims: list | None = None) -> str:
    """Signal task completion. The loop handles verification externally."""
    # This is a sentinel - the loop intercepts this and handles verification
    return json.dumps({"summary": summary, "claims": claims or []})


# Tool registry
TOOLS: dict[str, Callable] = {
    "list_inbox": list_inbox,
    "read_email": read_email,
    "read_pdf": read_pdf,
    "query_db": query_db,
    "browser_open": browser_open,
    "browser_read_page": browser_read_page,
    "browser_fill": browser_fill,
    "browser_click": browser_click,
    "browser_screenshot": browser_screenshot,
    "remember": remember,
    "ask_user": ask_user,
    "finish": finish,
}


def reset_tools_state() -> None:
    """Reset all tool state between runs."""
    global _browser_page, _run_dir, _screenshot_counter, _form_values, _scratchpad
    _browser_page = None
    _run_dir = None
    _screenshot_counter = 0
    _form_values = {}
    _scratchpad = {}
