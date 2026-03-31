"""Tests for the FastMCP adapter — 6 bundled tools."""
from __future__ import annotations

import asyncio
import unittest
from http import HTTPStatus
from unittest.mock import MagicMock

from agent_repl.core.mcp_adapter import create_mcp_server


def _mock_state() -> MagicMock:
    state = MagicMock()
    state.token = "test"
    state.pid = 1
    return state


def _run(coro):
    return asyncio.run(coro)


# The 6 bundle tools that constitute the v1 MCP surface.
EXPECTED_TOOLS = {
    "notebook_observe",
    "notebook_edit",
    "notebook_execute",
    "notebook_runtime",
    "workspace_files",
    "checkpoint",
}


class TestMcpToolRegistration(unittest.TestCase):
    def test_exactly_six_bundle_tools(self):
        mcp = create_mcp_server(_mock_state())
        tools = _run(mcp.list_tools())
        tool_names = {t.name for t in tools}
        missing = EXPECTED_TOOLS - tool_names
        extra = tool_names - EXPECTED_TOOLS
        self.assertFalse(missing, f"Missing MCP tools: {missing}")
        self.assertFalse(extra, f"Unexpected MCP tools: {extra}")

    def test_mcp_server_has_status_resource(self):
        mcp = create_mcp_server(_mock_state())
        resources = _run(mcp.list_resources())
        resource_uris = {str(r.uri) for r in resources}
        self.assertIn("agent-repl://status", resource_uris)

    def test_status_resource_returns_json(self):
        state = _mock_state()
        state.status_payload.return_value = {"status": "ok", "workspace_root": "/tmp/demo"}
        mcp = create_mcp_server(state)
        result = _run(mcp.read_resource("agent-repl://status"))
        self.assertEqual(result.contents[0].mime_type, "application/json")
        self.assertIn('"workspace_root": "/tmp/demo"', result.contents[0].content)


class TestNotebookObserve(unittest.TestCase):
    def setUp(self):
        self.state = _mock_state()
        self.mcp = create_mcp_server(self.state)

    def test_cells_aspect(self):
        self.state.notebook_contents.return_value = ({"cells": [{"id": "c1"}]}, HTTPStatus.OK)
        result = _run(self.mcp.call_tool("notebook_observe", {"path": "nb.ipynb", "aspect": "cells"}))
        self.state.notebook_contents.assert_called_once_with("nb.ipynb")
        self.assertIn("cells", result.structured_content)

    def test_summary_aspect(self):
        self.state.notebook_status.return_value = ({"status": "idle"}, HTTPStatus.OK)
        result = _run(self.mcp.call_tool("notebook_observe", {"path": "nb.ipynb", "aspect": "summary"}))
        self.state.notebook_status.assert_called_once_with("nb.ipynb")

    def test_queue_aspect(self):
        self.state.notebook_runtime.return_value = ({"runtime": "idle"}, HTTPStatus.OK)
        result = _run(self.mcp.call_tool("notebook_observe", {"path": "nb.ipynb", "aspect": "queue"}))
        self.state.notebook_runtime.assert_called_once_with("nb.ipynb")

    def test_activity_aspect(self):
        self.state.notebook_activity.return_value = ({"events": []}, HTTPStatus.OK)
        result = _run(self.mcp.call_tool("notebook_observe", {"path": "nb.ipynb", "aspect": "activity", "since": 100.0}))
        self.state.notebook_activity.assert_called_once_with("nb.ipynb", since=100.0)

    def test_projection_aspect(self):
        self.state.notebook_projection.return_value = ({"cells": []}, HTTPStatus.OK)
        result = _run(self.mcp.call_tool("notebook_observe", {"path": "nb.ipynb", "aspect": "projection"}))
        self.state.notebook_projection.assert_called_once_with("nb.ipynb")

    def test_search_aspect(self):
        self.state.notebook_contents.return_value = (
            {"cells": [
                {"id": "c1", "source": "import pandas"},
                {"id": "c2", "source": "print('hello')"},
            ]},
            HTTPStatus.OK,
        )
        result = _run(self.mcp.call_tool("notebook_observe", {"path": "nb.ipynb", "aspect": "search", "query": "pandas"}))
        matches = result.structured_content["matches"]
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["cell_id"], "c1")

    def test_default_aspect_is_cells(self):
        self.state.notebook_contents.return_value = ({"cells": []}, HTTPStatus.OK)
        _run(self.mcp.call_tool("notebook_observe", {"path": "nb.ipynb"}))
        self.state.notebook_contents.assert_called_once()


