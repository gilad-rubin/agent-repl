"""Tests for agent-repl bridge CLI."""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import sys
import threading
import tomllib
import unittest
from io import StringIO
from pathlib import Path
from unittest import mock

import requests

from agent_repl.cli import build_parser, main
from agent_repl.client import BridgeClient
from agent_repl.v2.client import DEFAULT_START_TIMEOUT, V2Client
from agent_repl.v2.server import CoreState, _handler_factory, _load_or_create_state


def _python_with_ipykernel() -> str:
    for candidate in filter(None, [shutil.which("python3"), shutil.which("python"), "/opt/miniconda3/bin/python3"]):
        probe = subprocess.run(
            [candidate, "-c", "import ipykernel"],
            capture_output=True,
            text=True,
        )
        if probe.returncode == 0:
            return candidate
    raise RuntimeError("No kernel-capable python executable was found for headless notebook tests")


# ---------------------------------------------------------------------------
# BridgeClient discovery
# ---------------------------------------------------------------------------

class TestBridgeDiscovery(unittest.TestCase):
    """BridgeClient.discover() scans runtime dir for connection files."""

    def test_discover_finds_healthy_bridge(self):
        info = json.dumps({
            "port": 12345,
            "token": "abc",
            "workspace_folders": ["/workspace"],
        })
        with (
            mock.patch("agent_repl.client.glob.glob", return_value=["/tmp/agent-repl-bridge-1.json"]),
            mock.patch("agent_repl.client.os.path.getmtime", return_value=1.0),
            mock.patch("agent_repl.client.os.getcwd", return_value="/workspace"),
            mock.patch("agent_repl.client.Path.read_text", return_value=info),
            mock.patch.object(BridgeClient, "health", return_value={"status": "ok"}),
        ):
            client = BridgeClient.discover()
        self.assertEqual(client.base_url, "http://127.0.0.1:12345")
        self.assertEqual(client.token, "abc")

    def test_discover_matches_workspace_hint(self):
        infos = [
            json.dumps({
                "port": 11111,
                "token": "nope",
                "workspace_folders": ["/other"],
            }),
            json.dumps({
                "port": 22222,
                "token": "yes",
                "workspace_folders": ["/project"],
            }),
        ]
        with (
            mock.patch("agent_repl.client.glob.glob", return_value=["/tmp/1.json", "/tmp/2.json"]),
            mock.patch("agent_repl.client.os.path.getmtime", side_effect=[2.0, 1.0]),
            mock.patch("agent_repl.client.os.getcwd", return_value="/outside"),
            mock.patch("agent_repl.client.Path.read_text", side_effect=infos),
            mock.patch.object(BridgeClient, "health", return_value={"status": "ok", "open_notebooks": []}),
        ):
            client = BridgeClient.discover(workspace_hint="/project/notebooks/demo.ipynb")
        self.assertEqual(client.base_url, "http://127.0.0.1:22222")
        self.assertEqual(client.token, "yes")

    def test_discover_raises_when_no_workspace_matches(self):
        infos = [
            json.dumps({
                "port": 11111,
                "token": "a",
                "workspace_folders": ["/other"],
            }),
            json.dumps({
                "port": 22222,
                "token": "b",
                "workspace_folders": ["/elsewhere"],
            }),
        ]
        with (
            mock.patch("agent_repl.client.glob.glob", return_value=["/tmp/1.json", "/tmp/2.json"]),
            mock.patch("agent_repl.client.os.path.getmtime", side_effect=[2.0, 1.0]),
            mock.patch("agent_repl.client.os.getcwd", return_value="/workspace"),
            mock.patch("agent_repl.client.Path.read_text", side_effect=infos),
            mock.patch.object(BridgeClient, "health", return_value={"status": "ok", "open_notebooks": []}),
        ):
            with self.assertRaisesRegex(RuntimeError, "No running agent-repl bridge matched '/workspace' or cwd '/workspace'"):
                BridgeClient.discover()

    def test_discover_matches_open_notebook_hint(self):
        info = json.dumps({
            "port": 12345,
            "token": "abc",
            "workspace_folders": ["/other"],
        })
        with (
            mock.patch("agent_repl.client.glob.glob", return_value=["/tmp/agent-repl-bridge-1.json"]),
            mock.patch("agent_repl.client.os.path.getmtime", return_value=1.0),
            mock.patch("agent_repl.client.os.getcwd", return_value="/workspace"),
            mock.patch("agent_repl.client.Path.read_text", return_value=info),
            mock.patch.object(BridgeClient, "health", return_value={
                "status": "ok",
                "open_notebooks": ["/tmp/demo.ipynb"],
            }),
        ):
            client = BridgeClient.discover(workspace_hint="/tmp/demo.ipynb")
        self.assertEqual(client.base_url, "http://127.0.0.1:12345")

    def test_discover_raises_when_no_bridge(self):
        with mock.patch("agent_repl.client.glob.glob", return_value=[]):
            with self.assertRaises(RuntimeError):
                BridgeClient.discover()


class TestV2Discovery(unittest.TestCase):
    """V2Client.discover() scans runtime dir for workspace daemons."""

    def test_discover_finds_matching_workspace(self):
        info = json.dumps({
            "pid": 123,
            "port": 23456,
            "token": "tok",
            "workspace_root": "/workspace",
        })
        with (
            mock.patch("agent_repl.v2.client.glob.glob", return_value=["/tmp/agent-repl-v2-core-1.json"]),
            mock.patch("agent_repl.v2.client.os.path.getmtime", return_value=1.0),
            mock.patch("agent_repl.v2.client.os.getcwd", return_value="/workspace"),
            mock.patch("agent_repl.v2.client.Path.read_text", return_value=info),
            mock.patch("agent_repl.v2.client._pid_alive", return_value=True),
            mock.patch.object(V2Client, "health", return_value={"status": "ok"}),
        ):
            client = V2Client.discover()
        self.assertEqual(client.base_url, "http://127.0.0.1:23456")
        self.assertEqual(client.token, "tok")

    def test_discover_raises_when_no_workspace_matches(self):
        info = json.dumps({
            "pid": 123,
            "port": 23456,
            "token": "tok",
            "workspace_root": "/other",
        })
        with (
            mock.patch("agent_repl.v2.client.glob.glob", return_value=["/tmp/agent-repl-v2-core-1.json"]),
            mock.patch("agent_repl.v2.client.os.path.getmtime", return_value=1.0),
            mock.patch("agent_repl.v2.client.os.getcwd", return_value="/workspace"),
            mock.patch("agent_repl.v2.client.Path.read_text", return_value=info),
            mock.patch("agent_repl.v2.client._pid_alive", return_value=True),
        ):
            with self.assertRaisesRegex(RuntimeError, "No running agent-repl v2 core daemon matched '/workspace'"):
                V2Client.discover()

    def test_discover_prefers_most_specific_workspace_match(self):
        info_parent = json.dumps({
            "pid": 123,
            "port": 23456,
            "token": "tok-parent",
            "workspace_root": "/workspace",
        })
        info_child = json.dumps({
            "pid": 456,
            "port": 34567,
            "token": "tok-child",
            "workspace_root": "/workspace/subproject",
        })
        read_map = {
            "/tmp/agent-repl-v2-core-parent.json": info_parent,
            "/tmp/agent-repl-v2-core-child.json": info_child,
        }
        mtime_map = {
            "/tmp/agent-repl-v2-core-parent.json": 20.0,
            "/tmp/agent-repl-v2-core-child.json": 10.0,
        }
        with (
            mock.patch(
                "agent_repl.v2.client.glob.glob",
                return_value=["/tmp/agent-repl-v2-core-parent.json", "/tmp/agent-repl-v2-core-child.json"],
            ),
            mock.patch("agent_repl.v2.client.os.path.getmtime", side_effect=lambda path: mtime_map[path]),
            mock.patch("agent_repl.v2.client.os.getcwd", return_value="/workspace/subproject"),
            mock.patch("agent_repl.v2.client.Path.read_text", autospec=True, side_effect=lambda self: read_map[str(self)]),
            mock.patch("agent_repl.v2.client._pid_alive", return_value=True),
            mock.patch.object(V2Client, "health", return_value={"status": "ok"}),
        ):
            client = V2Client.discover()
        self.assertEqual(client.base_url, "http://127.0.0.1:34567")
        self.assertEqual(client.token, "tok-child")

    def test_attach_starts_or_reuses_daemon_then_session(self):
        with (
            mock.patch.object(V2Client, "start", return_value={"status": "ok", "workspace_root": "/workspace", "already_running": True}),
            mock.patch.object(V2Client, "discover") as mock_discover,
        ):
            attached_client = mock.MagicMock(spec=V2Client)
            attached_client.start_session.return_value = {
                "status": "ok",
                "session": {"session_id": "sess-1", "status": "attached"},
            }
            mock_discover.return_value = attached_client
            result = V2Client.attach(
                "/workspace",
                actor="agent",
                client="cli",
                label="worker",
                capabilities=["projection", "ops"],
                session_id="sess-1",
            )
        self.assertTrue(result["attached"])
        self.assertEqual(result["session"]["session_id"], "sess-1")
        attached_client.start_session.assert_called_once_with(
            actor="agent",
            client="cli",
            label="worker",
            capabilities=["projection", "ops"],
            session_id="sess-1",
        )


