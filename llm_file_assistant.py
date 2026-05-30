from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

from fs_tools import list_files, read_file, search_in_file, write_file
from dotenv import load_dotenv

load_dotenv()

try:
    from openai import OpenAI
except Exception:  # pragma: no cover - optional dependency
    OpenAI = None


ToolResult = Dict[str, Any]


def _get_env_int(name: str, default: int) -> int:
    raw = os.getenv(name, str(default))
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


MAX_TOOL_CALLS = _get_env_int("LLM_TOOL_MAX_CALLS", 6)
REQUIRED_ARGS = {
    "list_files": ["directory"],
    "read_file": ["filepath"],
    "write_file": ["filepath", "content"],
    "search_in_file": ["filepath", "keyword"],
}


def _tool_schemas() -> List[Dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": "list_files",
                "description": "List files in a directory, optionally filtered by extension.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "directory": {"type": "string"},
                        "extension": {"type": "string"},
                    },
                    "required": ["directory"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "Read a resume file (PDF, TXT, DOCX) and return text and metadata.",
                "parameters": {
                    "type": "object",
                    "properties": {"filepath": {"type": "string"}},
                    "required": ["filepath"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "write_file",
                "description": "Write content to a file, creating directories if needed.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "filepath": {"type": "string"},
                        "content": {"type": "string"},
                        "allow_overwrite": {"type": "boolean"},
                    },
                    "required": ["filepath", "content"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "search_in_file",
                "description": "Search a file for a keyword with context.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "filepath": {"type": "string"},
                        "keyword": {"type": "string"},
                    },
                    "required": ["filepath", "keyword"],
                },
            },
        },
    ]


def _validate_args(name: str, args: Dict[str, Any]) -> Optional[str]:
    if not isinstance(args, dict):
        return "Tool arguments must be a JSON object"
    parse_error = args.get("__parse_error__")
    if parse_error:
        return str(parse_error)
    required = REQUIRED_ARGS.get(name, [])
    missing = [key for key in required if args.get(key) in (None, "")]
    if missing:
        return f"Missing required arguments: {', '.join(missing)}"
    if name == "write_file" and "allow_overwrite" in args and not isinstance(args["allow_overwrite"], bool):
        return "allow_overwrite must be a boolean"
    return None


def _call_tool(name: str, args: Dict[str, Any]) -> ToolResult:
    error = _validate_args(name, args)
    if error:
        return {"success": False, "error": error}
    safe_args = {key: value for key, value in args.items() if key != "__parse_error__"}
    try:
        if name == "list_files":
            return {"success": True, "result": list_files(**safe_args)}
        if name == "read_file":
            return read_file(**safe_args)
        if name == "write_file":
            return write_file(**safe_args)
        if name == "search_in_file":
            return search_in_file(**safe_args)
    except Exception as exc:
        return {"success": False, "error": str(exc)}
    return {"success": False, "error": f"Unknown tool: {name}"}


def _parse_arguments(raw_arguments: str) -> Dict[str, Any]:
    if not raw_arguments:
        return {}
    try:
        parsed = json.loads(raw_arguments)
    except json.JSONDecodeError as exc:
        return {"__parse_error__": f"Invalid JSON arguments: {exc.msg}"}
    if not isinstance(parsed, dict):
        return {"__parse_error__": "Arguments must be a JSON object"}
    return parsed


class OpenAIProvider:
    def __init__(self, model: Optional[str] = None) -> None:
        if OpenAI is None:
            raise RuntimeError("openai package not installed")
        self.client = OpenAI()
        self.model = model or os.getenv("OPENAI_MODEL", "gpt-4o-mini")

    def chat(self, messages: List[Dict[str, Any]], tools: List[Dict[str, Any]]) -> Any:
        return self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            tools=tools,
            tool_choice="auto",
        )


def load_provider() -> OpenAIProvider:
    provider_name = os.getenv("LLM_PROVIDER", "openai").lower()
    if provider_name == "openai":
        return OpenAIProvider()
    raise RuntimeError(f"Unsupported provider: {provider_name}")


def _system_prompt() -> str:
    return (
        "You are a file assistant. Use the available tools to answer user requests. "
        "When asked to read resumes, list the directory first, then read relevant files. "
        "When asked to find resumes with keywords, search each resume file. "
        "When asked to create a summary file, read the resume and then write a summary. "
        "Do not overwrite existing files unless the user explicitly asks; set allow_overwrite accordingly."
    )


class LLMFileAssistant:
    def __init__(self, provider: Optional[OpenAIProvider] = None) -> None:
        self.provider = provider or load_provider()
        self.tools = _tool_schemas()

    def run(self, user_query: str) -> Dict[str, Any]:
        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": _system_prompt()},
            {"role": "user", "content": user_query},
        ]

        tool_results: List[Dict[str, Any]] = []
        try:
            for _ in range(MAX_TOOL_CALLS):
                response = self.provider.chat(messages, self.tools)
                message = response.choices[0].message
                tool_calls = getattr(message, "tool_calls", None) or []
                assistant_payload: Dict[str, Any] = {
                    "role": "assistant",
                    "content": message.content or "",
                }
                if tool_calls:
                    assistant_payload["tool_calls"] = [
                        {
                            "id": tool_call.id,
                            "type": "function",
                            "function": {
                                "name": tool_call.function.name,
                                "arguments": tool_call.function.arguments,
                            },
                        }
                        for tool_call in tool_calls
                    ]
                messages.append(assistant_payload)

                if not tool_calls:
                    return {
                        "success": True,
                        "response": message.content or "",
                        "tool_calls": tool_results,
                    }

                for tool_call in tool_calls:
                    name = tool_call.function.name
                    args = _parse_arguments(tool_call.function.arguments)
                    result = _call_tool(name, args)
                    tool_results.append({"name": name, "arguments": args, "result": result})
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "content": json.dumps(result, ensure_ascii=True),
                        }
                    )
        except Exception as exc:
            return {"success": False, "error": str(exc), "tool_calls": tool_results}

        return {
            "success": False,
            "error": "Tool call limit reached",
            "tool_calls": tool_results,
        }


def run_cli() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="LLM file assistant")
    parser.add_argument("query", help="User query for the assistant")
    args = parser.parse_args()

    assistant = LLMFileAssistant()
    result = assistant.run(args.query)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    run_cli()
