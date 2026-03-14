"""Tests for agent-repl — covers new v1 features (steps 0-7)."""
from __future__ import annotations

import json
import os
import unittest
import uuid
from unittest import mock

from agent_repl.notebook import (
    apply_clear_outputs,
    apply_delete,
    apply_insert,
    apply_move,
    apply_replace_source,
    build_cell,
    resolve_cell_index,
    summarize_cell,
    summarize_cell_brief,
)
from agent_repl.cli import _print, _resolve_path_arg, build_parser, main
from agent_repl.core import CommandError, HTTPCommandError, TransportRetryUnsafeError
from agent_repl.execution import _belongs_to_execution, _sanitize_error_text, _ws_url
from agent_repl.core import DEFAULT_EXEC_TIMEOUT, DEFAULT_TIMEOUT, ExecutionResult, ServerInfo
from agent_repl.output import (
    events_to_notebook_outputs,
    strip_media_from_data,
    strip_media_from_event,
    strip_media_from_output,
    summarize_channel_message,
)

NOTEBOOK_PATH = "demo.ipynb"
TINY_PNG_BASE64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO7Z1ioAAAAASUVORK5CYII="

RUN_SLOW_INTEGRATION = os.environ.get("JLK_RUN_SLOW_INTEGRATION") == "1"


def slow_integration_test(fn):
    return unittest.skipUnless(
        RUN_SLOW_INTEGRATION,
        "slow integration test skipped (set JLK_RUN_SLOW_INTEGRATION=1 to include)",
    )(fn)


def _make_server(**kwargs) -> ServerInfo:
    defaults = dict(url="http://127.0.0.1:9999", base_url="/", root_dir=".", token="", port=9999)
    defaults.update(kwargs)
    return ServerInfo(**defaults)


def _make_code_cell(source: str = "", cell_id: str | None = None, outputs: list | None = None) -> dict:
    cell = dict(build_cell("code", source))
    if cell_id:
        cell["id"] = cell_id
    if outputs:
        cell["outputs"] = outputs
    return cell


def _make_md_cell(source: str = "", cell_id: str | None = None) -> dict:
    cell = dict(build_cell("markdown", source))
    if cell_id:
        cell["id"] = cell_id
    return cell


# ---------------------------------------------------------------------------
# Step 1: Output filtering
# ---------------------------------------------------------------------------

class TestStripMedia(unittest.TestCase):
    def test_strip_html_when_plain_present(self):
        data = {"text/plain": "hello", "text/html": "<b>hello</b>"}
        result = strip_media_from_data(data)
        self.assertNotIn("text/html", result)
        self.assertEqual(result["text/plain"], "hello")

    def test_keep_html_when_no_plain(self):
        data = {"text/html": "<b>hello</b>"}
        result = strip_media_from_data(data)
        self.assertEqual(result["text/html"], "<b>hello</b>")

    def test_replace_image_with_placeholder(self):
        data = {"image/png": TINY_PNG_BASE64, "text/plain": "<Figure>"}
        result = strip_media_from_data(data)
        self.assertEqual(result["image/png"], "[image: image/png]")
        self.assertEqual(result["text/plain"], "<Figure>")

    def test_replace_jpeg_and_svg(self):
        data = {"image/jpeg": "abc", "image/svg+xml": "<svg/>"}
        result = strip_media_from_data(data)
        self.assertEqual(result["image/jpeg"], "[image: image/jpeg]")
        self.assertEqual(result["image/svg+xml"], "[image: image/svg+xml]")

    def test_replace_widget(self):
        data = {"application/vnd.jupyter.widget-view+json": {"model_id": "abc"}}
        result = strip_media_from_data(data)
        self.assertEqual(result["application/vnd.jupyter.widget-view+json"], "[widget]")

    def test_keep_json_and_plain(self):
        data = {"application/json": {"key": "val"}, "text/plain": "text"}
        result = strip_media_from_data(data)
        self.assertEqual(result, data)

    def test_strip_event_execute_result(self):
        event = {"type": "execute_result", "data": {"text/plain": "42", "text/html": "<b>42</b>"}}
        result = strip_media_from_event(event)
        self.assertNotIn("text/html", result["data"])
        self.assertEqual(result["data"]["text/plain"], "42")

    def test_strip_event_display_data(self):
        event = {"type": "display_data", "data": {"image/png": TINY_PNG_BASE64}}
        result = strip_media_from_event(event)
        self.assertEqual(result["data"]["image/png"], "[image: image/png]")

    def test_passthrough_stream_event(self):
        event = {"type": "stream", "name": "stdout", "text": "hello"}
        result = strip_media_from_event(event)
        self.assertEqual(result, event)

    def test_passthrough_error_event(self):
        event = {"type": "error", "ename": "ValueError", "evalue": "bad", "traceback": []}
        result = strip_media_from_event(event)
        self.assertEqual(result, event)

    def test_strip_notebook_output(self):
        output = {"output_type": "execute_result", "data": {"text/plain": "ok", "text/html": "<b>ok</b>"}}
        result = strip_media_from_output(output)
        self.assertNotIn("text/html", result["data"])

    def test_strip_display_data_output(self):
        output = {"output_type": "display_data", "data": {"image/png": TINY_PNG_BASE64}}
        result = strip_media_from_output(output)
        self.assertEqual(result["data"]["image/png"], "[image: image/png]")

    def test_passthrough_stream_output(self):
        output = {"output_type": "stream", "name": "stdout", "text": "hello"}
        result = strip_media_from_output(output)
        self.assertEqual(result, output)


# ---------------------------------------------------------------------------
# Step 2: Contents filtering / brief mode
# ---------------------------------------------------------------------------

