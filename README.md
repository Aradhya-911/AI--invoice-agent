# Autonomous AI Task Worker

A genuinely working prototype of an autonomous AI agent that accepts natural-language tasks
and completes them using a real browser, files, and a database inside a simulated company
environment.

## Quick Start

```bash
# 1. Clone and enter the project
git clone https://github.com/Aradhya-911/AI--invoice-agent.git
cd "ai agent"

# 2. Create virtual environment
python -m venv venv
venv\Scripts\activate        # Windows
# source venv/bin/activate   # Linux/Mac

# 3. Install dependencies
pip install -r requirements.txt
playwright install chromium

# 4. Configure environment
cp .env.example .env
# Edit .env with your GEMINI_API_KEY

# 5. Seed the database
python seed.py --scenario clean

# 6. Start the Flask mock app (in a separate terminal)
python -m mock_app.app

# 7. Run the agent
python run_agent.py "Find the latest invoice from Acme Supplies, enter amount and due date into the system, tell me when done."

# 8. Run all scenarios (automated)
python run_scenarios.py

# 9. Run tests (no API key needed)
pytest tests/ -v
```

## Architecture

```
┌─────────────┐     ┌──────────────┐     ┌──────────────────┐
│  run_agent   │────▶│  agent/loop  │────▶│  agent/model     │
│  (CLI)       │     │  (while loop)│     │  (Gemini API)    │
└─────────────┘     └──────┬───────┘     └──────────────────┘
                           │
                    ┌──────▼───────┐
                    │   Dispatcher  │
                    │  ┌──────────┐ │
                    │  │ Policy   │ │──── policy.yaml
                    │  │ Engine   │ │
                    │  └──────────┘ │
                    └──────┬───────┘
                           │
        ┌──────────────────┼──────────────────┐
        │                  │                  │
   ┌────▼────┐      ┌─────▼─────┐     ┌─────▼─────┐
   │ Browser │      │  Files    │     │ Database  │
   │ Tools   │      │  (inbox)  │     │ (SQLite)  │
   └────┬────┘      └───────────┘     └───────────┘
        │
   ┌────▼────────────┐
   │ Flask Mock App  │
   │ (company portal)│
   └─────────────────┘
```

## Key Design Decisions

### Plain Python Loop (No Frameworks)
The agent loop is a simple `while step < MAX_STEPS` loop in plain Python. Every line is
readable, debuggable, and explainable. No LangChain, no abstractions—just a loop that
calls the model, executes tools, and collects observations.

### Tool Results as Observations
Every tool result—including errors, exceptions, and policy blocks—is returned to the model
as a text observation. The model sees what happened and decides what to do next. This makes
the agent self-correcting: it reads error messages and adjusts.

### Enforced Policy Engine
Policy rules (amount thresholds, vendor existence, duplicate detection) are enforced in
the tool dispatcher at the moment of form submission—not in the prompt. The model cannot
bypass this because the check happens before the click is executed. If blocked, the model
must use `ask_user` to get approval.

### Independent Database Verification
When the agent calls `finish(summary, claims)`, the verifier reads SQLite directly
(NOT through the browser) to check that claimed outcomes match reality. If verification
fails, the agent is told about the discrepancy and must fix it. This is a hard guarantee,
not a prompt-based hope.

### Claims-Based Generic Verifier
Claims are structured: `{table, where, expect}`. This makes verification domain-agnostic—
any table, any conditions, any expected values. The agent decides what to claim; the verifier
checks it independently.

### Swappable Model Function
`agent/model.py` contains a single `call_model()` function. To switch from Gemini to
OpenAI, Anthropic, or a local model, you only change this one file. The rest of the
codebase is model-agnostic.

### Scratchpad Memory
The agent can `remember(key, value)` to persist facts across steps. The scratchpad is
re-injected into the system prompt every step, giving the model working memory without
relying on the full conversation history.

## Scenarios

| Scenario | Description | What's Tested |
|----------|-------------|---------------|
| `clean` | Valid invoice from Acme Supplies | End-to-end: inbox → PDF → form → verify |
| `flaky` | Same data, but server returns 503 once | Retry/recovery from server errors |
| `duplicate` | Invoice already exists in DB | Agent detects duplicate, asks user, doesn't create |
| `over_threshold` | Invoice amount > ₹50,000 | Policy blocks submission, agent asks for approval |
| `read_query` | "Which invoices are due in 7 days?" | Read-only task using query_db (generalization) |

## Project Structure

```
agent/
  loop.py       — Core agent loop (dispatch, policy, loop detection)
  model.py      — LLM interface (Gemini, swappable)
  policy.py     — Policy engine (rules from YAML, enforced in dispatcher)
  verifier.py   — Claims-based DB verification
  prompts.py    — System prompt and tool schemas (task-agnostic)
tools/
  tool_defs.py  — All tool implementations
mock_app/
  app.py        — Flask company web app
  templates/    — Jinja2 HTML templates
tests/
  test_policy.py    — Policy engine tests
  test_verifier.py  — Verifier tests
  test_sql_guard.py — Read-only SQL guard tests
  test_loop.py      — Loop tests with FakeModel
seed.py         — Database and inbox seeder
policy.yaml     — Policy rules configuration
run_agent.py    — Single-task CLI entry point
run_scenarios.py — Automated 5-scenario test runner
```

## What's Real vs. Mock

| Component | Real or Mock? |
|-----------|---------------|
| Agent loop | **Real** — plain Python, every step logged |
| Browser actions | **Real** — Playwright drives actual Chromium |
| Policy enforcement | **Real** — checked in dispatcher, not prompt |
| DB verification | **Real** — reads SQLite directly |
| Company web app | **Mock** — Flask app simulating a company portal |
| Inbox/PDFs | **Mock** — Generated by reportlab |
| Email data | **Mock** — JSON file in inbox/ |

## Known Limitations

1. **Single-page interactions**: The agent navigates one page at a time. Complex multi-tab
   workflows aren't supported.
2. **No authentication**: The mock app has no login. A real app would need credential management.
3. **Browser element detection**: CSS selector-based; fragile if the app changes significantly.
4. **Context window**: Very long conversations may exceed the model's context window.
5. **Gemini function calling**: Gemini's function calling format differs from OpenAI's;
   the model.py adapter handles this but edge cases may exist.

## What I Would Build Next

1. **Multi-step planning**: Explicit plan → execute → verify cycles with rollback capability.
2. **Persistent memory**: Store completed task history for learning across sessions.
3. **Parallel tool execution**: Run independent tools concurrently.
4. **Visual understanding**: Use screenshot analysis for pages where text extraction fails.
5. **Approval workflows**: Integrate with real approval systems (email, Slack).
6. **Audit trail**: Structured audit log with tamper detection.
7. **Rate limiting**: Token and cost tracking per run.

## Assumptions

- Python 3.11+ is available.
- Chromium can be installed via Playwright.
- The Flask app runs on localhost (configurable port).
- All monetary values are in INR (₹).
- The inbox is a local folder; no real email integration.

## Models/APIs/Frameworks Used

- **LLM**: Google Gemini (via `google-genai` SDK)
- **Browser**: Playwright (Chromium)
- **Web framework**: Flask + Jinja2 (mock app only)
- **Database**: SQLite (via stdlib `sqlite3`)
- **PDF**: pypdf (reading), reportlab (generating test data)
- **Config**: PyYAML, python-dotenv
- **Testing**: pytest
- **CLI output**: rich
