"""HTTP route helpers for collaboration-oriented core APIs."""
from __future__ import annotations

from http import HTTPStatus
from typing import Any

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


def handle_collaboration_get(state: Any, path: str) -> tuple[HTTPStatus, dict[str, Any]] | None:
    if path == "/api/sessions":
        return HTTPStatus.OK, state.list_sessions_payload()
    if path == "/api/branches":
        return HTTPStatus.OK, state.list_branches_payload()
    return None


def handle_collaboration_post(
    state: Any,
    path: str,
    payload: dict[str, Any],
) -> tuple[HTTPStatus, dict[str, Any]] | None:
    if path == "/api/sessions/start":
        request = parse_request(payload, SessionStartRequest)
        if isinstance(request, tuple):
            return request
        return HTTPStatus.OK, state.start_session(
            request.actor, request.client, request.label, request.session_id, request.capabilities
        )

    if path == "/api/sessions/resolve":
        request = parse_request(payload, SessionResolveRequest)
        if isinstance(request, tuple):
            return request
        return HTTPStatus.OK, state.resolve_preferred_session(request.actor)

    if path == "/api/sessions/touch":
        request = parse_request(payload, SessionTouchRequest)
        if isinstance(request, tuple):
            return request
        body, status = state.touch_session(request.session_id)
        return status, body

    if path == "/api/sessions/detach":
        request = parse_request(payload, SessionDetachRequest)
        if isinstance(request, tuple):
            return request
        body, status = state.detach_session(request.session_id)
        return status, body

    if path == "/api/sessions/presence/upsert":
        request = parse_request(payload, PresenceUpsertRequest)
        if isinstance(request, tuple):
            return request
        body, status = state.upsert_notebook_presence(
            session_id=request.session_id,
            path=request.path,
            activity=request.activity,
            cell_id=request.cell_id,
            cell_index=request.cell_index,
        )
        return status, body

    if path == "/api/sessions/presence/clear":
        request = parse_request(payload, PresenceClearRequest)
        if isinstance(request, tuple):
            return request
        body, status = state.clear_notebook_presence(
            session_id=request.session_id,
            path=request.path,
        )
        return status, body

    if path == "/api/sessions/end":
        request = parse_request(payload, SessionEndRequest)
        if isinstance(request, tuple):
            return request
        body, status = state.end_session(request.session_id)
        return status, body

    if path == "/api/branches/start":
        request = parse_request(payload, BranchStartRequest)
        if isinstance(request, tuple):
            return request
        body, status = state.start_branch(
            branch_id=request.branch_id,
            document_id=request.document_id,
            owner_session_id=request.owner_session_id,
            parent_branch_id=request.parent_branch_id,
            title=request.title,
            purpose=request.purpose,
        )
        return status, body

    if path == "/api/branches/finish":
        request = parse_request(payload, BranchFinishRequest)
        if isinstance(request, tuple):
            return request
        body, status = state.finish_branch(request.branch_id, request.status)
        return status, body

    if path == "/api/branches/review-request":
        request = parse_request(payload, BranchReviewRequestRequest)
        if isinstance(request, tuple):
            return request
        body, status = state.request_branch_review(
            branch_id=request.branch_id,
            requested_by_session_id=request.requested_by_session_id,
            note=request.note,
        )
        return status, body

    if path == "/api/branches/review-resolve":
        request = parse_request(payload, BranchReviewResolveRequest)
        if isinstance(request, tuple):
            return request
        body, status = state.resolve_branch_review(
            branch_id=request.branch_id,
            resolved_by_session_id=request.resolved_by_session_id,
            resolution=request.resolution,
            note=request.note,
        )
        return status, body

    return None
