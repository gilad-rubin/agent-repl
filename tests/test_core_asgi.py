"""Tests for the ASGI application shell."""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from starlette.testclient import TestClient

from agent_repl.core.asgi import create_app


def _mock_state(token: str = "test-token") -> MagicMock:
    state = MagicMock()
    state.token = token
    state.pid = 12345
    state.health_payload.return_value = {"status": "ok", "healthy": True}
    state.status_payload.return_value = {"status": "ok", "version": "0.1.0"}
    return state


class TestTokenAuth(unittest.TestCase):
    def test_request_without_token_returns_401(self):
        app = create_app(_mock_state())
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/api/health")
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(resp.json()["error"], "Unauthorized")

    def test_request_with_wrong_token_returns_401(self):
        app = create_app(_mock_state(token="correct"))
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/api/health", headers={"Authorization": "token wrong"})
        self.assertEqual(resp.status_code, 401)

    def test_request_with_valid_token_succeeds(self):
        app = create_app(_mock_state(token="secret"))
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/api/health", headers={"Authorization": "token secret"})
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["healthy"])


class TestInlineRoutes(unittest.TestCase):
    def setUp(self):
        self.state = _mock_state()
        self.app = create_app(self.state)
        self.client = TestClient(self.app, raise_server_exceptions=False)
        self.headers = {"Authorization": "token test-token"}

    def test_health(self):
        resp = self.client.get("/api/health", headers=self.headers)
        self.assertEqual(resp.status_code, 200)
        self.state.health_payload.assert_called_once()

    def test_status(self):
        resp = self.client.get("/api/status", headers=self.headers)
        self.assertEqual(resp.status_code, 200)
        self.state.status_payload.assert_called_once()

    def test_shutdown_calls_callback(self):
        called = []
        app = create_app(self.state, shutdown_callback=lambda: called.append(True))
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post("/api/shutdown", headers=self.headers)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["stopping"])
        self.assertEqual(called, [True])

    def test_unknown_route_returns_404(self):
        resp = self.client.get("/api/nonexistent", headers=self.headers)
        self.assertEqual(resp.status_code, 404)


class TestDomainRouteDispatch(unittest.TestCase):
    def setUp(self):
        self.state = _mock_state()
        self.app = create_app(self.state)
        self.client = TestClient(self.app, raise_server_exceptions=False)
        self.headers = {"Authorization": "token test-token"}

    def test_notebook_contents_post(self):
        from http import HTTPStatus
        self.state.notebook_contents.return_value = (
            {"cells": [], "path": "demo.ipynb"},
            HTTPStatus.OK,
        )
        resp = self.client.post(
            "/api/notebooks/contents",
            json={"path": "demo.ipynb"},
            headers=self.headers,
        )
        self.assertEqual(resp.status_code, 200)
        self.state.notebook_contents.assert_called_once()

    def test_documents_get(self):
        from http import HTTPStatus
        self.state.list_documents_payload.return_value = {"documents": []}
        resp = self.client.get("/api/documents", headers=self.headers)
        self.assertEqual(resp.status_code, 200)

    def test_sessions_get(self):
        from http import HTTPStatus
        self.state.list_sessions_payload.return_value = {"sessions": []}
        resp = self.client.get("/api/sessions", headers=self.headers)
        self.assertEqual(resp.status_code, 200)

    def test_runtimes_get(self):
        from http import HTTPStatus
        self.state.list_runtimes_payload.return_value = {"runtimes": []}
        resp = self.client.get("/api/runtimes", headers=self.headers)
        self.assertEqual(resp.status_code, 200)

    def test_runs_get(self):
        from http import HTTPStatus
        self.state.list_runs_payload.return_value = {"runs": []}
        resp = self.client.get("/api/runs", headers=self.headers)
        self.assertEqual(resp.status_code, 200)


if __name__ == "__main__":
    unittest.main()
