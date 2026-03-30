"""YDoc-backed notebook document service.

Manages notebook documents as CRDTs via jupyter_ydoc.YNotebook.
Replaces the custom cell-lease concurrency model with CRDT-based
collaborative editing while keeping execution/outputs server-owned.
"""
from __future__ import annotations

import json
import threading
from typing import Any

import pycrdt
from jupyter_ydoc import YNotebook


class YDocService:
    """Manage YDoc-backed notebook documents."""

    def __init__(self) -> None:
        self._documents: dict[str, YNotebook] = {}
        self._awareness: dict[str, pycrdt.Awareness] = {}
        self._lock = threading.Lock()

    def get_or_create(self, path: str) -> YNotebook:
        """Get or create a YNotebook for the given path."""
        with self._lock:
            if path not in self._documents:
                self._documents[path] = YNotebook()
                self._awareness[path] = pycrdt.Awareness(self._documents[path].ydoc)
            return self._documents[path]

    def awareness(self, path: str) -> pycrdt.Awareness | None:
        """Get the Awareness instance for a notebook, if it exists."""
        return self._awareness.get(path)

    def load_from_nbformat(self, path: str, nb_dict: dict[str, Any]) -> YNotebook:
        """Load a notebook from nbformat dict into YDoc."""
        ynb = self.get_or_create(path)
        for cell_data in nb_dict.get("cells", []):
            ynb.append_cell(cell_data)
        return ynb

    def get_cells(self, path: str) -> list[dict[str, Any]]:
        """Get the current cells from a YDoc notebook."""
        ynb = self._documents.get(path)
        if ynb is None:
            return []
        return json.loads(str(ynb.ycells))

    def set_cell_source(self, path: str, index: int, source: str) -> bool:
        """Update a cell's source via CRDT mutation."""
        ynb = self._documents.get(path)
        if ynb is None:
            return False
        cells = json.loads(str(ynb.ycells))
        if index < 0 or index >= len(cells):
            return False
        cell = cells[index]
        cell["source"] = source
        ynb.set_cell(index, cell)
        return True

    def append_cell(self, path: str, cell_data: dict[str, Any]) -> bool:
        """Append a cell via CRDT mutation."""
        ynb = self._documents.get(path)
        if ynb is None:
            return False
        ynb.append_cell(cell_data)
        return True

    def insert_cell(self, path: str, index: int, cell_data: dict[str, Any]) -> bool:
        """Insert a cell at the given index via CRDT mutation."""
        ynb = self._documents.get(path)
        if ynb is None:
            return False
        cell_count = len(ynb.ycells)
        if index < 0 or index > cell_count:
            return False
        if index == cell_count:
            ynb.append_cell(cell_data)
        else:
            ynb.append_cell(cell_data)
            ynb.ycells.move(cell_count, index)
        return True

    def remove_cell(self, path: str, index: int) -> bool:
        """Remove the cell at the given index via CRDT mutation."""
        ynb = self._documents.get(path)
        if ynb is None:
            return False
        if index < 0 or index >= len(ynb.ycells):
            return False
        ynb.ycells.pop(index)
        return True

    def move_cell(self, path: str, from_index: int, to_index: int) -> bool:
        """Move a cell from one position to another via CRDT mutation."""
        ynb = self._documents.get(path)
        if ynb is None:
            return False
        cell_count = len(ynb.ycells)
        if from_index < 0 or from_index >= cell_count:
            return False
        if to_index < 0 or to_index >= cell_count:
            return False
        if from_index == to_index:
            return True
        ynb.ycells.move(from_index, to_index)
        return True

    def get_update(self, path: str) -> bytes | None:
        """Get the current YDoc state as an update for syncing."""
        ynb = self._documents.get(path)
        if ynb is None:
            return None
        return ynb.ydoc.get_update()

    def apply_update(self, path: str, update: bytes) -> bool:
        """Apply a remote YDoc update."""
        ynb = self.get_or_create(path)
        ynb.ydoc.apply_update(update)
        return True

    def set_presence(
        self,
        path: str,
        *,
        session_id: str,
        actor: str,
        activity: str,
        cell_id: str | None = None,
    ) -> None:
        """Update session presence via YDoc Awareness."""
        awareness = self._awareness.get(path)
        if awareness is None:
            return
        awareness.set_local_state({
            "session_id": session_id,
            "actor": actor,
            "activity": activity,
            "cell_id": cell_id,
        })

    def get_presence(self, path: str) -> dict[int, dict[str, Any]]:
        """Get all presence states for a notebook."""
        awareness = self._awareness.get(path)
        if awareness is None:
            return {}
        return dict(awareness.states)

    def close(self, path: str) -> None:
        """Remove a notebook from the service."""
        with self._lock:
            self._documents.pop(path, None)
            self._awareness.pop(path, None)
