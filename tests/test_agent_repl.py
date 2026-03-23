"""Tests for agent-repl bridge CLI."""
from __future__ import annotations

import json
import sys
import unittest
from io import StringIO
from unittest import mock

from agent_repl.cli import build_parser, main
from agent_repl.client import BridgeClient


# ---------------------------------------------------------------------------
# BridgeClient discovery
# ---------------------------------------------------------------------------

class TestBridgeDiscovery(unittest.TestCase):
    """BridgeClient.discover() scans runtime dir for connection files."""

    def test_discover_finds_healthy_bridge(self):
        info = json.dumps({"port": 12345, "token": "abc"})
        with (
            mock.patch("agent_repl.client.glob.glob", return_value=["/tmp/agent-repl-bridge-1.json"]),
            mock.patch("agent_repl.client.os.path.getmtime", return_value=1.0),
            mock.patch("agent_repl.client.Path.read_text", return_value=info),
            mock.patch.object(BridgeClient, "health", return_value={"status": "ok"}),
        ):
            client = BridgeClient.discover()
        self.assertEqual(client.base_url, "http://127.0.0.1:12345")
        self.assertEqual(client.token, "abc")

    def test_discover_raises_when_no_bridge(self):
        with mock.patch("agent_repl.client.glob.glob", return_value=[]):
            with self.assertRaises(RuntimeError):
                BridgeClient.discover()


# ---------------------------------------------------------------------------
# BridgeClient endpoint calls
# ---------------------------------------------------------------------------

class TestBridgeEndpoints(unittest.TestCase):
    """BridgeClient methods call correct HTTP endpoints."""

    def setUp(self):
        self.client = BridgeClient("http://127.0.0.1:9999", "tok")
        self.getcwd = mock.patch("agent_repl.client.os.getcwd", return_value="/workspace").start()
        self.mock_get = mock.patch.object(
            self.client._session, "get",
            return_value=mock.Mock(status_code=200, json=lambda: {"ok": True}),
        ).start()
        self.mock_post = mock.patch.object(
            self.client._session, "post",
            return_value=mock.Mock(status_code=200, json=lambda: {"ok": True}),
        ).start()

    def tearDown(self):
        mock.patch.stopall()

    def test_contents_calls_get(self):
        self.client.contents("nb.ipynb")
        self.mock_get.assert_called_once()
        url = self.mock_get.call_args[0][0]
        self.assertIn("/api/notebook/contents", url)
        self.assertEqual(self.mock_get.call_args.kwargs["params"], {"path": "nb.ipynb", "cwd": "/workspace"})

    def test_status_calls_get(self):
        self.client.status("nb.ipynb")
        url = self.mock_get.call_args[0][0]
        self.assertIn("/api/notebook/status", url)
        self.assertEqual(self.mock_get.call_args.kwargs["params"], {"path": "nb.ipynb", "cwd": "/workspace"})

    def test_edit_calls_post(self):
        self.client.edit("nb.ipynb", [{"op": "delete", "cell_index": 0}])
        self.mock_post.assert_called_once()
        url = self.mock_post.call_args[0][0]
        self.assertIn("/api/notebook/edit", url)
        self.assertEqual(
            self.mock_post.call_args.kwargs["json"],
            {"path": "nb.ipynb", "cwd": "/workspace", "operations": [{"op": "delete", "cell_index": 0}]},
        )

    def test_execute_cell_calls_post(self):
        self.client.execute_cell("nb.ipynb", cell_id="abc")
        url = self.mock_post.call_args[0][0]
        self.assertIn("/api/notebook/execute-cell", url)

    def test_insert_and_execute_calls_post(self):
        self.client.insert_and_execute("nb.ipynb", "x = 1")
        url = self.mock_post.call_args[0][0]
        self.assertIn("/api/notebook/insert-and-execute", url)

    def test_execute_all_calls_post(self):
        self.client.execute_all("nb.ipynb")
        url = self.mock_post.call_args[0][0]
        self.assertIn("/api/notebook/execute-all", url)

    def test_restart_kernel_calls_post(self):
        self.client.restart_kernel("nb.ipynb")
        url = self.mock_post.call_args[0][0]
        self.assertIn("/api/notebook/restart-kernel", url)

    def test_restart_and_run_all_calls_post(self):
        self.client.restart_and_run_all("nb.ipynb")
        url = self.mock_post.call_args[0][0]
        self.assertIn("/api/notebook/restart-and-run-all", url)

    def test_create_calls_post(self):
        self.client.create("new.ipynb")
        url = self.mock_post.call_args[0][0]
        self.assertIn("/api/notebook/create", url)
        self.assertEqual(self.mock_post.call_args.kwargs["json"], {"path": "new.ipynb", "cwd": "/workspace"})

    def test_create_includes_kernel_id_and_cells(self):
        self.client.create(
            "new.ipynb",
            cells=[{"type": "code", "source": "x = 1"}],
            kernel_id="/tmp/.venv/bin/python",
        )
        self.assertEqual(
            self.mock_post.call_args.kwargs["json"],
            {
                "path": "new.ipynb",
                "cwd": "/workspace",
                "cells": [{"type": "code", "source": "x = 1"}],
                "kernel_id": "/tmp/.venv/bin/python",
            },
        )

    def test_prompt_status_calls_post(self):
        self.client.prompt_status("nb.ipynb", "cell-1", "answered")
        url = self.mock_post.call_args[0][0]
        self.assertIn("/api/notebook/prompt-status", url)
        self.assertEqual(
            self.mock_post.call_args.kwargs["json"],
            {"path": "nb.ipynb", "cwd": "/workspace", "cell_id": "cell-1", "status": "answered"},
        )

    def test_reload_calls_post(self):
        self.client.reload()
        url = self.mock_post.call_args[0][0]
        self.assertIn("/api/reload", url)

    def test_auth_header_set(self):
        self.assertEqual(self.client._session.headers["Authorization"], "token tok")


