"""Minimal workspace-scoped HTTP daemon for the core daemon."""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import nbformat
from jupyter_client import KernelManager
from jupyter_client.kernelspec import KernelSpec

from agent_repl.client import BridgeClient
from agent_repl.core.collaboration import CollaborationConflictError
from agent_repl.core.notebook_execution_service import NotebookExecutionService
from agent_repl.core.notebook_read_service import NotebookReadService
from agent_repl.core.notebook_requests import (
    NotebookActivityRequest,
    NotebookCreateRequest,
    NotebookEditRequest,
    NotebookExecuteCellRequest,
    NotebookExecuteVisibleCellRequest,
    NotebookExecutionLookupRequest,
    NotebookInsertExecuteRequest,
    NotebookLeaseAcquireRequest,
    NotebookLeaseReleaseRequest,
    NotebookPathRequest,
    NotebookProjectVisibleRequest,
    NotebookSelectKernelRequest,
    NotebookSessionPathRequest,
)
from agent_repl.core.notebook_write_service import NotebookWriteService


CORE_VERSION = "0.1.0"
SESSION_STALE_AFTER_SECONDS = 60.0
SESSION_STATUS_RANK = {
    "attached": 3,
    "stale": 2,
    "detached": 1,
}
SESSION_CLIENT_RANK = {
    "vscode": 3,
    "browser": 2,
    "cli": 1,
    "worker": 0,
}
CELL_LEASE_TTL_SECONDS = 45.0
STATE_DIRNAME = ".agent-repl"
STATE_FILENAME = "core-state.json"
MAX_ACTIVITY_RECORDS = 500
RUNTIME_ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "provisioning": {"idle", "busy", "failed"},
    "idle": {"busy", "detached", "degraded", "draining", "failed", "recovery-needed"},
    "busy": {"idle", "detached", "degraded", "draining", "failed", "recovery-needed"},
    "degraded": {"idle", "draining", "failed", "recovery-needed"},
    "detached": {"idle", "busy", "failed", "recovery-needed"},
    "draining": {"stopped", "failed"},
    "stopped": {"provisioning", "reaped"},
    "failed": {"provisioning", "reaped"},
    "reaped": set(),
    "recovery-needed": {"provisioning", "stopped", "reaped"},
    "ready": {"recovery-needed"},
}


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
class NotebookPresenceRecord:
    session_id: str
    path: str
    activity: str
    cell_id: str | None
    cell_index: int | None
    created_at: float
    updated_at: float

    def payload(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "path": self.path,
            "activity": self.activity,
            "cell_id": self.cell_id,
            "cell_index": self.cell_index,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass
class CellLeaseRecord:
    lease_id: str
    session_id: str
    path: str
    cell_id: str
    kind: str
    created_at: float
    updated_at: float
    expires_at: float

    def payload(self) -> dict[str, Any]:
        return {
            "lease_id": self.lease_id,
            "session_id": self.session_id,
            "path": self.path,
            "cell_id": self.cell_id,
            "kind": self.kind,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "expires_at": self.expires_at,
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
    document_path: str | None = None
    branch_id: str | None = None
    health: str = "healthy"
    kernel_generation: int = 1
    expires_at: float | None = None

    def payload(self) -> dict[str, Any]:
        return {
            "runtime_id": self.runtime_id,
            "mode": self.mode,
            "label": self.label,
            "environment": self.environment,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "document_path": self.document_path,
            "branch_id": self.branch_id,
            "health": self.health,
            "kernel_generation": self.kernel_generation,
            "expires_at": self.expires_at,
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
class ActivityEventRecord:
    event_id: str
    path: str
    type: str
    detail: str
    actor: str | None
    session_id: str | None
    runtime_id: str | None
    cell_id: str | None
    cell_index: int | None
    data: dict[str, Any] | None
    timestamp: float

    def payload(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "path": self.path,
            "type": self.type,
            "detail": self.detail,
            "actor": self.actor,
            "session_id": self.session_id,
            "runtime_id": self.runtime_id,
            "cell_id": self.cell_id,
            "cell_index": self.cell_index,
            "data": self.data,
            "timestamp": self.timestamp,
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
    review_status: str | None = None
    review_requested_by_session_id: str | None = None
    review_requested_at: float | None = None
    review_resolved_by_session_id: str | None = None
    review_resolved_at: float | None = None
    review_resolution: str | None = None
    review_note: str | None = None

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
            "review_status": self.review_status,
            "review_requested_by_session_id": self.review_requested_by_session_id,
            "review_requested_at": self.review_requested_at,
            "review_resolved_by_session_id": self.review_resolved_by_session_id,
            "review_resolved_at": self.review_resolved_at,
            "review_resolution": self.review_resolution,
            "review_note": self.review_note,
        }


@dataclass
class HeadlessNotebookRuntime:
    runtime_id: str
    path: str
    python_path: str
    manager: Any
    client: Any
    created_at: float
    last_used_at: float
    kernel_generation: int = 1
    busy: bool = False
    current_execution: dict[str, Any] | None = None
    lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

    def payload(self) -> dict[str, Any]:
        return {
            "runtime_id": self.runtime_id,
            "path": self.path,
            "python_path": self.python_path,
            "kernel_generation": self.kernel_generation,
            "busy": self.busy,
            "current_execution": self.current_execution,
            "created_at": self.created_at,
            "last_used_at": self.last_used_at,
        }


@dataclass
class CoreState:
    workspace_root: str
    runtime_dir: str
    token: str
    pid: int
    started_at: float
    state_file: str | None = None
    version: str = CORE_VERSION
    documents: int = 0
    sessions: int = 0
    runs: int = 0
    runtime_file: str | None = None
    session_records: dict[str, SessionRecord] = field(default_factory=dict)
    notebook_presence: dict[str, NotebookPresenceRecord] = field(default_factory=dict)
    cell_leases: dict[str, CellLeaseRecord] = field(default_factory=dict)
    document_records: dict[str, DocumentRecord] = field(default_factory=dict)
    branch_records: dict[str, BranchRecord] = field(default_factory=dict)
    runtime_records: dict[str, RuntimeRecord] = field(default_factory=dict)
    run_records: dict[str, RunRecord] = field(default_factory=dict)
    activity_records: list[ActivityEventRecord] = field(default_factory=list, repr=False)
    headless_runtimes: dict[str, HeadlessNotebookRuntime] = field(default_factory=dict, repr=False)
    _validated_kernel_pythons: set[str] = field(default_factory=set, init=False, repr=False)
    _last_activity_timestamp: float = field(default=0.0, init=False, repr=False)
    _lock: threading.RLock = field(default_factory=threading.RLock, init=False, repr=False)
    _notebook_locks: dict[str, threading.RLock] = field(default_factory=dict, init=False, repr=False)
    _notebook_locks_guard: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)
    _notebook_execution_service: NotebookExecutionService = field(init=False, repr=False)
    _notebook_read_service: NotebookReadService = field(init=False, repr=False)
    _notebook_write_service: NotebookWriteService = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self.workspace_root = os.path.realpath(self.workspace_root)
        self.runtime_dir = os.path.realpath(self.runtime_dir)
        if self.state_file is None:
            self.state_file = _state_file_path(self.workspace_root)
        else:
            self.state_file = os.path.realpath(self.state_file)
        self._last_activity_timestamp = max(
            (record.timestamp for record in self.activity_records),
            default=self.started_at,
        )
        self._notebook_execution_service = NotebookExecutionService(self)
        self._notebook_read_service = NotebookReadService(self)
        self._notebook_write_service = NotebookWriteService(self)
        self._recompute_counts()

    def _next_activity_timestamp(self) -> float:
        now = time.time()
        # The notebook activity API paginates with `timestamp > since`, so
        # activity timestamps must be strictly monotonic or fast adjacent
        # execution events can disappear between polls.
        self._last_activity_timestamp = max(now, self._last_activity_timestamp + 1e-6)
        return self._last_activity_timestamp

    def health_payload(self) -> dict[str, Any]:
        return {
            "status": "ok",
            "mode": "core",
            "workspace_root": self.workspace_root,
            "pid": self.pid,
            "started_at": self.started_at,
            "state_file": self.state_file,
            "version": self.version,
            "code_hash": _current_package_hash(),
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

    def _append_activity_event(
        self,
        *,
        path: str,
        event_type: str,
        detail: str,
        actor: str | None = None,
        session_id: str | None = None,
        runtime_id: str | None = None,
        cell_id: str | None = None,
        cell_index: int | None = None,
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            event = ActivityEventRecord(
                event_id=str(uuid.uuid4()),
                path=path,
                type=event_type,
                detail=detail,
                actor=actor,
                session_id=session_id,
                runtime_id=runtime_id,
                cell_id=cell_id,
                cell_index=cell_index,
                data=json.loads(json.dumps(data)) if isinstance(data, dict) else None,
                timestamp=self._next_activity_timestamp(),
            )
            self.activity_records.append(event)
            if len(self.activity_records) > MAX_ACTIVITY_RECORDS:
                del self.activity_records[: len(self.activity_records) - MAX_ACTIVITY_RECORDS]
            return event.payload()

    def _transition_runtime_record(
        self,
        record: RuntimeRecord,
        status: str,
        *,
        health: str | None = None,
        reason: str | None = None,
        emit_event: bool = True,
    ) -> RuntimeRecord:
        previous_status = record.status
        if previous_status != status:
            allowed = RUNTIME_ALLOWED_TRANSITIONS.get(previous_status, set())
            if status not in allowed:
                raise RuntimeError(f"Invalid runtime transition: {record.runtime_id} {previous_status} -> {status}")
            record.status = status
        if health is not None:
            record.health = health
        record.updated_at = time.time()
        if emit_event and previous_status != status and record.document_path:
            self._append_activity_event(
                path=record.document_path,
                event_type="runtime-state-changed",
                detail=f"Runtime {record.runtime_id} transitioned {previous_status} -> {status}",
                runtime_id=record.runtime_id,
                data={
                    "from_status": previous_status,
                    "to_status": status,
                    "health": record.health,
                    "reason": reason,
                },
            )
        return record

    def _clear_session_presence(self, session_id: str) -> None:
        with self._lock:
            self.notebook_presence.pop(session_id, None)

    def _refresh_session_leases(self, session_id: str) -> None:
        with self._lock:
            now = time.time()
            changed = False
            for lease in list(self.cell_leases.values()):
                if lease.session_id != session_id:
                    continue
                lease.updated_at = now
                lease.expires_at = now + CELL_LEASE_TTL_SECONDS
                changed = True
        if changed:
            self.persist()

    def _clear_session_leases(self, session_id: str) -> None:
        with self._lock:
            removed = [key for key, lease in list(self.cell_leases.items()) if lease.session_id == session_id]
            for key in removed:
                self.cell_leases.pop(key, None)

    def _lease_key(self, relative_path: str, cell_id: str) -> str:
        return f"{relative_path}::{cell_id}"

    def _reap_expired_cell_leases(self) -> None:
        with self._lock:
            now = time.time()
            expired = [key for key, lease in list(self.cell_leases.items()) if lease.expires_at <= now]
            for key in expired:
                self.cell_leases.pop(key, None)

    def _leases_payload_for_path(self, relative_path: str) -> list[dict[str, Any]]:
        self._reap_expired_cell_leases()
        with self._lock:
            leases = [lease for lease in list(self.cell_leases.values()) if lease.path == relative_path]
            sessions = {session_id: record.payload() for session_id, record in self.session_records.items()}
        items: list[dict[str, Any]] = []
        for lease in leases:
            payload = lease.payload()
            payload["session"] = sessions.get(lease.session_id)
            items.append(payload)
        items.sort(key=lambda item: item.get("updated_at", 0), reverse=True)
        return items

    def _conflicting_lease(
        self,
        *,
        relative_path: str,
        cell_id: str,
        owner_session_id: str | None,
        kinds: set[str] | None = None,
    ) -> CellLeaseRecord | None:
        self._reap_expired_cell_leases()
        with self._lock:
            lease = self.cell_leases.get(self._lease_key(relative_path, cell_id))
        if lease is None:
            return None
        if owner_session_id is not None and lease.session_id == owner_session_id:
            return None
        if kinds is not None and lease.kind not in kinds:
            return None
        return lease

    def _active_structure_leases(self, relative_path: str, owner_session_id: str | None) -> list[CellLeaseRecord]:
        self._reap_expired_cell_leases()
        with self._lock:
            return [
                lease
                for lease in list(self.cell_leases.values())
                if lease.path == relative_path and lease.kind == "structure"
                and not (owner_session_id is not None and lease.session_id == owner_session_id)
            ]

    def _assert_structure_not_leased(
        self,
        *,
        relative_path: str,
        owner_session_id: str | None,
        operation: str,
    ) -> None:
        structure_leases = self._active_structure_leases(relative_path, owner_session_id)
        if not structure_leases:
            return
        lease = structure_leases[0]
        raise CollaborationConflictError(
            f"Operation '{operation}' is blocked by an active structure lease",
            payload=self._lease_conflict_payload(
                relative_path=relative_path,
                lease=lease,
                operation=operation,
                owner_session_id=owner_session_id,
            ),
        )

    def _lease_conflict_payload(
        self,
        *,
        relative_path: str,
        lease: CellLeaseRecord,
        operation: str,
        owner_session_id: str | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            session = self.session_records.get(lease.session_id)
            document = next(
                (record for record in list(self.document_records.values()) if record.relative_path == relative_path),
                None,
            )
        suggested_branch = None
        if owner_session_id is not None and document is not None:
            suggested_branch = {
                "action": "branch-start",
                "document_id": document.document_id,
                "owner_session_id": owner_session_id,
                "reason": "lease-conflict",
                "title": f"Conflict draft: {operation}",
            }
        return {
            "error": f"Operation '{operation}' is blocked by an active cell lease",
            "path": relative_path,
            "conflict": {
                "lease": lease.payload(),
                "holder": session.payload() if session is not None else None,
                "operation": operation,
                "suggested_branch": suggested_branch,
            },
        }

    def _assert_cell_not_leased(
        self,
        *,
        relative_path: str,
        cell_id: str,
        owner_session_id: str | None,
        operation: str,
        kinds: set[str] | None = None,
    ) -> None:
        lease = self._conflicting_lease(
            relative_path=relative_path,
            cell_id=cell_id,
            owner_session_id=owner_session_id,
            kinds=kinds,
        )
        if lease is None:
            return
        raise CollaborationConflictError(
            f"Operation '{operation}' is blocked by an active lease",
            payload=self._lease_conflict_payload(
                relative_path=relative_path,
                lease=lease,
                operation=operation,
                owner_session_id=owner_session_id,
            ),
        )

    def acquire_cell_lease(
        self,
        *,
        session_id: str,
        path: str,
        cell_id: str | None = None,
        cell_index: int | None = None,
        kind: str = "edit",
        ttl_seconds: float | None = None,
    ) -> tuple[dict[str, Any], HTTPStatus]:
        if kind not in {"edit", "structure"}:
            return {"error": f"Invalid lease kind: {kind}"}, HTTPStatus.BAD_REQUEST
        with self._lock:
            session = self.session_records.get(session_id)
        if session is None:
            return {"error": f"Unknown session_id: {session_id}"}, HTTPStatus.NOT_FOUND
        real_path, relative_path = self._resolve_document_path(path)
        with self._notebook_lock(real_path):
            notebook, changed = self._load_notebook(real_path)
            if changed:
                self._save_notebook(real_path, notebook)
            resolved_cell_id = cell_id
            if resolved_cell_id is None and cell_index is not None:
                index = self._find_cell_index(notebook, cell_id=None, cell_index=cell_index)
                resolved_cell_id = self._cell_id(notebook.cells[index], index)
        if not resolved_cell_id:
            return {"error": "Missing cell_id or cell_index"}, HTTPStatus.BAD_REQUEST
        conflict = self._conflicting_lease(
            relative_path=relative_path,
            cell_id=resolved_cell_id,
            owner_session_id=session_id,
        )
        if conflict is not None:
            return self._lease_conflict_payload(
                relative_path=relative_path,
                lease=conflict,
                operation="lease-acquire",
                owner_session_id=session_id,
            ), HTTPStatus.CONFLICT
        key = self._lease_key(relative_path, resolved_cell_id)
        now = time.time()
        expires_at = now + (ttl_seconds if ttl_seconds is not None else CELL_LEASE_TTL_SECONDS)
        with self._lock:
            existing = self.cell_leases.get(key)
            created = existing is None
            if existing is None:
                lease = CellLeaseRecord(
                    lease_id=str(uuid.uuid4()),
                    session_id=session_id,
                    path=relative_path,
                    cell_id=resolved_cell_id,
                    kind=kind,
                    created_at=now,
                    updated_at=now,
                    expires_at=expires_at,
                )
                self.cell_leases[key] = lease
            else:
                existing.session_id = session_id
                existing.kind = kind
                existing.updated_at = now
                existing.expires_at = expires_at
                lease = existing
        self._append_activity_event(
            path=relative_path,
            event_type="lease-acquired",
            detail=f"{session.actor} acquired {kind} lease",
            actor=session.actor,
            session_id=session_id,
            cell_id=resolved_cell_id,
            cell_index=cell_index,
        )
        self.persist()
        payload = lease.payload()
        payload["session"] = session.payload()
        return {"status": "ok", "created": created, "lease": payload, "workspace_root": self.workspace_root}, HTTPStatus.OK

    def release_cell_lease(
        self,
        *,
        session_id: str,
        path: str,
        cell_id: str | None = None,
        cell_index: int | None = None,
    ) -> tuple[dict[str, Any], HTTPStatus]:
        with self._lock:
            session = self.session_records.get(session_id)
        if session is None:
            return {"error": f"Unknown session_id: {session_id}"}, HTTPStatus.NOT_FOUND
        real_path, relative_path = self._resolve_document_path(path)
        with self._notebook_lock(real_path):
            notebook, changed = self._load_notebook(real_path)
            if changed:
                self._save_notebook(real_path, notebook)
            resolved_cell_id = cell_id
            if resolved_cell_id is None and cell_index is not None:
                index = self._find_cell_index(notebook, cell_id=None, cell_index=cell_index)
                resolved_cell_id = self._cell_id(notebook.cells[index], index)
        if not resolved_cell_id:
            return {"error": "Missing cell_id or cell_index"}, HTTPStatus.BAD_REQUEST
        key = self._lease_key(relative_path, resolved_cell_id)
        with self._lock:
            lease = self.cell_leases.get(key)
            if lease is None or lease.session_id != session_id:
                return {"status": "ok", "released": False, "workspace_root": self.workspace_root}, HTTPStatus.OK
            removed = self.cell_leases.pop(key)
        self._append_activity_event(
            path=relative_path,
            event_type="lease-released",
            detail=f"{session.actor} released {removed.kind} lease",
            actor=session.actor,
            session_id=session_id,
            cell_id=resolved_cell_id,
            cell_index=cell_index,
        )
        self.persist()
        return {"status": "ok", "released": True, "workspace_root": self.workspace_root}, HTTPStatus.OK

    def _presence_payload_for_path(self, relative_path: str) -> list[dict[str, Any]]:
        with self._lock:
            presence_records = [record.payload() for record in self.notebook_presence.values() if record.path == relative_path]
            session_payloads = {session_id: record.payload() for session_id, record in self.session_records.items()}
        items: list[dict[str, Any]] = []
        for payload in presence_records:
            payload["session"] = session_payloads.get(payload["session_id"])
            items.append(payload)
        items.sort(key=lambda item: item.get("updated_at", 0), reverse=True)
        return items

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

    def resolve_preferred_session(self, actor: str = "human") -> dict[str, Any]:
        self._refresh_session_liveness()
        best_record: SessionRecord | None = None
        best_key: tuple[int, int, int, float, float] | None = None
        with self._lock:
            sessions = list(self.session_records.values())

        for record in sessions:
            if record.actor != actor:
                continue
            status_rank = SESSION_STATUS_RANK.get(record.status, 0)
            if status_rank == 0:
                continue
            client_rank = SESSION_CLIENT_RANK.get(record.client, 0)
            editor_rank = 1 if (
                "editor" in record.capabilities or record.client == "vscode"
            ) else 0
            sort_key = (
                status_rank,
                editor_rank,
                client_rank,
                record.last_seen_at,
                record.created_at,
            )
            if best_key is None or sort_key > best_key:
                best_key = sort_key
                best_record = record

        return {
            "status": "ok",
            "session": best_record.payload() if best_record else None,
            "workspace_root": self.workspace_root,
        }

    def _refresh_session_liveness(self) -> None:
        with self._lock:
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
                "activity": [record.payload() for record in self.activity_records],
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
        self._refresh_session_leases(session_id)
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
        self._clear_session_presence(session_id)
        self._clear_session_leases(session_id)
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
        self._clear_session_presence(session_id)
        self._clear_session_leases(session_id)
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
        return self._notebook_read_service.contents(path)

    def notebook_status(self, path: str) -> tuple[dict[str, Any], HTTPStatus]:
        return self._notebook_read_service.status(path)

    def notebook_create(
        self,
        path: str,
        *,
        cells: list[dict[str, Any]] | None,
        kernel_id: str | None,
    ) -> tuple[dict[str, Any], HTTPStatus]:
        return self._notebook_write_service.create(path, cells=cells, kernel_id=kernel_id)

    def notebook_edit(
        self,
        path: str,
        operations: list[dict[str, Any]],
        *,
        owner_session_id: str | None = None,
    ) -> tuple[dict[str, Any], HTTPStatus]:
        return self._notebook_write_service.edit(path, operations, owner_session_id=owner_session_id)

    def notebook_execute_cell(
        self,
        path: str,
        *,
        cell_id: str | None,
        cell_index: int | None,
        owner_session_id: str | None = None,
    ) -> tuple[dict[str, Any], HTTPStatus]:
        return self._notebook_write_service.execute_cell(
            path,
            cell_id=cell_id,
            cell_index=cell_index,
            owner_session_id=owner_session_id,
        )

    def notebook_insert_execute(
        self,
        path: str,
        *,
        source: str,
        cell_type: str,
        at_index: int,
        owner_session_id: str | None = None,
    ) -> tuple[dict[str, Any], HTTPStatus]:
        return self._notebook_write_service.insert_execute(
            path,
            source=source,
            cell_type=cell_type,
            at_index=at_index,
            owner_session_id=owner_session_id,
        )

    def notebook_execution(self, execution_id: str) -> tuple[dict[str, Any], HTTPStatus]:
        client = self._projection_client(self.workspace_root)
        if client is None:
            return {"error": f"Unknown execution: {execution_id}"}, HTTPStatus.NOT_FOUND
        payload = client.execution(execution_id)
        return payload, HTTPStatus.OK

    def notebook_execute_all(
        self,
        path: str,
        *,
        owner_session_id: str | None = None,
    ) -> tuple[dict[str, Any], HTTPStatus]:
        return self._notebook_write_service.execute_all(path, owner_session_id=owner_session_id)

    def notebook_interrupt(self, path: str) -> tuple[dict[str, Any], HTTPStatus]:
        return self._notebook_write_service.interrupt(path)

    def notebook_select_kernel(
        self,
        path: str,
        *,
        kernel_id: str | None,
    ) -> tuple[dict[str, Any], HTTPStatus]:
        return self._notebook_write_service.select_kernel(path, kernel_id=kernel_id)

    def notebook_runtime(self, path: str) -> tuple[dict[str, Any], HTTPStatus]:
        return self._notebook_read_service.runtime(path)

    def notebook_projection(self, path: str) -> tuple[dict[str, Any], HTTPStatus]:
        return self._notebook_read_service.projection(path)

    def notebook_activity(self, path: str, *, since: float | None = None) -> tuple[dict[str, Any], HTTPStatus]:
        return self._notebook_read_service.activity(path, since=since)

    def upsert_notebook_presence(
        self,
        *,
        session_id: str,
        path: str,
        activity: str,
        cell_id: str | None = None,
        cell_index: int | None = None,
    ) -> tuple[dict[str, Any], HTTPStatus]:
        with self._lock:
            session = self.session_records.get(session_id)
        if session is None:
            return {"error": f"Unknown session_id: {session_id}"}, HTTPStatus.NOT_FOUND
        _real_path, relative_path = self._resolve_document_path(path)
        now = time.time()
        with self._lock:
            existing = self.notebook_presence.get(session_id)
            changed = (
                existing is None
                or existing.path != relative_path
                or existing.activity != activity
                or existing.cell_id != cell_id
                or existing.cell_index != cell_index
            )
            if existing is None:
                record = NotebookPresenceRecord(
                    session_id=session_id,
                    path=relative_path,
                    activity=activity,
                    cell_id=cell_id,
                    cell_index=cell_index,
                    created_at=now,
                    updated_at=now,
                )
                self.notebook_presence[session_id] = record
            else:
                existing.path = relative_path
                existing.activity = activity
                existing.cell_id = cell_id
                existing.cell_index = cell_index
                existing.updated_at = now
                record = existing
        if changed:
            self._append_activity_event(
                path=relative_path,
                event_type="presence-updated",
                detail=f"{session.actor} {activity}",
                actor=session.actor,
                session_id=session_id,
                cell_id=cell_id,
                cell_index=cell_index,
            )
        self.persist()
        payload = record.payload()
        payload["session"] = session.payload()
        return {
            "status": "ok",
            "presence": payload,
            "workspace_root": self.workspace_root,
        }, HTTPStatus.OK

    def clear_notebook_presence(self, *, session_id: str, path: str | None = None) -> tuple[dict[str, Any], HTTPStatus]:
        with self._lock:
            session = self.session_records.get(session_id)
            existing = self.notebook_presence.get(session_id)
        if existing is None:
            return {"status": "ok", "cleared": False, "workspace_root": self.workspace_root}, HTTPStatus.OK
        if path is not None:
            _real_path, relative_path = self._resolve_document_path(path)
            if existing.path != relative_path:
                return {"status": "ok", "cleared": False, "workspace_root": self.workspace_root}, HTTPStatus.OK
        with self._lock:
            removed = self.notebook_presence.pop(session_id, None)
        if removed is None:
            return {"status": "ok", "cleared": False, "workspace_root": self.workspace_root}, HTTPStatus.OK
        self._append_activity_event(
            path=removed.path,
            event_type="presence-cleared",
            detail=f"{session.actor if session is not None else 'session'} left notebook",
            actor=session.actor if session is not None else None,
            session_id=session_id,
            cell_id=removed.cell_id,
            cell_index=removed.cell_index,
        )
        self.persist()
        return {"status": "ok", "cleared": True, "workspace_root": self.workspace_root}, HTTPStatus.OK

    def notebook_project_visible(
        self,
        path: str,
        *,
        cells: list[dict[str, Any]],
        owner_session_id: str | None = None,
    ) -> tuple[dict[str, Any], HTTPStatus]:
        return self._notebook_write_service.project_visible(path, cells=cells, owner_session_id=owner_session_id)

    def notebook_execute_visible_cell(
        self,
        path: str,
        *,
        cell_index: int,
        source: str,
        owner_session_id: str | None = None,
    ) -> tuple[dict[str, Any], HTTPStatus]:
        return self._notebook_write_service.execute_visible_cell(
            path,
            cell_index=cell_index,
            source=source,
            owner_session_id=owner_session_id,
        )

    def notebook_restart(self, path: str) -> tuple[dict[str, Any], HTTPStatus]:
        return self._notebook_write_service.restart(path)

    def notebook_restart_and_run_all(
        self,
        path: str,
        *,
        owner_session_id: str | None = None,
    ) -> tuple[dict[str, Any], HTTPStatus]:
        return self._notebook_write_service.restart_and_run_all(path, owner_session_id=owner_session_id)

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

    def _headless_runtime_id(self, relative_path: str) -> str:
        return f"headless:{relative_path}"

    def _runtime_record_for_notebook(self, relative_path: str) -> RuntimeRecord | None:
        runtime_id = self._headless_runtime_id(relative_path)
        record = self.runtime_records.get(runtime_id)
        if record is not None:
            return record
        candidates = self._notebook_runtime_candidates(relative_path)
        if len(candidates) == 1:
            return candidates[0]
        return None

    def _selected_runtime_record_for_notebook(self, relative_path: str) -> RuntimeRecord | None:
        direct = self.runtime_records.get(self._headless_runtime_id(relative_path))
        if direct is not None:
            return direct
        candidates = self._notebook_runtime_candidates(relative_path)
        if len(candidates) == 1:
            return candidates[0]
        pinned = [record for record in candidates if record.mode == "pinned"]
        if len(pinned) == 1:
            return pinned[0]
        return None

    def _upsert_runtime_record(
        self,
        *,
        runtime_id: str,
        mode: str,
        label: str | None,
        environment: str | None,
        status: str,
        document_path: str | None = None,
        branch_id: str | None = None,
        health: str = "healthy",
        kernel_generation: int = 1,
        expires_at: float | None = None,
    ) -> RuntimeRecord:
        now = time.time()
        existing = self.runtime_records.get(runtime_id)
        if existing is None:
            record = RuntimeRecord(
                runtime_id=runtime_id,
                mode=mode,
                label=label,
                environment=environment,
                status=status,
                created_at=now,
                updated_at=now,
                document_path=document_path,
                branch_id=branch_id,
                health=health,
                kernel_generation=kernel_generation,
                expires_at=expires_at,
            )
            self.runtime_records[runtime_id] = record
            return record
        existing.mode = mode
        existing.label = label
        existing.environment = environment
        existing.document_path = document_path
        existing.branch_id = branch_id
        existing.kernel_generation = kernel_generation
        existing.expires_at = expires_at
        return self._transition_runtime_record(existing, status, health=health, reason="upsert-runtime-record")

    def _sync_headless_runtime_record(
        self,
        *,
        relative_path: str,
        runtime: HeadlessNotebookRuntime,
        status: str | None = None,
        health: str = "healthy",
        mode: str | None = None,
        expires_at: float | None = None,
    ) -> RuntimeRecord:
        existing = self.runtime_records.get(runtime.runtime_id)
        return self._upsert_runtime_record(
            runtime_id=runtime.runtime_id,
            mode=mode or (existing.mode if existing is not None else "headless"),
            label=f"Notebook runtime: {relative_path}",
            environment=runtime.python_path,
            status=status or ("busy" if runtime.busy else "idle"),
            document_path=relative_path,
            health=health,
            kernel_generation=runtime.kernel_generation,
            branch_id=existing.branch_id if existing is not None else None,
            expires_at=expires_at if expires_at is not None else (existing.expires_at if existing is not None else None),
        )

    def _notebook_runtime_candidates(self, relative_path: str) -> list[RuntimeRecord]:
        return [
            record
            for record in list(self.runtime_records.values())
            if record.document_path == relative_path and record.mode in {"headless", "shared", "pinned", "ephemeral"}
        ]

    def _notebook_reattach_policy(
        self,
        *,
        real_path: str,
        relative_path: str,
    ) -> dict[str, Any]:
        runtime = self.headless_runtimes.get(real_path)
        if runtime is not None:
            record = self._sync_headless_runtime_record(relative_path=relative_path, runtime=runtime)
            return {
                "action": "attach-live",
                "reason": "live-runtime",
                "selected_runtime_id": record.runtime_id,
                "candidate_runtime_ids": [record.runtime_id],
            }

        candidates = self._notebook_runtime_candidates(relative_path)
        active_like = [
            record for record in candidates
            if record.status in {"idle", "busy", "detached", "degraded", "recovery-needed"}
        ]
        if len(active_like) == 1:
            record = active_like[0]
            if record.status == "busy":
                return {
                    "action": "observe-or-queue",
                    "reason": "runtime-busy",
                    "selected_runtime_id": record.runtime_id,
                    "candidate_runtime_ids": [record.runtime_id],
                }
            if record.status == "degraded":
                return {
                    "action": "attach-with-warning",
                    "reason": "runtime-degraded",
                    "selected_runtime_id": record.runtime_id,
                    "candidate_runtime_ids": [record.runtime_id],
                }
            if record.status == "recovery-needed":
                return {
                    "action": "resume-runtime",
                    "reason": "continuity-lost",
                    "selected_runtime_id": record.runtime_id,
                    "candidate_runtime_ids": [record.runtime_id],
                }
            return {
                "action": "resume-runtime",
                "reason": "matching-runtime-record",
                "selected_runtime_id": record.runtime_id,
                "candidate_runtime_ids": [record.runtime_id],
            }

        if len(active_like) > 1:
            pinned = [record for record in active_like if record.mode == "pinned"]
            if len(pinned) == 1:
                return {
                    "action": "resume-runtime",
                    "reason": "preferred-pinned-runtime",
                    "selected_runtime_id": pinned[0].runtime_id,
                    "candidate_runtime_ids": [record.runtime_id for record in active_like],
                }
            return {
                "action": "select-runtime",
                "reason": "ambiguous-runtime-match",
                "selected_runtime_id": None,
                "candidate_runtime_ids": [record.runtime_id for record in active_like],
            }

        reusable_stopped = [
            record for record in candidates
            if record.status == "stopped" and bool(record.environment)
        ]
        if len(reusable_stopped) == 1:
            if reusable_stopped[0].mode == "ephemeral":
                return {
                    "action": "none",
                    "reason": "stopped-ephemeral-runtime",
                    "selected_runtime_id": reusable_stopped[0].runtime_id,
                    "candidate_runtime_ids": [reusable_stopped[0].runtime_id],
                }
            return {
                "action": "create-runtime",
                "reason": "stopped-runtime-can-be-recreated",
                "selected_runtime_id": reusable_stopped[0].runtime_id,
                "candidate_runtime_ids": [reusable_stopped[0].runtime_id],
            }

        try:
            default_python = self._resolve_python_path(None)
        except Exception:
            default_python = None
        if default_python:
            return {
                "action": "create-runtime",
                "reason": "workspace-default-kernel-available",
                "selected_runtime_id": self._headless_runtime_id(relative_path),
                "candidate_runtime_ids": [],
            }
        return {
            "action": "none",
            "reason": "no-compatible-runtime",
            "selected_runtime_id": None,
            "candidate_runtime_ids": [],
        }

    def _projection_client(self, workspace_hint: str) -> BridgeClient | None:
        try:
            return BridgeClient.discover(workspace_hint=workspace_hint)
        except Exception:
            return None

    def _reap_expired_runtimes(self) -> None:
        now = time.time()
        changed = False
        for record in list(self.runtime_records.values()):
            if record.mode != "ephemeral" or record.expires_at is None:
                continue
            if record.status in {"reaped", "stopped", "failed"}:
                continue
            if record.expires_at > now:
                continue
            if record.document_path:
                real_path = os.path.realpath(os.path.join(self.workspace_root, record.document_path))
                live = self.headless_runtimes.get(real_path)
                if live is not None and live.runtime_id == record.runtime_id:
                    self._shutdown_headless_runtime(real_path)
            if record.status not in {"stopped", "failed"}:
                if record.status in {"idle", "busy", "detached", "degraded"}:
                    self._transition_runtime_record(record, "draining", health="healthy", reason="ephemeral-runtime-expired")
                self._transition_runtime_record(record, "stopped", health="healthy", reason="ephemeral-runtime-expired")
            self._transition_runtime_record(record, "reaped", health="degraded", reason="ephemeral-runtime-expired")
            changed = True
        if changed:
            self.persist()

    def _resolve_python_path(self, kernel_id: str | None) -> str:
        # Use abspath (not realpath) to preserve venv symlinks.
        # .venv/bin/python is a symlink to the base interpreter, but running
        # through the symlink activates the venv's site-packages via pyvenv.cfg.
        # realpath resolves to the base interpreter which has no venv packages.
        if kernel_id:
            resolved = shutil.which(kernel_id) if not os.path.exists(kernel_id) else kernel_id
            if not resolved:
                raise RuntimeError(f"Explicit kernel '{kernel_id}' is not an executable path")
            return os.path.abspath(resolved)
        workspace_python = os.path.join(self.workspace_root, ".venv", "bin", "python")
        if os.path.exists(workspace_python):
            return os.path.abspath(workspace_python)
        raise RuntimeError(
            "No workspace .venv kernel was detected for this workspace. Re-run with --kernel <python-path>."
        )

    def _resolve_notebook_python_path(self, relative_path: str, kernel_id: str | None) -> str:
        if kernel_id is not None:
            return self._resolve_python_path(kernel_id)
        record = self._runtime_record_for_notebook(relative_path)
        if record and record.environment:
            resolved = shutil.which(record.environment) if not os.path.exists(record.environment) else record.environment
            if resolved:
                return os.path.abspath(resolved)
        return self._resolve_python_path(None)

    def _ensure_kernel_capable_python(self, python_path: str, *, source_hint: str | None = None) -> None:
        # Use the path as-is (preserving symlinks) for both caching and probing.
        canonical = os.path.abspath(python_path)
        if canonical in self._validated_kernel_pythons:
            return
        probe = subprocess.run(
            [canonical, "-c", "import ipykernel"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if probe.returncode != 0:
            hint = source_hint or canonical
            raise RuntimeError(
                f"Kernel executable '{hint}' is not kernel-capable (ipykernel not installed). "
                f"Install it with: {canonical} -m pip install ipykernel — or pass --kernel <path> to use a different environment."
            )
        self._validated_kernel_pythons.add(canonical)

    def _ensure_headless_runtime(self, real_path: str, kernel_id: str | None = None) -> HeadlessNotebookRuntime:
        relative_path = os.path.relpath(real_path, self.workspace_root)
        selected_record = self._selected_runtime_record_for_notebook(relative_path)
        runtime = self.headless_runtimes.get(real_path)
        if runtime is not None:
            if kernel_id is None:
                runtime.last_used_at = time.time()
                self._sync_headless_runtime_record(
                    relative_path=relative_path,
                    runtime=runtime,
                    mode=selected_record.mode if selected_record is not None else None,
                    expires_at=selected_record.expires_at if selected_record is not None else None,
                )
                return runtime
            python_path = self._resolve_notebook_python_path(relative_path, kernel_id)
            venv_path = os.path.join(self.workspace_root, ".venv", "bin", "python")
            source_hint = venv_path if not kernel_id and os.path.exists(venv_path) else None
            self._ensure_kernel_capable_python(python_path, source_hint=source_hint)
            if runtime.python_path == python_path:
                runtime.last_used_at = time.time()
                self._sync_headless_runtime_record(
                    relative_path=relative_path,
                    runtime=runtime,
                    mode=selected_record.mode if selected_record is not None else None,
                    expires_at=selected_record.expires_at if selected_record is not None else None,
                )
                return runtime
        else:
            python_path = self._resolve_notebook_python_path(relative_path, kernel_id)
            venv_path = os.path.join(self.workspace_root, ".venv", "bin", "python")
            source_hint = venv_path if not kernel_id and os.path.exists(venv_path) else None
            self._ensure_kernel_capable_python(python_path, source_hint=source_hint)
        if runtime is not None:
            self._shutdown_headless_runtime(real_path)

        provisioning_mode = selected_record.mode if selected_record is not None else "headless"
        self._upsert_runtime_record(
            runtime_id=selected_record.runtime_id if selected_record is not None else self._headless_runtime_id(relative_path),
            mode=provisioning_mode,
            label=f"Notebook runtime: {relative_path}",
            environment=python_path,
            status="provisioning",
            document_path=relative_path,
            branch_id=selected_record.branch_id if selected_record is not None else None,
            health="healthy",
            kernel_generation=selected_record.kernel_generation if selected_record is not None else 1,
            expires_at=selected_record.expires_at if selected_record is not None else None,
        )

        manager = KernelManager(kernel_name="python3")
        manager._kernel_spec = KernelSpec(  # type: ignore[attr-defined]
            argv=[python_path, "-m", "ipykernel", "-f", "{connection_file}"],
            display_name=f"agent-repl ({Path(python_path).name})",
            language="python",
        )
        manager.start_kernel()
        client = manager.client()
        client.start_channels()
        client.wait_for_ready(timeout=60)
        runtime = HeadlessNotebookRuntime(
            runtime_id=selected_record.runtime_id if selected_record is not None else self._headless_runtime_id(relative_path),
            path=real_path,
            python_path=python_path,
            manager=manager,
            client=client,
            created_at=time.time(),
            last_used_at=time.time(),
        )
        self.headless_runtimes[real_path] = runtime
        self._sync_headless_runtime_record(
            relative_path=relative_path,
            runtime=runtime,
            mode=selected_record.mode if selected_record is not None else None,
            expires_at=selected_record.expires_at if selected_record is not None else None,
        )
        self.persist()
        return runtime

    def _shutdown_headless_runtime(self, real_path: str) -> None:
        runtime = self.headless_runtimes.pop(real_path, None)
        if runtime is None:
            return
        relative_path = os.path.relpath(real_path, self.workspace_root)
        existing = self.runtime_records.get(runtime.runtime_id)
        if existing is not None and existing.status not in {"stopped", "reaped"}:
            self._transition_runtime_record(existing, "draining", reason="shutdown-headless-runtime")
        try:
            runtime.client.stop_channels()
        except Exception:
            pass
        try:
            runtime.manager.shutdown_kernel(now=True)
        except Exception:
            pass
        self._upsert_runtime_record(
            runtime_id=runtime.runtime_id,
            mode=existing.mode if existing is not None else "headless",
            label=f"Notebook runtime: {relative_path}",
            environment=runtime.python_path,
            status="stopped",
            document_path=relative_path,
            health="healthy",
            kernel_generation=runtime.kernel_generation,
            branch_id=existing.branch_id if existing is not None else None,
            expires_at=existing.expires_at if existing is not None else None,
        )
        self.persist()

    def shutdown_headless_runtimes(self) -> None:
        for real_path in list(self.headless_runtimes.keys()):
            self._shutdown_headless_runtime(real_path)

    def _notebook_lock(self, real_path: str) -> threading.RLock:
        with self._notebook_locks_guard:
            lock = self._notebook_locks.get(real_path)
            if lock is None:
                lock = threading.RLock()
                self._notebook_locks[real_path] = lock
            return lock

    def _rollback_inserted_cell(self, real_path: str, cell_id: str) -> None:
        """Remove a cell that was inserted but whose execution failed at the infra level."""
        try:
            notebook, _ = self._load_notebook(real_path)
            index = self._find_cell_index(notebook, cell_id=cell_id)
            notebook.cells.pop(index)
            self._save_notebook(real_path, notebook)
        except Exception:
            pass  # best-effort cleanup; don't mask the original error

    def _load_notebook(self, real_path: str) -> tuple[Any, bool]:
        created = False
        if os.path.exists(real_path):
            with open(real_path, "r", encoding="utf-8") as handle:
                raw_text = handle.read()
            if raw_text.strip():
                notebook = nbformat.reads(raw_text, as_version=4)
            else:
                notebook = nbformat.v4.new_notebook()
                created = True
        else:
            notebook = nbformat.v4.new_notebook()
            created = True
        changed = False
        for index, cell in enumerate(notebook.cells):
            changed = self._ensure_cell_identity(cell, index) or changed
        return notebook, created or changed

    def _save_notebook(self, real_path: str, notebook: Any) -> None:
        Path(real_path).parent.mkdir(parents=True, exist_ok=True)
        directory = str(Path(real_path).parent)
        fd, tmp_path = tempfile.mkstemp(
            prefix=f".{Path(real_path).name}.",
            suffix=".tmp",
            dir=directory,
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                nbformat.write(notebook, handle)
            os.replace(tmp_path, real_path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    def _ensure_cell_identity(self, cell: Any, index: int) -> bool:
        metadata = dict(getattr(cell, "metadata", {}) or {})
        custom = dict(metadata.get("custom", {}) or {})
        agent_repl = dict(custom.get("agent-repl", {}) or {})
        if agent_repl.get("cell_id"):
            return False
        agent_repl["cell_id"] = str(uuid.uuid4())
        custom["agent-repl"] = agent_repl
        metadata["custom"] = custom
        cell.metadata = metadata
        if not getattr(cell, "id", None):
            cell.id = f"cell-{index + 1}"
        return True

    def _cell_id(self, cell: Any, index: int) -> str:
        self._ensure_cell_identity(cell, index)
        metadata = dict(getattr(cell, "metadata", {}) or {})
        custom = dict(metadata.get("custom", {}) or {})
        agent_repl = dict(custom.get("agent-repl", {}) or {})
        return agent_repl["cell_id"]

    def _set_cell_runtime_provenance(
        self,
        cell: Any,
        *,
        runtime_id: str,
        kernel_generation: int,
        status: str,
    ) -> None:
        metadata = dict(getattr(cell, "metadata", {}) or {})
        custom = dict(metadata.get("custom", {}) or {})
        agent_repl = dict(custom.get("agent-repl", {}) or {})
        agent_repl["last_run"] = {
            "runtime_id": runtime_id,
            "kernel_generation": kernel_generation,
            "status": status,
            "updated_at": time.time(),
        }
        custom["agent-repl"] = agent_repl
        metadata["custom"] = custom
        cell.metadata = metadata

    def _clear_cell_runtime_provenance(self, cell: Any) -> None:
        metadata = dict(getattr(cell, "metadata", {}) or {})
        custom = dict(metadata.get("custom", {}) or {})
        agent_repl = dict(custom.get("agent-repl", {}) or {})
        if "last_run" not in agent_repl:
            return
        agent_repl.pop("last_run", None)
        custom["agent-repl"] = agent_repl
        metadata["custom"] = custom
        cell.metadata = metadata

    def _find_cell_index(self, notebook: Any, *, cell_id: str | None = None, cell_index: int | None = None) -> int:
        if cell_id is not None:
            for index, cell in enumerate(notebook.cells):
                if self._cell_id(cell, index) == cell_id:
                    return index
            if cell_index is not None and 0 <= cell_index < len(notebook.cells):
                return cell_index
            raise RuntimeError(f"No cell matched id '{cell_id}'")
        if cell_index is not None:
            if 0 <= cell_index < len(notebook.cells):
                return cell_index
            raise RuntimeError(f"Cell index out of range: {cell_index}")
        raise RuntimeError("Provide cell_id or cell_index")

    def _normalize_insert_index(self, notebook: Any, at_index: int | None) -> int:
        if at_index in {None, -1}:
            return len(notebook.cells)
        return max(0, min(int(at_index), len(notebook.cells)))

    def _canonical_outputs(self, outputs: Any) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        for output in outputs or []:
            if isinstance(output, dict):
                normalized.append(json.loads(json.dumps(output)))
            else:
                normalized.append(json.loads(json.dumps(output)))
        return normalized

    def _session_actor(self, session_id: str | None, fallback: str | None = None) -> str | None:
        if session_id is None:
            return fallback
        session = self.session_records.get(session_id)
        if session is None:
            return fallback
        return session.actor

    def _cell_payload(self, cell: Any, index: int) -> dict[str, Any]:
        is_code = cell.cell_type == "code"
        return {
            "index": index,
            "display_number": index + 1 if is_code else None,
            "cell_id": self._cell_id(cell, index),
            "cell_type": cell.cell_type,
            "source": cell.source,
            "outputs": self._canonical_outputs(getattr(cell, "outputs", [])),
            "execution_count": getattr(cell, "execution_count", None),
            "metadata": dict(getattr(cell, "metadata", {}) or {}),
        }

    def _notebook_cells_payload(self, notebook: Any) -> list[dict[str, Any]]:
        cells: list[dict[str, Any]] = []
        code_index = 0
        for index, cell in enumerate(notebook.cells):
            is_code = cell.cell_type == "code"
            if is_code:
                code_index += 1
            payload = self._cell_payload(cell, index)
            payload["display_number"] = code_index if is_code else None
            cells.append(payload)
        return cells

    def _incoming_cell_id(self, payload: dict[str, Any]) -> str | None:
        cell_id = payload.get("cell_id")
        if isinstance(cell_id, str) and cell_id:
            return cell_id
        metadata = payload.get("metadata")
        if not isinstance(metadata, dict):
            return None
        custom = metadata.get("custom")
        if not isinstance(custom, dict):
            return None
        agent_repl = custom.get("agent-repl")
        if not isinstance(agent_repl, dict):
            return None
        candidate = agent_repl.get("cell_id")
        return candidate if isinstance(candidate, str) and candidate else None

    def _materialize_visible_cell(self, payload: dict[str, Any], existing_by_id: dict[str, Any]) -> Any:
        cell_type = "code" if payload.get("cell_type") == "code" else "markdown"
        source = payload.get("source", "")
        metadata = payload.get("metadata")
        metadata_dict = json.loads(json.dumps(metadata)) if isinstance(metadata, dict) else {}
        stable_cell_id = self._incoming_cell_id(payload)
        existing = existing_by_id.get(stable_cell_id) if stable_cell_id else None

        if cell_type == "code":
            cell = nbformat.v4.new_code_cell(source=source)
            if existing is not None and getattr(existing, "cell_type", None) == "code" and getattr(existing, "source", "") == source:
                cell.outputs = [nbformat.from_dict(output) for output in self._canonical_outputs(getattr(existing, "outputs", []))]
                cell.execution_count = getattr(existing, "execution_count", None)
            else:
                cell.outputs = []
                cell.execution_count = None
        else:
            cell = nbformat.v4.new_markdown_cell(source=source)

        cell.metadata = metadata_dict
        if stable_cell_id:
            custom = dict(cell.metadata.get("custom", {}) or {})
            agent_repl = dict(custom.get("agent-repl", {}) or {})
            agent_repl["cell_id"] = stable_cell_id
            custom["agent-repl"] = agent_repl
            cell.metadata["custom"] = custom
        return cell

    def _headless_notebook_contents(self, real_path: str, relative_path: str) -> dict[str, Any]:
        # Read-only: generate cell IDs in memory but do NOT save.
        # IDs are persisted on the first actual write operation.
        notebook, _changed = self._load_notebook(real_path)
        cells = self._notebook_cells_payload(notebook)
        return {"path": relative_path, "cells": cells}

    def _headless_notebook_status(self, real_path: str, relative_path: str) -> dict[str, Any]:
        runtime = self.headless_runtimes.get(real_path)
        running = []
        if runtime and runtime.current_execution:
            running.append(runtime.current_execution)
        return {
            "path": relative_path,
            "open": False,
            "kernel_state": "busy" if runtime and runtime.busy else ("idle" if runtime else "not_open"),
            "busy": bool(runtime and runtime.busy),
            "running": running,
            "queued": [],
        }

    def _create_notebook_cells(self, cells: list[dict[str, Any]] | None) -> list[Any]:
        notebook_cells: list[Any] = []
        for index, cell in enumerate(cells or []):
            cell_type = "code" if cell.get("type") == "code" else "markdown"
            source = cell.get("source", "")
            if cell_type == "code":
                notebook_cell = nbformat.v4.new_code_cell(source=source)
            else:
                notebook_cell = nbformat.v4.new_markdown_cell(source=source)
            self._ensure_cell_identity(notebook_cell, index)
            notebook_cells.append(notebook_cell)
        return notebook_cells

    def _headless_notebook_create(
        self,
        real_path: str,
        relative_path: str,
        *,
        cells: list[dict[str, Any]] | None,
        kernel_id: str | None,
    ) -> dict[str, Any]:
        python_path = self._resolve_python_path(kernel_id)
        runtime = self._ensure_headless_runtime(real_path, python_path)
        notebook = nbformat.v4.new_notebook(cells=self._create_notebook_cells(cells))
        notebook.metadata["kernelspec"] = {
            "display_name": f"{Path(python_path).parent.parent.name or Path(python_path).name}",
            "language": "python",
            "name": "python3",
        }
        self._save_notebook(real_path, notebook)
        runtime.last_used_at = time.time()
        return {
            "status": "ok",
            "path": relative_path,
            "kernel_status": "selected",
            "ready": True,
            "kernel": {
                "id": python_path,
                "label": Path(python_path).name,
                "python": python_path,
                "type": "headless",
            },
            "message": f"Selected kernel: {python_path}",
            "mode": "headless",
        }

    def _headless_notebook_project_visible(
        self,
        real_path: str,
        relative_path: str,
        *,
        cells: list[dict[str, Any]],
        owner_session_id: str | None = None,
    ) -> dict[str, Any]:
        notebook, _ = self._load_notebook(real_path)
        self._assert_structure_not_leased(
            relative_path=relative_path,
            owner_session_id=owner_session_id,
            operation="project-visible-notebook",
        )
        existing_by_id = {
            self._cell_id(cell, index): cell
            for index, cell in enumerate(notebook.cells)
        }
        incoming_ids = {self._incoming_cell_id(payload) for payload in cells if self._incoming_cell_id(payload)}
        for existing_id in existing_by_id:
            if existing_id not in incoming_ids:
                self._assert_cell_not_leased(
                    relative_path=relative_path,
                    cell_id=existing_id,
                    owner_session_id=owner_session_id,
                    operation="project-visible-notebook",
                )
        for incoming_id in incoming_ids:
            self._assert_cell_not_leased(
                relative_path=relative_path,
                cell_id=incoming_id,
                owner_session_id=owner_session_id,
                operation="project-visible-notebook",
            )
            if owner_session_id is not None:
                self.acquire_cell_lease(
                    session_id=owner_session_id,
                    path=relative_path,
                    cell_id=incoming_id,
                    kind="edit",
                )
        notebook.cells = [
            self._materialize_visible_cell(payload, existing_by_id)
            for payload in cells
        ]
        for index, cell in enumerate(notebook.cells):
            self._ensure_cell_identity(cell, index)
        self._save_notebook(real_path, notebook)
        self._append_activity_event(
            path=relative_path,
            event_type="notebook-projected",
            detail=f"Projected {len(notebook.cells)} visible cells",
            runtime_id=self._selected_runtime_record_for_notebook(relative_path).runtime_id
            if self._selected_runtime_record_for_notebook(relative_path) is not None else None,
            session_id=owner_session_id,
            actor=self._session_actor(owner_session_id, "human"),
        )
        self._append_activity_event(
            path=relative_path,
            event_type="notebook-reset-needed",
            detail="Visible projection changed notebook structure",
            runtime_id=self._selected_runtime_record_for_notebook(relative_path).runtime_id
            if self._selected_runtime_record_for_notebook(relative_path) is not None else None,
            session_id=owner_session_id,
            actor=self._session_actor(owner_session_id, "human"),
        )
        return {
            "status": "ok",
            "path": relative_path,
            "cell_count": len(notebook.cells),
            "mode": "headless",
        }

    def _headless_notebook_edit(
        self,
        real_path: str,
        relative_path: str,
        operations: list[dict[str, Any]],
        *,
        owner_session_id: str | None = None,
    ) -> dict[str, Any]:
        notebook, changed = self._load_notebook(real_path)
        results: list[dict[str, Any]] = []
        actor = self._session_actor(owner_session_id, "agent")
        runtime_id = (
            self._selected_runtime_record_for_notebook(relative_path).runtime_id
            if self._selected_runtime_record_for_notebook(relative_path) is not None else None
        )
        for op in operations:
            command = op.get("op")
            if command == "replace-source":
                index = self._find_cell_index(notebook, cell_id=op.get("cell_id"), cell_index=op.get("cell_index"))
                cell = notebook.cells[index]
                stable_cell_id = self._cell_id(cell, index)
                self._assert_cell_not_leased(
                    relative_path=relative_path,
                    cell_id=stable_cell_id,
                    owner_session_id=owner_session_id,
                    operation="replace-source",
                )
                cell.source = op.get("source", "")
                if cell.cell_type == "code":
                    cell.outputs = []
                    cell.execution_count = None
                    self._clear_cell_runtime_provenance(cell)
                results.append({"op": "replace-source", "changed": True, "cell_id": stable_cell_id, "cell_count": len(notebook.cells)})
                self._append_activity_event(
                    path=relative_path,
                    event_type="cell-source-updated",
                    detail=f"Updated source for cell {index + 1}",
                    actor=actor,
                    session_id=owner_session_id,
                    runtime_id=runtime_id,
                    cell_id=stable_cell_id,
                    cell_index=index,
                    data={"cell": self._cell_payload(cell, index)},
                )
                changed = True
            elif command == "insert":
                self._assert_structure_not_leased(
                    relative_path=relative_path,
                    owner_session_id=owner_session_id,
                    operation="insert",
                )
                index = self._normalize_insert_index(notebook, op.get("at_index", -1))
                cell_type = op.get("cell_type", "code")
                source = op.get("source", "")
                cell = nbformat.v4.new_code_cell(source=source) if cell_type == "code" else nbformat.v4.new_markdown_cell(source=source)
                notebook.cells.insert(index, cell)
                for position, current in enumerate(notebook.cells):
                    self._ensure_cell_identity(current, position)
                inserted_cell = notebook.cells[index]
                results.append({"op": "insert", "changed": True, "cell_id": self._cell_id(inserted_cell, index), "cell_count": len(notebook.cells)})
                self._append_activity_event(
                    path=relative_path,
                    event_type="cell-inserted",
                    detail=f"Inserted {cell_type} cell at index {index}",
                    actor=actor,
                    session_id=owner_session_id,
                    runtime_id=runtime_id,
                    cell_id=self._cell_id(inserted_cell, index),
                    cell_index=index,
                    data={"cell": self._cell_payload(inserted_cell, index)},
                )
                changed = True
            elif command == "delete":
                index = self._find_cell_index(notebook, cell_id=op.get("cell_id"), cell_index=op.get("cell_index"))
                cell = notebook.cells[index]
                stable_cell_id = self._cell_id(cell, index)
                self._assert_structure_not_leased(
                    relative_path=relative_path,
                    owner_session_id=owner_session_id,
                    operation="delete",
                )
                self._assert_cell_not_leased(
                    relative_path=relative_path,
                    cell_id=stable_cell_id,
                    owner_session_id=owner_session_id,
                    operation="delete",
                )
                notebook.cells.pop(index)
                for position, current in enumerate(notebook.cells):
                    self._ensure_cell_identity(current, position)
                self.cell_leases.pop(self._lease_key(relative_path, stable_cell_id), None)
                results.append({"op": "delete", "changed": True, "cell_id": stable_cell_id, "cell_count": len(notebook.cells)})
                self._append_activity_event(
                    path=relative_path,
                    event_type="cell-removed",
                    detail=f"Removed cell at index {index}",
                    actor=actor,
                    session_id=owner_session_id,
                    runtime_id=runtime_id,
                    cell_id=stable_cell_id,
                    cell_index=index,
                    data={"cell_id": stable_cell_id},
                )
                changed = True
            elif command == "move":
                index = self._find_cell_index(notebook, cell_id=op.get("cell_id"), cell_index=op.get("cell_index"))
                to_index = int(op.get("to_index", index))
                if to_index == -1:
                    to_index = len(notebook.cells) - 1
                to_index = max(0, min(to_index, len(notebook.cells) - 1))
                cell = notebook.cells[index]
                stable_cell_id = self._cell_id(cell, index)
                self._assert_structure_not_leased(
                    relative_path=relative_path,
                    owner_session_id=owner_session_id,
                    operation="move",
                )
                self._assert_cell_not_leased(
                    relative_path=relative_path,
                    cell_id=stable_cell_id,
                    owner_session_id=owner_session_id,
                    operation="move",
                )
                cell = notebook.cells.pop(index)
                notebook.cells.insert(to_index, cell)
                for position, current in enumerate(notebook.cells):
                    self._ensure_cell_identity(current, position)
                results.append({"op": "move", "changed": True, "cell_id": self._cell_id(cell, to_index), "cell_count": len(notebook.cells)})
                self._append_activity_event(
                    path=relative_path,
                    event_type="notebook-reset-needed",
                    detail=f"Moved cell from index {index} to {to_index}",
                    actor=actor,
                    session_id=owner_session_id,
                    runtime_id=runtime_id,
                    cell_id=self._cell_id(cell, to_index),
                    cell_index=to_index,
                )
                changed = True
            elif command == "clear-outputs":
                if op.get("all"):
                    for index, cell in enumerate(notebook.cells):
                        if cell.cell_type == "code":
                            self._assert_cell_not_leased(
                                relative_path=relative_path,
                                cell_id=self._cell_id(cell, index),
                                owner_session_id=owner_session_id,
                                operation="clear-outputs",
                            )
                            cell.outputs = []
                            cell.execution_count = None
                            self._clear_cell_runtime_provenance(cell)
                            self._append_activity_event(
                                path=relative_path,
                                event_type="cell-outputs-updated",
                                detail=f"Cleared outputs for cell {index + 1}",
                                actor=actor,
                                session_id=owner_session_id,
                                runtime_id=runtime_id,
                                cell_id=self._cell_id(cell, index),
                                cell_index=index,
                                data={"cell": self._cell_payload(cell, index)},
                            )
                    results.append({"op": "clear-outputs", "changed": True, "cell_count": len(notebook.cells)})
                    changed = True
                else:
                    index = self._find_cell_index(notebook, cell_id=op.get("cell_id"), cell_index=op.get("cell_index"))
                    cell = notebook.cells[index]
                    if cell.cell_type == "code":
                        self._assert_cell_not_leased(
                            relative_path=relative_path,
                            cell_id=self._cell_id(cell, index),
                            owner_session_id=owner_session_id,
                            operation="clear-outputs",
                        )
                        cell.outputs = []
                        cell.execution_count = None
                        self._clear_cell_runtime_provenance(cell)
                    stable_cell_id = self._cell_id(cell, index)
                    self._append_activity_event(
                        path=relative_path,
                        event_type="cell-outputs-updated",
                        detail=f"Cleared outputs for cell {index + 1}",
                        actor=actor,
                        session_id=owner_session_id,
                        runtime_id=runtime_id,
                        cell_id=stable_cell_id,
                        cell_index=index,
                        data={"cell": self._cell_payload(cell, index)},
                    )
                    results.append({"op": "clear-outputs", "changed": True, "cell_id": stable_cell_id, "cell_count": len(notebook.cells)})
                    changed = True
            else:
                raise RuntimeError(f"Unsupported headless edit operation: {command}")
        if changed:
            self._save_notebook(real_path, notebook)
        return {"path": relative_path, "results": results}

    def _execute_source(
        self,
        runtime: HeadlessNotebookRuntime,
        source: str,
        *,
        cell_id: str,
        cell_index: int,
        owner_session_id: str | None = None,
    ) -> tuple[list[Any], int | None, str | None]:
        return self._notebook_execution_service.execute_source(
            runtime,
            source,
            cell_id=cell_id,
            cell_index=cell_index,
            owner_session_id=owner_session_id,
        )

    def _headless_notebook_execute_cell(
        self,
        real_path: str,
        relative_path: str,
        *,
        cell_id: str | None,
        cell_index: int | None,
        owner_session_id: str | None = None,
    ) -> dict[str, Any]:
        return self._notebook_execution_service.execute_cell(
            real_path,
            relative_path,
            cell_id=cell_id,
            cell_index=cell_index,
            owner_session_id=owner_session_id,
        )

    def _headless_notebook_insert_execute(
        self,
        real_path: str,
        relative_path: str,
        *,
        source: str,
        cell_type: str,
        at_index: int,
        owner_session_id: str | None = None,
    ) -> dict[str, Any]:
        return self._notebook_execution_service.insert_execute(
            real_path,
            relative_path,
            source=source,
            cell_type=cell_type,
            at_index=at_index,
            owner_session_id=owner_session_id,
        )

    def _headless_notebook_execute_all(
        self,
        real_path: str,
        relative_path: str,
        *,
        owner_session_id: str | None = None,
    ) -> dict[str, Any]:
        return self._notebook_execution_service.execute_all(
            real_path,
            relative_path,
            owner_session_id=owner_session_id,
        )

    def _headless_notebook_execute_visible_cell(
        self,
        real_path: str,
        relative_path: str,
        *,
        cell_index: int,
        source: str,
        owner_session_id: str | None = None,
    ) -> dict[str, Any]:
        return self._notebook_execution_service.execute_visible_cell(
            real_path,
            relative_path,
            cell_index=cell_index,
            source=source,
            owner_session_id=owner_session_id,
        )

    def _headless_notebook_restart(self, real_path: str, relative_path: str) -> dict[str, Any]:
        return self._notebook_execution_service.restart(real_path, relative_path)

    def _headless_notebook_restart_and_run_all(
        self,
        real_path: str,
        relative_path: str,
        *,
        owner_session_id: str | None = None,
    ) -> dict[str, Any]:
        return self._notebook_execution_service.restart_and_run_all(
            real_path,
            relative_path,
            owner_session_id=owner_session_id,
        )

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

    def request_branch_review(
        self,
        *,
        branch_id: str,
        requested_by_session_id: str,
        note: str | None = None,
    ) -> tuple[dict[str, Any], HTTPStatus]:
        branch = self.branch_records.get(branch_id)
        if branch is None:
            return {"error": f"Unknown branch_id: {branch_id}"}, HTTPStatus.NOT_FOUND
        if branch.status != "active":
            return {"error": f"Branch is not reviewable in status '{branch.status}'"}, HTTPStatus.BAD_REQUEST
        session = self.session_records.get(requested_by_session_id)
        if session is None:
            return {"error": f"Unknown requested_by_session_id: {requested_by_session_id}"}, HTTPStatus.BAD_REQUEST
        now = time.time()
        branch.review_status = "requested"
        branch.review_requested_by_session_id = requested_by_session_id
        branch.review_requested_at = now
        branch.review_resolved_by_session_id = None
        branch.review_resolved_at = None
        branch.review_resolution = None
        branch.review_note = note
        branch.updated_at = now
        document = self.document_records.get(branch.document_id)
        if document is not None:
            self._append_activity_event(
                path=document.relative_path,
                event_type="review-requested",
                detail=f"{session.actor} requested review for branch {branch_id}",
                actor=session.actor,
                session_id=requested_by_session_id,
            )
        self.persist()
        return {
            "status": "ok",
            "branch": branch.payload(),
            "workspace_root": self.workspace_root,
        }, HTTPStatus.OK

    def resolve_branch_review(
        self,
        *,
        branch_id: str,
        resolved_by_session_id: str,
        resolution: str,
        note: str | None = None,
    ) -> tuple[dict[str, Any], HTTPStatus]:
        if resolution not in {"approved", "changes-requested", "rejected"}:
            return {"error": f"Invalid review resolution: {resolution}"}, HTTPStatus.BAD_REQUEST
        branch = self.branch_records.get(branch_id)
        if branch is None:
            return {"error": f"Unknown branch_id: {branch_id}"}, HTTPStatus.NOT_FOUND
        if branch.review_status != "requested":
            return {"error": f"Branch review is not pending for {branch_id}"}, HTTPStatus.BAD_REQUEST
        session = self.session_records.get(resolved_by_session_id)
        if session is None:
            return {"error": f"Unknown resolved_by_session_id: {resolved_by_session_id}"}, HTTPStatus.BAD_REQUEST
        now = time.time()
        branch.review_status = "resolved"
        branch.review_resolved_by_session_id = resolved_by_session_id
        branch.review_resolved_at = now
        branch.review_resolution = resolution
        branch.review_note = note or branch.review_note
        branch.updated_at = now
        document = self.document_records.get(branch.document_id)
        if document is not None:
            self._append_activity_event(
                path=document.relative_path,
                event_type="review-resolved",
                detail=f"{session.actor} resolved branch {branch_id} review as {resolution}",
                actor=session.actor,
                session_id=resolved_by_session_id,
            )
        self.persist()
        return {
            "status": "ok",
            "branch": branch.payload(),
            "workspace_root": self.workspace_root,
        }, HTTPStatus.OK

    def list_runtimes_payload(self) -> dict[str, Any]:
        self._reap_expired_runtimes()
        runtimes = [record.payload() for record in list(self.runtime_records.values())]
        return {
            "status": "ok",
            "runtimes": runtimes,
            "count": len(runtimes),
            "workspace_root": self.workspace_root,
        }

    def start_runtime(
        self,
        *,
        runtime_id: str,
        mode: str,
        label: str | None,
        environment: str | None,
        document_path: str | None = None,
        branch_id: str | None = None,
        ttl_seconds: int | None = None,
    ) -> dict[str, Any]:
        expires_at = (time.time() + ttl_seconds) if (mode == "ephemeral" and ttl_seconds and ttl_seconds > 0) else None
        relative_document_path: str | None = None
        effective_runtime_id = runtime_id
        if document_path and mode in {"headless", "shared", "pinned", "ephemeral"}:
            real_path = os.path.realpath(os.path.join(self.workspace_root, document_path) if not os.path.isabs(document_path) else document_path)
            relative_document_path = (
                document_path if not os.path.isabs(document_path) else os.path.relpath(real_path, self.workspace_root)
            )
            selected = self._selected_runtime_record_for_notebook(relative_document_path)
            if selected is not None:
                effective_runtime_id = selected.runtime_id
        created = effective_runtime_id not in self.runtime_records
        existing = self.runtime_records.get(effective_runtime_id)
        initial_status = "provisioning" if relative_document_path and mode in {"headless", "shared", "pinned", "ephemeral"} else "idle"
        record = self._upsert_runtime_record(
            runtime_id=effective_runtime_id,
            mode=mode,
            label=label,
            environment=environment,
            status=initial_status,
            document_path=relative_document_path or document_path,
            branch_id=branch_id,
            health="healthy",
            kernel_generation=(
                self.runtime_records.get(effective_runtime_id).kernel_generation
                if effective_runtime_id in self.runtime_records
                else 1
            ),
            expires_at=expires_at,
        )
        if runtime_id != effective_runtime_id:
            aliased = self.runtime_records.get(runtime_id)
            if aliased is not None and aliased.document_path == (relative_document_path or document_path):
                self.runtime_records.pop(runtime_id, None)
        if relative_document_path and mode in {"headless", "shared", "pinned", "ephemeral"}:
            runtime = self._ensure_headless_runtime(real_path, kernel_id=environment)
            record = self._sync_headless_runtime_record(
                relative_path=relative_document_path,
                runtime=runtime,
                mode=mode,
                expires_at=expires_at,
            )
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
        if record.document_path:
            real_path = os.path.realpath(os.path.join(self.workspace_root, record.document_path))
            live_runtime = self.headless_runtimes.get(real_path)
            if live_runtime is not None and live_runtime.runtime_id == runtime_id:
                self._shutdown_headless_runtime(real_path)
                record = self.runtime_records.get(runtime_id) or record
        if record.status not in {"stopped", "reaped"}:
            self._transition_runtime_record(record, "draining", health="healthy", reason="stop-runtime")
            self._transition_runtime_record(record, "stopped", health="healthy", reason="stop-runtime")
        self.persist()
        return {
            "status": "ok",
            "runtime": record.payload(),
            "workspace_root": self.workspace_root,
        }, HTTPStatus.OK

    def recover_runtime(self, runtime_id: str) -> tuple[dict[str, Any], HTTPStatus]:
        record = self.runtime_records.get(runtime_id)
        if record is None:
            return {"error": f"Unknown runtime_id: {runtime_id}"}, HTTPStatus.NOT_FOUND
        if not record.document_path:
            return {"error": f"Runtime is not notebook-bound: {runtime_id}"}, HTTPStatus.BAD_REQUEST
        if record.mode == "ephemeral" and record.status in {"stopped", "reaped"}:
            return {
                "error": f"Ephemeral runtime was discarded: {runtime_id}. Start a new runtime instead.",
            }, HTTPStatus.BAD_REQUEST

        real_path = os.path.realpath(os.path.join(self.workspace_root, record.document_path))
        live_runtime = self.headless_runtimes.get(real_path)
        if live_runtime is not None and live_runtime.runtime_id != runtime_id:
            return {
                "error": f"Runtime identity mismatch for document-bound runtime: {runtime_id}",
            }, HTTPStatus.CONFLICT
        if live_runtime is not None and live_runtime.busy:
            return {"error": f"Runtime is busy: {runtime_id}"}, HTTPStatus.CONFLICT

        previous_status = record.status
        previous_generation = record.kernel_generation
        if live_runtime is not None:
            self._shutdown_headless_runtime(real_path)

        recovered = self._ensure_headless_runtime(real_path, kernel_id=record.environment)
        recovered.kernel_generation = max(recovered.kernel_generation, previous_generation + 1)
        refreshed = self._sync_headless_runtime_record(
            relative_path=record.document_path,
            runtime=recovered,
            status="idle",
            health="healthy",
            mode=record.mode,
            expires_at=record.expires_at,
        )
        self._append_activity_event(
            path=record.document_path,
            event_type="runtime-recovered",
            detail=f"Recovered runtime {runtime_id} from {previous_status}",
            runtime_id=runtime_id,
        )
        self.persist()
        return {
            "status": "ok",
            "recovered_from": previous_status,
            "runtime": refreshed.payload(),
            "workspace_root": self.workspace_root,
        }, HTTPStatus.OK

    def promote_runtime(self, runtime_id: str, *, mode: str = "shared") -> tuple[dict[str, Any], HTTPStatus]:
        if mode not in {"shared", "pinned"}:
            return {"error": f"Invalid promotion mode: {mode}"}, HTTPStatus.BAD_REQUEST
        record = self.runtime_records.get(runtime_id)
        if record is None:
            return {"error": f"Unknown runtime_id: {runtime_id}"}, HTTPStatus.NOT_FOUND
        if record.mode != "ephemeral":
            return {"error": f"Runtime is not ephemeral: {runtime_id}"}, HTTPStatus.BAD_REQUEST
        if record.status in {"reaped", "stopped"}:
            return {"error": f"Ephemeral runtime is no longer promotable: {runtime_id}"}, HTTPStatus.BAD_REQUEST
        if record.status == "busy":
            return {"error": f"Runtime is busy and cannot be promoted yet: {runtime_id}"}, HTTPStatus.CONFLICT
        previous_mode = record.mode
        record.mode = mode
        record.expires_at = None
        record.updated_at = time.time()
        if record.document_path:
            self._append_activity_event(
                path=record.document_path,
                event_type="runtime-promoted",
                detail=f"Promoted runtime {runtime_id} from {previous_mode} to {mode}",
                runtime_id=runtime_id,
                data={"from_mode": previous_mode, "to_mode": mode},
            )
        self.persist()
        return {
            "status": "ok",
            "runtime": record.payload(),
            "workspace_root": self.workspace_root,
        }, HTTPStatus.OK

    def discard_runtime(self, runtime_id: str) -> tuple[dict[str, Any], HTTPStatus]:
        record = self.runtime_records.get(runtime_id)
        if record is None:
            return {"error": f"Unknown runtime_id: {runtime_id}"}, HTTPStatus.NOT_FOUND
        if record.mode != "ephemeral":
            return {"error": f"Runtime is not ephemeral: {runtime_id}"}, HTTPStatus.BAD_REQUEST
        if record.status == "reaped":
            return {
                "status": "ok",
                "discarded": False,
                "runtime": record.payload(),
                "workspace_root": self.workspace_root,
            }, HTTPStatus.OK
        if record.document_path:
            real_path = os.path.realpath(os.path.join(self.workspace_root, record.document_path))
            live_runtime = self.headless_runtimes.get(real_path)
            if live_runtime is not None and live_runtime.runtime_id == runtime_id:
                self._shutdown_headless_runtime(real_path)
                record = self.runtime_records.get(runtime_id) or record
        if record.status not in {"stopped", "failed"}:
            if record.status in {"idle", "busy", "detached", "degraded"}:
                self._transition_runtime_record(record, "draining", health="healthy", reason="discard-runtime")
            self._transition_runtime_record(record, "stopped", health="healthy", reason="discard-runtime")
        self._transition_runtime_record(record, "reaped", health="degraded", reason="discard-runtime")
        if record.document_path:
            self._append_activity_event(
                path=record.document_path,
                event_type="runtime-discarded",
                detail=f"Discarded ephemeral runtime {runtime_id}",
                runtime_id=runtime_id,
                data={"mode": record.mode},
            )
        self.persist()
        return {
            "status": "ok",
            "discarded": True,
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
        self._reap_expired_runtimes()
        runtime = self.runtime_records.get(runtime_id)
        if runtime is None:
            return {"error": f"Unknown runtime_id: {runtime_id}"}, HTTPStatus.BAD_REQUEST
        if runtime.status in {"stopped", "reaped", "failed"}:
            return {"error": f"Runtime is not runnable: {runtime_id}"}, HTTPStatus.BAD_REQUEST
        if runtime.status == "recovery-needed":
            return {"error": f"Runtime requires recovery: {runtime_id}"}, HTTPStatus.BAD_REQUEST
        if runtime.status == "degraded":
            return {"error": f"Runtime is degraded and must be recovered before new runs: {runtime_id}"}, HTTPStatus.BAD_REQUEST
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
        self._transition_runtime_record(runtime, "busy", health="healthy", reason=f"run-start:{run_id}")
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
            target_status = "busy" if active_runtime_runs else "idle"
            target_health = "degraded" if status == "failed" else runtime.health
            self._transition_runtime_record(runtime, target_status, health=target_health, reason=f"run-finish:{run_id}")
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
    runtime_file = os.path.join(runtime_dir, f"agent-repl-core-{state.pid}.json")
    state.runtime_file = runtime_file
    Path(runtime_file).write_text(json.dumps({
        "pid": state.pid,
        "port": actual_port,
        "token": token,
        "version": state.version,
        "code_hash": _current_package_hash(),
        "workspace_root": workspace_root,
        "started_at": state.started_at,
    }))

    startup_hash = _current_package_hash()

    def _staleness_watchdog() -> None:
        while True:
            time.sleep(60)
            try:
                if _current_package_hash() != startup_hash:
                    server.shutdown()
                    return
            except Exception:
                continue

    watchdog = threading.Thread(target=_staleness_watchdog, daemon=True)
    watchdog.start()

    try:
        server.serve_forever(poll_interval=0.2)
    finally:
        try:
            state.shutdown_headless_runtimes()
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
                if self.path == "/api/sessions/resolve":
                    actor = payload.get("actor", "human")
                    if not isinstance(actor, str) or not actor:
                        self._json(HTTPStatus.BAD_REQUEST, {"error": "Missing actor"})
                        return
                    self._json(HTTPStatus.OK, state.resolve_preferred_session(actor))
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
                if self.path == "/api/sessions/presence/upsert":
                    session_id = payload.get("session_id")
                    path = payload.get("path")
                    activity = payload.get("activity")
                    cell_id = payload.get("cell_id")
                    cell_index = payload.get("cell_index")
                    if not isinstance(session_id, str) or not session_id:
                        self._json(HTTPStatus.BAD_REQUEST, {"error": "Missing session_id"})
                        return
                    if not isinstance(path, str) or not path:
                        self._json(HTTPStatus.BAD_REQUEST, {"error": "Missing path"})
                        return
                    if not isinstance(activity, str) or not activity:
                        self._json(HTTPStatus.BAD_REQUEST, {"error": "Missing activity"})
                        return
                    body, status = state.upsert_notebook_presence(
                        session_id=session_id,
                        path=path,
                        activity=activity,
                        cell_id=cell_id if isinstance(cell_id, str) else None,
                        cell_index=cell_index if isinstance(cell_index, int) else None,
                    )
                    self._json(status, body)
                    return
                if self.path == "/api/sessions/presence/clear":
                    session_id = payload.get("session_id")
                    path = payload.get("path")
                    if not isinstance(session_id, str) or not session_id:
                        self._json(HTTPStatus.BAD_REQUEST, {"error": "Missing session_id"})
                        return
                    body, status = state.clear_notebook_presence(
                        session_id=session_id,
                        path=path if isinstance(path, str) else None,
                    )
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
                    owner_session_id = payload.get("owner_session_id")
                    if not isinstance(path, str) or not path:
                        self._json(HTTPStatus.BAD_REQUEST, {"error": "Missing path"})
                        return
                    if not isinstance(operations, list):
                        self._json(HTTPStatus.BAD_REQUEST, {"error": "Missing operations"})
                        return
                    body, status = state.notebook_edit(
                        path,
                        [item for item in operations if isinstance(item, dict)],
                        owner_session_id=owner_session_id if isinstance(owner_session_id, str) else None,
                    )
                    self._json(status, body)
                    return
                if self.path == "/api/notebooks/select-kernel":
                    path = payload.get("path")
                    kernel_id = payload.get("kernel_id")
                    if not isinstance(path, str) or not path:
                        self._json(HTTPStatus.BAD_REQUEST, {"error": "Missing path"})
                        return
                    body, status = state.notebook_select_kernel(
                        path,
                        kernel_id=kernel_id if isinstance(kernel_id, str) else None,
                    )
                    self._json(status, body)
                    return
                if self.path == "/api/notebooks/execute-cell":
                    path = payload.get("path")
                    cell_id = payload.get("cell_id")
                    cell_index = payload.get("cell_index")
                    owner_session_id = payload.get("owner_session_id")
                    if not isinstance(path, str) or not path:
                        self._json(HTTPStatus.BAD_REQUEST, {"error": "Missing path"})
                        return
                    body, status = state.notebook_execute_cell(
                        path,
                        cell_id=cell_id if isinstance(cell_id, str) else None,
                        cell_index=cell_index if isinstance(cell_index, int) else None,
                        owner_session_id=owner_session_id if isinstance(owner_session_id, str) else None,
                    )
                    self._json(status, body)
                    return
                if self.path == "/api/notebooks/insert-and-execute":
                    path = payload.get("path")
                    source = payload.get("source")
                    cell_type = payload.get("cell_type")
                    at_index = payload.get("at_index")
                    owner_session_id = payload.get("owner_session_id")
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
                        owner_session_id=owner_session_id if isinstance(owner_session_id, str) else None,
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
                if self.path == "/api/notebooks/interrupt":
                    path = payload.get("path")
                    if not isinstance(path, str) or not path:
                        self._json(HTTPStatus.BAD_REQUEST, {"error": "Missing path"})
                        return
                    body, status = state.notebook_interrupt(path)
                    self._json(status, body)
                    return
                if self.path == "/api/notebooks/runtime":
                    path = payload.get("path")
                    if not isinstance(path, str) or not path:
                        self._json(HTTPStatus.BAD_REQUEST, {"error": "Missing path"})
                        return
                    body, status = state.notebook_runtime(path)
                    self._json(status, body)
                    return
                if self.path == "/api/notebooks/projection":
                    path = payload.get("path")
                    if not isinstance(path, str) or not path:
                        self._json(HTTPStatus.BAD_REQUEST, {"error": "Missing path"})
                        return
                    body, status = state.notebook_projection(path)
                    self._json(status, body)
                    return
                if self.path == "/api/notebooks/activity":
                    path = payload.get("path")
                    since = payload.get("since")
                    if not isinstance(path, str) or not path:
                        self._json(HTTPStatus.BAD_REQUEST, {"error": "Missing path"})
                        return
                    body, status = state.notebook_activity(path, since=since if isinstance(since, (int, float)) else None)
                    self._json(status, body)
                    return
                if self.path == "/api/notebooks/project-visible":
                    path = payload.get("path")
                    cells = payload.get("cells")
                    owner_session_id = payload.get("owner_session_id")
                    if not isinstance(path, str) or not path:
                        self._json(HTTPStatus.BAD_REQUEST, {"error": "Missing path"})
                        return
                    if not isinstance(cells, list):
                        self._json(HTTPStatus.BAD_REQUEST, {"error": "Missing cells"})
                        return
                    body, status = state.notebook_project_visible(
                        path,
                        cells=[item for item in cells if isinstance(item, dict)],
                        owner_session_id=owner_session_id if isinstance(owner_session_id, str) else None,
                    )
                    self._json(status, body)
                    return
                if self.path == "/api/notebooks/execute-visible-cell":
                    path = payload.get("path")
                    cell_index = payload.get("cell_index")
                    source = payload.get("source")
                    owner_session_id = payload.get("owner_session_id")
                    if not isinstance(path, str) or not path:
                        self._json(HTTPStatus.BAD_REQUEST, {"error": "Missing path"})
                        return
                    if not isinstance(cell_index, int):
                        self._json(HTTPStatus.BAD_REQUEST, {"error": "Missing cell_index"})
                        return
                    if not isinstance(source, str):
                        self._json(HTTPStatus.BAD_REQUEST, {"error": "Missing source"})
                        return
                    body, status = state.notebook_execute_visible_cell(
                        path,
                        cell_index=cell_index,
                        source=source,
                        owner_session_id=owner_session_id if isinstance(owner_session_id, str) else None,
                    )
                    self._json(status, body)
                    return
                if self.path == "/api/notebooks/lease/acquire":
                    path = payload.get("path")
                    session_id = payload.get("session_id")
                    cell_id = payload.get("cell_id")
                    cell_index = payload.get("cell_index")
                    kind = payload.get("kind")
                    ttl_seconds = payload.get("ttl_seconds")
                    if not isinstance(path, str) or not path:
                        self._json(HTTPStatus.BAD_REQUEST, {"error": "Missing path"})
                        return
                    if not isinstance(session_id, str) or not session_id:
                        self._json(HTTPStatus.BAD_REQUEST, {"error": "Missing session_id"})
                        return
                    body, status = state.acquire_cell_lease(
                        session_id=session_id,
                        path=path,
                        cell_id=cell_id if isinstance(cell_id, str) else None,
                        cell_index=cell_index if isinstance(cell_index, int) else None,
                        kind=kind if isinstance(kind, str) else "edit",
                        ttl_seconds=float(ttl_seconds) if isinstance(ttl_seconds, (int, float)) else None,
                    )
                    self._json(status, body)
                    return
                if self.path == "/api/notebooks/lease/release":
                    path = payload.get("path")
                    session_id = payload.get("session_id")
                    cell_id = payload.get("cell_id")
                    cell_index = payload.get("cell_index")
                    if not isinstance(path, str) or not path:
                        self._json(HTTPStatus.BAD_REQUEST, {"error": "Missing path"})
                        return
                    if not isinstance(session_id, str) or not session_id:
                        self._json(HTTPStatus.BAD_REQUEST, {"error": "Missing session_id"})
                        return
                    body, status = state.release_cell_lease(
                        session_id=session_id,
                        path=path,
                        cell_id=cell_id if isinstance(cell_id, str) else None,
                        cell_index=cell_index if isinstance(cell_index, int) else None,
                    )
                    self._json(status, body)
                    return
                if self.path == "/api/notebooks/restart":
                    path = payload.get("path")
                    if not isinstance(path, str) or not path:
                        self._json(HTTPStatus.BAD_REQUEST, {"error": "Missing path"})
                        return
                    body, status = state.notebook_restart(path)
                    self._json(status, body)
                    return
                if self.path == "/api/notebooks/execute-all":
                    path = payload.get("path")
                    owner_session_id = payload.get("owner_session_id")
                    if not isinstance(path, str) or not path:
                        self._json(HTTPStatus.BAD_REQUEST, {"error": "Missing path"})
                        return
                    body, status = state.notebook_execute_all(
                        path,
                        owner_session_id=owner_session_id if isinstance(owner_session_id, str) else None,
                    )
                    self._json(status, body)
                    return
                if self.path == "/api/notebooks/restart-and-run-all":
                    path = payload.get("path")
                    owner_session_id = payload.get("owner_session_id")
                    if not isinstance(path, str) or not path:
                        self._json(HTTPStatus.BAD_REQUEST, {"error": "Missing path"})
                        return
                    body, status = state.notebook_restart_and_run_all(
                        path,
                        owner_session_id=owner_session_id if isinstance(owner_session_id, str) else None,
                    )
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
                if self.path == "/api/branches/review-request":
                    branch_id = payload.get("branch_id")
                    requested_by_session_id = payload.get("requested_by_session_id")
                    note = payload.get("note")
                    if not isinstance(branch_id, str) or not branch_id:
                        self._json(HTTPStatus.BAD_REQUEST, {"error": "Missing branch_id"})
                        return
                    if not isinstance(requested_by_session_id, str) or not requested_by_session_id:
                        self._json(HTTPStatus.BAD_REQUEST, {"error": "Missing requested_by_session_id"})
                        return
                    body, status = state.request_branch_review(
                        branch_id=branch_id,
                        requested_by_session_id=requested_by_session_id,
                        note=note if isinstance(note, str) else None,
                    )
                    self._json(status, body)
                    return
                if self.path == "/api/branches/review-resolve":
                    branch_id = payload.get("branch_id")
                    resolved_by_session_id = payload.get("resolved_by_session_id")
                    resolution = payload.get("resolution")
                    note = payload.get("note")
                    if not isinstance(branch_id, str) or not branch_id:
                        self._json(HTTPStatus.BAD_REQUEST, {"error": "Missing branch_id"})
                        return
                    if not isinstance(resolved_by_session_id, str) or not resolved_by_session_id:
                        self._json(HTTPStatus.BAD_REQUEST, {"error": "Missing resolved_by_session_id"})
                        return
                    if not isinstance(resolution, str) or not resolution:
                        self._json(HTTPStatus.BAD_REQUEST, {"error": "Missing resolution"})
                        return
                    body, status = state.resolve_branch_review(
                        branch_id=branch_id,
                        resolved_by_session_id=resolved_by_session_id,
                        resolution=resolution,
                        note=note if isinstance(note, str) else None,
                    )
                    self._json(status, body)
                    return
                if self.path == "/api/runtimes/start":
                    runtime_id = payload.get("runtime_id")
                    mode = payload.get("mode")
                    label = payload.get("label")
                    environment = payload.get("environment")
                    document_path = payload.get("document_path")
                    ttl_seconds = payload.get("ttl_seconds")
                    if not isinstance(runtime_id, str) or not runtime_id:
                        self._json(HTTPStatus.BAD_REQUEST, {"error": "Missing runtime_id"})
                        return
                    if not isinstance(mode, str) or mode not in {"interactive", "shared", "headless", "pinned", "ephemeral"}:
                        self._json(HTTPStatus.BAD_REQUEST, {"error": "Invalid mode"})
                        return
                    if document_path is not None and not isinstance(document_path, str):
                        self._json(HTTPStatus.BAD_REQUEST, {"error": "Invalid document_path"})
                        return
                    if ttl_seconds is not None and not isinstance(ttl_seconds, int):
                        self._json(HTTPStatus.BAD_REQUEST, {"error": "Invalid ttl_seconds"})
                        return
                    self._json(HTTPStatus.OK, state.start_runtime(
                        runtime_id=runtime_id,
                        mode=mode,
                        label=label if isinstance(label, str) else None,
                        environment=environment if isinstance(environment, str) else None,
                        document_path=document_path if isinstance(document_path, str) else None,
                        ttl_seconds=ttl_seconds if isinstance(ttl_seconds, int) else None,
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
                if self.path == "/api/runtimes/recover":
                    runtime_id = payload.get("runtime_id")
                    if not isinstance(runtime_id, str) or not runtime_id:
                        self._json(HTTPStatus.BAD_REQUEST, {"error": "Missing runtime_id"})
                        return
                    body, status = state.recover_runtime(runtime_id)
                    self._json(status, body)
                    return
                if self.path == "/api/runtimes/promote":
                    runtime_id = payload.get("runtime_id")
                    mode = payload.get("mode", "shared")
                    if not isinstance(runtime_id, str) or not runtime_id:
                        self._json(HTTPStatus.BAD_REQUEST, {"error": "Missing runtime_id"})
                        return
                    if not isinstance(mode, str) or mode not in {"shared", "pinned"}:
                        self._json(HTTPStatus.BAD_REQUEST, {"error": "Invalid mode"})
                        return
                    body, status = state.promote_runtime(runtime_id, mode=mode)
                    self._json(status, body)
                    return
                if self.path == "/api/runtimes/discard":
                    runtime_id = payload.get("runtime_id")
                    if not isinstance(runtime_id, str) or not runtime_id:
                        self._json(HTTPStatus.BAD_REQUEST, {"error": "Missing runtime_id"})
                        return
                    body, status = state.discard_runtime(runtime_id)
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

        def _parse_request(self, payload: dict[str, Any], request_type: Any) -> Any | None:
            try:
                return request_type.from_payload(payload)
            except ValueError as err:
                self._json(HTTPStatus.BAD_REQUEST, {"error": str(err)})
                return None

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


def _current_package_hash() -> str:
    package_root = Path(__file__).resolve().parents[1]
    digest = hashlib.sha256()
    for source in sorted(package_root.rglob("*.py")):
        try:
            digest.update(str(source.relative_to(package_root)).encode("utf-8"))
            digest.update(source.read_bytes())
        except OSError:
            continue
    return digest.hexdigest()


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
        version=str(payload.get("version") or CORE_VERSION),
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
        activity_records=[
            ActivityEventRecord(**record)
            for record in payload.get("activity", [])
            if isinstance(record, dict) and isinstance(record.get("event_id"), str)
        ],
    )
    _normalize_restored_state(state)
    state.persist()
    return state


def _normalize_restored_state(state: CoreState) -> None:
    now = time.time()
    if len(state.activity_records) > MAX_ACTIVITY_RECORDS:
        state.activity_records = state.activity_records[-MAX_ACTIVITY_RECORDS:]
    for session in state.session_records.values():
        if session.status in {"attached", "stale"}:
            session.status = "detached"
            session.last_seen_at = now
    for runtime in state.runtime_records.values():
        if runtime.status in {"ready", "idle", "busy", "detached", "degraded"}:
            runtime.status = "recovery-needed"
            runtime.health = "degraded"
            runtime.updated_at = now
    for run in state.run_records.values():
        if run.status in {"queued", "running"}:
            run.status = "interrupted"
            run.updated_at = now
    state._recompute_counts()
