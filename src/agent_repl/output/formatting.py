"""Output summarization and notebook output conversion."""
from __future__ import annotations

from typing import Any


def summarize_output(output: dict[str, Any]) -> dict[str, Any]:
    """Summarize a single notebook cell output."""
    output_type = output.get("output_type")
    if output_type == "stream":
        return {"output_type": "stream", "name": output.get("name"), "text": output.get("text", "")}
    if output_type in {"display_data", "execute_result"}:
        return {
            "output_type": output_type,
            "data": output.get("data", {}),
            "execution_count": output.get("execution_count"),
        }
    if output_type == "error":
        return {
            "output_type": "error",
            "ename": output.get("ename"),
            "evalue": output.get("evalue"),
            "traceback": output.get("traceback", []),
        }
    return output


def summarize_channel_message(msg: dict[str, Any]) -> dict[str, Any] | None:
    """Summarize a kernel channel message into a normalized event dict."""
    msg_type = msg.get("msg_type") or (msg.get("header") or {}).get("msg_type")
    content = msg.get("content") or {}
    if msg_type == "stream":
        return {"type": "stream", "name": content.get("name"), "text": content.get("text", "")}
    if msg_type == "execute_result":
        return {
            "type": "execute_result",
            "execution_count": content.get("execution_count"),
            "data": content.get("data", {}),
            "metadata": content.get("metadata", {}),
        }
    if msg_type == "display_data":
        return {"type": "display_data", "data": content.get("data", {}), "metadata": content.get("metadata", {})}
    if msg_type == "error":
        return {
            "type": "error",
            "ename": content.get("ename"),
            "evalue": content.get("evalue"),
            "traceback": content.get("traceback", []),
        }
    if msg_type == "execute_input":
        return {"type": "execute_input", "execution_count": content.get("execution_count"), "code": content.get("code", "")}
    if msg_type == "status":
        return {"type": "status", "execution_state": content.get("execution_state")}
    return None


def events_to_notebook_outputs(
    events: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int | None]:
    """Convert execution events to notebook cell output format."""
    outputs: list[dict[str, Any]] = []
    execution_count: int | None = None
    for event in events:
        etype = event.get("type")
        if etype == "execute_input":
            execution_count = event.get("execution_count")
        elif etype == "stream":
            outputs.append({"output_type": "stream", "name": event.get("name", "stdout"), "text": event.get("text", "")})
        elif etype == "execute_result":
            execution_count = event.get("execution_count") or execution_count
            outputs.append({
                "output_type": "execute_result",
                "data": event.get("data", {}),
                "metadata": event.get("metadata", {}),
                "execution_count": event.get("execution_count"),
            })
        elif etype == "display_data":
            outputs.append({"output_type": "display_data", "data": event.get("data", {}), "metadata": event.get("metadata", {})})
        elif etype == "error":
            outputs.append({
                "output_type": "error",
                "ename": event.get("ename", ""),
                "evalue": event.get("evalue", ""),
                "traceback": event.get("traceback", []),
            })
    return outputs, execution_count
