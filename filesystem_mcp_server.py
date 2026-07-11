"""
Filesystem MCP Server — exposes Milestone 1 file tools over the Model Context Protocol.

JSON-RPC 2.0 compliant via the official MCP Python SDK. Supports stdio and
streamable-http transports.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

load_dotenv()

from fs_tools import (
    BASE_DIR,
    MAX_CONTENT_CHARS,
    MAX_FILE_BYTES,
    list_files,
    read_file,
    search_in_file,
    write_file,
)

logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("filesystem_mcp_server")

# JSON-RPC 2.0 error codes (https://www.jsonrpc.org/specification#error_object)
JSONRPC_PARSE_ERROR = -32700
JSONRPC_INVALID_REQUEST = -32600
JSONRPC_METHOD_NOT_FOUND = -32601
JSONRPC_INVALID_PARAMS = -32602
JSONRPC_INTERNAL_ERROR = -32603

# Application-specific error codes
FS_NOT_FOUND = -32001
FS_ACCESS_DENIED = -32002
FS_UNSUPPORTED = -32003
FS_VALIDATION_ERROR = -32004

DEFAULT_RESUME_DIR = os.getenv("MATCHING_AGENT_RESUME_DIR", "data/synthetic_resumes")
DEFAULT_WATCH_INTERVAL = float(os.getenv("MCP_WATCH_POLL_INTERVAL", "1.0"))

# Snapshot of known files for watch_directory (path -> mtime)
_watch_snapshots: Dict[str, Dict[str, float]] = {}


def _mcp_error(code: int, message: str, data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Build a structured error payload with JSON-RPC status code."""
    payload: Dict[str, Any] = {"success": False, "error": message, "code": code}
    if data:
        payload["data"] = data
    return payload


def _success(data: Dict[str, Any]) -> Dict[str, Any]:
    return {"success": True, **data}


def _resolve_relative(path_str: str) -> Path:
    candidate = Path(path_str)
    if candidate.is_absolute():
        return candidate
    return BASE_DIR / path_str


def _snapshot_directory(directory: str) -> Dict[str, float]:
    resolved = _resolve_relative(directory)
    if not resolved.exists() or not resolved.is_dir():
        return {}
    snapshot: Dict[str, float] = {}
    for entry in resolved.iterdir():
        if entry.is_file():
            try:
                snapshot[str(entry.resolve())] = entry.stat().st_mtime
            except OSError:
                continue
    return snapshot


