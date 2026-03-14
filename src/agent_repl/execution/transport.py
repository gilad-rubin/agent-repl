"""WebSocket and ZMQ transport implementations for kernel execution."""
from __future__ import annotations

import json
import re
import time
import uuid
from typing import Any
from urllib.parse import urlencode, urljoin

import requests
import websocket

from agent_repl.core import CommandError, ServerClient, ServerInfo, TransportRetryUnsafeError, ExecuteRequest, ExecutionResult, KernelTarget
from agent_repl.output.formatting import summarize_channel_message
from agent_repl.server.kernels import ensure_kernel_idle


def _message_type(msg: dict[str, Any]) -> str | None:
    return msg.get("msg_type") or (msg.get("header") or {}).get("msg_type")


def _belongs_to_execution(msg: dict[str, Any], msg_id: str) -> bool:
    return (msg.get("parent_header") or {}).get("msg_id") == msg_id


def _ws_url(server: ServerInfo, kernel_id: str, *, session_id: str | None = None) -> str:
    base = urljoin(server.ws_root_url, f"api/kernels/{kernel_id}/channels")
    params: dict[str, str] = {}
    if server.token:
        params["token"] = server.token
    if session_id:
        params["session_id"] = session_id
    if not params:
        return base
    separator = "&" if "?" in base else "?"
    return f"{base}{separator}{urlencode(params)}"


def _collect_output(events: list[dict[str, Any]], msg: dict[str, Any]) -> None:
    summary = summarize_channel_message(msg)
    if summary is not None:
        events.append(summary)


def _sanitize_error_text(text: str, *, server_token: str | None = None) -> str:
    redacted = re.sub(r'([?&]token=)([^&\s]+)', r'\1[REDACTED]', text)
    if server_token:
        redacted = redacted.replace(server_token, "[REDACTED]")
    return redacted


def execute_via_websocket(
    server: ServerInfo, *, kernel_id: str, session_id: str | None, path: str | None,
    request: ExecuteRequest, timeout: float | None,
) -> ExecutionResult:
    ensure_kernel_idle(server, kernel_id, 5.0)
    client = ServerClient(server, timeout=timeout or 10.0)
    msg_id = uuid.uuid4().hex
    shell_session_id = uuid.uuid4().hex
    payload = {
        "header": {"msg_id": msg_id, "username": "agent", "session": shell_session_id, "msg_type": "execute_request", "version": "5.3"},
        "parent_header": {}, "metadata": {},
        "content": {
            "code": request.code, "silent": request.silent, "store_history": request.store_history,
            "user_expressions": request.user_expressions, "allow_stdin": False, "stop_on_error": request.stop_on_error,
        },
        "channel": "shell", "buffers": [],
    }

    reply: dict[str, Any] | None = None
    events: list[dict[str, Any]] = []
    deadline = (time.time() + timeout) if timeout else None
    request_sent = False
    ws = None
    try:
        ws = websocket.create_connection(
            _ws_url(server, kernel_id, session_id=shell_session_id),
            header=client.websocket_headers(), timeout=10.0,
        )
        ws.send(json.dumps(payload))
        request_sent = True
        ws.settimeout(30.0)  # short recv timeout to detect dead connections quickly

        while deadline is None or time.time() < deadline:
            try:
                raw = ws.recv()
            except websocket.WebSocketTimeoutException:
                continue  # connection alive, no message yet; keep waiting
            msg = json.loads(raw)
            if not _belongs_to_execution(msg, msg_id):
                continue
            summary = summarize_channel_message(msg)
            if summary is not None:
                events.append(summary)
            mt = _message_type(msg)
            if mt == "execute_reply":
                reply = msg.get("content", {})
            if mt == "status" and (msg.get("content") or {}).get("execution_state") == "idle" and reply:
                break
        else:
            # Timeout — interrupt the kernel and raise a clear error
            try:
                ServerClient(server, timeout=5).request("POST", f"api/kernels/{kernel_id}/interrupt")
            except Exception:
                pass
            raise CommandError(
                f"Cell execution timed out after {timeout}s. "
                f"The kernel was interrupted. To allow more time, pass --timeout with a larger value, "
                f"or omit --timeout to wait indefinitely."
            )
    except CommandError as exc:
        if request_sent:
            raise TransportRetryUnsafeError(_sanitize_error_text(str(exc), server_token=server.token), request_sent=True) from exc
        raise
    except (OSError, websocket.WebSocketException, json.JSONDecodeError) as exc:
        raise TransportRetryUnsafeError(_sanitize_error_text(str(exc), server_token=server.token), request_sent=request_sent) from exc
    finally:
        if ws is not None:
            ws.close()

    return ExecutionResult(transport="websocket", kernel_id=kernel_id, session_id=session_id, path=path, reply=reply or {}, events=events)


def execute_via_zmq(*, kernel_id: str, session_id: str | None, path: str | None, request: ExecuteRequest, timeout: float | None) -> ExecutionResult:
    from jupyter_client import BlockingKernelClient
    from jupyter_client.connect import find_connection_file

    connection_file = find_connection_file(f"kernel-{kernel_id}.json")
    client = BlockingKernelClient(connection_file=connection_file)
    client.load_connection_file(connection_file)
    client.start_channels()
    events: list[dict[str, Any]] = []
    try:
        client.wait_for_ready(timeout=timeout or 10.0)
        reply_msg = client.execute_interactive(
            request.code, silent=request.silent, store_history=request.store_history,
            user_expressions=request.user_expressions, allow_stdin=False,
            stop_on_error=request.stop_on_error, timeout=timeout,
            output_hook=lambda msg: _collect_output(events, msg),
        )
    finally:
        client.stop_channels()

    return ExecutionResult(transport="zmq", kernel_id=kernel_id, session_id=session_id, path=path, reply=(reply_msg.get("content") or {}), events=events)


def execute_request_with_target(
    server: ServerInfo, *, target: KernelTarget, request: ExecuteRequest, transport: str, timeout: float | None,
) -> ExecutionResult:
    attempts = ["websocket", "zmq"] if transport == "auto" else [transport]
    last_error: Exception | None = None
    for attempt in attempts:
        try:
            if attempt == "websocket":
                return execute_via_websocket(server, kernel_id=target.kernel_id, session_id=target.session_id, path=target.path, request=request, timeout=timeout)
            elif attempt == "zmq":
                return execute_via_zmq(kernel_id=target.kernel_id, session_id=target.session_id, path=target.path, request=request, timeout=timeout)
            else:
                raise CommandError(f"Unknown transport {attempt!r}.")
        except TransportRetryUnsafeError as exc:
            if transport == "auto" and exc.request_sent:
                raise CommandError("Websocket execution may already have reached the kernel, so auto fallback was skipped to avoid running the code twice.") from exc
            last_error = exc
            continue
        except (CommandError, requests.RequestException, OSError, RuntimeError) as exc:
            last_error = exc
            continue

    raise CommandError(f"Execution failed for all transports: {last_error}")
