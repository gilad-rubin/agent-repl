from __future__ import annotations

import time
import unittest
from contextlib import nullcontext
from http import HTTPStatus
from types import SimpleNamespace
from unittest import mock

import nbformat

from agent_repl.core.collaboration_service import CollaborationService
from agent_repl.core.server import CellLeaseRecord, NotebookPresenceRecord


class TestCollaborationService(unittest.TestCase):
    def test_resolve_preferred_session_prefers_attached_editor_capable_human(self):
        browser = SimpleNamespace(
            actor="human",
            client="browser",
            capabilities=["projection", "presence"],
            status="attached",
            last_seen_at=10.0,
            created_at=5.0,
            payload=lambda: {"session_id": "sess-browser"},
        )
        vscode = SimpleNamespace(
            actor="human",
            client="vscode",
            capabilities=["projection", "editor", "presence"],
            status="attached",
            last_seen_at=9.0,
            created_at=4.0,
            payload=lambda: {"session_id": "sess-vscode"},
        )
        state = mock.Mock()
        state.workspace_root = "/tmp/workspace"
        state.session_records = {"sess-browser": browser, "sess-vscode": vscode}

        payload = CollaborationService(
            state,
            cell_lease_record_type=CellLeaseRecord,
            notebook_presence_record_type=NotebookPresenceRecord,
        ).resolve_preferred_session("human")

        self.assertEqual(payload["session"]["session_id"], "sess-vscode")
        state._refresh_session_liveness.assert_called_once_with()

    def test_acquire_cell_lease_returns_conflict_payload(self):
        now = time.time()
        notebook = nbformat.v4.new_notebook(cells=[nbformat.v4.new_code_cell("x = 1")])
        state = mock.Mock()
        state.workspace_root = "/tmp/workspace"
        state._resolve_document_path.return_value = ("/tmp/workspace/nb.ipynb", "nb.ipynb")
        state._notebook_lock.return_value = nullcontext()
        state._load_notebook.return_value = (notebook, False)
        state._find_cell_index.return_value = 0
        state._cell_id.return_value = "cell-1"
        state._lease_key.side_effect = lambda path, cell_id: f"{path}::{cell_id}"
        state._append_activity_event = mock.Mock()
        state.persist = mock.Mock()
        state.session_records = {
            "sess-owner": SimpleNamespace(payload=lambda: {"session_id": "sess-owner"}, actor="human"),
            "sess-requester": SimpleNamespace(payload=lambda: {"session_id": "sess-requester"}, actor="agent"),
        }
        state.document_records = {}
        state.cell_leases = {
            "nb.ipynb::cell-1": CellLeaseRecord(
                lease_id="lease-1",
                session_id="sess-owner",
                path="nb.ipynb",
                cell_id="cell-1",
                kind="edit",
                created_at=now,
                updated_at=now,
                expires_at=now + 60,
            )
        }
        state._lock = mock.MagicMock()
        state._lock.__enter__.return_value = None
        state._lock.__exit__.return_value = None

        body, status = CollaborationService(
            state,
            cell_lease_record_type=CellLeaseRecord,
            notebook_presence_record_type=NotebookPresenceRecord,
        ).acquire_cell_lease(session_id="sess-requester", path="nb.ipynb", cell_index=0)

        self.assertEqual(status, HTTPStatus.CONFLICT)
        self.assertEqual(body["conflict"]["lease"]["session_id"], "sess-owner")
        state.persist.assert_not_called()

    def test_upsert_notebook_presence_creates_record_and_emits_activity(self):
        state = mock.Mock()
        state.workspace_root = "/tmp/workspace"
        state._resolve_document_path.return_value = ("/tmp/workspace/nb.ipynb", "nb.ipynb")
        state.session_records = {
            "sess-1": SimpleNamespace(payload=lambda: {"session_id": "sess-1"}, actor="human"),
        }
        state.notebook_presence = {}
        state._append_activity_event = mock.Mock()
        state.persist = mock.Mock()
        state._lock = mock.MagicMock()
        state._lock.__enter__.return_value = None
        state._lock.__exit__.return_value = None

        body, status = CollaborationService(
            state,
            cell_lease_record_type=CellLeaseRecord,
            notebook_presence_record_type=NotebookPresenceRecord,
        ).upsert_notebook_presence(
            session_id="sess-1",
            path="nb.ipynb",
            activity="observing",
            cell_index=2,
        )

        self.assertEqual(status, HTTPStatus.OK)
        self.assertEqual(body["presence"]["session_id"], "sess-1")
        self.assertEqual(body["presence"]["activity"], "observing")
        state._append_activity_event.assert_called_once()
        state.persist.assert_called_once_with()
