"""Tests for the checkpoint service."""
from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from unittest import mock

import nbformat

from agent_repl.core.checkpoint_service import CheckpointService
from agent_repl.core.server import CheckpointRecord, CoreState


def _make_state(workspace_root: str) -> CoreState:
    """Create a minimal CoreState wired for checkpoint tests."""
    runtime_dir = os.path.join(workspace_root, ".agent-repl")
    os.makedirs(runtime_dir, exist_ok=True)
    state = CoreState(
        workspace_root=workspace_root,
        runtime_dir=runtime_dir,
        token="test-token",
        pid=os.getpid(),
        started_at=time.time(),
    )
    # Avoid SQLite persistence during tests
    state._db = None
    return state


def _write_notebook(path: str, cells: list[dict]) -> None:
    """Write a minimal .ipynb file."""
    nb = nbformat.v4.new_notebook()
    for cell_data in cells:
        cell_type = cell_data.get("cell_type", "code")
        source = cell_data.get("source", "")
        if cell_type == "code":
            nb.cells.append(nbformat.v4.new_code_cell(source))
        else:
            nb.cells.append(nbformat.v4.new_markdown_cell(source))
    with open(path, "w", encoding="utf-8") as f:
        nbformat.write(nb, f)


class TestCheckpointCreate(unittest.TestCase):
    def test_create_checkpoint_stores_record(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            nb_path = os.path.join(tmpdir, "demo.ipynb")
            _write_notebook(nb_path, [{"source": "x = 1"}])

            state = _make_state(tmpdir)
            payload, status = state.checkpoint_create("demo.ipynb")

            self.assertEqual(status.value, 200)
            self.assertEqual(payload["status"], "ok")
            cp = payload["checkpoint"]
            self.assertIn("checkpoint_id", cp)
            self.assertEqual(cp["path"], "demo.ipynb")

            # Record is in state
            self.assertIn(cp["checkpoint_id"], state.checkpoint_records)
            record = state.checkpoint_records[cp["checkpoint_id"]]
            # Snapshot contains the cell source
            self.assertIn("x = 1", record.snapshot_nbformat)

    def test_create_checkpoint_with_label(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            nb_path = os.path.join(tmpdir, "demo.ipynb")
            _write_notebook(nb_path, [{"source": "y = 2"}])

            state = _make_state(tmpdir)
            payload, status = state.checkpoint_create("demo.ipynb", label="before refactor")

            self.assertEqual(status.value, 200)
            self.assertEqual(payload["checkpoint"]["label"], "before refactor")


class TestCheckpointList(unittest.TestCase):
    def test_list_checkpoints_returns_correct_entries(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            nb_path = os.path.join(tmpdir, "demo.ipynb")
            _write_notebook(nb_path, [{"source": "a = 1"}])

            state = _make_state(tmpdir)
            state.checkpoint_create("demo.ipynb", label="first")
            state.checkpoint_create("demo.ipynb", label="second")

            payload, status = state.checkpoint_list("demo.ipynb")
            self.assertEqual(status.value, 200)
            self.assertEqual(payload["count"], 2)
            # Sorted newest first
            labels = [cp["label"] for cp in payload["checkpoints"]]
            self.assertEqual(labels, ["second", "first"])

    def test_list_checkpoints_filters_by_path(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            _write_notebook(os.path.join(tmpdir, "a.ipynb"), [{"source": "a"}])
            _write_notebook(os.path.join(tmpdir, "b.ipynb"), [{"source": "b"}])

            state = _make_state(tmpdir)
            state.checkpoint_create("a.ipynb")
            state.checkpoint_create("b.ipynb")

            payload, _status = state.checkpoint_list("a.ipynb")
            self.assertEqual(payload["count"], 1)
            self.assertEqual(payload["checkpoints"][0]["path"], "a.ipynb")


class TestCheckpointRestore(unittest.TestCase):
    def test_restore_recovers_prior_state(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            nb_path = os.path.join(tmpdir, "demo.ipynb")
            _write_notebook(nb_path, [{"source": "original"}])

            state = _make_state(tmpdir)
            # Create checkpoint of original state
            cp_payload, _ = state.checkpoint_create("demo.ipynb")
            checkpoint_id = cp_payload["checkpoint"]["checkpoint_id"]

            # Modify the notebook
            _write_notebook(nb_path, [{"source": "modified"}])
            # Force YDoc to reload from disk
            real_path = os.path.realpath(nb_path)
            relative_path = os.path.relpath(real_path, tmpdir)
            state._ydoc_service.close(relative_path)

            # Restore checkpoint
            restore_payload, status = state.checkpoint_restore(checkpoint_id)
            self.assertEqual(status.value, 200)
            self.assertTrue(restore_payload["restored"])

            # Verify the file on disk has the original content
            with open(nb_path, "r", encoding="utf-8") as f:
                nb = nbformat.read(f, as_version=4)
            self.assertEqual(nb.cells[0].source, "original")

    def test_restore_refuses_when_execution_active(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            nb_path = os.path.join(tmpdir, "demo.ipynb")
            _write_notebook(nb_path, [{"source": "x = 1"}])

            state = _make_state(tmpdir)
            cp_payload, _ = state.checkpoint_create("demo.ipynb")
            checkpoint_id = cp_payload["checkpoint"]["checkpoint_id"]

            # Simulate an active execution
            state.execution_records["exec-1"] = {
                "execution_id": "exec-1",
                "status": "running",
                "path": "demo.ipynb",
                "runtime_id": "rt-1",
                "cell_id": "cell-1",
                "cell_index": 0,
                "source_preview": "x = 1",
                "owner": "human",
                "session_id": None,
                "operation": "execute",
                "created_at": time.time(),
                "updated_at": time.time(),
            }

            restore_payload, status = state.checkpoint_restore(checkpoint_id)
            self.assertEqual(status.value, 409)
            self.assertIn("Cannot restore", restore_payload["error"])

    def test_restore_validates_snapshot_before_swapping(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            nb_path = os.path.join(tmpdir, "demo.ipynb")
            _write_notebook(nb_path, [{"source": "good"}])

            state = _make_state(tmpdir)
            cp_payload, _ = state.checkpoint_create("demo.ipynb")
            checkpoint_id = cp_payload["checkpoint"]["checkpoint_id"]

            # Corrupt the snapshot
            record = state.checkpoint_records[checkpoint_id]
            record.snapshot_nbformat = "{{not valid json notebook}}"

            restore_payload, status = state.checkpoint_restore(checkpoint_id)
            self.assertEqual(status.value, 422)
            self.assertIn("invalid", restore_payload["error"].lower())

            # Original file should be unchanged
            with open(nb_path, "r", encoding="utf-8") as f:
                nb = nbformat.read(f, as_version=4)
            self.assertEqual(nb.cells[0].source, "good")

    def test_restore_not_found(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state = _make_state(tmpdir)
            payload, status = state.checkpoint_restore("nonexistent")
            self.assertEqual(status.value, 404)

    def test_roundtrip_ipynb_matches_snapshot(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            nb_path = os.path.join(tmpdir, "demo.ipynb")
            _write_notebook(nb_path, [{"source": "snap_me"}])

            state = _make_state(tmpdir)
            cp_payload, _ = state.checkpoint_create("demo.ipynb")
            checkpoint_id = cp_payload["checkpoint"]["checkpoint_id"]
            record = state.checkpoint_records[checkpoint_id]

            # Modify then restore
            _write_notebook(nb_path, [{"source": "changed"}])
            state._ydoc_service.close("demo.ipynb")

            state.checkpoint_restore(checkpoint_id)

            # The written file should round-trip to the same content as the snapshot
            with open(nb_path, "r", encoding="utf-8") as f:
                written = f.read()
            restored_nb = nbformat.reads(written, as_version=4)
            snapshot_nb = nbformat.reads(record.snapshot_nbformat, as_version=4)
            self.assertEqual(len(restored_nb.cells), len(snapshot_nb.cells))
            for restored_cell, snapshot_cell in zip(restored_nb.cells, snapshot_nb.cells):
                self.assertEqual(restored_cell.source, snapshot_cell.source)
                self.assertEqual(restored_cell.cell_type, snapshot_cell.cell_type)


class TestCheckpointDelete(unittest.TestCase):
    def test_delete_removes_checkpoint(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            nb_path = os.path.join(tmpdir, "demo.ipynb")
            _write_notebook(nb_path, [{"source": "x = 1"}])

            state = _make_state(tmpdir)
            cp_payload, _ = state.checkpoint_create("demo.ipynb")
            checkpoint_id = cp_payload["checkpoint"]["checkpoint_id"]

            payload, status = state.checkpoint_delete(checkpoint_id)
            self.assertEqual(status.value, 200)
            self.assertTrue(payload["deleted"])
            self.assertNotIn(checkpoint_id, state.checkpoint_records)

    def test_delete_not_found(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state = _make_state(tmpdir)
            payload, status = state.checkpoint_delete("nonexistent")
            self.assertEqual(status.value, 404)


class TestCheckpointMultiCell(unittest.TestCase):
    def test_multi_cell_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            nb_path = os.path.join(tmpdir, "multi.ipynb")
            _write_notebook(nb_path, [
                {"source": "import os", "cell_type": "code"},
                {"source": "# Notes", "cell_type": "markdown"},
                {"source": "print('hello')", "cell_type": "code"},
            ])

            state = _make_state(tmpdir)
            cp_payload, _ = state.checkpoint_create("multi.ipynb")
            checkpoint_id = cp_payload["checkpoint"]["checkpoint_id"]

            # Replace with a single cell
            _write_notebook(nb_path, [{"source": "replaced"}])
            state._ydoc_service.close("multi.ipynb")

            state.checkpoint_restore(checkpoint_id)

            with open(nb_path, "r", encoding="utf-8") as f:
                nb = nbformat.read(f, as_version=4)
            self.assertEqual(len(nb.cells), 3)
            self.assertEqual(nb.cells[0].source, "import os")
            self.assertEqual(nb.cells[1].source, "# Notes")
            self.assertEqual(nb.cells[1].cell_type, "markdown")
            self.assertEqual(nb.cells[2].source, "print('hello')")


if __name__ == "__main__":
    unittest.main()
