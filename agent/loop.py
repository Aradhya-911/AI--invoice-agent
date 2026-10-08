"""Agent loop – the core execution engine.

A plain Python while-loop that:
1. Sends messages + tool schemas to the model
2. Executes each tool call
3. Appends observations
4. Repeats until finish() or ask_user() or MAX_STEPS

Every tool result (including exceptions) is returned as an observation.
Nothing crashes the loop. Loop detection is built in.
"""
from __future__ import annotations

import json
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from agent.model import call_model, ModelResponse
from agent.policy import check_policy, load_rules, PolicyResult
from agent.prompts import SYSTEM_PROMPT, TOOL_SCHEMAS
from agent.verifier import verify_claims, save_verification, VerificationResult
from tools.tool_defs import (
    TOOLS, get_form_values, get_scratchpad, clear_form_values,
    set_browser, set_run_dir, reset_tools_state,
)

console = Console()

MAX_STEPS_DEFAULT = 25


def _build_system_prompt() -> str:
    """Build the system prompt with current scratchpad contents."""
    scratchpad = get_scratchpad()
    if scratchpad:
        pad_text = "\n".join(f"  {k}: {v}" for k, v in scratchpad.items())
    else:
        pad_text = "  (empty)"
    return SYSTEM_PROMPT.replace("{scratchpad}", pad_text)


def _is_submit_click(selector: str) -> bool:
    """Heuristic: is this click likely a form submission?"""
    submit_patterns = [
        "submit", "save", "create", "mark-paid", "mark_paid",
        "btn-success", "type='submit'", 'type="submit"',
    ]
    sel_lower = selector.lower()
    return any(p in sel_lower for p in submit_patterns)


def _detect_loop(action_history: list[dict], window: int = 4) -> bool:
    """Detect if the agent is repeating the same actions."""
    if len(action_history) < window * 2:
        return False
    recent = action_history[-window:]
    previous = action_history[-window * 2:-window]
    # Compare action signatures (tool name + key args)
    def sig(a: dict) -> str:
        return f"{a.get('tool')}:{json.dumps(a.get('args', {}), sort_keys=True)}"
    return [sig(a) for a in recent] == [sig(a) for a in previous]


def _log_action(log_file: Path, step: int, entry: dict) -> None:
    """Append a single entry to the JSONL log."""
    entry["step"] = step
    entry["timestamp"] = datetime.now().isoformat()
    with open(log_file, "a") as f:
        f.write(json.dumps(entry, default=str) + "\n")


