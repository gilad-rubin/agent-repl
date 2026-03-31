"""Checkpoint HTTP route helpers for the core daemon."""
from __future__ import annotations

from typing import Any

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from agent_repl.core.checkpoint_requests import (
    CheckpointCreateRequest,
    CheckpointDeleteRequest,
    CheckpointListRequest,
    CheckpointRestoreRequest,
)
from agent_repl.core.request_parsing import parse_request
from agent_repl.core.route_helpers import parse_body


def routes(state: Any) -> list[Route]:
    """Return Starlette routes for checkpoint endpoints."""

    async def respond_from_threadpool(callback: Any, *args: Any, **kwargs: Any) -> JSONResponse:
        body, status = await run_in_threadpool(callback, *args, **kwargs)
        return JSONResponse(body, status_code=status.value)

    async def checkpoints_create(request: Request) -> JSONResponse:
        payload = await parse_body(request)
        req = parse_request(payload, CheckpointCreateRequest)
        if isinstance(req, tuple):
            return JSONResponse(req[1], status_code=req[0].value)
        return await respond_from_threadpool(
            state.checkpoint_create,
            req.path,
            label=req.label,
            session_id=req.session_id,
        )

    async def checkpoints_restore(request: Request) -> JSONResponse:
        payload = await parse_body(request)
        req = parse_request(payload, CheckpointRestoreRequest)
        if isinstance(req, tuple):
            return JSONResponse(req[1], status_code=req[0].value)
        return await respond_from_threadpool(
            state.checkpoint_restore,
            req.checkpoint_id,
        )

    async def checkpoints_list(request: Request) -> JSONResponse:
        path = request.query_params.get("path", "")
        if not path:
            return JSONResponse({"error": "Missing path query parameter"}, status_code=400)
        return await respond_from_threadpool(state.checkpoint_list, path)

    async def checkpoints_delete(request: Request) -> JSONResponse:
        payload = await parse_body(request)
        req = parse_request(payload, CheckpointDeleteRequest)
        if isinstance(req, tuple):
            return JSONResponse(req[1], status_code=req[0].value)
        return await respond_from_threadpool(
            state.checkpoint_delete,
            req.checkpoint_id,
        )

    return [
        Route("/api/checkpoints/create", checkpoints_create, methods=["POST"]),
        Route("/api/checkpoints/restore", checkpoints_restore, methods=["POST"]),
        Route("/api/checkpoints/list", checkpoints_list, methods=["GET"]),
        Route("/api/checkpoints/delete", checkpoints_delete, methods=["POST"]),
    ]
