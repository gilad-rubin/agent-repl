"""Tests for the YDoc document service."""
from __future__ import annotations

import unittest

from agent_repl.core.ydoc_service import YDocService


class TestYDocServiceBasics(unittest.TestCase):
    def test_get_or_create_returns_same_document(self):
        svc = YDocService()
        nb1 = svc.get_or_create("demo.ipynb")
        nb2 = svc.get_or_create("demo.ipynb")
        self.assertIs(nb1, nb2)

    def test_load_from_nbformat_populates_cells(self):
        svc = YDocService()
        svc.load_from_nbformat("demo.ipynb", {
            "cells": [
                {"cell_type": "code", "source": "x = 1", "metadata": {}, "outputs": []},
                {"cell_type": "markdown", "source": "# Hello", "metadata": {}},
            ],
        })
        cells = svc.get_cells("demo.ipynb")
        self.assertEqual(len(cells), 2)
        self.assertEqual(cells[0]["source"], "x = 1")
        self.assertEqual(cells[1]["source"], "# Hello")


class TestYDocServiceEditing(unittest.TestCase):
    def setUp(self):
        self.svc = YDocService()
        self.svc.load_from_nbformat("nb.ipynb", {
            "cells": [
                {"cell_type": "code", "source": "original", "metadata": {}, "outputs": []},
            ],
        })

    def test_set_cell_source(self):
        self.assertTrue(self.svc.set_cell_source("nb.ipynb", 0, "modified"))
        cells = self.svc.get_cells("nb.ipynb")
        self.assertEqual(cells[0]["source"], "modified")

    def test_append_cell(self):
        self.assertTrue(self.svc.append_cell("nb.ipynb", {
            "cell_type": "code", "source": "y = 2", "metadata": {}, "outputs": [],
        }))
        cells = self.svc.get_cells("nb.ipynb")
        self.assertEqual(len(cells), 2)
        self.assertEqual(cells[1]["source"], "y = 2")

    def test_set_cell_source_out_of_range(self):
        self.assertFalse(self.svc.set_cell_source("nb.ipynb", 99, "nope"))


class TestYDocServiceSync(unittest.TestCase):
    def test_get_update_and_apply(self):
        svc_a = YDocService()
        svc_a.load_from_nbformat("nb.ipynb", {
            "cells": [
                {"cell_type": "code", "source": "hello", "metadata": {}, "outputs": []},
            ],
        })
        update = svc_a.get_update("nb.ipynb")
        self.assertIsNotNone(update)

        svc_b = YDocService()
        svc_b.apply_update("nb.ipynb", update)
        cells = svc_b.get_cells("nb.ipynb")
        self.assertEqual(len(cells), 1)
        self.assertEqual(cells[0]["source"], "hello")


class TestYDocServicePresence(unittest.TestCase):
    def test_set_and_get_presence(self):
        svc = YDocService()
        svc.get_or_create("nb.ipynb")
        svc.set_presence(
            "nb.ipynb",
            session_id="sess-1",
            actor="human",
            activity="editing",
            cell_id="cell-1",
        )
        presence = svc.get_presence("nb.ipynb")
        # At least one client should have the presence state
        found = any(
            state.get("session_id") == "sess-1"
            for state in presence.values()
        )
        self.assertTrue(found)


class TestYDocServiceLifecycle(unittest.TestCase):
    def test_close_removes_document(self):
        svc = YDocService()
        svc.get_or_create("nb.ipynb")
        svc.close("nb.ipynb")
        self.assertEqual(svc.get_cells("nb.ipynb"), [])
        self.assertIsNone(svc.awareness("nb.ipynb"))


if __name__ == "__main__":
    unittest.main()
