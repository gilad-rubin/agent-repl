from __future__ import annotations

import unittest
from http import HTTPStatus
from unittest import mock

from agent_repl.core.notebook_http_routes import handle_notebook_post


class TestNotebookHttpRoutes(unittest.TestCase):
    def test_returns_none_for_unknown_path(self):
        self.assertIsNone(handle_notebook_post(mock.Mock(), "/api/notebooks/nope", {}))

    def test_validates_missing_path_with_shared_request_contract(self):
        status, body = handle_notebook_post(mock.Mock(), "/api/notebooks/contents", {})

        self.assertEqual(status, HTTPStatus.BAD_REQUEST)
        self.assertEqual(body, {"error": "Missing path"})

    def test_dispatches_execute_visible_cell_route(self):
        state = mock.Mock()
        state.notebook_execute_visible_cell.return_value = ({"status": "ok"}, HTTPStatus.OK)

        status, body = handle_notebook_post(
            state,
            "/api/notebooks/execute-visible-cell",
            {"path": "nb.ipynb", "cell_index": 2, "source": "x = 1", "owner_session_id": "sess-1"},
        )

        self.assertEqual(status, HTTPStatus.OK)
        self.assertEqual(body, {"status": "ok"})
        state.notebook_execute_visible_cell.assert_called_once_with(
            "nb.ipynb",
            cell_index=2,
            source="x = 1",
            owner_session_id="sess-1",
        )
