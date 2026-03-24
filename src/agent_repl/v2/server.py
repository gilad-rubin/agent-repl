"""Minimal workspace-scoped HTTP daemon for the experimental v2 core."""
from __future__ import annotations

import hashlib
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

from agent_repl.client import BridgeClient


V2_VERSION = "0.1.0"
SESSION_STALE_AFTER_SECONDS = 60.0
STATE_DIRNAME = ".agent-repl"
STATE_FILENAME = "v2-core-state.json"


@dataclass
class SessionRecord:
    session_id: str
    actor: str
    client: str
    label: str | None
    status: str
    capabilities: list[str]
    resume_count: int
    created_at: float
    last_seen_at: float

    def payload(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "actor": self.actor,
            "client": self.client,
            "label": self.label,
            "status": self.status,
            "capabilities": list(self.capabilities),
            "resume_count": self.resume_count,
            "created_at": self.created_at,
            "last_seen_at": self.last_seen_at,
        }


@dataclass
class DocumentRecord:
    document_id: str
    path: str
    relative_path: str
    file_format: str
    sync_state: str
    bound_snapshot: dict[str, Any] | None
    observed_snapshot: dict[str, Any] | None
    created_at: float
    updated_at: float

    def payload(self) -> dict[str, Any]:
        return {
            "document_id": self.document_id,
            "path": self.path,
            "relative_path": self.relative_path,
            "binding_kind": "file",
            "file_format": self.file_format,
            "sync_state": self.sync_state,
            "bound_snapshot": self.bound_snapshot,
            "observed_snapshot": self.observed_snapshot,
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
class BranchRecord:
    branch_id: str
    document_id: str
    owner_session_id: str | None
    parent_branch_id: str | None
    title: str | None
    purpose: str | None
    status: str
    created_at: float
    updated_at: float

    def payload(self) -> dict[str, Any]:
        return {
            "branch_id": self.branch_id,
            "document_id": self.document_id,
            "owner_session_id": self.owner_session_id,
            "parent_branch_id": self.parent_branch_id,
            "title": self.title,
            "purpose": self.purpose,
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
    state_file: str | None = None
    version: str = V2_VERSION
    documents: int = 0
    sessions: int = 0
    runs: int = 0
    runtime_file: str | None = None
    session_records: dict[str, SessionRecord] = field(default_factory=dict)
    document_records: dict[str, DocumentRecord] = field(default_factory=dict)
    branch_records: dict[str, BranchRecord] = field(default_factory=dict)
    runtime_records: dict[str, RuntimeRecord] = field(default_factory=dict)
    run_records: dict[str, RunRecord] = field(default_factory=dict)
    _lock: threading.RLock = field(default_factory=threading.RLock, init=False, repr=False)

    def __post_init__(self) -> None:
        self.workspace_root = os.path.realpath(self.workspace_root)
        self.runtime_dir = os.path.realpath(self.runtime_dir)
        if self.state_file is None:
            self.state_file = _state_file_path(self.workspace_root)
        else:
            self.state_file = os.path.realpath(self.state_file)
        self._recompute_counts()

    def health_payload(self) -> dict[str, Any]:
        return {
            "status": "ok",
            "mode": "v2",
            "workspace_root": self.workspace_root,
            "pid": self.pid,
            "started_at": self.started_at,
            "state_file": self.state_file,
            "version": self.version,
            "documents": self.documents,
            "sessions": self.sessions,
            "runs": self.runs,
        }

    def status_payload(self) -> dict[str, Any]:
        self._refresh_session_liveness()
        self._recompute_counts()
        payload = self.health_payload()
        payload["runtime_dir"] = self.runtime_dir
        payload["capabilities"] = [
            "workspace-scope",
            "core-authority",
            "session-ready",
            "projection-clients",
            "branch-ready",
            "runtime-ready",
            "run-ledger",
            "file-sync",
        ]
        return payload

    def list_sessions_payload(self) -> dict[str, Any]:
        self._refresh_session_liveness()
        self._recompute_counts()
        return {
            "status": "ok",
            "sessions": [record.payload() for record in self.session_records.values()],
            "count": self.sessions,
            "workspace_root": self.workspace_root,
        }

    def start_session(
        self,
        actor: str,
        client: str,
        label: str | None,
        session_id: str,
        capabilities: list[str] | None = None,
    ) -> dict[str, Any]:
        now = time.time()
        resolved_capabilities = capabilities or _default_session_capabilities(client)
        existing = self.session_records.get(session_id)
        if existing is None:
            record = SessionRecord(
                session_id=session_id,
                actor=actor,
                client=client,
                label=label,
                status="attached",
                capabilities=resolved_capabilities,
                resume_count=0,
                created_at=now,
                last_seen_at=now,
            )
            self.session_records[session_id] = record
            created = True
        else:
            existing.actor = actor
            existing.client = client
            existing.label = label
            existing.status = "attached"
            existing.capabilities = resolved_capabilities
            existing.resume_count += 1
            existing.last_seen_at = now
            record = existing
            created = False
        self.sessions = len(self.session_records)
        self.persist()
        return {
            "status": "ok",
            "created": created,
            "session": record.payload(),
            "workspace_root": self.workspace_root,
        }

    def _refresh_session_liveness(self) -> None:
        now = time.time()
        changed = False
        for record in self.session_records.values():
            if record.status == "attached" and (now - record.last_seen_at) > SESSION_STALE_AFTER_SECONDS:
                record.status = "stale"
                changed = True
        if changed:
            self.persist()

    def _recompute_counts(self) -> None:
        self.documents = len(self.document_records)
        self.sessions = len(self.session_records)
        self.runs = sum(1 for record in self.run_records.values() if record.status in {"queued", "running"})

    def persist(self) -> None:
        if self.state_file is None:
            return
        with self._lock:
            Path(self.state_file).parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "version": self.version,
                "workspace_root": self.workspace_root,
                "saved_at": time.time(),
                "sessions": [record.payload() for record in self.session_records.values()],
                "documents": [record.payload() for record in self.document_records.values()],
                "branches": [record.payload() for record in self.branch_records.values()],
                "runtimes": [record.payload() for record in self.runtime_records.values()],
                "runs": [record.payload() for record in self.run_records.values()],
            }
            tmp_path = f"{self.state_file}.tmp"
            Path(tmp_path).write_text(json.dumps(payload, indent=2, sort_keys=True))
            os.replace(tmp_path, self.state_file)

    def touch_session(self, session_id: str) -> tuple[dict[str, Any], HTTPStatus]:
        record = self.session_records.get(session_id)
        if record is None:
            return {"error": f"Unknown session_id: {session_id}"}, HTTPStatus.NOT_FOUND
        record.status = "attached"
        record.last_seen_at = time.time()
        self.persist()
        return {
            "status": "ok",
            "session": record.payload(),
            "workspace_root": self.workspace_root,
        }, HTTPStatus.OK

    def detach_session(self, session_id: str) -> tuple[dict[str, Any], HTTPStatus]:
        record = self.session_records.get(session_id)
        if record is None:
            return {"error": f"Unknown session_id: {session_id}"}, HTTPStatus.NOT_FOUND
        record.status = "detached"
        record.last_seen_at = time.time()
        self.persist()
        return {
            "status": "ok",
            "session": record.payload(),
            "workspace_root": self.workspace_root,
        }, HTTPStatus.OK

    def end_session(self, session_id: str) -> tuple[dict[str, Any], HTTPStatus]:
        record = self.session_records.pop(session_id, None)
        self.sessions = len(self.session_records)
        if record is None:
            return {"error": f"Unknown session_id: {session_id}"}, HTTPStatus.NOT_FOUND
        self.persist()
        return {
            "status": "ok",
            "ended": True,
            "session_id": session_id,
            "workspace_root": self.workspace_root,
        }, HTTPStatus.OK

    def list_documents_payload(self) -> dict[str, Any]:
        self._recompute_counts()
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
        relative_path = os.path.relpath(real_path, os.path.realpath(self.workspace_root))
        observed_snapshot = _snapshot_document(real_path, relative_path=relative_path, workspace_root=self.workspace_root)
        for record in self.document_records.values():
            if record.path == real_path:
                record.observed_snapshot = observed_snapshot
                record.sync_state = _compute_sync_state(record.bound_snapshot, observed_snapshot)
                record.updated_at = now
                self.persist()
                return {
                    "status": "ok",
                    "created": False,
                    "document": record.payload(),
                    "workspace_root": self.workspace_root,
                }, HTTPStatus.OK

        record = DocumentRecord(
            document_id=str(uuid.uuid4()),
            path=real_path,
            relative_path=relative_path,
            file_format=_file_format(real_path),
            sync_state=_compute_sync_state(observed_snapshot, observed_snapshot),
            bound_snapshot=observed_snapshot,
            observed_snapshot=observed_snapshot,
            created_at=now,
            updated_at=now,
        )
        self.document_records[record.document_id] = record
        self.persist()
        return {
            "status": "ok",
            "created": True,
            "document": record.payload(),
            "workspace_root": self.workspace_root,
        }, HTTPStatus.OK

    def notebook_contents(self, path: str) -> tuple[dict[str, Any], HTTPStatus]:
        real_path, relative_path = self._resolve_document_path(path)
        client = self._bridge_client(real_path)
        payload = client.contents(relative_path)
        self._sync_document_record(real_path, relative_path)
        return payload, HTTPStatus.OK

    def notebook_status(self, path: str) -> tuple[dict[str, Any], HTTPStatus]:
        real_path, relative_path = self._resolve_document_path(path)
        client = self._bridge_client(real_path)
        payload = client.status(relative_path)
        self._sync_document_record(real_path, relative_path)
        return payload, HTTPStatus.OK

    def notebook_create(
        self,
        path: str,
        *,
        cells: list[dict[str, Any]] | None,
        kernel_id: str | None,
    ) -> tuple[dict[str, Any], HTTPStatus]:
        real_path, relative_path = self._resolve_document_path(path)
        client = self._bridge_client(real_path)
        payload = client.create(relative_path, cells=cells, kernel_id=kernel_id)
        self._sync_document_record(real_path, relative_path)
        return payload, HTTPStatus.OK

    def notebook_edit(self, path: str, operations: list[dict[str, Any]]) -> tuple[dict[str, Any], HTTPStatus]:
        real_path, relative_path = self._resolve_document_path(path)
        client = self._bridge_client(real_path)
        payload = client.edit(relative_path, operations)
        self._sync_document_record(real_path, relative_path)
        return payload, HTTPStatus.OK

    def notebook_execute_cell(
        self,
        path: str,
        *,
        cell_id: str | None,
        cell_index: int | None,
    ) -> tuple[dict[str, Any], HTTPStatus]:
        real_path, relative_path = self._resolve_document_path(path)
        client = self._bridge_client(real_path)
        payload = client.execute_cell(
            relative_path,
            cell_id=cell_id,
            cell_index=cell_index,
            wait=False,
        )
        return payload, HTTPStatus.OK

    def notebook_insert_execute(
        self,
        path: str,
        *,
        source: str,
        cell_type: str,
        at_index: int,
    ) -> tuple[dict[str, Any], HTTPStatus]:
        real_path, relative_path = self._resolve_document_path(path)
        client = self._bridge_client(real_path)
        payload = client.insert_and_execute(
            relative_path,
            source,
            cell_type=cell_type,
            at_index=at_index,
            wait=False,
        )
        return payload, HTTPStatus.OK

    def notebook_execution(self, execution_id: str) -> tuple[dict[str, Any], HTTPStatus]:
        client = self._bridge_client(self.workspace_root)
        payload = client.execution(execution_id)
        return payload, HTTPStatus.OK

    def notebook_execute_all(self, path: str) -> tuple[dict[str, Any], HTTPStatus]:
        real_path, relative_path = self._resolve_document_path(path)
        client = self._bridge_client(real_path)
        payload = client.execute_all(relative_path)
        self._sync_document_record(real_path, relative_path)
        return payload, HTTPStatus.OK

    def refresh_document(self, document_id: str) -> tuple[dict[str, Any], HTTPStatus]:
        record = self.document_records.get(document_id)
        if record is None:
            return {"error": f"Unknown document_id: {document_id}"}, HTTPStatus.NOT_FOUND
        record.observed_snapshot = _snapshot_document(
            record.path,
            relative_path=record.relative_path,
            workspace_root=self.workspace_root,
        )
        record.sync_state = _compute_sync_state(record.bound_snapshot, record.observed_snapshot)
        record.updated_at = time.time()
        self.persist()
        return {
            "status": "ok",
            "document": record.payload(),
            "workspace_root": self.workspace_root,
        }, HTTPStatus.OK

    def _resolve_document_path(self, path: str) -> tuple[str, str]:
        real_path = os.path.realpath(os.path.join(self.workspace_root, path) if not os.path.isabs(path) else path)
        if not _path_within(real_path, self.workspace_root):
            raise ValueError(f"Path is outside workspace: {path}")
        relative_path = os.path.relpath(real_path, self.workspace_root)
        return real_path, relative_path

    def _bridge_client(self, workspace_hint: str) -> BridgeClient:
        return BridgeClient.discover(workspace_hint=workspace_hint)

    def _sync_document_record(self, real_path: str, relative_path: str) -> None:
        now = time.time()
        observed_snapshot = _snapshot_document(real_path, relative_path=relative_path, workspace_root=self.workspace_root)
        for record in self.document_records.values():
            if record.path != real_path:
                continue
            record.relative_path = relative_path
            record.observed_snapshot = observed_snapshot
            if record.bound_snapshot is None:
                record.bound_snapshot = observed_snapshot
            record.sync_state = _compute_sync_state(record.bound_snapshot, observed_snapshot)
            record.updated_at = now
            self.persist()
            return

        record = DocumentRecord(
            document_id=str(uuid.uuid4()),
            path=real_path,
            relative_path=relative_path,
            file_format=_file_format(real_path),
            sync_state=_compute_sync_state(observed_snapshot, observed_snapshot),
            bound_snapshot=observed_snapshot,
            observed_snapshot=observed_snapshot,
            created_at=now,
            updated_at=now,
        )
        self.document_records[record.document_id] = record
        self.persist()

    def rebind_document(self, document_id: str) -> tuple[dict[str, Any], HTTPStatus]:
        record = self.document_records.get(document_id)
        if record is None:
            return {"error": f"Unknown document_id: {document_id}"}, HTTPStatus.NOT_FOUND
        observed_snapshot = _snapshot_document(
            record.path,
            relative_path=record.relative_path,
            workspace_root=self.workspace_root,
        )
        record.bound_snapshot = observed_snapshot
        record.observed_snapshot = observed_snapshot
        record.sync_state = _compute_sync_state(record.bound_snapshot, record.observed_snapshot)
        record.updated_at = time.time()
        self.persist()
        return {
            "status": "ok",
            "document": record.payload(),
            "workspace_root": self.workspace_root,
        }, HTTPStatus.OK

    def list_branches_payload(self) -> dict[str, Any]:
        return {
            "status": "ok",
            "branches": [record.payload() for record in self.branch_records.values()],
            "count": len(self.branch_records),
            "workspace_root": self.workspace_root,
        }

    def start_branch(
        self,
        *,
        branch_id: str,
        document_id: str,
        owner_session_id: str | None,
        parent_branch_id: str | None,
        title: str | None,
        purpose: str | None,
    ) -> tuple[dict[str, Any], HTTPStatus]:
        if branch_id in self.branch_records:
            return {"error": f"Duplicate branch_id: {branch_id}"}, HTTPStatus.BAD_REQUEST
        if document_id not in self.document_records:
            return {"error": f"Unknown document_id: {document_id}"}, HTTPStatus.BAD_REQUEST
        if owner_session_id is not None and owner_session_id not in self.session_records:
            return {"error": f"Unknown owner_session_id: {owner_session_id}"}, HTTPStatus.BAD_REQUEST
        if parent_branch_id is not None and parent_branch_id not in self.branch_records:
            return {"error": f"Unknown parent_branch_id: {parent_branch_id}"}, HTTPStatus.BAD_REQUEST
        now = time.time()
        record = BranchRecord(
            branch_id=branch_id,
            document_id=document_id,
            owner_session_id=owner_session_id,
            parent_branch_id=parent_branch_id,
            title=title,
            purpose=purpose,
            status="active",
            created_at=now,
            updated_at=now,
        )
        self.branch_records[branch_id] = record
        self.persist()
        return {
            "status": "ok",
            "branch": record.payload(),
            "workspace_root": self.workspace_root,
        }, HTTPStatus.OK

    def finish_branch(self, branch_id: str, status: str) -> tuple[dict[str, Any], HTTPStatus]:
        if status not in {"merged", "rejected", "abandoned"}:
            return {"error": f"Invalid branch status: {status}"}, HTTPStatus.BAD_REQUEST
        record = self.branch_records.get(branch_id)
        if record is None:
            return {"error": f"Unknown branch_id: {branch_id}"}, HTTPStatus.NOT_FOUND
        record.status = status
        record.updated_at = time.time()
        self.persist()
        return {
            "status": "ok",
            "branch": record.payload(),
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
        self.persist()
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
        self.persist()
        return {
            "status": "ok",
            "runtime": record.payload(),
            "workspace_root": self.workspace_root,
        }, HTTPStatus.OK

    def list_runs_payload(self) -> dict[str, Any]:
        self._recompute_counts()
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
        if target_type == "document" and target_ref not in self.document_records:
            return {"error": f"Unknown document target_ref: {target_ref}"}, HTTPStatus.BAD_REQUEST
        if target_type == "branch" and target_ref not in self.branch_records:
            return {"error": f"Unknown branch target_ref: {target_ref}"}, HTTPStatus.BAD_REQUEST
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
        self._recompute_counts()
        self.persist()
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
            active_runtime_runs = sum(
                1
                for item in self.run_records.values()
                if item.runtime_id == record.runtime_id and item.status in {"queued", "running"}
            )
            runtime.status = "busy" if active_runtime_runs else "ready"
            runtime.updated_at = now
        self._recompute_counts()
        self.persist()
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
    state = _load_or_create_state(
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
            try:
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
                if self.path == "/api/branches":
                    self._json(HTTPStatus.OK, state.list_branches_payload())
                    return
                if self.path == "/api/runtimes":
                    self._json(HTTPStatus.OK, state.list_runtimes_payload())
                    return
                if self.path == "/api/runs":
                    self._json(HTTPStatus.OK, state.list_runs_payload())
                    return

                self._json(HTTPStatus.NOT_FOUND, {"error": "Not found"})
            except Exception as err:  # pragma: no cover - exercised via integration test
                self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(err)})

        def do_POST(self) -> None:  # noqa: N802
            try:
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
                    capabilities = payload.get("capabilities")
                    if not isinstance(actor, str) or not actor:
                        self._json(HTTPStatus.BAD_REQUEST, {"error": "Missing actor"})
                        return
                    if not isinstance(client, str) or not client:
                        self._json(HTTPStatus.BAD_REQUEST, {"error": "Missing client"})
                        return
                    if not isinstance(session_id, str) or not session_id:
                        self._json(HTTPStatus.BAD_REQUEST, {"error": "Missing session_id"})
                        return
                    resolved_capabilities = (
                        [item for item in capabilities if isinstance(item, str) and item]
                        if isinstance(capabilities, list)
                        else None
                    )
                    self._json(HTTPStatus.OK, state.start_session(actor, client, label, session_id, resolved_capabilities))
                    return
                if self.path == "/api/sessions/touch":
                    session_id = payload.get("session_id")
                    if not isinstance(session_id, str) or not session_id:
                        self._json(HTTPStatus.BAD_REQUEST, {"error": "Missing session_id"})
                        return
                    body, status = state.touch_session(session_id)
                    self._json(status, body)
                    return
                if self.path == "/api/sessions/detach":
                    session_id = payload.get("session_id")
                    if not isinstance(session_id, str) or not session_id:
                        self._json(HTTPStatus.BAD_REQUEST, {"error": "Missing session_id"})
                        return
                    body, status = state.detach_session(session_id)
                    self._json(status, body)
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
                if self.path == "/api/documents/refresh":
                    document_id = payload.get("document_id")
                    if not isinstance(document_id, str) or not document_id:
                        self._json(HTTPStatus.BAD_REQUEST, {"error": "Missing document_id"})
                        return
                    body, status = state.refresh_document(document_id)
                    self._json(status, body)
                    return
                if self.path == "/api/documents/rebind":
                    document_id = payload.get("document_id")
                    if not isinstance(document_id, str) or not document_id:
                        self._json(HTTPStatus.BAD_REQUEST, {"error": "Missing document_id"})
                        return
                    body, status = state.rebind_document(document_id)
                    self._json(status, body)
                    return
                if self.path == "/api/notebooks/contents":
                    path = payload.get("path")
                    if not isinstance(path, str) or not path:
                        self._json(HTTPStatus.BAD_REQUEST, {"error": "Missing path"})
                        return
                    body, status = state.notebook_contents(path)
                    self._json(status, body)
                    return
                if self.path == "/api/notebooks/status":
                    path = payload.get("path")
                    if not isinstance(path, str) or not path:
                        self._json(HTTPStatus.BAD_REQUEST, {"error": "Missing path"})
                        return
                    body, status = state.notebook_status(path)
                    self._json(status, body)
                    return
                if self.path == "/api/notebooks/create":
                    path = payload.get("path")
                    cells = payload.get("cells")
                    kernel_id = payload.get("kernel_id")
                    if not isinstance(path, str) or not path:
                        self._json(HTTPStatus.BAD_REQUEST, {"error": "Missing path"})
                        return
                    resolved_cells = [item for item in cells if isinstance(item, dict)] if isinstance(cells, list) else None
                    body, status = state.notebook_create(
                        path,
                        cells=resolved_cells,
                        kernel_id=kernel_id if isinstance(kernel_id, str) else None,
                    )
                    self._json(status, body)
                    return
                if self.path == "/api/notebooks/edit":
                    path = payload.get("path")
                    operations = payload.get("operations")
                    if not isinstance(path, str) or not path:
                        self._json(HTTPStatus.BAD_REQUEST, {"error": "Missing path"})
                        return
                    if not isinstance(operations, list):
                        self._json(HTTPStatus.BAD_REQUEST, {"error": "Missing operations"})
                        return
                    body, status = state.notebook_edit(
                        path,
                        [item for item in operations if isinstance(item, dict)],
                    )
                    self._json(status, body)
                    return
                if self.path == "/api/notebooks/execute-cell":
                    path = payload.get("path")
                    cell_id = payload.get("cell_id")
                    cell_index = payload.get("cell_index")
                    if not isinstance(path, str) or not path:
                        self._json(HTTPStatus.BAD_REQUEST, {"error": "Missing path"})
                        return
                    body, status = state.notebook_execute_cell(
                        path,
                        cell_id=cell_id if isinstance(cell_id, str) else None,
                        cell_index=cell_index if isinstance(cell_index, int) else None,
                    )
                    self._json(status, body)
                    return
                if self.path == "/api/notebooks/insert-and-execute":
                    path = payload.get("path")
                    source = payload.get("source")
                    cell_type = payload.get("cell_type")
                    at_index = payload.get("at_index")
                    if not isinstance(path, str) or not path:
                        self._json(HTTPStatus.BAD_REQUEST, {"error": "Missing path"})
                        return
                    if not isinstance(source, str):
                        self._json(HTTPStatus.BAD_REQUEST, {"error": "Missing source"})
                        return
                    body, status = state.notebook_insert_execute(
                        path,
                        source=source,
                        cell_type=cell_type if isinstance(cell_type, str) else "code",
                        at_index=at_index if isinstance(at_index, int) else -1,
                    )
                    self._json(status, body)
                    return
                if self.path == "/api/notebooks/execution":
                    execution_id = payload.get("execution_id")
                    if not isinstance(execution_id, str) or not execution_id:
                        self._json(HTTPStatus.BAD_REQUEST, {"error": "Missing execution_id"})
                        return
                    body, status = state.notebook_execution(execution_id)
                    self._json(status, body)
                    return
                if self.path == "/api/notebooks/execute-all":
                    path = payload.get("path")
                    if not isinstance(path, str) or not path:
                        self._json(HTTPStatus.BAD_REQUEST, {"error": "Missing path"})
                        return
                    body, status = state.notebook_execute_all(path)
                    self._json(status, body)
                    return
                if self.path == "/api/branches/start":
                    branch_id = payload.get("branch_id")
                    document_id = payload.get("document_id")
                    owner_session_id = payload.get("owner_session_id")
                    parent_branch_id = payload.get("parent_branch_id")
                    title = payload.get("title")
                    purpose = payload.get("purpose")
                    if not isinstance(branch_id, str) or not branch_id:
                        self._json(HTTPStatus.BAD_REQUEST, {"error": "Missing branch_id"})
                        return
                    if not isinstance(document_id, str) or not document_id:
                        self._json(HTTPStatus.BAD_REQUEST, {"error": "Missing document_id"})
                        return
                    body, status = state.start_branch(
                        branch_id=branch_id,
                        document_id=document_id,
                        owner_session_id=owner_session_id if isinstance(owner_session_id, str) else None,
                        parent_branch_id=parent_branch_id if isinstance(parent_branch_id, str) else None,
                        title=title if isinstance(title, str) else None,
                        purpose=purpose if isinstance(purpose, str) else None,
                    )
                    self._json(status, body)
                    return
                if self.path == "/api/branches/finish":
                    branch_id = payload.get("branch_id")
                    branch_status = payload.get("status")
                    if not isinstance(branch_id, str) or not branch_id:
                        self._json(HTTPStatus.BAD_REQUEST, {"error": "Missing branch_id"})
                        return
                    if not isinstance(branch_status, str) or not branch_status:
                        self._json(HTTPStatus.BAD_REQUEST, {"error": "Missing status"})
                        return
                    body, status = state.finish_branch(branch_id, branch_status)
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
            except Exception as err:  # pragma: no cover - exercised via integration test
                self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(err)})

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


