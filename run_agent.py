#!/usr/bin/env python3
"""Entry point to run the agent with a natural-language task.

Usage:
    python run_agent.py "Find the latest invoice and enter it" [--max-steps 25] [--headed]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

from agent.loop import run_agent


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the AI task agent.")
    parser.add_argument("task", help="Natural language task description.")
    parser.add_argument("--max-steps", type=int, default=25, help="Max agent steps (default: 25).")
    parser.add_argument("--headed", action="store_true", help="Show the browser window.")
    args = parser.parse_args()

    result = run_agent(
        task=args.task,
        max_steps=args.max_steps,
        headed=args.headed,
    )

    if result["success"]:
        print(f"\n✅ Task completed successfully in {result['steps_used']} steps.")
    else:
        print(f"\n❌ Task did not complete. Steps used: {result['steps_used']}")
        sys.exit(1)


if __name__ == "__main__":
    main()
