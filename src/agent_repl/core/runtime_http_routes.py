"""HTTP route helpers for runtime and run core APIs."""
from __future__ import annotations

from http import HTTPStatus
from typing import Any

from agent_repl.core.request_parsing import parse_request
from agent_repl.core.runtime_requests import (
    RunFinishRequest,
    RunStartRequest,
    RuntimeDiscardRequest,
    RuntimePromoteRequest,
    RuntimeRecoverRequest,
    RuntimeStartRequest,
    RuntimeStopRequest,
)


def handle_runtime_get(state: Any, path: str) -> tuple[HTTPStatus, dict[str, Any]] | None:
    if path == "/api/runtimes":
        return HTTPStatus.OK, state.list_runtimes_payload()
    if path == "/api/runs":
        return HTTPStatus.OK, state.list_runs_payload()
    return None


def handle_runtime_post(
    state: Any,
    path: str,
    payload: dict[str, Any],
) -> tuple[HTTPStatus, dict[str, Any]] | None:
    if path == "/api/runtimes/start":
        request = parse_request(payload, RuntimeStartRequest)
        if isinstance(request, tuple):
            return request
        return HTTPStatus.OK, state.start_runtime(
            runtime_id=request.runtime_id,
            mode=request.mode,
            label=request.label,
            environment=request.environment,
            document_path=request.document_path,
            ttl_seconds=request.ttl_seconds,
        )

    if path == "/api/runtimes/stop":
        request = parse_request(payload, RuntimeStopRequest)
        if isinstance(request, tuple):
            return request
        body, status = state.stop_runtime(request.runtime_id)
        return status, body

    if path == "/api/runtimes/recover":
        request = parse_request(payload, RuntimeRecoverRequest)
        if isinstance(request, tuple):
            return request
        body, status = state.recover_runtime(request.runtime_id)
        return status, body

    if path == "/api/runtimes/promote":
        request = parse_request(payload, RuntimePromoteRequest)
        if isinstance(request, tuple):
            return request
        body, status = state.promote_runtime(request.runtime_id, mode=request.mode)
        return status, body

    if path == "/api/runtimes/discard":
        request = parse_request(payload, RuntimeDiscardRequest)
        if isinstance(request, tuple):
            return request
        body, status = state.discard_runtime(request.runtime_id)
        return status, body

    if path == "/api/runs/start":
        request = parse_request(payload, RunStartRequest)
        if isinstance(request, tuple):
            return request
        body, status = state.start_run(
            run_id=request.run_id,
            runtime_id=request.runtime_id,
            target_type=request.target_type,
            target_ref=request.target_ref,
            kind=request.kind,
        )
        return status, body

    if path == "/api/runs/finish":
        request = parse_request(payload, RunFinishRequest)
        if isinstance(request, tuple):
            return request
        body, status = state.finish_run(request.run_id, request.status)
        return status, body

    return None
