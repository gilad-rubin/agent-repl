"""ASGI application shell for the core daemon."""
from __future__ import annotations

from typing import Any, Callable

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Mount, Route
from starlette.types import ASGIApp, Receive, Scope, Send

from agent_repl.core.collaboration_http_routes import routes as collaboration_routes
from agent_repl.core.document_http_routes import routes as document_routes
from agent_repl.core.notebook_http_routes import routes as notebook_routes
from agent_repl.core.runtime_http_routes import routes as runtime_routes


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
    # MCP adapter
    # ------------------------------------------------------------------

    from agent_repl.core.mcp_adapter import create_mcp_server

    mcp_server = create_mcp_server(state)
    mcp_app = mcp_server.http_app(path="/mcp")

    # ------------------------------------------------------------------
    # Route table — explicit per-path routes from domain modules
    # ------------------------------------------------------------------

    domain_routes: list[Route] = [
        *notebook_routes(state),
        *collaboration_routes(state),
        *document_routes(state),
        *runtime_routes(state),
    ]

    all_routes = [
        Route("/api/health", health, methods=["GET"]),
        Route("/api/status", status, methods=["GET"]),
        Route("/api/shutdown", shutdown, methods=["POST"]),
        *domain_routes,
        Mount("/mcp", app=mcp_app),
    ]

    async def _handle_server_error(request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse({"error": str(exc)}, status_code=500)

    app = Starlette(
        routes=all_routes,
        lifespan=mcp_app.lifespan,
        exception_handlers={500: _handle_server_error},
    )
    app.add_middleware(TokenAuthMiddleware, token=state.token)

    return app