def _detect_changes(
    directory: str,
    extensions: Optional[List[str]] = None,
    since_snapshot: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    current = _snapshot_directory(directory)
    normalized_exts = None
    if extensions:
        normalized_exts = [
            ext.lower() if ext.startswith(".") else f".{ext.lower()}" for ext in extensions
        ]

    new_files: List[Dict[str, Any]] = []
    modified_files: List[Dict[str, Any]] = []
    deleted_files: List[str] = []

    for path_str, mtime in current.items():
        path = Path(path_str)
        if normalized_exts and path.suffix.lower() not in normalized_exts:
            continue
        metadata = {
            "name": path.name,
            "path": path_str,
            "relative_path": str(path.relative_to(BASE_DIR)) if path.is_relative_to(BASE_DIR) else None,
            "modified": mtime,
        }
        if since_snapshot is None:
            new_files.append(metadata)
        elif path_str not in since_snapshot:
            new_files.append(metadata)
        elif since_snapshot[path_str] < mtime:
            modified_files.append(metadata)

    if since_snapshot:
        for path_str in since_snapshot:
            if path_str not in current:
                deleted_files.append(path_str)

    return {
        "directory": directory,
        "new_files": new_files,
        "modified_files": modified_files,
        "deleted_files": deleted_files,
        "total_tracked": len(current),
    }


mcp = FastMCP(
    "filesystem-mcp-server",
    instructions=(
        "Filesystem MCP server for resume and document operations. "
        "Provides read, list, write, search, batch processing, and directory watching."
    ),
)


@mcp.resource("filesystem://config")
def get_configuration() -> str:
    """Expose server configuration as an MCP resource."""
    config = {
        "base_dir": str(BASE_DIR),
        "max_file_bytes": MAX_FILE_BYTES,
        "max_content_chars": MAX_CONTENT_CHARS,
        "default_resume_dir": DEFAULT_RESUME_DIR,
        "watch_poll_interval": DEFAULT_WATCH_INTERVAL,
        "supported_extensions": [".txt", ".pdf", ".docx", ".json"],
        "tools": [
            "read_file",
            "list_files",
            "write_file",
            "search_in_file",
            "watch_directory",
            "batch_process",
            "get_config",
            "update_config",
        ],
    }
    return json.dumps(config, indent=2)


@mcp.resource("file://{relative_path}")
def read_file_resource(relative_path: str) -> str:
    """Read a file and expose its content as an MCP resource."""
    result = read_file(relative_path)
    if not result.get("success"):
        raise FileNotFoundError(str(result.get("error", "File not found")))
    return str(result.get("content", ""))


@mcp.tool()
def get_config() -> Dict[str, Any]:
    """Return current filesystem MCP server configuration."""
    return _success(
        {
            "base_dir": str(BASE_DIR),
            "max_file_bytes": MAX_FILE_BYTES,
            "max_content_chars": MAX_CONTENT_CHARS,
            "default_resume_dir": DEFAULT_RESUME_DIR,
            "watch_poll_interval": DEFAULT_WATCH_INTERVAL,
        }
    )


@mcp.tool()
def update_config(
    max_file_bytes: Optional[int] = None,
    max_content_chars: Optional[int] = None,
    watch_poll_interval: Optional[float] = None,
) -> Dict[str, Any]:
    """Update runtime configuration (session-scoped overrides)."""
    global MAX_FILE_BYTES, MAX_CONTENT_CHARS, DEFAULT_WATCH_INTERVAL

    updated: Dict[str, Any] = {}
    if max_file_bytes is not None:
        if max_file_bytes <= 0:
            return _mcp_error(JSONRPC_INVALID_PARAMS, "max_file_bytes must be positive")
        MAX_FILE_BYTES = max_file_bytes
        updated["max_file_bytes"] = max_file_bytes
    if max_content_chars is not None:
        if max_content_chars <= 0:
            return _mcp_error(JSONRPC_INVALID_PARAMS, "max_content_chars must be positive")
        MAX_CONTENT_CHARS = max_content_chars
        updated["max_content_chars"] = max_content_chars
    if watch_poll_interval is not None:
        if watch_poll_interval <= 0:
            return _mcp_error(JSONRPC_INVALID_PARAMS, "watch_poll_interval must be positive")
        DEFAULT_WATCH_INTERVAL = watch_poll_interval
        updated["watch_poll_interval"] = watch_poll_interval

    return _success({"updated": updated, "message": "Configuration updated for this session"})


@mcp.tool(name="read_file")
def mcp_read_file(filepath: str) -> Dict[str, Any]:
    """Read a file (TXT, PDF, DOCX) and return content with metadata."""
    result = read_file(filepath)
    if not result.get("success"):
        error = str(result.get("error", "Read failed"))
        code = FS_NOT_FOUND if "not found" in error.lower() else FS_ACCESS_DENIED
        return _mcp_error(code, error, {"metadata": result.get("metadata")})
    return _success(result)


@mcp.tool(name="list_files")
def mcp_list_files(directory: str, extension: Optional[str] = None) -> Dict[str, Any]:
    """List files in a directory, optionally filtered by extension."""
    try:
        files = list_files(directory=directory, extension=extension)
        return _success({"files": files, "count": len(files)})
    except ValueError as exc:
        return _mcp_error(FS_VALIDATION_ERROR, str(exc))
    except Exception as exc:
        return _mcp_error(JSONRPC_INTERNAL_ERROR, str(exc))


@mcp.tool(name="write_file")
def mcp_write_file(
    filepath: str,
    content: str,
    allow_overwrite: bool = False,
) -> Dict[str, Any]:
    """Write content to a file with atomic writes and overwrite protection."""
    result = write_file(filepath=filepath, content=content, allow_overwrite=allow_overwrite)
    if not result.get("success"):
        error = str(result.get("error", "Write failed"))
        code = FS_ACCESS_DENIED if "exists" in error.lower() else FS_VALIDATION_ERROR
        return _mcp_error(code, error, {"metadata": result.get("metadata")})
    return _success(result)


@mcp.tool(name="search_in_file")
def mcp_search_in_file(filepath: str, keyword: str) -> Dict[str, Any]:
    """Search for a keyword in a file with context snippets."""
    result = search_in_file(filepath=filepath, keyword=keyword)
    if not result.get("success"):
        error = str(result.get("error", "Search failed"))
        code = FS_NOT_FOUND if "not found" in error.lower() else FS_UNSUPPORTED
        return _mcp_error(code, error)
    return _success(result)


@mcp.tool()
def watch_directory(
    directory: str = DEFAULT_RESUME_DIR,
    extensions: Optional[List[str]] = None,
    timeout_seconds: float = 5.0,
    reset_baseline: bool = False,
) -> Dict[str, Any]:
    """
    Monitor a directory for new or modified resume files.

    Polls the directory until changes are detected or timeout is reached.
    Use reset_baseline=True to establish a fresh snapshot without reporting
    existing files as new.
    """
    watch_key = f"{directory}:{','.join(extensions or [])}"
    if reset_baseline or watch_key not in _watch_snapshots:
        _watch_snapshots[watch_key] = _snapshot_directory(directory)
        if reset_baseline:
            return _success(
                {
                    "status": "baseline_set",
                    "directory": directory,
                    "tracked_files": len(_watch_snapshots[watch_key]),
                }
            )

    since = _watch_snapshots[watch_key]
    deadline = time.monotonic() + max(timeout_seconds, 0.1)
    changes: Dict[str, Any] = {"new_files": [], "modified_files": [], "deleted_files": []}

    while time.monotonic() < deadline:
        changes = _detect_changes(directory, extensions=extensions, since_snapshot=since)
        if changes["new_files"] or changes["modified_files"] or changes["deleted_files"]:
            _watch_snapshots[watch_key] = _snapshot_directory(directory)
            return _success(
                {
                    "status": "changes_detected",
                    **changes,
                    "watched_seconds": round(timeout_seconds - max(0, deadline - time.monotonic()), 2),
                }
            )
        time.sleep(DEFAULT_WATCH_INTERVAL)

    return _success(
        {
            "status": "no_changes",
            "directory": directory,
            "watched_seconds": timeout_seconds,
            "tracked_files": len(since),
        }
    )


BatchAction = Literal["read", "search", "list"]


@mcp.tool()
def batch_process(
    operations: List[Dict[str, Any]],
    stop_on_error: bool = False,
) -> Dict[str, Any]:
    """
    Process multiple file operations in a single request.

    Each operation is a dict with an 'action' key:
    - read: {action, filepath}
    - search: {action, filepath, keyword}
    - list: {action, directory, extension?}
    """
    if not operations:
        return _mcp_error(JSONRPC_INVALID_PARAMS, "operations list is required")

    results: List[Dict[str, Any]] = []
    errors = 0

    for idx, op in enumerate(operations):
        action = op.get("action")
        entry: Dict[str, Any] = {"index": idx, "action": action}

        if action == "read":
            filepath = op.get("filepath")
            if not filepath:
                entry.update(_mcp_error(JSONRPC_INVALID_PARAMS, "filepath required for read"))
            else:
                entry["result"] = mcp_read_file(filepath)
        elif action == "search":
            filepath = op.get("filepath")
            keyword = op.get("keyword")
            if not filepath or not keyword:
                entry.update(_mcp_error(JSONRPC_INVALID_PARAMS, "filepath and keyword required for search"))
            else:
                entry["result"] = mcp_search_in_file(filepath, keyword)
        elif action == "list":
            directory = op.get("directory")
            if not directory:
                entry.update(_mcp_error(JSONRPC_INVALID_PARAMS, "directory required for list"))
            else:
                entry["result"] = mcp_list_files(directory, op.get("extension"))
        else:
            entry.update(_mcp_error(JSONRPC_METHOD_NOT_FOUND, f"Unknown action: {action}"))

        result_payload = entry.get("result", entry)
        if not result_payload.get("success", False):
            errors += 1
            if stop_on_error:
                results.append(entry)
                return _success(
                    {
                        "results": results,
                        "processed": len(results),
                        "errors": errors,
                        "stopped_early": True,
                    }
                )

        results.append(entry)

    return _success(
        {
            "results": results,
            "processed": len(results),
            "errors": errors,
            "stopped_early": False,
        }
    )


def main() -> None:
    transport = os.getenv("MCP_TRANSPORT", "stdio")
    host = os.getenv("MCP_HOST", "127.0.0.1")
    port = int(os.getenv("MCP_PORT", "8765"))

    logger.info("Starting filesystem MCP server (transport=%s, base_dir=%s)", transport, BASE_DIR)

    if transport == "stdio":
        mcp.run(transport="stdio")
    elif transport in ("streamable-http", "http"):
        mcp.run(transport="streamable-http", host=host, port=port)
    else:
        logger.error("Unsupported transport: %s", transport)
        sys.exit(1)


if __name__ == "__main__":
    main()
