"""Tests for checkpoint HTTP routes."""
from __future__ import annotations

import unittest
from http import HTTPStatus
from unittest.mock import MagicMock

from starlette.testclient import TestClient

from agent_repl.core.asgi import create_app


def _mock_state(token: str = "test-token") -> MagicMock:
    state = MagicMock()
    state.token = token
    state.pid = 12345
    state.health_payload.return_value = {"status": "ok"}
    state.status_payload.return_value = {"status": "ok"}
    return state


class TestCheckpointCreate(unittest.TestCase):
    def setUp(self):
        self.state = _mock_state()
        self.app = create_app(self.state)
        self.client = TestClient(self.app, raise_server_exceptions=False)
        self.headers = {"Authorization": "token test-token"}

    def test_create_checkpoint(self):
        self.state.checkpoint_create.return_value = (
            {"status": "ok", "checkpoint_id": "cp-1", "path": "demo.ipynb"},
            HTTPStatus.OK,
        )
        resp = self.client.post(
            "/api/checkpoints/create",
            json={"path": "demo.ipynb", "label": "before refactor"},
            headers=self.headers,
        )
        self.assertEqual(resp.status_code, 200)
        self.state.checkpoint_create.assert_called_once_with(
            "demo.ipynb", label="before refactor", session_id=None,
        )

    def test_create_checkpoint_with_session(self):
        self.state.checkpoint_create.return_value = (
            {"status": "ok", "checkpoint_id": "cp-2"},
            HTTPStatus.OK,
        )
        resp = self.client.post(
            "/api/checkpoints/create",
            json={"path": "nb.ipynb", "session_id": "s-1"},
            headers=self.headers,
        )
        self.assertEqual(resp.status_code, 200)
        self.state.checkpoint_create.assert_called_once_with(
            "nb.ipynb", label=None, session_id="s-1",
        )

    def test_create_missing_path_returns_400(self):
        resp = self.client.post(
            "/api/checkpoints/create",
            json={},
            headers=self.headers,
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("error", resp.json())


class TestCheckpointRestore(unittest.TestCase):
    def setUp(self):
        self.state = _mock_state()
        self.app = create_app(self.state)
        self.client = TestClient(self.app, raise_server_exceptions=False)
        self.headers = {"Authorization": "token test-token"}

    def test_restore_checkpoint(self):
        self.state.checkpoint_restore.return_value = (
            {"status": "ok", "checkpoint_id": "cp-1", "restored": True},
            HTTPStatus.OK,
        )
        resp = self.client.post(
            "/api/checkpoints/restore",
            json={"checkpoint_id": "cp-1"},
            headers=self.headers,
        )
        self.assertEqual(resp.status_code, 200)
        self.state.checkpoint_restore.assert_called_once_with("cp-1")

    def test_restore_missing_id_returns_400(self):
        resp = self.client.post(
            "/api/checkpoints/restore",
            json={},
            headers=self.headers,
        )
        self.assertEqual(resp.status_code, 400)

    def test_restore_not_found(self):
        self.state.checkpoint_restore.return_value = (
            {"error": "Checkpoint not found"},
            HTTPStatus.NOT_FOUND,
        )
        resp = self.client.post(
            "/api/checkpoints/restore",
            json={"checkpoint_id": "nonexistent"},
            headers=self.headers,
        )
        self.assertEqual(resp.status_code, 404)


class TestCheckpointList(unittest.TestCase):
    def setUp(self):
        self.state = _mock_state()
        self.app = create_app(self.state)
        self.client = TestClient(self.app, raise_server_exceptions=False)
        self.headers = {"Authorization": "token test-token"}

    def test_list_checkpoints(self):
        self.state.checkpoint_list.return_value = (
            {"status": "ok", "checkpoints": [{"checkpoint_id": "cp-1"}]},
            HTTPStatus.OK,
        )
        resp = self.client.get(
            "/api/checkpoints/list?path=demo.ipynb",
            headers=self.headers,
        )
        self.assertEqual(resp.status_code, 200)
        self.state.checkpoint_list.assert_called_once_with("demo.ipynb")

    def test_list_missing_path_returns_400(self):
        resp = self.client.get(
            "/api/checkpoints/list",
            headers=self.headers,
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("error", resp.json())


class TestCheckpointDelete(unittest.TestCase):
    def setUp(self):
        self.state = _mock_state()
        self.app = create_app(self.state)
        self.client = TestClient(self.app, raise_server_exceptions=False)
        self.headers = {"Authorization": "token test-token"}

    def test_delete_checkpoint(self):
        self.state.checkpoint_delete.return_value = (
            {"status": "ok", "deleted": True},
            HTTPStatus.OK,
        )
        resp = self.client.post(
            "/api/checkpoints/delete",
            json={"checkpoint_id": "cp-1"},
            headers=self.headers,
        )
        self.assertEqual(resp.status_code, 200)
        self.state.checkpoint_delete.assert_called_once_with("cp-1")

    def test_delete_missing_id_returns_400(self):
        resp = self.client.post(
            "/api/checkpoints/delete",
            json={},
            headers=self.headers,
        )
        self.assertEqual(resp.status_code, 400)

    def test_delete_not_found(self):
        self.state.checkpoint_delete.return_value = (
            {"error": "Checkpoint not found"},
            HTTPStatus.NOT_FOUND,
        )
        resp = self.client.post(
            "/api/checkpoints/delete",
            json={"checkpoint_id": "gone"},
            headers=self.headers,
        )
        self.assertEqual(resp.status_code, 404)


class TestCheckpointAuth(unittest.TestCase):
    def test_create_requires_auth(self):
        state = _mock_state()
        app = create_app(state)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post("/api/checkpoints/create", json={"path": "x.ipynb"})
        self.assertEqual(resp.status_code, 401)

    def test_list_requires_auth(self):
        state = _mock_state()
        app = create_app(state)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/api/checkpoints/list?path=x.ipynb")
        self.assertEqual(resp.status_code, 401)


if __name__ == "__main__":
    unittest.main()
