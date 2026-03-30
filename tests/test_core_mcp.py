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


# The full set of MCP tools that must exist for HTTP endpoint parity.
EXPECTED_TOOLS = {
    # Notebook
    "notebook_create",
    "notebook_contents",
    "notebook_edit",
    "notebook_execute_cell",
    "notebook_execute_all",
    "notebook_restart",
    "notebook_restart_and_run_all",
    "notebook_status",
    "notebook_activity",
    "notebook_interrupt",
    "notebook_select_kernel",
    "notebook_projection",
    "notebook_insert_execute",
    "notebook_execution_lookup",
    "notebook_project_visible",
    "notebook_execute_visible_cell",
    "notebook_runtime",
    # Runtime lifecycle
    "list_runtimes",
    "start_runtime",
    "stop_runtime",
    "recover_runtime",
    "promote_runtime",
    "discard_runtime",
    # Runs
    "list_runs",
    "start_run",
    "finish_run",
    # Documents
    "open_document",
    "list_documents",
    # Sessions
    "list_sessions",
    "start_session",
    "resolve_preferred_session",
    "touch_session",
    "detach_session",
    "end_session",
    # Presence
    "upsert_presence",
    "clear_presence",
    # Branches
    "start_branch",
    "finish_branch",
    "request_branch_review",
    "resolve_branch_review",
    # Leases
    "acquire_cell_lease",
    "release_cell_lease",
}


class TestMcpToolRegistration(unittest.TestCase):
    def test_full_tool_parity(self):
        """All HTTP endpoints have a corresponding MCP tool."""
        mcp = create_mcp_server(_mock_state())
        tools = _run(mcp.list_tools())
        tool_names = {t.name for t in tools}
        missing = EXPECTED_TOOLS - tool_names
        extra = tool_names - EXPECTED_TOOLS
        self.assertFalse(missing, f"Missing MCP tools: {missing}")
        self.assertFalse(extra, f"Unexpected MCP tools (update EXPECTED_TOOLS if intentional): {extra}")

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

    def test_notebook_interrupt_delegates_to_state(self):
        state = _mock_state()
        state.notebook_interrupt.return_value = ({"status": "ok"}, 200)
        mcp = create_mcp_server(state)
        result = _run(mcp.call_tool("notebook_interrupt", {"path": "demo.ipynb"}))
        state.notebook_interrupt.assert_called_once_with("demo.ipynb")
        self.assertEqual(result.structured_content["status"], "ok")

    def test_notebook_select_kernel_delegates_to_state(self):
        state = _mock_state()
        state.notebook_select_kernel.return_value = ({"status": "ok", "kernel_id": "python3"}, 200)
        mcp = create_mcp_server(state)
        result = _run(mcp.call_tool("notebook_select_kernel", {
            "path": "demo.ipynb",
            "kernel_id": "python3",
        }))
        state.notebook_select_kernel.assert_called_once_with("demo.ipynb", kernel_id="python3")
        self.assertEqual(result.structured_content["kernel_id"], "python3")

    def test_notebook_projection_delegates_to_state(self):
        state = _mock_state()
        state.notebook_projection.return_value = ({"cells": [{"id": "c1"}], "status": "ok"}, 200)
        mcp = create_mcp_server(state)
        result = _run(mcp.call_tool("notebook_projection", {"path": "demo.ipynb"}))
        state.notebook_projection.assert_called_once_with("demo.ipynb")
        self.assertEqual(result.structured_content["cells"], [{"id": "c1"}])


if __name__ == "__main__":
    unittest.main()
