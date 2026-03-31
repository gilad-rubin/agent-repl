"""FastMCP adapter exposing notebook operations as 6 bundled tools."""
from __future__ import annotations

import json
from typing import Any, Literal

from fastmcp import FastMCP
from fastmcp.resources.resource import ResourceContent
from mcp.types import ToolAnnotations


def create_mcp_server(state: Any) -> FastMCP:
    """Create a FastMCP server backed by the shared CoreState service layer.

    The MCP surface exposes 6 outcome-oriented bundle tools per the v1
    architecture. Every tool calls through to the same ``CoreState`` methods
    used by the CLI and REST API — no duplicated business logic.
    """
    mcp = FastMCP("agent-repl")

    # ------------------------------------------------------------------
    # 1. notebook_observe — read-only notebook inspection
    # ------------------------------------------------------------------

    @mcp.tool(
        annotations=ToolAnnotations(readOnlyHint=True),
        description=(
            "Observe notebook state. Use `aspect` to choose what to inspect:\n"
            '- "cells": full cell contents and outputs\n'
            '- "summary": notebook status, kernel, and execution state\n'
            '- "queue": execution queue and running state\n'
            '- "search": find text in cells (requires `query`)\n'
            '- "activity": recent activity events (optional `since` timestamp)\n'
            '- "projection": projected state with outputs and execution info\n'
            "\n"
            "Returns the requested notebook data. All aspects are read-only."
        ),
    )
    def notebook_observe(
        path: str,
        aspect: Literal["cells", "summary", "queue", "search", "activity", "projection"] = "cells",
        query: str | None = None,
        since: float | None = None,
    ) -> dict[str, Any]:
        """Observe notebook state without modifying it."""
        if aspect == "cells":
            body, _status = state.notebook_contents(path)
            return body
        if aspect == "summary":
            body, _status = state.notebook_status(path)
            return body
        if aspect == "queue":
            body, _status = state.notebook_runtime(path)
            return body
        if aspect == "activity":
            body, _status = state.notebook_activity(path, since=since)
            return body
        if aspect == "projection":
            body, _status = state.notebook_projection(path)
            return body
        if aspect == "search":
            body, _status = state.notebook_contents(path)
            if query:
                cells = body.get("cells", [])
                matches = [
                    {"cell_id": c.get("id"), "cell_index": i, "source": c.get("source")}
                    for i, c in enumerate(cells)
                    if query.lower() in (c.get("source") or "").lower()
                ]
                return {"path": path, "query": query, "matches": matches}
            return body
        return {"error": f"Unknown aspect: {aspect}"}

    # ------------------------------------------------------------------
    # 2. notebook_edit — mutating cell operations
    # ------------------------------------------------------------------

    @mcp.tool(
        annotations=ToolAnnotations(readOnlyHint=False),
        description=(
            "Edit notebook cells or create a new notebook.\n\n"
            "To **create** a new notebook: set `action` to `\"create\"`. "
            "Optional `cells` and `kernel_id` params.\n\n"
            "To **edit** an existing notebook: set `action` to `\"edit\"` (default) "
            "and provide `operations` — an array of edit ops:\n"
            '- {"op": "insert", "cell_type": "code", "source": "...", "at_index": 0}\n'
            '- {"op": "delete", "cell_id": "..."}\n'
            '- {"op": "replace-source", "cell_id": "...", "source": "..."}\n'
            '- {"op": "change-cell-type", "cell_id": "...", "cell_type": "markdown"}\n'
            '- {"op": "move", "cell_id": "...", "to_index": 2}\n'
            "\n"
            "Edits route through YDoc and persist to disk."
        ),
    )
    def notebook_edit(
        path: str,
        action: Literal["edit", "create"] = "edit",
        operations: list[dict[str, Any]] | None = None,
        cells: list[dict[str, Any]] | None = None,
        kernel_id: str | None = None,
        owner_session_id: str | None = None,
    ) -> dict[str, Any]:
        """Edit notebook cells or create a new notebook."""
        if action == "create":
            body, _status = state.notebook_create(path, cells=cells, kernel_id=kernel_id)
            return body
        # action == "edit"
        if not operations:
            return {"error": "operations is required for edit action"}
        body, _status = state.notebook_edit(path, operations, owner_session_id=owner_session_id)
        return body

    # ------------------------------------------------------------------
    # 3. notebook_execute — execution, interrupt, restart
    # ------------------------------------------------------------------

    @mcp.tool(
        annotations=ToolAnnotations(readOnlyHint=False),
        description=(
            "Execute code in a notebook. Use `action` to choose:\n"
            '- "cell": execute a single cell (provide `cell_id` or `cell_index`)\n'
            '- "all": execute all code cells in order\n'
            '- "insert-and-execute": insert a new cell with `source` and run it immediately\n'
            '- "interrupt": interrupt the currently running cell\n'
            '- "restart": restart the kernel (clears all runtime state)\n'
            '- "restart-and-run-all": restart kernel then run all cells\n'
            "\n"
            "Execution enters the same queue used by UI and CLI."
        ),
    )
    def notebook_execute(
        path: str,
        action: Literal["cell", "all", "insert-and-execute", "interrupt", "restart", "restart-and-run-all"] = "cell",
        cell_id: str | None = None,
        cell_index: int | None = None,
        source: str | None = None,
        cell_type: str = "code",
        at_index: int = -1,
        owner_session_id: str | None = None,
    ) -> dict[str, Any]:
        """Execute, interrupt, or restart notebook execution."""
        if action == "cell":
            body, _status = state.notebook_execute_cell(
                path, cell_id=cell_id, cell_index=cell_index, owner_session_id=owner_session_id,
            )
            return body
        if action == "all":
            body, _status = state.notebook_execute_all(path, owner_session_id=owner_session_id)
            return body
        if action == "insert-and-execute":
            if not source:
                return {"error": "source is required for insert-and-execute"}
            body, _status = state.notebook_insert_execute(
                path, source=source, cell_type=cell_type, at_index=at_index,
                owner_session_id=owner_session_id,
            )
            return body
        if action == "interrupt":
            body, _status = state.notebook_interrupt(path)
            return body
        if action == "restart":
            body, _status = state.notebook_restart(path)
            return body
        if action == "restart-and-run-all":
            body, _status = state.notebook_restart_and_run_all(path, owner_session_id=owner_session_id)
            return body
        return {"error": f"Unknown action: {action}"}

    # ------------------------------------------------------------------
    # 4. notebook_runtime — kernel and runtime management
    # ------------------------------------------------------------------

    @mcp.tool(
        annotations=ToolAnnotations(readOnlyHint=False),
        description=(
            "Manage notebook runtimes and kernels. Use `action` to choose:\n"
            '- "select-kernel": set the kernel for a notebook (provide `path` and optional `kernel_id`)\n'
            '- "status": get runtime state for a notebook (provide `path`)\n'
            '- "list-runtimes": list all active runtimes in the workspace\n'
            '- "start": register a new runtime (provide `mode`, optional `runtime_id`, `label`, etc.)\n'
            '- "stop": stop a runtime (provide `runtime_id`)\n'
            '- "recover": recover a failed runtime (provide `runtime_id`)\n'
        ),
    )
    def notebook_runtime(
        action: Literal["select-kernel", "status", "list-runtimes", "start", "stop", "recover"] = "status",
        path: str | None = None,
        kernel_id: str | None = None,
        runtime_id: str | None = None,
        mode: str | None = None,
        label: str | None = None,
        environment: str | None = None,
        document_path: str | None = None,
        ttl_seconds: int | None = None,
    ) -> dict[str, Any]:
        """Manage notebook runtimes and kernels."""
        if action == "select-kernel":
            if not path:
                return {"error": "path is required for select-kernel"}
            body, _status = state.notebook_select_kernel(path, kernel_id=kernel_id)
            return body
        if action == "status":
            if not path:
                return {"error": "path is required for status"}
            body, _status = state.notebook_runtime(path)
            return body
        if action == "list-runtimes":
            return state.list_runtimes_payload()
        if action == "start":
            if not mode or not runtime_id:
                return {"error": "mode and runtime_id are required for start"}
            return state.start_runtime(
                runtime_id=runtime_id, mode=mode, label=label,
                environment=environment, document_path=document_path,
                ttl_seconds=ttl_seconds,
            )
        if action == "stop":
            if not runtime_id:
                return {"error": "runtime_id is required for stop"}
            body, _status = state.stop_runtime(runtime_id)
            return body
        if action == "recover":
            if not runtime_id:
                return {"error": "runtime_id is required for recover"}
            body, _status = state.recover_runtime(runtime_id)
            return body
        return {"error": f"Unknown action: {action}"}

    # ------------------------------------------------------------------
    # 5. workspace_files — document listing and opening
    # ------------------------------------------------------------------

    @mcp.tool(
        annotations=ToolAnnotations(readOnlyHint=False),
        description=(
            "Manage workspace documents. Use `action` to choose:\n"
            '- "list": list all tracked documents in the workspace\n'
            '- "open": open and track a document (provide `path`)\n'
        ),
    )
    def workspace_files(
        action: Literal["list", "open"] = "list",
        path: str | None = None,
    ) -> dict[str, Any]:
        """List or open workspace documents."""
        if action == "list":
            return state.list_documents_payload()
        if action == "open":
            if not path:
                return {"error": "path is required for open"}
            body, _status = state.open_document(path)
            return body
        return {"error": f"Unknown action: {action}"}

    # ------------------------------------------------------------------
    # 6. checkpoint — create, restore, list, delete checkpoints
    # ------------------------------------------------------------------

    @mcp.tool(
        annotations=ToolAnnotations(readOnlyHint=False),
        description=(
            "Manage notebook checkpoints (save/restore snapshots). Use `action` to choose:\n"
            '- "create": create a checkpoint (provide `path`, optional `label`)\n'
            '- "restore": restore from a checkpoint (provide `checkpoint_id`)\n'
            '- "list": list checkpoints for a notebook (provide `path`)\n'
            '- "delete": delete a checkpoint (provide `checkpoint_id`)\n'
        ),
    )
    def checkpoint(
        action: Literal["create", "restore", "list", "delete"] = "list",
        path: str | None = None,
        label: str | None = None,
        checkpoint_id: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """Manage notebook checkpoints."""
        if action == "create":
            if not path:
                return {"error": "path is required for create"}
            body, _status = state.checkpoint_create(path, label=label, session_id=session_id)
            return body
        if action == "restore":
            if not checkpoint_id:
                return {"error": "checkpoint_id is required for restore"}
            body, _status = state.checkpoint_restore(checkpoint_id)
            return body
        if action == "list":
            if not path:
                return {"error": "path is required for list"}
            body, _status = state.checkpoint_list(path)
            return body
        if action == "delete":
            if not checkpoint_id:
                return {"error": "checkpoint_id is required for delete"}
            body, _status = state.checkpoint_delete(checkpoint_id)
            return body
        return {"error": f"Unknown action: {action}"}

    # ------------------------------------------------------------------
    # Resources
    # ------------------------------------------------------------------

    @mcp.resource("agent-repl://status", mime_type="application/json")
    def workspace_status() -> list[ResourceContent]:
        """Current workspace status including runtime and document counts."""
        return [
            ResourceContent(
                json.dumps(state.status_payload(), indent=2),
                mime_type="application/json",
            )
        ]

    return mcp
