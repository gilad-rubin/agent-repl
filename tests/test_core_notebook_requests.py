from __future__ import annotations

import unittest

from agent_repl.core.notebook_requests import (
    NotebookActivityRequest,
    NotebookCreateRequest,
    NotebookExecuteVisibleCellRequest,
    NotebookInsertExecuteRequest,
    NotebookLeaseAcquireRequest,
    NotebookSessionPathRequest,
)


class TestNotebookRequests(unittest.TestCase):
    def test_create_request_from_payload_filters_non_dict_cells(self):
        request = NotebookCreateRequest.from_payload(
            {
                "path": "nb.ipynb",
                "cells": [{"cell_type": "code"}, "bad", 3],
                "kernel_id": "python3",
            }
        )

        self.assertEqual(request.path, "nb.ipynb")
        self.assertEqual(request.cells, [{"cell_type": "code"}])
        self.assertEqual(request.kernel_id, "python3")
        self.assertEqual(
            request.to_payload(),
            {"path": "nb.ipynb", "cells": [{"cell_type": "code"}], "kernel_id": "python3"},
        )

    def test_insert_execute_request_defaults_optional_fields(self):
        request = NotebookInsertExecuteRequest.from_payload({"path": "nb.ipynb", "source": "x = 1"})

        self.assertEqual(request.cell_type, "code")
        self.assertEqual(request.at_index, -1)
        self.assertEqual(
            request.to_payload(),
            {"path": "nb.ipynb", "source": "x = 1", "cell_type": "code", "at_index": -1},
        )

    def test_execute_visible_cell_requires_cell_index_and_source(self):
        with self.assertRaisesRegex(ValueError, "Missing cell_index"):
            NotebookExecuteVisibleCellRequest.from_payload({"path": "nb.ipynb", "source": "x = 1"})

        with self.assertRaisesRegex(ValueError, "Missing source"):
            NotebookExecuteVisibleCellRequest.from_payload({"path": "nb.ipynb", "cell_index": 1})

    def test_session_path_request_round_trips_owner_session(self):
        request = NotebookSessionPathRequest.from_payload({"path": "nb.ipynb", "owner_session_id": "sess-1"})

        self.assertEqual(request.owner_session_id, "sess-1")
        self.assertEqual(request.to_payload(), {"path": "nb.ipynb", "owner_session_id": "sess-1"})

    def test_activity_and_lease_requests_preserve_numeric_fields(self):
        activity = NotebookActivityRequest.from_payload({"path": "nb.ipynb", "since": 12})
        lease = NotebookLeaseAcquireRequest.from_payload(
            {"path": "nb.ipynb", "session_id": "sess-1", "cell_index": 2, "ttl_seconds": 30}
        )

        self.assertEqual(activity.since, 12.0)
        self.assertEqual(lease.cell_index, 2)
        self.assertEqual(lease.ttl_seconds, 30.0)
        self.assertEqual(
            lease.to_payload(),
            {
                "path": "nb.ipynb",
                "session_id": "sess-1",
                "kind": "edit",
                "cell_index": 2,
                "ttl_seconds": 30.0,
            },
        )
