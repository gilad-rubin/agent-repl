from __future__ import annotations

import unittest
from http import HTTPStatus
from unittest import mock

from agent_repl.core.collaboration_http_routes import (
    handle_collaboration_get,
    handle_collaboration_post,
)


class TestCollaborationHttpRoutes(unittest.TestCase):
    def test_returns_none_for_unknown_path(self):
        self.assertIsNone(handle_collaboration_get(mock.Mock(), "/api/nope"))
        self.assertIsNone(handle_collaboration_post(mock.Mock(), "/api/nope", {}))

    def test_dispatches_sessions_get_route(self):
        state = mock.Mock()
        state.list_sessions_payload.return_value = {"status": "ok", "sessions": []}

        status, body = handle_collaboration_get(state, "/api/sessions")

        self.assertEqual(status, HTTPStatus.OK)
        self.assertEqual(body["status"], "ok")
        state.list_sessions_payload.assert_called_once_with()

    def test_validates_missing_session_id_for_presence_clear(self):
        status, body = handle_collaboration_post(mock.Mock(), "/api/sessions/presence/clear", {})

        self.assertEqual(status, HTTPStatus.BAD_REQUEST)
        self.assertEqual(body, {"error": "Missing session_id"})

    def test_dispatches_branch_review_resolve_route(self):
        state = mock.Mock()
        state.resolve_branch_review.return_value = ({"status": "ok"}, HTTPStatus.OK)

        status, body = handle_collaboration_post(
            state,
            "/api/branches/review-resolve",
            {
                "branch_id": "branch-1",
                "resolved_by_session_id": "sess-1",
                "resolution": "approved",
                "note": "looks good",
            },
        )

        self.assertEqual(status, HTTPStatus.OK)
        self.assertEqual(body, {"status": "ok"})
        state.resolve_branch_review.assert_called_once_with(
            branch_id="branch-1",
            resolved_by_session_id="sess-1",
            resolution="approved",
            note="looks good",
        )
