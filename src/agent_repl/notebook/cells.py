"""Cell building, summarization, resolution, and pure mutation helpers."""
from __future__ import annotations

from typing import Any

from nbformat import v4 as nbf

from agent_repl.core.errors import CommandError
from agent_repl.output.filtering import strip_media_from_output
from agent_repl.output.formatting import summarize_output


def build_cell(cell_type: str, source: str, *, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    if cell_type == "code":
        cell = dict(nbf.new_code_cell(source=source))
    elif cell_type == "markdown":
        cell = dict(nbf.new_markdown_cell(source=source))
    elif cell_type == "raw":
        cell = dict(nbf.new_raw_cell(source=source))
    else:
        raise CommandError(f"Unsupported cell type {cell_type!r}.")
    if metadata:
        cell.setdefault("metadata", {}).update(metadata)
    return cell


def summarize_cell(
    cell: dict[str, Any], *, index: int, include_outputs: bool, strip_media: bool = True,
) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "index": index, "cell_id": cell.get("id"),
        "cell_type": cell.get("cell_type"), "source": cell.get("source", ""),
    }
    if "execution_count" in cell:
        summary["execution_count"] = cell.get("execution_count")
    if include_outputs and cell.get("cell_type") == "code":
        outputs = [summarize_output(o) for o in cell.get("outputs", [])]
        if strip_media:
            outputs = [strip_media_from_output(o) for o in outputs]
        summary["outputs"] = outputs
    return summary


def summarize_cell_brief(cell: dict[str, Any], *, index: int) -> dict[str, Any]:
    """Brief cell summary: index, id, type, source preview (first 3 lines), execution_count."""
    source = cell.get("source", "")
    lines = source.split("\n")
    preview = "\n".join(lines[:3])
    if len(lines) > 3:
        preview += "\n..."
    result: dict[str, Any] = {
        "index": index, "cell_id": cell.get("id"),
        "cell_type": cell.get("cell_type"), "source_preview": preview,
    }
    if "execution_count" in cell:
        result["execution_count"] = cell.get("execution_count")
    return result


def summarize_cell_minimal(cell: dict[str, Any], *, index: int) -> dict[str, Any]:
    """Minimal cell summary: index, id, type, line count, execution_count."""
    source = cell.get("source", "")
    result: dict[str, Any] = {
        "index": index, "cell_id": cell.get("id"),
        "cell_type": cell.get("cell_type"), "line_count": len(source.split("\n")) if source else 0,
    }
    if "execution_count" in cell:
        result["execution_count"] = cell.get("execution_count")
    return result


def resolve_cell_index(cells: list[dict[str, Any]], *, index: int | None, cell_id: str | None) -> int:
    if index is not None:
        if index < 0 or index >= len(cells):
            raise CommandError(f"Cell index {index} is out of range for notebook with {len(cells)} cells.")
        return index
    if cell_id is not None:
        for i, cell in enumerate(cells):
            if cell.get("id") == cell_id:
                return i
        raise CommandError(f"No cell matched id {cell_id}.")
    raise CommandError("Pass one of --index or --cell-id.")


# --- Pure mutation helpers (_apply_*) ---

def apply_replace_source(cells: list[dict[str, Any]], *, index: int | None = None, cell_id: str | None = None, source: str) -> dict[str, Any]:
    resolved = resolve_cell_index(cells, index=index, cell_id=cell_id)
    changed = cells[resolved].get("source", "") != source
    if changed:
        cells[resolved]["source"] = source
    return {"changed": changed, "cell": summarize_cell(cells[resolved], index=resolved, include_outputs=False), "cell_count": len(cells)}


def apply_insert(cells: list[dict[str, Any]], *, cell_type: str, source: str, at_index: int, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    if at_index == -1:
        at_index = len(cells)
    if at_index < 0 or at_index > len(cells):
        raise CommandError(f"Insert index {at_index} is out of range for notebook with {len(cells)} cells.")
    cell = build_cell(cell_type, source, metadata=metadata)
    cells.insert(at_index, cell)
    return {"changed": True, "cell": summarize_cell(cell, index=at_index, include_outputs=False), "cell_id": cell.get("id"), "cell_count": len(cells)}


def apply_delete(cells: list[dict[str, Any]], *, index: int | None = None, cell_id: str | None = None) -> dict[str, Any]:
    resolved = resolve_cell_index(cells, index=index, cell_id=cell_id)
    removed = cells.pop(resolved)
    return {"changed": True, "deleted_cell": summarize_cell(removed, index=resolved, include_outputs=False), "cell_count": len(cells)}


def apply_move(cells: list[dict[str, Any]], *, index: int | None = None, cell_id: str | None = None, to_index: int) -> dict[str, Any]:
    resolved = resolve_cell_index(cells, index=index, cell_id=cell_id)
    if to_index == -1:
        to_index = len(cells) - 1
    if to_index < 0 or to_index >= len(cells):
        raise CommandError(f"Target index {to_index} is out of range for notebook with {len(cells)} cells.")
    if resolved == to_index:
        return {"changed": False, "from_index": resolved, "to_index": to_index, "cell": summarize_cell(cells[resolved], index=resolved, include_outputs=False), "cell_count": len(cells)}
    cell = cells.pop(resolved)
    cells.insert(to_index, cell)
    return {"changed": True, "from_index": resolved, "to_index": to_index, "cell": summarize_cell(cell, index=to_index, include_outputs=False), "cell_count": len(cells)}


def apply_clear_outputs(cells: list[dict[str, Any]], *, index: int | None = None, cell_id: str | None = None, all_cells: bool = False) -> dict[str, Any]:
    if all_cells or (index is None and cell_id is None):
        target_indexes = [i for i, c in enumerate(cells) if c.get("cell_type") == "code"]
    else:
        target_indexes = [resolve_cell_index(cells, index=index, cell_id=cell_id)]

    changed = False
    cleared_cells: list[dict[str, Any]] = []
    for cell_index in target_indexes:
        cell = cells[cell_index]
        if cell.get("cell_type") != "code":
            continue
        if cell.get("outputs") or cell.get("execution_count") is not None:
            cell["outputs"] = []
            cell["execution_count"] = None
            changed = True
        cleared_cells.append(summarize_cell(cell, index=cell_index, include_outputs=False))

    return {"changed": changed, "cleared_cell_count": len(cleared_cells), "cells": cleared_cells, "cell_count": len(cells)}
