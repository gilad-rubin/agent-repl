"""Minimal workspace-scoped HTTP daemon for the experimental v2 core."""
from __future__ import annotations

import json
import os
import secrets
import threading
import time
import uuid
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


V2_VERSION = "0.1.0"


@dataclass
class SessionRecord:
    session_id: str
    actor: str
    client: str
    label: str | None
    created_at: float
    last_seen_at: float

    def payload(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "actor": self.actor,
            "client": self.client,
            "label": self.label,
            "created_at": self.created_at,
            "last_seen_at": self.last_seen_at,
        }


@dataclass
class DocumentRecord:
    document_id: str
    path: str
    created_at: float
    updated_at: float

    def payload(self) -> dict[str, Any]:
        return {
            "document_id": self.document_id,
            "path": self.path,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


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
    session_records: dict[str, SessionRecord] = field(default_factory=dict)
    document_records: dict[str, DocumentRecord] = field(default_factory=dict)

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
        self.documents = len(self.document_records)
        self.sessions = len(self.session_records)
        payload = self.health_payload()
        payload["runtime_dir"] = self.runtime_dir
        payload["capabilities"] = [
            "workspace-scope",
            "core-authority",
            "session-ready",
            "runtime-ready",
        ]
        return payload

    def list_sessions_payload(self) -> dict[str, Any]:
        self.sessions = len(self.session_records)
        return {
            "status": "ok",
            "sessions": [record.payload() for record in self.session_records.values()],
            "count": self.sessions,
            "workspace_root": self.workspace_root,
        }

    def start_session(self, actor: str, client: str, label: str | None, session_id: str) -> dict[str, Any]:
        now = time.time()
        existing = self.session_records.get(session_id)
        if existing is None:
            record = SessionRecord(
                session_id=session_id,
                actor=actor,
                client=client,
                label=label,
                created_at=now,
                last_seen_at=now,
            )
            self.session_records[session_id] = record
            created = True
        else:
            existing.actor = actor
            existing.client = client
            existing.label = label
            existing.last_seen_at = now
            record = existing
            created = False
        self.sessions = len(self.session_records)
        return {
            "status": "ok",
            "created": created,
            "session": record.payload(),
            "workspace_root": self.workspace_root,
        }

    def end_session(self, session_id: str) -> tuple[dict[str, Any], HTTPStatus]:
        record = self.session_records.pop(session_id, None)
        self.sessions = len(self.session_records)
        if record is None:
            return {"error": f"Unknown session_id: {session_id}"}, HTTPStatus.NOT_FOUND
        return {
            "status": "ok",
            "ended": True,
            "session_id": session_id,
            "workspace_root": self.workspace_root,
        }, HTTPStatus.OK

    def list_documents_payload(self) -> dict[str, Any]:
        self.documents = len(self.document_records)
        return {
            "status": "ok",
            "documents": [record.payload() for record in self.document_records.values()],
            "count": self.documents,
            "workspace_root": self.workspace_root,
        }

    def open_document(self, path: str) -> tuple[dict[str, Any], HTTPStatus]:
        real_path = os.path.realpath(os.path.join(self.workspace_root, path) if not os.path.isabs(path) else path)
        if not _path_within(real_path, self.workspace_root):
            return {"error": f"Path is outside workspace: {path}"}, HTTPStatus.BAD_REQUEST

        now = time.time()
        for record in self.document_records.values():
            if record.path == real_path:
                record.updated_at = now
                return {
                    "status": "ok",
                    "created": False,
                    "document": record.payload(),
                    "workspace_root": self.workspace_root,
                }, HTTPStatus.OK

        record = DocumentRecord(
            document_id=str(uuid.uuid4()),
            path=real_path,
            created_at=now,
            updated_at=now,
        )
        self.document_records[record.document_id] = record
        self.documents = len(self.document_records)
        return {
            "status": "ok",
            "created": True,
            "document": record.payload(),
            "workspace_root": self.workspace_root,
        }, HTTPStatus.OK


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
            if self.path == "/api/sessions":
                self._json(HTTPStatus.OK, state.list_sessions_payload())
                return
            if self.path == "/api/documents":
                self._json(HTTPStatus.OK, state.list_documents_payload())
                return

            self._json(HTTPStatus.NOT_FOUND, {"error": "Not found"})

        def do_POST(self) -> None:  # noqa: N802
            if not _authorized(self.headers.get("Authorization"), state.token):
                self._json(HTTPStatus.UNAUTHORIZED, {"error": "Unauthorized"})
                return

            payload = self._body()

            if self.path == "/api/shutdown":
                self._json(HTTPStatus.OK, {"status": "ok", "stopping": True, "pid": state.pid})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return
            if self.path == "/api/sessions/start":
                actor = payload.get("actor")
                client = payload.get("client")
                session_id = payload.get("session_id")
                label = payload.get("label")
                if not isinstance(actor, str) or not actor:
                    self._json(HTTPStatus.BAD_REQUEST, {"error": "Missing actor"})
                    return
                if not isinstance(client, str) or not client:
                    self._json(HTTPStatus.BAD_REQUEST, {"error": "Missing client"})
                    return
                if not isinstance(session_id, str) or not session_id:
                    self._json(HTTPStatus.BAD_REQUEST, {"error": "Missing session_id"})
                    return
                self._json(HTTPStatus.OK, state.start_session(actor, client, label, session_id))
                return
            if self.path == "/api/sessions/end":
                session_id = payload.get("session_id")
                if not isinstance(session_id, str) or not session_id:
                    self._json(HTTPStatus.BAD_REQUEST, {"error": "Missing session_id"})
                    return
                body, status = state.end_session(session_id)
                self._json(status, body)
                return
            if self.path == "/api/documents/open":
                path = payload.get("path")
                if not isinstance(path, str) or not path:
                    self._json(HTTPStatus.BAD_REQUEST, {"error": "Missing path"})
                    return
                body, status = state.open_document(path)
                self._json(status, body)
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

        def _body(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0:
                return {}
            raw = self.rfile.read(length)
            if not raw:
                return {}
            try:
                data = json.loads(raw.decode("utf-8"))
            except ValueError:
                return {}
            return data if isinstance(data, dict) else {}

    return Handler


def _authorized(header: str | None, token: str) -> bool:
    return header == f"token {token}"


def _path_within(candidate: str, root: str) -> bool:
    try:
        common = os.path.commonpath([os.path.realpath(candidate), os.path.realpath(root)])
        return common == os.path.realpath(root)
    except ValueError:
        return False
