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

    @mcp.tool
    def start_runtime(runtime_id: str, mode: str, label: str | None = None, environment: str | None = None, document_path: str | None = None, ttl_seconds: int | None = None) -> dict[str, Any]:
        """Start or register a new runtime."""
        return state.start_runtime(runtime_id=runtime_id, mode=mode, label=label, environment=environment, document_path=document_path, ttl_seconds=ttl_seconds)

    @mcp.tool
    def stop_runtime(runtime_id: str) -> dict[str, Any]:
        """Stop a runtime, draining and shutting down its kernel."""
        body, status = state.stop_runtime(runtime_id)
        return body

    @mcp.tool
    def recover_runtime(runtime_id: str) -> dict[str, Any]:
        """Recover a runtime by restarting its kernel."""
        body, status = state.recover_runtime(runtime_id)
        return body

    @mcp.tool
    def promote_runtime(runtime_id: str, mode: str = "shared") -> dict[str, Any]:
        """Promote an ephemeral runtime to shared or pinned."""
        body, status = state.promote_runtime(runtime_id, mode=mode)
        return body

    @mcp.tool
    def discard_runtime(runtime_id: str) -> dict[str, Any]:
        """Discard an ephemeral runtime, stopping and reaping it."""
        body, status = state.discard_runtime(runtime_id)
        return body

    # ------------------------------------------------------------------
    # Run tools
    # ------------------------------------------------------------------

    @mcp.tool
    def list_runs() -> dict[str, Any]:
        """List all runs in the workspace."""
        return state.list_runs_payload()

    @mcp.tool
    def start_run(run_id: str, runtime_id: str, target_type: str, target_ref: str, kind: str) -> dict[str, Any]:
        """Start a new run targeting a document, node, or branch."""
        body, status = state.start_run(run_id=run_id, runtime_id=runtime_id, target_type=target_type, target_ref=target_ref, kind=kind)
        return body

    @mcp.tool
    def finish_run(run_id: str, status: str) -> dict[str, Any]:
        """Finish a run with a final status."""
        body, http_status = state.finish_run(run_id, status)
        return body

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

    @mcp.tool
    def start_session(actor: str, client: str, session_id: str, label: str | None = None, capabilities: list[str] | None = None) -> dict[str, Any]:
        """Start a new collaboration session."""
        return state.start_session(actor, client, label, session_id, capabilities)

    @mcp.tool
    def resolve_preferred_session(actor: str = "human") -> dict[str, Any]:
        """Resolve the preferred active session for an actor type."""
        return state.resolve_preferred_session(actor)

    @mcp.tool
    def touch_session(session_id: str) -> dict[str, Any]:
        """Touch a session to refresh its liveness timestamp."""
        body, status = state.touch_session(session_id)
        return body

    @mcp.tool
    def detach_session(session_id: str) -> dict[str, Any]:
        """Detach a session, marking it as inactive but not ended."""
        body, status = state.detach_session(session_id)
        return body

    @mcp.tool
    def end_session(session_id: str) -> dict[str, Any]:
        """End a session permanently."""
        body, status = state.end_session(session_id)
        return body

    # ------------------------------------------------------------------
    # Presence tools
    # ------------------------------------------------------------------

    @mcp.tool
    def upsert_presence(session_id: str, path: str, activity: str, cell_id: str | None = None, cell_index: int | None = None) -> dict[str, Any]:
        """Upsert notebook presence for a session (what the user is doing and where)."""
        body, status = state.upsert_notebook_presence(session_id=session_id, path=path, activity=activity, cell_id=cell_id, cell_index=cell_index)
        return body

    @mcp.tool
    def clear_presence(session_id: str, path: str | None = None) -> dict[str, Any]:
        """Clear notebook presence for a session, optionally scoped to a path."""
        body, status = state.clear_notebook_presence(session_id=session_id, path=path)
        return body

    # ------------------------------------------------------------------
    # Branch tools
    # ------------------------------------------------------------------

    @mcp.tool
    def start_branch(branch_id: str, document_id: str, owner_session_id: str | None = None, parent_branch_id: str | None = None, title: str | None = None, purpose: str | None = None) -> dict[str, Any]:
        """Start a new branch for a document."""
        body, status = state.start_branch(branch_id=branch_id, document_id=document_id, owner_session_id=owner_session_id, parent_branch_id=parent_branch_id, title=title, purpose=purpose)
        return body

    @mcp.tool
    def finish_branch(branch_id: str, status: str) -> dict[str, Any]:
        """Finish a branch with a final status."""
        body, http_status = state.finish_branch(branch_id, status)
        return body

    @mcp.tool
    def request_branch_review(branch_id: str, requested_by_session_id: str, note: str | None = None) -> dict[str, Any]:
        """Request a review for a branch."""
        body, status = state.request_branch_review(branch_id=branch_id, requested_by_session_id=requested_by_session_id, note=note)
        return body

    @mcp.tool
    def resolve_branch_review(branch_id: str, resolved_by_session_id: str, resolution: str, note: str | None = None) -> dict[str, Any]:
        """Resolve a branch review with a decision."""
        body, status = state.resolve_branch_review(branch_id=branch_id, resolved_by_session_id=resolved_by_session_id, resolution=resolution, note=note)
        return body

    # ------------------------------------------------------------------
    # Lease tools
    # ------------------------------------------------------------------

    @mcp.tool
    def acquire_cell_lease(session_id: str, path: str, cell_id: str | None = None, cell_index: int | None = None, kind: str = "edit", ttl_seconds: float | None = None) -> dict[str, Any]:
        """Acquire an edit lease on a notebook cell."""
        body, status = state.acquire_cell_lease(session_id=session_id, path=path, cell_id=cell_id, cell_index=cell_index, kind=kind, ttl_seconds=ttl_seconds)
        return body

    @mcp.tool
    def release_cell_lease(session_id: str, path: str, cell_id: str | None = None, cell_index: int | None = None) -> dict[str, Any]:
        """Release an edit lease on a notebook cell."""
        body, status = state.release_cell_lease(session_id=session_id, path=path, cell_id=cell_id, cell_index=cell_index)
        return body

    # ------------------------------------------------------------------
    # Resources
    # ------------------------------------------------------------------

    @mcp.resource("agent-repl://status")
    def workspace_status() -> dict[str, Any]:
        """Current workspace status including runtime and document counts."""
        return state.status_payload()

    return mcp
