from __future__ import annotations

import unittest
from http import HTTPStatus
from unittest import mock

from starlette.applications import Starlette
from starlette.testclient import TestClient

from agent_repl.core.runtime_http_routes import routes


def _make_client(state: mock.MagicMock) -> TestClient:
    app = Starlette(routes=routes(state))
    return TestClient(app, raise_server_exceptions=False)


class TestRuntimeHttpRoutes(unittest.TestCase):
    def test_unknown_route_returns_404(self):
        client = _make_client(mock.MagicMock())
        resp = client.get("/api/nope")
        self.assertEqual(resp.status_code, 404)

    def test_dispatches_runtimes_get_route(self):
        state = mock.MagicMock()
        state.list_runtimes_payload.return_value = {"status": "ok", "runtimes": []}
        client = _make_client(state)

        resp = client.get("/api/runtimes")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "ok")
        state.list_runtimes_payload.assert_called_once_with()

    def test_validates_invalid_runtime_mode(self):
        client = _make_client(mock.MagicMock())

        resp = client.post(
            "/api/runtimes/start",
            json={"runtime_id": "runtime-1", "mode": "wrong"},
        )

        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json(), {"error": "Invalid mode"})

    def test_dispatches_run_finish_route(self):
        state = mock.MagicMock()
        state.finish_run.return_value = ({"status": "ok"}, HTTPStatus.OK)
        client = _make_client(state)

        resp = client.post(
            "/api/runs/finish",
            json={"run_id": "run-1", "status": "completed"},
        )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"status": "ok"})
        state.finish_run.assert_called_once_with("run-1", "completed")
