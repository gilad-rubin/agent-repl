"""Minimal workspace-scoped HTTP daemon for the experimental v2 core."""
from __future__ import annotations

import json
import os
import secrets
import threading
import time
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


V2_VERSION = "0.1.0"


@dataclass
class CoreState:
    workspace_root: str
    runtime_dir: str
    token: str
    pid: int
    started_at: float
    version: str = V2_VERSION
    documents: int = 0
    sessions: int = 0
    runs: int = 0
    runtime_file: str | None = None

    def health_payload(self) -> dict[str, Any]:
        return {
            "status": "ok",
            "mode": "v2",
            "workspace_root": self.workspace_root,
            "pid": self.pid,
            "started_at": self.started_at,
            "version": self.version,
            "documents": self.documents,
            "sessions": self.sessions,
            "runs": self.runs,
        }

    def status_payload(self) -> dict[str, Any]:
        payload = self.health_payload()
        payload["runtime_dir"] = self.runtime_dir
        payload["capabilities"] = [
            "workspace-scope",
            "core-authority",
            "session-ready",
            "runtime-ready",
        ]
        return payload


def serve_forever(
    workspace_root: str,
    *,
    runtime_dir: str,
    token: str | None = None,
    port: int = 0,
) -> None:
    workspace_root = os.path.realpath(workspace_root)
    runtime_dir = os.path.realpath(runtime_dir)
    token = token or secrets.token_hex(24)
    state = CoreState(
        workspace_root=workspace_root,
        runtime_dir=runtime_dir,
        token=token,
        pid=os.getpid(),
        started_at=time.time(),
    )
    Path(runtime_dir).mkdir(parents=True, exist_ok=True)

    server = ThreadingHTTPServer(("127.0.0.1", port), _handler_factory(state))
    actual_port = server.server_address[1]
    runtime_file = os.path.join(runtime_dir, f"agent-repl-v2-core-{state.pid}.json")
    state.runtime_file = runtime_file
    Path(runtime_file).write_text(json.dumps({
        "pid": state.pid,
        "port": actual_port,
        "token": token,
        "version": state.version,
        "workspace_root": workspace_root,
        "started_at": state.started_at,
    }))

    try:
        server.serve_forever(poll_interval=0.2)
    finally:
        try:
            server.server_close()
        finally:
            if state.runtime_file:
                try:
                    os.unlink(state.runtime_file)
                except OSError:
                    pass


def _handler_factory(state: CoreState):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            if not _authorized(self.headers.get("Authorization"), state.token):
                self._json(HTTPStatus.UNAUTHORIZED, {"error": "Unauthorized"})
                return

            if self.path == "/api/health":
                self._json(HTTPStatus.OK, state.health_payload())
                return
            if self.path == "/api/status":
                self._json(HTTPStatus.OK, state.status_payload())
                return

            self._json(HTTPStatus.NOT_FOUND, {"error": "Not found"})

        def do_POST(self) -> None:  # noqa: N802
            if not _authorized(self.headers.get("Authorization"), state.token):
                self._json(HTTPStatus.UNAUTHORIZED, {"error": "Unauthorized"})
                return

            if self.path == "/api/shutdown":
                self._json(HTTPStatus.OK, {"status": "ok", "stopping": True, "pid": state.pid})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return

            self._json(HTTPStatus.NOT_FOUND, {"error": "Not found"})

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
            # Keep the daemon quiet unless we decide to surface structured logs later.
            return

        def _json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status.value)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return Handler


def _authorized(header: str | None, token: str) -> bool:
    return header == f"token {token}"

