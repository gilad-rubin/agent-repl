"""Tests for non-blocking ix (insert-execute).

The problem: `ix` blocks until cell execution finishes. For long-running cells,
the calling process times out. After that, there's no way to check status or
recover — and re-running `ix` double-inserts.

Design: `ix` should insert the cell, fire the execute request to the kernel, and
return immediately with the cell_id. The caller uses `exec --cell-id <id>` to
wait for results, or `cat --cells -1 --detail full` to poll.

This matches how a human works in Jupyter:
  - Adding a cell = one action (insert)
  - Running it = another action (exec)
  - ix = insert + fire, not insert + wait
"""
from __future__ import annotations

import json
import sys
import time
import unittest
from io import StringIO
from unittest import mock

from agent_repl.cli import build_parser, main
from agent_repl.core import ExecutionResult, ServerInfo
from agent_repl.notebook import build_cell

NOTEBOOK_PATH = "demo.ipynb"


def _make_server(**kwargs) -> ServerInfo:
    defaults = dict(url="http://127.0.0.1:9999", base_url="/", root_dir=".", token="", port=9999)
    defaults.update(kwargs)
    return ServerInfo(**defaults)


def _make_code_cell(source: str = "", cell_id: str | None = None) -> dict:
    cell = dict(build_cell("code", source))
    if cell_id:
        cell["id"] = cell_id
    return cell


def _notebook_content(*cells):
    return {
        "cells": list(cells) if cells else [_make_code_cell("x = 1", cell_id="cell-0")],
        "metadata": {},
        "nbformat": 4,
        "nbformat_minor": 5,
    }


# ---------------------------------------------------------------------------
# ix should not block on execution
# ---------------------------------------------------------------------------

class TestIxIsNonBlocking(unittest.TestCase):
    """ix inserts the cell and fires execution, but does NOT wait for it."""

    def _run_ix(self, source: str = "import time; time.sleep(30)") -> tuple[dict, mock.MagicMock]:
        from agent_repl.execution import runner as runner_mod

        model = {"content": _notebook_content(), "last_modified": "2024-01-01T00:00:00Z"}
        session = {"id": "s1", "path": NOTEBOOK_PATH, "kernel": {"id": "k1", "name": "python3"}}

        buf = StringIO()
        old_stdout = sys.stdout
        sys.stdout = buf
        try:
            with (
                mock.patch("agent_repl.server.select_server", return_value=_make_server()),
                mock.patch.object(runner_mod, "load_notebook_model", return_value=model),
                mock.patch.object(runner_mod, "save_notebook_content"),
                mock.patch.object(runner_mod, "_fire_execute_request") as fire_mock,
            ):
                main(["ix", NOTEBOOK_PATH, "-p", "9999", "-s", source])
        finally:
            sys.stdout = old_stdout

        return json.loads(buf.getvalue()), fire_mock

    def test_ix_returns_cell_id(self):
        result, _ = self._run_ix()
        self.assertIn("cell_id", result.get("insert", {}))

    def test_ix_returns_cell_index(self):
        result, _ = self._run_ix()
        cell = result.get("insert", {}).get("cell", {})
        self.assertIn("index", cell)

    def test_ix_does_not_contain_execution_events(self):
        """ix should not wait for execution — no events in output."""
        result, _ = self._run_ix()
        self.assertNotIn("execute", result,
                         "ix must not include an execute block — it fires and forgets")

    def test_ix_fires_execute_request(self):
        """ix must send the execute request to the kernel (fire-and-forget)."""
        _, fire_mock = self._run_ix()
        fire_mock.assert_called_once()

    def test_ix_returns_quickly(self):
        """ix with a sleep(30) cell should return in under 2 seconds."""
        start = time.time()
        self._run_ix("import time; time.sleep(30)")
        elapsed = time.time() - start
        self.assertLess(elapsed, 2.0, f"ix took {elapsed:.1f}s — it should not block on execution")


# ---------------------------------------------------------------------------
# ix --wait: blocks until execution completes
# ---------------------------------------------------------------------------