def run_agent(
    task: str,
    max_steps: int = MAX_STEPS_DEFAULT,
    headed: bool = False,
    run_dir: Path | None = None,
    ask_user_fn: Callable[[str], str] | None = None,
    flask_port: int = 5001,
) -> dict:
    """Run the agent loop for a given task.

    Args:
        task: Natural language task description.
        max_steps: Maximum number of loop iterations.
        headed: If True, show the browser window.
        run_dir: Directory to save logs, screenshots, etc.
        ask_user_fn: Optional function to handle ask_user calls (for scripted scenarios).
        flask_port: Port the Flask mock app is running on.

    Returns:
        Dict with keys: success, summary, verification, steps_used.
    """
    # Setup run directory
    if run_dir is None:
        root = Path(__file__).resolve().parent.parent
        run_dir = root / "runs" / datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "screenshots").mkdir(exist_ok=True)

    log_file = run_dir / "log.jsonl"
    reset_tools_state()
    set_run_dir(run_dir)

    # Setup browser
    from playwright.sync_api import sync_playwright
    pw = sync_playwright().start()
    browser = pw.chromium.launch(headless=not headed)
    page = browser.new_page()
    set_browser(page)

    # Initialize conversation
    messages: list[dict] = [
        {"role": "user", "content": task},
    ]

    action_history: list[dict] = []
    policy_rules = load_rules()
    policy_overrides: set[str] = set()  # Field values approved by user

    result = {
        "success": False,
        "summary": "",
        "verification": None,
        "steps_used": 0,
    }

    console.print(Panel(f"[bold cyan]Task:[/bold cyan] {task}", title="Agent Started"))

    try:
        for step in range(1, max_steps + 1):
            console.print(f"\n[dim]--- Step {step}/{max_steps} ---[/dim]")
            _log_action(log_file, step, {"type": "step_start"})

            # Build system prompt with scratchpad
            system = _build_system_prompt()

            # Call the model with retries for transient 503/429 errors
            response = None
            for attempt in range(6):
                try:
                    response = call_model(messages, TOOL_SCHEMAS, system)
                    break
                except Exception as e:
                    err_str = str(e)
                    if attempt < 5:
                        # Extract suggested retry delay if present in error message
                        wait_time = 15
                        import re
                        m = re.search(r"retry in (\d+(?:\.\d+)?)s", err_str, re.IGNORECASE)
                        if m:
                            wait_time = int(float(m.group(1))) + 2
                        elif "429" in err_str:
                            wait_time = 20 * (attempt + 1)
                        else:
                            wait_time = (attempt + 1) * 3
                        
                        console.print(f"[yellow][Rate limit/error: retry {attempt + 1}/5 in {wait_time}s...][/yellow]")
                        time.sleep(wait_time)
                    else:
                        console.print(f"[red]Model call failed after retries: {e}[/red]")
                        _log_action(log_file, step, {"type": "model_error", "error": str(e)})
                        result["summary"] = f"Model error: {e}"
                        break
            if response is None:
                break

            # Display model's text
            if response.text:
                console.print(Panel(response.text, title="Thought", border_style="blue"))
                _log_action(log_file, step, {"type": "thought", "content": response.text})

            if not response.tool_calls:
                # Model responded with only text, add it and continue
                if response.text:
                    messages.append({"role": "assistant", "content": response.text})
                continue

            # Process tool calls
            assistant_msg: dict = {"role": "assistant", "content": response.text or ""}
            if response.raw_content is not None:
                assistant_msg["raw_content"] = response.raw_content
            assistant_msg["tool_calls"] = [
                {"name": tc.name, "arguments": tc.arguments, "raw_part": tc.raw_part} for tc in response.tool_calls
            ]
            messages.append(assistant_msg)

            for tc in response.tool_calls:
                tool_name = tc.name
                tool_args = tc.arguments
                console.print(f"  [yellow][Tool] {tool_name}[/yellow]({json.dumps(tool_args, default=str)[:200]})")

                action_entry = {"tool": tool_name, "args": tool_args}
                action_history.append(action_entry)
                _log_action(log_file, step, {"type": "tool_call", **action_entry})

                # Check for loop detection
                if _detect_loop(action_history):
                    loop_msg = ("LOOP DETECTED: You are repeating the same actions without progress. "
                                "Try a different approach or use ask_user for help.")
                    console.print(f"  [red][Warning] {loop_msg}[/red]")
                    messages.append({"role": "tool", "tool_call_id": tool_name, "content": loop_msg})
                    _log_action(log_file, step, {"type": "loop_detected"})
                    continue

                # Handle finish tool
                if tool_name == "finish":
                    summary = tool_args.get("summary", "")
                    claims = tool_args.get("claims", [])

                    # Parse claims if they're a string
                    if isinstance(claims, str):
                        try:
                            claims = json.loads(claims)
                        except json.JSONDecodeError:
                            claims = []

                    console.print(Panel(summary, title="Finish", border_style="green"))
                    _log_action(log_file, step, {"type": "finish", "summary": summary, "claims": claims})

                    # Verify claims
                    vresult = verify_claims(claims)
                    save_verification(vresult, run_dir)

                    if vresult.passed:
                        console.print("[green][OK] All verification claims passed![/green]")
                        result["success"] = True
                        result["summary"] = summary
                        result["verification"] = vresult.to_dict()
                        result["steps_used"] = step

                        # Save summary.md
                        with open(run_dir / "summary.md", "w") as f:
                            f.write(f"# Task Result\n\n{summary}\n\n")
                            f.write(f"## Verification\n\nAll {len(claims)} claims passed.\n")
                        return result
                    else:
                        # Verification failed - tell the agent
                        fail_details = []
                        for cr in vresult.results:
                            if not cr.passed:
                                fail_details.append(cr.detail)
                        fail_msg = ("VERIFICATION FAILED. The following claims did not match the database:\n"
                                    + "\n".join(fail_details)
                                    + "\nPlease fix the issues and try finish() again.")
                        console.print(f"[red][Fail] Verification failed: {fail_msg}[/red]")
                        messages.append({"role": "tool", "tool_call_id": tool_name, "content": fail_msg})
                        _log_action(log_file, step, {"type": "verification_failed", "details": fail_details})
                        continue

                # Handle ask_user tool
                if tool_name == "ask_user":
                    question = tool_args.get("question", "")
                    if ask_user_fn:
                        answer = ask_user_fn(question)
                    else:
                        answer = TOOLS["ask_user"](question=question)
                    console.print(f"  [green][User]:[/green] {answer}")
                    messages.append({"role": "tool", "tool_call_id": tool_name, "content": answer})
                    _log_action(log_file, step, {"type": "ask_user", "question": question, "answer": answer})

                    # Check if user approved a policy override
                    answer_lower = answer.lower()
                    if any(w in answer_lower for w in ["yes", "approve", "proceed", "ok", "go ahead", "confirmed"]):
                        # Mark current form values as approved
                        for k, v in get_form_values().items():
                            policy_overrides.add(f"{k}={v}")
                    continue

                # Policy check for submit clicks
                if tool_name == "browser_click" and _is_submit_click(tool_args.get("selector", "")):
                    fv = get_form_values()
                    if fv:
                        # Check if this exact submission was already approved
                        override_keys = {f"{k}={v}" for k, v in fv.items()}
                        if not override_keys.issubset(policy_overrides):
                            policy_result = check_policy(fv, policy_rules)
                            if not policy_result.allowed:
                                block_msg = f"BLOCKED: {policy_result.reason} Use ask_user to get approval or clarification."
                                console.print(f"  [red][Policy Blocked]: {block_msg}[/red]")
                                messages.append({"role": "tool", "tool_call_id": tool_name, "content": block_msg})
                                _log_action(log_file, step, {"type": "policy_blocked", "reason": policy_result.reason})
                                continue

                # Execute the tool
                tool_fn = TOOLS.get(tool_name)
                if not tool_fn:
                    obs = f"ERROR: Unknown tool '{tool_name}'."
                else:
                    try:
                        obs = tool_fn(**tool_args)
                    except Exception as e:
                        obs = f"ERROR executing {tool_name}: {e}\n{traceback.format_exc()}"

                # Truncate very long observations
                if isinstance(obs, str) and len(obs) > 5000:
                    obs = obs[:5000] + "\n... (truncated)"

                console.print(f"  [dim]→ {str(obs)[:300]}[/dim]")
                messages.append({"role": "tool", "tool_call_id": tool_name, "content": str(obs)})
                _log_action(log_file, step, {"type": "observation", "tool": tool_name, "result": str(obs)[:1000]})

        # Max steps reached
        if not result["success"]:
            result["summary"] = "Max steps reached without completing the task."
            result["steps_used"] = max_steps
            console.print("[red]⚠️ Max steps reached.[/red]")

    finally:
        # Cleanup browser
        try:
            browser.close()
            pw.stop()
        except Exception:
            pass

    # Save summary
    with open(run_dir / "summary.md", "w") as f:
        f.write(f"# Task Result\n\n{result['summary']}\n\n")
        f.write(f"Steps used: {result['steps_used']}\n")
        f.write(f"Success: {result['success']}\n")

    return result