def _file_format(path: str) -> str:
    suffix = Path(path).suffix.lower()
    if suffix == ".ipynb":
        return "ipynb"
    if suffix:
        return suffix.lstrip(".")
    return "unknown"


def _snapshot_file(path: str) -> dict[str, Any]:
    observed_at = time.time()
    if not os.path.exists(path):
        return {
            "exists": False,
            "size_bytes": None,
            "mtime": None,
            "sha256": None,
            "observed_at": observed_at,
        }

    stat = os.stat(path)
    digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    return {
        "exists": True,
        "source_kind": "file",
        "size_bytes": stat.st_size,
        "mtime": stat.st_mtime,
        "sha256": digest,
        "observed_at": observed_at,
    }


def _snapshot_document(path: str, *, relative_path: str, workspace_root: str) -> dict[str, Any]:
    live_snapshot = _snapshot_live_document(path, relative_path=relative_path, workspace_root=workspace_root)
    if live_snapshot is not None:
        return live_snapshot
    return _snapshot_file(path)


def _snapshot_live_document(path: str, *, relative_path: str, workspace_root: str) -> dict[str, Any] | None:
    if _file_format(path) != "ipynb":
        return None

    try:
        from agent_repl.client import BridgeClient

        client = BridgeClient.discover(workspace_hint=path)
        payload = client.contents(relative_path)
    except Exception:
        return None

    cells = payload.get("cells")
    if not isinstance(cells, list):
        return None

    canonical_cells = []
    for cell in cells:
        if not isinstance(cell, dict):
            continue
        canonical_cells.append(
            {
                "cell_id": cell.get("cell_id"),
                "cell_type": cell.get("cell_type"),
                "source": cell.get("source"),
                "outputs": cell.get("outputs"),
                "execution_count": cell.get("execution_count"),
                "metadata": cell.get("metadata"),
            }
        )

    observed_at = time.time()
    encoded = json.dumps(
        {"path": relative_path, "cells": canonical_cells},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return {
        "exists": True,
        "source_kind": "bridge-live",
        "size_bytes": len(encoded),
        "sha256": hashlib.sha256(encoded).hexdigest(),
        "observed_at": observed_at,
        "cell_count": len(canonical_cells),
    }


def _compute_sync_state(bound_snapshot: dict[str, Any] | None, observed_snapshot: dict[str, Any] | None) -> str:
    if not observed_snapshot or not observed_snapshot.get("exists"):
        return "missing"
    if not bound_snapshot or not bound_snapshot.get("exists"):
        return "external-change"
    if (
        bound_snapshot.get("sha256") == observed_snapshot.get("sha256")
        and bound_snapshot.get("size_bytes") == observed_snapshot.get("size_bytes")
    ):
        return "in-sync"
    return "external-change"


def _default_session_capabilities(client: str) -> list[str]:
    normalized = client.lower()
    if normalized == "vscode":
        return ["projection", "editor", "presence"]
    if normalized == "cli":
        return ["projection", "ops", "automation"]
    if normalized == "browser":
        return ["projection", "presence"]
    return ["projection"]


def _state_file_path(workspace_root: str) -> str:
    return os.path.realpath(os.path.join(workspace_root, STATE_DIRNAME, STATE_FILENAME))


def _load_or_create_state(
    *,
    workspace_root: str,
    runtime_dir: str,
    token: str,
    pid: int,
    started_at: float,
) -> CoreState:
    state_file = _state_file_path(workspace_root)
    if not os.path.exists(state_file):
        return CoreState(
            workspace_root=workspace_root,
            runtime_dir=runtime_dir,
            token=token,
            pid=pid,
            started_at=started_at,
            state_file=state_file,
        )

    try:
        payload = json.loads(Path(state_file).read_text())
    except Exception:
        return CoreState(
            workspace_root=workspace_root,
            runtime_dir=runtime_dir,
            token=token,
            pid=pid,
            started_at=started_at,
            state_file=state_file,
        )

    state = CoreState(
        workspace_root=workspace_root,
        runtime_dir=runtime_dir,
        token=token,
        pid=pid,
        started_at=started_at,
        state_file=state_file,
        version=str(payload.get("version") or V2_VERSION),
        session_records={
            record["session_id"]: SessionRecord(**record)
            for record in payload.get("sessions", [])
            if isinstance(record, dict) and isinstance(record.get("session_id"), str)
        },
        document_records={
            record["document_id"]: DocumentRecord(
                document_id=record["document_id"],
                path=record["path"],
                relative_path=record.get("relative_path") or os.path.relpath(record["path"], workspace_root),
                file_format=record.get("file_format") or _file_format(record["path"]),
                sync_state=record.get("sync_state") or "unknown",
                bound_snapshot=record.get("bound_snapshot"),
                observed_snapshot=record.get("observed_snapshot"),
                created_at=record["created_at"],
                updated_at=record["updated_at"],
            )
            for record in payload.get("documents", [])
            if isinstance(record, dict) and isinstance(record.get("document_id"), str)
        },
        branch_records={
            record["branch_id"]: BranchRecord(**record)
            for record in payload.get("branches", [])
            if isinstance(record, dict) and isinstance(record.get("branch_id"), str)
        },
        runtime_records={
            record["runtime_id"]: RuntimeRecord(**record)
            for record in payload.get("runtimes", [])
            if isinstance(record, dict) and isinstance(record.get("runtime_id"), str)
        },
        run_records={
            record["run_id"]: RunRecord(**record)
            for record in payload.get("runs", [])
            if isinstance(record, dict) and isinstance(record.get("run_id"), str)
        },
    )
    _normalize_restored_state(state)
    state.persist()
    return state


def _normalize_restored_state(state: CoreState) -> None:
    now = time.time()
    for session in state.session_records.values():
        if session.status in {"attached", "stale"}:
            session.status = "detached"
            session.last_seen_at = now
    for runtime in state.runtime_records.values():
        if runtime.status in {"ready", "busy"}:
            runtime.status = "recovery-needed"
            runtime.updated_at = now
    for run in state.run_records.values():
        if run.status in {"queued", "running"}:
            run.status = "interrupted"
            run.updated_at = now
    state._recompute_counts()