class TestSummarizeCellBrief(unittest.TestCase):
    def test_brief_short_source(self):
        cell = _make_code_cell("x = 1", cell_id="abc")
        result = summarize_cell_brief(cell, index=0)
        self.assertEqual(result["source_preview"], "x = 1")
        self.assertEqual(result["cell_id"], "abc")
        self.assertEqual(result["index"], 0)

    def test_brief_truncates_after_3_lines(self):
        source = "line1\nline2\nline3\nline4\nline5"
        cell = _make_code_cell(source, cell_id="abc")
        result = summarize_cell_brief(cell, index=0)
        self.assertEqual(result["source_preview"], "line1\nline2\nline3\n...")

    def test_brief_includes_execution_count(self):
        cell = _make_code_cell("x = 1", cell_id="abc")
        cell["execution_count"] = 5
        result = summarize_cell_brief(cell, index=0)
        self.assertEqual(result["execution_count"], 5)

    def test_brief_no_outputs(self):
        cell = _make_code_cell("x = 1", cell_id="abc")
        cell["outputs"] = [{"output_type": "stream", "text": "hello"}]
        result = summarize_cell_brief(cell, index=0)
        self.assertNotIn("outputs", result)


class TestSummarizeCellFull(unittest.TestCase):
    def test_full_with_stripped_outputs(self):
        cell = _make_code_cell("x = 1", cell_id="abc")
        cell["outputs"] = [
            {"output_type": "execute_result", "data": {"text/plain": "1", "text/html": "<b>1</b>"}}
        ]
        result = summarize_cell(cell, index=0, include_outputs=True, strip_media=True)
        self.assertIn("outputs", result)
        self.assertNotIn("text/html", result["outputs"][0]["data"])

    def test_full_raw_outputs(self):
        cell = _make_code_cell("x = 1", cell_id="abc")
        cell["outputs"] = [
            {"output_type": "execute_result", "data": {"text/plain": "1", "text/html": "<b>1</b>"}}
        ]
        result = summarize_cell(cell, index=0, include_outputs=True, strip_media=False)
        self.assertIn("text/html", result["outputs"][0]["data"])


# ---------------------------------------------------------------------------
# Step 3: _apply_* helpers
# ---------------------------------------------------------------------------

class TestApplyReplaceSource(unittest.TestCase):
    def test_replace_by_index(self):
        cells = [_make_code_cell("old", cell_id="a")]
        result = apply_replace_source(cells, index=0, source="new")
        self.assertTrue(result["changed"])
        self.assertEqual(cells[0]["source"], "new")

    def test_no_change_same_source(self):
        cells = [_make_code_cell("same", cell_id="a")]
        result = apply_replace_source(cells, index=0, source="same")
        self.assertFalse(result["changed"])

    def test_replace_by_cell_id(self):
        cells = [_make_code_cell("old", cell_id="target")]
        result = apply_replace_source(cells, cell_id="target", source="new")
        self.assertTrue(result["changed"])


class TestApplyInsert(unittest.TestCase):
    def test_insert_at_index(self):
        cells = [_make_code_cell("a")]
        result = apply_insert(cells, cell_type="code", source="b", at_index=1)
        self.assertEqual(len(cells), 2)
        self.assertEqual(cells[1]["source"], "b")
        self.assertTrue(result["changed"])

    def test_insert_at_minus_one_appends(self):
        cells = [_make_code_cell("a")]
        apply_insert(cells, cell_type="code", source="b", at_index=-1)
        self.assertEqual(len(cells), 2)
        self.assertEqual(cells[1]["source"], "b")

    def test_insert_at_zero(self):
        cells = [_make_code_cell("a")]
        apply_insert(cells, cell_type="markdown", source="# Title", at_index=0)
        self.assertEqual(len(cells), 2)
        self.assertEqual(cells[0]["cell_type"], "markdown")

    def test_insert_out_of_range_raises(self):
        cells = [_make_code_cell("a")]
        with self.assertRaises(CommandError):
            apply_insert(cells, cell_type="code", source="b", at_index=5)

    def test_insert_returns_cell_id(self):
        cells = []
        result = apply_insert(cells, cell_type="code", source="x", at_index=-1)
        self.assertIn("cell_id", result)
        self.assertIsNotNone(result["cell_id"])


class TestApplyDelete(unittest.TestCase):
    def test_delete_by_index(self):
        cells = [_make_code_cell("a", cell_id="a"), _make_code_cell("b", cell_id="b")]
        result = apply_delete(cells, index=0)
        self.assertEqual(len(cells), 1)
        self.assertEqual(cells[0]["source"], "b")

    def test_delete_by_cell_id(self):
        cells = [_make_code_cell("a", cell_id="target"), _make_code_cell("b", cell_id="keep")]
        apply_delete(cells, cell_id="target")
        self.assertEqual(len(cells), 1)
        self.assertEqual(cells[0]["id"], "keep")


class TestApplyMove(unittest.TestCase):
    def test_move_forward(self):
        cells = [_make_code_cell("a", cell_id="a"), _make_code_cell("b", cell_id="b"), _make_code_cell("c", cell_id="c")]
        result = apply_move(cells, index=0, to_index=2)
        self.assertTrue(result["changed"])
        self.assertEqual([c["id"] for c in cells], ["b", "c", "a"])

    def test_move_no_change(self):
        cells = [_make_code_cell("a", cell_id="a")]
        result = apply_move(cells, index=0, to_index=0)
        self.assertFalse(result["changed"])

    def test_move_minus_one_goes_to_end(self):
        cells = [_make_code_cell("a", cell_id="a"), _make_code_cell("b", cell_id="b")]
        result = apply_move(cells, index=0, to_index=-1)
        self.assertTrue(result["changed"])
        self.assertEqual([c["id"] for c in cells], ["b", "a"])


