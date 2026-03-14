"""Streaming execution — yield events as JSONL as they arrive."""
from __future__ import annotations

import json
import time
import uuid
from typing import Any, Iterator
from urllib.parse import urlencode, urljoin

import websocket

from agent_repl.core import CommandError, ServerClient, ServerInfo, ExecuteRequest
from agent_repl.execution.transport import _belongs_to_execution, _message_type, _sanitize_error_text, _ws_url
from agent_repl.output.filtering import strip_media_from_event
from agent_repl.output.formatting import summarize_channel_message
from agent_repl.server.kernels import ensure_kernel_idle


def execute_streaming(
    server: ServerInfo,
    *,
    kernel_id: str,
    session_id: str | None,
    code: str,
    timeout: float,
    strip_media: bool = True,
) -> Iterator[dict[str, Any]]:
    """Execute code and yield events with elapsed timestamps as they arrive."""
    ensure_kernel_idle(server, kernel_id, min(timeout, 5.0))
    client = ServerClient(server, timeout=timeout)
    msg_id = uuid.uuid4().hex
    shell_session_id = uuid.uuid4().hex
    payload = {
        "header": {"msg_id": msg_id, "username": "agent", "session": shell_session_id, "msg_type": "execute_request", "version": "5.3"},
        "parent_header": {}, "metadata": {},
        "content": {
            "code": code, "silent": False, "store_history": True,
            "user_expressions": {}, "allow_stdin": False, "stop_on_error": True,
        },
        "channel": "shell", "buffers": [],
    }

    start_time = time.time()
    deadline = start_time + timeout
    ws = None
    try:
        ws = websocket.create_connection(
            _ws_url(server, kernel_id, session_id=session_id),
            header=client.websocket_headers(), timeout=timeout,
        )
        ws.send(json.dumps(payload))
        reply = None

        while time.time() < deadline:
            raw = ws.recv()
            msg = json.loads(raw)
            if not _belongs_to_execution(msg, msg_id):
                continue
            summary = summarize_channel_message(msg)
            if summary is not None:
                if strip_media:
                    summary = strip_media_from_event(summary)
                summary["elapsed"] = round(time.time() - start_time, 2)
                yield summary
            mt = _message_type(msg)
            if mt == "execute_reply":
                reply = msg.get("content", {})
            if mt == "status" and (msg.get("content") or {}).get("execution_state") == "idle" and reply:
                break
        else:
            yield {"type": "timeout", "elapsed": round(time.time() - start_time, 2), "message": f"Timed out after {timeout}s"}
    except (OSError, websocket.WebSocketException, json.JSONDecodeError) as exc:
        yield {"type": "error", "elapsed": round(time.time() - start_time, 2), "message": _sanitize_error_text(str(exc), server_token=server.token)}
    finally:
        if ws is not None:
            ws.close()
