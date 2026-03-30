from __future__ import annotations

import unittest
from http import HTTPStatus
from unittest import mock

from starlette.applications import Starlette
from starlette.testclient import TestClient

from agent_repl.core.collaboration_http_routes import routes


def _make_client(state: mock.MagicMock) -> TestClient:
    app = Starlette(routes=routes(state))
    return TestClient(app, raise_server_exceptions=False)


class TestCollaborationHttpRoutes(unittest.TestCase):
    def test_unknown_route_returns_404(self):
        client = _make_client(mock.MagicMock())
        resp = client.get("/api/nope")
        self.assertEqual(resp.status_code, 404)

    def test_dispatches_sessions_get_route(self):
        state = mock.MagicMock()
        state.list_sessions_payload.return_value = {"status": "ok", "sessions": []}
        client = _make_client(state)

        resp = client.get("/api/sessions")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "ok")
        state.list_sessions_payload.assert_called_once_with()

    def test_validates_missing_session_id_for_presence_clear(self):
        client = _make_client(mock.MagicMock())

        resp = client.post("/api/sessions/presence/clear", json={})

        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json(), {"error": "Missing session_id"})

    def test_dispatches_branch_review_resolve_route(self):
        state = mock.MagicMock()
        state.resolve_branch_review.return_value = ({"status": "ok"}, HTTPStatus.OK)
        client = _make_client(state)

        resp = client.post(
            "/api/branches/review-resolve",
            json={
                "branch_id": "branch-1",
                "resolved_by_session_id": "sess-1",
                "resolution": "approved",
                "note": "looks good",
            },
        )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"status": "ok"})
        state.resolve_branch_review.assert_called_once_with(
            branch_id="branch-1",
            resolved_by_session_id="sess-1",
            resolution="approved",
            note="looks good",
        )