class TestApplyClearOutputs(unittest.TestCase):
    def test_clear_single_cell(self):
        cells = [_make_code_cell("a", cell_id="a")]
        cells[0]["outputs"] = [{"output_type": "stream", "text": "hello"}]
        cells[0]["execution_count"] = 1
        result = apply_clear_outputs(cells, index=0)
        self.assertTrue(result["changed"])
        self.assertEqual(cells[0]["outputs"], [])
        self.assertIsNone(cells[0]["execution_count"])

    def test_clear_all_cells(self):
        cells = [
            _make_code_cell("a", cell_id="a"),
            _make_md_cell("# title", cell_id="b"),
            _make_code_cell("c", cell_id="c"),
        ]
        cells[0]["outputs"] = [{"output_type": "stream", "text": "1"}]
        cells[0]["execution_count"] = 1
        cells[2]["outputs"] = [{"output_type": "stream", "text": "2"}]
        cells[2]["execution_count"] = 2
        result = apply_clear_outputs(cells, all_cells=True)
        self.assertTrue(result["changed"])
        self.assertEqual(result["cleared_cell_count"], 2)


# ---------------------------------------------------------------------------
# Step 4: Batch edit (unit-level, no server)
# ---------------------------------------------------------------------------

class TestBatchEditOperations(unittest.TestCase):
    """Test the _apply_* chain that batch_edit uses."""

    def test_insert_then_replace(self):
        cells = [_make_code_cell("original", cell_id="orig")]
        # insert at end
        r1 = apply_insert(cells, cell_type="code", source="new", at_index=-1)
        self.assertEqual(len(cells), 2)
        # replace source of original
        r2 = apply_replace_source(cells, cell_id="orig", source="replaced")
        self.assertTrue(r2["changed"])
        self.assertEqual(cells[0]["source"], "replaced")

    def test_insert_then_delete(self):
        cells = [_make_code_cell("a", cell_id="a")]
        apply_insert(cells, cell_type="code", source="b", at_index=-1)
        self.assertEqual(len(cells), 2)
        apply_delete(cells, index=1)
        self.assertEqual(len(cells), 1)
        self.assertEqual(cells[0]["id"], "a")


# ---------------------------------------------------------------------------
# Step 0: CLI ergonomics
# ---------------------------------------------------------------------------

class TestParserAliases(unittest.TestCase):
    def test_exec_alias(self):
        parser = build_parser()
        args = parser.parse_args(["exec", "demo.ipynb", "-p", "8899", "-c", "x=1"])
        self.assertEqual(args.command, "exec")
        self.assertEqual(args.path, "demo.ipynb")
        self.assertEqual(args.port, 8899)
        self.assertEqual(args.code, "x=1")

    def test_cat_alias(self):
        parser = build_parser()
        args = parser.parse_args(["cat", "demo.ipynb", "-p", "8899"])
        self.assertEqual(args.command, "cat")
        self.assertEqual(args.path, "demo.ipynb")

    def test_ls_alias(self):
        parser = build_parser()
        args = parser.parse_args(["ls", "-p", "8899"])
        self.assertEqual(args.command, "ls")
        self.assertEqual(args.port, 8899)

    def test_vars_alias(self):
        parser = build_parser()
        args = parser.parse_args(["vars", "demo.ipynb", "-p", "8899", "list"])
        self.assertEqual(args.command, "vars")
        self.assertEqual(args.path, "demo.ipynb")

    def test_ix_alias(self):
        parser = build_parser()
        args = parser.parse_args(["ix", "demo.ipynb", "-p", "8899", "-s", "x=1"])
        self.assertEqual(args.command, "ix")
        self.assertEqual(args.source, "x=1")


class TestPositionalPath(unittest.TestCase):
    def test_positional_path(self):
        parser = build_parser()
        args = parser.parse_args(["exec", "demo.ipynb", "-p", "8899", "-c", "x=1"])
        self.assertEqual(args.path, "demo.ipynb")

    def test_flag_path_backward_compat(self):
        parser = build_parser()
        args = parser.parse_args(["exec", "--path", "demo.ipynb", "-p", "8899", "-c", "x=1"])
        self.assertEqual(args.path_flag, "demo.ipynb")

    def test_resolve_positional_over_flag(self):
        parser = build_parser()
        args = parser.parse_args(["exec", "positional.ipynb", "-p", "8899", "-c", "x=1"])
        path = _resolve_path_arg(args)
        self.assertEqual(path, "positional.ipynb")

    def test_resolve_flag_when_no_positional(self):
        parser = build_parser()
        args = parser.parse_args(["exec", "--path", "flag.ipynb", "-p", "8899", "-c", "x=1"])
        path = _resolve_path_arg(args)
        self.assertEqual(path, "flag.ipynb")


class TestCompactDefault(unittest.TestCase):
    def test_default_is_compact(self):
        import io
        import sys
        buf = io.StringIO()
        old_stdout = sys.stdout
        sys.stdout = buf
        try:
            _print({"key": "value"})
        finally:
            sys.stdout = old_stdout
        output = buf.getvalue().strip()
        self.assertNotIn(" ", output)  # compact = no spaces
        self.assertEqual(json.loads(output), {"key": "value"})

    def test_pretty_flag(self):
        import io
        import sys
        buf = io.StringIO()
        old_stdout = sys.stdout
        sys.stdout = buf
        try:
            _print({"key": "value"}, pretty=True)
        finally:
            sys.stdout = old_stdout
        output = buf.getvalue().strip()
        self.assertIn("\n", output)  # pretty = indented


class TestShortFlags(unittest.TestCase):
    def test_short_port(self):
        parser = build_parser()
        args = parser.parse_args(["servers"])
        # servers doesn't have -p, just checking it parses
        self.assertEqual(args.command, "servers")

    def test_short_code(self):
        parser = build_parser()
        args = parser.parse_args(["exec", "demo.ipynb", "-c", "print(1)"])
        self.assertEqual(args.code, "print(1)")

    def test_short_source_on_edit(self):
        parser = build_parser()
        args = parser.parse_args(["edit", "demo.ipynb", "replace-source", "--cell-id", "abc", "-s", "x=1"])
        self.assertEqual(args.source, "x=1")

    def test_short_index_on_edit(self):
        parser = build_parser()
        args = parser.parse_args(["edit", "demo.ipynb", "replace-source", "-i", "0", "-s", "x=1"])
        self.assertEqual(args.index, 0)


