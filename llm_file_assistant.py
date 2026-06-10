from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

from fs_tools import list_files, read_file, search_in_file, write_file
from dotenv import load_dotenv

load_dotenv()

try:
    from google import genai
    from google.genai import types
except Exception:  # pragma: no cover - optional dependency
    genai = None
    types = None


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


DEFAULT_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")


def _dict_to_schema(d: Dict[str, Any]) -> types.Schema:
    if types is None:
        raise RuntimeError("google-genai package not installed")
    properties = {}
    if "properties" in d:
        for k, v in d["properties"].items():
            properties[k] = _dict_to_schema(v)
    items = None
    if "items" in d:
        items = _dict_to_schema(d["items"])
    return types.Schema(
        type=d.get("type", "string").upper(),
        description=d.get("description"),
        properties=properties or None,
        required=d.get("required"),
        items=items,
        enum=d.get("enum"),
    )


def _function_declarations() -> List[types.FunctionDeclaration]:
    if types is None:
        raise RuntimeError("google-genai package not installed")
    schemas = _tool_schemas()
    declarations = []
    for s in schemas:
        func_schema = s["function"]
        declarations.append(
            types.FunctionDeclaration(
                name=func_schema["name"],
                description=func_schema["description"],
                parameters=_dict_to_schema(func_schema["parameters"])
            )
        )
    return declarations


def _tool_config() -> types.GenerateContentConfig:
    if types is None:
        raise RuntimeError("google-genai package not installed")
    return types.GenerateContentConfig(
        system_instruction=_system_prompt(),
        tools=[types.Tool(function_declarations=_function_declarations())],
    )


class GeminiProvider:
    def __init__(self, model: Optional[str] = None) -> None:
        if genai is None or types is None:
            raise RuntimeError("google-genai package not installed")
        self.client = genai.Client()
        self.model = model or DEFAULT_MODEL

    def generate(self, contents: List[Any]) -> Any:
        return self.client.models.generate_content(
            model=self.model,
            contents=contents,
            config=_tool_config(),
        )


def load_provider() -> GeminiProvider:
    return GeminiProvider()


def _system_prompt() -> str:
    return (
        "You are a file assistant. Use the available tools to answer user requests. "
        "When asked to read resumes, list the directory first, then read relevant files. "
        "When asked to find resumes with keywords, search each resume file. "
        "When asked to create a summary file, read the resume and then write a summary. "
        "Do not overwrite existing files unless the user explicitly asks; set allow_overwrite accordingly."
    )


class LLMFileAssistant:
    def __init__(self, provider: Optional[GeminiProvider] = None) -> None:
        self.provider = provider or load_provider()

    def run(self, user_query: str) -> Dict[str, Any]:
        contents: List[Any] = [user_query]
        tool_results: List[Dict[str, Any]] = []
        try:
            for _ in range(MAX_TOOL_CALLS):
                response = self.provider.generate(contents)
                candidates = getattr(response, "candidates", None) or []
                if not candidates:
                    return {
                        "success": False,
                        "error": "No response candidates",
                        "tool_calls": tool_results,
                    }
                content = candidates[0].content
                contents.append(content)

                # Extract function calls from the model's response
                function_calls = []
                parts = getattr(content, "parts", None) or []
                for part in parts:
                    function_call = getattr(part, "function_call", None)
                    if function_call:
                        function_calls.append(function_call)

                if not function_calls:
                    # Check if there is text in the content
                    text_parts = [part.text for part in parts if getattr(part, "text", None)]
                    response_text = "\n".join(text_parts).strip() if text_parts else ""
                    return {
                        "success": True,
                        "response": response_text,
                        "tool_calls": tool_results,
                    }

                # Execute function calls and gather responses
                response_parts = []
                for function_call in function_calls:
                    name = function_call.name
                    args = function_call.args if isinstance(function_call.args, dict) else {}
                    result = _call_tool(name, args)
                    tool_results.append({"name": name, "arguments": args, "result": result})

                    fc_id = getattr(function_call, "id", None)
                    if fc_id:
                        part = types.Part.from_function_response(name=name, response=result, id=fc_id)
                    else:
                        part = types.Part.from_function_response(name=name, response=result)
                    response_parts.append(part)

                contents.append(types.Content(role="tool", parts=response_parts))

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
