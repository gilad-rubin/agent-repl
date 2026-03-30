"""HTTP route helpers for runtime and run core APIs."""
from __future__ import annotations

from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from agent_repl.core.request_parsing import parse_request
from agent_repl.core.route_helpers import parse_body
from agent_repl.core.runtime_requests import (
    RunFinishRequest,
    RunStartRequest,
    RuntimeDiscardRequest,
    RuntimePromoteRequest,
    RuntimeRecoverRequest,
    RuntimeStartRequest,
    RuntimeStopRequest,
)


def routes(state: Any) -> list[Route]:
    """Return Starlette routes for runtime and run endpoints."""

    async def runtimes_list(request: Request) -> JSONResponse:
        return JSONResponse(state.list_runtimes_payload())

    async def runs_list(request: Request) -> JSONResponse:
        return JSONResponse(state.list_runs_payload())

    async def runtimes_start(request: Request) -> JSONResponse:
        payload = await parse_body(request)
        request_obj = parse_request(payload, RuntimeStartRequest)
        if isinstance(request_obj, tuple):
            return JSONResponse(request_obj[1], status_code=request_obj[0].value)
        return JSONResponse(
            state.start_runtime(
                runtime_id=request_obj.runtime_id,
                mode=request_obj.mode,
                label=request_obj.label,
                environment=request_obj.environment,
                document_path=request_obj.document_path,
                ttl_seconds=request_obj.ttl_seconds,
            )
        )

    async def runtimes_stop(request: Request) -> JSONResponse:
        payload = await parse_body(request)
        request_obj = parse_request(payload, RuntimeStopRequest)
        if isinstance(request_obj, tuple):
            return JSONResponse(request_obj[1], status_code=request_obj[0].value)
        body, status = state.stop_runtime(request_obj.runtime_id)
        return JSONResponse(body, status_code=status.value)

    async def runtimes_recover(request: Request) -> JSONResponse:
        payload = await parse_body(request)
        request_obj = parse_request(payload, RuntimeRecoverRequest)
        if isinstance(request_obj, tuple):
            return JSONResponse(request_obj[1], status_code=request_obj[0].value)
        body, status = state.recover_runtime(request_obj.runtime_id)
        return JSONResponse(body, status_code=status.value)

    async def runtimes_promote(request: Request) -> JSONResponse:
        payload = await parse_body(request)
        request_obj = parse_request(payload, RuntimePromoteRequest)
        if isinstance(request_obj, tuple):
            return JSONResponse(request_obj[1], status_code=request_obj[0].value)
        body, status = state.promote_runtime(request_obj.runtime_id, mode=request_obj.mode)
        return JSONResponse(body, status_code=status.value)

    async def runtimes_discard(request: Request) -> JSONResponse:
        payload = await parse_body(request)
        request_obj = parse_request(payload, RuntimeDiscardRequest)
        if isinstance(request_obj, tuple):
            return JSONResponse(request_obj[1], status_code=request_obj[0].value)
        body, status = state.discard_runtime(request_obj.runtime_id)
        return JSONResponse(body, status_code=status.value)

    async def runs_start(request: Request) -> JSONResponse:
        payload = await parse_body(request)
        request_obj = parse_request(payload, RunStartRequest)
        if isinstance(request_obj, tuple):
            return JSONResponse(request_obj[1], status_code=request_obj[0].value)
        body, status = state.start_run(
            run_id=request_obj.run_id,
            runtime_id=request_obj.runtime_id,
            target_type=request_obj.target_type,
            target_ref=request_obj.target_ref,
            kind=request_obj.kind,
        )
        return JSONResponse(body, status_code=status.value)

    async def runs_finish(request: Request) -> JSONResponse:
        payload = await parse_body(request)
        request_obj = parse_request(payload, RunFinishRequest)
        if isinstance(request_obj, tuple):
            return JSONResponse(request_obj[1], status_code=request_obj[0].value)
        body, status = state.finish_run(request_obj.run_id, request_obj.status)
        return JSONResponse(body, status_code=status.value)

    return [
        Route("/api/runtimes", runtimes_list, methods=["GET"]),
        Route("/api/runs", runs_list, methods=["GET"]),
        Route("/api/runtimes/start", runtimes_start, methods=["POST"]),
        Route("/api/runtimes/stop", runtimes_stop, methods=["POST"]),
        Route("/api/runtimes/recover", runtimes_recover, methods=["POST"]),
        Route("/api/runtimes/promote", runtimes_promote, methods=["POST"]),
        Route("/api/runtimes/discard", runtimes_discard, methods=["POST"]),
        Route("/api/runs/start", runs_start, methods=["POST"]),
        Route("/api/runs/finish", runs_finish, methods=["POST"]),
    ]
