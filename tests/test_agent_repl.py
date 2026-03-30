"""Tests for agent-repl bridge CLI."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import sys
import threading
import time
import tomllib
import unittest
from contextlib import contextmanager
from io import StringIO
from pathlib import Path
from typing import Any
from unittest import mock

import requests

import agent_repl.cli as cli_module
from agent_repl.cli import build_parser, main
from agent_repl.client import BridgeClient
from agent_repl.core.client import DEFAULT_START_TIMEOUT, CoreClient
from agent_repl.core.server import CoreState, _load_or_create_state
from agent_repl.http_api import ApiError, poll_execution_until_complete
from agent_repl.notebook_runtime_client import (
    call_with_owner_session,
    resolve_owner_session_id,
)


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
# Shared HTTP helpers
# ---------------------------------------------------------------------------

class TestHttpApiHelpers(unittest.TestCase):
    """Shared HTTP polling helpers preserve execution semantics across clients."""

    def test_poll_execution_until_complete_carries_initial_metadata_forward(self):
        fetch_execution = mock.Mock(side_effect=[
            {"status": "running"},
            {"status": "ok", "outputs": []},
        ])

        with (
            mock.patch("agent_repl.http_api.time.sleep"),
            mock.patch("agent_repl.http_api.time.monotonic", side_effect=[0.0, 0.1, 0.2]),
        ):
            result = poll_execution_until_complete(
                {
                    "execution_id": "exec-1",
                    "status": "started",
                    "cell_id": "cell-1",
                    "cell_index": 3,
                    "operation": "execute-cell",
                },
                timeout=5,
                fetch_execution=fetch_execution,
                in_progress_statuses={"running", "queued", "started"},
            )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["cell_id"], "cell-1")
        self.assertEqual(result["cell_index"], 3)
        self.assertEqual(result["operation"], "execute-cell")
        self.assertEqual(fetch_execution.call_args_list, [mock.call("exec-1"), mock.call("exec-1")])

    def test_poll_execution_until_complete_returns_timeout_payload(self):
        fetch_execution = mock.Mock(return_value={"status": "running"})

        with (
            mock.patch("agent_repl.http_api.time.sleep"),
            mock.patch("agent_repl.http_api.time.monotonic", side_effect=[0.0, 0.1, 0.2, 0.31]),
        ):
            result = poll_execution_until_complete(
                {"execution_id": "exec-2", "status": "queued", "cell_id": "cell-2"},
                timeout=0.3,
                fetch_execution=fetch_execution,
                in_progress_statuses={"running", "queued"},
            )

        self.assertEqual(
            result,
            {
                "execution_id": "exec-2",
                "status": "timeout",
                "cell_id": "cell-2",
                "timeout_seconds": 0.3,
            },
        )
        self.assertEqual(fetch_execution.call_count, 2)

    def test_api_error_keeps_structured_recovery_payload(self):
        error = ApiError(
            "409 Conflict: Operation blocked",
            status_code=409,
            reason="Conflict",
            url="http://example.test/api/notebooks/edit",
            payload={
                "error": "Operation blocked",
                "recovery": {
                    "reason": "lease-conflict",
                    "summary": "Another session holds the lease.",
                },
            },
        )

        self.assertEqual(
            error.to_payload(),
            {
                "error": "Operation blocked",
                "recovery": {
                    "reason": "lease-conflict",
                    "summary": "Another session holds the lease.",
                },
                "status_code": 409,
                "reason_phrase": "Conflict",
                "url": "http://example.test/api/notebooks/edit",
            },
        )


class TestPreviewServerCompatibilityHelpers(unittest.TestCase):
    def test_preview_server_is_compatible_requires_workspace_protocol_and_routes(self):
        self.assertTrue(
            cli_module._preview_server_is_compatible(
                {
                    "protocol_version": cli_module.STANDALONE_PREVIEW_PROTOCOL_VERSION,
                    "workspace_root": "/workspace",
                    "api_routes": sorted(cli_module.STANDALONE_PREVIEW_REQUIRED_ROUTES),
                },
                workspace_root="/workspace",
            )
        )

        self.assertFalse(
            cli_module._preview_server_is_compatible(
                {
                    "protocol_version": "old-preview",
                    "workspace_root": "/workspace",
                    "api_routes": sorted(cli_module.STANDALONE_PREVIEW_REQUIRED_ROUTES),
                },
                workspace_root="/workspace",
            )
        )

        self.assertFalse(
            cli_module._preview_server_is_compatible(
                {
                    "protocol_version": cli_module.STANDALONE_PREVIEW_PROTOCOL_VERSION,
                    "workspace_root": "/other-workspace",
                    "api_routes": sorted(cli_module.STANDALONE_PREVIEW_REQUIRED_ROUTES),
                },
                workspace_root="/workspace",
            )
        )

        self.assertFalse(
            cli_module._preview_server_is_compatible(
                {
                    "protocol_version": cli_module.STANDALONE_PREVIEW_PROTOCOL_VERSION,
                    "workspace_root": "/workspace",
                    "api_routes": ["/api/standalone/health"],
                },
                workspace_root="/workspace",
            )
        )


class TestNotebookRuntimeClientHelpers(unittest.TestCase):
    """Shared notebook runtime helper behavior."""

    def test_resolve_owner_session_id_prefers_explicit_session(self):
        client = mock.Mock()

        session_id = resolve_owner_session_id(client, explicit_session_id="sess-explicit")

        self.assertEqual(session_id, "sess-explicit")
        client.resolve_preferred_session.assert_not_called()
        client.start_session.assert_not_called()

    def test_call_with_owner_session_injects_reused_session_id(self):
        client = mock.Mock()
        client.resolve_preferred_session.return_value = {
            "status": "ok",
            "session": {"session_id": "sess-vscode"},
        }
        operation = mock.Mock(return_value={"status": "ok"})

        result = call_with_owner_session(
            client,
            operation,
            "nb.ipynb",
            wait=True,
        )

        self.assertEqual(result, {"status": "ok"})
        operation.assert_called_once_with("nb.ipynb", wait=True, owner_session_id="sess-vscode")
        client.start_session.assert_not_called()

    def test_resolve_owner_session_id_starts_session_when_no_preferred_session_exists(self):
        client = mock.Mock()
        client.resolve_preferred_session.return_value = {"status": "ok", "session": None}
        client.start_session.return_value = {"status": "ok", "session": {"session_id": "sess-started"}}

        session_id = resolve_owner_session_id(client, client_type="browser", label="Browser Preview")

        self.assertEqual(session_id, "sess-started")
        client.start_session.assert_called_once_with(actor="human", client="browser", label="Browser Preview")

    def test_call_with_owner_session_omits_owner_when_resolution_fails(self):
        client = mock.Mock()
        client.resolve_preferred_session.side_effect = RuntimeError("boom")
        client.start_session.side_effect = RuntimeError("still-boom")
        operation = mock.Mock(return_value={"status": "ok"})

        result = call_with_owner_session(client, operation, path="nb.ipynb")

        self.assertEqual(result, {"status": "ok"})
        operation.assert_called_once_with(path="nb.ipynb")


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


class TestCoreDiscovery(unittest.TestCase):
    """CoreClient.discover() scans runtime dir for workspace daemons."""

    def test_discover_finds_matching_workspace(self):
        info = json.dumps({
            "pid": 123,
            "port": 23456,
            "token": "tok",
            "workspace_root": "/workspace",
        })
        with (
            mock.patch("agent_repl.core.client.glob.glob", return_value=["/tmp/agent-repl-core-1.json"]),
            mock.patch("agent_repl.core.client.os.path.getmtime", return_value=1.0),
            mock.patch("agent_repl.core.client.os.getcwd", return_value="/workspace"),
            mock.patch("agent_repl.core.client.Path.read_text", return_value=info),
            mock.patch("agent_repl.core.client._pid_alive", return_value=True),
            mock.patch.object(CoreClient, "health", return_value={"status": "ok"}),
        ):
            client = CoreClient.discover()
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
            mock.patch("agent_repl.core.client.glob.glob", return_value=["/tmp/agent-repl-core-1.json"]),
            mock.patch("agent_repl.core.client.os.path.getmtime", return_value=1.0),
            mock.patch("agent_repl.core.client.os.getcwd", return_value="/workspace"),
            mock.patch("agent_repl.core.client.Path.read_text", return_value=info),
            mock.patch("agent_repl.core.client._pid_alive", return_value=True),
        ):
            with self.assertRaisesRegex(RuntimeError, "No running agent-repl core daemon matched '/workspace'"):
                CoreClient.discover()

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
            "/tmp/agent-repl-core-parent.json": info_parent,
            "/tmp/agent-repl-core-child.json": info_child,
        }
        mtime_map = {
            "/tmp/agent-repl-core-parent.json": 20.0,
            "/tmp/agent-repl-core-child.json": 10.0,
        }
        with (
            mock.patch(
                "agent_repl.core.client.glob.glob",
                return_value=["/tmp/agent-repl-core-parent.json", "/tmp/agent-repl-core-child.json"],
            ),
            mock.patch("agent_repl.core.client.os.path.getmtime", side_effect=lambda path: mtime_map[path]),
            mock.patch("agent_repl.core.client.os.getcwd", return_value="/workspace/subproject"),
            mock.patch("agent_repl.core.client.Path.read_text", autospec=True, side_effect=lambda self: read_map[str(self)]),
            mock.patch("agent_repl.core.client._pid_alive", return_value=True),
            mock.patch.object(CoreClient, "health", return_value={"status": "ok"}),
        ):
            client = CoreClient.discover()
        self.assertEqual(client.base_url, "http://127.0.0.1:34567")
        self.assertEqual(client.token, "tok-child")

    def test_attach_starts_or_reuses_daemon_then_session(self):
        with (
            mock.patch.object(CoreClient, "start", return_value={"status": "ok", "workspace_root": "/workspace", "already_running": True}),
            mock.patch.object(CoreClient, "discover") as mock_discover,
        ):
            attached_client = mock.MagicMock(spec=CoreClient)
            attached_client.start_session.return_value = {
                "status": "ok",
                "session": {"session_id": "sess-1", "status": "attached"},
            }
            mock_discover.return_value = attached_client
            result = CoreClient.attach(
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

class TestPackagingMetadata(unittest.TestCase):
    """Packaging metadata must include the headless runtime dependencies."""

    def test_project_dependencies_include_headless_runtime_requirements(self):
        pyproject = tomllib.loads((Path(__file__).resolve().parents[1] / "pyproject.toml").read_text())
        dependencies = pyproject["project"]["dependencies"]
        self.assertTrue(any(dep.startswith("jupyter-client") for dep in dependencies))
        self.assertTrue(any(dep.startswith("nbformat") for dep in dependencies))


class TestCoreState(unittest.TestCase):
    """Direct tests for core document/file sync behavior."""

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
        from agent_repl.core.db import open_db
        self._test_db = open_db(str(self.workspace_root))
        self.state._db = self._test_db

    def tearDown(self):
        self.state.shutdown_headless_runtimes()
        self.state._ydoc_service.close_all()
        self._test_db.close()
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

    def test_snapshot_file_hashes_existing_files(self):
        from agent_repl.core.server import _snapshot_file

        snapshot = _snapshot_file(str(self.doc_path))

        self.assertTrue(snapshot["exists"])
        self.assertEqual(snapshot["source_kind"], "file")
        self.assertIsInstance(snapshot["sha256"], str)
        self.assertEqual(len(snapshot["sha256"]), 64)

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
            mock.patch("agent_repl.core.server._snapshot_live_document", side_effect=[live_bound, live_changed]),
            mock.patch("agent_repl.core.server._snapshot_file", return_value=file_snapshot),
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

        original_persist_all = __import__("agent_repl.core.db", fromlist=["persist_all"]).persist_all

        def guarded_persist_all(conn, **kwargs):
            nonlocal active_writers
            with active_lock:
                active_writers += 1
                if active_writers > 1:
                    overlap_detected.set()
                started.set()
            release.wait(timeout=2)
            try:
                return original_persist_all(conn, **kwargs)
            finally:
                with active_lock:
                    active_writers -= 1

        first = threading.Thread(target=self.state.persist)
        second = threading.Thread(target=self.state.persist)

        with mock.patch("agent_repl.core.db.persist_all", new=guarded_persist_all):
            first.start()
            self.assertTrue(started.wait(timeout=1))
            second.start()
            release.set()
            first.join(timeout=2)
            second.join(timeout=2)

        self.assertFalse(overlap_detected.is_set())

    def test_notebook_contents_uses_headless_snapshot_and_syncs_document_record(self):
        with mock.patch.object(self.state, "_projection_client", return_value=None):
            body, status = self.state.notebook_contents("notebooks/demo.ipynb")

        self.assertEqual(status, 200)
        self.assertGreaterEqual(len(body["cells"]), 0)
        self.assertEqual(len(self.state.document_records), 1)
        record = next(iter(self.state.document_records.values()))
        self.assertEqual(record.relative_path, "notebooks/demo.ipynb")
        self.assertEqual(record.sync_state, "in-sync")

    def test_notebook_contents_treats_empty_file_as_blank_notebook(self):
        empty_path = self.workspace_root / "notebooks" / "empty.ipynb"
        empty_path.write_text("")

        with mock.patch.object(self.state, "_projection_client", return_value=None):
            body, status = self.state.notebook_contents("notebooks/empty.ipynb")

        self.assertEqual(status, 200)
        self.assertEqual(body["path"], "notebooks/empty.ipynb")
        self.assertEqual(body["cells"], [])

    def test_notebook_create_uses_headless_runtime_and_registers_document(self):
        with mock.patch.object(self.state, "_projection_client", return_value=None):
            body, status = self.state.notebook_create(
                "notebooks/demo.ipynb",
                cells=[{"type": "code", "source": "x = 1"}],
                kernel_id=_python_with_ipykernel(),
            )

        self.assertEqual(status, 200)
        self.assertEqual(body["kernel_status"], "selected")
        self.assertEqual(len(self.state.document_records), 1)

    def test_notebook_edit_uses_headless_runtime_and_syncs_document_record(self):
        with mock.patch.object(self.state, "_projection_client", return_value=None):
            self.state.notebook_create(
                "notebooks/demo.ipynb",
                cells=[{"type": "code", "source": "x = 1"}],
                kernel_id=_python_with_ipykernel(),
            )
            body, status = self.state.notebook_edit(
                "notebooks/demo.ipynb",
                [{"op": "replace-source", "cell_id": None, "cell_index": 0, "source": "x = 2"}],
            )

        self.assertEqual(status, 200)
        self.assertEqual(body["results"][0]["op"], "replace-source")
        self.assertEqual(len(self.state.document_records), 1)

    def test_notebook_edit_uses_generated_cell_id_for_read_only_contents(self):
        notebook_path = self.workspace_root / "notebooks" / "ephemeral-ids.ipynb"
        notebook_path.parent.mkdir(parents=True, exist_ok=True)
        notebook_path.write_text(json.dumps({
            "cells": [{
                "cell_type": "code",
                "execution_count": None,
                "id": "legacy-nbformat-id",
                "metadata": {},
                "outputs": [],
                "source": ["x = 1\n"],
            }],
            "metadata": {},
            "nbformat": 4,
            "nbformat_minor": 5,
        }))

        with mock.patch.object(self.state, "_projection_client", return_value=None):
            contents_body, contents_status = self.state.notebook_contents("notebooks/ephemeral-ids.ipynb")
            self.assertEqual(contents_status, 200)
            generated_cell_id = contents_body["cells"][0]["cell_id"]

            body, status = self.state.notebook_edit(
                "notebooks/ephemeral-ids.ipynb",
                [{"op": "replace-source", "cell_id": generated_cell_id, "cell_index": 0, "source": "x = 2"}],
            )

            refreshed_body, refreshed_status = self.state.notebook_contents("notebooks/ephemeral-ids.ipynb")

        self.assertEqual(status, 200)
        self.assertEqual(body["results"][0]["op"], "replace-source")
        self.assertEqual(refreshed_status, 200)
        self.assertEqual(refreshed_body["cells"][0]["source"], "x = 2")

    def test_notebook_edit_insert_clamps_out_of_range_index_for_blank_headless_notebook(self):
        with mock.patch.object(self.state, "_projection_client", return_value=None):
            create_body, create_status = self.state.notebook_create(
                "notebooks/blank-insert.ipynb",
                cells=[],
                kernel_id=_python_with_ipykernel(),
            )
            self.assertEqual(create_status, 200)
            self.assertTrue(create_body["ready"])

            body, status = self.state.notebook_edit(
                "notebooks/blank-insert.ipynb",
                [{"op": "insert", "cell_type": "code", "source": "", "at_index": 1}],
            )

        self.assertEqual(status, 200)
        self.assertEqual(body["results"][0]["op"], "insert")
        self.assertEqual(body["results"][0]["cell_count"], 1)

        contents_body, contents_status = self.state.notebook_contents("notebooks/blank-insert.ipynb")
        self.assertEqual(contents_status, 200)
        self.assertEqual(len(contents_body["cells"]), 1)

    def test_ydoc_shadow_stays_in_sync_after_edit_operations(self):
        with mock.patch.object(self.state, "_projection_client", return_value=None):
            self.state.notebook_create(
                "notebooks/ydoc-sync.ipynb",
                cells=[
                    {"type": "code", "source": "a = 1"},
                    {"type": "code", "source": "b = 2"},
                    {"type": "code", "source": "c = 3"},
                ],
                kernel_id=_python_with_ipykernel(),
            )
            relative_path = "notebooks/ydoc-sync.ipynb"

            # Verify initial shadow load
            ydoc_cells = self.state._ydoc_service.get_cells(relative_path)
            self.assertEqual(len(ydoc_cells), 3)
            self.assertEqual(ydoc_cells[0]["source"], "a = 1")

            # Replace source
            self.state.notebook_edit(
                relative_path,
                [{"op": "replace-source", "cell_index": 0, "source": "a = 10"}],
            )
            ydoc_cells = self.state._ydoc_service.get_cells(relative_path)
            self.assertEqual(ydoc_cells[0]["source"], "a = 10")

            # Insert cell
            self.state.notebook_edit(
                relative_path,
                [{"op": "insert", "cell_type": "code", "source": "d = 4", "at_index": 1}],
            )
            ydoc_cells = self.state._ydoc_service.get_cells(relative_path)
            self.assertEqual(len(ydoc_cells), 4)
            self.assertEqual(ydoc_cells[1]["source"], "d = 4")

            # Delete cell
            self.state.notebook_edit(
                relative_path,
                [{"op": "delete", "cell_index": 1}],
            )
            ydoc_cells = self.state._ydoc_service.get_cells(relative_path)
            self.assertEqual(len(ydoc_cells), 3)
            self.assertEqual(ydoc_cells[0]["source"], "a = 10")
            self.assertEqual(ydoc_cells[1]["source"], "b = 2")

            # Move cell
            self.state.notebook_edit(
                relative_path,
                [{"op": "move", "cell_index": 0, "to_index": 2}],
            )
            ydoc_cells = self.state._ydoc_service.get_cells(relative_path)
            self.assertEqual(len(ydoc_cells), 3)
            # After move(0,2): b, c, a
            self.assertEqual(ydoc_cells[0]["source"], "b = 2")
            self.assertEqual(ydoc_cells[2]["source"], "a = 10")

    def test_notebook_insert_execute_clamps_out_of_range_index_for_blank_headless_notebook(self):
        with mock.patch.object(self.state, "_projection_client", return_value=None):
            create_body, create_status = self.state.notebook_create(
                "notebooks/blank-insert-execute.ipynb",
                cells=[],
                kernel_id=_python_with_ipykernel(),
            )
            self.assertEqual(create_status, 200)
            self.assertTrue(create_body["ready"])

            body, status = self.state.notebook_insert_execute(
                "notebooks/blank-insert-execute.ipynb",
                source="21 * 2",
                cell_type="code",
                at_index=1,
            )

        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["cell_index"], 0)
        self.assertEqual(body["outputs"][0]["data"]["text/plain"], "42")

    def test_notebook_execute_cell_uses_headless_runtime(self):
        with mock.patch.object(self.state, "_projection_client", return_value=None):
            self.state.notebook_create(
                "notebooks/demo.ipynb",
                cells=[{"type": "code", "source": "x = 1\nx"}],
                kernel_id=_python_with_ipykernel(),
            )
            body, status = self.state.notebook_execute_cell(
                "notebooks/demo.ipynb",
                cell_id=None,
                cell_index=0,
            )

        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "ok")
        self.assertIsInstance(body["execution_id"], str)
        lookup_body, lookup_status = self.state.notebook_execution(body["execution_id"])
        self.assertEqual(lookup_status, 200)
        self.assertEqual(lookup_body["status"], "ok")
        self.assertEqual(lookup_body["cell_id"], body["cell_id"])
        self.assertEqual(lookup_body["outputs"][0]["data"]["text/plain"], "1")

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
        with (
            mock.patch.object(self.state, "_projection_client", return_value=None),
            mock.patch("agent_repl.core.server.subprocess.run", return_value=mock.Mock(returncode=1, stderr="No module named 'ipykernel'", stdout="")),
        ):
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

    def test_headless_restart_and_run_all_assigns_missing_cell_ids_before_batch_execution(self):
        notebook_path = "notebooks/headless-restart-missing-ids.ipynb"
        kernel_python = _python_with_ipykernel()
        raw_notebook = {
            "cells": [{
                "cell_type": "code",
                "execution_count": None,
                "id": "cell-1",
                "metadata": {},
                "outputs": [],
                "source": [
                    "import time\n",
                    "for i in range(3):\n",
                    "    print(i, flush=True)\n",
                    "    time.sleep(0.1)\n",
                ],
            }],
            "metadata": {},
            "nbformat": 4,
            "nbformat_minor": 5,
        }

        with mock.patch.object(self.state, "_projection_client", return_value=None):
            notebook_file = self.workspace_root / notebook_path
            notebook_file.parent.mkdir(parents=True, exist_ok=True)
            notebook_file.write_text(json.dumps(raw_notebook))

            select_body, select_status = self.state.notebook_select_kernel(
                notebook_path,
                kernel_id=kernel_python,
            )
            self.assertEqual(select_status, 200)
            self.assertEqual(select_body["status"], "ok")

            restart_body, restart_status = self.state.notebook_restart_and_run_all(notebook_path)
            self.assertEqual(restart_status, 200)
            self.assertEqual(restart_body["status"], "ok")
            self.assertEqual(len(restart_body["executions"]), 1)
            self.assertEqual(
                [output["text"] for output in restart_body["executions"][0]["outputs"]],
                ["0\n", "1\n", "2\n"],
            )

            contents_body, contents_status = self.state.notebook_contents(notebook_path)
            self.assertEqual(contents_status, 200)
            self.assertTrue(contents_body["cells"][0]["cell_id"])

    def test_headless_restart_and_run_all_allows_owner_session_leases(self):
        notebook_path = "notebooks/headless-restart-lease.ipynb"
        kernel_python = _python_with_ipykernel()

        with mock.patch.object(self.state, "_projection_client", return_value=None):
            create_body, create_status = self.state.notebook_create(
                notebook_path,
                cells=[{"type": "code", "source": "21 * 2"}],
                kernel_id=kernel_python,
            )
            self.assertEqual(create_status, 200)
            self.assertTrue(create_body["ready"])

            self.state.start_session("human", "vscode", "editor", "sess-human", ["projection", "editor", "presence"])

            contents_body, contents_status = self.state.notebook_contents(notebook_path)
            self.assertEqual(contents_status, 200)
            cell_id = contents_body["cells"][0]["cell_id"]

            lease_body, lease_status = self.state.acquire_cell_lease(
                session_id="sess-human",
                path=notebook_path,
                cell_id=cell_id,
            )
            self.assertEqual(lease_status, 200)
            self.assertEqual(lease_body["lease"]["session_id"], "sess-human")

            blocked_body, blocked_status = self.state.notebook_restart_and_run_all(notebook_path)
            self.assertEqual(blocked_status, 409)
            self.assertEqual(blocked_body["conflict"]["lease"]["session_id"], "sess-human")
            self.assertEqual(blocked_body["conflict"]["operation"], "execute-cell")

            allowed_body, allowed_status = self.state.notebook_restart_and_run_all(
                notebook_path,
                owner_session_id="sess-human",
            )
            self.assertEqual(allowed_status, 200)
            self.assertEqual(allowed_body["status"], "ok")
            self.assertEqual(allowed_body["executions"][0]["outputs"][0]["data"]["text/plain"], "42")

    def test_headless_execute_all_stops_after_first_failed_cell(self):
        notebook_path = "notebooks/headless-stop-on-error.ipynb"
        kernel_python = _python_with_ipykernel()

        with mock.patch.object(self.state, "_projection_client", return_value=None):
            create_body, create_status = self.state.notebook_create(
                notebook_path,
                cells=[
                    {"type": "code", "source": "print('before')"},
                    {"type": "code", "source": "raise ValueError('boom')"},
                    {"type": "code", "source": "print('after')"},
                ],
                kernel_id=kernel_python,
            )
            self.assertEqual(create_status, 200)
            self.assertTrue(create_body["ready"])

            contents_body, contents_status = self.state.notebook_contents(notebook_path)
            self.assertEqual(contents_status, 200)
            failed_cell_id = contents_body["cells"][1]["cell_id"]

            execute_body, execute_status = self.state.notebook_execute_all(notebook_path)
            self.assertEqual(execute_status, 200)
            self.assertEqual(execute_body["status"], "error")
            self.assertTrue(execute_body["stopped_on_error"])
            self.assertEqual(execute_body["failed_cell_id"], failed_cell_id)
            self.assertEqual(len(execute_body["executions"]), 2)
            self.assertEqual(execute_body["executions"][0]["status"], "ok")
            self.assertEqual(execute_body["executions"][1]["status"], "error")
            self.assertEqual(execute_body["executions"][1]["cell_id"], failed_cell_id)

            contents_body, contents_status = self.state.notebook_contents(notebook_path)
            self.assertEqual(contents_status, 200)
            self.assertEqual(contents_body["cells"][0]["outputs"][0]["text"], "before\n")
            self.assertEqual(contents_body["cells"][1]["outputs"][0]["output_type"], "error")
            self.assertEqual(contents_body["cells"][2]["outputs"], [])
            self.assertIsNone(contents_body["cells"][2]["execution_count"])

    def test_headless_restart_and_run_all_stops_after_first_failed_cell(self):
        notebook_path = "notebooks/headless-restart-stop-on-error.ipynb"
        kernel_python = _python_with_ipykernel()

        with mock.patch.object(self.state, "_projection_client", return_value=None):
            create_body, create_status = self.state.notebook_create(
                notebook_path,
                cells=[
                    {"type": "code", "source": "print('before restart')"},
                    {"type": "code", "source": "raise RuntimeError('boom')"},
                    {"type": "code", "source": "print('after restart')"},
                ],
                kernel_id=kernel_python,
            )
            self.assertEqual(create_status, 200)
            self.assertTrue(create_body["ready"])

            restart_body, restart_status = self.state.notebook_restart_and_run_all(notebook_path)
            self.assertEqual(restart_status, 200)
            self.assertEqual(restart_body["status"], "error")
            self.assertEqual(restart_body["mode"], "headless")
            self.assertTrue(restart_body["stopped_on_error"])
            self.assertEqual(len(restart_body["executions"]), 2)
            self.assertEqual(restart_body["restart"]["kernel_state"], "idle")

            contents_body, contents_status = self.state.notebook_contents(notebook_path)
            self.assertEqual(contents_status, 200)
            self.assertEqual(contents_body["cells"][2]["outputs"], [])
            self.assertIsNone(contents_body["cells"][2]["execution_count"])

    def test_headless_notebook_runtime_reports_active_runtime_without_projection_client(self):
        notebook_path = "notebooks/headless-runtime.ipynb"
        kernel_python = _python_with_ipykernel()

        with mock.patch.object(self.state, "_projection_client", return_value=None):
            create_body, create_status = self.state.notebook_create(
                notebook_path,
                cells=[{"type": "code", "source": "x = 3\nx"}],
                kernel_id=kernel_python,
            )
            self.assertEqual(create_status, 200)

            runtime_body, runtime_status = self.state.notebook_runtime(notebook_path)
            self.assertEqual(runtime_status, 200)
            self.assertTrue(runtime_body["active"])
            self.assertEqual(runtime_body["mode"], "headless")
            self.assertEqual(runtime_body["reattach_policy"]["action"], "attach-live")
            self.assertEqual(runtime_body["runtime"]["python_path"], os.path.abspath(kernel_python))
            self.assertEqual(runtime_body["runtime_record"]["status"], "idle")
            self.assertEqual(runtime_body["runtime_record"]["document_path"], notebook_path)

    def test_headless_notebook_runtime_reports_resume_policy_after_runtime_shutdown(self):
        notebook_path = "notebooks/headless-resume.ipynb"
        kernel_python = _python_with_ipykernel()

        with mock.patch.object(self.state, "_projection_client", return_value=None):
            create_body, create_status = self.state.notebook_create(
                notebook_path,
                cells=[{"type": "code", "source": "x = 5\nx"}],
                kernel_id=kernel_python,
            )
            self.assertEqual(create_status, 200)

            real_path = os.path.realpath(os.path.join(str(self.workspace_root), notebook_path))
            self.state._shutdown_headless_runtime(real_path)

            runtime_body, runtime_status = self.state.notebook_runtime(notebook_path)
            self.assertEqual(runtime_status, 200)
            self.assertFalse(runtime_body["active"])
            self.assertEqual(runtime_body["mode"], "headless")
            self.assertEqual(runtime_body["reattach_policy"]["action"], "create-runtime")
            self.assertEqual(runtime_body["runtime_record"]["status"], "stopped")

    def test_headless_notebook_runtime_reports_ambiguous_runtime_candidates(self):
        notebook_path = "notebooks/ambiguous-runtime.ipynb"
        relative_path = notebook_path
        self.state._upsert_runtime_record(
            runtime_id="rt-a",
            mode="shared",
            label="A",
            environment="/tmp/python-a",
            status="idle",
            document_path=relative_path,
        )
        self.state._upsert_runtime_record(
            runtime_id="rt-b",
            mode="shared",
            label="B",
            environment="/tmp/python-b",
            status="idle",
            document_path=relative_path,
        )

        runtime_body, runtime_status = self.state.notebook_runtime(notebook_path)
        self.assertEqual(runtime_status, 200)
        self.assertFalse(runtime_body["active"])
        self.assertEqual(runtime_body["mode"], "headless")
        self.assertEqual(runtime_body["reattach_policy"]["action"], "select-runtime")
        self.assertEqual(set(runtime_body["reattach_policy"]["candidate_runtime_ids"]), {"rt-a", "rt-b"})

    def test_ephemeral_runtime_can_bind_to_notebook_and_expires(self):
        notebook_path = "notebooks/ephemeral.ipynb"
        kernel_python = _python_with_ipykernel()

        runtime = self.state.start_runtime(
            runtime_id="rt-ephemeral",
            mode="ephemeral",
            label="Ephemeral notebook",
            environment=kernel_python,
            document_path=notebook_path,
            ttl_seconds=60,
        )
        self.assertEqual(runtime["runtime"]["mode"], "ephemeral")
        self.assertEqual(runtime["runtime"]["status"], "idle")
        self.assertIsNotNone(runtime["runtime"]["expires_at"])

        runtime_body, runtime_status = self.state.notebook_runtime(notebook_path)
        self.assertEqual(runtime_status, 200)
        self.assertTrue(runtime_body["active"])
        self.assertEqual(runtime_body["runtime_record"]["mode"], "ephemeral")
        self.assertEqual(runtime_body["reattach_policy"]["action"], "attach-live")

        self.state.stop_runtime("rt-ephemeral")
        stopped_body, stopped_status = self.state.notebook_runtime(notebook_path)
        self.assertEqual(stopped_status, 200)
        self.assertFalse(stopped_body["active"])
        self.assertEqual(stopped_body["runtime_record"]["mode"], "ephemeral")
        self.assertEqual(stopped_body["reattach_policy"]["action"], "none")
        self.assertEqual(stopped_body["reattach_policy"]["reason"], "stopped-ephemeral-runtime")

        self.state.runtime_records["rt-ephemeral"].status = "idle"
        self.state.runtime_records["rt-ephemeral"].expires_at = time.time() - 5
        reaped_payload = self.state.list_runtimes_payload()
        reaped = next(item for item in reaped_payload["runtimes"] if item["runtime_id"] == "rt-ephemeral")
        self.assertEqual(reaped["status"], "reaped")

    def test_notebook_bound_runtime_start_reuses_existing_document_identity(self):
        notebook_path = "tmp/existing-runtime.ipynb"
        kernel_python = _python_with_ipykernel()

        create_body, create_status = self.state.notebook_create(notebook_path, cells=[], kernel_id=kernel_python)
        self.assertEqual(create_status, 200)
        self.assertTrue(create_body["ready"])

        self.state.stop_runtime(f"headless:{notebook_path}")
        runtime = self.state.start_runtime(
            runtime_id="rt-ephemeral-alias",
            mode="ephemeral",
            label="Ephemeral alias",
            environment=kernel_python,
            document_path=notebook_path,
            ttl_seconds=60,
        )
        self.assertEqual(runtime["runtime"]["runtime_id"], f"headless:{notebook_path}")
        candidates = self.state._notebook_runtime_candidates(notebook_path)
        self.assertEqual([record.runtime_id for record in candidates], [f"headless:{notebook_path}"])

    def test_recover_runtime_restores_notebook_bound_runtime(self):
        notebook_path = "notebooks/recoverable.ipynb"
        kernel_python = _python_with_ipykernel()

        runtime = self.state.start_runtime(
            runtime_id="rt-recoverable",
            mode="shared",
            label="Recover me",
            environment=kernel_python,
            document_path=notebook_path,
        )
        self.assertEqual(runtime["runtime"]["status"], "idle")
        self.state.stop_runtime("rt-recoverable")
        record = self.state.runtime_records["rt-recoverable"]
        record.status = "recovery-needed"
        record.health = "degraded"

        body, status = self.state.recover_runtime("rt-recoverable")
        self.assertEqual(status, 200)
        self.assertEqual(body["recovered_from"], "recovery-needed")
        self.assertEqual(body["runtime"]["status"], "idle")
        self.assertEqual(body["runtime"]["health"], "healthy")
        self.assertGreaterEqual(body["runtime"]["kernel_generation"], 2)

        runtime_body, runtime_status = self.state.notebook_runtime(notebook_path)
        self.assertEqual(runtime_status, 200)
        self.assertTrue(runtime_body["active"])
        self.assertEqual(runtime_body["reattach_policy"]["action"], "attach-live")

    def test_recover_runtime_rejects_discarded_ephemeral_runtime(self):
        notebook_path = "notebooks/ephemeral-discarded.ipynb"
        kernel_python = _python_with_ipykernel()

        self.state.start_runtime(
            runtime_id="rt-ephemeral-discarded",
            mode="ephemeral",
            label="Ephemeral",
            environment=kernel_python,
            document_path=notebook_path,
            ttl_seconds=60,
        )
        self.state.stop_runtime("rt-ephemeral-discarded")

        body, status = self.state.recover_runtime("rt-ephemeral-discarded")
        self.assertEqual(status, 400)
        self.assertIn("Ephemeral runtime was discarded", body["error"])

    def test_promote_runtime_converts_ephemeral_runtime_to_shared(self):
        notebook_path = "notebooks/ephemeral-promote.ipynb"
        kernel_python = _python_with_ipykernel()

        with mock.patch.object(self.state, "_projection_client", return_value=None):
            runtime = self.state.start_runtime(
                runtime_id="rt-ephemeral-promote",
                mode="ephemeral",
                label="Ephemeral promotion candidate",
                environment=kernel_python,
                document_path=notebook_path,
                ttl_seconds=60,
            )
            self.assertEqual(runtime["runtime"]["mode"], "ephemeral")
            self.assertIsNotNone(runtime["runtime"]["expires_at"])

            promoted_body, promoted_status = self.state.promote_runtime("rt-ephemeral-promote", mode="shared")
            self.assertEqual(promoted_status, 200)
            self.assertEqual(promoted_body["runtime"]["mode"], "shared")
            self.assertIsNone(promoted_body["runtime"]["expires_at"])

            activity_body, activity_status = self.state.notebook_activity(notebook_path)
            self.assertEqual(activity_status, 200)
            promoted_event = next(event for event in activity_body["recent_events"] if event["type"] == "runtime-promoted")
            self.assertEqual(promoted_event["data"]["from_mode"], "ephemeral")
            self.assertEqual(promoted_event["data"]["to_mode"], "shared")

    def test_discard_runtime_marks_ephemeral_runtime_terminal(self):
        notebook_path = "notebooks/ephemeral-discard.ipynb"
        kernel_python = _python_with_ipykernel()

        with mock.patch.object(self.state, "_projection_client", return_value=None):
            runtime = self.state.start_runtime(
                runtime_id="rt-ephemeral-discard",
                mode="ephemeral",
                label="Disposable runtime",
                environment=kernel_python,
                document_path=notebook_path,
                ttl_seconds=60,
            )
            self.assertEqual(runtime["runtime"]["mode"], "ephemeral")

            discarded_body, discarded_status = self.state.discard_runtime("rt-ephemeral-discard")
            self.assertEqual(discarded_status, 200)
            self.assertTrue(discarded_body["discarded"])
            self.assertEqual(discarded_body["runtime"]["status"], "reaped")

            runtime_body, runtime_status = self.state.notebook_runtime(notebook_path)
            self.assertEqual(runtime_status, 200)
            self.assertFalse(runtime_body["active"])
            self.assertEqual(runtime_body["runtime_record"]["status"], "reaped")
            self.assertEqual(runtime_body["reattach_policy"]["action"], "none")

            recover_body, recover_status = self.state.recover_runtime("rt-ephemeral-discard")
            self.assertEqual(recover_status, 400)
            self.assertIn("Ephemeral runtime was discarded", recover_body["error"])

    def test_headless_notebook_projection_returns_runtime_and_contents(self):
        notebook_path = "notebooks/headless-projection.ipynb"
        kernel_python = _python_with_ipykernel()

        with mock.patch.object(self.state, "_projection_client", return_value=None):
            create_body, create_status = self.state.notebook_create(
                notebook_path,
                cells=[{"type": "code", "source": "x = 4\nx"}],
                kernel_id=kernel_python,
            )
            self.assertEqual(create_status, 200)
            self.assertTrue(create_body["ready"])

            exec_body, exec_status = self.state.notebook_execute_cell(
                notebook_path,
                cell_id=None,
                cell_index=0,
            )
            self.assertEqual(exec_status, 200)
            self.assertEqual(exec_body["status"], "ok")

            projection_body, projection_status = self.state.notebook_projection(notebook_path)
            self.assertEqual(projection_status, 200)
            self.assertTrue(projection_body["active"])
            self.assertEqual(projection_body["mode"], "headless")
            self.assertEqual(projection_body["runtime"]["python_path"], os.path.abspath(kernel_python))
            self.assertEqual(len(projection_body["contents"]["cells"]), 1)
            self.assertEqual(projection_body["contents"]["cells"][0]["source"], "x = 4\nx")
            self.assertEqual(
                projection_body["contents"]["cells"][0]["outputs"][0]["data"]["text/plain"],
                "4",
            )

    def test_headless_execute_visible_cell_updates_source_and_outputs(self):
        notebook_path = "notebooks/headless-visible.ipynb"
        kernel_python = _python_with_ipykernel()

        with mock.patch.object(self.state, "_projection_client", return_value=None):
            create_body, create_status = self.state.notebook_create(
                notebook_path,
                cells=[{"type": "code", "source": "x = 1\nx"}],
                kernel_id=kernel_python,
            )
            self.assertEqual(create_status, 200)

            exec_body, exec_status = self.state.notebook_execute_visible_cell(
                notebook_path,
                cell_index=0,
                source="x = 9\nx",
            )
            self.assertEqual(exec_status, 200)
            self.assertEqual(exec_body["status"], "ok")
            self.assertEqual(exec_body["outputs"][0]["data"]["text/plain"], "9")

            contents_body, contents_status = self.state.notebook_contents(notebook_path)
            self.assertEqual(contents_status, 200)
            self.assertEqual(contents_body["cells"][0]["source"], "x = 9\nx")
            self.assertEqual(contents_body["cells"][0]["outputs"][0]["data"]["text/plain"], "9")

    def test_project_visible_and_execute_visible_cell_create_runtime_when_missing(self):
        notebook_path = "notebooks/lazy-runtime.ipynb"
        kernel_python = _python_with_ipykernel()

        with mock.patch.object(self.state, "_projection_client", return_value=None):
            create_body, create_status = self.state.notebook_create(
                notebook_path,
                cells=[{"type": "code", "source": "x = 2\nx"}],
                kernel_id=kernel_python,
            )
            self.assertEqual(create_status, 200)

            real_path = os.path.realpath(os.path.join(str(self.workspace_root), notebook_path))
            self.state._shutdown_headless_runtime(real_path)

            project_body, project_status = self.state.notebook_project_visible(
                notebook_path,
                cells=[{"cell_type": "code", "source": "x = 11\nx", "metadata": {}}],
            )
            self.assertEqual(project_status, 200)
            self.assertEqual(project_body["status"], "ok")

            exec_body, exec_status = self.state.notebook_execute_visible_cell(
                notebook_path,
                cell_index=0,
                source="x = 11\nx",
            )
            self.assertEqual(exec_status, 200)
            self.assertEqual(exec_body["status"], "ok")
            self.assertEqual(exec_body["outputs"][0]["data"]["text/plain"], "11")

            runtime_body, runtime_status = self.state.notebook_runtime(notebook_path)
            self.assertEqual(runtime_status, 200)
            self.assertTrue(runtime_body["active"])
            self.assertEqual(runtime_body["reattach_policy"]["action"], "attach-live")

    def test_headless_runtime_keeps_live_memory_for_next_visible_cell_execution(self):
        notebook_path = "notebooks/headless-continuity.ipynb"
        kernel_python = _python_with_ipykernel()

        with mock.patch.object(self.state, "_projection_client", return_value=None):
            create_body, create_status = self.state.notebook_create(
                notebook_path,
                cells=[
                    {"type": "code", "source": "x = 9\nx"},
                    {"type": "code", "source": "x + 1"},
                ],
                kernel_id=kernel_python,
            )
            self.assertEqual(create_status, 200)
            self.assertTrue(create_body["ready"])

            seed_exec, seed_status = self.state.notebook_execute_cell(notebook_path, cell_id=None, cell_index=0)
            self.assertEqual(seed_status, 200)
            self.assertEqual(seed_exec["status"], "ok")
            self.assertEqual(seed_exec["outputs"][0]["data"]["text/plain"], "9")

            visible_exec, visible_status = self.state.notebook_execute_visible_cell(
                notebook_path,
                cell_index=1,
                source="x + 1",
            )
            self.assertEqual(visible_status, 200)
            self.assertEqual(visible_exec["status"], "ok")
            self.assertEqual(visible_exec["outputs"][0]["data"]["text/plain"], "10")

            runtime_body, runtime_status = self.state.notebook_runtime(notebook_path)
            self.assertEqual(runtime_status, 200)
            self.assertTrue(runtime_body["active"])
            self.assertEqual(runtime_body["mode"], "headless")
            self.assertFalse(runtime_body["runtime"]["busy"])
            self.assertEqual(runtime_body["runtime"]["python_path"], os.path.abspath(kernel_python))

    def test_headless_projection_accepts_new_visible_cell_and_executes_against_live_runtime(self):
        notebook_path = "notebooks/headless-open-later.ipynb"
        kernel_python = _python_with_ipykernel()

        with mock.patch.object(self.state, "_projection_client", return_value=None):
            create_body, create_status = self.state.notebook_create(
                notebook_path,
                cells=[{"type": "code", "source": "x = 9\nx"}],
                kernel_id=kernel_python,
            )
            self.assertEqual(create_status, 200)
            self.assertTrue(create_body["ready"])

            seed_exec, seed_status = self.state.notebook_execute_cell(notebook_path, cell_id=None, cell_index=0)
            self.assertEqual(seed_status, 200)
            self.assertEqual(seed_exec["status"], "ok")
            self.assertEqual(seed_exec["outputs"][0]["data"]["text/plain"], "9")

            project_body, project_status = self.state.notebook_project_visible(
                notebook_path,
                cells=[
                    {
                        "cell_type": "code",
                        "source": "x = 9\nx",
                        "cell_id": seed_exec["cell_id"],
                        "metadata": {"custom": {"agent-repl": {"cell_id": seed_exec["cell_id"]}}},
                    },
                    {
                        "cell_type": "code",
                        "source": "x + 1",
                        "metadata": {},
                    },
                ],
            )
            self.assertEqual(project_status, 200)
            self.assertEqual(project_body["status"], "ok")
            self.assertEqual(project_body["cell_count"], 2)

            visible_exec, visible_status = self.state.notebook_execute_visible_cell(
                notebook_path,
                cell_index=1,
                source="x + 1",
            )
            self.assertEqual(visible_status, 200)
            self.assertEqual(visible_exec["status"], "ok")
            self.assertEqual(visible_exec["outputs"][0]["data"]["text/plain"], "10")

            contents_body, contents_status = self.state.notebook_contents(notebook_path)
            self.assertEqual(contents_status, 200)
            self.assertEqual(len(contents_body["cells"]), 2)
            self.assertEqual(contents_body["cells"][0]["outputs"][0]["data"]["text/plain"], "9")
            self.assertEqual(contents_body["cells"][1]["source"], "x + 1")
            self.assertEqual(contents_body["cells"][1]["outputs"][0]["data"]["text/plain"], "10")

    def test_notebook_activity_reports_multiplayer_live_execution(self):
        notebook_path = "notebooks/live-activity.ipynb"
        kernel_python = _python_with_ipykernel()

        with mock.patch.object(self.state, "_projection_client", return_value=None):
            create_body, create_status = self.state.notebook_create(notebook_path, cells=[], kernel_id=kernel_python)
            self.assertEqual(create_status, 200)
            self.assertTrue(create_body["ready"])

            self.state.start_session("agent", "cli", "worker", "sess-agent")
            self.state.start_session("human", "vscode", "editor", "sess-human", ["projection", "editor", "presence"])
            self.state.upsert_notebook_presence(session_id="sess-agent", path=notebook_path, activity="executing")
            self.state.upsert_notebook_presence(session_id="sess-human", path=notebook_path, activity="observing")

            result_holder: dict[str, Any] = {}

            def run_insert_execute() -> None:
                body, status = self.state.notebook_insert_execute(
                    notebook_path,
                    source="import time\ntime.sleep(0.4)\n21 * 2",
                    cell_type="code",
                    at_index=-1,
                )
                result_holder["body"] = body
                result_holder["status"] = status

            thread = threading.Thread(target=run_insert_execute)
            thread.start()
            live_body: dict[str, Any] | None = None
            deadline = time.time() + 10
            while time.time() < deadline:
                body, status = self.state.notebook_activity(notebook_path)
                self.assertEqual(status, 200)
                if body["current_execution"] and any(event["type"] == "execution-started" for event in body["recent_events"]):
                    live_body = body
                    break
                time.sleep(0.05)
            self.assertIsNotNone(live_body)
            self.assertEqual({item["session_id"] for item in live_body["presence"]}, {"sess-agent", "sess-human"})
            self.assertEqual(live_body["current_execution"]["owner"], "agent")
            execution_lookup, execution_lookup_status = self.state.notebook_execution(
                live_body["current_execution"]["execution_id"]
            )
            self.assertEqual(execution_lookup_status, 200)
            self.assertEqual(execution_lookup["status"], "running")
            self.assertEqual(execution_lookup["cell_id"], live_body["current_execution"]["cell_id"])
            live_cursor = live_body["cursor"]

            thread.join(timeout=10)
            self.assertFalse(thread.is_alive())
            self.assertEqual(result_holder["status"], 200)
            self.assertEqual(result_holder["body"]["outputs"][0]["data"]["text/plain"], "42")

            finished_body, finished_status = self.state.notebook_activity(notebook_path, since=live_cursor)
            self.assertEqual(finished_status, 200)
            self.assertTrue(any(event["type"] == "execution-finished" for event in finished_body["recent_events"]))

    def test_notebook_insert_execute_exposes_inserted_cell_in_contents_while_running(self):
        notebook_path = "notebooks/ix-visible-while-running.ipynb"
        kernel_python = _python_with_ipykernel()

        with mock.patch.object(self.state, "_projection_client", return_value=None):
            create_body, create_status = self.state.notebook_create(notebook_path, cells=[], kernel_id=kernel_python)
            self.assertEqual(create_status, 200)
            self.assertTrue(create_body["ready"])

            entered_execute = threading.Event()
            release_execute = threading.Event()
            original_execute_source = self.state._execute_source
            result_holder: dict[str, Any] = {}

            def delayed_execute_source(*args: Any, **kwargs: Any) -> tuple[list[Any], int | None, str | None]:
                entered_execute.set()
                self.assertTrue(release_execute.wait(timeout=5), "timed out waiting to release ix execution")
                return original_execute_source(*args, **kwargs)

            def run_insert_execute() -> None:
                body, status = self.state.notebook_insert_execute(
                    notebook_path,
                    source="21 * 2",
                    cell_type="code",
                    at_index=-1,
                )
                result_holder["body"] = body
                result_holder["status"] = status

            with mock.patch.object(self.state, "_execute_source", side_effect=delayed_execute_source):
                thread = threading.Thread(target=run_insert_execute)
                thread.start()
                self.assertTrue(entered_execute.wait(timeout=5), "ix execution never started")

                contents_body, contents_status = self.state.notebook_contents(notebook_path)
                self.assertEqual(contents_status, 200)
                self.assertEqual(len(contents_body["cells"]), 1)
                self.assertEqual(contents_body["cells"][0]["source"], "21 * 2")
                self.assertEqual(contents_body["cells"][0]["outputs"], [])
                self.assertTrue(thread.is_alive(), "ix finished before contents could observe the inserted cell")

                release_execute.set()
                thread.join(timeout=10)

            self.assertFalse(thread.is_alive())
            self.assertEqual(result_holder["status"], 200)
            self.assertEqual(result_holder["body"]["outputs"][0]["data"]["text/plain"], "42")

    def test_notebook_activity_cursor_does_not_skip_same_tick_events(self):
        notebook_path = "notebooks/demo.ipynb"

        with mock.patch("agent_repl.core.server.time.time", side_effect=[100.0, 100.0]):
            first_event = self.state._append_activity_event(
                path=notebook_path,
                event_type="execution-finished",
                detail="Finished cell 1",
                cell_id="cell-1",
                cell_index=0,
            )
            second_event = self.state._append_activity_event(
                path=notebook_path,
                event_type="execution-started",
                detail="Executing cell 2",
                cell_id="cell-2",
                cell_index=1,
            )

        self.assertGreater(second_event["timestamp"], first_event["timestamp"])

        body, status = self.state.notebook_activity(notebook_path, since=first_event["timestamp"])
        self.assertEqual(status, 200)
        self.assertEqual(
            [event["event_id"] for event in body["recent_events"]],
            [second_event["event_id"]],
        )

    def test_notebook_edit_emits_cell_source_update_event_with_payload(self):
        notebook_path = "notebooks/source-update-event.ipynb"
        kernel_python = _python_with_ipykernel()

        with mock.patch.object(self.state, "_projection_client", return_value=None):
            create_body, create_status = self.state.notebook_create(
                notebook_path,
                cells=[{"type": "code", "source": "x = 1\nx"}],
                kernel_id=kernel_python,
            )
            self.assertEqual(create_status, 200)
            self.assertTrue(create_body["ready"])

            self.state.start_session("human", "vscode", "editor", "sess-human", ["projection", "editor", "presence"])
            contents_body, contents_status = self.state.notebook_contents(notebook_path)
            self.assertEqual(contents_status, 200)
            cell_id = contents_body["cells"][0]["cell_id"]

            activity_before, activity_status = self.state.notebook_activity(notebook_path)
            self.assertEqual(activity_status, 200)

            edit_body, edit_status = self.state.notebook_edit(
                notebook_path,
                [{"op": "replace-source", "cell_id": cell_id, "source": "x = 2\nx"}],
                owner_session_id="sess-human",
            )
            self.assertEqual(edit_status, 200)
            self.assertEqual(edit_body["results"][0]["cell_id"], cell_id)

            activity_body, activity_status = self.state.notebook_activity(notebook_path, since=activity_before["cursor"])
            self.assertEqual(activity_status, 200)
            source_event = next(event for event in activity_body["recent_events"] if event["type"] == "cell-source-updated")
            self.assertEqual(source_event["session_id"], "sess-human")
            self.assertEqual(source_event["cell_id"], cell_id)
            self.assertEqual(source_event["data"]["cell"]["source"], "x = 2\nx")

    def test_notebook_activity_streams_output_append_events_while_running(self):
        notebook_path = "notebooks/live-output-stream.ipynb"
        kernel_python = _python_with_ipykernel()
        source = "import time\nprint('start', flush=True)\ntime.sleep(0.4)\nprint('done', flush=True)"

        with mock.patch.object(self.state, "_projection_client", return_value=None):
            create_body, create_status = self.state.notebook_create(
                notebook_path,
                cells=[{"type": "code", "source": source}],
                kernel_id=kernel_python,
            )
            self.assertEqual(create_status, 200)
            self.assertTrue(create_body["ready"])

            self.state.start_session("agent", "cli", "worker", "sess-agent", ["projection", "ops", "automation"])
            start_activity, start_status = self.state.notebook_activity(notebook_path)
            self.assertEqual(start_status, 200)

            result_holder: dict[str, Any] = {}

            def run_execution() -> None:
                body, status = self.state.notebook_execute_cell(
                    notebook_path,
                    cell_id=None,
                    cell_index=0,
                    owner_session_id="sess-agent",
                )
                result_holder["body"] = body
                result_holder["status"] = status

            thread = threading.Thread(target=run_execution)
            thread.start()

            stream_event: dict[str, Any] | None = None
            deadline = time.time() + 10
            while time.time() < deadline:
                body, status = self.state.notebook_activity(notebook_path, since=start_activity["cursor"])
                self.assertEqual(status, 200)
                stream_event = next((event for event in body["recent_events"] if event["type"] == "cell-output-appended"), None)
                if stream_event is not None:
                    self.assertEqual(body["current_execution"]["owner"], "agent")
                    break
                time.sleep(0.05)

            self.assertIsNotNone(stream_event)
            self.assertEqual(stream_event["session_id"], "sess-agent")
            self.assertEqual(stream_event["data"]["output"]["output_type"], "stream")
            self.assertIn("start", stream_event["data"]["output"]["text"])
            self.assertEqual(stream_event["data"]["cell"]["source"], source)

            thread.join(timeout=10)
            self.assertFalse(thread.is_alive())
            self.assertEqual(result_holder["status"], 200)
            self.assertEqual(result_holder["body"]["status"], "ok")

    def test_notebook_execute_all_emits_finished_after_output_update_for_each_cell(self):
        notebook_path = "notebooks/run-all-activity-order.ipynb"
        kernel_python = _python_with_ipykernel()

        with mock.patch.object(self.state, "_projection_client", return_value=None):
            create_body, create_status = self.state.notebook_create(
                notebook_path,
                cells=[
                    {"type": "code", "source": "print('one')"},
                    {"type": "code", "source": "print('two')"},
                ],
                kernel_id=kernel_python,
            )
            self.assertEqual(create_status, 200)
            self.assertTrue(create_body["ready"])

            contents_body, contents_status = self.state.notebook_contents(notebook_path)
            self.assertEqual(contents_status, 200)
            cell_ids = [cell["cell_id"] for cell in contents_body["cells"]]

            execute_body, execute_status = self.state.notebook_execute_all(
                notebook_path,
                owner_session_id=None,
            )
            self.assertEqual(execute_status, 200)
            self.assertEqual(execute_body["status"], "ok")

            activity_body, activity_status = self.state.notebook_activity(notebook_path)
            self.assertEqual(activity_status, 200)
            events = activity_body["recent_events"]

            for cell_id in cell_ids:
                cell_events = [event["type"] for event in events if event["cell_id"] == cell_id]
                self.assertIn("cell-outputs-updated", cell_events)
                self.assertIn("execution-finished", cell_events)
                self.assertGreater(
                    max(index for index, event_type in enumerate(cell_events) if event_type == "execution-finished"),
                    max(index for index, event_type in enumerate(cell_events) if event_type == "cell-outputs-updated"),
                )

    def test_cell_leases_resolve_cells_under_notebook_lock(self):
        import nbformat

        notebook_path = "notebooks/lease-locking.ipynb"
        real_path = os.path.realpath(str(self.workspace_root / notebook_path))
        notebook = nbformat.v4.new_notebook(cells=[nbformat.v4.new_code_cell(source="x = 1")])
        lock_state = {"entered": False}

        @contextmanager
        def guarded_notebook_lock(path: str):
            self.assertEqual(path, real_path)
            lock_state["entered"] = True
            try:
                yield
            finally:
                lock_state["entered"] = False

        def load_notebook(path: str):
            self.assertTrue(lock_state["entered"])
            self.assertEqual(path, real_path)
            return notebook, False

        with (
            mock.patch.object(self.state, "_notebook_lock", side_effect=guarded_notebook_lock),
            mock.patch.object(self.state, "_load_notebook", side_effect=load_notebook),
            mock.patch.object(self.state, "_save_notebook"),
        ):
            self.state.start_session("human", "vscode", "editor", "sess-human", ["projection", "editor", "presence"])

            lease_body, lease_status = self.state.acquire_cell_lease(
                session_id="sess-human",
                path=notebook_path,
                cell_index=0,
            )
            self.assertEqual(lease_status, 200)
            self.assertEqual(lease_body["lease"]["session_id"], "sess-human")

            release_body, release_status = self.state.release_cell_lease(
                session_id="sess-human",
                path=notebook_path,
                cell_index=0,
            )
            self.assertEqual(release_status, 200)
            self.assertTrue(release_body["released"])

    def test_notebook_activity_tolerates_live_presence_updates(self):
        notebook_path = "notebooks/presence-race.ipynb"
        kernel_python = _python_with_ipykernel()

        with mock.patch.object(self.state, "_projection_client", return_value=None):
            create_body, create_status = self.state.notebook_create(notebook_path, cells=[], kernel_id=kernel_python)
            self.assertEqual(create_status, 200)
            self.assertTrue(create_body["ready"])

            self.state.start_session("agent", "cli", "worker", "sess-agent")
            self.state.start_session("human", "vscode", "editor", "sess-human", ["projection", "editor", "presence"])
            failures: list[Exception] = []
            stop_event = threading.Event()

            def churn_presence() -> None:
                try:
                    while not stop_event.is_set():
                        self.state.upsert_notebook_presence(session_id="sess-agent", path=notebook_path, activity="executing")
                        self.state.upsert_notebook_presence(session_id="sess-human", path=notebook_path, activity="observing")
                        self.state.clear_notebook_presence(session_id="sess-human", path=notebook_path)
                except Exception as err:  # pragma: no cover - defensive concurrency test
                    failures.append(err)

            thread = threading.Thread(target=churn_presence)
            thread.start()
            try:
                deadline = time.time() + 1.0
                while time.time() < deadline:
                    body, status = self.state.notebook_activity(notebook_path)
                    self.assertEqual(status, 200)
                    self.assertEqual(body["status"], "ok")
                    time.sleep(0.01)
            finally:
                stop_event.set()
                thread.join(timeout=5)

            self.assertFalse(thread.is_alive())
            self.assertEqual(failures, [])

    def test_cell_lease_blocks_cross_session_execution_until_it_expires(self):
        notebook_path = "notebooks/leased-cell.ipynb"
        kernel_python = _python_with_ipykernel()

        with mock.patch.object(self.state, "_projection_client", return_value=None):
            create_body, create_status = self.state.notebook_create(
                notebook_path,
                cells=[{"type": "code", "source": "21 * 2"}],
                kernel_id=kernel_python,
            )
            self.assertEqual(create_status, 200)
            self.assertTrue(create_body["ready"])

            self.state.start_session("human", "vscode", "editor", "sess-human", ["projection", "editor", "presence"])
            self.state.start_session("agent", "cli", "worker", "sess-agent")

            contents_body, contents_status = self.state.notebook_contents(notebook_path)
            self.assertEqual(contents_status, 200)
            cell_id = contents_body["cells"][0]["cell_id"]

            lease_body, lease_status = self.state.acquire_cell_lease(
                session_id="sess-human",
                path=notebook_path,
                cell_id=cell_id,
            )
            self.assertEqual(lease_status, 200)
            self.assertEqual(lease_body["lease"]["session_id"], "sess-human")

            activity_body, activity_status = self.state.notebook_activity(notebook_path)
            self.assertEqual(activity_status, 200)
            self.assertEqual(len(activity_body["leases"]), 1)
            self.assertEqual(activity_body["leases"][0]["cell_id"], cell_id)

            conflict_body, conflict_status = self.state.notebook_execute_visible_cell(
                notebook_path,
                cell_index=0,
                source="84 // 2",
                owner_session_id="sess-agent",
            )
            self.assertEqual(conflict_status, 409)
            self.assertEqual(conflict_body["conflict"]["lease"]["session_id"], "sess-human")

            allowed_body, allowed_status = self.state.notebook_execute_visible_cell(
                notebook_path,
                cell_index=0,
                source="84 // 2",
                owner_session_id="sess-human",
            )
            self.assertEqual(allowed_status, 200)
            self.assertEqual(allowed_body["outputs"][0]["data"]["text/plain"], "42")

            lease_key = self.state._lease_key("notebooks/leased-cell.ipynb", cell_id)
            self.state.cell_leases[lease_key].expires_at = time.time() - 1

            retry_body, retry_status = self.state.notebook_execute_visible_cell(
                notebook_path,
                cell_index=0,
                source="6 * 7",
                owner_session_id="sess-agent",
            )
            self.assertEqual(retry_status, 200)
            self.assertEqual(retry_body["outputs"][0]["data"]["text/plain"], "42")

    def test_structure_lease_blocks_cross_session_insert_and_projection(self):
        notebook_path = "notebooks/structure-lease.ipynb"
        kernel_python = _python_with_ipykernel()

        with mock.patch.object(self.state, "_projection_client", return_value=None):
            create_body, create_status = self.state.notebook_create(
                notebook_path,
                cells=[{"type": "code", "source": "x = 1\nx"}],
                kernel_id=kernel_python,
            )
            self.assertEqual(create_status, 200)
            self.assertTrue(create_body["ready"])

            self.state.start_session("human", "vscode", "editor", "sess-human", ["projection", "editor", "presence"])
            self.state.start_session("agent", "cli", "worker", "sess-agent")

            contents_body, contents_status = self.state.notebook_contents(notebook_path)
            self.assertEqual(contents_status, 200)
            cell_id = contents_body["cells"][0]["cell_id"]

            lease_body, lease_status = self.state.acquire_cell_lease(
                session_id="sess-human",
                path=notebook_path,
                cell_id=cell_id,
                kind="structure",
            )
            self.assertEqual(lease_status, 200)
            self.assertEqual(lease_body["lease"]["kind"], "structure")

            insert_body, insert_status = self.state.notebook_insert_execute(
                notebook_path,
                source="x + 1",
                cell_type="code",
                at_index=-1,
                owner_session_id="sess-agent",
            )
            self.assertEqual(insert_status, 409)
            self.assertEqual(insert_body["conflict"]["lease"]["kind"], "structure")

            project_body, project_status = self.state.notebook_project_visible(
                notebook_path,
                cells=[
                    {
                        "cell_type": "code",
                        "source": "x = 1\nx",
                        "cell_id": cell_id,
                        "metadata": {"custom": {"agent-repl": {"cell_id": cell_id}}},
                    },
                    {
                        "cell_type": "markdown",
                        "source": "blocked",
                        "metadata": {},
                    },
                ],
                owner_session_id="sess-agent",
            )
            self.assertEqual(project_status, 409)
            self.assertEqual(project_body["conflict"]["operation"], "project-visible-notebook")

            own_project_body, own_project_status = self.state.notebook_project_visible(
                notebook_path,
                cells=[
                    {
                        "cell_type": "code",
                        "source": "x = 1\nx",
                        "cell_id": cell_id,
                        "metadata": {"custom": {"agent-repl": {"cell_id": cell_id}}},
                    },
                    {
                        "cell_type": "markdown",
                        "source": "allowed",
                        "metadata": {},
                    },
                ],
                owner_session_id="sess-human",
            )
            self.assertEqual(own_project_status, 200)
            self.assertEqual(own_project_body["cell_count"], 2)

    def test_headless_edit_delete_and_move_update_structure(self):
        notebook_path = "notebooks/headless-structure.ipynb"
        kernel_python = _python_with_ipykernel()

        with mock.patch.object(self.state, "_projection_client", return_value=None):
            create_body, create_status = self.state.notebook_create(
                notebook_path,
                cells=[
                    {"type": "markdown", "source": "# Title"},
                    {"type": "code", "source": "x = 1\nx"},
                    {"type": "code", "source": "x + 1"},
                ],
                kernel_id=kernel_python,
            )
            self.assertEqual(create_status, 200)
            self.assertTrue(create_body["ready"])

            contents_body, contents_status = self.state.notebook_contents(notebook_path)
            self.assertEqual(contents_status, 200)
            code_cell_id = contents_body["cells"][2]["cell_id"]

            move_body, move_status = self.state.notebook_edit(
                notebook_path,
                [{"op": "move", "cell_id": code_cell_id, "to_index": 1}],
            )
            self.assertEqual(move_status, 200)
            self.assertTrue(move_body["results"][0]["changed"])

            moved_body, moved_status = self.state.notebook_contents(notebook_path)
            self.assertEqual(moved_status, 200)
            self.assertEqual(moved_body["cells"][1]["cell_id"], code_cell_id)

            delete_body, delete_status = self.state.notebook_edit(
                notebook_path,
                [{"op": "delete", "cell_id": code_cell_id}],
            )
            self.assertEqual(delete_status, 200)
            self.assertTrue(delete_body["results"][0]["changed"])

            deleted_body, deleted_status = self.state.notebook_contents(notebook_path)
            self.assertEqual(deleted_status, 200)
            self.assertEqual(len(deleted_body["cells"]), 2)
            self.assertNotIn(code_cell_id, [cell["cell_id"] for cell in deleted_body["cells"]])

    def test_headless_delete_can_remove_a_running_cell_without_resurrecting_it(self):
        notebook_path = "notebooks/headless-delete-running.ipynb"
        kernel_python = _python_with_ipykernel()
        execution_started = threading.Event()
        allow_finish = threading.Event()
        execution_result: dict[str, Any] = {}
        edit_result: dict[str, Any] = {}

        def fake_execute_source(*args, **kwargs):
            execution_started.set()
            self.assertTrue(allow_finish.wait(timeout=5), "test did not release the mocked execution")
            return [], 1, None

        with (
            mock.patch.object(self.state, "_projection_client", return_value=None),
            mock.patch.object(self.state, "_execute_source", side_effect=fake_execute_source),
        ):
            create_body, create_status = self.state.notebook_create(
                notebook_path,
                cells=[
                    {"type": "code", "source": "import time\ntime.sleep(5)"},
                    {"type": "code", "source": "print('still here')"},
                ],
                kernel_id=kernel_python,
            )
            self.assertEqual(create_status, 200)
            self.assertTrue(create_body["ready"])

            contents_body, contents_status = self.state.notebook_contents(notebook_path)
            self.assertEqual(contents_status, 200)
            running_cell_id = contents_body["cells"][0]["cell_id"]

            def run_execution():
                execution_result["value"] = self.state.notebook_execute_cell(
                    notebook_path,
                    cell_id=running_cell_id,
                    cell_index=None,
                )

            def delete_running_cell():
                edit_result["value"] = self.state.notebook_edit(
                    notebook_path,
                    [{"op": "delete", "cell_id": running_cell_id}],
                )

            execution_thread = threading.Thread(target=run_execution, daemon=True)
            execution_thread.start()
            self.assertTrue(execution_started.wait(timeout=5), "mocked execution never started")

            edit_thread = threading.Thread(target=delete_running_cell, daemon=True)
            edit_thread.start()
            edit_thread.join(timeout=2)
            self.assertFalse(edit_thread.is_alive(), "delete should not wait for the running cell to finish")

            allow_finish.set()
            execution_thread.join(timeout=5)
            self.assertFalse(execution_thread.is_alive(), "mocked execution should finish after release")

            edit_body, edit_status = edit_result["value"]
            self.assertEqual(edit_status, 200)
            self.assertTrue(edit_body["results"][0]["changed"])

            exec_body, exec_status = execution_result["value"]
            self.assertEqual(exec_status, 200)
            self.assertEqual(exec_body["status"], "ok")

            final_body, final_status = self.state.notebook_contents(notebook_path)
            self.assertEqual(final_status, 200)
            self.assertEqual(len(final_body["cells"]), 1)
            self.assertNotIn(running_cell_id, [cell["cell_id"] for cell in final_body["cells"]])

    def test_headless_clear_outputs_all_clears_every_code_cell(self):
        notebook_path = "notebooks/headless-clear-outputs.ipynb"
        kernel_python = _python_with_ipykernel()

        with mock.patch.object(self.state, "_projection_client", return_value=None):
            create_body, create_status = self.state.notebook_create(
                notebook_path,
                cells=[
                    {"type": "code", "source": "x = 2\nx"},
                    {"type": "code", "source": "x + 5"},
                ],
                kernel_id=kernel_python,
            )
            self.assertEqual(create_status, 200)
            self.assertTrue(create_body["ready"])

            first_exec, first_status = self.state.notebook_execute_cell(notebook_path, cell_id=None, cell_index=0)
            self.assertEqual(first_status, 200)
            self.assertEqual(first_exec["status"], "ok")
            second_exec, second_status = self.state.notebook_execute_cell(notebook_path, cell_id=None, cell_index=1)
            self.assertEqual(second_status, 200)
            self.assertEqual(second_exec["status"], "ok")

            clear_body, clear_status = self.state.notebook_edit(
                notebook_path,
                [{"op": "clear-outputs", "all": True}],
            )
            self.assertEqual(clear_status, 200)
            self.assertTrue(clear_body["results"][0]["changed"])

            contents_body, contents_status = self.state.notebook_contents(notebook_path)
            self.assertEqual(contents_status, 200)
            self.assertEqual(contents_body["cells"][0]["outputs"], [])
            self.assertEqual(contents_body["cells"][1]["outputs"], [])

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

    def test_resolve_preferred_session_prefers_attached_editor_capable_human(self):
        self.state.start_session("human", "browser", "browser", "sess-browser", ["projection", "presence"])
        self.state.start_session("human", "vscode", "editor", "sess-vscode", ["projection", "editor", "presence"])
        payload = self.state.resolve_preferred_session("human")
        self.assertEqual(payload["session"]["session_id"], "sess-vscode")

    def test_resolve_preferred_session_returns_none_when_no_matching_actor_exists(self):
        self.state.start_session("agent", "cli", "worker", "sess-agent", ["projection", "ops"])
        payload = self.state.resolve_preferred_session("human")
        self.assertIsNone(payload["session"])

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

    def test_branch_review_can_be_requested_and_resolved(self):
        requester = self.state.start_session("agent", "cli", "worker", "sess-agent")
        reviewer = self.state.start_session("human", "vscode", "editor", "sess-human", ["projection", "editor", "presence"])
        opened, status = self.state.open_document("notebooks/reviewable.ipynb")
        self.assertEqual(status, 200)

        branch_body, branch_status = self.state.start_branch(
            branch_id="branch-review",
            document_id=opened["document"]["document_id"],
            owner_session_id=requester["session"]["session_id"],
            parent_branch_id=None,
            title="Review me",
            purpose="Draft risky edit",
        )
        self.assertEqual(branch_status, 200)

        review_body, review_status = self.state.request_branch_review(
            branch_id="branch-review",
            requested_by_session_id="sess-agent",
            note="Please review the draft",
        )
        self.assertEqual(review_status, 200)
        self.assertEqual(review_body["branch"]["review_status"], "requested")
        self.assertEqual(review_body["branch"]["review_requested_by_session_id"], "sess-agent")

        activity_body, activity_status = self.state.notebook_activity("notebooks/reviewable.ipynb")
        self.assertEqual(activity_status, 200)
        self.assertTrue(any(event["type"] == "review-requested" for event in activity_body["recent_events"]))

        resolved_body, resolved_status = self.state.resolve_branch_review(
            branch_id="branch-review",
            resolved_by_session_id="sess-human",
            resolution="approved",
            note="Looks good",
        )
        self.assertEqual(resolved_status, 200)
        self.assertEqual(resolved_body["branch"]["review_status"], "resolved")
        self.assertEqual(resolved_body["branch"]["review_resolution"], "approved")
        self.assertEqual(resolved_body["branch"]["review_resolved_by_session_id"], "sess-human")

        resolved_activity, resolved_activity_status = self.state.notebook_activity("notebooks/reviewable.ipynb")
        self.assertEqual(resolved_activity_status, 200)
        self.assertTrue(any(event["type"] == "review-resolved" for event in resolved_activity["recent_events"]))

    def test_lease_conflict_payload_suggests_branch_handoff(self):
        notebook_path = "notebooks/conflict-handoff.ipynb"
        kernel_python = _python_with_ipykernel()

        with mock.patch.object(self.state, "_projection_client", return_value=None):
            create_body, create_status = self.state.notebook_create(
                notebook_path,
                cells=[{"type": "code", "source": "21 * 2"}],
                kernel_id=kernel_python,
            )
            self.assertEqual(create_status, 200)
            self.assertTrue(create_body["ready"])
            opened, opened_status = self.state.open_document(notebook_path)
            self.assertEqual(opened_status, 200)

            self.state.start_session("human", "vscode", "editor", "sess-human", ["projection", "editor", "presence"])
            self.state.start_session("agent", "cli", "worker", "sess-agent")

            lease_body, lease_status = self.state.acquire_cell_lease(
                session_id="sess-human",
                path=notebook_path,
                cell_index=0,
            )
            self.assertEqual(lease_status, 200)

            conflict_body, conflict_status = self.state.notebook_execute_visible_cell(
                notebook_path,
                cell_index=0,
                source="84 // 2",
                owner_session_id="sess-agent",
            )
            self.assertEqual(conflict_status, 409)
            suggested_branch = conflict_body["conflict"]["suggested_branch"]
            self.assertEqual(suggested_branch["action"], "branch-start")
            self.assertEqual(suggested_branch["document_id"], opened["document"]["document_id"])
            self.assertEqual(suggested_branch["owner_session_id"], "sess-agent")
            self.assertEqual(conflict_body["recovery"]["reason"], "lease-conflict")
            self.assertIn("Refresh the notebook surface", conflict_body["recovery"]["suggestions"][0])

    def test_finish_run_keeps_runtime_busy_while_another_run_is_active(self):
        opened, status = self.state.open_document("notebooks/demo.ipynb")
        self.assertEqual(status, 200)
        document_id = opened["document"]["document_id"]
        runtime = self.state.start_runtime(runtime_id="rt-1", mode="shared", label=None, environment=None)
        self.assertEqual(runtime["runtime"]["status"], "idle")

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
        self.assertEqual(first_body["run"]["status"], "running")
        self.assertIsNone(first_body["run"]["queue_position"])
        self.assertEqual(second_body["run"]["status"], "queued")
        self.assertEqual(second_body["run"]["queue_position"], 1)

        finish_body, finish_status = self.state.finish_run(first_body["run"]["run_id"], "completed")
        self.assertEqual(finish_status, 200)
        self.assertEqual(self.state.runtime_records["rt-1"].status, "busy")
        self.assertEqual(self.state.run_records["run-2"].status, "running")
        self.assertIsNone(self.state.run_records["run-2"].queue_position)

        final_body, final_status = self.state.finish_run(second_body["run"]["run_id"], "completed")
        self.assertEqual(final_status, 200)
        self.assertEqual(self.state.runtime_records["rt-1"].status, "idle")

    def test_queue_promotion_emits_activity_event(self):
        opened, status = self.state.open_document("notebooks/demo.ipynb")
        self.assertEqual(status, 200)
        document_id = opened["document"]["document_id"]
        self.state.start_runtime(runtime_id="rt-1", mode="shared", label=None, environment=None)

        self.state.start_run(
            run_id="run-1",
            runtime_id="rt-1",
            target_type="document",
            target_ref=document_id,
            kind="execute",
        )
        self.state.start_run(
            run_id="run-2",
            runtime_id="rt-1",
            target_type="document",
            target_ref=document_id,
            kind="execute",
        )
        events_before = len(self.state.activity_records)
        self.state.finish_run("run-1", "completed")

        promotion_events = [
            event for event in self.state.activity_records[events_before:]
            if event.type == "queue-promotion"
        ]
        self.assertEqual(len(promotion_events), 1)
        self.assertIn("run-2", promotion_events[0].detail)

    def test_start_run_recomputes_queue_positions_for_multiple_waiting_runs(self):
        opened, status = self.state.open_document("notebooks/demo.ipynb")
        self.assertEqual(status, 200)
        document_id = opened["document"]["document_id"]
        self.state.start_runtime(runtime_id="rt-1", mode="shared", label=None, environment=None)

        self.state.start_run(
            run_id="run-1",
            runtime_id="rt-1",
            target_type="document",
            target_ref=document_id,
            kind="execute",
        )
        second_body, second_status = self.state.start_run(
            run_id="run-2",
            runtime_id="rt-1",
            target_type="document",
            target_ref=document_id,
            kind="execute",
        )
        self.assertEqual(second_status, 200)
        third_body, third_status = self.state.start_run(
            run_id="run-3",
            runtime_id="rt-1",
            target_type="document",
            target_ref=document_id,
            kind="execute",
        )
        self.assertEqual(third_status, 200)
        self.assertEqual(second_body["run"]["queue_position"], 1)
        self.assertEqual(third_body["run"]["queue_position"], 2)

        finish_body, finish_status = self.state.finish_run("run-1", "completed")
        self.assertEqual(finish_status, 200)
        self.assertEqual(finish_body["run"]["status"], "completed")
        self.assertEqual(self.state.run_records["run-2"].status, "running")
        self.assertEqual(self.state.run_records["run-3"].status, "queued")
        self.assertEqual(self.state.run_records["run-3"].queue_position, 1)

    def test_notebook_status_reports_server_owned_running_and_queued_runs(self):
        opened, status = self.state.open_document("notebooks/demo.ipynb")
        self.assertEqual(status, 200)
        document_id = opened["document"]["document_id"]
        runtime = self.state.start_runtime(
            runtime_id="rt-headless",
            mode="shared",
            label=None,
            environment=_python_with_ipykernel(),
            document_path="notebooks/demo.ipynb",
        )
        runtime_id = runtime["runtime"]["runtime_id"]
        self.state.start_run(
            run_id="run-1",
            runtime_id=runtime_id,
            target_type="document",
            target_ref=document_id,
            kind="execute",
        )
        self.state.start_run(
            run_id="run-2",
            runtime_id=runtime_id,
            target_type="document",
            target_ref=document_id,
            kind="execute",
        )

        body, status = self.state.notebook_status("notebooks/demo.ipynb")

        self.assertEqual(status, 200)
        self.assertEqual([item["run_id"] for item in body["running"]], ["run-1"])
        self.assertEqual([item["run_id"] for item in body["queued"]], ["run-2"])
        self.assertEqual(body["queued"][0]["queue_position"], 1)

    def test_notebook_activity_reports_server_owned_running_and_queued_runs(self):
        opened, status = self.state.open_document("notebooks/demo.ipynb")
        self.assertEqual(status, 200)
        document_id = opened["document"]["document_id"]
        runtime = self.state.start_runtime(
            runtime_id="rt-headless",
            mode="shared",
            label=None,
            environment=_python_with_ipykernel(),
            document_path="notebooks/demo.ipynb",
        )
        runtime_id = runtime["runtime"]["runtime_id"]
        self.state.start_run(
            run_id="run-1",
            runtime_id=runtime_id,
            target_type="document",
            target_ref=document_id,
            kind="execute",
        )
        self.state.start_run(
            run_id="run-2",
            runtime_id=runtime_id,
            target_type="document",
            target_ref=document_id,
            kind="execute",
        )

        body, status = self.state.notebook_activity("notebooks/demo.ipynb")

        self.assertEqual(status, 200)
        self.assertEqual([item["run_id"] for item in body["running"]], ["run-1"])
        self.assertEqual([item["run_id"] for item in body["queued"]], ["run-2"])
        self.assertEqual(body["queued"][0]["queue_position"], 1)

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

    def test_start_run_rejects_reaped_runtime(self):
        self.state.start_runtime(runtime_id="rt-ephemeral", mode="ephemeral", label=None, environment=None, ttl_seconds=1)
        self.state.runtime_records["rt-ephemeral"].expires_at = time.time() - 5
        body, status = self.state.start_run(
            run_id="run-expired",
            runtime_id="rt-ephemeral",
            target_type="document",
            target_ref="doc-missing",
            kind="execute",
        )
        self.assertEqual(status, 400)
        self.assertIn("Runtime is not runnable", body["error"])

    def test_start_run_rejects_runtime_that_requires_recovery(self):
        self.state.start_runtime(runtime_id="rt-1", mode="shared", label=None, environment=None)
        self.state.runtime_records["rt-1"].status = "recovery-needed"
        body, status = self.state.start_run(
            run_id="run-recover",
            runtime_id="rt-1",
            target_type="document",
            target_ref="doc-missing",
            kind="execute",
        )
        self.assertEqual(status, 400)
        self.assertIn("Runtime requires recovery", body["error"])

    def test_start_run_rejects_degraded_runtime(self):
        self.state.start_runtime(runtime_id="rt-1", mode="shared", label=None, environment=None)
        self.state.runtime_records["rt-1"].status = "degraded"
        body, status = self.state.start_run(
            run_id="run-degraded",
            runtime_id="rt-1",
            target_type="document",
            target_ref="doc-missing",
            kind="execute",
        )
        self.assertEqual(status, 400)
        self.assertIn("must be recovered", body["error"])

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
        self.state._append_activity_event(
            path="notebooks/demo.ipynb",
            event_type="runtime-state-changed",
            detail="Runtime rt-1 transitioned idle -> busy",
            runtime_id="rt-1",
            data={"from_status": "idle", "to_status": "busy"},
        )
        self.state.persist()

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
        self.assertTrue(any(event.type == "runtime-state-changed" for event in restored.activity_records))
        db_path = Path(str(self.workspace_root)) / ".agent-repl" / "core-state.db"
        self.assertTrue(db_path.exists())

    def test_runtime_state_changes_are_visible_in_notebook_activity(self):
        notebook_path = "notebooks/runtime-transitions.ipynb"
        kernel_python = _python_with_ipykernel()

        with mock.patch.object(self.state, "_projection_client", return_value=None):
            create_body, create_status = self.state.notebook_create(
                notebook_path,
                cells=[{"type": "code", "source": "21 * 2"}],
                kernel_id=kernel_python,
            )
            self.assertEqual(create_status, 200)
            self.assertTrue(create_body["ready"])

            runtime_body, runtime_status = self.state.notebook_runtime(notebook_path)
            self.assertEqual(runtime_status, 200)
            runtime_id = runtime_body["runtime"]["runtime_id"]

            activity_before, activity_status = self.state.notebook_activity(notebook_path)
            self.assertEqual(activity_status, 200)

            stopped_body, stopped_status = self.state.stop_runtime(runtime_id)
            self.assertEqual(stopped_status, 200)
            self.assertEqual(stopped_body["runtime"]["status"], "stopped")

            activity_after, activity_status = self.state.notebook_activity(notebook_path, since=activity_before["cursor"])
            self.assertEqual(activity_status, 200)
            transitions = [
                event["data"]["to_status"]
                for event in activity_after["recent_events"]
                if event["type"] == "runtime-state-changed"
            ]
            self.assertIn("draining", transitions)
            self.assertIn("stopped", transitions)

    def test_select_kernel_changes_headless_runtime(self):
        notebook_path = "notebooks/select-kernel.ipynb"
        kernel_python = _python_with_ipykernel()

        with mock.patch.object(self.state, "_projection_client", return_value=None):
            create_body, create_status = self.state.notebook_create(
                notebook_path,
                cells=[{"type": "code", "source": "import sys; sys.executable"}],
                kernel_id=kernel_python,
            )
            self.assertEqual(create_status, 200)

            # select-kernel with the same python should succeed without restart
            select_body, select_status = self.state.notebook_select_kernel(
                notebook_path, kernel_id=kernel_python,
            )
            self.assertEqual(select_status, 200)
            self.assertEqual(select_body["status"], "ok")
            self.assertEqual(select_body["mode"], "headless")
            self.assertEqual(select_body["kernel"]["python"], os.path.abspath(kernel_python))

    def test_select_kernel_without_kernel_id_uses_workspace_venv(self):
        # Create a fake .venv that we'll mock as kernel-capable
        venv_python = self.workspace_root / ".venv" / "bin" / "python"
        venv_python.parent.mkdir(parents=True)
        venv_python.write_text("#!/bin/sh\n")
        venv_python.chmod(0o755)

        with (
            mock.patch.object(self.state, "_projection_client", return_value=None),
            mock.patch.object(self.state, "_ensure_kernel_capable_python"),
            mock.patch.object(self.state, "_ensure_headless_runtime"),
        ):
            select_body, select_status = self.state.notebook_select_kernel(
                "notebooks/demo.ipynb", kernel_id=None,
            )
            self.assertEqual(select_status, 200)
            self.assertIn(".venv", select_body["kernel"]["python"])

    def test_insert_execute_does_not_mutate_notebook_on_kernel_failure(self):
        notebook_path = "notebooks/ix-rollback.ipynb"

        with mock.patch.object(self.state, "_projection_client", return_value=None):
            # Create the notebook file first
            create_body, create_status = self.state.notebook_create(
                notebook_path, cells=None, kernel_id=_python_with_ipykernel(),
            )
            self.assertEqual(create_status, 200)

            # Shut down the runtime so next execution needs to recreate
            real_path = os.path.realpath(os.path.join(str(self.workspace_root), notebook_path))
            self.state._shutdown_headless_runtime(real_path)

            # Count cells before failed ix
            contents_before, _ = self.state.notebook_contents(notebook_path)
            cell_count_before = len(contents_before["cells"])

            # Mock _ensure_headless_runtime to fail (simulating kernel resolution failure)
            with mock.patch.object(
                self.state, "_ensure_headless_runtime",
                side_effect=RuntimeError("Kernel not capable"),
            ):
                with self.assertRaises(RuntimeError):
                    self.state.notebook_insert_execute(
                        notebook_path,
                        source="print('should not persist')",
                        cell_type="code",
                        at_index=-1,
                    )

            # Cell count should be unchanged
            contents_after, _ = self.state.notebook_contents(notebook_path)
            self.assertEqual(len(contents_after["cells"]), cell_count_before)

    def test_insert_execute_rolls_back_cell_on_infra_error(self):
        notebook_path = "notebooks/ix-infra-rollback.ipynb"
        kernel_python = _python_with_ipykernel()

        with mock.patch.object(self.state, "_projection_client", return_value=None):
            create_body, create_status = self.state.notebook_create(
                notebook_path, cells=[{"type": "code", "source": "x = 1"}], kernel_id=kernel_python,
            )
            self.assertEqual(create_status, 200)

            contents_before, _ = self.state.notebook_contents(notebook_path)
            cell_count_before = len(contents_before["cells"])

            # Mock _execute_source to raise (simulating kernel crash / connection lost)
            with mock.patch.object(
                self.state, "_execute_source",
                side_effect=RuntimeError("Kernel connection lost"),
            ):
                with self.assertRaisesRegex(RuntimeError, "ix failed and the inserted cell was rolled back"):
                    self.state.notebook_insert_execute(
                        notebook_path,
                        source="print('should be rolled back')",
                        cell_type="code",
                        at_index=-1,
                    )

            # Cell should have been rolled back
            contents_after, _ = self.state.notebook_contents(notebook_path)
            self.assertEqual(len(contents_after["cells"]), cell_count_before)

    def test_kernel_capable_error_message_includes_install_hint(self):
        with mock.patch("agent_repl.core.server.subprocess.run") as mock_run:
            mock_run.return_value = mock.Mock(returncode=1, stderr="No module named 'ipykernel'", stdout="")
            # Clear validation cache
            self.state._validated_kernel_pythons.clear()
            with self.assertRaisesRegex(RuntimeError, "pip install ipykernel"):
                self.state._ensure_kernel_capable_python("/fake/python")

    def test_cat_does_not_mutate_notebook_on_disk(self):
        notebook_path = "notebooks/demo.ipynb"
        real_path = os.path.join(str(self.workspace_root), notebook_path)
        # Write a notebook without agent-repl cell IDs
        import nbformat
        nb = nbformat.v4.new_notebook(cells=[
            nbformat.v4.new_code_cell(source="x = 1"),
            nbformat.v4.new_markdown_cell(source="# hello"),
        ])
        Path(real_path).parent.mkdir(parents=True, exist_ok=True)
        with open(real_path, "w") as f:
            nbformat.write(nb, f)
        before = Path(real_path).read_text()

        with mock.patch.object(self.state, "_projection_client", return_value=None):
            body, status = self.state.notebook_contents(notebook_path)
            self.assertEqual(status, 200)
            self.assertEqual(len(body["cells"]), 2)
            # Cell IDs should be in the response
            self.assertTrue(body["cells"][0]["cell_id"])

        # File should be identical — cat must not write
        after = Path(real_path).read_text()
        self.assertEqual(before, after)

    def test_concurrent_edits_do_not_corrupt_notebook(self):
        notebook_path = "notebooks/concurrent.ipynb"
        kernel_python = _python_with_ipykernel()

        with mock.patch.object(self.state, "_projection_client", return_value=None):
            create_body, create_status = self.state.notebook_create(
                notebook_path,
                cells=[
                    {"type": "code", "source": "a = 1"},
                    {"type": "code", "source": "b = 2"},
                    {"type": "code", "source": "c = 3"},
                ],
                kernel_id=kernel_python,
            )
            self.assertEqual(create_status, 200)

            contents, _ = self.state.notebook_contents(notebook_path)
            cell_ids = [c["cell_id"] for c in contents["cells"]]

            errors = []
            def delete_cell(cid):
                try:
                    self.state.notebook_edit(notebook_path, [{"op": "delete", "cell_id": cid}])
                except Exception as e:
                    errors.append(str(e))

            t1 = threading.Thread(target=delete_cell, args=(cell_ids[2],))
            t2 = threading.Thread(target=delete_cell, args=(cell_ids[1],))
            t1.start()
            t2.start()
            t1.join()
            t2.join()

            # File should be valid JSON regardless of race
            real_path = os.path.join(str(self.workspace_root), notebook_path)
            content = Path(real_path).read_text()
            import json
            parsed = json.loads(content)  # should not raise
            self.assertIn("cells", parsed)
            # Should have exactly 1 cell left
            self.assertEqual(len(parsed["cells"]), 1)


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

    def test_open_calls_post(self):
        self.client.open("demo.ipynb")
        url = self.mock_post.call_args[0][0]
        self.assertIn("/api/notebook/open", url)
        self.assertEqual(
            self.mock_post.call_args.kwargs["json"],
            {"path": "demo.ipynb", "cwd": "/workspace", "editor": "canvas", "target": "vscode"},
        )

    def test_open_passes_selected_editor(self):
        self.client.open("demo.ipynb", editor="jupyter")
        self.assertEqual(
            self.mock_post.call_args.kwargs["json"],
            {"path": "demo.ipynb", "cwd": "/workspace", "editor": "jupyter", "target": "vscode"},
        )

    def test_open_passes_browser_target_and_url(self):
        self.client.open("demo.ipynb", target="browser", browser_url="http://127.0.0.1:4183/preview.html")
        self.assertEqual(
            self.mock_post.call_args.kwargs["json"],
            {
                "path": "demo.ipynb",
                "cwd": "/workspace",
                "editor": "canvas",
                "target": "browser",
                "browser_url": "http://127.0.0.1:4183/preview.html",
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


class TestCoreEndpoints(unittest.TestCase):
    """CoreClient methods call correct HTTP endpoints."""

    def setUp(self):
        self.client = CoreClient("http://127.0.0.1:9998", "tok")
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

    def test_resolve_preferred_session_calls_post(self):
        self.client.resolve_preferred_session(actor="human")
        url = self.mock_post.call_args[0][0]
        self.assertIn("/api/sessions/resolve", url)
        self.assertEqual(self.mock_post.call_args.kwargs["json"], {"actor": "human"})

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

    def test_session_presence_upsert_calls_post(self):
        self.client.session_presence_upsert("sess-1", path="nb.ipynb", activity="observing", cell_index=2)
        url = self.mock_post.call_args[0][0]
        self.assertIn("/api/sessions/presence/upsert", url)
        self.assertEqual(
            self.mock_post.call_args.kwargs["json"],
            {"session_id": "sess-1", "path": "nb.ipynb", "activity": "observing", "cell_index": 2},
        )

    def test_session_presence_clear_calls_post(self):
        self.client.session_presence_clear("sess-1", path="nb.ipynb")
        url = self.mock_post.call_args[0][0]
        self.assertIn("/api/sessions/presence/clear", url)
        self.assertEqual(self.mock_post.call_args.kwargs["json"], {"session_id": "sess-1", "path": "nb.ipynb"})

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

    def test_notebook_select_kernel_calls_post(self):
        self.client.notebook_select_kernel("nb.ipynb", kernel_id="/opt/python3")
        url = self.mock_post.call_args[0][0]
        self.assertIn("/api/notebooks/select-kernel", url)
        self.assertEqual(
            self.mock_post.call_args.kwargs["json"],
            {"path": "nb.ipynb", "kernel_id": "/opt/python3"},
        )

    def test_notebook_select_kernel_without_kernel_id(self):
        self.client.notebook_select_kernel("nb.ipynb")
        payload = self.mock_post.call_args.kwargs["json"]
        self.assertEqual(payload, {"path": "nb.ipynb"})
        self.assertNotIn("kernel_id", payload)

    def test_notebook_edit_calls_post(self):
        self.client.notebook_edit("nb.ipynb", [{"op": "replace-source", "cell_id": "cell-1", "source": "x = 2"}])
        url = self.mock_post.call_args[0][0]
        self.assertIn("/api/notebooks/edit", url)

    def test_notebook_edit_calls_post_with_owner_session(self):
        self.client.notebook_edit(
            "nb.ipynb",
            [{"op": "replace-source", "cell_id": "cell-1", "source": "x = 2"}],
            owner_session_id="sess-1",
        )
        self.assertEqual(
            self.mock_post.call_args.kwargs["json"],
            {
                "path": "nb.ipynb",
                "operations": [{"op": "replace-source", "cell_id": "cell-1", "source": "x = 2"}],
                "owner_session_id": "sess-1",
            },
        )

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

    def test_notebook_execute_cell_calls_post_with_owner_session(self):
        self.client.notebook_execute_cell("nb.ipynb", cell_id="cell-1", owner_session_id="sess-1", wait=False)
        self.assertEqual(
            self.mock_post.call_args.kwargs["json"],
            {"path": "nb.ipynb", "cell_id": "cell-1", "owner_session_id": "sess-1", "wait": False},
        )

    def test_notebook_insert_execute_calls_post_with_owner_session(self):
        self.client.notebook_insert_execute("nb.ipynb", "x = 1", owner_session_id="sess-1", wait=False)
        self.assertEqual(
            self.mock_post.call_args.kwargs["json"],
            {
                "path": "nb.ipynb",
                "source": "x = 1",
                "cell_type": "code",
                "at_index": -1,
                "owner_session_id": "sess-1",
                "wait": False,
            },
        )

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

    def test_notebook_execute_all_includes_owner_session_id(self):
        self.client.notebook_execute_all("nb.ipynb", owner_session_id="sess-1")
        url = self.mock_post.call_args[0][0]
        self.assertIn("/api/notebooks/execute-all", url)
        self.assertEqual(
            self.mock_post.call_args.kwargs["json"],
            {"path": "nb.ipynb", "owner_session_id": "sess-1"},
        )

    def test_notebook_restart_and_run_all_includes_owner_session_id(self):
        self.client.notebook_restart_and_run_all("nb.ipynb", owner_session_id="sess-1")
        url = self.mock_post.call_args[0][0]
        self.assertIn("/api/notebooks/restart-and-run-all", url)
        self.assertEqual(
            self.mock_post.call_args.kwargs["json"],
            {"path": "nb.ipynb", "owner_session_id": "sess-1"},
        )

    def test_notebook_runtime_calls_post(self):
        self.client.notebook_runtime("nb.ipynb")
        url = self.mock_post.call_args[0][0]
        self.assertIn("/api/notebooks/runtime", url)
        self.assertEqual(self.mock_post.call_args.kwargs["json"], {"path": "nb.ipynb"})

    def test_notebook_activity_calls_post(self):
        self.client.notebook_activity("nb.ipynb", since=10.5)
        url = self.mock_post.call_args[0][0]
        self.assertIn("/api/notebooks/activity", url)
        self.assertEqual(self.mock_post.call_args.kwargs["json"], {"path": "nb.ipynb", "since": 10.5})

    def test_notebook_project_visible_calls_post(self):
        self.client.notebook_project_visible(
            "nb.ipynb",
            cells=[{"cell_type": "code", "source": "x = 1\nx"}],
        )
        url = self.mock_post.call_args[0][0]
        self.assertIn("/api/notebooks/project-visible", url)
        self.assertEqual(
            self.mock_post.call_args.kwargs["json"],
            {"path": "nb.ipynb", "cells": [{"cell_type": "code", "source": "x = 1\nx"}]},
        )

    def test_notebook_execute_visible_cell_calls_post(self):
        self.client.notebook_execute_visible_cell("nb.ipynb", cell_index=2, source="x = 9\nx")
        url = self.mock_post.call_args[0][0]
        self.assertIn("/api/notebooks/execute-visible-cell", url)
        self.assertEqual(
            self.mock_post.call_args.kwargs["json"],
            {"path": "nb.ipynb", "cell_index": 2, "source": "x = 9\nx"},
        )

    def test_acquire_cell_lease_calls_post(self):
        self.client.acquire_cell_lease("nb.ipynb", session_id="sess-1", cell_id="cell-1", kind="structure", ttl_seconds=12)
        url = self.mock_post.call_args[0][0]
        self.assertIn("/api/notebooks/lease/acquire", url)
        self.assertEqual(
            self.mock_post.call_args.kwargs["json"],
            {
                "path": "nb.ipynb",
                "session_id": "sess-1",
                "cell_id": "cell-1",
                "kind": "structure",
                "ttl_seconds": 12,
            },
        )

    def test_release_cell_lease_calls_post(self):
        self.client.release_cell_lease("nb.ipynb", session_id="sess-1", cell_index=2)
        url = self.mock_post.call_args[0][0]
        self.assertIn("/api/notebooks/lease/release", url)
        self.assertEqual(
            self.mock_post.call_args.kwargs["json"],
            {"path": "nb.ipynb", "session_id": "sess-1", "cell_index": 2},
        )

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

    def test_request_branch_review_calls_post(self):
        self.client.request_branch_review("branch-1", requested_by_session_id="sess-1", note="Please review")
        url = self.mock_post.call_args[0][0]
        self.assertIn("/api/branches/review-request", url)
        self.assertEqual(
            self.mock_post.call_args.kwargs["json"],
            {"branch_id": "branch-1", "requested_by_session_id": "sess-1", "note": "Please review"},
        )

    def test_resolve_branch_review_calls_post(self):
        self.client.resolve_branch_review("branch-1", resolved_by_session_id="sess-2", resolution="approved", note="Ship it")
        url = self.mock_post.call_args[0][0]
        self.assertIn("/api/branches/review-resolve", url)
        self.assertEqual(
            self.mock_post.call_args.kwargs["json"],
            {"branch_id": "branch-1", "resolved_by_session_id": "sess-2", "resolution": "approved", "note": "Ship it"},
        )

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

    def test_recover_runtime_calls_post(self):
        self.client.recover_runtime("rt-1")
        url = self.mock_post.call_args[0][0]
        self.assertIn("/api/runtimes/recover", url)
        self.assertEqual(self.mock_post.call_args.kwargs["json"], {"runtime_id": "rt-1"})

    def test_promote_runtime_calls_post(self):
        self.client.promote_runtime("rt-1", mode="shared")
        url = self.mock_post.call_args[0][0]
        self.assertIn("/api/runtimes/promote", url)
        self.assertEqual(self.mock_post.call_args.kwargs["json"], {"runtime_id": "rt-1", "mode": "shared"})

    def test_discard_runtime_calls_post(self):
        self.client.discard_runtime("rt-1")
        url = self.mock_post.call_args[0][0]
        self.assertIn("/api/runtimes/discard", url)
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
        self.assertIsNone(args.session_id)

    def test_ix(self):
        args = build_parser().parse_args(["ix", "nb.ipynb", "-s", "print(1)"])
        self.assertEqual(args.source, "print(1)")
        self.assertIsNone(args.session_id)

    def test_ix_with_session_id(self):
        args = build_parser().parse_args(["ix", "nb.ipynb", "--session-id", "sess-1", "-s", "print(1)"])
        self.assertEqual(args.session_id, "sess-1")

    def test_ix_with_cells_json(self):
        args = build_parser().parse_args([
            "ix", "nb.ipynb", "--cells-json", '[{"type":"markdown","source":"# hi"},{"type":"code","source":"x=1"}]',
        ])
        self.assertEqual(args.cells_json, '[{"type":"markdown","source":"# hi"},{"type":"code","source":"x=1"}]')

    def test_edit_replace_source(self):
        args = build_parser().parse_args(["edit", "nb.ipynb", "replace-source", "-s", "x=1", "--cell-id", "c1"])
        self.assertEqual(args.edit_command, "replace-source")
        self.assertIsNone(args.session_id)

    def test_edit_replace_source_with_session_id(self):
        args = build_parser().parse_args(["edit", "nb.ipynb", "--session-id", "sess-1", "replace-source", "-s", "x=1", "--cell-id", "c1"])
        self.assertEqual(args.session_id, "sess-1")

    def test_edit_insert(self):
        args = build_parser().parse_args(["edit", "nb.ipynb", "insert", "-s", "# hi", "--cell-type", "markdown"])
        self.assertEqual(args.edit_command, "insert")
        self.assertEqual(getattr(args, "cell_type", None), "markdown")

    def test_edit_insert_with_cells_json(self):
        args = build_parser().parse_args([
            "edit", "nb.ipynb", "insert", "--cells-json", '[{"type":"markdown","source":"# hi"},{"type":"code","source":"x=1"}]',
        ])
        self.assertEqual(args.cells_json, '[{"type":"markdown","source":"# hi"},{"type":"code","source":"x=1"}]')

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
        self.assertFalse(args.open)
        self.assertEqual(args.target, "vscode")
        self.assertEqual(args.editor, "canvas")

    def test_new_with_kernel_and_cells_json(self):
        args = build_parser().parse_args([
            "new", "nb.ipynb", "--kernel", "/tmp/.venv/bin/python", "--cells-json", '[{"type":"code","source":"x=1"}]',
        ])
        self.assertEqual(args.kernel, "/tmp/.venv/bin/python")
        self.assertEqual(args.cells_json, '[{"type":"code","source":"x=1"}]')

    def test_new_with_open(self):
        args = build_parser().parse_args(["new", "nb.ipynb", "--open"])
        self.assertTrue(args.open)
        self.assertEqual(args.target, "vscode")
        self.assertEqual(args.editor, "canvas")

    def test_new_with_open_in_browser(self):
        args = build_parser().parse_args(["new", "nb.ipynb", "--open", "--target", "browser", "--browser-url", "http://127.0.0.1:4183/preview.html"])
        self.assertTrue(args.open)
        self.assertEqual(args.target, "browser")
        self.assertEqual(args.browser_url, "http://127.0.0.1:4183/preview.html")

    def test_open(self):
        args = build_parser().parse_args(["open", "nb.ipynb"])
        self.assertEqual(args.command, "open")
        self.assertEqual(args.target, "vscode")
        self.assertEqual(args.editor, "canvas")

    def test_open_with_jupyter_editor(self):
        args = build_parser().parse_args(["open", "nb.ipynb", "--editor", "jupyter"])
        self.assertEqual(args.editor, "jupyter")

    def test_open_in_browser(self):
        args = build_parser().parse_args(["open", "nb.ipynb", "--target", "browser"])
        self.assertEqual(args.target, "browser")

    def test_mcp_setup(self):
        args = build_parser().parse_args(["mcp", "setup"])
        self.assertEqual(args.command, "mcp")
        self.assertEqual(args.mcp_command, "setup")

    def test_mcp_config(self):
        args = build_parser().parse_args(["mcp", "config", "--server-name", "analysis-repl"])
        self.assertEqual(args.command, "mcp")
        self.assertEqual(args.mcp_command, "config")
        self.assertEqual(args.server_name, "analysis-repl")

    def test_mcp_smoke_test(self):
        args = build_parser().parse_args(["mcp", "smoke-test"])
        self.assertEqual(args.command, "mcp")
        self.assertEqual(args.mcp_command, "smoke-test")

    def test_setup(self):
        args = build_parser().parse_args(["setup", "--with-mcp", "--configure-editor-default", "--smoke-test"])
        self.assertEqual(args.command, "setup")
        self.assertTrue(args.with_mcp)
        self.assertTrue(args.configure_editor_default)
        self.assertTrue(args.smoke_test)

    def test_doctor(self):
        args = build_parser().parse_args(["doctor", "--probe-mcp", "--smoke-test"])
        self.assertEqual(args.command, "doctor")
        self.assertTrue(args.probe_mcp)
        self.assertTrue(args.smoke_test)

    def test_editor_configure(self):
        args = build_parser().parse_args(["editor", "configure", "--default-canvas"])
        self.assertEqual(args.command, "editor")
        self.assertEqual(args.editor_command, "configure")
        self.assertTrue(args.default_canvas)

    def test_editor_dev(self):
        args = build_parser().parse_args(["editor", "dev", "--editor", "cursor", "--reuse-window", "--skip-compile"])
        self.assertEqual(args.command, "editor")
        self.assertEqual(args.editor_command, "dev")
        self.assertEqual(args.editor_name, "cursor")
        self.assertTrue(args.reuse_window)
        self.assertTrue(args.skip_compile)

    def test_reload(self):
        args = build_parser().parse_args(["reload"])
        self.assertEqual(args.command, "reload")

    def test_select_kernel_interactive(self):
        args = build_parser().parse_args(["select-kernel", "nb.ipynb", "--interactive"])
        self.assertTrue(args.interactive)

    def test_core_start(self):
        args = build_parser().parse_args(["core", "start"])
        self.assertEqual(args.command, "core")
        self.assertEqual(args.core_command, "start")

    def test_core_attach(self):
        args = build_parser().parse_args(["core", "attach", "--actor", "agent", "--client-type", "cli"])
        self.assertEqual(args.core_command, "attach")
        self.assertEqual(args.actor, "agent")
        self.assertEqual(args.client_type, "cli")

    def test_core_status(self):
        args = build_parser().parse_args(["core", "status", "--workspace-root", "/workspace"])
        self.assertEqual(args.core_command, "status")
        self.assertEqual(args.workspace_root, "/workspace")

    def test_core_stop(self):
        args = build_parser().parse_args(["core", "stop"])
        self.assertEqual(args.core_command, "stop")

    def test_core_session_start(self):
        args = build_parser().parse_args(["core", "session-start", "--actor", "agent", "--client-type", "cli"])
        self.assertEqual(args.core_command, "session-start")
        self.assertEqual(args.actor, "agent")
        self.assertEqual(args.client_type, "cli")

    def test_core_session_resolve(self):
        args = build_parser().parse_args(["core", "session-resolve", "--actor", "human"])
        self.assertEqual(args.core_command, "session-resolve")
        self.assertEqual(args.actor, "human")

    def test_core_session_touch(self):
        args = build_parser().parse_args(["core", "session-touch", "--session-id", "sess-1"])
        self.assertEqual(args.core_command, "session-touch")
        self.assertEqual(args.session_id, "sess-1")

    def test_core_session_detach(self):
        args = build_parser().parse_args(["core", "session-detach", "--session-id", "sess-1"])
        self.assertEqual(args.core_command, "session-detach")
        self.assertEqual(args.session_id, "sess-1")

    def test_core_document_open(self):
        args = build_parser().parse_args(["core", "document-open", "notebooks/demo.ipynb"])
        self.assertEqual(args.core_command, "document-open")
        self.assertEqual(args.path, "notebooks/demo.ipynb")

    def test_core_document_refresh(self):
        args = build_parser().parse_args(["core", "document-refresh", "--document-id", "doc-1"])
        self.assertEqual(args.core_command, "document-refresh")
        self.assertEqual(args.document_id, "doc-1")

    def test_core_document_rebind(self):
        args = build_parser().parse_args(["core", "document-rebind", "--document-id", "doc-1"])
        self.assertEqual(args.core_command, "document-rebind")
        self.assertEqual(args.document_id, "doc-1")

    def test_core_notebook_runtime(self):
        args = build_parser().parse_args(["core", "notebook-runtime", "notebooks/demo.ipynb"])
        self.assertEqual(args.core_command, "notebook-runtime")
        self.assertEqual(args.path, "notebooks/demo.ipynb")

    def test_core_notebook_projection(self):
        args = build_parser().parse_args(["core", "notebook-projection", "notebooks/demo.ipynb"])
        self.assertEqual(args.core_command, "notebook-projection")
        self.assertEqual(args.path, "notebooks/demo.ipynb")

    def test_core_project_visible_notebook(self):
        args = build_parser().parse_args(
            ["core", "project-visible-notebook", "notebooks/demo.ipynb", "--cells-file", "/tmp/cells.json"]
        )
        self.assertEqual(args.core_command, "project-visible-notebook")
        self.assertEqual(args.path, "notebooks/demo.ipynb")
        self.assertEqual(args.cells_file, "/tmp/cells.json")

    def test_core_execute_visible_cell(self):
        args = build_parser().parse_args(["core", "execute-visible-cell", "notebooks/demo.ipynb", "--cell-index", "2", "-s", "x = 1"])
        self.assertEqual(args.core_command, "execute-visible-cell")
        self.assertEqual(args.path, "notebooks/demo.ipynb")
        self.assertEqual(args.cell_index, 2)
        self.assertEqual(args.source, "x = 1")

    def test_core_branch_start(self):
        args = build_parser().parse_args(["core", "branch-start", "--document-id", "doc-1"])
        self.assertEqual(args.core_command, "branch-start")
        self.assertEqual(args.document_id, "doc-1")

    def test_core_branch_finish(self):
        args = build_parser().parse_args(["core", "branch-finish", "--branch-id", "branch-1", "--status-value", "merged"])
        self.assertEqual(args.core_command, "branch-finish")
        self.assertEqual(args.branch_id, "branch-1")

    def test_core_branch_review_request(self):
        args = build_parser().parse_args([
            "core", "branch-review-request",
            "--branch-id", "branch-1",
            "--requested-by-session-id", "sess-1",
        ])
        self.assertEqual(args.core_command, "branch-review-request")
        self.assertEqual(args.branch_id, "branch-1")

    def test_core_branch_review_resolve(self):
        args = build_parser().parse_args([
            "core", "branch-review-resolve",
            "--branch-id", "branch-1",
            "--resolved-by-session-id", "sess-2",
            "--resolution", "approved",
        ])
        self.assertEqual(args.core_command, "branch-review-resolve")
        self.assertEqual(args.resolution, "approved")

    def test_core_runtime_start(self):
        args = build_parser().parse_args(["core", "runtime-start", "--mode", "shared"])
        self.assertEqual(args.core_command, "runtime-start")
        self.assertEqual(args.mode, "shared")

    def test_core_runtime_promote(self):
        args = build_parser().parse_args(["core", "runtime-promote", "--runtime-id", "rt-1", "--mode", "pinned"])
        self.assertEqual(args.core_command, "runtime-promote")
        self.assertEqual(args.runtime_id, "rt-1")
        self.assertEqual(args.mode, "pinned")

    def test_core_runtime_discard(self):
        args = build_parser().parse_args(["core", "runtime-discard", "--runtime-id", "rt-1"])
        self.assertEqual(args.core_command, "runtime-discard")
        self.assertEqual(args.runtime_id, "rt-1")

    def test_core_run_start(self):
        args = build_parser().parse_args(["core", "run-start", "--runtime-id", "rt-1", "--target-type", "document", "--target-ref", "doc-1"])
        self.assertEqual(args.core_command, "run-start")
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
                mock.patch("agent_repl.cli._notebook_client", return_value=self._mock_notebook_runtime_client(mock_client)),
            ):
                code = main(argv)
        finally:
            sys.stdout = old
        return code, buf.getvalue()

    def _mock_notebook_runtime_client(self, bridge_client: BridgeClient):
        runtime = mock.Mock()
        runtime.notebook_contents = bridge_client.contents
        runtime.notebook_status = bridge_client.status
        runtime.notebook_insert_execute = bridge_client.insert_and_execute
        runtime.notebook_execute_cell = bridge_client.execute_cell
        runtime.notebook_execute_all = bridge_client.execute_all
        runtime.notebook_restart = bridge_client.restart_kernel
        runtime.notebook_restart_and_run_all = bridge_client.restart_and_run_all
        runtime.notebook_create = bridge_client.create
        runtime.notebook_edit = bridge_client.edit
        runtime.notebook_select_kernel = bridge_client.select_kernel
        return runtime

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
        client.open.return_value = {"status": "ok", "editor": "canvas", "target": "vscode"}
        client.select_kernel.return_value = {"status": "ok"}
        client.edit.return_value = {"results": []}
        client.prompt_status.return_value = {"status": "ok"}
        client.reload.return_value = {"status": "ok"}
        for k, v in overrides.items():
            setattr(client, k, mock.Mock(return_value=v))
        return client

    def _mock_core_client(self, **overrides):
        client = mock.MagicMock(spec=CoreClient)
        client.status.return_value = {"status": "ok", "mode": "core"}
        client.shutdown.return_value = {"status": "ok", "stopping": True}
        client.list_sessions.return_value = {"status": "ok", "sessions": []}
        client.start_session.return_value = {"status": "ok", "session": {"session_id": "sess-1"}}
        client.resolve_preferred_session.return_value = {"status": "ok", "session": None}
        client.touch_session.return_value = {"status": "ok", "session": {"session_id": "sess-1", "status": "attached"}}
        client.detach_session.return_value = {"status": "ok", "session": {"session_id": "sess-1", "status": "detached"}}
        client.session_presence_upsert.return_value = {"status": "ok", "presence": {"session_id": "sess-1", "activity": "observing"}}
        client.session_presence_clear.return_value = {"status": "ok", "cleared": True}
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
        client.notebook_runtime.return_value = {"status": "ok", "active": True}
        client.notebook_projection.return_value = {"status": "ok", "active": True, "contents": {"path": "nb.ipynb", "cells": []}}
        client.notebook_activity.return_value = {"status": "ok", "path": "nb.ipynb", "presence": [], "recent_events": []}
        client.notebook_project_visible.return_value = {"status": "ok", "path": "nb.ipynb", "cell_count": 1}
        client.notebook_execute_visible_cell.return_value = {"status": "ok"}
        client.acquire_cell_lease.return_value = {"status": "ok", "lease": {"lease_id": "lease-1"}}
        client.release_cell_lease.return_value = {"status": "ok", "released": True}
        client.list_branches.return_value = {"status": "ok", "branches": []}
        client.start_branch.return_value = {"status": "ok", "branch": {"branch_id": "branch-1", "status": "active"}}
        client.finish_branch.return_value = {"status": "ok", "branch": {"branch_id": "branch-1", "status": "merged"}}
        client.request_branch_review.return_value = {"status": "ok", "branch": {"branch_id": "branch-1", "review_status": "requested"}}
        client.resolve_branch_review.return_value = {"status": "ok", "branch": {"branch_id": "branch-1", "review_status": "resolved"}}
        client.list_runtimes.return_value = {"status": "ok", "runtimes": []}
        client.start_runtime.return_value = {"status": "ok", "runtime": {"runtime_id": "rt-1"}}
        client.stop_runtime.return_value = {"status": "ok", "runtime": {"runtime_id": "rt-1", "status": "stopped"}}
        client.recover_runtime.return_value = {"status": "ok", "runtime": {"runtime_id": "rt-1", "status": "idle"}}
        client.promote_runtime.return_value = {"status": "ok", "runtime": {"runtime_id": "rt-1", "mode": "shared"}}
        client.discard_runtime.return_value = {"status": "ok", "runtime": {"runtime_id": "rt-1", "status": "reaped"}}
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

    def test_new_does_not_fall_back_to_bridge_when_core_runtime_bootstrap_fails(self):
        stderr = StringIO()
        old_err = sys.stderr
        sys.stderr = stderr
        bridge = self._mock_client()
        try:
            with (
                mock.patch("agent_repl.cli.CoreClient.start", side_effect=RuntimeError("core bootstrap failed")),
                mock.patch("agent_repl.cli._client", return_value=bridge),
            ):
                code = main(["new", "nb.ipynb"])
        finally:
            sys.stderr = old_err
        self.assertEqual(code, 1)
        self.assertIn("core bootstrap failed", stderr.getvalue())
        bridge.create.assert_not_called()

    def test_cat_prefers_core_notebook_projection(self):
        bridge = self._mock_client()
        core = self._mock_core_client(notebook_contents={
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
        core = self._mock_core_client()
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
        client.insert_and_execute.assert_called_once_with(
            "nb.ipynb",
            "x=1",
            at_index=-1,
            cell_type="code",
            wait=True,
            timeout=30,
        )

    def test_ix_prefers_core_execution_surface(self):
        bridge = self._mock_client()
        core = self._mock_core_client()
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
        core.notebook_insert_execute.assert_called_once_with(
            "nb.ipynb",
            "x=1",
            at_index=-1,
            cell_type="code",
            wait=True,
            timeout=30,
            owner_session_id="sess-1",
        )
        core.start_session.assert_called_once_with(actor="human", client="cli", label="CLI")
        bridge.insert_and_execute.assert_not_called()

    def test_ix_passes_session_id_to_core_execution_surface(self):
        bridge = self._mock_client()
        core = self._mock_core_client()
        buf = StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            with (
                mock.patch("agent_repl.cli._client", return_value=bridge),
                mock.patch("agent_repl.cli._notebook_client", return_value=core),
            ):
                code = main(["ix", "nb.ipynb", "--session-id", "sess-1", "-s", "x=1"])
        finally:
            sys.stdout = old
        self.assertEqual(code, 0)
        core.notebook_insert_execute.assert_called_once_with(
            "nb.ipynb",
            "x=1",
            at_index=-1,
            cell_type="code",
            wait=True,
            timeout=30,
            owner_session_id="sess-1",
        )
        bridge.insert_and_execute.assert_not_called()

    def test_ix_no_wait(self):
        client = self._mock_client()
        code, _ = self._run(["ix", "nb.ipynb", "-s", "x=1", "--no-wait"], client)
        self.assertEqual(code, 0)
        client.insert_and_execute.assert_called_once_with(
            "nb.ipynb",
            "x=1",
            at_index=-1,
            cell_type="code",
            wait=False,
            timeout=30,
        )

    def test_ix_batch_uses_bridge_surface_sequentially(self):
        client = self._mock_client()
        client.edit.return_value = {"path": "nb.ipynb", "results": [{"op": "insert", "cell_id": "md-1"}]}
        client.insert_and_execute.side_effect = [
            {"status": "ok", "cell_id": "code-1", "cell_index": 1},
            {"status": "ok", "cell_id": "code-2", "cell_index": 2},
        ]
        code, out = self._run([
            "ix",
            "nb.ipynb",
            "--cells-json",
            '[{"type":"markdown","source":"# hi"},{"type":"code","source":"x=1"},{"type":"code","source":"x+1"}]',
        ], client)
        self.assertEqual(code, 0)
        client.edit.assert_called_once_with(
            "nb.ipynb",
            [{"op": "insert", "source": "# hi", "cell_type": "markdown", "at_index": -1}],
        )
        self.assertEqual(
            client.insert_and_execute.call_args_list,
            [
                mock.call("nb.ipynb", "x=1", at_index=-1, cell_type="code", wait=True, timeout=30),
                mock.call("nb.ipynb", "x+1", at_index=-1, cell_type="code", wait=True, timeout=30),
            ],
        )
        payload = json.loads(out)
        self.assertEqual(payload["operation"], "batch-insert-execute")
        self.assertEqual(len(payload["results"]), 3)

    def test_ix_batch_prefers_core_surfaces_sequentially(self):
        bridge = self._mock_client()
        core = self._mock_core_client()
        core.notebook_edit.return_value = {"path": "nb.ipynb", "results": [{"op": "insert", "cell_id": "md-1"}]}
        core.notebook_insert_execute.side_effect = [
            {"status": "ok", "cell_id": "code-1", "cell_index": 1},
            {"status": "ok", "cell_id": "code-2", "cell_index": 2},
        ]
        buf = StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            with (
                mock.patch("agent_repl.cli._client", return_value=bridge),
                mock.patch("agent_repl.cli._notebook_client", return_value=core),
            ):
                code = main([
                    "ix",
                    "nb.ipynb",
                    "--session-id",
                    "sess-1",
                    "--cells-json",
                    '[{"type":"markdown","source":"# hi"},{"type":"code","source":"x=1"},{"type":"code","source":"x+1"}]',
                ])
        finally:
            sys.stdout = old
        self.assertEqual(code, 0)
        core.notebook_edit.assert_called_once_with(
            "nb.ipynb",
            [{"op": "insert", "source": "# hi", "cell_type": "markdown", "at_index": -1}],
            owner_session_id="sess-1",
        )
        self.assertEqual(
            core.notebook_insert_execute.call_args_list,
            [
                mock.call("nb.ipynb", "x=1", at_index=-1, cell_type="code", wait=True, timeout=30, owner_session_id="sess-1"),
                mock.call("nb.ipynb", "x+1", at_index=-1, cell_type="code", wait=True, timeout=30, owner_session_id="sess-1"),
            ],
        )
        bridge.insert_and_execute.assert_not_called()
        payload = json.loads(buf.getvalue())
        self.assertEqual(payload["operation"], "batch-insert-execute")
        self.assertEqual(len(payload["results"]), 3)

    def test_ix_batch_no_wait_rejected(self):
        client = self._mock_client()
        with self.assertRaises(SystemExit) as exited:
            self._run([
                "ix",
                "nb.ipynb",
                "--cells-json",
                '[{"type":"code","source":"x=1"},{"type":"code","source":"x+1"}]',
                "--no-wait",
            ], client)
        self.assertIn("batch ix does not support --no-wait", str(exited.exception))

    def test_exec_with_code(self):
        client = self._mock_client()
        code, _ = self._run(["exec", "nb.ipynb", "-c", "x=1"], client)
        self.assertEqual(code, 0)
        client.insert_and_execute.assert_called_once()

    def test_exec_with_cell_id(self):
        client = self._mock_client()
        code, _ = self._run(["exec", "nb.ipynb", "--cell-id", "abc"], client)
        self.assertEqual(code, 0)
        client.execute_cell.assert_called_once_with(
            "nb.ipynb",
            cell_id="abc",
            wait=True,
            timeout=30,
        )

    def test_exec_prefers_core_execution_surface(self):
        bridge = self._mock_client()
        core = self._mock_core_client()
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
        core.notebook_execute_cell.assert_called_once_with(
            "nb.ipynb",
            cell_id="abc",
            wait=True,
            timeout=30,
            owner_session_id="sess-1",
        )
        core.resolve_preferred_session.assert_called_once_with(actor="human")
        core.start_session.assert_called_once_with(actor="human", client="cli", label="CLI")
        bridge.execute_cell.assert_not_called()

    def test_exec_passes_session_id_to_core_execution_surface(self):
        bridge = self._mock_client()
        core = self._mock_core_client()
        buf = StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            with (
                mock.patch("agent_repl.cli._client", return_value=bridge),
                mock.patch("agent_repl.cli._notebook_client", return_value=core),
            ):
                code = main(["exec", "nb.ipynb", "--session-id", "sess-1", "--cell-id", "abc"])
        finally:
            sys.stdout = old
        self.assertEqual(code, 0)
        core.notebook_execute_cell.assert_called_once_with(
            "nb.ipynb",
            cell_id="abc",
            wait=True,
            timeout=30,
            owner_session_id="sess-1",
        )
        bridge.execute_cell.assert_not_called()

    def test_exec_reuses_preferred_human_session_when_session_id_is_omitted(self):
        bridge = self._mock_client()
        core = self._mock_core_client(resolve_preferred_session={
            "status": "ok",
            "session": {
                "session_id": "sess-vscode",
                "actor": "human",
                "client": "vscode",
                "status": "attached",
                "capabilities": ["projection", "editor", "presence"],
                "last_seen_at": 9,
                "created_at": 2,
            },
        })
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
        core.notebook_execute_cell.assert_called_once_with(
            "nb.ipynb",
            cell_id="abc",
            wait=True,
            timeout=30,
            owner_session_id="sess-vscode",
        )
        core.start_session.assert_not_called()
        bridge.execute_cell.assert_not_called()

    def test_exec_starts_human_cli_session_when_no_reusable_session_exists(self):
        bridge = self._mock_client()
        core = self._mock_core_client(
            resolve_preferred_session={"status": "ok", "session": None},
            start_session={"status": "ok", "session": {"session_id": "sess-cli"}},
        )
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
        core.start_session.assert_called_once_with(actor="human", client="cli", label="CLI")
        core.resolve_preferred_session.assert_called_once_with(actor="human")
        core.notebook_execute_cell.assert_called_once_with(
            "nb.ipynb",
            cell_id="abc",
            wait=True,
            timeout=30,
            owner_session_id="sess-cli",
        )
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
        client.open.assert_not_called()

    def test_new_prefers_core_notebook_projection(self):
        bridge = self._mock_client()
        core = self._mock_core_client()
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
        bridge.open.assert_not_called()

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

    def test_new_with_open_uses_canvas_editor_by_default(self):
        bridge = self._mock_client()
        core = self._mock_core_client(notebook_create={"status": "ok", "path": "nb.ipynb"})
        buf = StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            with (
                mock.patch("agent_repl.cli._client", return_value=bridge),
                mock.patch("agent_repl.cli._notebook_client", return_value=core),
            ):
                code = main(["new", "nb.ipynb", "--open"])
        finally:
            sys.stdout = old
        self.assertEqual(code, 0)
        core.notebook_create.assert_called_once_with("nb.ipynb", cells=None, kernel_id=None)
        bridge.open.assert_called_once_with("nb.ipynb", editor="canvas", target="vscode", browser_url=None)
        payload = json.loads(buf.getvalue())
        self.assertEqual(payload["open"]["editor"], "canvas")
        self.assertEqual(payload["open"]["target"], "vscode")

    def test_new_with_open_can_target_browser(self):
        bridge = self._mock_client(open={"status": "ok", "path": "nb.ipynb", "editor": "canvas", "target": "browser", "url": "http://127.0.0.1:4183/preview.html?path=nb.ipynb"})
        core = self._mock_core_client(notebook_create={"status": "ok", "path": "nb.ipynb"})
        buf = StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            with (
                mock.patch("agent_repl.cli._client", return_value=bridge),
                mock.patch("agent_repl.cli._notebook_client", return_value=core),
            ):
                code = main(["new", "nb.ipynb", "--open", "--target", "browser", "--browser-url", "http://127.0.0.1:4183/preview.html"])
        finally:
            sys.stdout = old
        self.assertEqual(code, 0)
        bridge.open.assert_called_once_with(
            "nb.ipynb",
            editor="canvas",
            target="browser",
            browser_url="http://127.0.0.1:4183/preview.html",
        )

    def test_open_uses_canvas_editor_by_default(self):
        client = self._mock_client(open={"status": "ok", "path": "nb.ipynb", "editor": "canvas"})
        code, _ = self._run(["open", "nb.ipynb"], client)
        self.assertEqual(code, 0)
        client.open.assert_called_once_with("nb.ipynb", editor="canvas", target="vscode", browser_url=None)

    def test_open_can_target_jupyter(self):
        client = self._mock_client(open={"status": "ok", "path": "nb.ipynb", "editor": "jupyter"})
        code, _ = self._run(["open", "nb.ipynb", "--editor", "jupyter"], client)
        self.assertEqual(code, 0)
        client.open.assert_called_once_with("nb.ipynb", editor="jupyter", target="vscode", browser_url=None)

    def test_open_can_target_browser(self):
        client = self._mock_client(open={"status": "ok", "path": "nb.ipynb", "editor": "canvas", "target": "browser"})
        code, _ = self._run(["open", "nb.ipynb", "--target", "browser", "--browser-url", "http://127.0.0.1:4183/preview.html"], client)
        self.assertEqual(code, 0)
        client.open.assert_called_once_with(
            "nb.ipynb",
            editor="canvas",
            target="browser",
            browser_url="http://127.0.0.1:4183/preview.html",
        )

    def test_edit_prefers_core_execution_surface(self):
        bridge = self._mock_client()
        core = self._mock_core_client()
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
            owner_session_id="sess-1",
        )
        core.resolve_preferred_session.assert_called_once_with(actor="human")
        core.start_session.assert_called_once_with(actor="human", client="cli", label="CLI")
        bridge.edit.assert_not_called()

    def test_edit_insert_batch_prefers_core_execution_surface(self):
        bridge = self._mock_client()
        core = self._mock_core_client()
        buf = StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            with (
                mock.patch("agent_repl.cli._client", return_value=bridge),
                mock.patch("agent_repl.cli._notebook_client", return_value=core),
            ):
                code = main([
                    "edit",
                    "nb.ipynb",
                    "insert",
                    "--cells-json",
                    '[{"type":"markdown","source":"# hi"},{"type":"code","source":"x=1"}]',
                    "--at-index",
                    "2",
                ])
        finally:
            sys.stdout = old
        self.assertEqual(code, 0)
        core.notebook_edit.assert_called_once_with(
            "nb.ipynb",
            [
                {"op": "insert", "source": "# hi", "cell_type": "markdown", "at_index": 2},
                {"op": "insert", "source": "x=1", "cell_type": "code", "at_index": 3},
            ],
            owner_session_id="sess-1",
        )
        core.resolve_preferred_session.assert_called_once_with(actor="human")
        core.start_session.assert_called_once_with(actor="human", client="cli", label="CLI")
        bridge.edit.assert_not_called()

    def test_run_all_prefers_core_execution_surface(self):
        bridge = self._mock_client()
        core = self._mock_core_client()
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
        core.notebook_execute_all.assert_called_once_with("nb.ipynb", owner_session_id="sess-1")
        core.resolve_preferred_session.assert_called_once_with(actor="human")
        core.start_session.assert_called_once_with(actor="human", client="cli", label="CLI")
        bridge.execute_all.assert_not_called()

    def test_run_all_reuses_preferred_human_session(self):
        bridge = self._mock_client()
        core = self._mock_core_client(resolve_preferred_session={
            "status": "ok",
            "session": {
                "session_id": "sess-vscode",
                "actor": "human",
                "client": "vscode",
                "status": "attached",
                "capabilities": ["projection", "editor", "presence"],
                "last_seen_at": 42,
                "created_at": 24,
            },
        })
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
        core.notebook_execute_all.assert_called_once_with("nb.ipynb", owner_session_id="sess-vscode")
        core.start_session.assert_not_called()
        bridge.execute_all.assert_not_called()

    def test_restart_prefers_core_execution_surface(self):
        bridge = self._mock_client()
        core = self._mock_core_client()
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
        core = self._mock_core_client()
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
        core.notebook_restart_and_run_all.assert_called_once_with("nb.ipynb", owner_session_id="sess-1")
        core.resolve_preferred_session.assert_called_once_with(actor="human")
        core.start_session.assert_called_once_with(actor="human", client="cli", label="CLI")
        bridge.restart_and_run_all.assert_not_called()

    def test_restart_run_all_reuses_preferred_human_session(self):
        bridge = self._mock_client()
        core = self._mock_core_client(resolve_preferred_session={
            "status": "ok",
            "session": {
                "session_id": "sess-browser",
                "actor": "human",
                "client": "browser",
                "status": "stale",
                "capabilities": ["projection", "presence"],
                "last_seen_at": 42,
                "created_at": 24,
            },
        })
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
        core.notebook_restart_and_run_all.assert_called_once_with("nb.ipynb", owner_session_id="sess-browser")
        core.start_session.assert_not_called()
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

    def test_select_kernel_prefers_core_route_when_available(self):
        bridge = self._mock_client()
        core = self._mock_core_client()
        core.notebook_select_kernel.return_value = {"status": "ok", "mode": "headless"}
        buf = StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            with (
                mock.patch("agent_repl.cli._client", return_value=bridge),
                mock.patch("agent_repl.cli._notebook_client", return_value=core),
            ):
                code = main(["select-kernel", "nb.ipynb", "--kernel-id", "python3"])
        finally:
            sys.stdout = old
        self.assertEqual(code, 0)
        core.notebook_select_kernel.assert_called_once_with("nb.ipynb", kernel_id="python3")
        bridge.select_kernel.assert_not_called()

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

    def test_core_start(self):
        client = self._mock_client()
        with (
            mock.patch("agent_repl.cli._client", return_value=client),
            mock.patch("agent_repl.cli.CoreClient.start", return_value={"status": "ok", "mode": "core", "already_running": False}),
        ):
            code = main(["core", "start"])
        self.assertEqual(code, 0)

    def test_core_attach(self):
        client = self._mock_client()
        with (
            mock.patch("agent_repl.cli._client", return_value=client),
            mock.patch("agent_repl.cli.CoreClient.attach", return_value={"status": "ok", "attached": True, "session": {"session_id": "sess-1"}}) as mock_attach,
        ):
            code = main(["core", "attach", "--actor", "agent", "--client-type", "cli", "--label", "worker"])
        self.assertEqual(code, 0)
        mock_attach.assert_called_once_with(
            os.getcwd(),
            actor="agent",
            client="cli",
            label="worker",
            capabilities=None,
            session_id=None,
            timeout=DEFAULT_START_TIMEOUT,
            runtime_dir=None,
        )

    def test_core_status(self):
        client = self._mock_core_client()
        buf = StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            with (
                mock.patch("agent_repl.cli._client"),
                mock.patch("agent_repl.cli._core_client_raw", return_value=client),
            ):
                code = main(["core", "status"])
        finally:
            sys.stdout = old
        self.assertEqual(code, 0)
        client.status.assert_called_once()

    def test_core_stop(self):
        client = self._mock_core_client()
        buf = StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            with (
                mock.patch("agent_repl.cli._client"),
                mock.patch("agent_repl.cli._core_client_raw", return_value=client),
            ):
                code = main(["core", "stop"])
        finally:
            sys.stdout = old
        self.assertEqual(code, 0)
        client.shutdown.assert_called_once()

    def test_core_sessions(self):
        client = self._mock_core_client()
        buf = StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            with mock.patch("agent_repl.cli._core_client", return_value=client):
                code = main(["core", "sessions"])
        finally:
            sys.stdout = old
        self.assertEqual(code, 0)
        client.list_sessions.assert_called_once()

    def test_core_session_start(self):
        client = self._mock_core_client()
        buf = StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            with mock.patch("agent_repl.cli._core_client", return_value=client):
                code = main(["core", "session-start", "--actor", "agent", "--client-type", "cli", "--label", "worker"])
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

    def test_core_session_resolve(self):
        client = self._mock_core_client(resolve_preferred_session={
            "status": "ok",
            "session": {"session_id": "sess-vscode"},
        })
        buf = StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            with mock.patch("agent_repl.cli._core_client", return_value=client):
                code = main(["core", "session-resolve", "--actor", "human"])
        finally:
            sys.stdout = old
        self.assertEqual(code, 0)
        client.resolve_preferred_session.assert_called_once_with(actor="human")

    def test_core_session_touch(self):
        client = self._mock_core_client()
        buf = StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            with mock.patch("agent_repl.cli._core_client", return_value=client):
                code = main(["core", "session-touch", "--session-id", "sess-1"])
        finally:
            sys.stdout = old
        self.assertEqual(code, 0)
        client.touch_session.assert_called_once_with("sess-1")

    def test_core_session_detach(self):
        client = self._mock_core_client()
        buf = StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            with mock.patch("agent_repl.cli._core_client", return_value=client):
                code = main(["core", "session-detach", "--session-id", "sess-1"])
        finally:
            sys.stdout = old
        self.assertEqual(code, 0)
        client.detach_session.assert_called_once_with("sess-1")

    def test_core_session_end(self):
        client = self._mock_core_client()
        buf = StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            with mock.patch("agent_repl.cli._core_client", return_value=client):
                code = main(["core", "session-end", "--session-id", "sess-1"])
        finally:
            sys.stdout = old
        self.assertEqual(code, 0)
        client.end_session.assert_called_once_with("sess-1")

    def test_core_session_presence_upsert(self):
        client = self._mock_core_client()
        buf = StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            with mock.patch("agent_repl.cli._core_client", return_value=client):
                code = main([
                    "core", "session-presence-upsert", "notebooks/demo.ipynb",
                    "--session-id", "sess-1",
                    "--activity", "observing",
                    "--cell-index", "2",
                ])
        finally:
            sys.stdout = old
        self.assertEqual(code, 0)
        client.session_presence_upsert.assert_called_once_with(
            "sess-1",
            path="notebooks/demo.ipynb",
            activity="observing",
            cell_id=None,
            cell_index=2,
        )

    def test_core_session_presence_clear(self):
        client = self._mock_core_client()
        buf = StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            with mock.patch("agent_repl.cli._core_client", return_value=client):
                code = main([
                    "core", "session-presence-clear",
                    "--session-id", "sess-1",
                    "--path", "notebooks/demo.ipynb",
                ])
        finally:
            sys.stdout = old
        self.assertEqual(code, 0)
        client.session_presence_clear.assert_called_once_with("sess-1", path="notebooks/demo.ipynb")

    def test_core_documents(self):
        client = self._mock_core_client()
        buf = StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            with mock.patch("agent_repl.cli._core_client", return_value=client):
                code = main(["core", "documents"])
        finally:
            sys.stdout = old
        self.assertEqual(code, 0)
        client.list_documents.assert_called_once()

    def test_core_document_open(self):
        client = self._mock_core_client()
        buf = StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            with mock.patch("agent_repl.cli._core_client", return_value=client):
                code = main(["core", "document-open", "notebooks/demo.ipynb"])
        finally:
            sys.stdout = old
        self.assertEqual(code, 0)
        client.open_document.assert_called_once_with("notebooks/demo.ipynb")

    def test_core_document_refresh(self):
        client = self._mock_core_client()
        buf = StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            with mock.patch("agent_repl.cli._core_client", return_value=client):
                code = main(["core", "document-refresh", "--document-id", "doc-1"])
        finally:
            sys.stdout = old
        self.assertEqual(code, 0)
        client.refresh_document.assert_called_once_with("doc-1")

    def test_core_document_rebind(self):
        client = self._mock_core_client()
        buf = StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            with mock.patch("agent_repl.cli._core_client", return_value=client):
                code = main(["core", "document-rebind", "--document-id", "doc-1"])
        finally:
            sys.stdout = old
        self.assertEqual(code, 0)
        client.rebind_document.assert_called_once_with("doc-1")

    def test_core_notebook_runtime(self):
        client = self._mock_core_client()
        buf = StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            with mock.patch("agent_repl.cli._core_client", return_value=client):
                code = main(["core", "notebook-runtime", "notebooks/demo.ipynb"])
        finally:
            sys.stdout = old
        self.assertEqual(code, 0)
        client.notebook_runtime.assert_called_once_with("notebooks/demo.ipynb")

    def test_core_notebook_projection(self):
        client = self._mock_core_client()
        buf = StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            with mock.patch("agent_repl.cli._core_client", return_value=client):
                code = main(["core", "notebook-projection", "notebooks/demo.ipynb"])
        finally:
            sys.stdout = old
        self.assertEqual(code, 0)
        client.notebook_projection.assert_called_once_with("notebooks/demo.ipynb")

    def test_core_notebook_activity(self):
        client = self._mock_core_client()
        buf = StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            with mock.patch("agent_repl.cli._core_client", return_value=client):
                code = main(["core", "notebook-activity", "notebooks/demo.ipynb", "--since", "12.5"])
        finally:
            sys.stdout = old
        self.assertEqual(code, 0)
        client.notebook_activity.assert_called_once_with("notebooks/demo.ipynb", since=12.5)

    def test_core_project_visible_notebook(self):
        client = self._mock_core_client()
        buf = StringIO()
        old = sys.stdout
        with tempfile.TemporaryDirectory() as tmp:
            cells_file = Path(tmp) / "cells.json"
            cells_file.write_text(json.dumps([{"cell_type": "code", "source": "x = 1"}]))
            sys.stdout = buf
            try:
                with mock.patch("agent_repl.cli._core_client", return_value=client):
                    code = main(["core", "project-visible-notebook", "notebooks/demo.ipynb", "--cells-file", str(cells_file)])
            finally:
                sys.stdout = old
        self.assertEqual(code, 0)
        client.notebook_project_visible.assert_called_once_with(
            "notebooks/demo.ipynb",
            cells=[{"cell_type": "code", "source": "x = 1"}],
            owner_session_id="sess-1",
        )
        client.start_session.assert_called_once_with(actor="human", client="cli", label="CLI")

    def test_core_project_visible_notebook_with_session_id(self):
        client = self._mock_core_client()
        buf = StringIO()
        old = sys.stdout
        with tempfile.TemporaryDirectory() as tmp:
            cells_file = Path(tmp) / "cells.json"
            cells_file.write_text(json.dumps([{"cell_type": "code", "source": "x = 1"}]))
            sys.stdout = buf
            try:
                with mock.patch("agent_repl.cli._core_client", return_value=client):
                    code = main([
                        "core", "project-visible-notebook", "notebooks/demo.ipynb",
                        "--cells-file", str(cells_file),
                        "--session-id", "sess-1",
                    ])
            finally:
                sys.stdout = old
        self.assertEqual(code, 0)
        client.notebook_project_visible.assert_called_once_with(
            "notebooks/demo.ipynb",
            cells=[{"cell_type": "code", "source": "x = 1"}],
            owner_session_id="sess-1",
        )

    def test_core_execute_visible_cell(self):
        client = self._mock_core_client()
        buf = StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            with mock.patch("agent_repl.cli._core_client", return_value=client):
                code = main(["core", "execute-visible-cell", "notebooks/demo.ipynb", "--cell-index", "2", "-s", "x = 1"])
        finally:
            sys.stdout = old
        self.assertEqual(code, 0)
        client.notebook_execute_visible_cell.assert_called_once_with(
            "notebooks/demo.ipynb",
            cell_index=2,
            source="x = 1",
            owner_session_id="sess-1",
        )
        client.start_session.assert_called_once_with(actor="human", client="cli", label="CLI")

    def test_core_execute_visible_cell_with_session_id(self):
        client = self._mock_core_client()
        buf = StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            with mock.patch("agent_repl.cli._core_client", return_value=client):
                code = main([
                    "core", "execute-visible-cell", "notebooks/demo.ipynb",
                    "--session-id", "sess-1",
                    "--cell-index", "2",
                    "-s", "x = 1",
                ])
        finally:
            sys.stdout = old
        self.assertEqual(code, 0)
        client.notebook_execute_visible_cell.assert_called_once_with(
            "notebooks/demo.ipynb",
            cell_index=2,
            source="x = 1",
            owner_session_id="sess-1",
        )

    def test_core_cell_lease_acquire(self):
        client = self._mock_core_client()
        buf = StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            with mock.patch("agent_repl.cli._core_client", return_value=client):
                code = main([
                    "core", "cell-lease-acquire", "notebooks/demo.ipynb",
                    "--session-id", "sess-1",
                    "--cell-id", "cell-1",
                    "--kind", "structure",
                    "--ttl-seconds", "12",
                ])
        finally:
            sys.stdout = old
        self.assertEqual(code, 0)
        client.acquire_cell_lease.assert_called_once_with(
            "notebooks/demo.ipynb",
            session_id="sess-1",
            cell_id="cell-1",
            cell_index=None,
            kind="structure",
            ttl_seconds=12.0,
        )

    def test_core_cell_lease_release(self):
        client = self._mock_core_client()
        buf = StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            with mock.patch("agent_repl.cli._core_client", return_value=client):
                code = main([
                    "core", "cell-lease-release", "notebooks/demo.ipynb",
                    "--session-id", "sess-1",
                    "--cell-index", "2",
                ])
        finally:
            sys.stdout = old
        self.assertEqual(code, 0)
        client.release_cell_lease.assert_called_once_with(
            "notebooks/demo.ipynb",
            session_id="sess-1",
            cell_id=None,
            cell_index=2,
        )

    def test_core_branches(self):
        client = self._mock_core_client()
        buf = StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            with mock.patch("agent_repl.cli._core_client", return_value=client):
                code = main(["core", "branches"])
        finally:
            sys.stdout = old
        self.assertEqual(code, 0)
        client.list_branches.assert_called_once()

    def test_core_branch_start(self):
        client = self._mock_core_client()
        buf = StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            with mock.patch("agent_repl.cli._core_client", return_value=client):
                code = main([
                    "core", "branch-start",
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

    def test_core_branch_finish(self):
        client = self._mock_core_client()
        buf = StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            with mock.patch("agent_repl.cli._core_client", return_value=client):
                code = main(["core", "branch-finish", "--branch-id", "branch-1", "--status-value", "merged"])
        finally:
            sys.stdout = old
        self.assertEqual(code, 0)
        client.finish_branch.assert_called_once_with("branch-1", status="merged")

    def test_core_branch_review_request(self):
        client = self._mock_core_client()
        buf = StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            with mock.patch("agent_repl.cli._core_client", return_value=client):
                code = main([
                    "core", "branch-review-request",
                    "--branch-id", "branch-1",
                    "--requested-by-session-id", "sess-1",
                    "--note", "Please review",
                ])
        finally:
            sys.stdout = old
        self.assertEqual(code, 0)
        client.request_branch_review.assert_called_once_with(
            "branch-1",
            requested_by_session_id="sess-1",
            note="Please review",
        )

    def test_core_branch_review_resolve(self):
        client = self._mock_core_client()
        buf = StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            with mock.patch("agent_repl.cli._core_client", return_value=client):
                code = main([
                    "core", "branch-review-resolve",
                    "--branch-id", "branch-1",
                    "--resolved-by-session-id", "sess-2",
                    "--resolution", "approved",
                    "--note", "Ship it",
                ])
        finally:
            sys.stdout = old
        self.assertEqual(code, 0)
        client.resolve_branch_review.assert_called_once_with(
            "branch-1",
            resolved_by_session_id="sess-2",
            resolution="approved",
            note="Ship it",
        )

    def test_core_runtimes(self):
        client = self._mock_core_client()
        buf = StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            with mock.patch("agent_repl.cli._core_client", return_value=client):
                code = main(["core", "runtimes"])
        finally:
            sys.stdout = old
        self.assertEqual(code, 0)
        client.list_runtimes.assert_called_once()

    def test_core_runtime_start(self):
        client = self._mock_core_client()
        buf = StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            with mock.patch("agent_repl.cli._core_client", return_value=client):
                code = main(["core", "runtime-start", "--mode", "shared", "--label", "primary", "--environment", ".venv"])
        finally:
            sys.stdout = old
        self.assertEqual(code, 0)
        client.start_runtime.assert_called_once_with(
            mode="shared",
            label="primary",
            runtime_id=None,
            environment=".venv",
            document_path=None,
            ttl_seconds=None,
        )

    def test_core_runtime_stop(self):
        client = self._mock_core_client()
        buf = StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            with mock.patch("agent_repl.cli._core_client", return_value=client):
                code = main(["core", "runtime-stop", "--runtime-id", "rt-1"])
        finally:
            sys.stdout = old
        self.assertEqual(code, 0)
        client.stop_runtime.assert_called_once_with("rt-1")

    def test_core_runtime_recover(self):
        client = self._mock_core_client()
        buf = StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            with mock.patch("agent_repl.cli._core_client", return_value=client):
                code = main(["core", "runtime-recover", "--runtime-id", "rt-1"])
        finally:
            sys.stdout = old
        self.assertEqual(code, 0)
        client.recover_runtime.assert_called_once_with("rt-1")

    def test_core_runtime_promote(self):
        client = self._mock_core_client()
        buf = StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            with mock.patch("agent_repl.cli._core_client", return_value=client):
                code = main(["core", "runtime-promote", "--runtime-id", "rt-1", "--mode", "pinned"])
        finally:
            sys.stdout = old
        self.assertEqual(code, 0)
        client.promote_runtime.assert_called_once_with("rt-1", mode="pinned")

    def test_core_runtime_discard(self):
        client = self._mock_core_client()
        buf = StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            with mock.patch("agent_repl.cli._core_client", return_value=client):
                code = main(["core", "runtime-discard", "--runtime-id", "rt-1"])
        finally:
            sys.stdout = old
        self.assertEqual(code, 0)
        client.discard_runtime.assert_called_once_with("rt-1")

    def test_core_runs(self):
        client = self._mock_core_client()
        buf = StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            with mock.patch("agent_repl.cli._core_client", return_value=client):
                code = main(["core", "runs"])
        finally:
            sys.stdout = old
        self.assertEqual(code, 0)
        client.list_runs.assert_called_once()

    def test_core_run_start(self):
        client = self._mock_core_client()
        buf = StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            with mock.patch("agent_repl.cli._core_client", return_value=client):
                code = main([
                    "core", "run-start",
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

    def test_core_run_finish(self):
        client = self._mock_core_client()
        buf = StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            with mock.patch("agent_repl.cli._core_client", return_value=client):
                code = main(["core", "run-finish", "--run-id", "run-1", "--status-value", "completed"])
        finally:
            sys.stdout = old
        self.assertEqual(code, 0)
        client.finish_run.assert_called_once_with("run-1", status="completed")

    def test_mcp_setup_returns_canonical_connection_details(self):
        discovered = self._mock_core_client()
        discovered.base_url = "http://127.0.0.1:4312"
        discovered.token = "secret-token"
        buf = StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            with (
                mock.patch(
                    "agent_repl.cli.CoreClient.start",
                    return_value={"status": "ok", "workspace_root": "/workspace", "already_running": False},
                ) as mock_start,
                mock.patch("agent_repl.cli.CoreClient.discover", return_value=discovered),
            ):
                code = main(["mcp", "setup", "--workspace-root", "/workspace"])
        finally:
            sys.stdout = old
        self.assertEqual(code, 0)
        mock_start.assert_called_once_with("/workspace", runtime_dir=None)
        payload = json.loads(buf.getvalue())
        self.assertEqual(payload["mcp"]["url"], "http://127.0.0.1:4312/mcp")
        self.assertEqual(payload["mcp"]["legacy_url"], "http://127.0.0.1:4312/mcp/mcp")
        self.assertEqual(
            payload["config"]["mcpServers"]["agent-repl"]["headers"]["Authorization"],
            "token secret-token",
        )

    def test_mcp_config_prints_standard_mcp_servers_block(self):
        discovered = self._mock_core_client()
        discovered.base_url = "http://127.0.0.1:4312"
        discovered.token = "secret-token"
        buf = StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            with (
                mock.patch(
                    "agent_repl.cli.CoreClient.start",
                    return_value={"status": "ok", "workspace_root": os.getcwd(), "already_running": True},
                ),
                mock.patch("agent_repl.cli.CoreClient.discover", return_value=discovered),
            ):
                code = main(["mcp", "config", "--server-name", "analysis-repl"])
        finally:
            sys.stdout = old
        self.assertEqual(code, 0)
        payload = json.loads(buf.getvalue())
        self.assertEqual(list(payload["mcpServers"].keys()), ["analysis-repl"])
        self.assertEqual(payload["mcpServers"]["analysis-repl"]["url"], "http://127.0.0.1:4312/mcp")

    def test_mcp_smoke_test_runs_reported_checks(self):
        buf = StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            with mock.patch(
                "agent_repl.cli._mcp_smoke_test_payload",
                return_value={
                    "status": "ok",
                    "mcp": {"url": "http://127.0.0.1:4312/mcp"},
                    "checks": [
                        {"name": "core-status", "status": "ok"},
                        {"name": "list-tools", "status": "ok", "tool_count": 42},
                    ],
                },
            ) as mock_smoke:
                code = main(["mcp", "smoke-test"])
        finally:
            sys.stdout = old
        self.assertEqual(code, 0)
        mock_smoke.assert_called_once()
        payload = json.loads(buf.getvalue())
        self.assertEqual(payload["checks"][1]["tool_count"], 42)

    def test_doctor_reports_workspace_canvas_status_and_recommendations(self):
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        workspace_root = Path(tmpdir.name)
        buf = StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            with mock.patch("agent_repl.cli.shutil.which", side_effect=lambda name: "/usr/bin/code" if name == "code" else None):
                code = main(["doctor", "--workspace-root", str(workspace_root)])
        finally:
            sys.stdout = old
        self.assertEqual(code, 0)
        payload = json.loads(buf.getvalue())
        self.assertEqual(payload["workspace_root"], os.path.realpath(str(workspace_root)))
        self.assertFalse(payload["editor"]["workspace"]["default_canvas_configured"])
        self.assertTrue(
            any("agent-repl editor configure --default-canvas" in item for item in payload["recommendations"])
        )

    def test_doctor_reports_extension_dev_loop_and_sync_mismatch(self):
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        workspace_root = Path(tmpdir.name)
        repo_extension = workspace_root / "extension"
        (repo_extension / "scripts").mkdir(parents=True)
        (repo_extension / "scripts" / "preview-webview.mjs").write_text("// preview\n", encoding="utf-8")
        (repo_extension / "out").mkdir()
        (repo_extension / "media").mkdir()
        (repo_extension / "package.json").write_text(json.dumps({"version": "0.3.0"}), encoding="utf-8")
        (repo_extension / "out" / "extension.js").write_text("repo extension\n", encoding="utf-8")
        (repo_extension / "out" / "routes.js").write_text("repo routes\n", encoding="utf-8")
        (repo_extension / "media" / "canvas.js").write_text("repo canvas js\n", encoding="utf-8")
        (repo_extension / "media" / "canvas.css").write_text("repo canvas css\n", encoding="utf-8")

        installed_root = workspace_root / "installed-vscode"
        (installed_root / "out").mkdir(parents=True)
        (installed_root / "media").mkdir()
        (installed_root / "package.json").write_text(json.dumps({"version": "0.3.0"}), encoding="utf-8")
        (installed_root / "out" / "extension.js").write_text("installed extension\n", encoding="utf-8")
        (installed_root / "out" / "routes.js").write_text("installed routes\n", encoding="utf-8")
        (installed_root / "media" / "canvas.js").write_text("installed canvas js\n", encoding="utf-8")
        (installed_root / "media" / "canvas.css").write_text("installed canvas css\n", encoding="utf-8")

        buf = StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            with (
                mock.patch("agent_repl.cli.shutil.which", side_effect=lambda name: "/usr/bin/code" if name == "code" else None),
                mock.patch(
                    "agent_repl.cli._detect_installed_extensions",
                    return_value={
                        "vscode": {"extensions_root": "/tmp/vscode", "installed": [str(installed_root)]},
                        "cursor": {"extensions_root": "/tmp/cursor", "installed": []},
                        "windsurf": {"extensions_root": "/tmp/windsurf", "installed": []},
                    },
                ),
            ):
                code = main(["doctor", "--workspace-root", str(workspace_root)])
        finally:
            sys.stdout = old
        self.assertEqual(code, 0)
        payload = json.loads(buf.getvalue())
        self.assertEqual(payload["editor"]["development"]["repo_extension"]["status"], "ok")
        self.assertEqual(payload["editor"]["development"]["sync"]["vscode"]["status"], "warn")
        self.assertIn("vscode", payload["editor"]["development"]["mismatch_editors"])
        self.assertTrue(
            any("agent-repl editor dev --editor vscode" in item for item in payload["recommendations"])
        )

    def test_editor_dev_launches_extension_development_host(self):
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        workspace_root = Path(tmpdir.name)
        repo_extension = workspace_root / "extension"
        (repo_extension / "scripts").mkdir(parents=True)
        (repo_extension / "scripts" / "preview-webview.mjs").write_text("// preview\n", encoding="utf-8")
        (repo_extension / "out").mkdir()
        (repo_extension / "media").mkdir()
        (repo_extension / "package.json").write_text(json.dumps({"version": "0.3.0"}), encoding="utf-8")
        (repo_extension / "out" / "extension.js").write_text("repo extension\n", encoding="utf-8")
        (repo_extension / "out" / "routes.js").write_text("repo routes\n", encoding="utf-8")
        (repo_extension / "media" / "canvas.js").write_text("repo canvas js\n", encoding="utf-8")
        (repo_extension / "media" / "canvas.css").write_text("repo canvas css\n", encoding="utf-8")

        buf = StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            with (
                mock.patch("agent_repl.cli.shutil.which", side_effect=lambda name: "/usr/bin/code" if name == "code" else None),
                mock.patch("agent_repl.cli.subprocess.Popen") as mock_popen,
            ):
                code = main(["editor", "dev", "--workspace-root", str(workspace_root), "--skip-compile"])
        finally:
            sys.stdout = old
        self.assertEqual(code, 0)
        payload = json.loads(buf.getvalue())
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["editor"], "vscode")
        self.assertEqual(payload["extension_root"], os.path.realpath(str(repo_extension)))
        self.assertFalse(payload["compiled"])
        mock_popen.assert_called_once()
        command = mock_popen.call_args.args[0]
        self.assertIn("--extensionDevelopmentPath", command)
        self.assertIn(os.path.realpath(str(repo_extension)), command)
        self.assertIn(os.path.realpath(str(workspace_root)), command)

    def test_reload_reports_build_sync_warning(self):
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        workspace_root = Path(tmpdir.name)
        repo_extension = workspace_root / "extension"
        (repo_extension / "scripts").mkdir(parents=True)
        (repo_extension / "scripts" / "preview-webview.mjs").write_text("// preview\n", encoding="utf-8")
        (repo_extension / "out").mkdir()
        (repo_extension / "media").mkdir()
        (repo_extension / "package.json").write_text(json.dumps({"version": "0.3.0"}), encoding="utf-8")
        (repo_extension / "out" / "extension.js").write_text("repo extension\n", encoding="utf-8")
        (repo_extension / "out" / "routes.js").write_text("repo routes\n", encoding="utf-8")
        (repo_extension / "media" / "canvas.js").write_text("repo canvas js\n", encoding="utf-8")
        (repo_extension / "media" / "canvas.css").write_text("repo canvas css\n", encoding="utf-8")

        installed_root = workspace_root / "installed-vscode"
        (installed_root / "out").mkdir(parents=True)
        (installed_root / "media").mkdir()
        (installed_root / "package.json").write_text(json.dumps({"version": "0.3.0"}), encoding="utf-8")
        (installed_root / "out" / "extension.js").write_text("installed extension\n", encoding="utf-8")
        (installed_root / "out" / "routes.js").write_text("installed routes\n", encoding="utf-8")
        (installed_root / "media" / "canvas.js").write_text("installed canvas js\n", encoding="utf-8")
        (installed_root / "media" / "canvas.css").write_text("installed canvas css\n", encoding="utf-8")

        buf = StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            with (
                mock.patch("agent_repl.cli._workspace_root", return_value=os.path.realpath(str(workspace_root))),
                mock.patch("agent_repl.cli._client") as mock_client_factory,
            ):
                mock_client_factory.return_value.reload.return_value = {
                    "status": "ok",
                    "extension_root": str(installed_root),
                }
                code = main(["reload"])
        finally:
            sys.stdout = old
        self.assertEqual(code, 0)
        payload = json.loads(buf.getvalue())
        self.assertEqual(payload["build_sync"]["status"], "warn")
        self.assertIn("warning", payload)

    def test_editor_configure_sets_workspace_default_canvas(self):
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        workspace_root = Path(tmpdir.name)
        settings_path = workspace_root / ".vscode" / "settings.json"
        settings_path.parent.mkdir(parents=True)
        settings_path.write_text(json.dumps({"python.defaultInterpreterPath": ".venv/bin/python"}), encoding="utf-8")
        buf = StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            code = main(["editor", "configure", "--workspace-root", str(workspace_root), "--default-canvas"])
        finally:
            sys.stdout = old
        self.assertEqual(code, 0)
        payload = json.loads(buf.getvalue())
        self.assertEqual(payload["association"], "agent-repl.canvasEditor")
        updated = json.loads(settings_path.read_text(encoding="utf-8"))
        self.assertEqual(updated["workbench.editorAssociations"]["*.ipynb"], "agent-repl.canvasEditor")
        self.assertEqual(updated["python.defaultInterpreterPath"], ".venv/bin/python")

    def test_setup_can_configure_editor_and_include_public_mcp_surface(self):
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        workspace_root = Path(tmpdir.name)
        buf = StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            with (
                mock.patch("agent_repl.cli._mcp_connection_payload", return_value={"status": "ok", "mcp": {"url": "http://127.0.0.1:4312/mcp"}}),
                mock.patch("agent_repl.cli._mcp_smoke_test_payload", return_value={"status": "ok", "checks": [{"name": "list-tools", "status": "ok"}]}),
            ):
                code = main([
                    "setup",
                    "--workspace-root",
                    str(workspace_root),
                    "--configure-editor-default",
                    "--with-mcp",
                ])
        finally:
            sys.stdout = old
        self.assertEqual(code, 0)
        payload = json.loads(buf.getvalue())
        self.assertEqual(payload["actions"][0]["name"], "editor-configure")
        self.assertEqual(payload["actions"][1]["name"], "mcp-setup")
        self.assertEqual(payload["actions"][2]["name"], "mcp-smoke-test")
        self.assertTrue(payload["doctor"]["editor"]["workspace"]["default_canvas_configured"])
        updated = json.loads((workspace_root / ".vscode" / "settings.json").read_text(encoding="utf-8"))
        self.assertEqual(updated["workbench.editorAssociations"]["*.ipynb"], "agent-repl.canvasEditor")


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
        self.assertNotIn("core", stdout.getvalue())

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
        self.assertIn("not auto-executed", skill)
        self.assertNotIn("agent-repl v2 --help", skill)
        self.assertNotIn("may briefly steal focus", skill)
        self.assertNotIn("make install-dev", skill)
        self.assertNotIn("make install-ext", skill)

    def test_getting_started_matches_ix_wait_behavior(self):
        root = Path(__file__).resolve().parents[1]
        guide = (root / "docs" / "getting-started.md").read_text()
        self.assertIn("ix", guide)
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
        self.assertIn("agent-repl setup --smoke-test", install)
        self.assertIn("agent-repl mcp setup", install)
        self.assertIn("agent-repl editor configure --default-canvas", install)
        self.assertIn("npx --yes @vscode/vsce package", install)
        self.assertNotIn("make install-dev", install)
        self.assertNotIn("make install-ext", install)
        self.assertNotIn("make verify-install", install)

    def test_command_reference_describes_live_cell_ids_and_session_reuse(self):
        root = Path(__file__).resolve().parents[1]
        commands = (root / "docs" / "commands.md").read_text()
        self.assertIn("live `cell_id` values", commands)
        self.assertIn("agent-repl setup", commands)
        self.assertIn("agent-repl doctor", commands)
        self.assertIn("agent-repl editor configure --default-canvas", commands)
        self.assertIn("`agent-repl mcp setup`", commands)
        self.assertIn("`--session-id` overrides the default session reuse", commands)
        self.assertIn("agent-repl reload --pretty", commands)
        self.assertNotIn("index-1", commands)
        self.assertNotIn("fire-and-forget behavior", commands)
        self.assertNotIn("## v2", commands)
        self.assertNotIn("agent-repl v2", commands)

    def test_readme_and_docs_summary_link_to_mcp_guide(self):
        root = Path(__file__).resolve().parents[1]
        readme = (root / "README.md").read_text()
        summary = (root / "docs" / "SUMMARY.md").read_text()
        self.assertIn("[MCP](", readme)
        self.assertIn("[MCP](", summary)

    def test_public_docs_lock_in_workspace_venv_as_default_kernel(self):
        root = Path(__file__).resolve().parents[1]
        skill = (root / "SKILL.md").read_text()
        commands = (root / "docs" / "commands.md").read_text()
        self.assertIn("`.venv` exists, it is the default runtime", skill)
        self.assertIn("`select-kernel` changes the active kernel", skill)
        self.assertIn("`.venv` first", commands)
        self.assertIn("--interactive", commands)


class TestServerRobustness(unittest.TestCase):
    def test_asgi_execute_cell_keeps_activity_polling_live_while_running(self):
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

        notebook_path = "notebooks/live-http-stream.ipynb"
        source = "import time\nprint('start', flush=True)\ntime.sleep(0.4)\nprint('done', flush=True)"

        with mock.patch.object(state, "_projection_client", return_value=None):
            create_body, create_status = state.notebook_create(
                notebook_path,
                cells=[{"type": "code", "source": source}],
                kernel_id=_python_with_ipykernel(),
            )
            self.assertEqual(create_status, 200)
            self.assertTrue(create_body["ready"])

        state.start_session("agent", "cli", "worker", "sess-agent", ["projection", "ops", "automation"])

        import uvicorn
        from agent_repl.core.asgi import create_app

        app = create_app(state)
        config = uvicorn.Config(
            app,
            host="127.0.0.1",
            port=0,
            log_level="error",
            ws="none",
        )
        uv_server = uvicorn.Server(config)
        thread = threading.Thread(target=uv_server.run, daemon=True)
        thread.start()
        for _ in range(50):
            if uv_server.started:
                break
            time.sleep(0.05)

        def _stop_server() -> None:
            uv_server.should_exit = True
            thread.join(timeout=5)

        self.addCleanup(_stop_server)

        port = uv_server.servers[0].sockets[0].getsockname()[1]
        client = CoreClient(f"http://127.0.0.1:{port}", "tok")
        activity_before = client.notebook_activity(notebook_path)

        result_holder: dict[str, Any] = {}

        def run_execution() -> None:
            result_holder["body"] = client.notebook_execute_cell(
                notebook_path,
                cell_index=0,
                wait=False,
                owner_session_id="sess-agent",
            )

        execution_thread = threading.Thread(target=run_execution, daemon=True)
        execution_thread.start()

        stream_event: dict[str, Any] | None = None
        deadline = time.time() + 10
        while time.time() < deadline:
            body = client.notebook_activity(notebook_path, since=activity_before["cursor"])
            stream_event = next((event for event in body["recent_events"] if event["type"] == "cell-output-appended"), None)
            if stream_event is not None:
                break
            time.sleep(0.05)

        self.assertIsNotNone(stream_event)
        execution_thread.join(timeout=2)
        self.assertFalse(execution_thread.is_alive(), "wait=False should return once the execution is queued")
        self.assertEqual(result_holder["body"]["status"], "started")
        self.assertEqual(stream_event["data"]["output"]["output_type"], "stream")
        self.assertIn("start", stream_event["data"]["output"]["text"])

    def test_asgi_execute_cell_reports_second_async_cell_as_queued(self):
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

        notebook_path = "notebooks/queued-http-stream.ipynb"
        first_source = "import time\nprint('start', flush=True)\ntime.sleep(0.4)\nprint('done', flush=True)"
        second_source = "21 * 2"

        with mock.patch.object(state, "_projection_client", return_value=None):
            create_body, create_status = state.notebook_create(
                notebook_path,
                cells=[
                    {"type": "code", "source": first_source},
                    {"type": "code", "source": second_source},
                ],
                kernel_id=_python_with_ipykernel(),
            )
            self.assertEqual(create_status, 200)
            self.assertTrue(create_body["ready"])

        state.start_session("agent", "cli", "worker", "sess-agent", ["projection", "ops", "automation"])

        import uvicorn
        from agent_repl.core.asgi import create_app

        app = create_app(state)
        config = uvicorn.Config(
            app,
            host="127.0.0.1",
            port=0,
            log_level="error",
            ws="none",
        )
        uv_server = uvicorn.Server(config)
        thread = threading.Thread(target=uv_server.run, daemon=True)
        thread.start()
        for _ in range(50):
            if uv_server.started:
                break
            time.sleep(0.05)

        def _stop_server() -> None:
            uv_server.should_exit = True
            thread.join(timeout=5)

        self.addCleanup(_stop_server)

        port = uv_server.servers[0].sockets[0].getsockname()[1]
        client = CoreClient(f"http://127.0.0.1:{port}", "tok")

        first = client.notebook_execute_cell(
            notebook_path,
            cell_index=0,
            wait=False,
            owner_session_id="sess-agent",
        )
        self.assertEqual(first["status"], "started")

        second = client.notebook_execute_cell(
            notebook_path,
            cell_index=1,
            wait=False,
            owner_session_id="sess-agent",
        )
        self.assertEqual(second["status"], "queued")

        status_body = client.notebook_status(notebook_path)
        self.assertEqual([item["cell_id"] for item in status_body["running"]], [first["cell_id"]])
        self.assertEqual([item["cell_id"] for item in status_body["queued"]], [second["cell_id"]])
        self.assertEqual(status_body["queued"][0]["queue_position"], 1)

        queued_lookup = client.notebook_execution(second["execution_id"])
        self.assertEqual(queued_lookup["status"], "queued")

        final_lookup: dict[str, Any] | None = None
        deadline = time.time() + 10
        while time.time() < deadline:
            final_lookup = client.notebook_execution(second["execution_id"])
            if final_lookup["status"] in {"ok", "error"}:
                break
            time.sleep(0.05)

        self.assertIsNotNone(final_lookup)
        self.assertEqual(final_lookup["status"], "ok")

        contents = client.notebook_contents(notebook_path)
        second_cell = next(cell for cell in contents["cells"] if cell["cell_id"] == second["cell_id"])
        self.assertEqual(second_cell["outputs"][0]["data"]["text/plain"], "42")

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

        import threading
        import uvicorn
        from agent_repl.core.asgi import create_app

        app = create_app(state)
        config = uvicorn.Config(
            app,
            host="127.0.0.1",
            port=0,
            log_level="error",
            ws="none",
        )
        uv_server = uvicorn.Server(config)
        thread = threading.Thread(target=uv_server.run, daemon=True)
        thread.start()
        # Wait for the server to start
        import time
        for _ in range(50):
            if uv_server.started:
                break
            time.sleep(0.05)
        def _stop_server() -> None:
            uv_server.should_exit = True
            thread.join(timeout=5)
        self.addCleanup(_stop_server)

        port = uv_server.servers[0].sockets[0].getsockname()[1]
        client = CoreClient(f"http://127.0.0.1:{port}", "tok")

        with self.assertRaisesRegex(RuntimeError, "boom for run-1:completed"):
            client.finish_run("run-1", status="completed")


if __name__ == "__main__":
    unittest.main()
