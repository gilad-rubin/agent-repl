from __future__ import annotations

import unittest
from http import HTTPStatus
from unittest import mock

from agent_repl.core.runtime_http_routes import handle_runtime_get, handle_runtime_post


class TestRuntimeHttpRoutes(unittest.TestCase):
    def test_returns_none_for_unknown_path(self):
        self.assertIsNone(handle_runtime_get(mock.Mock(), "/api/nope"))
        self.assertIsNone(handle_runtime_post(mock.Mock(), "/api/nope", {}))

    def test_dispatches_runtimes_get_route(self):
        state = mock.Mock()
        state.list_runtimes_payload.return_value = {"status": "ok", "runtimes": []}

        status, body = handle_runtime_get(state, "/api/runtimes")

        self.assertEqual(status, HTTPStatus.OK)
        self.assertEqual(body["status"], "ok")
        state.list_runtimes_payload.assert_called_once_with()

    def test_validates_invalid_runtime_mode(self):
        status, body = handle_runtime_post(
            mock.Mock(),
            "/api/runtimes/start",
            {"runtime_id": "runtime-1", "mode": "wrong"},
        )

        self.assertEqual(status, HTTPStatus.BAD_REQUEST)
        self.assertEqual(body, {"error": "Invalid mode"})

    def test_dispatches_run_finish_route(self):
        state = mock.Mock()
        state.finish_run.return_value = ({"status": "ok"}, HTTPStatus.OK)

        status, body = handle_runtime_post(
            state,
            "/api/runs/finish",
            {"run_id": "run-1", "status": "completed"},
        )

        self.assertEqual(status, HTTPStatus.OK)
        self.assertEqual(body, {"status": "ok"})
        state.finish_run.assert_called_once_with("run-1", "completed")
