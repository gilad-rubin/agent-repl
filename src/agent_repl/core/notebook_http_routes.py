"""Notebook-specific HTTP route helpers for the core daemon."""
from __future__ import annotations

from http import HTTPStatus
from typing import Any

from agent_repl.core.notebook_requests import (
    NotebookActivityRequest,
    NotebookCreateRequest,
    NotebookEditRequest,
    NotebookExecuteCellRequest,
    NotebookExecuteVisibleCellRequest,
    NotebookExecutionLookupRequest,
    NotebookInsertExecuteRequest,
    NotebookLeaseAcquireRequest,
    NotebookLeaseReleaseRequest,
    NotebookPathRequest,
    NotebookProjectVisibleRequest,
    NotebookSelectKernelRequest,
    NotebookSessionPathRequest,
)
from agent_repl.core.request_parsing import parse_request as _parse_request


def handle_notebook_post(
    state: Any,
    path: str,
    payload: dict[str, Any],
) -> tuple[HTTPStatus, dict[str, Any]] | None:
    if path == "/api/notebooks/contents":
        request = _parse_request(payload, NotebookPathRequest)
        if isinstance(request, tuple):
            return request
        body, status = state.notebook_contents(request.path)
        return status, body
    if path == "/api/notebooks/status":
        request = _parse_request(payload, NotebookPathRequest)
        if isinstance(request, tuple):
            return request
        body, status = state.notebook_status(request.path)
        return status, body
    if path == "/api/notebooks/create":
        request = _parse_request(payload, NotebookCreateRequest)
        if isinstance(request, tuple):
            return request
        body, status = state.notebook_create(request.path, cells=request.cells, kernel_id=request.kernel_id)
        return status, body
    if path == "/api/notebooks/edit":
        request = _parse_request(payload, NotebookEditRequest)
        if isinstance(request, tuple):
            return request
        body, status = state.notebook_edit(request.path, request.operations, owner_session_id=request.owner_session_id)
        return status, body
    if path == "/api/notebooks/select-kernel":
        request = _parse_request(payload, NotebookSelectKernelRequest)
        if isinstance(request, tuple):
            return request
        body, status = state.notebook_select_kernel(request.path, kernel_id=request.kernel_id)
        return status, body
    if path == "/api/notebooks/execute-cell":
        request = _parse_request(payload, NotebookExecuteCellRequest)
        if isinstance(request, tuple):
            return request
        body, status = state.notebook_execute_cell(
            request.path,
            cell_id=request.cell_id,
            cell_index=request.cell_index,
            owner_session_id=request.owner_session_id,
        )
        return status, body
    if path == "/api/notebooks/insert-and-execute":
        request = _parse_request(payload, NotebookInsertExecuteRequest)
        if isinstance(request, tuple):
            return request
        body, status = state.notebook_insert_execute(
            request.path,
            source=request.source,
            cell_type=request.cell_type,
            at_index=request.at_index,
            owner_session_id=request.owner_session_id,
        )
        return status, body
    if path == "/api/notebooks/execution":
        request = _parse_request(payload, NotebookExecutionLookupRequest)
        if isinstance(request, tuple):
            return request
        body, status = state.notebook_execution(request.execution_id)
        return status, body
    if path == "/api/notebooks/interrupt":
        request = _parse_request(payload, NotebookPathRequest)
        if isinstance(request, tuple):
            return request
        body, status = state.notebook_interrupt(request.path)
        return status, body
    if path == "/api/notebooks/runtime":
        request = _parse_request(payload, NotebookPathRequest)
        if isinstance(request, tuple):
            return request
        body, status = state.notebook_runtime(request.path)
        return status, body
    if path == "/api/notebooks/projection":
        request = _parse_request(payload, NotebookPathRequest)
        if isinstance(request, tuple):
            return request
        body, status = state.notebook_projection(request.path)
        return status, body
    if path == "/api/notebooks/activity":
        request = _parse_request(payload, NotebookActivityRequest)
        if isinstance(request, tuple):
            return request
        body, status = state.notebook_activity(request.path, since=request.since)
        return status, body
    if path == "/api/notebooks/project-visible":
        request = _parse_request(payload, NotebookProjectVisibleRequest)
        if isinstance(request, tuple):
            return request
        body, status = state.notebook_project_visible(
            request.path,
            cells=request.cells,
            owner_session_id=request.owner_session_id,
        )
        return status, body
    if path == "/api/notebooks/execute-visible-cell":
        request = _parse_request(payload, NotebookExecuteVisibleCellRequest)
        if isinstance(request, tuple):
            return request
        body, status = state.notebook_execute_visible_cell(
            request.path,
            cell_index=request.cell_index,
            source=request.source,
            owner_session_id=request.owner_session_id,
        )
        return status, body
    if path == "/api/notebooks/lease/acquire":
        request = _parse_request(payload, NotebookLeaseAcquireRequest)
        if isinstance(request, tuple):
            return request
        body, status = state.acquire_cell_lease(
            session_id=request.session_id,
            path=request.path,
            cell_id=request.cell_id,
            cell_index=request.cell_index,
            kind=request.kind,
            ttl_seconds=request.ttl_seconds,
        )
        return status, body
    if path == "/api/notebooks/lease/release":
        request = _parse_request(payload, NotebookLeaseReleaseRequest)
        if isinstance(request, tuple):
            return request
        body, status = state.release_cell_lease(
            session_id=request.session_id,
            path=request.path,
            cell_id=request.cell_id,
            cell_index=request.cell_index,
        )
        return status, body
    if path == "/api/notebooks/restart":
        request = _parse_request(payload, NotebookPathRequest)
        if isinstance(request, tuple):
            return request
        body, status = state.notebook_restart(request.path)
        return status, body
    if path == "/api/notebooks/execute-all":
        request = _parse_request(payload, NotebookSessionPathRequest)
        if isinstance(request, tuple):
            return request
        body, status = state.notebook_execute_all(request.path, owner_session_id=request.owner_session_id)
        return status, body
    if path == "/api/notebooks/restart-and-run-all":
        request = _parse_request(payload, NotebookSessionPathRequest)
        if isinstance(request, tuple):
            return request
        body, status = state.notebook_restart_and_run_all(request.path, owner_session_id=request.owner_session_id)
        return status, body
    return None


