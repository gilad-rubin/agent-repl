from __future__ import annotations

import unittest
from http import HTTPStatus
from unittest import mock

from starlette.applications import Starlette
from starlette.testclient import TestClient

from agent_repl.core.document_http_routes import routes


def _make_client(state: mock.MagicMock) -> TestClient:
    app = Starlette(routes=routes(state))
    return TestClient(app, raise_server_exceptions=False)


class TestDocumentHttpRoutes(unittest.TestCase):
    def test_unknown_route_returns_404(self):
        client = _make_client(mock.MagicMock())
        resp = client.get("/api/nope")
        self.assertEqual(resp.status_code, 404)

    def test_dispatches_documents_get_route(self):
        state = mock.MagicMock()
        state.list_documents_payload.return_value = {"status": "ok", "documents": []}
        client = _make_client(state)

        resp = client.get("/api/documents")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "ok")
        state.list_documents_payload.assert_called_once_with()

    def test_validates_missing_path_for_open(self):
        client = _make_client(mock.MagicMock())

        resp = client.post("/api/documents/open", json={})

        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json(), {"error": "Missing path"})

    def test_dispatches_rebind_route(self):
        state = mock.MagicMock()
        state.rebind_document.return_value = ({"status": "ok"}, HTTPStatus.OK)
        client = _make_client(state)

        resp = client.post("/api/documents/rebind", json={"document_id": "doc-1"})

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"status": "ok"})
        state.rebind_document.assert_called_once_with("doc-1")
