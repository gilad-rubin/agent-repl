"""Media stripping for CLI output — keep rich data in notebook, strip for agents."""
from __future__ import annotations

from typing import Any

_IMAGE_MIME_TYPES = frozenset({"image/png", "image/jpeg", "image/svg+xml"})
_WIDGET_MIME_PREFIX = "application/vnd.jupyter.widget"


def strip_media_from_data(data: dict[str, Any]) -> dict[str, Any]:
    """Filter a MIME bundle: drop HTML when text/plain exists, replace images/widgets with placeholders."""
    result: dict[str, Any] = {}
    has_plain = "text/plain" in data
    for mime, value in data.items():
        if mime == "text/html" and has_plain:
            continue
        if mime in _IMAGE_MIME_TYPES:
            result[mime] = f"[image: {mime}]"
        elif mime.startswith(_WIDGET_MIME_PREFIX):
            result[mime] = "[widget]"
        else:
            result[mime] = value
    return result


def strip_media_from_event(event: dict[str, Any]) -> dict[str, Any]:
    """Strip rich media from an execution event dict."""
    etype = event.get("type")
    if etype in ("display_data", "execute_result") and "data" in event:
        return {**event, "data": strip_media_from_data(event["data"])}
    return event


def strip_media_from_output(output: dict[str, Any]) -> dict[str, Any]:
    """Strip rich media from a notebook cell output dict."""
    output_type = output.get("output_type")
    if output_type in ("display_data", "execute_result") and "data" in output:
        return {**output, "data": strip_media_from_data(output["data"])}
    return output
