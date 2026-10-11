# Autonomous Task Worker (Take-Home Prototype)

A working prototype of a task-agnostic autonomous agent designed to complete back-office workflows in a simulated company environment.

Given a high-level instruction in plain English, the agent parses unstructured documents (PDFs, emails), interacts with a web portal using a headless or headed Chromium browser, executes actions governed by a deterministic policy engine, and independently verifies database mutations before reporting completion.

No agent frameworks (LangChain, LlamaIndex, CrewAI) are used. The control loop is written in pure Python so every state transition, tool call, and failure mode is explicit and explainable.

---

## Quickstart

### 1. Environment Setup
```bash
# Clone the repository
git clone https://github.com/Aradhya-911/AI--invoice-agent.git
cd AI--invoice-agent

# Create and activate virtual environment
python -m venv venv
.\venv\Scripts\activate       # Windows
# source venv/bin/activate    # Linux / macOS

# Install dependencies and Playwright browser binaries
pip install -r requirements.txt
playwright install chromium
```

### 2. Configure Credentials
Copy `.env.example` to `.env` and add your Google Gemini API key:
```bash
cp .env.example .env
```
Ensure your `.env` contains:
```ini
GEMINI_API_KEY=your_gemini_api_key_here
MODEL=gemini-3.5-flash-lite
FLASK_PORT=5001
FLAKY=0
```

### 3. Run Unit Tests (Offline / Zero API Cost)
```bash
pytest tests/ -v
```
All 40 tests run locally using a deterministic `FakeModel` and in-memory SQLite fixtures to validate policy checks, SQL safety guards, claim verification, and loop behavior.

---

## Running the System

### Automated Benchmark Suite (5 Scenarios)
The scenario runner automatically seeds the database, spins up the mock application, executes the agent against 5 distinct operational scenarios, and outputs a structured results table:
```bash
python run_scenarios.py
```

To run a single scenario in isolation:
```bash
python run_scenarios.py --scenario clean
# Options: clean | flaky | duplicate | over_threshold | read_query
```

### Interactive Ad-Hoc Execution (With Live Browser)
To run custom tasks and observe the browser actions on-screen:

1. **Terminal 1:** Start the simulated company portal:
   ```bash
   python seed.py --scenario clean
   python -m mock_app.app
   ```

2. **Terminal 2:** Launch the agent with `--headed`:
   ```bash
   python run_agent.py "Find the latest invoice from Acme Supplies in the inbox, enter its amount and due date into the company system at http://127.0.0.1:5001, and tell me when done." --headed
   ```

---

## Benchmark Scenarios

The test harness evaluates the agent across five scenarios designed to stress different aspects of autonomy and reliability:

| Scenario | Objective | What is Evaluated | Expected Behavior |
| :--- | :--- | :--- | :--- |
| **`clean`** | Invoice entry | End-to-end multimodal execution | Reads PDF, enters data via UI, confirms row in SQLite. |
| **`flaky`** | Fault tolerance | Recovery from 503 HTTP errors | Detects transient failure, re-navigates, and re-submits successfully. |
| **`duplicate`** | Data integrity | Idempotency & duplicate prevention | Policy engine blocks re-entry; agent halts without writing duplicate records. |
| **`over_threshold`**| Policy compliance | Hard safety boundaries | Invoices > ₹50,000 are blocked in code until explicit approval via `ask_user`. |
| **`read_query`** | Generalization | Read-only business intelligence | Queries SQLite for upcoming dues and calculates aggregations without UI interaction. |

---

## Architecture

```
                    ┌────────────────────────────────────────┐
                    │               User Task                │
                    └───────────────────┬────────────────────┘
                                        │
                                        ▼
                       ┌─────────────────────────────────┐
              ┌────────┤   agent/loop.py (while loop)    │◄────────┐
              │        └────────────────┬────────────────┘         │
              │                         │                          │
              │                         ▼                          │
              │        ┌─────────────────────────────────┐         │
              │        │   agent/model.py (call_model)   │         │
              │        │   Gemini API with backoff       │         │
              │        └────────────────┬────────────────┘         │
              │                         │                          │
              │                         ▼                          │
              │        ┌─────────────────────────────────┐         │
              │        │        Tool Dispatcher          │         │
              │        └────────┬───────────────┬────────┘         │
              │                 │               │                  │
         finish()      browser_click()     other tools             │
              │                 │               │                  │
              ▼                 ▼               ▼                  │
      ┌───────────────┐ ┌───────────────┐ ┌───────────┐            │
      │agent/verifier │ │ agent/policy  │ │   tools/  │            │
      │Direct DB check│ │ (policy.yaml) │ │tool_defs  │            │
      └───────┬───────┘ └───────┬───────┘ └─────┬─────┘            │
              │                 │               │                  │
              │ Pass/Fail       │ Block/Allow   │ Observation      │
              └────────┬────────┴───────────────┴──────────────────┘
                       │
                       ▼
              Appended to context (log.jsonl)
```

