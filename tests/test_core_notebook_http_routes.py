from __future__ import annotations

import unittest
from http import HTTPStatus
from unittest import mock

from starlette.applications import Starlette
from starlette.testclient import TestClient

from agent_repl.core.notebook_http_routes import routes


def _make_client(state: mock.MagicMock) -> TestClient:
    app = Starlette(routes=routes(state))
    return TestClient(app, raise_server_exceptions=False)


class TestNotebookHttpRoutes(unittest.TestCase):
    def test_unknown_route_returns_404(self):
        client = _make_client(mock.MagicMock())
        resp = client.post("/api/notebooks/nope", json={})
        self.assertEqual(resp.status_code, 404)

    def test_validates_missing_path_with_shared_request_contract(self):
        client = _make_client(mock.MagicMock())

        resp = client.post("/api/notebooks/contents", json={})

        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json(), {"error": "Missing path"})

    def test_dispatches_execute_visible_cell_route(self):
        state = mock.MagicMock()
        state.notebook_execute_visible_cell.return_value = ({"status": "ok"}, HTTPStatus.OK)
        client = _make_client(state)

        resp = client.post(
            "/api/notebooks/execute-visible-cell",
            json={"path": "nb.ipynb", "cell_index": 2, "source": "x = 1", "owner_session_id": "sess-1"},
        )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"status": "ok"})
        state.notebook_execute_visible_cell.assert_called_once_with(
            "nb.ipynb",
            cell_index=2,
            source="x = 1",
            owner_session_id="sess-1",
        )

    def test_execute_cell_wait_false_uses_server_queue(self):
        state = mock.MagicMock()
        state.notebook_enqueue_execute_cell.return_value = ({"status": "queued"}, HTTPStatus.OK)
        client = _make_client(state)

        resp = client.post(
            "/api/notebooks/execute-cell",
            json={"path": "nb.ipynb", "cell_id": "cell-1", "owner_session_id": "sess-1", "wait": False},
        )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"status": "queued"})
        state.notebook_enqueue_execute_cell.assert_called_once_with(
            "nb.ipynb",
            cell_id="cell-1",
            cell_index=None,
            owner_session_id="sess-1",
        )
        state.notebook_execute_cell.assert_not_called()
