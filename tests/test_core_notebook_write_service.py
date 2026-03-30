from __future__ import annotations

import unittest
from contextlib import nullcontext
from http import HTTPStatus
from types import SimpleNamespace
from unittest import mock

from agent_repl.core.collaboration import CollaborationConflictError
from agent_repl.core.notebook_write_service import NotebookWriteService


class TestNotebookWriteService(unittest.TestCase):
    def test_edit_returns_conflict_payload_without_syncing_document(self):
        state = mock.Mock()
        state._resolve_document_path.return_value = ("/tmp/nb.ipynb", "nb.ipynb")
        state._notebook_lock.return_value = nullcontext()
        state._headless_notebook_edit.side_effect = CollaborationConflictError(
            "lease-conflict",
            payload={"error": "lease-conflict", "owner_session_id": "sess-1"},
        )

        body, status = NotebookWriteService(state).edit(
            "nb.ipynb",
            [{"op": "replace-source", "cell_id": "cell-1", "source": "x = 2"}],
            owner_session_id="sess-2",
        )

        self.assertEqual(status, HTTPStatus.CONFLICT)
        self.assertEqual(body["owner_session_id"], "sess-1")
        state._sync_document_record.assert_not_called()

    def test_execute_visible_cell_ensures_runtime_syncs_and_persists(self):
        state = mock.Mock()
        state._resolve_document_path.return_value = ("/tmp/nb.ipynb", "nb.ipynb")
        state._notebook_lock.return_value = nullcontext()
        state.headless_runtimes = {}
        runtime = SimpleNamespace(runtime_id="runtime-1")
        state._ensure_headless_runtime.return_value = runtime
        state._headless_notebook_execute_visible_cell.return_value = {"status": "ok", "execution_id": "exec-1"}

        body, status = NotebookWriteService(state).execute_visible_cell(
            "nb.ipynb",
            cell_index=3,
            source="x = 1\nx",
            owner_session_id="sess-1",
        )

        self.assertEqual(status, HTTPStatus.OK)
        self.assertEqual(body["execution_id"], "exec-1")
        state._ensure_headless_runtime.assert_called_once_with("/tmp/nb.ipynb")
        state._sync_document_record.assert_called_once_with("/tmp/nb.ipynb", "nb.ipynb")
        state._sync_headless_runtime_record.assert_called_once_with(relative_path="nb.ipynb", runtime=runtime)
        state.persist.assert_called_once_with()

    def test_interrupt_records_activity_for_running_execution(self):
        state = mock.Mock()
        manager = mock.Mock()
        runtime = SimpleNamespace(
            current_execution={"cell_id": "cell-1", "cell_index": 0},
            manager=manager,
            runtime_id="runtime-1",
        )
        state._resolve_document_path.return_value = ("/tmp/nb.ipynb", "nb.ipynb")
        state.headless_runtimes = {"/tmp/nb.ipynb": runtime}

        body, status = NotebookWriteService(state).interrupt("nb.ipynb")

        self.assertEqual(status, HTTPStatus.OK)
        self.assertTrue(body["interrupted"])
        manager.interrupt_kernel.assert_called_once_with()
        state._append_activity_event.assert_called_once_with(
            path="nb.ipynb",
            event_type="execution-interrupt-requested",
            detail="Interrupt requested for current execution",
            actor="agent",
            session_id=None,
            runtime_id="runtime-1",
            cell_id="cell-1",
            cell_index=0,
        )
        state.persist.assert_called_once_with()
