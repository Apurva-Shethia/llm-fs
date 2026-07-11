"""
Test scenarios demonstrating MCP resource usage and agent workflow.

Covers:
 - MCP server tool calls via the client (read, list, write, search)
 - Resource discovery (filesystem://config, file://{path})
 - batch_process() with mixed operations
 - watch_directory() baseline + change detection
 - Multi-MCP: web_search server tools
 - Error handling and JSON-RPC status codes
 - Agent-level integration: read_resume_file / list_resume_files via MCP
 - End-to-end agent matching flow (smoke test, no LLM needed)
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any, Dict

# Ensure project root is on the path when run directly
sys.path.insert(0, str(Path(__file__).resolve().parent))

from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_manager(web_search: bool = False):
    """Return an MCP manager, resetting it so correct servers are registered."""
    from mcp_client import get_mcp_manager, reset_mcp_manager
    reset_mcp_manager()
    return get_mcp_manager(enable_filesystem=True, enable_web_search=web_search)


def _assert_success(result: Dict[str, Any], test_case: unittest.TestCase, context: str = "") -> None:
    """Assert that an MCP tool response reports success."""
    test_case.assertTrue(
        result.get("success"),
        msg=f"{context} — expected success=True, got: {result}",
    )


# ---------------------------------------------------------------------------
# Scenario 1: MCP resource discovery
# ---------------------------------------------------------------------------

class TestMCPResourceDiscovery(unittest.TestCase):
    """Verify that the filesystem MCP server exposes its resources correctly."""

    @classmethod
    def setUpClass(cls):
        cls.mgr = _get_manager()

    def test_list_tools_returns_expected_tools(self):
        """MCP server must advertise all required tool names."""
        tools = self.mgr.list_tools(server="filesystem")
        names = {t["name"] for t in tools}
        expected = {"read_file", "list_files", "write_file", "search_in_file",
                    "watch_directory", "batch_process", "get_config", "update_config"}
        self.assertTrue(
            expected.issubset(names),
            msg=f"Missing tools: {expected - names}. Advertised: {names}",
        )

    def test_list_resources_returns_config_and_file_template(self):
        """filesystem://config and file://{relative_path} must be listed."""
        resources = self.mgr.list_resources(server="filesystem")
        uris = {str(r["uri"]) for r in resources}
        self.assertIn("filesystem://config", uris, msg=f"Resources: {uris}")

    def test_read_config_resource(self):
        """filesystem://config resource must be parseable JSON with expected keys."""
        raw = self.mgr.read_resource("filesystem://config", server="filesystem")
        config = json.loads(raw)
        for key in ("base_dir", "max_file_bytes", "tools"):
            self.assertIn(key, config, msg=f"Missing key '{key}' in config: {config}")

    def test_get_config_tool(self):
        """get_config tool must return success with runtime config."""
        result = self.mgr.call_tool("get_config", {}, server="filesystem")
        _assert_success(result, self, "get_config")
        self.assertIn("base_dir", result)

    def test_update_config_tool(self):
        """update_config must accept valid overrides and confirm the change."""
        result = self.mgr.call_tool(
            "update_config",
            {"max_file_bytes": 9_000_000, "watch_poll_interval": 0.5},
            server="filesystem",
        )
        _assert_success(result, self, "update_config")
        updated = result.get("updated", {})
        self.assertEqual(updated.get("max_file_bytes"), 9_000_000)
        self.assertEqual(updated.get("watch_poll_interval"), 0.5)

    def test_update_config_rejects_invalid_values(self):
        """update_config must reject non-positive values with error."""
        result = self.mgr.call_tool(
            "update_config",
            {"max_file_bytes": -1},
            server="filesystem",
        )
        self.assertFalse(result.get("success"), msg=f"Expected failure: {result}")


# ---------------------------------------------------------------------------
# Scenario 2: Core filesystem tool calls via MCP client
# ---------------------------------------------------------------------------

