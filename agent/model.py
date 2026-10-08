"""Model interface – one function to call the LLM.

Default provider is Google Gemini via the google-genai SDK.
Swap providers by editing only this file.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any

from google import genai
from google.genai import types

_client: genai.Client | None = None


def _get_client() -> genai.Client:
    """Lazy-init the Gemini client."""
    global _client
    if _client is None:
        api_key = os.environ.get("GEMINI_API_KEY", "")
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY not set in environment")
        _client = genai.Client(api_key=api_key)
    return _client


@dataclass
class ToolCall:
    """A single tool call extracted from the model response."""
    id: str
    name: str
    arguments: dict[str, Any]
    raw_part: Any = None


@dataclass
class ModelResponse:
    """Parsed model response."""
    text: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    raw_content: Any = None


def _convert_tools_to_gemini(tools: list[dict]) -> list[types.Tool]:
    """Convert our tool schemas to Gemini function declarations."""
    declarations = []
    for tool in tools:
        func = tool["function"]
        params = func.get("parameters", {})
        schema = params if params else None
        declarations.append(types.FunctionDeclaration(
            name=func["name"],
            description=func.get("description", ""),
            parameters=schema,
        ))
    return [types.Tool(function_declarations=declarations)]


def _convert_messages_to_gemini(
    messages: list[dict], system: str
) -> tuple[str, list[types.Content]]:
    """Convert our message format to Gemini contents."""
    contents: list[types.Content] = []
    for msg in messages:
        role = msg["role"]
        if role == "system":
            continue
        gemini_role = "user" if role in ("user", "tool") else "model"

        if role == "tool":
            part = types.Part.from_function_response(
                name=msg.get("name", msg.get("tool_call_id", "unknown")),
                response={"result": msg.get("content", "")},
            )
            contents.append(types.Content(role="user", parts=[part]))
        elif role == "assistant":
            # If we saved the raw Gemini content with its thought_signatures, reuse it!
            if msg.get("raw_content") is not None:
                contents.append(msg["raw_content"])
            elif "tool_calls" in msg:
                parts = []
                if msg.get("content"):
                    parts.append(types.Part.from_text(text=msg["content"]))
                for tc in msg["tool_calls"]:
                    if isinstance(tc, dict) and tc.get("raw_part") is not None:
                        parts.append(tc["raw_part"])
                    else:
                        tc_name = tc["name"] if isinstance(tc, dict) else tc.name
                        tc_args = tc["arguments"] if isinstance(tc, dict) else tc.arguments
                        parts.append(types.Part.from_function_call(
                            name=tc_name,
                            args=tc_args,
                        ))
                contents.append(types.Content(role="model", parts=parts))
            else:
                text = msg.get("content", "")
                if text:
                    contents.append(types.Content(
                        role="model",
                        parts=[types.Part.from_text(text=text)],
                    ))
        else:
            text = msg.get("content", "")
            if text:
                contents.append(types.Content(
                    role=gemini_role,
                    parts=[types.Part.from_text(text=text)],
                ))
    return system, contents


def call_model(
    messages: list[dict],
    tools: list[dict],
    system: str,
    model: str | None = None,
) -> ModelResponse:
    """Call the LLM and return a parsed ModelResponse.

    Args:
        messages: Conversation history in OpenAI-style format.
        tools: Tool schemas in OpenAI-style function-calling format.
        system: System prompt text.
        model: Model name override (default from MODEL env var).

    Returns:
        ModelResponse with optional text and tool_calls.
    """
    client = _get_client()
    model_name = model or os.environ.get("MODEL", "gemini-3.5-flash-lite")
    system_instruction, contents = _convert_messages_to_gemini(messages, system)

    config = types.GenerateContentConfig(
        system_instruction=system_instruction or system,
        tools=_convert_tools_to_gemini(tools) if tools else None,
        temperature=0.1,
    )

    response = client.models.generate_content(
        model=model_name,
        contents=contents,
        config=config,
    )

    # Parse the response
    result = ModelResponse()
    if response.candidates and response.candidates[0].content:
        result.raw_content = response.candidates[0].content
        for part in response.candidates[0].content.parts:
            if part.text:
                result.text = (result.text or "") + part.text
            elif part.function_call:
                fc = part.function_call
                # Convert args from proto to dict
                args = dict(fc.args) if fc.args else {}
                clean_args = {}
                for k, v in args.items():
                    if isinstance(v, (str, int, float, bool)):
                        clean_args[k] = v
                    elif isinstance(v, list):
                        clean_args[k] = [
                            dict(item) if hasattr(item, '__iter__') and not isinstance(item, str)
                            else item
                            for item in v
                        ]
                    elif hasattr(v, '__iter__') and not isinstance(v, str):
                        try:
                            clean_args[k] = json.loads(json.dumps(v, default=str))
                        except (TypeError, ValueError):
                            clean_args[k] = str(v)
                    else:
                        clean_args[k] = v

                result.tool_calls.append(ToolCall(
                    id=fc.name,
                    name=fc.name,
                    arguments=clean_args,
                    raw_part=part,
                ))
    return result