class TestNotebookEdit(unittest.TestCase):
    def setUp(self):
        self.state = _mock_state()
        self.mcp = create_mcp_server(self.state)

    def test_edit_operations(self):
        ops = [{"op": "insert", "source": "x = 1", "cell_type": "code"}]
        self.state.notebook_edit.return_value = ({"status": "ok"}, HTTPStatus.OK)
        result = _run(self.mcp.call_tool("notebook_edit", {"path": "nb.ipynb", "operations": ops}))
        self.state.notebook_edit.assert_called_once_with("nb.ipynb", ops, owner_session_id=None)

    def test_create_action(self):
        self.state.notebook_create.return_value = ({"path": "new.ipynb"}, HTTPStatus.OK)
        result = _run(self.mcp.call_tool("notebook_edit", {"path": "new.ipynb", "action": "create"}))
        self.state.notebook_create.assert_called_once_with("new.ipynb", cells=None, kernel_id=None)

    def test_edit_missing_operations_returns_error(self):
        result = _run(self.mcp.call_tool("notebook_edit", {"path": "nb.ipynb"}))
        self.assertIn("error", result.structured_content)


class TestNotebookExecute(unittest.TestCase):
    def setUp(self):
        self.state = _mock_state()
        self.mcp = create_mcp_server(self.state)

    def test_execute_cell(self):
        self.state.notebook_execute_cell.return_value = ({"status": "ok"}, HTTPStatus.OK)
        _run(self.mcp.call_tool("notebook_execute", {"path": "nb.ipynb", "cell_id": "c1"}))
        self.state.notebook_execute_cell.assert_called_once_with(
            "nb.ipynb", cell_id="c1", cell_index=None, owner_session_id=None,
        )

    def test_execute_all(self):
        self.state.notebook_execute_all.return_value = ({"status": "ok"}, HTTPStatus.OK)
        _run(self.mcp.call_tool("notebook_execute", {"path": "nb.ipynb", "action": "all"}))
        self.state.notebook_execute_all.assert_called_once()

    def test_insert_and_execute(self):
        self.state.notebook_insert_execute.return_value = ({"status": "ok"}, HTTPStatus.OK)
        _run(self.mcp.call_tool("notebook_execute", {
            "path": "nb.ipynb", "action": "insert-and-execute", "source": "1+1",
        }))
        self.state.notebook_insert_execute.assert_called_once()

    def test_insert_and_execute_missing_source(self):
        result = _run(self.mcp.call_tool("notebook_execute", {
            "path": "nb.ipynb", "action": "insert-and-execute",
        }))
        self.assertIn("error", result.structured_content)

    def test_interrupt(self):
        self.state.notebook_interrupt.return_value = ({"status": "ok"}, HTTPStatus.OK)
        _run(self.mcp.call_tool("notebook_execute", {"path": "nb.ipynb", "action": "interrupt"}))
        self.state.notebook_interrupt.assert_called_once_with("nb.ipynb")

    def test_restart(self):
        self.state.notebook_restart.return_value = ({"status": "ok"}, HTTPStatus.OK)
        _run(self.mcp.call_tool("notebook_execute", {"path": "nb.ipynb", "action": "restart"}))
        self.state.notebook_restart.assert_called_once_with("nb.ipynb")

    def test_restart_and_run_all(self):
        self.state.notebook_restart_and_run_all.return_value = ({"status": "ok"}, HTTPStatus.OK)
        _run(self.mcp.call_tool("notebook_execute", {"path": "nb.ipynb", "action": "restart-and-run-all"}))
        self.state.notebook_restart_and_run_all.assert_called_once()


