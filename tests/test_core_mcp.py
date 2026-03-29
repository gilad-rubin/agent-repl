"""Tests for the FastMCP adapter."""
from __future__ import annotations

import asyncio
import unittest
from unittest.mock import MagicMock

from agent_repl.core.mcp_adapter import create_mcp_server


def _mock_state() -> MagicMock:
    state = MagicMock()
    state.token = "test"
    state.pid = 1
    return state


def _run(coro):
    return asyncio.run(coro)


class TestMcpToolRegistration(unittest.TestCase):
    def test_mcp_server_has_notebook_tools(self):
        mcp = create_mcp_server(_mock_state())
        tools = _run(mcp.list_tools())
        tool_names = {t.name for t in tools}
        expected = {
            "notebook_create",
            "notebook_contents",
            "notebook_edit",
            "notebook_execute_cell",
            "notebook_execute_all",
            "notebook_restart",
            "notebook_restart_and_run_all",
            "notebook_status",
            "notebook_activity",
            "notebook_runtime",
            "list_runtimes",
            "open_document",
            "list_documents",
            "list_sessions",
        }
        self.assertTrue(expected.issubset(tool_names), f"Missing tools: {expected - tool_names}")

    def test_mcp_server_has_status_resource(self):
        mcp = create_mcp_server(_mock_state())
        resources = _run(mcp.list_resources())
        resource_uris = {str(r.uri) for r in resources}
        self.assertIn("agent-repl://status", resource_uris)


class TestMcpToolDelegation(unittest.TestCase):
    def test_notebook_contents_delegates_to_state(self):
        state = _mock_state()
        state.notebook_contents.return_value = ({"cells": []}, 200)
        mcp = create_mcp_server(state)
        result = _run(mcp.call_tool("notebook_contents", {"path": "demo.ipynb"}))
        state.notebook_contents.assert_called_once_with("demo.ipynb")
        self.assertIn("cells", result.structured_content)

    def test_notebook_execute_cell_delegates_to_state(self):
        state = _mock_state()
        state.notebook_execute_cell.return_value = ({"status": "ok"}, 200)
        mcp = create_mcp_server(state)
        result = _run(mcp.call_tool("notebook_execute_cell", {
            "path": "demo.ipynb",
            "cell_id": "cell-1",
        }))
        state.notebook_execute_cell.assert_called_once_with(
            "demo.ipynb",
            cell_id="cell-1",
            cell_index=None,
            owner_session_id=None,
        )


if __name__ == "__main__":
    unittest.main()
