"""Tests for the agent loop using a FakeModel (no API key needed).

Tests the loop machinery: tool dispatch, policy enforcement, loop
detection, and verification—all without calling any real LLM.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import patch, MagicMock

import pytest

from agent.model import ModelResponse, ToolCall
from agent.loop import run_agent, _detect_loop, _is_submit_click


class FakeModel:
    """A scripted fake model that returns pre-programmed responses in sequence."""

    def __init__(self, responses: list[ModelResponse]):
        self._responses = list(responses)
        self._call_count = 0

    def __call__(self, messages, tools, system, model=None) -> ModelResponse:
        if self._call_count < len(self._responses):
            resp = self._responses[self._call_count]
            self._call_count += 1
            return resp
        # Default: finish
        return ModelResponse(
            text="Done.",
            tool_calls=[ToolCall(id="finish", name="finish", arguments={
                "summary": "Completed (fallback).", "claims": []
            })],
        )


class TestDetectLoop:
    def test_no_loop(self):
        history = [
            {"tool": "a", "args": {"x": 1}},
            {"tool": "b", "args": {"x": 2}},
            {"tool": "c", "args": {"x": 3}},
            {"tool": "d", "args": {"x": 4}},
        ]
        assert _detect_loop(history) is False

    def test_loop_detected(self):
        action = {"tool": "browser_click", "args": {"selector": "#btn"}}
        history = [action, action, action, action, action, action, action, action]
        assert _detect_loop(history) is True

    def test_short_history(self):
        history = [{"tool": "a", "args": {}}]
        assert _detect_loop(history) is False


class TestIsSubmitClick:
    def test_save_button(self):
        assert _is_submit_click("#save-btn") is True

    def test_mark_paid(self):
        assert _is_submit_click("#mark-paid-btn") is True

    def test_regular_link(self):
        assert _is_submit_click("a.nav-link") is False

    def test_submit_type(self):
        assert _is_submit_click("button[type='submit']") is True


class TestLoopWithFakeModel:
    """Test the full loop with a scripted FakeModel."""

    def _make_test_db(self, tmp_path: Path) -> Path:
        db_path = tmp_path / "company.db"
        conn = sqlite3.connect(str(db_path))
        conn.executescript("""
            CREATE TABLE vendors (id INTEGER PRIMARY KEY, name TEXT UNIQUE);
            CREATE TABLE invoices (id INTEGER PRIMARY KEY, vendor TEXT, number TEXT UNIQUE,
                                   amount REAL, due_date TEXT, status TEXT DEFAULT 'unpaid');
            INSERT INTO vendors (name) VALUES ('Acme Supplies');
        """)
        conn.commit()
        conn.close()
        return db_path

    def test_simple_finish(self, tmp_path):
        """Test that the loop handles a simple finish response."""
        responses = [
            ModelResponse(
                text="I'll complete the task now.",
                tool_calls=[ToolCall(id="finish", name="finish", arguments={
                    "summary": "Task completed successfully.",
                    "claims": [],
                })],
            ),
        ]
        fake = FakeModel(responses)

        run_dir = tmp_path / "run"

        with patch("agent.loop.call_model", side_effect=fake), \
             patch("playwright.sync_api.sync_playwright") as mock_pw:
            # Mock playwright
            mock_browser = MagicMock()
            mock_page = MagicMock()
            mock_browser.new_page.return_value = mock_page
            mock_pw_instance = MagicMock()
            mock_pw_instance.chromium.launch.return_value = mock_browser
            mock_pw.return_value.start.return_value = mock_pw_instance

            result = run_agent(
                task="Do a simple task",
                max_steps=5,
                run_dir=run_dir,
            )

        assert result["success"] is True
        assert "completed" in result["summary"].lower()

    def test_max_steps(self, tmp_path):
        """Test that the loop stops at max_steps."""
        # Model that never finishes
        responses = [
            ModelResponse(text="Thinking...", tool_calls=[])
            for _ in range(10)
        ]
        fake = FakeModel(responses)

        run_dir = tmp_path / "run"

        with patch("agent.loop.call_model", side_effect=fake), \
             patch("playwright.sync_api.sync_playwright") as mock_pw:
            mock_browser = MagicMock()
            mock_page = MagicMock()
            mock_browser.new_page.return_value = mock_page
            mock_pw_instance = MagicMock()
            mock_pw_instance.chromium.launch.return_value = mock_browser
            mock_pw.return_value.start.return_value = mock_pw_instance

            result = run_agent(
                task="Never-ending task",
                max_steps=3,
                run_dir=run_dir,
            )

        assert result["success"] is False
        assert result["steps_used"] == 3
