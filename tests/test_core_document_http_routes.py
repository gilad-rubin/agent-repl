from __future__ import annotations

import unittest
from http import HTTPStatus
from unittest import mock

from agent_repl.core.document_http_routes import handle_document_get, handle_document_post


class TestDocumentHttpRoutes(unittest.TestCase):
    def test_returns_none_for_unknown_path(self):
        self.assertIsNone(handle_document_get(mock.Mock(), "/api/nope"))
        self.assertIsNone(handle_document_post(mock.Mock(), "/api/nope", {}))

    def test_dispatches_documents_get_route(self):
        state = mock.Mock()
        state.list_documents_payload.return_value = {"status": "ok", "documents": []}

        status, body = handle_document_get(state, "/api/documents")

        self.assertEqual(status, HTTPStatus.OK)
        self.assertEqual(body["status"], "ok")
        state.list_documents_payload.assert_called_once_with()

    def test_validates_missing_path_for_open(self):
        status, body = handle_document_post(mock.Mock(), "/api/documents/open", {})

        self.assertEqual(status, HTTPStatus.BAD_REQUEST)
        self.assertEqual(body, {"error": "Missing path"})

    def test_dispatches_rebind_route(self):
        state = mock.Mock()
        state.rebind_document.return_value = ({"status": "ok"}, HTTPStatus.OK)

        status, body = handle_document_post(
            state,
            "/api/documents/rebind",
            {"document_id": "doc-1"},
        )

        self.assertEqual(status, HTTPStatus.OK)
        self.assertEqual(body, {"status": "ok"})
        state.rebind_document.assert_called_once_with("doc-1")