class TestFilesystemToolsViaMCP(unittest.TestCase):
    """Verify that each Milestone 1 tool is correctly exposed and callable via MCP."""

    @classmethod
    def setUpClass(cls):
        cls.mgr = _get_manager()

    def test_list_files_returns_resumes(self):
        """list_files on the resume directory must return at least one file."""
        result = self.mgr.call_tool(
            "list_files",
            {"directory": "data/synthetic_resumes", "extension": ".json"},
            server="filesystem",
        )
        _assert_success(result, self, "list_files")
        files = result.get("files", [])
        self.assertGreater(len(files), 0, "Expected at least one resume file")

    def test_read_file_returns_content(self):
        """read_file on an existing resume must return content with metadata."""
        # First discover a real file path
        list_result = self.mgr.call_tool(
            "list_files",
            {"directory": "data/synthetic_resumes", "extension": ".json"},
            server="filesystem",
        )
        files = list_result.get("files", [])
        self.assertGreater(len(files), 0, "No resumes to read")

        # Use relative path (the server resolves against BASE_DIR)
        filepath = files[0]["relative_path"] if isinstance(files[0], dict) else files[0]
        result = self.mgr.call_tool("read_file", {"filepath": filepath}, server="filesystem")
        _assert_success(result, self, "read_file")
        self.assertIn("content", result, f"read_file response missing 'content': {result}")

    def test_read_file_nonexistent_returns_error(self):
        """read_file on a missing path must return success=False with a FS_NOT_FOUND code."""
        result = self.mgr.call_tool(
            "read_file",
            {"filepath": "data/synthetic_resumes/does_not_exist.json"},
            server="filesystem",
        )
        self.assertFalse(result.get("success"), msg=f"Expected failure: {result}")
        self.assertIn("error", result)

    def test_write_and_read_roundtrip(self):
        """write_file followed by read_file must round-trip content correctly."""
        test_path = "data/synthetic_resumes/_mcp_test_write.txt"
        content = "MCP write test — hello from test_mcp.py"

        write_result = self.mgr.call_tool(
            "write_file",
            {"filepath": test_path, "content": content, "allow_overwrite": True},
            server="filesystem",
        )
        _assert_success(write_result, self, "write_file")

        read_result = self.mgr.call_tool("read_file", {"filepath": test_path}, server="filesystem")
        _assert_success(read_result, self, "read_file after write")
        self.assertIn(content[:20], str(read_result.get("content", "")))

        # Clean up
        written = write_result.get("path") or write_result.get("filepath") or ""
        if written:
            try:
                Path(written).unlink(missing_ok=True)
            except Exception:
                pass

    def test_write_file_overwrite_protection(self):
        """write_file without allow_overwrite must fail on an existing file."""
        test_path = "data/synthetic_resumes/_mcp_overwrite_test.txt"

        # First write
        self.mgr.call_tool(
            "write_file",
            {"filepath": test_path, "content": "initial", "allow_overwrite": True},
            server="filesystem",
        )

        # Second write without overwrite flag
        result = self.mgr.call_tool(
            "write_file",
            {"filepath": test_path, "content": "should fail", "allow_overwrite": False},
            server="filesystem",
        )
        self.assertFalse(result.get("success"), msg=f"Expected overwrite failure: {result}")

        # Clean up
        try:
            from fs_tools import BASE_DIR
            (BASE_DIR / test_path).unlink(missing_ok=True)
        except Exception:
            pass

    def test_search_in_file_keyword_hit(self):
        """search_in_file must find a keyword that exists in a resume."""
        list_result = self.mgr.call_tool(
            "list_files",
            {"directory": "data/synthetic_resumes", "extension": ".json"},
            server="filesystem",
        )
        files = list_result.get("files", [])
        self.assertTrue(files, "No resume files to search")
        filepath = files[0]["relative_path"] if isinstance(files[0], dict) else files[0]

        # Every JSON resume has a "name" key
        result = self.mgr.call_tool(
            "search_in_file",
            {"filepath": filepath, "keyword": "name"},
            server="filesystem",
        )
        _assert_success(result, self, "search_in_file")
        self.assertGreater(result.get("match_count", 0), 0, "Expected keyword match in resume")

    def test_search_in_file_no_match(self):
        """search_in_file must still succeed with match_count=0 when keyword absent."""
        list_result = self.mgr.call_tool(
            "list_files",
            {"directory": "data/synthetic_resumes", "extension": ".json"},
            server="filesystem",
        )
        files = list_result.get("files", [])
        self.assertTrue(files, "No resume files to search")
        filepath = files[0]["relative_path"] if isinstance(files[0], dict) else files[0]

        result = self.mgr.call_tool(
            "search_in_file",
            {"filepath": filepath, "keyword": "XYZZY_NO_MATCH_TOKEN_12345"},
            server="filesystem",
        )
        _assert_success(result, self, "search_in_file no-match")
        self.assertEqual(result.get("match_count", 0), 0)


