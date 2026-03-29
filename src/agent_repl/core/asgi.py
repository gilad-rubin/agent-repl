"""ASGI application shell for the core daemon."""
from __future__ import annotations

from http import HTTPStatus
from typing import Any, Callable

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route
from starlette.types import ASGIApp, Receive, Scope, Send

from agent_repl.core.collaboration_http_routes import (
    handle_collaboration_get,
    handle_collaboration_post,
)
from agent_repl.core.document_http_routes import (
    handle_document_get,
    handle_document_post,
)
from agent_repl.core.notebook_http_routes import handle_notebook_post
from agent_repl.core.runtime_http_routes import (
    handle_runtime_get,
    handle_runtime_post,
)


class TokenAuthMiddleware:
    """Reject requests without a valid bearer token."""

    def __init__(self, app: ASGIApp, *, token: str) -> None:
        self.app = app
        self.token = token

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers", []))
        auth = headers.get(b"authorization", b"").decode("utf-8", errors="replace")
        if auth != f"token {self.token}":
            response = JSONResponse({"error": "Unauthorized"}, status_code=401)
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)


async def _parse_body(request: Request) -> dict[str, Any]:
    try:
        data = await request.json()
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


GetHandler = Callable[[Any, str], tuple[HTTPStatus, dict[str, Any]] | None]
PostHandler = Callable[[Any, str, dict[str, Any]], tuple[HTTPStatus, dict[str, Any]] | None]


def create_app(
    state: Any,
    *,
    shutdown_callback: Callable[[], None] | None = None,
) -> Starlette:
    """Build the ASGI application for the core daemon.

    ``state`` is the ``CoreState`` instance that backs all routes.
    ``shutdown_callback`` is invoked when ``POST /api/shutdown`` is received.
    """

    # ------------------------------------------------------------------
    # Inline route handlers
    # ------------------------------------------------------------------

    async def health(request: Request) -> Response:
        return JSONResponse(state.health_payload())

    async def status(request: Request) -> Response:
        return JSONResponse(state.status_payload())

    async def shutdown(request: Request) -> Response:
        body = {"status": "ok", "stopping": True, "pid": state.pid}
        if shutdown_callback:
            shutdown_callback()
        return JSONResponse(body)

    # ------------------------------------------------------------------
    # Domain route adapters
    #
    # Each domain handler checks the request path internally and returns
    # (HTTPStatus, dict) on match or None when the path is not handled.
    # The adapters call through to those handlers. Individual routes
    # will be decomposed in follow-up slices.
    # ------------------------------------------------------------------

    get_handlers: list[GetHandler] = [
        handle_document_get,
        handle_collaboration_get,
        handle_runtime_get,
    ]

    post_handlers: list[PostHandler] = [
        handle_document_post,
        handle_notebook_post,
        handle_collaboration_post,
        handle_runtime_post,
    ]

    async def domain_dispatch(request: Request) -> Response:
        path = request.url.path
        try:
            if request.method == "GET":
                for handler in get_handlers:
                    result = handler(state, path)
                    if result is not None:
                        http_status, body = result
                        return JSONResponse(body, status_code=http_status.value)
            else:
                payload = await _parse_body(request)
                for handler in post_handlers:
                    result = handler(state, path, payload)
                    if result is not None:
                        http_status, body = result
                        return JSONResponse(body, status_code=http_status.value)
            return JSONResponse({"error": "Not found"}, status_code=404)
        except Exception as err:
            return JSONResponse({"error": str(err)}, status_code=500)

    # ------------------------------------------------------------------
    # Route table
    # ------------------------------------------------------------------

    routes = [
        Route("/api/health", health, methods=["GET"]),
        Route("/api/status", status, methods=["GET"]),
        Route("/api/shutdown", shutdown, methods=["POST"]),
        Route("/api/{rest:path}", domain_dispatch, methods=["GET", "POST"]),
    ]

    app = Starlette(routes=routes)
    app.add_middleware(TokenAuthMiddleware, token=state.token)

    return app