# ---------------------------------------------------------------------------
# Step 5: New command parser
# ---------------------------------------------------------------------------

class TestNewCommandParser(unittest.TestCase):
    def test_new_defaults(self):
        parser = build_parser()
        args = parser.parse_args(["new", "test.ipynb", "-p", "8899"])
        self.assertEqual(args.command, "new")
        self.assertEqual(args.path, "test.ipynb")
        self.assertEqual(args.kernel_name, "python3")
        self.assertFalse(args.no_start_kernel)

    def test_new_with_kernel_name(self):
        parser = build_parser()
        args = parser.parse_args(["new", "test.ipynb", "-p", "8899", "--kernel-name", "julia-1.9"])
        self.assertEqual(args.kernel_name, "julia-1.9")


# ---------------------------------------------------------------------------
# Step 6: Insert-execute parser
# ---------------------------------------------------------------------------

class TestInsertExecuteParser(unittest.TestCase):
    def test_ix_defaults(self):
        parser = build_parser()
        args = parser.parse_args(["ix", "demo.ipynb", "-p", "8899", "-s", "x=1"])
        self.assertEqual(args.command, "ix")
        self.assertEqual(args.at_index, -1)
        self.assertEqual(args.cell_type, "code")

    def test_ix_with_at_index(self):
        parser = build_parser()
        args = parser.parse_args(["ix", "demo.ipynb", "-p", "8899", "-s", "x=1", "--at-index", "3"])
        self.assertEqual(args.at_index, 3)


# ---------------------------------------------------------------------------
# Step 7: Kernels parser
# ---------------------------------------------------------------------------

class TestKernelsParser(unittest.TestCase):
    def test_kernels_parser(self):
        parser = build_parser()
        args = parser.parse_args(["kernels", "-p", "8899"])
        self.assertEqual(args.command, "kernels")
        self.assertEqual(args.port, 8899)


# ---------------------------------------------------------------------------
# Contents detail modes parser
# ---------------------------------------------------------------------------

class TestContentsParser(unittest.TestCase):
    def test_detail_default_is_brief(self):
        parser = build_parser()
        args = parser.parse_args(["cat", "demo.ipynb", "-p", "8899"])
        self.assertEqual(args.detail, "brief")

    def test_detail_full(self):
        parser = build_parser()
        args = parser.parse_args(["cat", "demo.ipynb", "--detail", "full"])
        self.assertEqual(args.detail, "full")

    def test_cells_filter(self):
        parser = build_parser()
        args = parser.parse_args(["cat", "demo.ipynb", "--cells", "0,2,5"])
        self.assertEqual(args.cells, "0,2,5")

    def test_cell_type_filter(self):
        parser = build_parser()
        args = parser.parse_args(["cat", "demo.ipynb", "--cell-type", "code"])
        self.assertEqual(args.cell_type, "code")

    def test_include_outputs_alias(self):
        parser = build_parser()
        args = parser.parse_args(["cat", "demo.ipynb", "--include-outputs"])
        self.assertTrue(args.include_outputs)


# ---------------------------------------------------------------------------
# Batch edit parser
# ---------------------------------------------------------------------------

class TestBatchEditParser(unittest.TestCase):
    def test_batch_parser(self):
        parser = build_parser()
        ops = '[{"op":"insert","at_index":-1,"cell_type":"code","source":"x=1"}]'
        args = parser.parse_args(["edit", "demo.ipynb", "batch", "--operations", ops])
        self.assertEqual(args.edit_command, "batch")
        self.assertEqual(args.operations, ops)


# ---------------------------------------------------------------------------
# Existing functionality preserved
# ---------------------------------------------------------------------------

class TestSanitizeErrorText(unittest.TestCase):
    def test_redacts_token_in_url(self):
        msg = "ws://127.0.0.1:9999/api/kernels/id/channels?token=secret-token"
        redacted = _sanitize_error_text(msg, server_token="secret-token")
        self.assertNotIn("secret-token", redacted)
        self.assertIn("token=[REDACTED]", redacted)


class TestWsUrl(unittest.TestCase):
    def test_includes_token_and_session(self):
        server = _make_server(token="abc123")
        url = _ws_url(server, "kernel-1", session_id="session-42")
        self.assertIn("/api/kernels/kernel-1/channels?", url)
        self.assertIn("token=abc123", url)
        self.assertIn("session_id=session-42", url)


class TestBelongsToExecution(unittest.TestCase):
    def test_matches_parent_header(self):
        self.assertTrue(_belongs_to_execution({"parent_header": {"msg_id": "abc"}}, "abc"))
        self.assertFalse(_belongs_to_execution({"parent_header": {}}, "abc"))


class TestEventsToNotebookOutputs(unittest.TestCase):
    def test_stream_event(self):
        events = [{"type": "stream", "name": "stdout", "text": "hello"}]
        outputs, ec = events_to_notebook_outputs(events)
        self.assertEqual(len(outputs), 1)
        self.assertEqual(outputs[0]["output_type"], "stream")

    def test_execute_result_event(self):
        events = [
            {"type": "execute_input", "execution_count": 1, "code": "1+1"},
            {"type": "execute_result", "execution_count": 1, "data": {"text/plain": "2"}, "metadata": {}},
        ]
        outputs, ec = events_to_notebook_outputs(events)
        self.assertEqual(ec, 1)
        self.assertEqual(len(outputs), 1)
        self.assertEqual(outputs[0]["output_type"], "execute_result")