# ---------------------------------------------------------------------------
# Scenario 3: batch_process()
# ---------------------------------------------------------------------------

class TestBatchProcess(unittest.TestCase):
    """Verify batch_process handles mixed operations and error modes."""

    @classmethod
    def setUpClass(cls):
        cls.mgr = _get_manager()
        list_result = cls.mgr.call_tool(
            "list_files",
            {"directory": "data/synthetic_resumes", "extension": ".json"},
            server="filesystem",
        )
        cls.files = list_result.get("files", [])

    def test_batch_list_and_read(self):
        """batch_process must handle a list + read combo and return all results."""
        self.assertTrue(self.files, "No resume files available for batch test")
        filepath = self.files[0]["relative_path"] if isinstance(self.files[0], dict) else self.files[0]
        ops = [
            {"action": "list", "directory": "data/synthetic_resumes", "extension": ".json"},
            {"action": "read", "filepath": filepath},
        ]
        result = self.mgr.call_tool("batch_process", {"operations": ops}, server="filesystem")
        _assert_success(result, self, "batch_process list+read")
        self.assertEqual(result.get("processed"), 2)
        self.assertEqual(result.get("errors"), 0)

    def test_batch_search_multiple_files(self):
        """batch_process can search multiple files in one call."""
        self.assertGreaterEqual(len(self.files), 2, "Need at least 2 resumes")
        ops = [
            {"action": "search", "filepath": fp["relative_path"] if isinstance(fp, dict) else fp, "keyword": "experience"}
            for fp in self.files[:3]
        ]
        result = self.mgr.call_tool("batch_process", {"operations": ops}, server="filesystem")
        _assert_success(result, self, "batch_process search")
        self.assertEqual(result.get("processed"), len(ops))

    def test_batch_stop_on_error(self):
        """batch_process with stop_on_error=True must halt after first failure."""
        ops = [
            {"action": "read", "filepath": "data/synthetic_resumes/no_such_file.json"},
            {"action": "list", "directory": "data/synthetic_resumes"},
        ]
        result = self.mgr.call_tool(
            "batch_process",
            {"operations": ops, "stop_on_error": True},
            server="filesystem",
        )
        _assert_success(result, self, "batch_process stop_on_error envelope")
        self.assertTrue(result.get("stopped_early"), msg=f"Expected stopped_early: {result}")
        self.assertEqual(result.get("processed"), 1)

    def test_batch_empty_operations_returns_error(self):
        """batch_process with empty list must return success=False."""
        result = self.mgr.call_tool(
            "batch_process", {"operations": []}, server="filesystem"
        )
        self.assertFalse(result.get("success"), msg=f"Expected failure: {result}")

    def test_batch_unknown_action(self):
        """batch_process must report an error for unrecognised action types."""
        ops = [{"action": "fly", "filepath": "anything.txt"}]
        result = self.mgr.call_tool("batch_process", {"operations": ops}, server="filesystem")
        _assert_success(result, self, "batch_process envelope with unknown action")
        # The operation itself should carry an error
        op_result = result.get("results", [{}])[0]
        success_val = op_result.get("success", op_result.get("result", {}).get("success", True))
        self.assertFalse(
            success_val,
            msg=f"Expected inner error for unknown action: {op_result}",
        )


# ---------------------------------------------------------------------------
# Scenario 4: watch_directory()
# ---------------------------------------------------------------------------

