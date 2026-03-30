"""Shared request parsing helper for HTTP route modules."""
from __future__ import annotations

from http import HTTPStatus
from typing import Any


def parse_request(payload: dict[str, Any], request_type: Any) -> Any | tuple[HTTPStatus, dict[str, Any]]:
    """Parse *payload* into *request_type* via ``from_payload()``.

    Returns the parsed request on success, or a ``(HTTPStatus, dict)``
    error tuple when validation fails.
    """
    try:
        return request_type.from_payload(payload)
    except ValueError as err:
        return HTTPStatus.BAD_REQUEST, {"error": str(err)}