class TestResolveCellIndex(unittest.TestCase):
    def test_by_index(self):
        cells = [{"id": "a"}, {"id": "b"}]
        self.assertEqual(resolve_cell_index(cells, index=1, cell_id=None), 1)

    def test_by_cell_id(self):
        cells = [{"id": "a"}, {"id": "b"}]
        self.assertEqual(resolve_cell_index(cells, index=None, cell_id="b"), 1)

    def test_out_of_range(self):
        cells = [{"id": "a"}]
        with self.assertRaises(CommandError):
            resolve_cell_index(cells, index=5, cell_id=None)

    def test_no_match(self):
        cells = [{"id": "a"}]
        with self.assertRaises(CommandError):
            resolve_cell_index(cells, index=None, cell_id="nonexistent")

    def test_neither_provided(self):
        cells = [{"id": "a"}]
        with self.assertRaises(CommandError):
            resolve_cell_index(cells, index=None, cell_id=None)


class TestSummarizeChannelMessage(unittest.TestCase):
    def test_stream(self):
        msg = {"msg_type": "stream", "content": {"name": "stdout", "text": "hi"}}
        result = summarize_channel_message(msg)
        self.assertEqual(result["type"], "stream")
        self.assertEqual(result["text"], "hi")

    def test_execute_result(self):
        msg = {"msg_type": "execute_result", "content": {"execution_count": 1, "data": {"text/plain": "2"}}}
        result = summarize_channel_message(msg)
        self.assertEqual(result["type"], "execute_result")

    def test_unknown_returns_none(self):
        msg = {"msg_type": "comm_open", "content": {}}
        self.assertIsNone(summarize_channel_message(msg))


class TestAutoTransportSafety(unittest.TestCase):
    def test_will_not_retry_after_unsafe_websocket_failure(self):
        from agent_repl.execution import runner as runner_mod
        from agent_repl.execution import transport as transport_mod
        server = _make_server()
        session = {"id": "session-1", "path": NOTEBOOK_PATH, "kernel": {"id": "kernel-1"}}

        with (
            mock.patch.object(runner_mod, "_resolve_session", return_value=session),
            mock.patch.object(
                transport_mod,
                "execute_via_websocket",
                side_effect=TransportRetryUnsafeError("socket closed", request_sent=True),
            ),
            mock.patch.object(transport_mod, "execute_via_zmq") as mock_zmq,
        ):
            with self.assertRaises(CommandError) as ctx:
                runner_mod.execute_code(
                    server, path=NOTEBOOK_PATH, session_id=None, kernel_id=None,
                    code="print(1)", transport="auto", timeout=5,
                )
            mock_zmq.assert_not_called()
            self.assertIn("auto fallback was skipped", str(ctx.exception))


class TestExplicitServerAuth(unittest.TestCase):
    def test_requires_working_auth(self):
        from agent_repl.server import discovery as discovery_mod
        from agent_repl.core import ProbeResult
        srv = _make_server(token="bad-token")

        with (
            mock.patch.object(discovery_mod, "_running_server_infos", return_value=[srv]),
            mock.patch.object(
                discovery_mod, "probe_server",
                return_value=ProbeResult(reachable=True, auth_ok=False, error="forbidden"),
            ),
        ):
            with self.assertRaises(CommandError) as ctx:
                discovery_mod.select_server(server_url=None, port=9999, timeout=5)
            self.assertIn("authentication failed", str(ctx.exception))


# ---------------------------------------------------------------------------
# v2: Cell Directives (Step 4)
# ---------------------------------------------------------------------------

class TestDirectives(unittest.TestCase):
    def test_parse_agent_prompt_code_cell(self):
        from agent_repl.notebook.directives import parse_directives
        cell = _make_code_cell("#| agent: clean this data\ndf.head()")
        directives = parse_directives(cell)
        self.assertEqual(directives["agent"], ["clean this data"])

    def test_parse_agent_tags(self):
        from agent_repl.notebook.directives import extract_tags
        cell = _make_code_cell("#| agent-tags: critical, setup\nx = 1")
        tags = extract_tags(cell)
        self.assertEqual(tags, {"critical", "setup"})

    def test_parse_skip_directive(self):
        from agent_repl.notebook.directives import has_skip_directive
        cell = _make_code_cell("#| agent-skip\nx = 1")
        self.assertTrue(has_skip_directive(cell))

    def test_no_directives(self):
        from agent_repl.notebook.directives import parse_directives
        cell = _make_code_cell("x = 1")
        self.assertEqual(parse_directives(cell), {})

    def test_markdown_agent_prompt(self):
        from agent_repl.notebook.directives import has_agent_prompt, extract_prompt
        cell = _make_md_cell("<!-- agent: explain this -->")
        self.assertTrue(has_agent_prompt(cell))
        self.assertEqual(extract_prompt(cell), "explain this")

    def test_has_agent_prompt(self):
        from agent_repl.notebook.directives import has_agent_prompt
        self.assertTrue(has_agent_prompt(_make_code_cell("#| agent: do X\nx = 1")))
        self.assertFalse(has_agent_prompt(_make_code_cell("x = 1")))

    def test_is_response_cell(self):
        from agent_repl.notebook.directives import is_response_cell
        cell = _make_code_cell("x = 1")
        cell["metadata"] = {"agent-repl": {"responds_to": "abc"}}
        self.assertTrue(is_response_cell(cell, "abc"))
        self.assertFalse(is_response_cell(cell, "xyz"))


# ---------------------------------------------------------------------------
# v2: Prompts listing (Step 5)
# ---------------------------------------------------------------------------