class TestV2CoreState(unittest.TestCase):
    """Direct tests for v2 core document/file sync behavior."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.workspace_root = Path(self.tmpdir.name)
        self.runtime_dir = self.workspace_root / "runtime"
        self.runtime_dir.mkdir()
        self.doc_path = self.workspace_root / "notebooks" / "demo.ipynb"
        self.doc_path.parent.mkdir()
        self.doc_path.write_text('{"cells": []}\n')
        self.state = CoreState(
            workspace_root=str(self.workspace_root),
            runtime_dir=str(self.runtime_dir),
            token="tok",
            pid=1234,
            started_at=1.0,
        )

    def tearDown(self):
        self.state.shutdown_headless_runtimes()
        self.tmpdir.cleanup()

    def test_open_document_captures_bound_file_snapshot(self):
        body, status = self.state.open_document("notebooks/demo.ipynb")
        self.assertEqual(status, 200)
        document = body["document"]
        self.assertEqual(document["relative_path"], "notebooks/demo.ipynb")
        self.assertEqual(document["file_format"], "ipynb")
        self.assertEqual(document["sync_state"], "in-sync")
        self.assertTrue(document["bound_snapshot"]["exists"])
        self.assertEqual(document["bound_snapshot"]["sha256"], document["observed_snapshot"]["sha256"])

    def test_refresh_document_reports_external_change_until_rebound(self):
        body, status = self.state.open_document("notebooks/demo.ipynb")
        self.assertEqual(status, 200)
        document_id = body["document"]["document_id"]

        self.doc_path.write_text('{"cells": [{"cell_type": "markdown"}]}\n')
        refresh_body, refresh_status = self.state.refresh_document(document_id)
        self.assertEqual(refresh_status, 200)
        refreshed = refresh_body["document"]
        self.assertEqual(refreshed["sync_state"], "external-change")
        self.assertNotEqual(refreshed["bound_snapshot"]["sha256"], refreshed["observed_snapshot"]["sha256"])

        rebind_body, rebind_status = self.state.rebind_document(document_id)
        self.assertEqual(rebind_status, 200)
        rebound = rebind_body["document"]
        self.assertEqual(rebound["sync_state"], "in-sync")
        self.assertEqual(rebound["bound_snapshot"]["sha256"], rebound["observed_snapshot"]["sha256"])

    def test_refresh_document_prefers_live_notebook_snapshot_over_stale_disk_state(self):
        live_bound = {
            "exists": True,
            "source_kind": "bridge-live",
            "size_bytes": 100,
            "sha256": "live-bound",
            "observed_at": 1.0,
            "cell_count": 2,
        }
        live_changed = {
            "exists": True,
            "source_kind": "bridge-live",
            "size_bytes": 140,
            "sha256": "live-changed",
            "observed_at": 2.0,
            "cell_count": 3,
        }
        file_snapshot = {
            "exists": True,
            "size_bytes": 11,
            "mtime": 1.0,
            "sha256": "disk-stale",
            "observed_at": 1.0,
        }

        with (
            mock.patch("agent_repl.v2.server._snapshot_live_document", side_effect=[live_bound, live_changed]),
            mock.patch("agent_repl.v2.server._snapshot_file", return_value=file_snapshot),
        ):
            body, status = self.state.open_document("notebooks/demo.ipynb")
            self.assertEqual(status, 200)
            document = body["document"]
            self.assertEqual(document["bound_snapshot"]["sha256"], "live-bound")
            self.assertEqual(document["sync_state"], "in-sync")

            refresh_body, refresh_status = self.state.refresh_document(document["document_id"])
            self.assertEqual(refresh_status, 200)
            refreshed = refresh_body["document"]
            self.assertEqual(refreshed["observed_snapshot"]["sha256"], "live-changed")
            self.assertEqual(refreshed["sync_state"], "external-change")

    def test_refresh_document_reports_missing_file(self):
        body, status = self.state.open_document("notebooks/demo.ipynb")
        self.assertEqual(status, 200)
        document_id = body["document"]["document_id"]

        self.doc_path.unlink()
        refresh_body, refresh_status = self.state.refresh_document(document_id)
        self.assertEqual(refresh_status, 200)
        refreshed = refresh_body["document"]
        self.assertEqual(refreshed["sync_state"], "missing")
        self.assertFalse(refreshed["observed_snapshot"]["exists"])

    def test_persist_serializes_concurrent_writes(self):
        started = threading.Event()
        release = threading.Event()
        overlap_detected = threading.Event()
        active_writers = 0
        active_lock = threading.Lock()
        original_write_text = Path.write_text

        def guarded_write_text(path_obj, *args, **kwargs):
            nonlocal active_writers
            if str(path_obj).endswith(".tmp"):
                with active_lock:
                    active_writers += 1
                    if active_writers > 1:
                        overlap_detected.set()
                    started.set()
                release.wait(timeout=2)
                try:
                    return original_write_text(path_obj, *args, **kwargs)
                finally:
                    with active_lock:
                        active_writers -= 1
            return original_write_text(path_obj, *args, **kwargs)

        first = threading.Thread(target=self.state.persist)
        second = threading.Thread(target=self.state.persist)

        with mock.patch("pathlib.Path.write_text", new=guarded_write_text):
            first.start()
            self.assertTrue(started.wait(timeout=1))
            second.start()
            release.set()
            first.join(timeout=2)
            second.join(timeout=2)

        self.assertFalse(overlap_detected.is_set())

    def test_notebook_contents_proxies_through_bridge_and_syncs_document_record(self):
        bridge = mock.Mock(spec=BridgeClient)
        bridge.contents.return_value = {
            "path": "notebooks/demo.ipynb",
            "cells": [{"index": 0, "cell_id": "cell-1", "cell_type": "code", "source": "x = 1"}],
        }

        with mock.patch.object(self.state, "_projection_client", return_value=bridge):
            body, status = self.state.notebook_contents("notebooks/demo.ipynb")

        self.assertEqual(status, 200)
        self.assertEqual(body["cells"][0]["cell_id"], "cell-1")
        bridge.contents.assert_called_once_with("notebooks/demo.ipynb")
        self.assertEqual(len(self.state.document_records), 1)
        record = next(iter(self.state.document_records.values()))
        self.assertEqual(record.relative_path, "notebooks/demo.ipynb")
        self.assertEqual(record.sync_state, "in-sync")

    def test_notebook_create_proxies_through_bridge_and_registers_document(self):
        bridge = mock.Mock(spec=BridgeClient)
        bridge.create.return_value = {
            "status": "ok",
            "path": "notebooks/demo.ipynb",
            "kernel_status": "selected",
        }

        with mock.patch.object(self.state, "_projection_client", return_value=bridge):
            body, status = self.state.notebook_create(
                "notebooks/demo.ipynb",
                cells=[{"type": "code", "source": "x = 1"}],
                kernel_id="subtext-venv",
            )

        self.assertEqual(status, 200)
        self.assertEqual(body["kernel_status"], "selected")
        bridge.create.assert_called_once_with(
            "notebooks/demo.ipynb",
            cells=[{"type": "code", "source": "x = 1"}],
            kernel_id="subtext-venv",
        )
        self.assertEqual(len(self.state.document_records), 1)

    def test_notebook_edit_proxies_through_bridge_and_syncs_document_record(self):
        bridge = mock.Mock(spec=BridgeClient)
        bridge.edit.return_value = {"path": "notebooks/demo.ipynb", "results": [{"op": "replace-source", "changed": True}]}

        with mock.patch.object(self.state, "_projection_client", return_value=bridge):
            body, status = self.state.notebook_edit(
                "notebooks/demo.ipynb",
                [{"op": "replace-source", "cell_id": "cell-1", "source": "x = 2"}],
            )

        self.assertEqual(status, 200)
        self.assertEqual(body["results"][0]["op"], "replace-source")
        bridge.edit.assert_called_once_with(
            "notebooks/demo.ipynb",
            [{"op": "replace-source", "cell_id": "cell-1", "source": "x = 2"}],
        )
        self.assertEqual(len(self.state.document_records), 1)

    def test_notebook_execute_cell_proxies_wait_false(self):
        bridge = mock.Mock(spec=BridgeClient)
        bridge.execute_cell.return_value = {"status": "started", "execution_id": "exec-1", "cell_id": "cell-1"}

        with mock.patch.object(self.state, "_projection_client", return_value=bridge):
            body, status = self.state.notebook_execute_cell(
                "notebooks/demo.ipynb",
                cell_id="cell-1",
                cell_index=None,
            )

        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "started")
        bridge.execute_cell.assert_called_once_with(
            "notebooks/demo.ipynb",
            cell_id="cell-1",
            cell_index=None,
            wait=False,
        )

    def test_headless_notebook_round_trip_without_projection_client(self):
        notebook_path = "notebooks/headless.ipynb"
        kernel_python = _python_with_ipykernel()

        with mock.patch.object(self.state, "_projection_client", return_value=None):
            create_body, create_status = self.state.notebook_create(
                notebook_path,
                cells=None,
                kernel_id=kernel_python,
            )
            self.assertEqual(create_status, 200)
            self.assertEqual(create_body["kernel_status"], "selected")
            self.assertTrue(create_body["ready"])
            self.assertEqual(create_body["mode"], "headless")

            insert_body, insert_status = self.state.notebook_insert_execute(
                notebook_path,
                source="x = 2\nx * 3",
                cell_type="code",
                at_index=-1,
            )
            self.assertEqual(insert_status, 200)
            self.assertEqual(insert_body["status"], "ok")
            self.assertEqual(insert_body["execution_mode"], "headless-runtime")
            self.assertEqual(insert_body["outputs"][0]["data"]["text/plain"], "6")

            contents_body, contents_status = self.state.notebook_contents(notebook_path)
            self.assertEqual(contents_status, 200)
            self.assertEqual(len(contents_body["cells"]), 1)
            cell_id = contents_body["cells"][0]["cell_id"]
            self.assertEqual(contents_body["cells"][0]["outputs"][0]["data"]["text/plain"], "6")

            edit_body, edit_status = self.state.notebook_edit(
                notebook_path,
                [{"op": "replace-source", "cell_id": cell_id, "source": "x = 7\nx ** 2"}],
            )
            self.assertEqual(edit_status, 200)
            self.assertTrue(edit_body["results"][0]["changed"])

            edited_body, edited_status = self.state.notebook_contents(notebook_path)
            self.assertEqual(edited_status, 200)
            self.assertEqual(edited_body["cells"][0]["source"], "x = 7\nx ** 2")
            self.assertEqual(edited_body["cells"][0]["outputs"], [])

            exec_body, exec_status = self.state.notebook_execute_cell(
                notebook_path,
                cell_id=cell_id,
                cell_index=None,
            )
            self.assertEqual(exec_status, 200)
            self.assertEqual(exec_body["status"], "ok")
            self.assertEqual(exec_body["outputs"][0]["data"]["text/plain"], "49")

            status_body, status_status = self.state.notebook_status(notebook_path)
            self.assertEqual(status_status, 200)
            self.assertFalse(status_body["open"])
            self.assertEqual(status_body["kernel_state"], "idle")

            reopened_body, reopened_status = self.state.notebook_contents(notebook_path)
            self.assertEqual(reopened_status, 200)
            self.assertEqual(reopened_body["cells"][0]["source"], "x = 7\nx ** 2")
            self.assertEqual(reopened_body["cells"][0]["outputs"][0]["data"]["text/plain"], "49")

    def test_headless_notebook_create_requires_explicit_kernel_when_workspace_has_no_venv(self):
        with mock.patch.object(self.state, "_projection_client", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "No workspace \\.venv kernel was detected"):
                self.state.notebook_create("notebooks/no-kernel.ipynb", cells=None, kernel_id=None)

    def test_headless_notebook_create_rejects_python_without_ipykernel(self):
        with mock.patch.object(self.state, "_projection_client", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "is not kernel-capable"):
                self.state.notebook_create("notebooks/no-ipykernel.ipynb", cells=None, kernel_id=sys.executable)

    def test_headless_restart_and_run_all_without_projection_client(self):
        notebook_path = "notebooks/headless-restart.ipynb"
        kernel_python = _python_with_ipykernel()

        with mock.patch.object(self.state, "_projection_client", return_value=None):
            create_body, create_status = self.state.notebook_create(
                notebook_path,
                cells=[{"type": "code", "source": "x = 5\nx"}],
                kernel_id=kernel_python,
            )
            self.assertEqual(create_status, 200)
            self.assertTrue(create_body["ready"])

            restart_body, restart_status = self.state.notebook_restart_and_run_all(notebook_path)
            self.assertEqual(restart_status, 200)
            self.assertEqual(restart_body["status"], "ok")
            self.assertEqual(restart_body["mode"], "headless")
            self.assertEqual(restart_body["restart"]["kernel_state"], "idle")
            self.assertEqual(len(restart_body["executions"]), 1)
            self.assertEqual(restart_body["executions"][0]["outputs"][0]["data"]["text/plain"], "5")

            status_body, status_status = self.state.notebook_status(notebook_path)
            self.assertEqual(status_status, 200)
            self.assertFalse(status_body["open"])
            self.assertEqual(status_body["kernel_state"], "idle")

    def test_session_can_detach_touch_and_resume(self):
        created = self.state.start_session("agent", "cli", "worker", "sess-1")
        self.assertEqual(created["session"]["status"], "attached")
        self.assertEqual(created["session"]["resume_count"], 0)
        self.assertEqual(created["session"]["capabilities"], ["projection", "ops", "automation"])

        detached_body, detached_status = self.state.detach_session("sess-1")
        self.assertEqual(detached_status, 200)
        self.assertEqual(detached_body["session"]["status"], "detached")

        touched_body, touched_status = self.state.touch_session("sess-1")
        self.assertEqual(touched_status, 200)
        self.assertEqual(touched_body["session"]["status"], "attached")

        resumed = self.state.start_session("agent", "cli", "worker", "sess-1", ["projection", "ops"])
        self.assertFalse(resumed["created"])
        self.assertEqual(resumed["session"]["resume_count"], 1)
        self.assertEqual(resumed["session"]["capabilities"], ["projection", "ops"])

    def test_list_sessions_marks_stale_attachments(self):
        self.state.start_session("human", "vscode", "editor", "sess-1")
        self.state.session_records["sess-1"].last_seen_at -= 120
        payload = self.state.list_sessions_payload()
        self.assertEqual(payload["sessions"][0]["status"], "stale")

    def test_branch_can_be_owned_and_finished(self):
        session = self.state.start_session("agent", "cli", "worker", "sess-1")
        opened, status = self.state.open_document("notebooks/demo.ipynb")
        self.assertEqual(status, 200)

        branch_body, branch_status = self.state.start_branch(
            branch_id="branch-1",
            document_id=opened["document"]["document_id"],
            owner_session_id=session["session"]["session_id"],
            parent_branch_id=None,
            title="Agent experiment",
            purpose="Explore risky changes",
        )
        self.assertEqual(branch_status, 200)
        branch = branch_body["branch"]
        self.assertEqual(branch["owner_session_id"], "sess-1")
        self.assertEqual(branch["status"], "active")

        finished_body, finished_status = self.state.finish_branch("branch-1", "merged")
        self.assertEqual(finished_status, 200)
        self.assertEqual(finished_body["branch"]["status"], "merged")

    def test_finish_run_keeps_runtime_busy_while_another_run_is_active(self):
        opened, status = self.state.open_document("notebooks/demo.ipynb")
        self.assertEqual(status, 200)
        document_id = opened["document"]["document_id"]
        runtime = self.state.start_runtime(runtime_id="rt-1", mode="shared", label=None, environment=None)
        self.assertEqual(runtime["runtime"]["status"], "ready")

        first_body, first_status = self.state.start_run(
            run_id="run-1",
            runtime_id="rt-1",
            target_type="document",
            target_ref=document_id,
            kind="execute",
        )
        self.assertEqual(first_status, 200)
        second_body, second_status = self.state.start_run(
            run_id="run-2",
            runtime_id="rt-1",
            target_type="document",
            target_ref=document_id,
            kind="execute",
        )
        self.assertEqual(second_status, 200)

        finish_body, finish_status = self.state.finish_run(first_body["run"]["run_id"], "completed")
        self.assertEqual(finish_status, 200)
        self.assertEqual(self.state.runtime_records["rt-1"].status, "busy")

        final_body, final_status = self.state.finish_run(second_body["run"]["run_id"], "completed")
        self.assertEqual(final_status, 200)
        self.assertEqual(self.state.runtime_records["rt-1"].status, "ready")

    def test_start_run_rejects_unknown_document_and_branch_targets(self):
        self.state.start_runtime(runtime_id="rt-1", mode="shared", label=None, environment=None)
        bad_document_body, bad_document_status = self.state.start_run(
            run_id="run-doc",
            runtime_id="rt-1",
            target_type="document",
            target_ref="doc-missing",
            kind="execute",
        )
        self.assertEqual(bad_document_status, 400)
        self.assertIn("Unknown document target_ref", bad_document_body["error"])

        bad_branch_body, bad_branch_status = self.state.start_run(
            run_id="run-branch",
            runtime_id="rt-1",
            target_type="branch",
            target_ref="branch-missing",
            kind="execute",
        )
        self.assertEqual(bad_branch_status, 400)
        self.assertIn("Unknown branch target_ref", bad_branch_body["error"])

    def test_load_or_create_state_restores_persisted_records_with_recovery_normalization(self):
        session = self.state.start_session("agent", "cli", "worker", "sess-1")
        opened, status = self.state.open_document("notebooks/demo.ipynb")
        self.assertEqual(status, 200)
        branch_body, branch_status = self.state.start_branch(
            branch_id="branch-1",
            document_id=opened["document"]["document_id"],
            owner_session_id=session["session"]["session_id"],
            parent_branch_id=None,
            title="Experiment",
            purpose="Persist me",
        )
        self.assertEqual(branch_status, 200)
        self.state.start_runtime(runtime_id="rt-1", mode="shared", label="primary", environment=None)
        run_body, run_status = self.state.start_run(
            run_id="run-1",
            runtime_id="rt-1",
            target_type="document",
            target_ref=opened["document"]["document_id"],
            kind="execute",
        )
        self.assertEqual(run_status, 200)

        restored = _load_or_create_state(
            workspace_root=str(self.workspace_root),
            runtime_dir=str(self.runtime_dir),
            token="tok-2",
            pid=9999,
            started_at=2.0,
        )
        self.assertIn("sess-1", restored.session_records)
        self.assertEqual(restored.session_records["sess-1"].status, "detached")
        self.assertIn(opened["document"]["document_id"], restored.document_records)
        self.assertIn(branch_body["branch"]["branch_id"], restored.branch_records)
        self.assertEqual(restored.runtime_records["rt-1"].status, "recovery-needed")
        self.assertEqual(restored.run_records[run_body["run"]["run_id"]].status, "interrupted")
        self.assertTrue(Path(restored.state_file).exists())


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

    def test_execution_calls_get(self):
        self.client.execution("exec-1")
        url = self.mock_get.call_args[0][0]
        self.assertIn("/api/notebook/execution", url)
        self.assertEqual(self.mock_get.call_args.kwargs["params"], {"id": "exec-1"})

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

    def test_select_kernel_includes_interactive(self):
        self.client.select_kernel("nb.ipynb", interactive=True)
        self.assertEqual(
            self.mock_post.call_args.kwargs["json"],
            {"path": "nb.ipynb", "cwd": "/workspace", "interactive": True},
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

    def test_post_surfaces_bridge_error_message(self):
        response = mock.Mock(status_code=500, reason="Internal Server Error", url="http://127.0.0.1:9999/api/notebook/execute-cell")
        response.json.return_value = {"error": 'Could NOT open editor for "vscode-notebook-cell:demo"'}
        response.raise_for_status.side_effect = requests.HTTPError("500 Server Error", response=response)
        self.mock_post.return_value = response

        with self.assertRaisesRegex(RuntimeError, 'Could NOT open editor for "vscode-notebook-cell:demo"'):
            self.client.execute_cell("nb.ipynb", cell_id="abc", wait=False)

    def test_auth_header_set(self):
        self.assertEqual(self.client._session.headers["Authorization"], "token tok")


class TestV2Endpoints(unittest.TestCase):
    """V2Client methods call correct HTTP endpoints."""

    def setUp(self):
        self.client = V2Client("http://127.0.0.1:9998", "tok")
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

    def test_health_calls_get(self):
        self.client.health()
        url = self.mock_get.call_args[0][0]
        self.assertIn("/api/health", url)

    def test_status_calls_get(self):
        self.client.status()
        url = self.mock_get.call_args[0][0]
        self.assertIn("/api/status", url)

    def test_shutdown_calls_post(self):
        self.client.shutdown()
        url = self.mock_post.call_args[0][0]
        self.assertIn("/api/shutdown", url)

    def test_list_sessions_calls_get(self):
        self.client.list_sessions()
        url = self.mock_get.call_args[0][0]
        self.assertIn("/api/sessions", url)

    def test_start_session_calls_post(self):
        self.client.start_session(actor="agent", client="cli", label="worker", capabilities=["projection"])
        url = self.mock_post.call_args[0][0]
        self.assertIn("/api/sessions/start", url)
        payload = self.mock_post.call_args.kwargs["json"]
        self.assertEqual(payload["actor"], "agent")
        self.assertEqual(payload["client"], "cli")
        self.assertEqual(payload["label"], "worker")
        self.assertEqual(payload["capabilities"], ["projection"])
        self.assertIn("session_id", payload)

    def test_touch_session_calls_post(self):
        self.client.touch_session("sess-1")
        url = self.mock_post.call_args[0][0]
        self.assertIn("/api/sessions/touch", url)
        self.assertEqual(self.mock_post.call_args.kwargs["json"], {"session_id": "sess-1"})

    def test_detach_session_calls_post(self):
        self.client.detach_session("sess-1")
        url = self.mock_post.call_args[0][0]
        self.assertIn("/api/sessions/detach", url)
        self.assertEqual(self.mock_post.call_args.kwargs["json"], {"session_id": "sess-1"})

    def test_end_session_calls_post(self):
        self.client.end_session("sess-1")
        url = self.mock_post.call_args[0][0]
        self.assertIn("/api/sessions/end", url)
        self.assertEqual(self.mock_post.call_args.kwargs["json"], {"session_id": "sess-1"})

    def test_list_documents_calls_get(self):
        self.client.list_documents()
        url = self.mock_get.call_args[0][0]
        self.assertIn("/api/documents", url)

    def test_open_document_calls_post(self):
        self.client.open_document("notebooks/demo.ipynb")
        url = self.mock_post.call_args[0][0]
        self.assertIn("/api/documents/open", url)
        self.assertEqual(self.mock_post.call_args.kwargs["json"], {"path": "notebooks/demo.ipynb"})

    def test_refresh_document_calls_post(self):
        self.client.refresh_document("doc-1")
        url = self.mock_post.call_args[0][0]
        self.assertIn("/api/documents/refresh", url)
        self.assertEqual(self.mock_post.call_args.kwargs["json"], {"document_id": "doc-1"})

    def test_rebind_document_calls_post(self):
        self.client.rebind_document("doc-1")
        url = self.mock_post.call_args[0][0]
        self.assertIn("/api/documents/rebind", url)
        self.assertEqual(self.mock_post.call_args.kwargs["json"], {"document_id": "doc-1"})

    def test_notebook_contents_calls_post(self):
        self.client.notebook_contents("nb.ipynb")
        url = self.mock_post.call_args[0][0]
        self.assertIn("/api/notebooks/contents", url)
        self.assertEqual(self.mock_post.call_args.kwargs["json"], {"path": "nb.ipynb"})

    def test_notebook_status_calls_post(self):
        self.client.notebook_status("nb.ipynb")
        url = self.mock_post.call_args[0][0]
        self.assertIn("/api/notebooks/status", url)
        self.assertEqual(self.mock_post.call_args.kwargs["json"], {"path": "nb.ipynb"})

    def test_notebook_create_calls_post(self):
        self.client.notebook_create("nb.ipynb", cells=[{"type": "code", "source": "x = 1"}], kernel_id="subtext-venv")
        url = self.mock_post.call_args[0][0]
        self.assertIn("/api/notebooks/create", url)
        self.assertEqual(
            self.mock_post.call_args.kwargs["json"],
            {
                "path": "nb.ipynb",
                "cells": [{"type": "code", "source": "x = 1"}],
                "kernel_id": "subtext-venv",
            },
        )

    def test_notebook_edit_calls_post(self):
        self.client.notebook_edit("nb.ipynb", [{"op": "replace-source", "cell_id": "cell-1", "source": "x = 2"}])
        url = self.mock_post.call_args[0][0]
        self.assertIn("/api/notebooks/edit", url)

    def test_notebook_execute_cell_polls(self):
        self.client.notebook_execution = mock.Mock(return_value={"status": "ok", "outputs": []})
        with mock.patch.object(self.client, "_post", return_value={"status": "started", "execution_id": "exec-1", "cell_id": "cell-1"}) as post:
            result = self.client.notebook_execute_cell("nb.ipynb", cell_id="cell-1")
        self.assertEqual(result["status"], "ok")
        post.assert_called_once()
        self.client.notebook_execution.assert_called_once_with("exec-1")

    def test_notebook_insert_execute_polls(self):
        self.client.notebook_execution = mock.Mock(return_value={"status": "ok", "outputs": []})
        with mock.patch.object(self.client, "_post", return_value={"status": "started", "execution_id": "exec-2", "cell_id": "cell-2"}) as post:
            result = self.client.notebook_insert_execute("nb.ipynb", "x = 1")
        self.assertEqual(result["status"], "ok")
        post.assert_called_once()
        self.client.notebook_execution.assert_called_once_with("exec-2")

    def test_notebook_execution_calls_post(self):
        self.client.notebook_execution("exec-1")
        url = self.mock_post.call_args[0][0]
        self.assertIn("/api/notebooks/execution", url)
        self.assertEqual(self.mock_post.call_args.kwargs["json"], {"execution_id": "exec-1"})

    def test_notebook_execute_all_calls_post(self):
        self.client.notebook_execute_all("nb.ipynb")
        url = self.mock_post.call_args[0][0]
        self.assertIn("/api/notebooks/execute-all", url)
        self.assertEqual(self.mock_post.call_args.kwargs["json"], {"path": "nb.ipynb"})

    def test_list_branches_calls_get(self):
        self.client.list_branches()
        url = self.mock_get.call_args[0][0]
        self.assertIn("/api/branches", url)

    def test_start_branch_calls_post(self):
        self.client.start_branch(document_id="doc-1", owner_session_id="sess-1", title="Experiment")
        url = self.mock_post.call_args[0][0]
        self.assertIn("/api/branches/start", url)
        payload = self.mock_post.call_args.kwargs["json"]
        self.assertEqual(payload["document_id"], "doc-1")
        self.assertEqual(payload["owner_session_id"], "sess-1")
        self.assertEqual(payload["title"], "Experiment")
        self.assertIn("branch_id", payload)

    def test_finish_branch_calls_post(self):
        self.client.finish_branch("branch-1", status="merged")
        url = self.mock_post.call_args[0][0]
        self.assertIn("/api/branches/finish", url)
        self.assertEqual(self.mock_post.call_args.kwargs["json"], {"branch_id": "branch-1", "status": "merged"})

    def test_list_runtimes_calls_get(self):
        self.client.list_runtimes()
        url = self.mock_get.call_args[0][0]
        self.assertIn("/api/runtimes", url)

    def test_start_runtime_calls_post(self):
        self.client.start_runtime(mode="shared", label="primary", environment=".venv")
        url = self.mock_post.call_args[0][0]
        self.assertIn("/api/runtimes/start", url)
        payload = self.mock_post.call_args.kwargs["json"]
        self.assertEqual(payload["mode"], "shared")
        self.assertEqual(payload["label"], "primary")
        self.assertEqual(payload["environment"], ".venv")
        self.assertIn("runtime_id", payload)

    def test_stop_runtime_calls_post(self):
        self.client.stop_runtime("rt-1")
        url = self.mock_post.call_args[0][0]
        self.assertIn("/api/runtimes/stop", url)
        self.assertEqual(self.mock_post.call_args.kwargs["json"], {"runtime_id": "rt-1"})

    def test_list_runs_calls_get(self):
        self.client.list_runs()
        url = self.mock_get.call_args[0][0]
        self.assertIn("/api/runs", url)

    def test_start_run_calls_post(self):
        self.client.start_run(runtime_id="rt-1", target_type="document", target_ref="doc-1")
        url = self.mock_post.call_args[0][0]
        self.assertIn("/api/runs/start", url)
        payload = self.mock_post.call_args.kwargs["json"]
        self.assertEqual(payload["runtime_id"], "rt-1")
        self.assertEqual(payload["target_type"], "document")
        self.assertEqual(payload["target_ref"], "doc-1")
        self.assertEqual(payload["kind"], "execute")
        self.assertIn("run_id", payload)

    def test_finish_run_calls_post(self):
        self.client.finish_run("run-1", status="completed")
        url = self.mock_post.call_args[0][0]
        self.assertIn("/api/runs/finish", url)
        self.assertEqual(self.mock_post.call_args.kwargs["json"], {"run_id": "run-1", "status": "completed"})


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

    def test_select_kernel_interactive(self):
        args = build_parser().parse_args(["select-kernel", "nb.ipynb", "--interactive"])
        self.assertTrue(args.interactive)

    def test_v2_start(self):
        args = build_parser().parse_args(["v2", "start"])
        self.assertEqual(args.command, "v2")
        self.assertEqual(args.v2_command, "start")

    def test_v2_attach(self):
        args = build_parser().parse_args(["v2", "attach", "--actor", "agent", "--client-type", "cli"])
        self.assertEqual(args.v2_command, "attach")
        self.assertEqual(args.actor, "agent")
        self.assertEqual(args.client_type, "cli")

    def test_v2_status(self):
        args = build_parser().parse_args(["v2", "status", "--workspace-root", "/workspace"])
        self.assertEqual(args.v2_command, "status")
        self.assertEqual(args.workspace_root, "/workspace")

    def test_v2_stop(self):
        args = build_parser().parse_args(["v2", "stop"])
        self.assertEqual(args.v2_command, "stop")

    def test_v2_session_start(self):
        args = build_parser().parse_args(["v2", "session-start", "--actor", "agent", "--client-type", "cli"])
        self.assertEqual(args.v2_command, "session-start")
        self.assertEqual(args.actor, "agent")
        self.assertEqual(args.client_type, "cli")

    def test_v2_session_touch(self):
        args = build_parser().parse_args(["v2", "session-touch", "--session-id", "sess-1"])
        self.assertEqual(args.v2_command, "session-touch")
        self.assertEqual(args.session_id, "sess-1")

    def test_v2_session_detach(self):
        args = build_parser().parse_args(["v2", "session-detach", "--session-id", "sess-1"])
        self.assertEqual(args.v2_command, "session-detach")
        self.assertEqual(args.session_id, "sess-1")

    def test_v2_document_open(self):
        args = build_parser().parse_args(["v2", "document-open", "notebooks/demo.ipynb"])
        self.assertEqual(args.v2_command, "document-open")
        self.assertEqual(args.path, "notebooks/demo.ipynb")

    def test_v2_document_refresh(self):
        args = build_parser().parse_args(["v2", "document-refresh", "--document-id", "doc-1"])
        self.assertEqual(args.v2_command, "document-refresh")
        self.assertEqual(args.document_id, "doc-1")

    def test_v2_document_rebind(self):
        args = build_parser().parse_args(["v2", "document-rebind", "--document-id", "doc-1"])
        self.assertEqual(args.v2_command, "document-rebind")
        self.assertEqual(args.document_id, "doc-1")

    def test_v2_branch_start(self):
        args = build_parser().parse_args(["v2", "branch-start", "--document-id", "doc-1"])
        self.assertEqual(args.v2_command, "branch-start")
        self.assertEqual(args.document_id, "doc-1")

    def test_v2_branch_finish(self):
        args = build_parser().parse_args(["v2", "branch-finish", "--branch-id", "branch-1", "--status-value", "merged"])
        self.assertEqual(args.v2_command, "branch-finish")
        self.assertEqual(args.branch_id, "branch-1")

    def test_v2_runtime_start(self):
        args = build_parser().parse_args(["v2", "runtime-start", "--mode", "shared"])
        self.assertEqual(args.v2_command, "runtime-start")
        self.assertEqual(args.mode, "shared")

    def test_v2_run_start(self):
        args = build_parser().parse_args(["v2", "run-start", "--runtime-id", "rt-1", "--target-type", "document", "--target-ref", "doc-1"])
        self.assertEqual(args.v2_command, "run-start")
        self.assertEqual(args.runtime_id, "rt-1")
        self.assertEqual(args.target_type, "document")


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
            with (
                mock.patch("agent_repl.cli._client", return_value=mock_client),
                mock.patch("agent_repl.cli._notebook_client", return_value=mock_client),
            ):
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
        client.select_kernel.return_value = {"status": "ok"}
        client.edit.return_value = {"results": []}
        client.prompt_status.return_value = {"status": "ok"}
        client.reload.return_value = {"status": "ok"}
        for k, v in overrides.items():
            setattr(client, k, mock.Mock(return_value=v))
        return client

    def _mock_v2_client(self, **overrides):
        client = mock.MagicMock(spec=V2Client)
        client.status.return_value = {"status": "ok", "mode": "v2"}
        client.shutdown.return_value = {"status": "ok", "stopping": True}
        client.list_sessions.return_value = {"status": "ok", "sessions": []}
        client.start_session.return_value = {"status": "ok", "session": {"session_id": "sess-1"}}
        client.touch_session.return_value = {"status": "ok", "session": {"session_id": "sess-1", "status": "attached"}}
        client.detach_session.return_value = {"status": "ok", "session": {"session_id": "sess-1", "status": "detached"}}
        client.end_session.return_value = {"status": "ok", "ended": True}
        client.list_documents.return_value = {"status": "ok", "documents": []}
        client.open_document.return_value = {"status": "ok", "document": {"document_id": "doc-1"}}
        client.refresh_document.return_value = {"status": "ok", "document": {"document_id": "doc-1", "sync_state": "external-change"}}
        client.rebind_document.return_value = {"status": "ok", "document": {"document_id": "doc-1", "sync_state": "in-sync"}}
        client.notebook_contents.return_value = {"path": "nb.ipynb", "cells": []}
        client.notebook_status.return_value = {"path": "nb.ipynb", "kernel_state": "idle"}
        client.notebook_create.return_value = {"status": "ok", "path": "nb.ipynb"}
        client.notebook_edit.return_value = {"path": "nb.ipynb", "results": []}
        client.notebook_execute_cell.return_value = {"status": "ok"}
        client.notebook_insert_execute.return_value = {"status": "ok", "cell_id": "new-cell"}
        client.notebook_execution.return_value = {"status": "ok"}
        client.notebook_execute_all.return_value = {"status": "ok"}
        client.notebook_restart.return_value = {"status": "ok"}
        client.notebook_restart_and_run_all.return_value = {"status": "ok"}
        client.list_branches.return_value = {"status": "ok", "branches": []}
        client.start_branch.return_value = {"status": "ok", "branch": {"branch_id": "branch-1", "status": "active"}}
        client.finish_branch.return_value = {"status": "ok", "branch": {"branch_id": "branch-1", "status": "merged"}}
        client.list_runtimes.return_value = {"status": "ok", "runtimes": []}
        client.start_runtime.return_value = {"status": "ok", "runtime": {"runtime_id": "rt-1"}}
        client.stop_runtime.return_value = {"status": "ok", "runtime": {"runtime_id": "rt-1", "status": "stopped"}}
        client.list_runs.return_value = {"status": "ok", "runs": []}
        client.start_run.return_value = {"status": "ok", "run": {"run_id": "run-1"}}
        client.finish_run.return_value = {"status": "ok", "run": {"run_id": "run-1", "status": "completed"}}
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

    def test_cat_prefers_core_notebook_projection(self):
        bridge = self._mock_client()
        core = self._mock_v2_client(notebook_contents={
            "path": "nb.ipynb",
            "cells": [{"index": 0, "cell_id": "doc-1", "cell_type": "markdown", "source": "# hi"}],
        })
        buf = StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            with (
                mock.patch("agent_repl.cli._client", return_value=bridge),
                mock.patch("agent_repl.cli._notebook_client", return_value=core),
            ):
                code = main(["cat", "nb.ipynb"])
        finally:
            sys.stdout = old
        self.assertEqual(code, 0)
        core.notebook_contents.assert_called_once_with("nb.ipynb")
        bridge.contents.assert_not_called()

    def test_status(self):
        client = self._mock_client()
        code, _ = self._run(["status", "nb.ipynb"], client)
        self.assertEqual(code, 0)
        client.status.assert_called_once_with("nb.ipynb")

    def test_status_prefers_core_notebook_projection(self):
        bridge = self._mock_client()
        core = self._mock_v2_client()
        buf = StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            with (
                mock.patch("agent_repl.cli._client", return_value=bridge),
                mock.patch("agent_repl.cli._notebook_client", return_value=core),
            ):
                code = main(["status", "nb.ipynb"])
        finally:
            sys.stdout = old
        self.assertEqual(code, 0)
        core.notebook_status.assert_called_once_with("nb.ipynb")
        bridge.status.assert_not_called()

    def test_ix(self):
        client = self._mock_client()
        code, _ = self._run(["ix", "nb.ipynb", "-s", "x=1"], client)
        self.assertEqual(code, 0)
        client.insert_and_execute.assert_called_once_with("nb.ipynb", "x=1", at_index=-1, wait=True, timeout=30)

    def test_ix_prefers_core_execution_surface(self):
        bridge = self._mock_client()
        core = self._mock_v2_client()
        buf = StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            with (
                mock.patch("agent_repl.cli._client", return_value=bridge),
                mock.patch("agent_repl.cli._notebook_client", return_value=core),
            ):
                code = main(["ix", "nb.ipynb", "-s", "x=1"])
        finally:
            sys.stdout = old
        self.assertEqual(code, 0)
        core.notebook_insert_execute.assert_called_once_with("nb.ipynb", "x=1", at_index=-1, wait=True, timeout=30)
        bridge.insert_and_execute.assert_not_called()

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

    def test_exec_prefers_core_execution_surface(self):
        bridge = self._mock_client()
        core = self._mock_v2_client()
        buf = StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            with (
                mock.patch("agent_repl.cli._client", return_value=bridge),
                mock.patch("agent_repl.cli._notebook_client", return_value=core),
            ):
                code = main(["exec", "nb.ipynb", "--cell-id", "abc"])
        finally:
            sys.stdout = old
        self.assertEqual(code, 0)
        core.notebook_execute_cell.assert_called_once_with("nb.ipynb", cell_id="abc", wait=True, timeout=30)
        bridge.execute_cell.assert_not_called()

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

    def test_new_prefers_core_notebook_projection(self):
        bridge = self._mock_client()
        core = self._mock_v2_client()
        buf = StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            with (
                mock.patch("agent_repl.cli._client", return_value=bridge),
                mock.patch("agent_repl.cli._notebook_client", return_value=core),
            ):
                code = main(["new", "nb.ipynb"])
        finally:
            sys.stdout = old
        self.assertEqual(code, 0)
        core.notebook_create.assert_called_once_with("nb.ipynb", cells=None, kernel_id=None)
        bridge.create.assert_not_called()

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

    def test_edit_prefers_core_execution_surface(self):
        bridge = self._mock_client()
        core = self._mock_v2_client()
        buf = StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            with (
                mock.patch("agent_repl.cli._client", return_value=bridge),
                mock.patch("agent_repl.cli._notebook_client", return_value=core),
            ):
                code = main(["edit", "nb.ipynb", "replace-source", "--cell-id", "abc", "-s", "x=2"])
        finally:
            sys.stdout = old
        self.assertEqual(code, 0)
        core.notebook_edit.assert_called_once_with(
            "nb.ipynb",
            [{"op": "replace-source", "source": "x=2", "cell_id": "abc"}],
        )
        bridge.edit.assert_not_called()

    def test_run_all_prefers_core_execution_surface(self):
        bridge = self._mock_client()
        core = self._mock_v2_client()
        buf = StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            with (
                mock.patch("agent_repl.cli._client", return_value=bridge),
                mock.patch("agent_repl.cli._notebook_client", return_value=core),
            ):
                code = main(["run-all", "nb.ipynb"])
        finally:
            sys.stdout = old
        self.assertEqual(code, 0)
        core.notebook_execute_all.assert_called_once_with("nb.ipynb")
        bridge.execute_all.assert_not_called()

    def test_restart_prefers_core_execution_surface(self):
        bridge = self._mock_client()
        core = self._mock_v2_client()
        buf = StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            with (
                mock.patch("agent_repl.cli._client", return_value=bridge),
                mock.patch("agent_repl.cli._notebook_client", return_value=core),
            ):
                code = main(["restart", "nb.ipynb"])
        finally:
            sys.stdout = old
        self.assertEqual(code, 0)
        core.notebook_restart.assert_called_once_with("nb.ipynb")
        bridge.restart_kernel.assert_not_called()

    def test_restart_run_all_prefers_core_execution_surface(self):
        bridge = self._mock_client()
        core = self._mock_v2_client()
        buf = StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            with (
                mock.patch("agent_repl.cli._client", return_value=bridge),
                mock.patch("agent_repl.cli._notebook_client", return_value=core),
            ):
                code = main(["restart-run-all", "nb.ipynb"])
        finally:
            sys.stdout = old
        self.assertEqual(code, 0)
        core.notebook_restart_and_run_all.assert_called_once_with("nb.ipynb")
        bridge.restart_and_run_all.assert_not_called()

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

    def test_select_kernel_defaults_to_preferred_route_behavior(self):
        client = self._mock_client()
        code, _ = self._run(["select-kernel", "nb.ipynb"], client)
        self.assertEqual(code, 0)
        client.select_kernel.assert_called_once_with(
            "nb.ipynb",
            kernel_id=None,
            extension="ms-toolsai.jupyter",
            interactive=False,
        )

    def test_select_kernel_interactive_flag(self):
        client = self._mock_client()
        code, _ = self._run(["select-kernel", "nb.ipynb", "--interactive"], client)
        self.assertEqual(code, 0)
        client.select_kernel.assert_called_once_with(
            "nb.ipynb",
            kernel_id=None,
            extension="ms-toolsai.jupyter",
            interactive=True,
        )

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

    def test_v2_start(self):
        client = self._mock_client()
        with (
            mock.patch("agent_repl.cli._client", return_value=client),
            mock.patch("agent_repl.cli.V2Client.start", return_value={"status": "ok", "mode": "v2", "already_running": False}),
        ):
            code = main(["v2", "start"])
        self.assertEqual(code, 0)

    def test_v2_attach(self):
        client = self._mock_client()
        with (
            mock.patch("agent_repl.cli._client", return_value=client),
            mock.patch("agent_repl.cli.V2Client.attach", return_value={"status": "ok", "attached": True, "session": {"session_id": "sess-1"}}) as mock_attach,
        ):
            code = main(["v2", "attach", "--actor", "agent", "--client-type", "cli", "--label", "worker"])
        self.assertEqual(code, 0)
        mock_attach.assert_called_once_with(
            "/Users/giladrubin/python_workspace/agent-repl",
            actor="agent",
            client="cli",
            label="worker",
            capabilities=None,
            session_id=None,
            timeout=DEFAULT_START_TIMEOUT,
            runtime_dir=None,
        )

    def test_v2_status(self):
        client = self._mock_v2_client()
        buf = StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            with (
                mock.patch("agent_repl.cli._client"),
                mock.patch("agent_repl.cli._v2_client", return_value=client),
            ):
                code = main(["v2", "status"])
        finally:
            sys.stdout = old
        self.assertEqual(code, 0)
        client.status.assert_called_once()

    def test_v2_stop(self):
        client = self._mock_v2_client()
        buf = StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            with (
                mock.patch("agent_repl.cli._client"),
                mock.patch("agent_repl.cli._v2_client", return_value=client),
            ):
                code = main(["v2", "stop"])
        finally:
            sys.stdout = old
        self.assertEqual(code, 0)
        client.shutdown.assert_called_once()

    def test_v2_sessions(self):
        client = self._mock_v2_client()
        buf = StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            with mock.patch("agent_repl.cli._v2_client", return_value=client):
                code = main(["v2", "sessions"])
        finally:
            sys.stdout = old
        self.assertEqual(code, 0)
        client.list_sessions.assert_called_once()

    def test_v2_session_start(self):
        client = self._mock_v2_client()
        buf = StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            with mock.patch("agent_repl.cli._v2_client", return_value=client):
                code = main(["v2", "session-start", "--actor", "agent", "--client-type", "cli", "--label", "worker"])
        finally:
            sys.stdout = old
        self.assertEqual(code, 0)
        client.start_session.assert_called_once_with(
            actor="agent",
            client="cli",
            label="worker",
            capabilities=None,
            session_id=None,
        )

    def test_v2_session_touch(self):
        client = self._mock_v2_client()
        buf = StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            with mock.patch("agent_repl.cli._v2_client", return_value=client):
                code = main(["v2", "session-touch", "--session-id", "sess-1"])
        finally:
            sys.stdout = old
        self.assertEqual(code, 0)
        client.touch_session.assert_called_once_with("sess-1")

    def test_v2_session_detach(self):
        client = self._mock_v2_client()
        buf = StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            with mock.patch("agent_repl.cli._v2_client", return_value=client):
                code = main(["v2", "session-detach", "--session-id", "sess-1"])
        finally:
            sys.stdout = old
        self.assertEqual(code, 0)
        client.detach_session.assert_called_once_with("sess-1")

    def test_v2_session_end(self):
        client = self._mock_v2_client()
        buf = StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            with mock.patch("agent_repl.cli._v2_client", return_value=client):
                code = main(["v2", "session-end", "--session-id", "sess-1"])
        finally:
            sys.stdout = old
        self.assertEqual(code, 0)
        client.end_session.assert_called_once_with("sess-1")

    def test_v2_documents(self):
        client = self._mock_v2_client()
        buf = StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            with mock.patch("agent_repl.cli._v2_client", return_value=client):
                code = main(["v2", "documents"])
        finally:
            sys.stdout = old
        self.assertEqual(code, 0)
        client.list_documents.assert_called_once()

    def test_v2_document_open(self):
        client = self._mock_v2_client()
        buf = StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            with mock.patch("agent_repl.cli._v2_client", return_value=client):
                code = main(["v2", "document-open", "notebooks/demo.ipynb"])
        finally:
            sys.stdout = old
        self.assertEqual(code, 0)
        client.open_document.assert_called_once_with("notebooks/demo.ipynb")

    def test_v2_document_refresh(self):
        client = self._mock_v2_client()
        buf = StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            with mock.patch("agent_repl.cli._v2_client", return_value=client):
                code = main(["v2", "document-refresh", "--document-id", "doc-1"])
        finally:
            sys.stdout = old
        self.assertEqual(code, 0)
        client.refresh_document.assert_called_once_with("doc-1")

    def test_v2_document_rebind(self):
        client = self._mock_v2_client()
        buf = StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            with mock.patch("agent_repl.cli._v2_client", return_value=client):
                code = main(["v2", "document-rebind", "--document-id", "doc-1"])
        finally:
            sys.stdout = old
        self.assertEqual(code, 0)
        client.rebind_document.assert_called_once_with("doc-1")

    def test_v2_branches(self):
        client = self._mock_v2_client()
        buf = StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            with mock.patch("agent_repl.cli._v2_client", return_value=client):
                code = main(["v2", "branches"])
        finally:
            sys.stdout = old
        self.assertEqual(code, 0)
        client.list_branches.assert_called_once()

    def test_v2_branch_start(self):
        client = self._mock_v2_client()
        buf = StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            with mock.patch("agent_repl.cli._v2_client", return_value=client):
                code = main([
                    "v2", "branch-start",
                    "--document-id", "doc-1",
                    "--owner-session-id", "sess-1",
                    "--title", "Experiment",
                    "--purpose", "Risky parallel work",
                ])
        finally:
            sys.stdout = old
        self.assertEqual(code, 0)
        client.start_branch.assert_called_once_with(
            document_id="doc-1",
            owner_session_id="sess-1",
            parent_branch_id=None,
            title="Experiment",
            purpose="Risky parallel work",
            branch_id=None,
        )

    def test_v2_branch_finish(self):
        client = self._mock_v2_client()
        buf = StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            with mock.patch("agent_repl.cli._v2_client", return_value=client):
                code = main(["v2", "branch-finish", "--branch-id", "branch-1", "--status-value", "merged"])
        finally:
            sys.stdout = old
        self.assertEqual(code, 0)
        client.finish_branch.assert_called_once_with("branch-1", status="merged")

    def test_v2_runtimes(self):
        client = self._mock_v2_client()
        buf = StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            with mock.patch("agent_repl.cli._v2_client", return_value=client):
                code = main(["v2", "runtimes"])
        finally:
            sys.stdout = old
        self.assertEqual(code, 0)
        client.list_runtimes.assert_called_once()

    def test_v2_runtime_start(self):
        client = self._mock_v2_client()
        buf = StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            with mock.patch("agent_repl.cli._v2_client", return_value=client):
                code = main(["v2", "runtime-start", "--mode", "shared", "--label", "primary", "--environment", ".venv"])
        finally:
            sys.stdout = old
        self.assertEqual(code, 0)
        client.start_runtime.assert_called_once_with(
            mode="shared",
            label="primary",
            runtime_id=None,
            environment=".venv",
        )

    def test_v2_runtime_stop(self):
        client = self._mock_v2_client()
        buf = StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            with mock.patch("agent_repl.cli._v2_client", return_value=client):
                code = main(["v2", "runtime-stop", "--runtime-id", "rt-1"])
        finally:
            sys.stdout = old
        self.assertEqual(code, 0)
        client.stop_runtime.assert_called_once_with("rt-1")

    def test_v2_runs(self):
        client = self._mock_v2_client()
        buf = StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            with mock.patch("agent_repl.cli._v2_client", return_value=client):
                code = main(["v2", "runs"])
        finally:
            sys.stdout = old
        self.assertEqual(code, 0)
        client.list_runs.assert_called_once()

    def test_v2_run_start(self):
        client = self._mock_v2_client()
        buf = StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            with mock.patch("agent_repl.cli._v2_client", return_value=client):
                code = main([
                    "v2", "run-start",
                    "--runtime-id", "rt-1",
                    "--target-type", "document",
                    "--target-ref", "doc-1",
                ])
        finally:
            sys.stdout = old
        self.assertEqual(code, 0)
        client.start_run.assert_called_once_with(
            runtime_id="rt-1",
            target_type="document",
            target_ref="doc-1",
            kind="execute",
            run_id=None,
        )

    def test_v2_run_finish(self):
        client = self._mock_v2_client()
        buf = StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            with mock.patch("agent_repl.cli._v2_client", return_value=client):
                code = main(["v2", "run-finish", "--run-id", "run-1", "--status-value", "completed"])
        finally:
            sys.stdout = old
        self.assertEqual(code, 0)
        client.finish_run.assert_called_once_with("run-1", status="completed")


class TestVersionSurface(unittest.TestCase):
    def test_cli_exposes_version_flag(self):
        stdout = StringIO()
        old = sys.stdout
        sys.stdout = stdout
        try:
            with self.assertRaises(SystemExit) as exited:
                build_parser().parse_args(["--version"])
        finally:
            sys.stdout = old
        self.assertEqual(exited.exception.code, 0)
        self.assertRegex(stdout.getvalue().strip(), r"^\d+\.\d+\.\d+$")

    def test_help_hides_internal_core_command_group(self):
        stdout = StringIO()
        old = sys.stdout
        sys.stdout = stdout
        try:
            with self.assertRaises(SystemExit) as exited:
                build_parser().parse_args(["--help"])
        finally:
            sys.stdout = old
        self.assertEqual(exited.exception.code, 0)
        self.assertNotIn("v2", stdout.getvalue())

    def test_python_and_extension_versions_stay_in_sync(self):
        root = Path(__file__).resolve().parents[1]
        pyproject = tomllib.loads((root / "pyproject.toml").read_text())
        extension_package = json.loads((root / "extension" / "package.json").read_text())
        self.assertEqual(pyproject["project"]["version"], extension_package["version"])


class TestDocsSurface(unittest.TestCase):
    def test_repo_skill_uses_single_agent_repl_workflow(self):
        root = Path(__file__).resolve().parents[1]
        skill = (root / "SKILL.md").read_text()
        self.assertIn("agent-repl --version", skill)
        self.assertIn("uv tool install . --reinstall", skill)
        self.assertIn("index-1", skill)
        self.assertIn("starter cells are created, not auto-executed", skill)
        self.assertNotIn("agent-repl v2 --help", skill)
        self.assertNotIn("may briefly steal focus", skill)
        self.assertNotIn("make install-dev", skill)
        self.assertNotIn("make install-ext", skill)

    def test_getting_started_matches_ix_wait_behavior(self):
        root = Path(__file__).resolve().parents[1]
        guide = (root / "docs" / "getting-started.md").read_text()
        self.assertIn("ix` waits for completion by default", guide)
        self.assertNotIn("ix` returns immediately", guide)
        self.assertNotIn("agent-repl v2 --help", guide)
        self.assertNotIn("may briefly reveal the notebook", guide)

    def test_readme_uses_single_agent_repl_surface(self):
        root = Path(__file__).resolve().parents[1]
        readme = (root / "README.md").read_text()
        self.assertNotIn("Experimental v2 core daemon", readme)
        self.assertNotIn("| `v2` |", readme)
        self.assertNotIn("including experimental `v2`", readme)
        self.assertNotIn("make install-dev", readme)
        self.assertNotIn("make install-ext", readme)

    def test_installation_docs_prefer_direct_uv_and_extension_commands(self):
        root = Path(__file__).resolve().parents[1]
        install = (root / "docs" / "installation.md").read_text()
        self.assertIn("uv tool install . --reinstall", install)
        self.assertIn("npx --yes @vscode/vsce package", install)
        self.assertNotIn("make install-dev", install)
        self.assertNotIn("make install-ext", install)
        self.assertNotIn("make verify-install", install)

    def test_command_reference_warns_about_closed_notebook_fallback_ids(self):
        root = Path(__file__).resolve().parents[1]
        commands = (root / "docs" / "commands.md").read_text()
        self.assertIn("index-1", commands)
        self.assertIn("Use `--no-wait` only when you intentionally want fire-and-forget behavior.", commands)
        self.assertIn("agent-repl reload --pretty", commands)
        self.assertNotIn("## v2", commands)
        self.assertNotIn("agent-repl v2", commands)

    def test_public_docs_lock_in_workspace_venv_as_default_kernel(self):
        root = Path(__file__).resolve().parents[1]
        skill = (root / "SKILL.md").read_text()
        commands = (root / "docs" / "commands.md").read_text()
        self.assertIn("`new` prefers the workspace `.venv` when it exists", skill)
        self.assertIn("`select-kernel` now defaults to the workspace `.venv` when it exists", skill)
        self.assertIn("Without it, `agent-repl` first tries the workspace `.venv` automatically when one exists.", commands)
        self.assertIn("Use `--interactive` to open VS Code's kernel picker explicitly.", commands)


class TestV2ServerRobustness(unittest.TestCase):
    def test_server_returns_json_error_when_run_finish_handler_raises(self):
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)

        workspace_root = Path(tmpdir.name)
        runtime_dir = workspace_root / "runtime"
        runtime_dir.mkdir()
        state = CoreState(
            workspace_root=str(workspace_root),
            runtime_dir=str(runtime_dir),
            token="tok",
            pid=1234,
            started_at=1.0,
        )

        def boom(run_id: str, status: str):
            raise RuntimeError(f"boom for {run_id}:{status}")

        state.finish_run = boom  # type: ignore[method-assign]

        from http.server import ThreadingHTTPServer
        import threading

        server = ThreadingHTTPServer(("127.0.0.1", 0), _handler_factory(state))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 2)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)

        port = server.server_address[1]
        client = V2Client(f"http://127.0.0.1:{port}", "tok")

        with self.assertRaisesRegex(RuntimeError, "boom for run-1:completed"):
            client.finish_run("run-1", status="completed")


if __name__ == "__main__":
    unittest.main()
