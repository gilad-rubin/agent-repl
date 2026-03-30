"""HTTP route helpers for collaboration-oriented core APIs."""
from __future__ import annotations

from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from agent_repl.core.collaboration_requests import (
    BranchFinishRequest,
    BranchReviewRequestRequest,
    BranchReviewResolveRequest,
    BranchStartRequest,
    PresenceClearRequest,
    PresenceUpsertRequest,
    SessionDetachRequest,
    SessionEndRequest,
    SessionResolveRequest,
    SessionStartRequest,
    SessionTouchRequest,
)
from agent_repl.core.request_parsing import parse_request
from agent_repl.core.route_helpers import parse_body


def routes(state: Any) -> list[Route]:
    """Return Starlette routes for collaboration endpoints."""

    async def sessions_list(request: Request) -> JSONResponse:
        return JSONResponse(state.list_sessions_payload())

    async def branches_list(request: Request) -> JSONResponse:
        return JSONResponse(state.list_branches_payload())

    async def sessions_start(request: Request) -> JSONResponse:
        payload = await parse_body(request)
        request_obj = parse_request(payload, SessionStartRequest)
        if isinstance(request_obj, tuple):
            return JSONResponse(request_obj[1], status_code=request_obj[0].value)
        return JSONResponse(
            state.start_session(
                request_obj.actor,
                request_obj.client,
                request_obj.label,
                request_obj.session_id,
                request_obj.capabilities,
            )
        )

    async def sessions_resolve(request: Request) -> JSONResponse:
        payload = await parse_body(request)
        request_obj = parse_request(payload, SessionResolveRequest)
        if isinstance(request_obj, tuple):
            return JSONResponse(request_obj[1], status_code=request_obj[0].value)
        return JSONResponse(state.resolve_preferred_session(request_obj.actor))

    async def sessions_touch(request: Request) -> JSONResponse:
        payload = await parse_body(request)
        request_obj = parse_request(payload, SessionTouchRequest)
        if isinstance(request_obj, tuple):
            return JSONResponse(request_obj[1], status_code=request_obj[0].value)
        body, status = state.touch_session(request_obj.session_id)
        return JSONResponse(body, status_code=status.value)

    async def sessions_detach(request: Request) -> JSONResponse:
        payload = await parse_body(request)
        request_obj = parse_request(payload, SessionDetachRequest)
        if isinstance(request_obj, tuple):
            return JSONResponse(request_obj[1], status_code=request_obj[0].value)
        body, status = state.detach_session(request_obj.session_id)
        return JSONResponse(body, status_code=status.value)

    async def sessions_presence_upsert(request: Request) -> JSONResponse:
        payload = await parse_body(request)
        request_obj = parse_request(payload, PresenceUpsertRequest)
        if isinstance(request_obj, tuple):
            return JSONResponse(request_obj[1], status_code=request_obj[0].value)
        body, status = state.upsert_notebook_presence(
            session_id=request_obj.session_id,
            path=request_obj.path,
            activity=request_obj.activity,
            cell_id=request_obj.cell_id,
            cell_index=request_obj.cell_index,
        )
        return JSONResponse(body, status_code=status.value)

    async def sessions_presence_clear(request: Request) -> JSONResponse:
        payload = await parse_body(request)
        request_obj = parse_request(payload, PresenceClearRequest)
        if isinstance(request_obj, tuple):
            return JSONResponse(request_obj[1], status_code=request_obj[0].value)
        body, status = state.clear_notebook_presence(
            session_id=request_obj.session_id,
            path=request_obj.path,
        )
        return JSONResponse(body, status_code=status.value)

    async def sessions_end(request: Request) -> JSONResponse:
        payload = await parse_body(request)
        request_obj = parse_request(payload, SessionEndRequest)
        if isinstance(request_obj, tuple):
            return JSONResponse(request_obj[1], status_code=request_obj[0].value)
        body, status = state.end_session(request_obj.session_id)
        return JSONResponse(body, status_code=status.value)

    async def branches_start(request: Request) -> JSONResponse:
        payload = await parse_body(request)
        request_obj = parse_request(payload, BranchStartRequest)
        if isinstance(request_obj, tuple):
            return JSONResponse(request_obj[1], status_code=request_obj[0].value)
        body, status = state.start_branch(
            branch_id=request_obj.branch_id,
            document_id=request_obj.document_id,
            owner_session_id=request_obj.owner_session_id,
            parent_branch_id=request_obj.parent_branch_id,
            title=request_obj.title,
            purpose=request_obj.purpose,
        )
        return JSONResponse(body, status_code=status.value)

    async def branches_finish(request: Request) -> JSONResponse:
        payload = await parse_body(request)
        request_obj = parse_request(payload, BranchFinishRequest)
        if isinstance(request_obj, tuple):
            return JSONResponse(request_obj[1], status_code=request_obj[0].value)
        body, status = state.finish_branch(request_obj.branch_id, request_obj.status)
        return JSONResponse(body, status_code=status.value)

    async def branches_review_request(request: Request) -> JSONResponse:
        payload = await parse_body(request)
        request_obj = parse_request(payload, BranchReviewRequestRequest)
        if isinstance(request_obj, tuple):
            return JSONResponse(request_obj[1], status_code=request_obj[0].value)
        body, status = state.request_branch_review(
            branch_id=request_obj.branch_id,
            requested_by_session_id=request_obj.requested_by_session_id,
            note=request_obj.note,
        )
        return JSONResponse(body, status_code=status.value)

    async def branches_review_resolve(request: Request) -> JSONResponse:
        payload = await parse_body(request)
        request_obj = parse_request(payload, BranchReviewResolveRequest)
        if isinstance(request_obj, tuple):
            return JSONResponse(request_obj[1], status_code=request_obj[0].value)
        body, status = state.resolve_branch_review(
            branch_id=request_obj.branch_id,
            resolved_by_session_id=request_obj.resolved_by_session_id,
            resolution=request_obj.resolution,
            note=request_obj.note,
        )
        return JSONResponse(body, status_code=status.value)

    return [
        Route("/api/sessions", sessions_list, methods=["GET"]),
        Route("/api/branches", branches_list, methods=["GET"]),
        Route("/api/sessions/start", sessions_start, methods=["POST"]),
        Route("/api/sessions/resolve", sessions_resolve, methods=["POST"]),
        Route("/api/sessions/touch", sessions_touch, methods=["POST"]),
        Route("/api/sessions/detach", sessions_detach, methods=["POST"]),
        Route("/api/sessions/presence/upsert", sessions_presence_upsert, methods=["POST"]),
        Route("/api/sessions/presence/clear", sessions_presence_clear, methods=["POST"]),
        Route("/api/sessions/end", sessions_end, methods=["POST"]),
        Route("/api/branches/start", branches_start, methods=["POST"]),
        Route("/api/branches/finish", branches_finish, methods=["POST"]),
        Route("/api/branches/review-request", branches_review_request, methods=["POST"]),
        Route("/api/branches/review-resolve", branches_review_resolve, methods=["POST"]),
    ]
