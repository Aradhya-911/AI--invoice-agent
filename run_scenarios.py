#!/usr/bin/env python3
"""Run all 5 scenarios against the real model and print a pass/fail table.

Seeds the DB, starts Flask, runs the agent, and checks results.
ask_user answers are scripted per scenario.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from datetime import date, timedelta
from pathlib import Path

from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

from agent.loop import run_agent
from tools.tool_defs import reset_tools_state

console = Console()

FLASK_PORT = int(os.environ.get("FLASK_PORT", "5001"))
BASE_URL = f"http://127.0.0.1:{FLASK_PORT}"


def _seed(scenario: str, env_overrides: dict | None = None) -> None:
    """Run seed.py for a given scenario."""
    env = os.environ.copy()
    if env_overrides:
        env.update(env_overrides)
    subprocess.run(
        [sys.executable, "seed.py", "--scenario", scenario],
        cwd=str(ROOT),
        env=env,
        check=True,
        capture_output=True,
    )


def _start_flask(env_overrides: dict | None = None) -> subprocess.Popen:
    """Start the Flask mock app as a subprocess."""
    env = os.environ.copy()
    env["FLASK_PORT"] = str(FLASK_PORT)
    if env_overrides:
        env.update(env_overrides)
    proc = subprocess.Popen(
        [sys.executable, "-m", "mock_app.app"],
        cwd=str(ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
    )
    # Wait for Flask to be ready
    time.sleep(3)
    return proc


def _stop_flask(proc: subprocess.Popen) -> None:
    """Stop the Flask subprocess."""
    try:
        if sys.platform == "win32":
            proc.terminate()
        else:
            proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=5)
    except Exception:
        proc.kill()


# Scenario definitions
SCENARIOS = [
    {
        "name": "clean",
        "seed": "clean",
        "task": (
            f"Find the latest invoice from Acme Supplies in the inbox, "
            f"enter its amount and due date into the company system at {BASE_URL}, "
            f"and tell me when done."
        ),
        "env": {},
        "ask_user_answers": {},
        "expect_success": True,
        "description": "Standard invoice entry from PDF",
    },
    {
        "name": "flaky",
        "seed": "flaky",
        "task": (
            f"Find the latest invoice from Acme Supplies in the inbox, "
            f"enter its amount and due date into the company system at {BASE_URL}, "
            f"and tell me when done."
        ),
        "env": {"FLAKY": "1"},
        "ask_user_answers": {},
        "expect_success": True,
        "description": "Recovers from 503 server error",
    },
    {
        "name": "duplicate",
        "seed": "duplicate",
        "task": (
            f"Find the latest invoice from Acme Supplies in the inbox, "
            f"enter its amount and due date into the company system at {BASE_URL}, "
            f"and tell me when done."
        ),
        "env": {},
        "ask_user_answers": {
            "default": "No, do not create a duplicate. The invoice already exists."
        },
        "expect_success": True,
        "description": "Detects duplicate, asks user, does NOT create",
    },
    {
        "name": "over_threshold",
        "seed": "over_threshold",
        "task": (
            f"Find the latest invoice from Gamma Industries in the inbox, "
            f"enter its amount and due date into the company system at {BASE_URL}, "
            f"and tell me when done."
        ),
        "env": {},
        "ask_user_answers": {
            "default": "Yes, approved. Please proceed with the submission."
        },
        "expect_success": True,
        "description": "Amount exceeds threshold, gets approval, completes",
    },
    {
        "name": "read_query",
        "seed": "clean",
        "task": (
            f"Which invoices in the company database are due in the next 7 days "
            f"and what is the total amount? The system is at {BASE_URL}."
        ),
        "env": {},
        "ask_user_answers": {},
        "expect_success": True,
        "description": "Read-only query task (generalization)",
    },
]


def _make_ask_user_fn(scenario: dict):
    """Create a scripted ask_user function for a scenario."""
    answers = scenario.get("ask_user_answers", {})
    default = answers.get("default", "Yes, proceed.")
    call_count = [0]

    def ask_user_fn(question: str) -> str:
        call_count[0] += 1
        # Check for specific question-answer mappings
        for key, answer in answers.items():
            if key != "default" and key.lower() in question.lower():
                return answer
        return default

    return ask_user_fn


def run_scenario(scenario: dict) -> dict:
    """Run a single scenario and return the result."""
    name = scenario["name"]
    console.print(f"\n[bold]{'='*60}[/bold]")
    console.print(f"[bold cyan]Scenario: {name}[/bold cyan] - {scenario['description']}")
    console.print(f"[bold]{'='*60}[/bold]")

    # Set env overrides
    for k, v in scenario.get("env", {}).items():
        os.environ[k] = v

    # Seed the database
    _seed(scenario["seed"], scenario.get("env"))

    # Start Flask
    flask_proc = _start_flask(scenario.get("env"))

    try:
        run_dir = ROOT / "runs" / f"scenario_{name}_{int(time.time())}"
        result = run_agent(
            task=scenario["task"],
            max_steps=25,
            headed=False,
            run_dir=run_dir,
            ask_user_fn=_make_ask_user_fn(scenario),
            flask_port=FLASK_PORT,
        )

        # Determine pass/fail
        passed = result.get("success", False)

        # Special check for duplicate: verify NO new row was created
        if name == "duplicate":
            import sqlite3
            conn = sqlite3.connect(str(ROOT / "company.db"))
            count = conn.execute("SELECT COUNT(*) FROM invoices WHERE number = 'INV-1001'").fetchone()[0]
            conn.close()
            # Should have exactly 1 (the pre-existing one), not 2
            if count > 1:
                passed = False
                result["summary"] += " [FAIL: Duplicate was created!]"
            elif count == 1:
                passed = True  # Agent correctly didn't create a duplicate

        return {
            "name": name,
            "description": scenario["description"],
            "passed": passed,
            "steps": result.get("steps_used", 0),
            "summary": result.get("summary", ""),
        }
    except Exception as e:
        return {
            "name": name,
            "description": scenario["description"],
            "passed": False,
            "steps": 0,
            "summary": f"Error: {e}",
        }
    finally:
        _stop_flask(flask_proc)
        # Clean env overrides
        for k in scenario.get("env", {}).keys():
            os.environ.pop(k, None)


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Run test scenarios.")
    parser.add_argument("--scenario", choices=[s["name"] for s in SCENARIOS], help="Run only a single scenario.")
    args = parser.parse_args()

    console.print("[bold magenta]=== AI Agent Scenario Runner ===[/bold magenta]\n")

    scenarios_to_run = [s for s in SCENARIOS if s["name"] == args.scenario] if args.scenario else SCENARIOS

    results = []
    for i, scenario in enumerate(scenarios_to_run):
        if i > 0:
            console.print("[dim]Pausing 15s to respect API rate limits...[/dim]")
            time.sleep(15)
        result = run_scenario(scenario)
        results.append(result)

    # Print results table
    console.print(f"\n[bold magenta]{'='*60}[/bold magenta]")
    console.print("[bold magenta]           RESULTS SUMMARY[/bold magenta]")
    console.print(f"[bold magenta]{'='*60}[/bold magenta]\n")

    table = Table(title="Scenario Results")
    table.add_column("Scenario", style="cyan")
    table.add_column("Description", style="white")
    table.add_column("Result", justify="center")
    table.add_column("Steps", justify="right")

    passed_count = 0
    for r in results:
        status = "[green]PASS[/green]" if r["passed"] else "[red]FAIL[/red]"
        if r["passed"]:
            passed_count += 1
        table.add_row(r["name"], r["description"], status, str(r["steps"]))

    console.print(table)
    console.print(f"\n[bold]{passed_count}/{len(results)} scenarios passed[/bold]")

    if passed_count < len(results):
        sys.exit(1)


if __name__ == "__main__":
    main()
