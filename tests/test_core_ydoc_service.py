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

    def test_snapshot_exposes_cells_and_monotonic_version(self):
        svc = YDocService()
        svc.load_from_nbformat("demo.ipynb", {
            "cells": [
                {"cell_type": "code", "source": "x = 1", "metadata": {}, "outputs": []},
            ],
        })

        first = svc.get_snapshot("demo.ipynb")
        self.assertEqual(first["document_version"], 1)
        self.assertEqual(first["cells"][0]["source"], "x = 1")

        svc.set_cell_source("demo.ipynb", 0, "x = 2")
        second = svc.get_snapshot("demo.ipynb")
        self.assertEqual(second["document_version"], 2)
        self.assertEqual(second["cells"][0]["source"], "x = 2")


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

    def test_replace_cell(self):
        self.assertTrue(self.svc.replace_cell("nb.ipynb", {
            "cell_type": "markdown",
            "source": "# changed",
            "metadata": {"custom": {"agent-repl": {"cell_id": "reused-id"}}},
        }, 0))
        cells = self.svc.get_cells("nb.ipynb")
        self.assertEqual(cells[0]["cell_type"], "markdown")
        self.assertEqual(cells[0]["source"], "# changed")

    def test_set_cell_source_out_of_range(self):
        self.assertFalse(self.svc.set_cell_source("nb.ipynb", 99, "nope"))

    def test_change_cell_type(self):
        self.assertTrue(self.svc.change_cell_type("nb.ipynb", cell_type="markdown", index=0, source="# heading"))
        cells = self.svc.get_cells("nb.ipynb")
        self.assertEqual(cells[0]["cell_type"], "markdown")
        self.assertEqual(cells[0]["source"], "# heading")
        self.assertNotIn("outputs", cells[0])


class TestYDocServiceInsert(unittest.TestCase):
    def setUp(self):
        self.svc = YDocService()
        self.svc.load_from_nbformat("nb.ipynb", {
            "cells": [
                {"cell_type": "code", "source": "a", "metadata": {}, "outputs": []},
                {"cell_type": "code", "source": "c", "metadata": {}, "outputs": []},
            ],
        })

    def test_insert_at_middle(self):
        self.assertTrue(self.svc.insert_cell("nb.ipynb", 1, {
            "cell_type": "code", "source": "b", "metadata": {}, "outputs": [],
        }))
        cells = self.svc.get_cells("nb.ipynb")
        self.assertEqual([c["source"] for c in cells], ["a", "b", "c"])

    def test_insert_at_beginning(self):
        self.assertTrue(self.svc.insert_cell("nb.ipynb", 0, {
            "cell_type": "code", "source": "z", "metadata": {}, "outputs": [],
        }))
        cells = self.svc.get_cells("nb.ipynb")
        self.assertEqual(cells[0]["source"], "z")
        self.assertEqual(len(cells), 3)

    def test_insert_at_end(self):
        self.assertTrue(self.svc.insert_cell("nb.ipynb", 2, {
            "cell_type": "code", "source": "d", "metadata": {}, "outputs": [],
        }))
        cells = self.svc.get_cells("nb.ipynb")
        self.assertEqual(cells[-1]["source"], "d")
        self.assertEqual(len(cells), 3)

    def test_insert_out_of_range(self):
        self.assertFalse(self.svc.insert_cell("nb.ipynb", 99, {
            "cell_type": "code", "source": "x", "metadata": {}, "outputs": [],
        }))
        self.assertFalse(self.svc.insert_cell("nb.ipynb", -1, {
            "cell_type": "code", "source": "x", "metadata": {}, "outputs": [],
        }))

    def test_insert_unknown_path(self):
        self.assertFalse(self.svc.insert_cell("nope.ipynb", 0, {
            "cell_type": "code", "source": "x", "metadata": {}, "outputs": [],
        }))


