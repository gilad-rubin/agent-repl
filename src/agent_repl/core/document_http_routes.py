"""HTTP route helpers for document-oriented core APIs."""
from __future__ import annotations

from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from agent_repl.core.document_requests import (
    DocumentOpenRequest,
    DocumentRebindRequest,
    DocumentRefreshRequest,
)
from agent_repl.core.request_parsing import parse_request
from agent_repl.core.route_helpers import parse_body


def routes(state: Any) -> list[Route]:
    """Return Starlette routes for document endpoints."""

    async def documents_list(request: Request) -> JSONResponse:
        return JSONResponse(state.list_documents_payload())

    async def documents_open(request: Request) -> JSONResponse:
        payload = await parse_body(request)
        request_obj = parse_request(payload, DocumentOpenRequest)
        if isinstance(request_obj, tuple):
            return JSONResponse(request_obj[1], status_code=request_obj[0].value)
        body, status = state.open_document(request_obj.path)
        return JSONResponse(body, status_code=status.value)

    async def documents_refresh(request: Request) -> JSONResponse:
        payload = await parse_body(request)
        request_obj = parse_request(payload, DocumentRefreshRequest)
        if isinstance(request_obj, tuple):
            return JSONResponse(request_obj[1], status_code=request_obj[0].value)
        body, status = state.refresh_document(request_obj.document_id)
        return JSONResponse(body, status_code=status.value)

    async def documents_rebind(request: Request) -> JSONResponse:
        payload = await parse_body(request)
        request_obj = parse_request(payload, DocumentRebindRequest)
        if isinstance(request_obj, tuple):
            return JSONResponse(request_obj[1], status_code=request_obj[0].value)
        body, status = state.rebind_document(request_obj.document_id)
        return JSONResponse(body, status_code=status.value)

    return [
        Route("/api/documents", documents_list, methods=["GET"]),
        Route("/api/documents/open", documents_open, methods=["POST"]),
        Route("/api/documents/refresh", documents_refresh, methods=["POST"]),
        Route("/api/documents/rebind", documents_rebind, methods=["POST"]),
    ]