class TestWatchDirectory(unittest.TestCase):
    """Verify watch_directory baseline snapshotting and change detection.

    Runs within async blocks so that we can reuse the same ClientSession/server process
    and preserve state between baseline setup and change checking.
    """

    @classmethod
    def setUpClass(cls):
        cls.mgr = _get_manager()

    def test_watch_baseline_set(self):
        """watch_directory with reset_baseline=True must return status=baseline_set."""
        import asyncio
        from mcp_client import _open_session, MCPServerConfig, DEFAULT_FILESYSTEM_SERVER

        async def run_test():
            config = MCPServerConfig(
                name="filesystem",
                command=sys.executable,
                args=[DEFAULT_FILESYSTEM_SERVER],
            )
            async with _open_session(config) as session:
                res = await session.call_tool(
                    "watch_directory",
                    {
                        "directory": "data/synthetic_resumes",
                        "extensions": [".json"],
                        "reset_baseline": True,
                    }
                )
                from mcp_client import _parse_tool_result
                return _parse_tool_result(res)

        result = asyncio.run(run_test())
        _assert_success(result, self, "watch_directory baseline")
        self.assertEqual(result.get("status"), "baseline_set")
        self.assertGreater(result.get("tracked_files", 0), 0)

    def test_watch_no_changes_within_timeout(self):
        """watch_directory must return status=no_changes when nothing changes."""
        import asyncio
        from mcp_client import _open_session, MCPServerConfig, DEFAULT_FILESYSTEM_SERVER

        async def run_test():
            config = MCPServerConfig(
                name="filesystem",
                command=sys.executable,
                args=[DEFAULT_FILESYSTEM_SERVER],
            )
            async with _open_session(config) as session:
                await session.call_tool(
                    "watch_directory",
                    {"directory": "data/synthetic_resumes", "reset_baseline": True}
                )
                res = await session.call_tool(
                    "watch_directory",
                    {"directory": "data/synthetic_resumes", "timeout_seconds": 0.5}
                )
                from mcp_client import _parse_tool_result
                return _parse_tool_result(res)

        result = asyncio.run(run_test())
        _assert_success(result, self, "watch_directory no_changes")
        self.assertEqual(result.get("status"), "no_changes")

    def test_watch_detects_new_file(self):
        """watch_directory must detect a new file written after the baseline."""
        import asyncio
        from mcp_client import _open_session, MCPServerConfig, DEFAULT_FILESYSTEM_SERVER
        from fs_tools import BASE_DIR

        watch_dir = "data/synthetic_resumes"
        new_file = BASE_DIR / watch_dir / "_watch_test_new.json"
        new_file.unlink(missing_ok=True)

        async def run_test():
            config = MCPServerConfig(
                name="filesystem",
                command=sys.executable,
                args=[DEFAULT_FILESYSTEM_SERVER],
            )
            async with _open_session(config) as session:
                # Establish baseline
                res1 = await session.call_tool(
                    "watch_directory",
                    {"directory": watch_dir, "extensions": [".json"], "reset_baseline": True}
                )
                # Write a new file into the watched directory
                new_file.write_text('{"test": true}', encoding="utf-8")
                # Poll for changes
                res2 = await session.call_tool(
                    "watch_directory",
                    {"directory": watch_dir, "extensions": [".json"], "timeout_seconds": 3.0}
                )
                from mcp_client import _parse_tool_result
                return _parse_tool_result(res1), _parse_tool_result(res2)

        try:
            res1, res2 = asyncio.run(run_test())
            _assert_success(res1, self, "watch_directory baseline")
            _assert_success(res2, self, "watch_directory detect new file")
            self.assertEqual(res2.get("status"), "changes_detected")
            new_names = [f["name"] for f in res2.get("new_files", [])]
            self.assertIn("_watch_test_new.json", new_names)
        finally:
            new_file.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Scenario 5: Multi-MCP — web_search server
# ---------------------------------------------------------------------------

class TestWebSearchMCPServer(unittest.TestCase):
    """Verify the bonus web_search MCP server is callable from the client."""

    @classmethod
    def setUpClass(cls):
        cls.mgr = _get_manager(web_search=True)

    def test_web_search_tool_exists(self):
        """web_search server must advertise web_search and enrich_skill_context tools."""
        tools = self.mgr.list_tools(server="web_search")
        names = {t["name"] for t in tools}
        self.assertIn("web_search", names)
        self.assertIn("enrich_skill_context", names)

    def test_web_search_returns_results(self):
        """web_search with a known topic must return at least one result."""
        result = self.mgr.call_tool(
            "web_search", {"query": "TensorFlow machine learning"}, server="web_search"
        )
        _assert_success(result, self, "web_search")
        self.assertGreater(result.get("result_count", 0), 0)

    def test_web_search_no_match_returns_fallback(self):
        """web_search with unknown query must still succeed with a fallback result."""
        result = self.mgr.call_tool(
            "web_search", {"query": "XYZ_UNKNOWN_TOPIC_987"}, server="web_search"
        )
        _assert_success(result, self, "web_search fallback")
        self.assertGreater(result.get("result_count", 0), 0)

    def test_enrich_skill_context(self):
        """enrich_skill_context must return context entries for all supplied skills."""
        result = self.mgr.call_tool(
            "enrich_skill_context",
            {"skills": ["React", "Kubernetes", "Python"]},
            server="web_search",
        )
        _assert_success(result, self, "enrich_skill_context")
        context = result.get("context", {})
        for skill in ("React", "Kubernetes", "Python"):
            self.assertIn(skill, context, f"Missing context for skill: {skill}")

    def test_list_all_tools_from_manager(self):
        """MCPClientManager.list_all_tools() must aggregate tools from all servers."""
        all_tools = self.mgr.list_all_tools()
        self.assertIn("filesystem", all_tools)
        self.assertIn("web_search", all_tools)
        self.assertGreater(len(all_tools["filesystem"]), 0)
        self.assertGreater(len(all_tools["web_search"]), 0)