class TestListPrompts(unittest.TestCase):
    def test_pending_prompt(self):
        from agent_repl.notebook.directives import list_prompts
        cells = [
            _make_code_cell("import pandas", cell_id="a"),
            _make_code_cell("#| agent: clean data\ndf.head()", cell_id="b"),
        ]
        prompts = list_prompts(cells, pending_only=True, context_cells=1)
        self.assertEqual(len(prompts), 1)
        self.assertEqual(prompts[0]["cell_id"], "b")
        self.assertEqual(prompts[0]["status"], "pending")
        self.assertEqual(prompts[0]["instruction"], "clean data")

    def test_answered_prompt_excluded(self):
        from agent_repl.notebook.directives import list_prompts
        cells = [
            _make_code_cell("#| agent: do X", cell_id="prompt"),
            _make_code_cell("result = 1", cell_id="response"),
        ]
        cells[1]["metadata"] = {"agent-repl": {"responds_to": "prompt"}}
        prompts = list_prompts(cells, pending_only=True, context_cells=0)
        self.assertEqual(len(prompts), 0)

    def test_answered_prompt_included_with_all(self):
        from agent_repl.notebook.directives import list_prompts
        cells = [
            _make_code_cell("#| agent: do X", cell_id="prompt"),
            _make_code_cell("result = 1", cell_id="response"),
        ]
        cells[1]["metadata"] = {"agent-repl": {"responds_to": "prompt"}}
        prompts = list_prompts(cells, pending_only=False, context_cells=0)
        self.assertEqual(len(prompts), 1)
        self.assertEqual(prompts[0]["status"], "answered")


# ---------------------------------------------------------------------------
# v2: Cell range parsing (Step 2)
# ---------------------------------------------------------------------------

class TestCellRangeParsing(unittest.TestCase):
    def test_simple_indexes(self):
        from agent_repl.notebook.contents import parse_cell_ranges
        self.assertEqual(parse_cell_ranges("0,2,5", 10), {0, 2, 5})

    def test_range(self):
        from agent_repl.notebook.contents import parse_cell_ranges
        self.assertEqual(parse_cell_ranges("0-2", 10), {0, 1, 2})

    def test_open_end_range(self):
        from agent_repl.notebook.contents import parse_cell_ranges
        self.assertEqual(parse_cell_ranges("7-", 10), {7, 8, 9})

    def test_mixed(self):
        from agent_repl.notebook.contents import parse_cell_ranges
        self.assertEqual(parse_cell_ranges("0-2,4,7-", 10), {0, 1, 2, 4, 7, 8, 9})

    def test_single(self):
        from agent_repl.notebook.contents import parse_cell_ranges
        self.assertEqual(parse_cell_ranges("3", 10), {3})

    def test_out_of_range_raises(self):
        from agent_repl.notebook.contents import parse_cell_ranges
        with self.assertRaises(CommandError):
            parse_cell_ranges("15", 10)


# ---------------------------------------------------------------------------
# v2: Output normalization (Step 3)
# ---------------------------------------------------------------------------

class TestOutputNormalization(unittest.TestCase):
    def test_strip_ansi(self):
        from agent_repl.output.normalize import strip_ansi
        self.assertEqual(strip_ansi("\x1b[31mred\x1b[0m"), "red")

    def test_clean_repr_ids(self):
        from agent_repl.output.normalize import clean_repr_ids
        self.assertEqual(clean_repr_ids("<Foo at 0x7f1234>"), "<Foo>")

    def test_normalize_cell_outputs(self):
        from agent_repl.output.normalize import normalize_cell_outputs
        cell = _make_code_cell("x = 1")
        cell["outputs"] = [
            {"output_type": "stream", "text": "\x1b[31mhello\x1b[0m"},
            {"output_type": "execute_result", "data": {"text/plain": "<Obj at 0xabc>", "application/vnd.google.colaboratory.intrinsic+json": {}}},
        ]
        normalize_cell_outputs(cell)
        self.assertEqual(cell["outputs"][0]["text"], "hello")
        self.assertEqual(cell["outputs"][1]["data"]["text/plain"], "<Obj>")
        self.assertNotIn("application/vnd.google.colaboratory.intrinsic+json", cell["outputs"][1]["data"])


# ---------------------------------------------------------------------------
# v2: Minimal detail level (Step 14)
# ---------------------------------------------------------------------------

class TestMinimalDetail(unittest.TestCase):
    def test_minimal_cell_summary(self):
        from agent_repl.notebook.cells import summarize_cell_minimal
        cell = _make_code_cell("line1\nline2\nline3", cell_id="abc")
        result = summarize_cell_minimal(cell, index=0)
        self.assertEqual(result["index"], 0)
        self.assertEqual(result["cell_id"], "abc")
        self.assertEqual(result["line_count"], 3)
        self.assertNotIn("source", result)
        self.assertNotIn("outputs", result)


# ---------------------------------------------------------------------------
# v2: Build cell with metadata (Step 8)
# ---------------------------------------------------------------------------

class TestBuildCellWithMetadata(unittest.TestCase):
    def test_metadata_attached(self):
        cell = build_cell("code", "x = 1", metadata={"agent-repl": {"responds_to": "abc"}})
        self.assertEqual(cell["metadata"]["agent-repl"]["responds_to"], "abc")

    def test_no_metadata_default(self):
        cell = build_cell("code", "x = 1")
        # metadata may exist from nbformat but shouldn't have agent-repl
        self.assertNotIn("agent-repl", cell.get("metadata", {}))


# ---------------------------------------------------------------------------
# v2: New parser commands (Steps 5, 6, 7, 9, 13)
# ---------------------------------------------------------------------------

