"""HTTP route helpers for document-oriented core APIs."""
from __future__ import annotations

from http import HTTPStatus
from typing import Any

from agent_repl.core.document_requests import (
    DocumentOpenRequest,
    DocumentRebindRequest,
    DocumentRefreshRequest,
)
from agent_repl.core.request_parsing import parse_request


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
        request = parse_request(payload, DocumentOpenRequest)
        if isinstance(request, tuple):
            return request
        body, status = state.open_document(request.path)
        return status, body

    if path == "/api/documents/refresh":
        request = parse_request(payload, DocumentRefreshRequest)
        if isinstance(request, tuple):
            return request
        body, status = state.refresh_document(request.document_id)
        return status, body

    if path == "/api/documents/rebind":
        request = parse_request(payload, DocumentRebindRequest)
        if isinstance(request, tuple):
            return request
        body, status = state.rebind_document(request.document_id)
        return status, body

    return None
