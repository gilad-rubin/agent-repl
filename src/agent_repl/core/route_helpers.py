"""Shared helpers for ASGI route modules."""
from __future__ import annotations

from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse


async def parse_body(request: Request) -> dict[str, Any]:
    """Parse the JSON body from a Starlette request, returning {} on failure."""
    try:
        data = await request.json()
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def make_route_handler(handler_fn: Any) -> Any:
    """Identity passthrough — reserved for future middleware wrapping."""
    return handler_fn
