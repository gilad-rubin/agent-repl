"""FastMCP adapter exposing notebook operations as MCP tools."""
from __future__ import annotations

from typing import Any

from fastmcp import FastMCP


def create_mcp_server(state: Any) -> FastMCP:
    """Create a FastMCP server backed by the shared application service layer.

    Every MCP tool calls through to the same ``CoreState`` methods used by the
    CLI and REST API, so there is no duplicated business logic.
    """
    mcp = FastMCP("agent-repl")

    # ------------------------------------------------------------------
    # Notebook tools
    # ------------------------------------------------------------------

    @mcp.tool
    def notebook_create(path: str, cells: list[dict[str, str]] | None = None, kernel_id: str | None = None) -> dict[str, Any]:
        """Create a new notebook file with optional initial cells."""
        body, status = state.notebook_create(path, cells=cells, kernel_id=kernel_id)
        return body

    @mcp.tool
    def notebook_contents(path: str) -> dict[str, Any]:
        """Get the cells and metadata of a notebook."""
        body, status = state.notebook_contents(path)
        return body

    @mcp.tool
    def notebook_edit(path: str, operations: list[dict[str, Any]], owner_session_id: str | None = None) -> dict[str, Any]:
        """Apply edit operations (insert, delete, move, replace-source) to a notebook."""
        body, status = state.notebook_edit(path, operations, owner_session_id=owner_session_id)
        return body

    @mcp.tool
    def notebook_execute_cell(path: str, cell_id: str | None = None, cell_index: int | None = None, owner_session_id: str | None = None) -> dict[str, Any]:
        """Execute a single cell in a notebook."""
        body, status = state.notebook_execute_cell(path, cell_id=cell_id, cell_index=cell_index, owner_session_id=owner_session_id)
        return body

    @mcp.tool
    def notebook_execute_all(path: str, owner_session_id: str | None = None) -> dict[str, Any]:
        """Execute all code cells in a notebook, stopping on first error."""
        body, status = state.notebook_execute_all(path, owner_session_id=owner_session_id)
        return body

    @mcp.tool
    def notebook_restart(path: str) -> dict[str, Any]:
        """Restart the kernel for a notebook."""
        body, status = state.notebook_restart(path)
        return body

    @mcp.tool
    def notebook_restart_and_run_all(path: str, owner_session_id: str | None = None) -> dict[str, Any]:
        """Restart the kernel and run all cells in a notebook."""
        body, status = state.notebook_restart_and_run_all(path, owner_session_id=owner_session_id)
        return body

    @mcp.tool
    def notebook_status(path: str) -> dict[str, Any]:
        """Get the execution and queue status of a notebook."""
        body, status = state.notebook_status(path)
        return body

    @mcp.tool
    def notebook_activity(path: str, since: float | None = None) -> dict[str, Any]:
        """Get recent activity events for a notebook."""
        body, status = state.notebook_activity(path, since=since)
        return body

    @mcp.tool
    def notebook_interrupt(path: str) -> dict[str, Any]:
        """Interrupt the currently executing cell in a notebook."""
        body, status = state.notebook_interrupt(path)
        return body

    @mcp.tool
    def notebook_select_kernel(path: str, kernel_id: str | None = None) -> dict[str, Any]:
        """Select or change the kernel for a notebook."""
        body, status = state.notebook_select_kernel(path, kernel_id=kernel_id)
        return body

    @mcp.tool
    def notebook_projection(path: str) -> dict[str, Any]:
        """Get the projected state of a notebook (cells with outputs and execution state)."""
        body, status = state.notebook_projection(path)
        return body

    @mcp.tool
    def notebook_insert_execute(path: str, source: str, cell_type: str = "code", at_index: int = -1, owner_session_id: str | None = None) -> dict[str, Any]:
        """Insert a new cell and execute it immediately."""
        body, status = state.notebook_insert_execute(path, source=source, cell_type=cell_type, at_index=at_index, owner_session_id=owner_session_id)
        return body

    @mcp.tool
    def notebook_execution_lookup(execution_id: str) -> dict[str, Any]:
        """Look up the status and result of an execution by its ID."""
        body, status = state.notebook_execution(execution_id)
        return body

    @mcp.tool
    def notebook_project_visible(path: str, cells: list[dict[str, Any]], owner_session_id: str | None = None) -> dict[str, Any]:
        """Project visible cells into a notebook, syncing editor state."""
        body, status = state.notebook_project_visible(path, cells=cells, owner_session_id=owner_session_id)
        return body

    @mcp.tool
    def notebook_execute_visible_cell(path: str, cell_index: int, source: str, owner_session_id: str | None = None) -> dict[str, Any]:
        """Execute a visible cell in a notebook by index with updated source."""
        body, status = state.notebook_execute_visible_cell(path, cell_index=cell_index, source=source, owner_session_id=owner_session_id)
        return body

    # ------------------------------------------------------------------
    # Runtime tools
    # ------------------------------------------------------------------

    @mcp.tool
    def notebook_runtime(path: str) -> dict[str, Any]:
        """Get the runtime state for a notebook (kernel, busy, execution)."""
        body, status = state.notebook_runtime(path)
        return body

    @mcp.tool
    def list_runtimes() -> dict[str, Any]:
        """List all active runtimes in the workspace."""
        return state.list_runtimes_payload()

    # ------------------------------------------------------------------
    # Document tools
    # ------------------------------------------------------------------

    @mcp.tool
    def open_document(path: str) -> dict[str, Any]:
        """Open and track a document in the workspace."""
        body, status = state.open_document(path)
        return body

    @mcp.tool
    def list_documents() -> dict[str, Any]:
        """List all tracked documents in the workspace."""
        return state.list_documents_payload()

    # ------------------------------------------------------------------
    # Session tools
    # ------------------------------------------------------------------

    @mcp.tool
    def list_sessions() -> dict[str, Any]:
        """List all sessions in the workspace."""
        return state.list_sessions_payload()

    # ------------------------------------------------------------------
    # Resources
    # ------------------------------------------------------------------

    @mcp.resource("agent-repl://status")
    def workspace_status() -> dict[str, Any]:
        """Current workspace status including runtime and document counts."""
        return state.status_payload()

    return mcp