# ---------------------------------------------------------------------------
# CLI parser
# ---------------------------------------------------------------------------

class TestParser(unittest.TestCase):
    """CLI argument parsing."""

    def test_cat(self):
        args = build_parser().parse_args(["cat", "nb.ipynb"])
        self.assertEqual(args.command, "cat")
        self.assertEqual(args.path, "nb.ipynb")

    def test_cat_no_outputs(self):
        args = build_parser().parse_args(["cat", "nb.ipynb", "--no-outputs"])
        self.assertTrue(args.no_outputs)

    def test_status(self):
        args = build_parser().parse_args(["status", "nb.ipynb"])
        self.assertEqual(args.command, "status")

    def test_exec_with_code(self):
        args = build_parser().parse_args(["exec", "nb.ipynb", "-c", "x=1"])
        self.assertEqual(args.code, "x=1")

    def test_exec_with_cell_id(self):
        args = build_parser().parse_args(["exec", "nb.ipynb", "--cell-id", "abc"])
        self.assertEqual(args.cell_id, "abc")

    def test_ix(self):
        args = build_parser().parse_args(["ix", "nb.ipynb", "-s", "print(1)"])
        self.assertEqual(args.source, "print(1)")

    def test_edit_replace_source(self):
        args = build_parser().parse_args(["edit", "nb.ipynb", "replace-source", "-s", "x=1", "--cell-id", "c1"])
        self.assertEqual(args.edit_command, "replace-source")

    def test_edit_insert(self):
        args = build_parser().parse_args(["edit", "nb.ipynb", "insert", "-s", "# hi", "--cell-type", "markdown"])
        self.assertEqual(args.edit_command, "insert")
        self.assertEqual(getattr(args, "cell_type", None), "markdown")

    def test_edit_delete(self):
        args = build_parser().parse_args(["edit", "nb.ipynb", "delete", "-i", "2"])
        self.assertEqual(args.edit_command, "delete")
        self.assertEqual(args.index, 2)

    def test_edit_move(self):
        args = build_parser().parse_args(["edit", "nb.ipynb", "move", "--cell-id", "c1", "--to-index", "5"])
        self.assertEqual(args.edit_command, "move")
        self.assertEqual(args.to_index, 5)

    def test_edit_clear_outputs_all(self):
        args = build_parser().parse_args(["edit", "nb.ipynb", "clear-outputs", "--all"])
        self.assertTrue(args.all)

    def test_respond(self):
        args = build_parser().parse_args(["respond", "nb.ipynb", "--to", "cell-1", "-s", "42"])
        self.assertEqual(args.to, "cell-1")

    def test_new(self):
        args = build_parser().parse_args(["new", "nb.ipynb"])
        self.assertEqual(args.command, "new")

    def test_new_with_kernel_and_cells_json(self):
        args = build_parser().parse_args([
            "new", "nb.ipynb", "--kernel", "/tmp/.venv/bin/python", "--cells-json", '[{"type":"code","source":"x=1"}]',
        ])
        self.assertEqual(args.kernel, "/tmp/.venv/bin/python")
        self.assertEqual(args.cells_json, '[{"type":"code","source":"x=1"}]')

    def test_reload(self):
        args = build_parser().parse_args(["reload"])
        self.assertEqual(args.command, "reload")


# ---------------------------------------------------------------------------
# CLI command handlers
# ---------------------------------------------------------------------------

