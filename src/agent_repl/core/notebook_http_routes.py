"""Notebook-specific HTTP route helpers for the core daemon."""
from __future__ import annotations

import logging
import threading
from typing import Any

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

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
from agent_repl.core.request_parsing import parse_request
from agent_repl.core.route_helpers import parse_body


def routes(state: Any) -> list[Route]:
    """Return Starlette routes for notebook endpoints."""

    logger = logging.getLogger(__name__)

    async def respond_from_threadpool(callback: Any, *args: Any, **kwargs: Any) -> JSONResponse:
        body, status = await run_in_threadpool(callback, *args, **kwargs)
        return JSONResponse(body, status_code=status.value)

    def start_background_task(name: str, callback: Any, *args: Any, **kwargs: Any) -> None:
        def run() -> None:
            try:
                callback(*args, **kwargs)
            except Exception:
                logger.exception("Background notebook task failed: %s", name)

        thread = threading.Thread(target=run, name=name, daemon=True)
        thread.start()

    async def notebooks_contents(request: Request) -> JSONResponse:
        payload = await parse_body(request)
        req = parse_request(payload, NotebookPathRequest)
        if isinstance(req, tuple):
            return JSONResponse(req[1], status_code=req[0].value)
        return await respond_from_threadpool(state.notebook_contents, req.path)

    async def notebooks_status(request: Request) -> JSONResponse:
        payload = await parse_body(request)
        req = parse_request(payload, NotebookPathRequest)
        if isinstance(req, tuple):
            return JSONResponse(req[1], status_code=req[0].value)
        return await respond_from_threadpool(state.notebook_status, req.path)

    async def notebooks_create(request: Request) -> JSONResponse:
        payload = await parse_body(request)
        req = parse_request(payload, NotebookCreateRequest)
        if isinstance(req, tuple):
            return JSONResponse(req[1], status_code=req[0].value)
        return await respond_from_threadpool(
            state.notebook_create,
            req.path,
            cells=req.cells,
            kernel_id=req.kernel_id,
        )

    async def notebooks_edit(request: Request) -> JSONResponse:
        payload = await parse_body(request)
        req = parse_request(payload, NotebookEditRequest)
        if isinstance(req, tuple):
            return JSONResponse(req[1], status_code=req[0].value)
        return await respond_from_threadpool(
            state.notebook_edit,
            req.path,
            req.operations,
            owner_session_id=req.owner_session_id,
        )

    async def notebooks_select_kernel(request: Request) -> JSONResponse:
        payload = await parse_body(request)
        req = parse_request(payload, NotebookSelectKernelRequest)
        if isinstance(req, tuple):
            return JSONResponse(req[1], status_code=req[0].value)
        return await respond_from_threadpool(
            state.notebook_select_kernel,
            req.path,
            kernel_id=req.kernel_id,
        )

    async def notebooks_execute_cell(request: Request) -> JSONResponse:
        payload = await parse_body(request)
        req = parse_request(payload, NotebookExecuteCellRequest)
        if isinstance(req, tuple):
            return JSONResponse(req[1], status_code=req[0].value)
        if not req.wait:
            start_background_task(
                "agent-repl-execute-cell",
                state.notebook_execute_cell,
                req.path,
                cell_id=req.cell_id,
                cell_index=req.cell_index,
                owner_session_id=req.owner_session_id,
            )
            return JSONResponse(
                {
                    "status": "started",
                    "path": req.path,
                    "cell_id": req.cell_id,
                    "cell_index": req.cell_index,
                },
                status_code=200,
            )
        return await respond_from_threadpool(
            state.notebook_execute_cell,
            req.path, cell_id=req.cell_id, cell_index=req.cell_index, owner_session_id=req.owner_session_id
        )

    async def notebooks_insert_execute(request: Request) -> JSONResponse:
        payload = await parse_body(request)
        req = parse_request(payload, NotebookInsertExecuteRequest)
        if isinstance(req, tuple):
            return JSONResponse(req[1], status_code=req[0].value)
        if not req.wait:
            start_background_task(
                "agent-repl-insert-execute",
                state.notebook_insert_execute,
                req.path,
                source=req.source,
                cell_type=req.cell_type,
                at_index=req.at_index,
                owner_session_id=req.owner_session_id,
            )
            return JSONResponse(
                {
                    "status": "started",
                    "path": req.path,
                    "at_index": req.at_index,
                    "cell_type": req.cell_type,
                    "operation": "insert-execute",
                },
                status_code=200,
            )
        return await respond_from_threadpool(
            state.notebook_insert_execute,
            req.path,
            source=req.source,
            cell_type=req.cell_type,
            at_index=req.at_index,
            owner_session_id=req.owner_session_id,
        )

    async def notebooks_execution(request: Request) -> JSONResponse:
        payload = await parse_body(request)
        req = parse_request(payload, NotebookExecutionLookupRequest)
        if isinstance(req, tuple):
            return JSONResponse(req[1], status_code=req[0].value)
        return await respond_from_threadpool(state.notebook_execution, req.execution_id)

    async def notebooks_interrupt(request: Request) -> JSONResponse:
        payload = await parse_body(request)
        req = parse_request(payload, NotebookPathRequest)
        if isinstance(req, tuple):
            return JSONResponse(req[1], status_code=req[0].value)
        return await respond_from_threadpool(state.notebook_interrupt, req.path)

    async def notebooks_runtime(request: Request) -> JSONResponse:
        payload = await parse_body(request)
        req = parse_request(payload, NotebookPathRequest)
        if isinstance(req, tuple):
            return JSONResponse(req[1], status_code=req[0].value)
        return await respond_from_threadpool(state.notebook_runtime, req.path)

    async def notebooks_projection(request: Request) -> JSONResponse:
        payload = await parse_body(request)
        req = parse_request(payload, NotebookPathRequest)
        if isinstance(req, tuple):
            return JSONResponse(req[1], status_code=req[0].value)
        return await respond_from_threadpool(state.notebook_projection, req.path)

    async def notebooks_activity(request: Request) -> JSONResponse:
        payload = await parse_body(request)
        req = parse_request(payload, NotebookActivityRequest)
        if isinstance(req, tuple):
            return JSONResponse(req[1], status_code=req[0].value)
        return await respond_from_threadpool(state.notebook_activity, req.path, since=req.since)

    async def notebooks_project_visible(request: Request) -> JSONResponse:
        payload = await parse_body(request)
        req = parse_request(payload, NotebookProjectVisibleRequest)
        if isinstance(req, tuple):
            return JSONResponse(req[1], status_code=req[0].value)
        return await respond_from_threadpool(
            state.notebook_project_visible,
            req.path, cells=req.cells, owner_session_id=req.owner_session_id
        )

    async def notebooks_execute_visible_cell(request: Request) -> JSONResponse:
        payload = await parse_body(request)
        req = parse_request(payload, NotebookExecuteVisibleCellRequest)
        if isinstance(req, tuple):
            return JSONResponse(req[1], status_code=req[0].value)
        return await respond_from_threadpool(
            state.notebook_execute_visible_cell,
            req.path, cell_index=req.cell_index, source=req.source, owner_session_id=req.owner_session_id
        )

    async def notebooks_lease_acquire(request: Request) -> JSONResponse:
        payload = await parse_body(request)
        req = parse_request(payload, NotebookLeaseAcquireRequest)
        if isinstance(req, tuple):
            return JSONResponse(req[1], status_code=req[0].value)
        return await respond_from_threadpool(
            state.acquire_cell_lease,
            session_id=req.session_id,
            path=req.path,
            cell_id=req.cell_id,
            cell_index=req.cell_index,
            kind=req.kind,
            ttl_seconds=req.ttl_seconds,
        )

    async def notebooks_lease_release(request: Request) -> JSONResponse:
        payload = await parse_body(request)
        req = parse_request(payload, NotebookLeaseReleaseRequest)
        if isinstance(req, tuple):
            return JSONResponse(req[1], status_code=req[0].value)
        return await respond_from_threadpool(
            state.release_cell_lease,
            session_id=req.session_id, path=req.path, cell_id=req.cell_id, cell_index=req.cell_index
        )

    async def notebooks_restart(request: Request) -> JSONResponse:
        payload = await parse_body(request)
        req = parse_request(payload, NotebookPathRequest)
        if isinstance(req, tuple):
            return JSONResponse(req[1], status_code=req[0].value)
        return await respond_from_threadpool(state.notebook_restart, req.path)

    async def notebooks_execute_all(request: Request) -> JSONResponse:
        payload = await parse_body(request)
        req = parse_request(payload, NotebookSessionPathRequest)
        if isinstance(req, tuple):
            return JSONResponse(req[1], status_code=req[0].value)
        if not req.wait:
            start_background_task(
                "agent-repl-execute-all",
                state.notebook_execute_all,
                req.path,
                owner_session_id=req.owner_session_id,
            )
            return JSONResponse(
                {
                    "status": "started",
                    "path": req.path,
                    "mode": "headless",
                },
                status_code=200,
            )
        return await respond_from_threadpool(
            state.notebook_execute_all,
            req.path,
            owner_session_id=req.owner_session_id,
        )

    async def notebooks_restart_and_run_all(request: Request) -> JSONResponse:
        payload = await parse_body(request)
        req = parse_request(payload, NotebookSessionPathRequest)
        if isinstance(req, tuple):
            return JSONResponse(req[1], status_code=req[0].value)
        if not req.wait:
            start_background_task(
                "agent-repl-restart-and-run-all",
                state.notebook_restart_and_run_all,
                req.path,
                owner_session_id=req.owner_session_id,
            )
            return JSONResponse(
                {
                    "status": "started",
                    "path": req.path,
                    "mode": "headless",
                },
                status_code=200,
            )
        return await respond_from_threadpool(
            state.notebook_restart_and_run_all,
            req.path,
            owner_session_id=req.owner_session_id,
        )

    return [
        Route("/api/notebooks/contents", notebooks_contents, methods=["POST"]),
        Route("/api/notebooks/status", notebooks_status, methods=["POST"]),
        Route("/api/notebooks/create", notebooks_create, methods=["POST"]),
        Route("/api/notebooks/edit", notebooks_edit, methods=["POST"]),
        Route("/api/notebooks/select-kernel", notebooks_select_kernel, methods=["POST"]),
        Route("/api/notebooks/execute-cell", notebooks_execute_cell, methods=["POST"]),
        Route("/api/notebooks/insert-and-execute", notebooks_insert_execute, methods=["POST"]),
        Route("/api/notebooks/execution", notebooks_execution, methods=["POST"]),
        Route("/api/notebooks/interrupt", notebooks_interrupt, methods=["POST"]),
        Route("/api/notebooks/runtime", notebooks_runtime, methods=["POST"]),
        Route("/api/notebooks/projection", notebooks_projection, methods=["POST"]),
        Route("/api/notebooks/activity", notebooks_activity, methods=["POST"]),
        Route("/api/notebooks/project-visible", notebooks_project_visible, methods=["POST"]),
        Route("/api/notebooks/execute-visible-cell", notebooks_execute_visible_cell, methods=["POST"]),
        Route("/api/notebooks/lease/acquire", notebooks_lease_acquire, methods=["POST"]),
        Route("/api/notebooks/lease/release", notebooks_lease_release, methods=["POST"]),
        Route("/api/notebooks/restart", notebooks_restart, methods=["POST"]),
        Route("/api/notebooks/execute-all", notebooks_execute_all, methods=["POST"]),
        Route("/api/notebooks/restart-and-run-all", notebooks_restart_and_run_all, methods=["POST"]),
    ]
