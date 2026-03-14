"""Notebook contents reading with filtering and brief/full/minimal modes."""
from __future__ import annotations

from typing import Any

from agent_repl.core.errors import CommandError
from agent_repl.core.models import DEFAULT_TIMEOUT, ServerInfo
from agent_repl.notebook.cells import summarize_cell, summarize_cell_brief, summarize_cell_minimal
from agent_repl.notebook.io import load_notebook_model


def parse_cell_ranges(spec: str, max_cells: int) -> set[int]:
    """Parse flexible cell index spec: '0-2,4,7-' → {0,1,2,4,7,8,9,...}"""
    result: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            pieces = part.split("-", 1)
            start_str, end_str = pieces[0].strip(), pieces[1].strip()
            start = int(start_str) if start_str else 0
            end = int(end_str) if end_str else max_cells - 1
            if start < 0 or end >= max_cells:
                raise CommandError(f"Cell range {part!r} out of bounds (notebook has {max_cells} cells).")
            result.update(range(start, end + 1))
        else:
            idx = int(part)
            if idx < 0 or idx >= max_cells:
                raise CommandError(f"Cell index {idx} out of bounds (notebook has {max_cells} cells).")
            result.add(idx)
    return result


def get_contents(
    server: ServerInfo, path: str, *,
    detail: str = "brief", raw: bool = False, timeout: float = DEFAULT_TIMEOUT,
    cell_indexes: set[int] | None = None, cell_type_filter: str | None = None, strip_media: bool = True,
) -> dict[str, Any]:
    model = load_notebook_model(server, path, timeout=timeout)
    if raw:
        return model

    content = model.get("content") or {}
    cells = []
    for index, cell in enumerate(content.get("cells", [])):
        if cell_indexes is not None and index not in cell_indexes:
            continue
        if cell_type_filter and cell.get("cell_type") != cell_type_filter:
            continue
        if detail == "minimal":
            cells.append(summarize_cell_minimal(cell, index=index))
        elif detail == "brief":
            cells.append(summarize_cell_brief(cell, index=index))
        else:
            cells.append(summarize_cell(cell, index=index, include_outputs=True, strip_media=strip_media))

    return {
        "path": model.get("path"), "name": model.get("name"),
        "type": model.get("type"), "last_modified": model.get("last_modified"),
        "format": model.get("format"),
        "nbformat": content.get("nbformat"), "nbformat_minor": content.get("nbformat_minor"),
        "cells": cells,
    }