class TestV2Parsers(unittest.TestCase):
    def test_start_parser(self):
        parser = build_parser()
        args = parser.parse_args(["start"])
        self.assertEqual(args.command, "start")

    def test_prompts_parser(self):
        parser = build_parser()
        args = parser.parse_args(["prompts", "demo.ipynb", "-p", "8899"])
        self.assertEqual(args.command, "prompts")
        self.assertEqual(args.path, "demo.ipynb")

    def test_respond_parser(self):
        parser = build_parser()
        args = parser.parse_args(["respond", "demo.ipynb", "-p", "8899", "--to", "abc", "-s", "x=1"])
        self.assertEqual(args.command, "respond")
        self.assertEqual(args.prompt_cell_id, "abc")

    def test_watch_parser(self):
        parser = build_parser()
        args = parser.parse_args(["watch", "demo.ipynb", "-p", "8899", "--once"])
        self.assertEqual(args.command, "watch")
        self.assertTrue(args.once)
        self.assertEqual(args.interval, 2.0)

    def test_context_parser(self):
        parser = build_parser()
        args = parser.parse_args(["context", "demo.ipynb", "-p", "8899"])
        self.assertEqual(args.command, "context")

    def test_clean_parser(self):
        parser = build_parser()
        args = parser.parse_args(["clean", "demo.ipynb", "-p", "8899"])
        self.assertEqual(args.command, "clean")

    def test_git_setup_parser(self):
        parser = build_parser()
        args = parser.parse_args(["git-setup"])
        self.assertEqual(args.command, "git-setup")

    def test_minimal_detail(self):
        parser = build_parser()
        args = parser.parse_args(["cat", "demo.ipynb", "--detail", "minimal"])
        self.assertEqual(args.detail, "minimal")


# ---------------------------------------------------------------------------
# v2: Git clean (Step 13)
# ---------------------------------------------------------------------------

class TestGitClean(unittest.TestCase):
    def test_clean_strips_outputs(self):
        from agent_repl.git import clean_notebook
        content = {
            "metadata": {"kernelspec": {"name": "python3"}},
            "nbformat": 4, "nbformat_minor": 5,
            "cells": [
                {"cell_type": "code", "source": "x = 1", "outputs": [{"output_type": "stream", "text": "1"}], "execution_count": 1, "metadata": {}},
                {"cell_type": "markdown", "source": "# Title", "metadata": {}},
            ],
        }
        cleaned = clean_notebook(content)
        self.assertEqual(cleaned["cells"][0]["outputs"], [])
        self.assertIsNone(cleaned["cells"][0]["execution_count"])
        # Original unchanged
        self.assertEqual(content["cells"][0]["outputs"][0]["text"], "1")

    def test_clean_preserves_agent_tags(self):
        from agent_repl.git import clean_notebook
        content = {
            "metadata": {}, "nbformat": 4, "nbformat_minor": 5,
            "cells": [
                {"cell_type": "code", "source": "x = 1", "outputs": [], "metadata": {"agent-repl": {"tags": ["critical"], "timestamp": 123}}},
            ],
        }
        cleaned = clean_notebook(content)
        self.assertEqual(cleaned["cells"][0]["metadata"]["agent-repl"]["tags"], ["critical"])
        self.assertNotIn("timestamp", cleaned["cells"][0]["metadata"]["agent-repl"])


# ---------------------------------------------------------------------------
# v2: Default port env var (Step 1)
# ---------------------------------------------------------------------------

class TestDefaultPort(unittest.TestCase):
    def test_env_var_port(self):
        import os
        old = os.environ.get("AGENT_REPL_PORT")
        try:
            os.environ["AGENT_REPL_PORT"] = "8899"
            parser = build_parser()
            args2 = parser.parse_args(["exec", "demo.ipynb", "-c", "x=1"])
            self.assertEqual(args2.port, 8899)
        finally:
            if old is None:
                os.environ.pop("AGENT_REPL_PORT", None)
            else:
                os.environ["AGENT_REPL_PORT"] = old


# ---------------------------------------------------------------------------
# v2: Execution filtering via directives (Step 10)
# ---------------------------------------------------------------------------

class TestExecutionFiltering(unittest.TestCase):
    def test_skip_directive(self):
        from agent_repl.execution.runner import _should_execute_cell
        cell = _make_code_cell("#| agent-skip\nx = 1")
        should, reason = _should_execute_cell(cell, skip_tags=None, only_tags=None)
        self.assertFalse(should)
        self.assertEqual(reason, "agent-skip")

    def test_skip_tags(self):
        from agent_repl.execution.runner import _should_execute_cell
        cell = _make_code_cell("#| agent-tags: setup\nx = 1")
        should, reason = _should_execute_cell(cell, skip_tags={"setup"}, only_tags=None)
        self.assertFalse(should)
        self.assertEqual(reason, "in-skip-tags")

    def test_only_tags_match(self):
        from agent_repl.execution.runner import _should_execute_cell
        cell = _make_code_cell("#| agent-tags: critical\nx = 1")
        should, reason = _should_execute_cell(cell, skip_tags=None, only_tags={"critical"})
        self.assertTrue(should)

    def test_only_tags_no_match(self):
        from agent_repl.execution.runner import _should_execute_cell
        cell = _make_code_cell("x = 1")
        should, reason = _should_execute_cell(cell, skip_tags=None, only_tags={"critical"})
        self.assertFalse(should)
        self.assertEqual(reason, "not-in-only-tags")

    def test_no_filters_runs(self):
        from agent_repl.execution.runner import _should_execute_cell
        cell = _make_code_cell("x = 1")
        should, reason = _should_execute_cell(cell, skip_tags=None, only_tags=None)
        self.assertTrue(should)


# ---------------------------------------------------------------------------
# v2: Streaming parser (Step 11)
# ---------------------------------------------------------------------------

class TestStreamParser(unittest.TestCase):
    def test_stream_flag(self):
        parser = build_parser()
        args = parser.parse_args(["exec", "demo.ipynb", "-c", "x=1", "--stream"])
        self.assertTrue(args.stream)

    def test_no_stream_default(self):
        parser = build_parser()
        args = parser.parse_args(["exec", "demo.ipynb", "-c", "x=1"])
        self.assertFalse(args.stream)


# ---------------------------------------------------------------------------
# v2: Tag filters parser (Step 10)
# ---------------------------------------------------------------------------