class TestNotebookRuntime(unittest.TestCase):
    def setUp(self):
        self.state = _mock_state()
        self.mcp = create_mcp_server(self.state)

    def test_select_kernel(self):
        self.state.notebook_select_kernel.return_value = ({"kernel_id": "python3"}, HTTPStatus.OK)
        _run(self.mcp.call_tool("notebook_runtime", {
            "action": "select-kernel", "path": "nb.ipynb", "kernel_id": "python3",
        }))
        self.state.notebook_select_kernel.assert_called_once_with("nb.ipynb", kernel_id="python3")

    def test_status(self):
        self.state.notebook_runtime.return_value = ({"status": "idle"}, HTTPStatus.OK)
        _run(self.mcp.call_tool("notebook_runtime", {"action": "status", "path": "nb.ipynb"}))
        self.state.notebook_runtime.assert_called_once_with("nb.ipynb")

    def test_list_runtimes(self):
        self.state.list_runtimes_payload.return_value = {"runtimes": []}
        _run(self.mcp.call_tool("notebook_runtime", {"action": "list-runtimes"}))
        self.state.list_runtimes_payload.assert_called_once()

    def test_start_runtime(self):
        self.state.start_runtime.return_value = {"runtime_id": "r1"}
        _run(self.mcp.call_tool("notebook_runtime", {
            "action": "start", "runtime_id": "r1", "mode": "interactive",
        }))
        self.state.start_runtime.assert_called_once()

    def test_stop_runtime(self):
        self.state.stop_runtime.return_value = ({"status": "ok"}, HTTPStatus.OK)
        _run(self.mcp.call_tool("notebook_runtime", {"action": "stop", "runtime_id": "r1"}))
        self.state.stop_runtime.assert_called_once_with("r1")

    def test_recover_runtime(self):
        self.state.recover_runtime.return_value = ({"status": "ok"}, HTTPStatus.OK)
        _run(self.mcp.call_tool("notebook_runtime", {"action": "recover", "runtime_id": "r1"}))
        self.state.recover_runtime.assert_called_once_with("r1")

    def test_missing_path_returns_error(self):
        result = _run(self.mcp.call_tool("notebook_runtime", {"action": "status"}))
        self.assertIn("error", result.structured_content)


class TestWorkspaceFiles(unittest.TestCase):
    def setUp(self):
        self.state = _mock_state()
        self.mcp = create_mcp_server(self.state)

    def test_list_documents(self):
        self.state.list_documents_payload.return_value = {"documents": []}
        _run(self.mcp.call_tool("workspace_files", {"action": "list"}))
        self.state.list_documents_payload.assert_called_once()

    def test_open_document(self):
        self.state.open_document.return_value = ({"path": "nb.ipynb"}, HTTPStatus.OK)
        _run(self.mcp.call_tool("workspace_files", {"action": "open", "path": "nb.ipynb"}))
        self.state.open_document.assert_called_once_with("nb.ipynb")

    def test_open_missing_path(self):
        result = _run(self.mcp.call_tool("workspace_files", {"action": "open"}))
        self.assertIn("error", result.structured_content)


class TestCheckpoint(unittest.TestCase):
    def setUp(self):
        self.state = _mock_state()
        self.mcp = create_mcp_server(self.state)

    def test_create(self):
        self.state.checkpoint_create.return_value = ({"checkpoint_id": "cp-1"}, HTTPStatus.OK)
        _run(self.mcp.call_tool("checkpoint", {
            "action": "create", "path": "nb.ipynb", "label": "v1",
        }))
        self.state.checkpoint_create.assert_called_once_with("nb.ipynb", label="v1", session_id=None)

    def test_restore(self):
        self.state.checkpoint_restore.return_value = ({"restored": True}, HTTPStatus.OK)
        _run(self.mcp.call_tool("checkpoint", {"action": "restore", "checkpoint_id": "cp-1"}))
        self.state.checkpoint_restore.assert_called_once_with("cp-1")

    def test_list(self):
        self.state.checkpoint_list.return_value = ({"checkpoints": []}, HTTPStatus.OK)
        _run(self.mcp.call_tool("checkpoint", {"action": "list", "path": "nb.ipynb"}))
        self.state.checkpoint_list.assert_called_once_with("nb.ipynb")

    def test_delete(self):
        self.state.checkpoint_delete.return_value = ({"deleted": True}, HTTPStatus.OK)
        _run(self.mcp.call_tool("checkpoint", {"action": "delete", "checkpoint_id": "cp-1"}))
        self.state.checkpoint_delete.assert_called_once_with("cp-1")

    def test_create_missing_path(self):
        result = _run(self.mcp.call_tool("checkpoint", {"action": "create"}))
        self.assertIn("error", result.structured_content)

    def test_restore_missing_id(self):
        result = _run(self.mcp.call_tool("checkpoint", {"action": "restore"}))
        self.assertIn("error", result.structured_content)


if __name__ == "__main__":
    unittest.main()
