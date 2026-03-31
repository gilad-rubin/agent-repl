from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

import nbformat

from agent_repl.core.server import CoreState


class TestNotebookTrustFlow(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.workspace_root = Path(self.tmpdir.name)
        self.runtime_dir = self.workspace_root / "runtime"
        self.runtime_dir.mkdir()
        self.trust_db = self.workspace_root / "trust" / "nbsignatures.db"
        self._old_trust_db = os.environ.get("AGENT_REPL_NOTEBOOK_TRUST_DB")
        os.environ["AGENT_REPL_NOTEBOOK_TRUST_DB"] = str(self.trust_db)

        self.notebook_path = self.workspace_root / "notebooks" / "trust-demo.ipynb"
        self.notebook_path.parent.mkdir(parents=True, exist_ok=True)
        notebook = nbformat.v4.new_notebook(
            cells=[
                nbformat.v4.new_code_cell(
                    source="from IPython.display import HTML\nHTML('<iframe srcdoc=\"<p>trusted iframe</p>\"></iframe>')",
                    outputs=[
                        nbformat.v4.new_output(
                            output_type="display_data",
                            data={
                                "text/plain": "<iframe fallback>",
                                "text/html": "<iframe srcdoc=\"<p>trusted iframe</p>\"></iframe>",
                            },
                            metadata={},
                        ),
                    ],
                ),
            ],
        )
        notebook.metadata["widgets"] = {
            "application/vnd.jupyter.widget-state+json": {
                "widget-model-1": {
                    "model_module": "@jupyter-widgets/controls",
                    "model_module_version": "1.5.0",
                    "model_name": "HTMLModel",
                    "state": {
                        "_model_module": "@jupyter-widgets/controls",
                        "_model_module_version": "1.5.0",
                        "_model_name": "HTMLModel",
                        "_view_module": "@jupyter-widgets/controls",
                        "_view_module_version": "1.5.0",
                        "_view_name": "HTMLView",
                        "value": "Widget payload",
                    },
                },
            },
        }
        nbformat.write(notebook, self.notebook_path)

        self.state = CoreState(
            workspace_root=str(self.workspace_root),
            runtime_dir=str(self.runtime_dir),
            token="tok",
            pid=1234,
            started_at=1.0,
        )

    def tearDown(self):
        self.state.shutdown_headless_runtimes()
        self.state._ydoc_service.close_all()
        if self._old_trust_db is None:
            os.environ.pop("AGENT_REPL_NOTEBOOK_TRUST_DB", None)
        else:
            os.environ["AGENT_REPL_NOTEBOOK_TRUST_DB"] = self._old_trust_db
        self.tmpdir.cleanup()

    def test_shared_model_reports_untrusted_cells_until_notebook_is_trusted(self):
        payload, status = self.state.notebook_shared_model("notebooks/trust-demo.ipynb")

        self.assertEqual(status.value, 200)
        self.assertFalse(payload["notebook_trusted"])
        self.assertEqual(payload["trusted_code_cells"], 0)
        self.assertEqual(payload["total_code_cells"], 1)
        self.assertIs(payload["cells"][0]["trusted"], False)
        self.assertIn("widgets", payload["notebook_metadata"])
        self.assertIn(
            "application/vnd.jupyter.widget-state+json",
            payload["notebook_metadata"]["widgets"],
        )

        trust_payload, trust_status = self.state.notebook_trust("notebooks/trust-demo.ipynb")

        self.assertEqual(trust_status.value, 200)
        self.assertTrue(trust_payload["notebook_trusted"])

        refreshed, refreshed_status = self.state.notebook_shared_model("notebooks/trust-demo.ipynb")
        self.assertEqual(refreshed_status.value, 200)
        self.assertTrue(refreshed["notebook_trusted"])
        self.assertEqual(refreshed["trusted_code_cells"], 1)
        self.assertIs(refreshed["cells"][0]["trusted"], True)

    def test_source_edits_invalidate_trust_until_retrusted(self):
        trust_payload, trust_status = self.state.notebook_trust("notebooks/trust-demo.ipynb")
        self.assertEqual(trust_status.value, 200)
        self.assertTrue(trust_payload["notebook_trusted"])

        edit_payload, edit_status = self.state.notebook_edit(
            "notebooks/trust-demo.ipynb",
            [{"op": "replace-source", "cell_index": 0, "source": "print('changed')"}],
        )
        self.assertEqual(edit_status.value, 200)
        self.assertEqual(edit_payload["path"], "notebooks/trust-demo.ipynb")

        refreshed, refreshed_status = self.state.notebook_shared_model("notebooks/trust-demo.ipynb")
        self.assertEqual(refreshed_status.value, 200)
        self.assertFalse(refreshed["notebook_trusted"])
        self.assertEqual(refreshed["trusted_code_cells"], 0)
        self.assertIs(refreshed["cells"][0]["trusted"], False)
