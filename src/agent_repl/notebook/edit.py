"""Edit operations and batch edit."""
from __future__ import annotations

from typing import Any

from agent_repl.core import CommandError, ServerInfo, DEFAULT_TIMEOUT
from agent_repl.notebook.cells import apply_clear_outputs, apply_delete, apply_insert, apply_move, apply_replace_source
from agent_repl.notebook.io import load_notebook_model, save_notebook_content


def edit_cell_source(server: ServerInfo, *, path: str, index: int | None, cell_id: str | None, source: str, timeout: float = DEFAULT_TIMEOUT) -> dict[str, Any]:
    model = load_notebook_model(server, path, timeout=timeout)
    cells = model["content"]["cells"]
    result = apply_replace_source(cells, index=index, cell_id=cell_id, source=source)
    if result["changed"]:
        save_notebook_content(server, path, model["content"], timeout=timeout, expected_last_modified=model.get("last_modified"))
    return {"path": path, "operation": "replace-source", **result}


def insert_cell(server: ServerInfo, *, path: str, cell_type: str, source: str, at_index: int, timeout: float = DEFAULT_TIMEOUT) -> dict[str, Any]:
    model = load_notebook_model(server, path, timeout=timeout)
    result = apply_insert(model["content"]["cells"], cell_type=cell_type, source=source, at_index=at_index)
    save_notebook_content(server, path, model["content"], timeout=timeout, expected_last_modified=model.get("last_modified"))
    return {"path": path, "operation": "insert-cell", **result}


def delete_cell(server: ServerInfo, *, path: str, index: int | None, cell_id: str | None, timeout: float = DEFAULT_TIMEOUT) -> dict[str, Any]:
    model = load_notebook_model(server, path, timeout=timeout)
    result = apply_delete(model["content"]["cells"], index=index, cell_id=cell_id)
    save_notebook_content(server, path, model["content"], timeout=timeout, expected_last_modified=model.get("last_modified"))
    return {"path": path, "operation": "delete-cell", **result}


def move_cell(server: ServerInfo, *, path: str, index: int | None, cell_id: str | None, to_index: int, timeout: float = DEFAULT_TIMEOUT) -> dict[str, Any]:
    model = load_notebook_model(server, path, timeout=timeout)
    result = apply_move(model["content"]["cells"], index=index, cell_id=cell_id, to_index=to_index)
    if result["changed"]:
        save_notebook_content(server, path, model["content"], timeout=timeout, expected_last_modified=model.get("last_modified"))
    return {"path": path, "operation": "move-cell", **result}


def clear_cell_outputs(server: ServerInfo, *, path: str, index: int | None, cell_id: str | None, all_cells: bool, timeout: float = DEFAULT_TIMEOUT) -> dict[str, Any]:
    model = load_notebook_model(server, path, timeout=timeout)
    result = apply_clear_outputs(model["content"]["cells"], index=index, cell_id=cell_id, all_cells=all_cells)
    if result["changed"]:
        save_notebook_content(server, path, model["content"], timeout=timeout, expected_last_modified=model.get("last_modified"))
    return {"path": path, "operation": "clear-outputs", **result}


_BATCH_OPS = frozenset({"replace-source", "insert", "delete", "move", "clear-outputs"})


def batch_edit(server: ServerInfo, *, path: str, operations: list[dict[str, Any]], timeout: float = DEFAULT_TIMEOUT) -> dict[str, Any]:
    """Apply multiple edit operations in a single load/save cycle."""
    model = load_notebook_model(server, path, timeout=timeout)
    cells = model["content"]["cells"]
    results: list[dict[str, Any]] = []

    for op_dict in operations:
        op = op_dict.get("op")
        if op not in _BATCH_OPS:
            raise CommandError(f"Unknown batch operation {op!r}. Valid: {sorted(_BATCH_OPS)}")

        if op == "replace-source":
            result = apply_replace_source(cells, cell_id=op_dict.get("cell_id"), index=op_dict.get("index"), source=op_dict["source"])
        elif op == "insert":
            result = apply_insert(cells, cell_type=op_dict.get("cell_type", "code"), source=op_dict.get("source", ""), at_index=op_dict.get("at_index", -1))
        elif op == "delete":
            result = apply_delete(cells, cell_id=op_dict.get("cell_id"), index=op_dict.get("index"))
        elif op == "move":
            result = apply_move(cells, cell_id=op_dict.get("cell_id"), index=op_dict.get("index"), to_index=op_dict["to_index"])
        elif op == "clear-outputs":
            result = apply_clear_outputs(cells, cell_id=op_dict.get("cell_id"), index=op_dict.get("index"), all_cells=op_dict.get("all", False))
        else:
            raise CommandError(f"Unknown batch operation {op!r}.")

        results.append({"op": op, **result})

    save_notebook_content(server, path, model["content"], timeout=timeout, expected_last_modified=model.get("last_modified"))
    return {"operation": "batch", "path": path, "results": results, "cell_count": len(cells)}
