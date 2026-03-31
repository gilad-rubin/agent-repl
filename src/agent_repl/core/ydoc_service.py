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


def _extract_cell_id(cell: dict[str, Any]) -> str | None:
    """Extract the stable cell_id from cell metadata, if present."""
    metadata = cell.get("metadata") or {}
    custom = metadata.get("custom") or {}
    agent_repl = custom.get("agent-repl") or {}
    cell_id = agent_repl.get("cell_id")
    return cell_id if isinstance(cell_id, str) and cell_id else None


class YDocService:
    """Manage YDoc-backed notebook documents."""

    def __init__(self) -> None:
        self._documents: dict[str, YNotebook] = {}
        self._awareness: dict[str, pycrdt.Awareness] = {}
        self._owner_threads: dict[str, int] = {}
        self._pending_disposals: dict[int, list[tuple[YNotebook | None, pycrdt.Awareness | None]]] = {}
        self._versions: dict[str, int] = {}
        # Bidirectional cell_id <-> index mapping per path
        self._id_to_index: dict[str, dict[str, int]] = {}
        self._index_to_id: dict[str, dict[int, str]] = {}
        self._lock = threading.Lock()

    def _touch_version(self, path: str) -> int:
        next_version = self._versions.get(path, 0) + 1
        self._versions[path] = next_version
        return next_version

    def _flush_thread_disposals(self) -> None:
        """Drop any queued YDoc objects owned by the current thread."""
        thread_id = threading.get_ident()
        with self._lock:
            pending = self._pending_disposals.pop(thread_id, [])
        # Releasing the references on the owner thread avoids pycrdt's
        # cross-thread drop warnings for UndoManager-backed notebooks.
        for notebook, awareness in pending:
            del awareness
            del notebook

    def get_or_create(self, path: str) -> YNotebook:
        """Get or create a YNotebook for the given path."""
        self._flush_thread_disposals()
        with self._lock:
            if path not in self._documents:
                self._documents[path] = YNotebook()
                self._awareness[path] = pycrdt.Awareness(self._documents[path].ydoc)
                self._owner_threads[path] = threading.get_ident()
            return self._documents[path]

    def awareness(self, path: str) -> pycrdt.Awareness | None:
        """Get the Awareness instance for a notebook, if it exists."""
        return self._awareness.get(path)

    def load_from_nbformat(self, path: str, nb_dict: dict[str, Any]) -> YNotebook:
        """Load a notebook from nbformat dict into YDoc."""
        ynb = self.get_or_create(path)
        id_to_idx: dict[str, int] = {}
        idx_to_id: dict[int, str] = {}
        for index, cell_data in enumerate(nb_dict.get("cells", [])):
            ynb.append_cell(cell_data)
            cell_id = _extract_cell_id(cell_data)
            if cell_id:
                id_to_idx[cell_id] = index
                idx_to_id[index] = cell_id
        self._id_to_index[path] = id_to_idx
        self._index_to_id[path] = idx_to_id
        self._touch_version(path)
        return ynb

    def has_cells(self, path: str) -> bool:
        """Return True if the YDoc for this path already has cells loaded."""
        ynb = self._documents.get(path)
        if ynb is None:
            return False
        return len(ynb.ycells) > 0

    def _rebuild_id_map(self, path: str) -> None:
        """Rebuild bidirectional cell ID mapping from current YDoc state."""
        cells = self.get_cells(path)
        id_to_idx: dict[str, int] = {}
        idx_to_id: dict[int, str] = {}
        for index, cell in enumerate(cells):
            cell_id = _extract_cell_id(cell)
            if cell_id:
                id_to_idx[cell_id] = index
                idx_to_id[index] = cell_id
        self._id_to_index[path] = id_to_idx
        self._index_to_id[path] = idx_to_id

    def index_for_cell_id(self, path: str, cell_id: str) -> int | None:
        """Return the current index for a cell_id, or None if not found."""
        return self._id_to_index.get(path, {}).get(cell_id)

    def cell_id_at_index(self, path: str, index: int) -> str | None:
        """Return the cell_id at a given index, or None if not mapped."""
        return self._index_to_id.get(path, {}).get(index)

    def _resolve_index(self, path: str, *, index: int | None = None, cell_id: str | None = None) -> int | None:
        """Resolve a cell_id or index to a concrete index. cell_id takes priority."""
        if cell_id is not None:
            return self.index_for_cell_id(path, cell_id)
        return index

    def get_cells(self, path: str) -> list[dict[str, Any]]:
        """Get the current cells from a YDoc notebook."""
        ynb = self._documents.get(path)
        if ynb is None:
            return []
        return json.loads(str(ynb.ycells))

    def get_version(self, path: str) -> int:
        """Return the monotonic shared-model version for a notebook path."""
        return self._versions.get(path, 0)

    def get_snapshot(self, path: str) -> dict[str, Any]:
        """Return the current YDoc-backed cells plus a monotonic version."""
        return {
            "document_version": self.get_version(path),
            "cells": self.get_cells(path),
        }

    def set_cell_source(
        self, path: str, index: int | None = None, source: str = "", *, cell_id: str | None = None,
    ) -> bool:
        """Update a cell's source via CRDT mutation."""
        ynb = self._documents.get(path)
        if ynb is None:
            return False
        resolved = self._resolve_index(path, index=index, cell_id=cell_id)
        if resolved is None:
            return False
        cells = json.loads(str(ynb.ycells))
        if resolved < 0 or resolved >= len(cells):
            return False
        cell = cells[resolved]
        cell["source"] = source
        ynb.set_cell(resolved, cell)
        self._touch_version(path)
        return True

    def replace_cell(
        self, path: str, cell_data: dict[str, Any], index: int | None = None, *, cell_id: str | None = None,
    ) -> bool:
        """Replace the full cell payload at an index via CRDT mutation."""
        ynb = self._documents.get(path)
        if ynb is None:
            return False
        resolved = self._resolve_index(path, index=index, cell_id=cell_id)
        if resolved is None:
            return False
        cells = json.loads(str(ynb.ycells))
        if resolved < 0 or resolved >= len(cells):
            return False
        ynb.set_cell(resolved, cell_data)
        self._rebuild_id_map(path)
        self._touch_version(path)
        return True

    def append_cell(self, path: str, cell_data: dict[str, Any]) -> bool:
        """Append a cell via CRDT mutation."""
        ynb = self._documents.get(path)
        if ynb is None:
            return False
        ynb.append_cell(cell_data)
        self._touch_version(path)
        return True

    def change_cell_type(
        self,
        path: str,
        *,
        cell_type: str,
        source: str | None = None,
        index: int | None = None,
        cell_id: str | None = None,
    ) -> bool:
        """Change a cell's type while preserving its stable identity metadata."""
        ynb = self._documents.get(path)
        if ynb is None:
            return False
        resolved = self._resolve_index(path, index=index, cell_id=cell_id)
        if resolved is None:
            return False
        cells = json.loads(str(ynb.ycells))
        if resolved < 0 or resolved >= len(cells):
            return False
        cell = dict(cells[resolved])
        next_type = cell_type if cell_type in {"code", "markdown", "raw"} else "markdown"
        cell["cell_type"] = next_type
        if source is not None:
            cell["source"] = source
        if next_type == "code":
            cell["outputs"] = []
            cell["execution_count"] = None
        else:
            cell.pop("outputs", None)
            cell.pop("execution_count", None)
        ynb.set_cell(resolved, cell)
        self._rebuild_id_map(path)
        self._touch_version(path)
        return True

    def insert_cell(
        self, path: str, index: int, cell_data: dict[str, Any], *, cell_id: str | None = None,
    ) -> bool:
        """Insert a cell at the given index via CRDT mutation.

        If cell_id is provided, it overrides index (inserts before the cell with that ID).
        """
        ynb = self._documents.get(path)
        if ynb is None:
            return False
        if cell_id is not None:
            resolved = self.index_for_cell_id(path, cell_id)
            if resolved is None:
                return False
            index = resolved
        cell_count = len(ynb.ycells)
        if index < 0 or index > cell_count:
            return False
        if index == cell_count:
            ynb.append_cell(cell_data)
        else:
            ynb.append_cell(cell_data)
            ynb.ycells.move(cell_count, index)
        self._rebuild_id_map(path)
        self._touch_version(path)
        return True

    def remove_cell(
        self, path: str, index: int | None = None, *, cell_id: str | None = None,
    ) -> bool:
        """Remove the cell at the given index via CRDT mutation."""
        ynb = self._documents.get(path)
        if ynb is None:
            return False
        resolved = self._resolve_index(path, index=index, cell_id=cell_id)
        if resolved is None:
            return False
        if resolved < 0 or resolved >= len(ynb.ycells):
            return False
        ynb.ycells.pop(resolved)
        self._rebuild_id_map(path)
        self._touch_version(path)
        return True

    def move_cell(
        self, path: str, from_index: int | None = None, to_index: int | None = None,
        *, from_cell_id: str | None = None, to_cell_id: str | None = None,
    ) -> bool:
        """Move a cell from one position to another via CRDT mutation."""
        ynb = self._documents.get(path)
        if ynb is None:
            return False
        resolved_from = self._resolve_index(path, index=from_index, cell_id=from_cell_id)
        resolved_to = self._resolve_index(path, index=to_index, cell_id=to_cell_id)
        if resolved_from is None or resolved_to is None:
            return False
        cell_count = len(ynb.ycells)
        if resolved_from < 0 or resolved_from >= cell_count:
            return False
        if resolved_to < 0 or resolved_to >= cell_count:
            return False
        if resolved_from == resolved_to:
            return True
        ynb.ycells.move(resolved_from, resolved_to)
        self._rebuild_id_map(path)
        self._touch_version(path)
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
        self._touch_version(path)
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
            notebook = self._documents.pop(path, None)
            awareness = self._awareness.pop(path, None)
            owner_thread = self._owner_threads.pop(path, None)
            self._id_to_index.pop(path, None)
            self._index_to_id.pop(path, None)

        if notebook is None and awareness is None:
            return

        current_thread = threading.get_ident()
        if owner_thread is None or owner_thread == current_thread:
            return

        with self._lock:
            self._pending_disposals.setdefault(owner_thread, []).append((notebook, awareness))

    def close_all(self) -> None:
        """Remove every notebook from the service."""
        with self._lock:
            paths = list(self._documents.keys())
        for path in paths:
            self.close(path)
        self._flush_thread_disposals()
