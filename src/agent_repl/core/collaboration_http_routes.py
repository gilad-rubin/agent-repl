"""HTTP route helpers for collaboration-oriented core APIs."""
from __future__ import annotations

from http import HTTPStatus
from typing import Any


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
        actor = payload.get("actor")
        client = payload.get("client")
        session_id = payload.get("session_id")
        label = payload.get("label")
        capabilities = payload.get("capabilities")
        if not isinstance(actor, str) or not actor:
            return HTTPStatus.BAD_REQUEST, {"error": "Missing actor"}
        if not isinstance(client, str) or not client:
            return HTTPStatus.BAD_REQUEST, {"error": "Missing client"}
        if not isinstance(session_id, str) or not session_id:
            return HTTPStatus.BAD_REQUEST, {"error": "Missing session_id"}
        resolved_capabilities = (
            [item for item in capabilities if isinstance(item, str) and item]
            if isinstance(capabilities, list)
            else None
        )
        return HTTPStatus.OK, state.start_session(actor, client, label, session_id, resolved_capabilities)

    if path == "/api/sessions/resolve":
        actor = payload.get("actor", "human")
        if not isinstance(actor, str) or not actor:
            return HTTPStatus.BAD_REQUEST, {"error": "Missing actor"}
        return HTTPStatus.OK, state.resolve_preferred_session(actor)

    if path == "/api/sessions/touch":
        session_id = payload.get("session_id")
        if not isinstance(session_id, str) or not session_id:
            return HTTPStatus.BAD_REQUEST, {"error": "Missing session_id"}
        body, status = state.touch_session(session_id)
        return status, body

    if path == "/api/sessions/detach":
        session_id = payload.get("session_id")
        if not isinstance(session_id, str) or not session_id:
            return HTTPStatus.BAD_REQUEST, {"error": "Missing session_id"}
        body, status = state.detach_session(session_id)
        return status, body

    if path == "/api/sessions/presence/upsert":
        session_id = payload.get("session_id")
        notebook_path = payload.get("path")
        activity = payload.get("activity")
        cell_id = payload.get("cell_id")
        cell_index = payload.get("cell_index")
        if not isinstance(session_id, str) or not session_id:
            return HTTPStatus.BAD_REQUEST, {"error": "Missing session_id"}
        if not isinstance(notebook_path, str) or not notebook_path:
            return HTTPStatus.BAD_REQUEST, {"error": "Missing path"}
        if not isinstance(activity, str) or not activity:
            return HTTPStatus.BAD_REQUEST, {"error": "Missing activity"}
        body, status = state.upsert_notebook_presence(
            session_id=session_id,
            path=notebook_path,
            activity=activity,
            cell_id=cell_id if isinstance(cell_id, str) else None,
            cell_index=cell_index if isinstance(cell_index, int) else None,
        )
        return status, body

    if path == "/api/sessions/presence/clear":
        session_id = payload.get("session_id")
        notebook_path = payload.get("path")
        if not isinstance(session_id, str) or not session_id:
            return HTTPStatus.BAD_REQUEST, {"error": "Missing session_id"}
        body, status = state.clear_notebook_presence(
            session_id=session_id,
            path=notebook_path if isinstance(notebook_path, str) else None,
        )
        return status, body

    if path == "/api/sessions/end":
        session_id = payload.get("session_id")
        if not isinstance(session_id, str) or not session_id:
            return HTTPStatus.BAD_REQUEST, {"error": "Missing session_id"}
        body, status = state.end_session(session_id)
        return status, body

    if path == "/api/branches/start":
        branch_id = payload.get("branch_id")
        document_id = payload.get("document_id")
        owner_session_id = payload.get("owner_session_id")
        parent_branch_id = payload.get("parent_branch_id")
        title = payload.get("title")
        purpose = payload.get("purpose")
        if not isinstance(branch_id, str) or not branch_id:
            return HTTPStatus.BAD_REQUEST, {"error": "Missing branch_id"}
        if not isinstance(document_id, str) or not document_id:
            return HTTPStatus.BAD_REQUEST, {"error": "Missing document_id"}
        body, status = state.start_branch(
            branch_id=branch_id,
            document_id=document_id,
            owner_session_id=owner_session_id if isinstance(owner_session_id, str) else None,
            parent_branch_id=parent_branch_id if isinstance(parent_branch_id, str) else None,
            title=title if isinstance(title, str) else None,
            purpose=purpose if isinstance(purpose, str) else None,
        )
        return status, body

    if path == "/api/branches/finish":
        branch_id = payload.get("branch_id")
        branch_status = payload.get("status")
        if not isinstance(branch_id, str) or not branch_id:
            return HTTPStatus.BAD_REQUEST, {"error": "Missing branch_id"}
        if not isinstance(branch_status, str) or not branch_status:
            return HTTPStatus.BAD_REQUEST, {"error": "Missing status"}
        body, status = state.finish_branch(branch_id, branch_status)
        return status, body

    if path == "/api/branches/review-request":
        branch_id = payload.get("branch_id")
        requested_by_session_id = payload.get("requested_by_session_id")
        note = payload.get("note")
        if not isinstance(branch_id, str) or not branch_id:
            return HTTPStatus.BAD_REQUEST, {"error": "Missing branch_id"}
        if not isinstance(requested_by_session_id, str) or not requested_by_session_id:
            return HTTPStatus.BAD_REQUEST, {"error": "Missing requested_by_session_id"}
        body, status = state.request_branch_review(
            branch_id=branch_id,
            requested_by_session_id=requested_by_session_id,
            note=note if isinstance(note, str) else None,
        )
        return status, body

    if path == "/api/branches/review-resolve":
        branch_id = payload.get("branch_id")
        resolved_by_session_id = payload.get("resolved_by_session_id")
        resolution = payload.get("resolution")
        note = payload.get("note")
        if not isinstance(branch_id, str) or not branch_id:
            return HTTPStatus.BAD_REQUEST, {"error": "Missing branch_id"}
        if not isinstance(resolved_by_session_id, str) or not resolved_by_session_id:
            return HTTPStatus.BAD_REQUEST, {"error": "Missing resolved_by_session_id"}
        if not isinstance(resolution, str) or not resolution:
            return HTTPStatus.BAD_REQUEST, {"error": "Missing resolution"}
        body, status = state.resolve_branch_review(
            branch_id=branch_id,
            resolved_by_session_id=resolved_by_session_id,
            resolution=resolution,
            note=note if isinstance(note, str) else None,
        )
        return status, body

    return None