class TestTagFiltersParser(unittest.TestCase):
    def test_skip_tags(self):
        parser = build_parser()
        args = parser.parse_args(["run-all", "demo.ipynb", "--skip-tags", "setup,expensive"])
        self.assertEqual(args.skip_tags, "setup,expensive")

    def test_only_tags(self):
        parser = build_parser()
        args = parser.parse_args(["run-all", "demo.ipynb", "--only-tags", "critical"])
        self.assertEqual(args.only_tags, "critical")


# ---------------------------------------------------------------------------
# Bug fix: strip_media before saving notebook outputs
# ---------------------------------------------------------------------------

class TestExecuteCodeStripsMediaBeforeSave(unittest.TestCase):
    """Regression: execute_code must strip media BEFORE saving outputs to the notebook.

    Before the fix, strip_media ran after save_notebook_content — so 800KB+ HTML from
    widget-based cells (e.g. graph.visualize()) was written to the notebook file on
    every ix call. This bloated notebooks and created a fragile window where concurrent
    WebSocket connections were disrupted by the large PUT request.

    After the fix, strip_media runs first so only text/plain is persisted.
    """

    LARGE_HTML = "<html>" + "x" * 800_000 + "</html>"

    def _widget_result(self) -> ExecutionResult:
        return ExecutionResult(
            transport="websocket", kernel_id="kernel-1", session_id="session-1",
            path=NOTEBOOK_PATH,
            reply={"status": "ok", "execution_count": 1, "payload": [], "user_expressions": {}},
            events=[
                {"type": "execute_input", "execution_count": 1, "code": "graph.visualize()"},
                {"type": "execute_result", "execution_count": 1, "metadata": {}, "data": {
                    "text/html": self.LARGE_HTML,
                    "text/plain": "<ScrollablePipelineWidget>",
                }},
                {"type": "status", "execution_state": "idle"},
            ],
        )

    def _notebook(self, cell_id: str) -> dict:
        return {
            "last_modified": "2024-01-01T00:00:00Z",
            "content": {
                "cells": [{"id": cell_id, "cell_type": "code", "source": "graph.visualize()",
                            "outputs": [], "execution_count": None, "metadata": {}}],
                "metadata": {}, "nbformat": 4, "nbformat_minor": 5,
            },
        }

    def _run(self, *, strip_media: bool) -> list:
        from agent_repl.execution import runner as runner_mod
        saved: list = []

        def capture_save(server, path, content, *, timeout, **kwargs):
            for cell in content.get("cells", []):
                saved.extend(cell.get("outputs", []))

        with (
            mock.patch.object(runner_mod, "_execute_request", return_value=self._widget_result()),
            mock.patch.object(runner_mod, "load_notebook_model", return_value=self._notebook("cell-1")),
            mock.patch.object(runner_mod, "save_notebook_content", side_effect=capture_save),
        ):
            runner_mod.execute_code(
                _make_server(), path=NOTEBOOK_PATH, session_id="session-1", kernel_id="kernel-1",
                code="graph.visualize()", transport="websocket", timeout=10,
                save_outputs=True, cell_id="cell-1", strip_media=strip_media,
            )
        return saved

    def test_html_not_saved_when_strip_media_true(self):
        saved = self._run(strip_media=True)
        self.assertEqual(len(saved), 1)
        self.assertNotIn("text/html", saved[0]["data"],
                         "800KB HTML must not be written to notebook when strip_media=True")
        self.assertEqual(saved[0]["data"]["text/plain"], "<ScrollablePipelineWidget>")

    def test_html_saved_when_strip_media_false(self):
        saved = self._run(strip_media=False)
        self.assertEqual(len(saved), 1)
        self.assertIn("text/html", saved[0]["data"],
                      "HTML should be persisted to notebook when strip_media=False (raw mode)")


# ---------------------------------------------------------------------------
# Bug fix: WebSocket uses unique session_id per connection
# ---------------------------------------------------------------------------

class TestWebSocketUsesUniqueSessionId(unittest.TestCase):
    """Regression: execute_via_websocket used to pass the Jupyter session_id into
    the WebSocket URL. When two agent-repl processes connected to the same kernel
    with the same session_id, Jupyter stopped routing messages to the older connection,
    causing it to hang for ~45s until the OS dropped the TCP connection.

    After the fix, the WebSocket URL uses shell_session_id — a fresh UUID generated
    per call — so concurrent connections never share a session_id and Jupyter routes
    messages to each independently.
    """

    def test_ws_url_does_not_reuse_jupyter_session_id(self):
        from agent_repl.execution import transport as transport_mod
        from agent_repl.core import ExecuteRequest

        jupyter_session_id = "fixed-jupyter-session-abc123"
        ws_urls_seen: list[str] = []

        def fake_create_connection(url, **kwargs):
            ws_urls_seen.append(url)
            raise OSError("fake: connection refused")

        with (
            mock.patch.object(transport_mod, "ensure_kernel_idle"),
            mock.patch("websocket.create_connection", side_effect=fake_create_connection),
        ):
            with self.assertRaises(TransportRetryUnsafeError):
                transport_mod.execute_via_websocket(
                    _make_server(), kernel_id="kernel-1",
                    session_id=jupyter_session_id, path=NOTEBOOK_PATH,
                    request=ExecuteRequest(code="1+1"), timeout=5,
                )

        self.assertEqual(len(ws_urls_seen), 1)
        url = ws_urls_seen[0]
        self.assertIn("session_id=", url,
                      "WebSocket URL must include a session_id parameter")
        self.assertNotIn(jupyter_session_id, url,
                         "WebSocket URL must use a fresh unique session_id, not the "
                         "fixed Jupyter session_id — reusing it causes concurrent "
                         "connections to starve each other of kernel messages")


if __name__ == "__main__":
    unittest.main()