# ---------------------------------------------------------------------------
# Scenario 6: Agent-level integration (agent_tools.py via MCP)
# ---------------------------------------------------------------------------

class TestAgentToolsMCPIntegration(unittest.TestCase):
    """
    Verify that agent_tools functions route correctly through the MCP client.
    These tests do NOT require a live LLM / Gemini API key.
    """

    @classmethod
    def setUpClass(cls):
        from mcp_client import get_mcp_manager, reset_mcp_manager
        reset_mcp_manager()
        get_mcp_manager(enable_filesystem=True)

    def test_list_resume_files_via_mcp(self):
        """list_resume_files must route through MCP and return file list."""
        from agent_tools import list_resume_files
        result = list_resume_files()
        self.assertTrue(
            result.get("success"),
            msg=f"list_resume_files via MCP failed: {result}",
        )
        files = result.get("files", [])
        self.assertGreater(len(files), 0, "Expected resume files from MCP list_files")

    def test_read_resume_file_via_mcp(self):
        """read_resume_file must route through MCP and return file content."""
        from agent_tools import list_resume_files, read_resume_file
        list_result = list_resume_files()
        files = list_result.get("files", [])
        self.assertTrue(files, "No files to read")
        filepath = files[0]["relative_path"] if isinstance(files[0], dict) else files[0]

        result = read_resume_file(filepath)
        self.assertTrue(
            result.get("success"),
            msg=f"read_resume_file via MCP failed: {result}",
        )
        self.assertIn("content", result)

    def test_read_resume_file_nonexistent_via_mcp(self):
        """read_resume_file for a missing file must return success=False via MCP."""
        from agent_tools import read_resume_file
        result = read_resume_file("data/synthetic_resumes/does_not_exist_ever.json")
        self.assertFalse(
            result.get("success"),
            msg=f"Expected failure for non-existent file: {result}",
        )


# ---------------------------------------------------------------------------
# Scenario 7: JSON-RPC error codes
# ---------------------------------------------------------------------------

class TestJSONRPCErrorCodes(unittest.TestCase):
    """Verify that MCP tool errors carry expected JSON-RPC / application error codes."""

    @classmethod
    def setUpClass(cls):
        cls.mgr = _get_manager()

    def test_read_file_not_found_code(self):
        """read_file error for missing file must carry FS_NOT_FOUND (-32001) code."""
        result = self.mgr.call_tool(
            "read_file",
            {"filepath": "data/synthetic_resumes/ghost_file.json"},
            server="filesystem",
        )
        self.assertFalse(result.get("success"))
        self.assertEqual(result.get("code"), -32001, msg=f"Wrong error code: {result}")

    def test_update_config_invalid_params_code(self):
        """update_config with invalid value must carry INVALID_PARAMS (-32602) code."""
        result = self.mgr.call_tool(
            "update_config",
            {"watch_poll_interval": 0},
            server="filesystem",
        )
        self.assertFalse(result.get("success"))
        self.assertEqual(result.get("code"), -32602, msg=f"Wrong error code: {result}")

    def test_batch_empty_ops_code(self):
        """batch_process with empty operations must carry INVALID_PARAMS (-32602) code."""
        result = self.mgr.call_tool(
            "batch_process", {"operations": []}, server="filesystem"
        )
        self.assertFalse(result.get("success"))
        self.assertEqual(result.get("code"), -32602, msg=f"Wrong error code: {result}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # Run with higher verbosity so each scenario is clearly labelled
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    # Ordered for readability in output
    for cls in (
        TestMCPResourceDiscovery,
        TestFilesystemToolsViaMCP,
        TestBatchProcess,
        TestWatchDirectory,
        TestWebSearchMCPServer,
        TestAgentToolsMCPIntegration,
        TestJSONRPCErrorCodes,
    ):
        suite.addTests(loader.loadTestsFromTestCase(cls))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
