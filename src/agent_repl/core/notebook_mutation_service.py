"""Mutation-focused notebook helpers for the core daemon."""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import nbformat


class NotebookMutationService:
    """Own the private notebook creation, edit, and projection flows."""

    def __init__(self, state: Any):
        self.state = state

    def create_notebook_cells(self, cells: list[dict[str, Any]] | None) -> list[Any]:
        notebook_cells: list[Any] = []
        for index, cell in enumerate(cells or []):
            cell_type = "code" if cell.get("type") == "code" else "markdown"
            source = cell.get("source", "")
            if cell_type == "code":
                notebook_cell = nbformat.v4.new_code_cell(source=source)
            else:
                notebook_cell = nbformat.v4.new_markdown_cell(source=source)
            self.state._ensure_cell_identity(notebook_cell, index)
            notebook_cells.append(notebook_cell)
        return notebook_cells

    def create(
        self,
        real_path: str,
        relative_path: str,
        *,
        cells: list[dict[str, Any]] | None,
        kernel_id: str | None,
    ) -> dict[str, Any]:
        python_path = self.state._resolve_python_path(kernel_id)
        runtime = self.state._ensure_headless_runtime(real_path, python_path)
        notebook = nbformat.v4.new_notebook(cells=self.create_notebook_cells(cells))
        notebook.metadata["kernelspec"] = {
            "display_name": f"{Path(python_path).parent.parent.name or Path(python_path).name}",
            "language": "python",
            "name": "python3",
        }
        self.state._save_notebook(real_path, notebook)
        runtime.last_used_at = time.time()
        return {
            "status": "ok",
            "path": relative_path,
            "kernel_status": "selected",
            "ready": True,
            "kernel": {
                "id": python_path,
                "label": Path(python_path).name,
                "python": python_path,
                "type": "headless",
            },
            "message": f"Selected kernel: {python_path}",
            "mode": "headless",
        }

    def project_visible(
        self,
        real_path: str,
        relative_path: str,
        *,
        cells: list[dict[str, Any]],
        owner_session_id: str | None = None,
    ) -> dict[str, Any]:
        notebook, _ = self.state._load_notebook(real_path)
        self.state._assert_structure_not_leased(
            relative_path=relative_path,
            owner_session_id=owner_session_id,
            operation="project-visible-notebook",
        )
        existing_by_id = {
            self.state._cell_id(cell, index): cell
            for index, cell in enumerate(notebook.cells)
        }
        incoming_ids = {self.state._incoming_cell_id(payload) for payload in cells if self.state._incoming_cell_id(payload)}
        for existing_id in existing_by_id:
            if existing_id not in incoming_ids:
                self.state._assert_cell_not_leased(
                    relative_path=relative_path,
                    cell_id=existing_id,
                    owner_session_id=owner_session_id,
                    operation="project-visible-notebook",
                )
        for incoming_id in incoming_ids:
            self.state._assert_cell_not_leased(
                relative_path=relative_path,
                cell_id=incoming_id,
                owner_session_id=owner_session_id,
                operation="project-visible-notebook",
            )
            if owner_session_id is not None:
                self.state.acquire_cell_lease(
                    session_id=owner_session_id,
                    path=relative_path,
                    cell_id=incoming_id,
                    kind="edit",
                )
        notebook.cells = [
            self.state._materialize_visible_cell(payload, existing_by_id)
            for payload in cells
        ]
        for index, cell in enumerate(notebook.cells):
            self.state._ensure_cell_identity(cell, index)
        self.state._save_notebook(real_path, notebook)
        runtime_id = self._selected_runtime_id(relative_path)
        self.state._append_activity_event(
            path=relative_path,
            event_type="notebook-projected",
            detail=f"Projected {len(notebook.cells)} visible cells",
            runtime_id=runtime_id,
            session_id=owner_session_id,
            actor=self.state._session_actor(owner_session_id, "human"),
        )
        self.state._append_activity_event(
            path=relative_path,
            event_type="notebook-reset-needed",
            detail="Visible projection changed notebook structure",
            runtime_id=runtime_id,
            session_id=owner_session_id,
            actor=self.state._session_actor(owner_session_id, "human"),
        )
        return {
            "status": "ok",
            "path": relative_path,
            "cell_count": len(notebook.cells),
            "mode": "headless",
        }

    def edit(
        self,
        real_path: str,
        relative_path: str,
        operations: list[dict[str, Any]],
        *,
        owner_session_id: str | None = None,
    ) -> dict[str, Any]:
        notebook, changed = self.state._load_notebook(real_path)
        results: list[dict[str, Any]] = []
        actor = self.state._session_actor(owner_session_id, "agent")
        runtime_id = self._selected_runtime_id(relative_path)
        for op in operations:
            command = op.get("op")
            if command == "replace-source":
                index = self.state._find_cell_index(notebook, cell_id=op.get("cell_id"), cell_index=op.get("cell_index"))
                cell = notebook.cells[index]
                stable_cell_id = self.state._cell_id(cell, index)
                self.state._assert_cell_not_leased(
                    relative_path=relative_path,
                    cell_id=stable_cell_id,
                    owner_session_id=owner_session_id,
                    operation="replace-source",
                )
                cell.source = op.get("source", "")
                if cell.cell_type == "code":
                    cell.outputs = []
                    cell.execution_count = None
                    self.state._clear_cell_runtime_provenance(cell)
                results.append({"op": "replace-source", "changed": True, "cell_id": stable_cell_id, "cell_count": len(notebook.cells)})
                self.state._append_activity_event(
                    path=relative_path,
                    event_type="cell-source-updated",
                    detail=f"Updated source for cell {index + 1}",
                    actor=actor,
                    session_id=owner_session_id,
                    runtime_id=runtime_id,
                    cell_id=stable_cell_id,
                    cell_index=index,
                    data={"cell": self.state._cell_payload(cell, index)},
                )
                changed = True
            elif command == "insert":
                self.state._assert_structure_not_leased(
                    relative_path=relative_path,
                    owner_session_id=owner_session_id,
                    operation="insert",
                )
                index = self.state._normalize_insert_index(notebook, op.get("at_index", -1))
                cell_type = op.get("cell_type", "code")
                source = op.get("source", "")
                cell = nbformat.v4.new_code_cell(source=source) if cell_type == "code" else nbformat.v4.new_markdown_cell(source=source)
                notebook.cells.insert(index, cell)
                for position, current in enumerate(notebook.cells):
                    self.state._ensure_cell_identity(current, position)
                inserted_cell = notebook.cells[index]
                results.append({"op": "insert", "changed": True, "cell_id": self.state._cell_id(inserted_cell, index), "cell_count": len(notebook.cells)})
                self.state._append_activity_event(
                    path=relative_path,
                    event_type="cell-inserted",
                    detail=f"Inserted {cell_type} cell at index {index}",
                    actor=actor,
                    session_id=owner_session_id,
                    runtime_id=runtime_id,
                    cell_id=self.state._cell_id(inserted_cell, index),
                    cell_index=index,
                    data={"cell": self.state._cell_payload(inserted_cell, index)},
                )
                changed = True
            elif command == "delete":
                index = self.state._find_cell_index(notebook, cell_id=op.get("cell_id"), cell_index=op.get("cell_index"))
                cell = notebook.cells[index]
                stable_cell_id = self.state._cell_id(cell, index)
                self.state._assert_structure_not_leased(
                    relative_path=relative_path,
                    owner_session_id=owner_session_id,
                    operation="delete",
                )
                self.state._assert_cell_not_leased(
                    relative_path=relative_path,
                    cell_id=stable_cell_id,
                    owner_session_id=owner_session_id,
                    operation="delete",
                )
                notebook.cells.pop(index)
                for position, current in enumerate(notebook.cells):
                    self.state._ensure_cell_identity(current, position)
                self.state.cell_leases.pop(self.state._lease_key(relative_path, stable_cell_id), None)
                results.append({"op": "delete", "changed": True, "cell_id": stable_cell_id, "cell_count": len(notebook.cells)})
                self.state._append_activity_event(
                    path=relative_path,
                    event_type="cell-removed",
                    detail=f"Removed cell at index {index}",
                    actor=actor,
                    session_id=owner_session_id,
                    runtime_id=runtime_id,
                    cell_id=stable_cell_id,
                    cell_index=index,
                    data={"cell_id": stable_cell_id},
                )
                changed = True
            elif command == "move":
                index = self.state._find_cell_index(notebook, cell_id=op.get("cell_id"), cell_index=op.get("cell_index"))
                to_index = int(op.get("to_index", index))
                if to_index == -1:
                    to_index = len(notebook.cells) - 1
                to_index = max(0, min(to_index, len(notebook.cells) - 1))
                cell = notebook.cells[index]
                stable_cell_id = self.state._cell_id(cell, index)
                self.state._assert_structure_not_leased(
                    relative_path=relative_path,
                    owner_session_id=owner_session_id,
                    operation="move",
                )
                self.state._assert_cell_not_leased(
                    relative_path=relative_path,
                    cell_id=stable_cell_id,
                    owner_session_id=owner_session_id,
                    operation="move",
                )
                cell = notebook.cells.pop(index)
                notebook.cells.insert(to_index, cell)
                for position, current in enumerate(notebook.cells):
                    self.state._ensure_cell_identity(current, position)
                results.append({"op": "move", "changed": True, "cell_id": self.state._cell_id(cell, to_index), "cell_count": len(notebook.cells)})
                self.state._append_activity_event(
                    path=relative_path,
                    event_type="notebook-reset-needed",
                    detail=f"Moved cell from index {index} to {to_index}",
                    actor=actor,
                    session_id=owner_session_id,
                    runtime_id=runtime_id,
                    cell_id=self.state._cell_id(cell, to_index),
                    cell_index=to_index,
                )
                changed = True
            elif command == "clear-outputs":
                if op.get("all"):
                    for index, cell in enumerate(notebook.cells):
                        if cell.cell_type == "code":
                            self.state._assert_cell_not_leased(
                                relative_path=relative_path,
                                cell_id=self.state._cell_id(cell, index),
                                owner_session_id=owner_session_id,
                                operation="clear-outputs",
                            )
                            cell.outputs = []
                            cell.execution_count = None
                            self.state._clear_cell_runtime_provenance(cell)
                            self.state._append_activity_event(
                                path=relative_path,
                                event_type="cell-outputs-updated",
                                detail=f"Cleared outputs for cell {index + 1}",
                                actor=actor,
                                session_id=owner_session_id,
                                runtime_id=runtime_id,
                                cell_id=self.state._cell_id(cell, index),
                                cell_index=index,
                                data={"cell": self.state._cell_payload(cell, index)},
                            )
                    results.append({"op": "clear-outputs", "changed": True, "cell_count": len(notebook.cells)})
                    changed = True
                else:
                    index = self.state._find_cell_index(notebook, cell_id=op.get("cell_id"), cell_index=op.get("cell_index"))
                    cell = notebook.cells[index]
                    if cell.cell_type == "code":
                        self.state._assert_cell_not_leased(
                            relative_path=relative_path,
                            cell_id=self.state._cell_id(cell, index),
                            owner_session_id=owner_session_id,
                            operation="clear-outputs",
                        )
                        cell.outputs = []
                        cell.execution_count = None
                        self.state._clear_cell_runtime_provenance(cell)
                    stable_cell_id = self.state._cell_id(cell, index)
                    self.state._append_activity_event(
                        path=relative_path,
                        event_type="cell-outputs-updated",
                        detail=f"Cleared outputs for cell {index + 1}",
                        actor=actor,
                        session_id=owner_session_id,
                        runtime_id=runtime_id,
                        cell_id=stable_cell_id,
                        cell_index=index,
                        data={"cell": self.state._cell_payload(cell, index)},
                    )
                    results.append({"op": "clear-outputs", "changed": True, "cell_id": stable_cell_id, "cell_count": len(notebook.cells)})
                    changed = True
            else:
                raise RuntimeError(f"Unsupported headless edit operation: {command}")
        if changed:
            self.state._save_notebook(real_path, notebook)
        return {"path": relative_path, "results": results}

    def _selected_runtime_id(self, relative_path: str) -> str | None:
        record = self.state._selected_runtime_record_for_notebook(relative_path)
        return record.runtime_id if record is not None else None
