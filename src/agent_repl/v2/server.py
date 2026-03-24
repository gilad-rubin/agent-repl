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
class RuntimeRecord:
    runtime_id: str
    mode: str
    label: str | None
    environment: str | None
    status: str
    created_at: float
    updated_at: float

    def payload(self) -> dict[str, Any]:
        return {
            "runtime_id": self.runtime_id,
            "mode": self.mode,
            "label": self.label,
            "environment": self.environment,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass
class RunRecord:
    run_id: str
    runtime_id: str
    target_type: str
    target_ref: str
    kind: str
    status: str
    created_at: float
    updated_at: float

    def payload(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "runtime_id": self.runtime_id,
            "target_type": self.target_type,
            "target_ref": self.target_ref,
            "kind": self.kind,
            "status": self.status,
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
    runtime_records: dict[str, RuntimeRecord] = field(default_factory=dict)
    run_records: dict[str, RunRecord] = field(default_factory=dict)

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
        self.runs = sum(1 for record in self.run_records.values() if record.status in {"queued", "running"})
        payload = self.health_payload()
        payload["runtime_dir"] = self.runtime_dir
        payload["capabilities"] = [
            "workspace-scope",
            "core-authority",
            "session-ready",
            "runtime-ready",
            "run-ledger",
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

    def list_runtimes_payload(self) -> dict[str, Any]:
        return {
            "status": "ok",
            "runtimes": [record.payload() for record in self.runtime_records.values()],
            "count": len(self.runtime_records),
            "workspace_root": self.workspace_root,
        }

    def start_runtime(
        self,
        *,
        runtime_id: str,
        mode: str,
        label: str | None,
        environment: str | None,
    ) -> dict[str, Any]:
        now = time.time()
        existing = self.runtime_records.get(runtime_id)
        if existing is None:
            record = RuntimeRecord(
                runtime_id=runtime_id,
                mode=mode,
                label=label,
                environment=environment,
                status="ready",
                created_at=now,
                updated_at=now,
            )
            self.runtime_records[runtime_id] = record
            created = True
        else:
            existing.mode = mode
            existing.label = label
            existing.environment = environment
            existing.status = "ready"
            existing.updated_at = now
            record = existing
            created = False
        return {
            "status": "ok",
            "created": created,
            "runtime": record.payload(),
            "workspace_root": self.workspace_root,
        }

    def stop_runtime(self, runtime_id: str) -> tuple[dict[str, Any], HTTPStatus]:
        record = self.runtime_records.get(runtime_id)
        if record is None:
            return {"error": f"Unknown runtime_id: {runtime_id}"}, HTTPStatus.NOT_FOUND
        record.status = "stopped"
        record.updated_at = time.time()
        return {
            "status": "ok",
            "runtime": record.payload(),
            "workspace_root": self.workspace_root,
        }, HTTPStatus.OK

    def list_runs_payload(self) -> dict[str, Any]:
        self.runs = sum(1 for record in self.run_records.values() if record.status in {"queued", "running"})
        return {
            "status": "ok",
            "runs": [record.payload() for record in self.run_records.values()],
            "count": len(self.run_records),
            "active_count": self.runs,
            "workspace_root": self.workspace_root,
        }

    def start_run(
        self,
        *,
        run_id: str,
        runtime_id: str,
        target_type: str,
        target_ref: str,
        kind: str,
    ) -> tuple[dict[str, Any], HTTPStatus]:
        runtime = self.runtime_records.get(runtime_id)
        if runtime is None:
            return {"error": f"Unknown runtime_id: {runtime_id}"}, HTTPStatus.BAD_REQUEST
        if runtime.status == "stopped":
            return {"error": f"Runtime is stopped: {runtime_id}"}, HTTPStatus.BAD_REQUEST
        now = time.time()
        record = RunRecord(
            run_id=run_id,
            runtime_id=runtime_id,
            target_type=target_type,
            target_ref=target_ref,
            kind=kind,
            status="running",
            created_at=now,
            updated_at=now,
        )
        self.run_records[run_id] = record
        runtime.status = "busy"
        runtime.updated_at = now
        self.runs = sum(1 for item in self.run_records.values() if item.status in {"queued", "running"})
        return {
            "status": "ok",
            "run": record.payload(),
            "workspace_root": self.workspace_root,
        }, HTTPStatus.OK

    def finish_run(self, run_id: str, status: str) -> tuple[dict[str, Any], HTTPStatus]:
        record = self.run_records.get(run_id)
        if record is None:
            return {"error": f"Unknown run_id: {run_id}"}, HTTPStatus.NOT_FOUND
        if status not in {"completed", "failed", "interrupted"}:
            return {"error": f"Invalid run status: {status}"}, HTTPStatus.BAD_REQUEST
        now = time.time()
        record.status = status
        record.updated_at = now
        runtime = self.runtime_records.get(record.runtime_id)
        if runtime is not None:
            runtime.status = "ready"
            runtime.updated_at = now
        self.runs = sum(1 for item in self.run_records.values() if item.status in {"queued", "running"})
        return {
            "status": "ok",
            "run": record.payload(),
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
            if self.path == "/api/runtimes":
                self._json(HTTPStatus.OK, state.list_runtimes_payload())
                return
            if self.path == "/api/runs":
                self._json(HTTPStatus.OK, state.list_runs_payload())
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
            if self.path == "/api/runtimes/start":
                runtime_id = payload.get("runtime_id")
                mode = payload.get("mode")
                label = payload.get("label")
                environment = payload.get("environment")
                if not isinstance(runtime_id, str) or not runtime_id:
                    self._json(HTTPStatus.BAD_REQUEST, {"error": "Missing runtime_id"})
                    return
                if not isinstance(mode, str) or mode not in {"interactive", "shared", "headless", "pinned", "ephemeral"}:
                    self._json(HTTPStatus.BAD_REQUEST, {"error": "Invalid mode"})
                    return
                self._json(HTTPStatus.OK, state.start_runtime(
                    runtime_id=runtime_id,
                    mode=mode,
                    label=label if isinstance(label, str) else None,
                    environment=environment if isinstance(environment, str) else None,
                ))
                return
            if self.path == "/api/runtimes/stop":
                runtime_id = payload.get("runtime_id")
                if not isinstance(runtime_id, str) or not runtime_id:
                    self._json(HTTPStatus.BAD_REQUEST, {"error": "Missing runtime_id"})
                    return
                body, status = state.stop_runtime(runtime_id)
                self._json(status, body)
                return
            if self.path == "/api/runs/start":
                run_id = payload.get("run_id")
                runtime_id = payload.get("runtime_id")
                target_type = payload.get("target_type")
                target_ref = payload.get("target_ref")
                kind = payload.get("kind")
                if not isinstance(run_id, str) or not run_id:
                    self._json(HTTPStatus.BAD_REQUEST, {"error": "Missing run_id"})
                    return
                if not isinstance(runtime_id, str) or not runtime_id:
                    self._json(HTTPStatus.BAD_REQUEST, {"error": "Missing runtime_id"})
                    return
                if not isinstance(target_type, str) or target_type not in {"document", "node", "branch"}:
                    self._json(HTTPStatus.BAD_REQUEST, {"error": "Invalid target_type"})
                    return
                if not isinstance(target_ref, str) or not target_ref:
                    self._json(HTTPStatus.BAD_REQUEST, {"error": "Missing target_ref"})
                    return
                if not isinstance(kind, str) or not kind:
                    self._json(HTTPStatus.BAD_REQUEST, {"error": "Missing kind"})
                    return
                body, status = state.start_run(
                    run_id=run_id,
                    runtime_id=runtime_id,
                    target_type=target_type,
                    target_ref=target_ref,
                    kind=kind,
                )
                self._json(status, body)
                return
            if self.path == "/api/runs/finish":
                run_id = payload.get("run_id")
                run_status = payload.get("status")
                if not isinstance(run_id, str) or not run_id:
                    self._json(HTTPStatus.BAD_REQUEST, {"error": "Missing run_id"})
                    return
                if not isinstance(run_status, str) or not run_status:
                    self._json(HTTPStatus.BAD_REQUEST, {"error": "Missing status"})
                    return
                body, status = state.finish_run(run_id, run_status)
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