class TestIxWait(unittest.TestCase):
    """ix --wait should insert the cell AND wait for execution to complete."""

    def test_wait_flag_accepted(self):
        parser = build_parser()
        args = parser.parse_args(["ix", "demo.ipynb", "-s", "x=1", "--wait"])
        self.assertTrue(args.wait)

    def test_wait_default_is_false(self):
        parser = build_parser()
        args = parser.parse_args(["ix", "demo.ipynb", "-s", "x=1"])
        self.assertFalse(args.wait)

    def test_wait_includes_execute_result(self):
        from agent_repl.execution import runner as runner_mod

        model = {"content": _notebook_content(), "last_modified": "2024-01-01T00:00:00Z"}
        session = {"id": "s1", "path": NOTEBOOK_PATH, "kernel": {"id": "k1", "name": "python3"}}
        exec_result = ExecutionResult(
            transport="websocket", kernel_id="k1", session_id="s1", path=NOTEBOOK_PATH,
            reply={"status": "ok", "execution_count": 1, "payload": [], "user_expressions": {}},
            events=[{"type": "stream", "name": "stdout", "text": "hello\n"}],
        )

        buf = StringIO()
        old_stdout = sys.stdout
        sys.stdout = buf
        try:
            with (
                mock.patch("agent_repl.server.select_server", return_value=_make_server()),
                mock.patch.object(runner_mod, "load_notebook_model", return_value=model),
                mock.patch.object(runner_mod, "save_notebook_content"),
                mock.patch.object(runner_mod, "_execute_request", return_value=exec_result),
            ):
                main(["ix", NOTEBOOK_PATH, "-p", "9999", "-s", "print('hello')", "--wait"])
        finally:
            sys.stdout = old_stdout

        result = json.loads(buf.getvalue())
        self.assertIn("execute", result, "ix --wait must include execute result")
        self.assertIn("events", result["execute"])
        self.assertIn("cell_id", result)


# ---------------------------------------------------------------------------
# _fire_execute_request: sends to kernel without waiting
# ---------------------------------------------------------------------------

class TestFireExecuteRequest(unittest.TestCase):
    """_fire_execute_request opens a websocket, sends the execute_request msg,
    and closes immediately — without waiting for reply or idle."""

    def test_function_exists(self):
        from agent_repl.execution.runner import _fire_execute_request
        import inspect
        sig = inspect.signature(_fire_execute_request)
        self.assertIn("server", sig.parameters)
        self.assertIn("code", sig.parameters)

    def test_sends_execute_request_message(self):
        """Should send a Jupyter execute_request message over websocket."""
        from agent_repl.execution import runner as runner_mod

        server = _make_server()
        session = {"id": "s1", "path": NOTEBOOK_PATH, "kernel": {"id": "k1", "name": "python3"}}

        mock_ws = mock.MagicMock()
        with (
            mock.patch.object(runner_mod, "_resolve_session", return_value=session),
            mock.patch("websocket.create_connection", return_value=mock_ws),
        ):
            runner_mod._fire_execute_request(server, path=NOTEBOOK_PATH, code="x = 1")

        # Should have sent exactly one message
        mock_ws.send.assert_called_once()
        msg = json.loads(mock_ws.send.call_args[0][0])
        self.assertEqual(msg["header"]["msg_type"], "execute_request")
        self.assertEqual(msg["content"]["code"], "x = 1")

        # Should have closed the websocket
        mock_ws.close.assert_called_once()


# ---------------------------------------------------------------------------
# exec --cell-id: the wait path (already exists, verify contract)
# ---------------------------------------------------------------------------

class TestExecCellIdContract(unittest.TestCase):
    """exec --cell-id <id> should execute the cell and wait for the result.
    This is the 'wait' half of the split: ix fires, exec --cell-id waits."""

    def test_exec_accepts_cell_id(self):
        parser = build_parser()
        args = parser.parse_args(["exec", NOTEBOOK_PATH, "--cell-id", "abc123"])
        self.assertEqual(args.cell_id, "abc123")

    def test_exec_cell_id_looks_up_source(self):
        """exec --cell-id should read the cell's source from the notebook."""
        from agent_repl.execution import runner as runner_mod
        from agent_repl import notebook as notebook_mod

        cells = [_make_code_cell("print('hello')", cell_id="target-cell")]
        model = {"content": _notebook_content(*cells), "last_modified": "2024-01-01T00:00:00Z"}

        exec_result = ExecutionResult(
            transport="websocket", kernel_id="k1", session_id="s1", path=NOTEBOOK_PATH,
            reply={"status": "ok", "execution_count": 1, "payload": [], "user_expressions": {}},
            events=[],
        )

        buf = StringIO()
        old_stdout = sys.stdout
        sys.stdout = buf
        try:
            with (
                mock.patch("agent_repl.server.select_server", return_value=_make_server()),
                mock.patch.object(notebook_mod, "load_notebook_model", return_value=model),
                mock.patch.object(runner_mod, "load_notebook_model", return_value=model),
                mock.patch.object(runner_mod, "save_notebook_content"),
                mock.patch.object(runner_mod, "_execute_request", return_value=exec_result) as exec_mock,
            ):
                main(["exec", NOTEBOOK_PATH, "-p", "9999", "--cell-id", "target-cell"])
        finally:
            sys.stdout = old_stdout

        # Should have executed with the cell's source code
        exec_mock.assert_called_once()
        request = exec_mock.call_args.kwargs.get("request") or exec_mock.call_args[1].get("request")
        self.assertEqual(request.code, "print('hello')")


if __name__ == "__main__":
    unittest.main()