---

## Engineering Design Decisions

### 1. Plain While-Loop over Framework Abstractions
Frameworks like LangChain and CrewAI introduce nested abstractions that obscure prompt construction, error recovery, and context management. Here, `agent/loop.py` is a single `while step < MAX_STEPS` loop. Every tool dispatch, state update, retry attempt, and observation is plain Python code that can be stepped through with a debugger.

### 2. Deterministic Policy Enforcement in Code (Not Just Prompts)
Prompt engineering alone cannot guarantee compliance; LLMs routinely ignore soft constraints under context pressure. Our policy engine (`agent/policy.py`) evaluates rules from `policy.yaml` **at click-time in the Python dispatcher**. If an action violates a rule (such as submitting an invoice amount exceeding ₹50,000 without prior approval), the physical browser click is blocked by Python code before it ever reaches Chromium.

### 3. Independent Database Verification
The agent's `finish(summary, claims)` tool requires structured assertion claims (e.g., `{"table": "invoices", "where": {"number": "INV-1001"}, "expect": {"amount": 42000.0}}`). The test harness uses `agent/verifier.py` to check SQLite directly via disk queries, bypassing the browser and DOM entirely. If the database state does not match the claims, the completion is rejected and the discrepancy is returned to the agent to diagnose and correct.

### 4. Task-Agnostic Core
Nothing in the `agent/` directory references invoices, purchase orders, or vendors. The core loop, system prompt, policy engine, and verifier are domain-agnostic primitives designed to handle any document parsing, web navigation, or SQL verification task.

### 5. Resilient Model Adapter with Thought Signature Preservation
`agent/model.py` isolates all LLM provider communication behind a single `call_model()` signature. It converts messages to the native Google GenAI SDK format, preserves model thought signatures across multi-turn function calls, and implements exponential backoff to handle transient 429 rate limits and 503 service spikes.

---

## Directory Structure

```text
├── agent/
│   ├── loop.py            # Main control loop, tool dispatcher, loop detection
│   ├── model.py           # Gemini SDK interface, thought signature handling
│   ├── policy.py          # Click-time policy enforcement engine
│   ├── prompts.py         # Task-agnostic system prompt & tool schemas
│   └── verifier.py        # Independent SQLite claim verifier
├── tools/
│   ├── __init__.py
│   └── tool_defs.py       # Tool implementations (Playwright, SQLite, pypdf)
├── mock_app/
│   ├── app.py             # Flask application simulating company portal
│   └── templates/         # Jinja2 templates (vendors, pos, invoices)
├── tests/
│   ├── test_loop.py       # Control loop tests using FakeModel
│   ├── test_policy.py     # Policy engine unit tests
│   ├── test_sql_guard.py  # Read-only SQL guard security tests
│   └── test_verifier.py   # Ground-truth claim verification tests
├── inbox/                 # Simulated email inbox with generated invoice PDFs
├── runs/                  # Execution audit trails (logs, screenshots, claims)
├── policy.yaml            # Declarative company business rules
├── seed.py                # Database and inbox test scenario generator
├── run_agent.py           # CLI entry point for ad-hoc prompts
├── run_scenarios.py       # Automated 5-scenario evaluation runner
├── requirements.txt       # Minimal dependency manifest
└── README.md
```

---

## Run Artifacts & Observability

Every execution generates an isolated run folder inside `runs/<timestamp>/` containing:
* **`log.jsonl`**: Complete chronological trace of thoughts, tool invocations, inputs, and observations.
* **`screenshots/`**: Page state snapshots taken during critical navigation and completion phases.
* **`verification.json`**: Output of direct SQLite assertions evaluated at task completion.
* **`summary.md`**: Human-readable outcome summary and operational log.

---

## System Boundaries & Future Improvements

### Current Limitations
1. **Single Tab Context**: The browser driver operates on a single page context. Multi-tab authentication flows or popup windows are not currently managed.
2. **Text-Centric DOM Inspection**: Page elements are parsed into structured text selectors. Highly dynamic canvas elements or non-standard shadow DOM components require additional accessibility tree extraction.
3. **Local Filesystem Mailbox**: Incoming messages are simulated via `inbox/emails.json` rather than live IMAP or Microsoft Graph webhooks.

### Next Steps for Production
1. **Two-Way Webhook Integration**: Connect the inbox tool directly to Google Workspace or Microsoft 365 APIs with OAuth 2.0 token refresh.
2. **Vision-Augmented Fallbacks**: When DOM element selectors cannot be resolved cleanly, invoke visual coordinate clicks using screenshot annotations.
3. **Human-in-the-Loop Slack / Teams Integration**: Route `ask_user` requests to interactive webhook cards for asynchronous manager approval.
