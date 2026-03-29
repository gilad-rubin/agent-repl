"""HTTP route helpers for document-oriented core APIs."""
from __future__ import annotations

from http import HTTPStatus
from typing import Any


def handle_document_get(state: Any, path: str) -> tuple[HTTPStatus, dict[str, Any]] | None:
    if path == "/api/documents":
        return HTTPStatus.OK, state.list_documents_payload()
    return None


def handle_document_post(
    state: Any,
    path: str,
    payload: dict[str, Any],
) -> tuple[HTTPStatus, dict[str, Any]] | None:
    if path == "/api/documents/open":
        document_path = payload.get("path")
        if not isinstance(document_path, str) or not document_path:
            return HTTPStatus.BAD_REQUEST, {"error": "Missing path"}
        body, status = state.open_document(document_path)
        return status, body

    if path == "/api/documents/refresh":
        document_id = payload.get("document_id")
        if not isinstance(document_id, str) or not document_id:
            return HTTPStatus.BAD_REQUEST, {"error": "Missing document_id"}
        body, status = state.refresh_document(document_id)
        return status, body

    if path == "/api/documents/rebind":
        document_id = payload.get("document_id")
        if not isinstance(document_id, str) or not document_id:
            return HTTPStatus.BAD_REQUEST, {"error": "Missing document_id"}
        body, status = state.rebind_document(document_id)
        return status, body

    return None
