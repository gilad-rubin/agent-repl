"""YDoc collaboration spike tests.

These tests prove or disprove that Jupyter's YDoc stack can replace the
custom cell-lease concurrency model with CRDT-based collaborative editing.

Go/no-go criteria:
- Two independent YDoc clients can edit the same notebook source/structure
  and converge to a consistent state after syncing updates.
- Notebook round-trips through YDoc preserve cell identity, type, and source.
- Session identity can be mapped to YDoc Awareness for presence.
"""
from __future__ import annotations

import json
import unittest

import pycrdt
from jupyter_ydoc import YNotebook


class TestTwoClientEditSync(unittest.TestCase):
    """Prove that two YDoc clients can edit the same notebook and converge."""

    def _make_notebook(self) -> YNotebook:
        nb = YNotebook()
        return nb

    def _sync(self, source: YNotebook, target: YNotebook) -> None:
        """Simulate sync by applying one doc's update to the other."""
        update = source.ydoc.get_update()
        target.ydoc.apply_update(update)

    def test_two_clients_add_cells_and_converge(self):
        # Client A creates a notebook with one cell
        client_a = self._make_notebook()
        client_a.append_cell(
            {"cell_type": "code", "source": "x = 1", "metadata": {}, "outputs": []},
        )

        # Sync A → B
        client_b = self._make_notebook()
        self._sync(client_a, client_b)

        cells_b = json.loads(str(client_b.ycells))
        self.assertEqual(len(cells_b), 1)
        self.assertEqual(cells_b[0]["source"], "x = 1")

        # Client B adds a second cell
        client_b.append_cell(
            {"cell_type": "code", "source": "y = 2", "metadata": {}, "outputs": []},
        )

        # Sync B → A
        self._sync(client_b, client_a)

        cells_a = json.loads(str(client_a.ycells))
        self.assertEqual(len(cells_a), 2)
        self.assertEqual(cells_a[0]["source"], "x = 1")
        self.assertEqual(cells_a[1]["source"], "y = 2")

    def test_concurrent_source_edits_converge(self):
        # Both clients start with the same notebook
        client_a = self._make_notebook()
        client_a.append_cell(
            {"cell_type": "code", "source": "original", "metadata": {}, "outputs": []},
        )
        client_b = self._make_notebook()
        self._sync(client_a, client_b)

        # Client A edits source to "modified_a"
        cells_a = json.loads(str(client_a.ycells))
        cell_id = cells_a[0]["id"]
        client_a.set_cell(0, {"cell_type": "code", "source": "modified_a", "metadata": {}, "outputs": [], "id": cell_id})

        # Client B edits source to "modified_b" (without seeing A's edit)
        cells_b = json.loads(str(client_b.ycells))
        cell_id_b = cells_b[0]["id"]
        client_b.set_cell(0, {"cell_type": "code", "source": "modified_b", "metadata": {}, "outputs": [], "id": cell_id_b})

        # Sync both ways
        self._sync(client_a, client_b)
        self._sync(client_b, client_a)

        # Both should converge to the same state (last-writer-wins for YText)
        cells_a_final = json.loads(str(client_a.ycells))
        cells_b_final = json.loads(str(client_b.ycells))
        self.assertEqual(cells_a_final[0]["source"], cells_b_final[0]["source"])


class TestNotebookRoundTrip(unittest.TestCase):
    """Prove that notebook content round-trips through YDoc without loss."""

    def test_cell_identity_preserved(self):
        nb = YNotebook()
        nb.append_cell(
            {"cell_type": "code", "source": "import os", "metadata": {}, "outputs": []},
        )
        nb.append_cell(
            {"cell_type": "markdown", "source": "# Title", "metadata": {}},
        )

        cells = json.loads(str(nb.ycells))
        self.assertEqual(len(cells), 2)
        self.assertEqual(cells[0]["cell_type"], "code")
        self.assertEqual(cells[0]["source"], "import os")
        self.assertEqual(cells[1]["cell_type"], "markdown")
        self.assertEqual(cells[1]["source"], "# Title")

        # Each cell has a unique id
        ids = [c["id"] for c in cells]
        self.assertEqual(len(set(ids)), 2)

    def test_cell_outputs_are_preserved(self):
        nb = YNotebook()
        nb.append_cell({
            "cell_type": "code",
            "source": "print('hello')",
            "metadata": {},
            "outputs": [{"output_type": "stream", "name": "stdout", "text": "hello\n"}],
        })

        cells = json.loads(str(nb.ycells))
        self.assertEqual(len(cells[0]["outputs"]), 1)
        self.assertEqual(cells[0]["outputs"][0]["text"], "hello\n")


class TestAwarenessMapping(unittest.TestCase):
    """Prove that session identity can be mapped to YDoc Awareness."""

    def test_awareness_tracks_client_identity(self):
        doc = pycrdt.Doc()
        awareness = pycrdt.Awareness(doc)
        awareness.set_local_state({
            "session_id": "sess-1",
            "actor": "human",
            "activity": "editing",
            "cell_id": "cell-abc",
        })
        local = awareness.get_local_state()
        self.assertEqual(local["session_id"], "sess-1")
        self.assertEqual(local["actor"], "human")

    def test_two_awareness_instances_share_presence(self):
        doc_a = pycrdt.Doc()
        awareness_a = pycrdt.Awareness(doc_a)
        awareness_a.set_local_state({"session_id": "sess-a", "activity": "reading"})
        client_id_a = awareness_a.client_id

        doc_b = pycrdt.Doc()
        awareness_b = pycrdt.Awareness(doc_b)
        awareness_b.set_local_state({"session_id": "sess-b", "activity": "editing"})

        # Simulate awareness sync: apply A's awareness update to B
        update_a = awareness_a.encode_awareness_update([client_id_a])
        awareness_b.apply_awareness_update(update_a, "sync")

        # B should now see A's presence
        states = awareness_b.states
        self.assertIn(client_id_a, states)
        self.assertEqual(states[client_id_a]["session_id"], "sess-a")


class TestYDocSpikeRecommendation(unittest.TestCase):
    """Document the spike conclusion."""

    def test_go_no_go_recommendation(self):
        """This test documents the spike findings.

        GO recommendation: jupyter_ydoc + pycrdt provides:
        1. CRDT-based cell source editing that converges without locks
        2. Cell identity preservation through round-trips
        3. Awareness API that maps cleanly to session presence
        4. Output preservation (execution state stays server-owned)

        Key decisions for Piece 7:
        - Use jupyter_ydoc as the notebook document model
        - Use pycrdt Awareness for presence (replaces NotebookPresenceRecord)
        - Keep execution/outputs server-owned (outside the CRDT)
        - Cell leases can be removed once YDoc editing is in place
        - Branch/review flows are workflow features, not edit-conflict machinery
        """
        # This test always passes — it documents the recommendation.
        self.assertTrue(True)


if __name__ == "__main__":
    unittest.main()