class TestYDocServiceRemove(unittest.TestCase):
    def setUp(self):
        self.svc = YDocService()
        self.svc.load_from_nbformat("nb.ipynb", {
            "cells": [
                {"cell_type": "code", "source": "a", "metadata": {}, "outputs": []},
                {"cell_type": "code", "source": "b", "metadata": {}, "outputs": []},
                {"cell_type": "code", "source": "c", "metadata": {}, "outputs": []},
            ],
        })

    def test_remove_middle(self):
        self.assertTrue(self.svc.remove_cell("nb.ipynb", 1))
        cells = self.svc.get_cells("nb.ipynb")
        self.assertEqual([c["source"] for c in cells], ["a", "c"])

    def test_remove_first(self):
        self.assertTrue(self.svc.remove_cell("nb.ipynb", 0))
        cells = self.svc.get_cells("nb.ipynb")
        self.assertEqual(cells[0]["source"], "b")

    def test_remove_last(self):
        self.assertTrue(self.svc.remove_cell("nb.ipynb", 2))
        cells = self.svc.get_cells("nb.ipynb")
        self.assertEqual(len(cells), 2)
        self.assertEqual(cells[-1]["source"], "b")

    def test_remove_out_of_range(self):
        self.assertFalse(self.svc.remove_cell("nb.ipynb", 99))
        self.assertFalse(self.svc.remove_cell("nb.ipynb", -1))

    def test_remove_unknown_path(self):
        self.assertFalse(self.svc.remove_cell("nope.ipynb", 0))


class TestYDocServiceMove(unittest.TestCase):
    def setUp(self):
        self.svc = YDocService()
        self.svc.load_from_nbformat("nb.ipynb", {
            "cells": [
                {"cell_type": "code", "source": "a", "metadata": {}, "outputs": []},
                {"cell_type": "code", "source": "b", "metadata": {}, "outputs": []},
                {"cell_type": "code", "source": "c", "metadata": {}, "outputs": []},
            ],
        })

    def test_move_forward(self):
        self.assertTrue(self.svc.move_cell("nb.ipynb", 0, 2))
        cells = self.svc.get_cells("nb.ipynb")
        self.assertEqual([c["source"] for c in cells], ["b", "a", "c"])

    def test_move_backward(self):
        self.assertTrue(self.svc.move_cell("nb.ipynb", 2, 0))
        cells = self.svc.get_cells("nb.ipynb")
        self.assertEqual([c["source"] for c in cells], ["c", "a", "b"])

    def test_move_same_position(self):
        self.assertTrue(self.svc.move_cell("nb.ipynb", 1, 1))
        cells = self.svc.get_cells("nb.ipynb")
        self.assertEqual([c["source"] for c in cells], ["a", "b", "c"])

    def test_move_out_of_range(self):
        self.assertFalse(self.svc.move_cell("nb.ipynb", 0, 99))
        self.assertFalse(self.svc.move_cell("nb.ipynb", 99, 0))
        self.assertFalse(self.svc.move_cell("nb.ipynb", -1, 0))

    def test_move_unknown_path(self):
        self.assertFalse(self.svc.move_cell("nope.ipynb", 0, 1))


def _cell_with_id(source: str, cell_id: str) -> dict:
    """Helper to create a cell dict with a stable agent-repl cell_id."""
    return {
        "cell_type": "code",
        "source": source,
        "metadata": {"custom": {"agent-repl": {"cell_id": cell_id}}},
        "outputs": [],
    }