class TestCommands(unittest.TestCase):
    """CLI commands call correct BridgeClient methods."""

    def _run(self, argv: list[str], mock_client: BridgeClient) -> tuple[int, str]:
        buf = StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            with mock.patch("agent_repl.cli._client", return_value=mock_client):
                code = main(argv)
        finally:
            sys.stdout = old
        return code, buf.getvalue()

    def _mock_client(self, **overrides):
        client = mock.MagicMock(spec=BridgeClient)
        client.contents.return_value = {
            "path": "nb.ipynb",
            "cells": [
                {
                    "index": 0, "cell_id": "c1", "cell_type": "code",
                    "source": "x = 1", "outputs": [], "execution_count": 1,
                    "metadata": {},
                },
            ],
        }
        client.status.return_value = {"kernel_state": "idle"}
        client.insert_and_execute.return_value = {"status": "ok", "cell_id": "new-cell"}
        client.execute_cell.return_value = {"status": "ok"}
        client.execute_all.return_value = {"status": "ok"}
        client.restart_kernel.return_value = {"status": "ok"}
        client.restart_and_run_all.return_value = {"status": "ok"}
        client.create.return_value = {"status": "ok"}
        client.edit.return_value = {"results": []}
        client.prompt_status.return_value = {"status": "ok"}
        client.reload.return_value = {"status": "ok"}
        for k, v in overrides.items():
            setattr(client, k, mock.Mock(return_value=v))
        return client

    def test_cat_outputs_json(self):
        client = self._mock_client()
        code, out = self._run(["cat", "nb.ipynb"], client)
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual(data["path"], "nb.ipynb")
        self.assertEqual(len(data["cells"]), 1)
        client.contents.assert_called_once_with("nb.ipynb")

    def test_status(self):
        client = self._mock_client()
        code, _ = self._run(["status", "nb.ipynb"], client)
        self.assertEqual(code, 0)
        client.status.assert_called_once_with("nb.ipynb")

    def test_ix(self):
        client = self._mock_client()
        code, _ = self._run(["ix", "nb.ipynb", "-s", "x=1"], client)
        self.assertEqual(code, 0)
        client.insert_and_execute.assert_called_once_with("nb.ipynb", "x=1", at_index=-1, wait=True, timeout=30)

    def test_ix_no_wait(self):
        client = self._mock_client()
        code, _ = self._run(["ix", "nb.ipynb", "-s", "x=1", "--no-wait"], client)
        self.assertEqual(code, 0)
        client.insert_and_execute.assert_called_once_with("nb.ipynb", "x=1", at_index=-1, wait=False, timeout=30)

    def test_exec_with_code(self):
        client = self._mock_client()
        code, _ = self._run(["exec", "nb.ipynb", "-c", "x=1"], client)
        self.assertEqual(code, 0)
        client.insert_and_execute.assert_called_once()

    def test_exec_with_cell_id(self):
        client = self._mock_client()
        code, _ = self._run(["exec", "nb.ipynb", "--cell-id", "abc"], client)
        self.assertEqual(code, 0)
        client.execute_cell.assert_called_once_with("nb.ipynb", cell_id="abc", wait=True, timeout=30)

    def test_respond(self):
        client = self._mock_client()
        code, _ = self._run(["respond", "nb.ipynb", "--to", "cell-1", "-s", "42"], client)
        self.assertEqual(code, 0)
        # Should mark in-progress, insert+execute, mark answered
        self.assertEqual(client.prompt_status.call_count, 2)
        client.insert_and_execute.assert_called_once()

    def test_prompts(self):
        client = self._mock_client(contents={
            "path": "nb.ipynb",
            "cells": [
                {"index": 0, "cell_id": "p1", "cell_type": "markdown", "source": "do X",
                 "metadata": {"custom": {"agent-repl": {"type": "prompt", "status": "pending"}}}},
                {"index": 1, "cell_id": "c1", "cell_type": "code", "source": "x=1", "metadata": {}},
            ],
        })
        code, out = self._run(["prompts", "nb.ipynb"], client)
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual(len(data["prompts"]), 1)
        self.assertEqual(data["prompts"][0]["cell_id"], "p1")

    def test_new(self):
        client = self._mock_client()
        code, _ = self._run(["new", "nb.ipynb"], client)
        self.assertEqual(code, 0)
        client.create.assert_called_once_with("nb.ipynb", cells=None, kernel_id=None)

    def test_new_with_kernel_and_cells_json(self):
        client = self._mock_client()
        code, _ = self._run([
            "new", "nb.ipynb", "--kernel", "/tmp/.venv/bin/python", "--cells-json", '[{"type":"code","source":"x=1"}]',
        ], client)
        self.assertEqual(code, 0)
        client.create.assert_called_once_with(
            "nb.ipynb",
            cells=[{"type": "code", "source": "x=1"}],
            kernel_id="/tmp/.venv/bin/python",
        )

    def test_reload_outputs_response(self):
        client = self._mock_client(reload={
            "status": "ok",
            "extension_root": "/tmp/agent-repl",
            "routes_module": "/tmp/agent-repl/out/routes.js",
        })
        code, out = self._run(["reload"], client)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["extension_root"], "/tmp/agent-repl")
        client.reload.assert_called_once()

    def test_pretty_flag(self):
        client = self._mock_client()
        code, out = self._run(["--pretty", "status", "nb.ipynb"], client)
        self.assertEqual(code, 0)
        # Pretty output has newlines/indentation
        self.assertIn("\n", out)

    def test_no_command_shows_help(self):
        client = self._mock_client()
        code, _ = self._run([], client)
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
