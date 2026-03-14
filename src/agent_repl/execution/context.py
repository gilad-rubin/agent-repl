"""Execution context — snapshot kernel + notebook state for agents."""
from __future__ import annotations

from typing import Any

from agent_repl.core import ServerInfo, DEFAULT_EXEC_TIMEOUT
from agent_repl.notebook.cells import summarize_cell_brief
from agent_repl.notebook.directives import list_prompts
from agent_repl.notebook.io import load_notebook_model
from agent_repl.server.kernels import get_kernel_model


def build_execution_context(
    server: ServerInfo,
    *,
    path: str,
    session_id: str | None = None,
    kernel_id: str | None = None,
    transport: str = "auto",
    timeout: float = DEFAULT_EXEC_TIMEOUT,
    include_outputs: bool = False,
) -> dict[str, Any]:
    """Snapshot kernel + notebook state for agent consumption.

    One call instead of cat + vars list + prompts.
    """
    result: dict[str, Any] = {}

    # Notebook state
    model = load_notebook_model(server, path, timeout=timeout)
    content = model.get("content") or {}
    raw_cells = content.get("cells", [])
    cells = [summarize_cell_brief(c, index=i) for i, c in enumerate(raw_cells)]
    result["notebook"] = {
        "path": model.get("path"),
        "cell_count": len(raw_cells),
        "cells": cells,
    }

    # Pending prompts
    prompts = list_prompts(raw_cells, pending_only=True, context_cells=0)
    result["pending_prompts"] = prompts

    # Kernel state (if we can resolve it)
    if kernel_id:
        try:
            km = get_kernel_model(server, kernel_id, timeout=timeout)
            result["kernel"] = {
                "id": km.get("id"),
                "name": km.get("name"),
                "execution_state": km.get("execution_state"),
                "last_activity": km.get("last_activity"),
            }
        except Exception:
            result["kernel"] = None
    else:
        result["kernel"] = None

    # Variables (optional, only if we have a session)
    if session_id or kernel_id:
        try:
            from agent_repl.execution.variables import list_variables
            vars_result = list_variables(
                server, path=path, session_id=session_id, kernel_id=kernel_id,
                transport=transport, timeout=timeout, limit=25,
                include_private=False, include_callables=False,
            )
            result["variables"] = vars_result.get("variables", [])
        except Exception:
            result["variables"] = []
    else:
        result["variables"] = []

    return result