class TestYDocServiceCellIdMapping(unittest.TestCase):
    def setUp(self):
        self.svc = YDocService()
        self.svc.load_from_nbformat("nb.ipynb", {
            "cells": [
                _cell_with_id("a", "id-a"),
                _cell_with_id("b", "id-b"),
                _cell_with_id("c", "id-c"),
            ],
        })

    def test_index_for_cell_id(self):
        self.assertEqual(self.svc.index_for_cell_id("nb.ipynb", "id-a"), 0)
        self.assertEqual(self.svc.index_for_cell_id("nb.ipynb", "id-b"), 1)
        self.assertEqual(self.svc.index_for_cell_id("nb.ipynb", "id-c"), 2)

    def test_cell_id_at_index(self):
        self.assertEqual(self.svc.cell_id_at_index("nb.ipynb", 0), "id-a")
        self.assertEqual(self.svc.cell_id_at_index("nb.ipynb", 1), "id-b")
        self.assertEqual(self.svc.cell_id_at_index("nb.ipynb", 2), "id-c")

    def test_unknown_cell_id_returns_none(self):
        self.assertIsNone(self.svc.index_for_cell_id("nb.ipynb", "id-missing"))

    def test_unknown_index_returns_none(self):
        self.assertIsNone(self.svc.cell_id_at_index("nb.ipynb", 99))

    def test_set_cell_source_by_cell_id(self):
        self.assertTrue(self.svc.set_cell_source("nb.ipynb", cell_id="id-b", source="modified"))
        cells = self.svc.get_cells("nb.ipynb")
        self.assertEqual(cells[1]["source"], "modified")

    def test_change_cell_type_by_cell_id_preserves_id_mapping(self):
        self.assertTrue(self.svc.change_cell_type("nb.ipynb", cell_id="id-b", cell_type="markdown", source="# heading"))
        cells = self.svc.get_cells("nb.ipynb")
        self.assertEqual(cells[1]["cell_type"], "markdown")
        self.assertEqual(cells[1]["source"], "# heading")
        self.assertEqual(self.svc.index_for_cell_id("nb.ipynb", "id-b"), 1)

    def test_change_cell_type_to_code_clears_outputs(self):
        svc = YDocService()
        svc.load_from_nbformat("nb3.ipynb", {
            "cells": [
                {
                    "cell_type": "markdown",
                    "source": "# markdown",
                    "metadata": {"custom": {"agent-repl": {"cell_id": "id-md"}}},
                },
            ],
        })
        self.assertTrue(svc.change_cell_type("nb3.ipynb", cell_id="id-md", cell_type="code", source="print(1)"))
        cells = svc.get_cells("nb3.ipynb")
        self.assertEqual(cells[0]["cell_type"], "code")
        self.assertEqual(cells[0]["source"], "print(1)")
        self.assertEqual(cells[0]["outputs"], [])
        self.assertIsNone(cells[0]["execution_count"])

    def test_remove_cell_by_cell_id(self):
        self.assertTrue(self.svc.remove_cell("nb.ipynb", cell_id="id-b"))
        cells = self.svc.get_cells("nb.ipynb")
        self.assertEqual([c["source"] for c in cells], ["a", "c"])

    def test_move_cell_by_cell_id(self):
        self.assertTrue(self.svc.move_cell("nb.ipynb", from_cell_id="id-a", to_cell_id="id-c"))
        cells = self.svc.get_cells("nb.ipynb")
        self.assertEqual([c["source"] for c in cells], ["b", "a", "c"])

    def test_insert_before_cell_id(self):
        self.assertTrue(self.svc.insert_cell("nb.ipynb", 0, _cell_with_id("x", "id-x"), cell_id="id-b"))
        cells = self.svc.get_cells("nb.ipynb")
        self.assertEqual([c["source"] for c in cells], ["a", "x", "b", "c"])

    def test_ids_stay_consistent_through_insert_remove_move(self):
        # Insert a new cell before "b"
        self.svc.insert_cell("nb.ipynb", 0, _cell_with_id("x", "id-x"), cell_id="id-b")
        # Now: a, x, b, c
        self.assertEqual(self.svc.index_for_cell_id("nb.ipynb", "id-x"), 1)
        self.assertEqual(self.svc.index_for_cell_id("nb.ipynb", "id-b"), 2)

        # Remove "a" (index 0)
        self.svc.remove_cell("nb.ipynb", cell_id="id-a")
        # Now: x, b, c
        self.assertEqual(self.svc.index_for_cell_id("nb.ipynb", "id-x"), 0)
        self.assertEqual(self.svc.index_for_cell_id("nb.ipynb", "id-b"), 1)
        self.assertEqual(self.svc.index_for_cell_id("nb.ipynb", "id-c"), 2)
        self.assertIsNone(self.svc.index_for_cell_id("nb.ipynb", "id-a"))

        # Move "c" to front
        self.svc.move_cell("nb.ipynb", from_cell_id="id-c", to_index=0)
        # Now: c, x, b
        self.assertEqual(self.svc.index_for_cell_id("nb.ipynb", "id-c"), 0)
        self.assertEqual(self.svc.index_for_cell_id("nb.ipynb", "id-x"), 1)
        self.assertEqual(self.svc.index_for_cell_id("nb.ipynb", "id-b"), 2)

    def test_cells_without_ids_not_in_mapping(self):
        svc = YDocService()
        svc.load_from_nbformat("nb2.ipynb", {
            "cells": [
                {"cell_type": "code", "source": "bare", "metadata": {}, "outputs": []},
                _cell_with_id("tagged", "id-t"),
            ],
        })
        self.assertIsNone(svc.cell_id_at_index("nb2.ipynb", 0))
        self.assertEqual(svc.cell_id_at_index("nb2.ipynb", 1), "id-t")
        self.assertEqual(svc.index_for_cell_id("nb2.ipynb", "id-t"), 1)

    def test_close_clears_id_mapping(self):
        self.svc.close("nb.ipynb")
        self.assertIsNone(self.svc.index_for_cell_id("nb.ipynb", "id-a"))
        self.assertIsNone(self.svc.cell_id_at_index("nb.ipynb", 0))


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
