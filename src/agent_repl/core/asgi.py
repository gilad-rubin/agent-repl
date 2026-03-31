"""ASGI application shell for the core daemon."""
from __future__ import annotations

import urllib.parse
from typing import Any, Callable

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response
from starlette.routing import Mount, Route, WebSocketRoute
from starlette.types import ASGIApp, Receive, Scope, Send
from starlette.websockets import WebSocket, WebSocketDisconnect

from agent_repl.core.collaboration_http_routes import routes as collaboration_routes
from agent_repl.core.document_http_routes import routes as document_routes
from agent_repl.core.notebook_http_routes import routes as notebook_routes
from agent_repl.core.runtime_http_routes import routes as runtime_routes


class TokenAuthMiddleware:
    """Reject requests without a valid bearer token.

    HTTP requests require ``Authorization: token <token>``.
    WebSocket upgrades authenticate via a single-use nonce in the query string
    (``?nonce=<nonce>``), validated against the ``WebSocketTransport``.
    """

    def __init__(self, app: ASGIApp, *, token: str, ws_transport: Any = None) -> None:
        self.app = app
        self.token = token
        self._ws_transport = ws_transport

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            headers = dict(scope.get("headers", []))
            auth = headers.get(b"authorization", b"").decode("utf-8", errors="replace")
            if auth != f"token {self.token}":
                response = JSONResponse({"error": "Unauthorized"}, status_code=401)
                await response(scope, receive, send)
                return
        elif scope["type"] == "websocket":
            qs = scope.get("query_string", b"").decode("utf-8", errors="replace")
            params = urllib.parse.parse_qs(qs)
            nonce = (params.get("nonce") or [None])[0]
            if not nonce or not self._ws_transport or not self._ws_transport.redeem_nonce(nonce):
                # Reject: accept then immediately close with policy-violation code
                ws = WebSocket(scope, receive, send)
                await ws.accept()
                await ws.close(code=4401, reason="Unauthorized")
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

    async def legacy_mcp_redirect(request: Request) -> Response:
        destination = "/mcp"
        if request.url.query:
            destination = f"{destination}?{request.url.query}"
        return RedirectResponse(destination, status_code=307)

    # ------------------------------------------------------------------
    # WebSocket nonce endpoint
    # ------------------------------------------------------------------

    async def ws_nonce(request: Request) -> Response:
        transport = getattr(state, "_ws_transport", None)
        if transport is None:
            return JSONResponse({"error": "WebSocket transport not available"}, status_code=503)
        nonce = transport.create_nonce()
        return JSONResponse({"nonce": nonce})

    # ------------------------------------------------------------------
    # WebSocket handler
    # ------------------------------------------------------------------

    async def ws_handler(ws: WebSocket) -> None:
        transport = getattr(state, "_ws_transport", None)
        if transport is None:
            await ws.close(code=4503, reason="WebSocket transport not available")
            return
        qs = ws.query_params
        last_cursor = int(qs.get("last_cursor", "0") or "0")
        await transport.accept(ws, last_cursor=last_cursor)
        try:
            while True:
                data = await ws.receive_json()
                await transport.handle_client_message(ws, data)
        except WebSocketDisconnect:
            pass
        finally:
            transport.disconnect(ws)

    # ------------------------------------------------------------------
    # MCP adapter
    # ------------------------------------------------------------------

    from agent_repl.core.mcp_adapter import create_mcp_server

    mcp_server = create_mcp_server(state)
    mcp_app = mcp_server.http_app(path="/")

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
        Route("/api/ws-nonce", ws_nonce, methods=["POST"]),
        WebSocketRoute("/ws", ws_handler),
        Route("/mcp/mcp", legacy_mcp_redirect, methods=["GET", "POST", "DELETE"]),
        Route("/mcp/mcp/", legacy_mcp_redirect, methods=["GET", "POST", "DELETE"]),
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

    ws_transport = getattr(state, "_ws_transport", None)
    app.add_middleware(TokenAuthMiddleware, token=state.token, ws_transport=ws_transport)

    return app
