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
from pathlib import Path
from typing import Any

import nbformat
from jupyter_client import KernelManager
from jupyter_client.kernelspec import KernelSpec

from agent_repl.client import BridgeClient
from agent_repl.core.collaboration import CollaborationConflictError
from agent_repl.core.collaboration_service import CollaborationService
from agent_repl.core.execution_ledger_service import ExecutionLedgerService
from agent_repl.core.notebook_execution_service import NotebookExecutionService
from agent_repl.core.notebook_mutation_service import NotebookMutationService
from agent_repl.core.notebook_read_service import NotebookReadService
from agent_repl.core.notebook_write_service import NotebookWriteService
from agent_repl.core.ydoc_service import YDocService


CORE_VERSION = "0.1.0"
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
    queue_position: int | None = None

    def payload(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "runtime_id": self.runtime_id,
            "target_type": self.target_type,
            "target_ref": self.target_ref,
            "kind": self.kind,
            "status": self.status,
            "queue_position": self.queue_position,
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
    execution_records: dict[str, dict[str, Any]] = field(default_factory=dict, repr=False)
    activity_records: list[ActivityEventRecord] = field(default_factory=list, repr=False)
    headless_runtimes: dict[str, HeadlessNotebookRuntime] = field(default_factory=dict, repr=False)
    _validated_kernel_pythons: set[str] = field(default_factory=set, init=False, repr=False)
    _last_activity_timestamp: float = field(default=0.0, init=False, repr=False)
    _lock: threading.RLock = field(default_factory=threading.RLock, init=False, repr=False)
    _notebook_locks: dict[str, threading.RLock] = field(default_factory=dict, init=False, repr=False)
    _notebook_locks_guard: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)
    _collaboration_service: CollaborationService = field(init=False, repr=False)
    _execution_ledger_service: ExecutionLedgerService = field(init=False, repr=False)
    _notebook_execution_service: NotebookExecutionService = field(init=False, repr=False)
    _notebook_mutation_service: NotebookMutationService = field(init=False, repr=False)
    _notebook_read_service: NotebookReadService = field(init=False, repr=False)
    _notebook_write_service: NotebookWriteService = field(init=False, repr=False)
    _ydoc_service: YDocService = field(init=False, repr=False)
    _db: Any = field(default=None, init=False, repr=False)

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
        self._collaboration_service = CollaborationService(
            self,
            session_record_type=SessionRecord,
            cell_lease_record_type=CellLeaseRecord,
            notebook_presence_record_type=NotebookPresenceRecord,
            branch_record_type=BranchRecord,
            default_session_capabilities=_default_session_capabilities,
        )
        self._execution_ledger_service = ExecutionLedgerService(
            self,
            run_record_type=RunRecord,
        )
        self._notebook_execution_service = NotebookExecutionService(self)
        self._notebook_mutation_service = NotebookMutationService(self)
        self._notebook_read_service = NotebookReadService(self)
        self._notebook_write_service = NotebookWriteService(self)
        self._ydoc_service = YDocService()
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
        return self._collaboration_service.list_sessions_payload()

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
        self._collaboration_service.clear_session_presence(session_id)

    def _refresh_session_leases(self, session_id: str) -> None:
        self._collaboration_service.refresh_session_leases(session_id)

    def _clear_session_leases(self, session_id: str) -> None:
        self._collaboration_service.clear_session_leases(session_id)

    def _lease_key(self, relative_path: str, cell_id: str) -> str:
        return f"{relative_path}::{cell_id}"

    def _reap_expired_cell_leases(self) -> None:
        self._collaboration_service.reap_expired_cell_leases()

    def _leases_payload_for_path(self, relative_path: str) -> list[dict[str, Any]]:
        return self._collaboration_service.leases_payload_for_path(relative_path)

    def _conflicting_lease(
        self,
        *,
        relative_path: str,
        cell_id: str,
        owner_session_id: str | None,
        kinds: set[str] | None = None,
    ) -> CellLeaseRecord | None:
        return self._collaboration_service.conflicting_lease(
            relative_path=relative_path,
            cell_id=cell_id,
            owner_session_id=owner_session_id,
            kinds=kinds,
        )

    def _active_structure_leases(self, relative_path: str, owner_session_id: str | None) -> list[CellLeaseRecord]:
        return self._collaboration_service.active_structure_leases(relative_path, owner_session_id)

    def _assert_structure_not_leased(
        self,
        *,
        relative_path: str,
        owner_session_id: str | None,
        operation: str,
    ) -> None:
        self._collaboration_service.assert_structure_not_leased(
            relative_path=relative_path,
            owner_session_id=owner_session_id,
            operation=operation,
        )

    def _lease_conflict_payload(
        self,
        *,
        relative_path: str,
        lease: CellLeaseRecord,
        operation: str,
        owner_session_id: str | None = None,
    ) -> dict[str, Any]:
        return self._collaboration_service.lease_conflict_payload(
            relative_path=relative_path,
            lease=lease,
            operation=operation,
            owner_session_id=owner_session_id,
        )

    def _assert_cell_not_leased(
        self,
        *,
        relative_path: str,
        cell_id: str,
        owner_session_id: str | None,
        operation: str,
        kinds: set[str] | None = None,
    ) -> None:
        self._collaboration_service.assert_cell_not_leased(
            relative_path=relative_path,
            cell_id=cell_id,
            owner_session_id=owner_session_id,
            operation=operation,
            kinds=kinds,
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
        return self._collaboration_service.acquire_cell_lease(
            session_id=session_id,
            path=path,
            cell_id=cell_id,
            cell_index=cell_index,
            kind=kind,
            ttl_seconds=ttl_seconds,
        )

    def release_cell_lease(
        self,
        *,
        session_id: str,
        path: str,
        cell_id: str | None = None,
        cell_index: int | None = None,
    ) -> tuple[dict[str, Any], HTTPStatus]:
        return self._collaboration_service.release_cell_lease(
            session_id=session_id,
            path=path,
            cell_id=cell_id,
            cell_index=cell_index,
        )

    def _presence_payload_for_path(self, relative_path: str) -> list[dict[str, Any]]:
        return self._collaboration_service.presence_payload_for_path(relative_path)

    def start_session(
        self,
        actor: str,
        client: str,
        label: str | None,
        session_id: str,
        capabilities: list[str] | None = None,
    ) -> dict[str, Any]:
        return self._collaboration_service.start_session(
            actor,
            client,
            label,
            session_id,
            capabilities=capabilities,
        )

    def resolve_preferred_session(self, actor: str = "human") -> dict[str, Any]:
        return self._collaboration_service.resolve_preferred_session(actor)

    def _refresh_session_liveness(self) -> None:
        self._collaboration_service.refresh_session_liveness()

    def _recompute_counts(self) -> None:
        self._execution_ledger_service.recompute_counts()

    def persist(self) -> None:
        with self._lock:
            if self._db is None:
                return
            from agent_repl.core.db import persist_all
            persist_all(
                self._db,
                sessions=[r.payload() for r in self.session_records.values()],
                documents=[r.payload() for r in self.document_records.values()],
                branches=[r.payload() for r in self.branch_records.values()],
                runtimes=[r.payload() for r in self.runtime_records.values()],
                runs=[r.payload() for r in self.run_records.values()],
                activity=[r.payload() for r in self.activity_records],
                executions=list(self.execution_records.values()),
            )

    def touch_session(self, session_id: str) -> tuple[dict[str, Any], HTTPStatus]:
        return self._collaboration_service.touch_session(session_id)

    def detach_session(self, session_id: str) -> tuple[dict[str, Any], HTTPStatus]:
        return self._collaboration_service.detach_session(session_id)

    def end_session(self, session_id: str) -> tuple[dict[str, Any], HTTPStatus]:
        return self._collaboration_service.end_session(session_id)

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
        payload = self._execution_ledger_service.notebook_execution(execution_id)
        if payload is not None:
            return payload, HTTPStatus.OK
        client = self._projection_client(self.workspace_root)
        if client is None:
            return {"error": f"Unknown execution: {execution_id}"}, HTTPStatus.NOT_FOUND
        fallback_payload = client.execution(execution_id)
        return fallback_payload, HTTPStatus.OK

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
        return self._collaboration_service.upsert_notebook_presence(
            session_id=session_id,
            path=path,
            activity=activity,
            cell_id=cell_id,
            cell_index=cell_index,
        )

    def clear_notebook_presence(self, *, session_id: str, path: str | None = None) -> tuple[dict[str, Any], HTTPStatus]:
        return self._collaboration_service.clear_notebook_presence(session_id=session_id, path=path)

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
        # Shadow-load into YDoc if not already populated
        relative_path = os.path.relpath(real_path, self.workspace_root)
        if not self._ydoc_service.has_cells(relative_path):
            self._sync_notebook_to_ydoc(relative_path, notebook)
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
        # Keep YDoc shadow in sync after every save
        relative_path = os.path.relpath(real_path, self.workspace_root)
        self._sync_notebook_to_ydoc(relative_path, notebook)

    def _sync_notebook_to_ydoc(self, relative_path: str, notebook: Any) -> None:
        """Sync an nbformat notebook into the YDoc shadow."""
        cells = [dict(cell) for cell in notebook.cells]
        self._ydoc_service.close(relative_path)
        self._ydoc_service.load_from_nbformat(relative_path, {"cells": cells})

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
        return self._collaboration_service.session_actor(session_id, fallback)

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
        runtime_record = self._runtime_record_for_notebook(relative_path)
        running: list[dict[str, Any]]
        queued: list[dict[str, Any]]
        if runtime_record is not None:
            running, queued = self._execution_ledger_service.notebook_status(runtime=runtime, runtime_record=runtime_record)
        else:
            running = [dict(runtime.current_execution)] if runtime and runtime.current_execution else []
            queued = []
        return {
            "path": relative_path,
            "open": False,
            "kernel_state": "busy" if runtime and runtime.busy else ("idle" if runtime else "not_open"),
            "busy": bool(runtime and runtime.busy),
            "running": running,
            "queued": queued,
        }

    def _create_notebook_cells(self, cells: list[dict[str, Any]] | None) -> list[Any]:
        return self._notebook_mutation_service.create_notebook_cells(cells)

    def _headless_notebook_create(
        self,
        real_path: str,
        relative_path: str,
        *,
        cells: list[dict[str, Any]] | None,
        kernel_id: str | None,
    ) -> dict[str, Any]:
        return self._notebook_mutation_service.create(
            real_path,
            relative_path,
            cells=cells,
            kernel_id=kernel_id,
        )

    def _headless_notebook_project_visible(
        self,
        real_path: str,
        relative_path: str,
        *,
        cells: list[dict[str, Any]],
        owner_session_id: str | None = None,
    ) -> dict[str, Any]:
        return self._notebook_mutation_service.project_visible(
            real_path,
            relative_path,
            cells=cells,
            owner_session_id=owner_session_id,
        )

    def _headless_notebook_edit(
        self,
        real_path: str,
        relative_path: str,
        operations: list[dict[str, Any]],
        *,
        owner_session_id: str | None = None,
    ) -> dict[str, Any]:
        return self._notebook_mutation_service.edit(
            real_path,
            relative_path,
            operations,
            owner_session_id=owner_session_id,
        )

    def _execute_source(
        self,
        runtime: HeadlessNotebookRuntime,
        source: str,
        *,
        cell_id: str,
        cell_index: int,
        owner_session_id: str | None = None,
        execution_id: str | None = None,
        operation: str = "execute-cell",
    ) -> tuple[list[Any], int | None, str | None]:
        return self._notebook_execution_service.execute_source(
            runtime,
            source,
            cell_id=cell_id,
            cell_index=cell_index,
            owner_session_id=owner_session_id,
            execution_id=execution_id,
            operation=operation,
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
        return self._collaboration_service.start_branch(
            branch_id=branch_id,
            document_id=document_id,
            owner_session_id=owner_session_id,
            parent_branch_id=parent_branch_id,
            title=title,
            purpose=purpose,
        )

    def finish_branch(self, branch_id: str, status: str) -> tuple[dict[str, Any], HTTPStatus]:
        return self._collaboration_service.finish_branch(branch_id, status)

    def request_branch_review(
        self,
        *,
        branch_id: str,
        requested_by_session_id: str,
        note: str | None = None,
    ) -> tuple[dict[str, Any], HTTPStatus]:
        return self._collaboration_service.request_branch_review(
            branch_id=branch_id,
            requested_by_session_id=requested_by_session_id,
            note=note,
        )

    def resolve_branch_review(
        self,
        *,
        branch_id: str,
        resolved_by_session_id: str,
        resolution: str,
        note: str | None = None,
    ) -> tuple[dict[str, Any], HTTPStatus]:
        return self._collaboration_service.resolve_branch_review(
            branch_id=branch_id,
            resolved_by_session_id=resolved_by_session_id,
            resolution=resolution,
            note=note,
        )

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
        return self._execution_ledger_service.list_runs_payload()

    def start_run(
        self,
        *,
        run_id: str,
        runtime_id: str,
        target_type: str,
        target_ref: str,
        kind: str,
    ) -> tuple[dict[str, Any], HTTPStatus]:
        return self._execution_ledger_service.start_run(
            run_id=run_id,
            runtime_id=runtime_id,
            target_type=target_type,
            target_ref=target_ref,
            kind=kind,
        )

    def finish_run(self, run_id: str, status: str) -> tuple[dict[str, Any], HTTPStatus]:
        return self._execution_ledger_service.finish_run(run_id, status)


def serve_forever(
    workspace_root: str,
    *,
    runtime_dir: str,
    token: str | None = None,
    port: int = 0,
) -> None:
    import socket

    import uvicorn

    from agent_repl.core.asgi import create_app

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

    # Resolve an ephemeral port before uvicorn starts so we can write
    # the runtime metadata file with the real port.
    if port == 0:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]

    runtime_file = os.path.join(runtime_dir, f"agent-repl-core-{state.pid}.json")
    state.runtime_file = runtime_file
    Path(runtime_file).write_text(json.dumps({
        "pid": state.pid,
        "port": port,
        "token": token,
        "version": state.version,
        "code_hash": _current_package_hash(),
        "workspace_root": workspace_root,
        "started_at": state.started_at,
    }))

    config = uvicorn.Config(
        create_app(state, shutdown_callback=lambda: setattr(server, "should_exit", True)),
        host="127.0.0.1",
        port=port,
        log_level="error",
    )
    server = uvicorn.Server(config)

    startup_hash = _current_package_hash()

    def _staleness_watchdog() -> None:
        while True:
            time.sleep(60)
            try:
                if _current_package_hash() != startup_hash:
                    server.should_exit = True
                    return
            except Exception:
                continue

    watchdog = threading.Thread(target=_staleness_watchdog, daemon=True)
    watchdog.start()

    try:
        server.run()
    finally:
        try:
            state.shutdown_headless_runtimes()
        finally:
            if state.runtime_file:
                try:
                    os.unlink(state.runtime_file)
                except OSError:
                    pass


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
    from agent_repl.core.db import load_all, migrate_from_json, open_db

    state_file = _state_file_path(workspace_root)
    db = open_db(workspace_root)

    # Migrate from JSON if the old state file still exists.
    migrate_from_json(db, state_file)

    data = load_all(db)

    def _new_state(**extra: Any) -> CoreState:
        s = CoreState(
            workspace_root=workspace_root,
            runtime_dir=runtime_dir,
            token=token,
            pid=pid,
            started_at=started_at,
            state_file=state_file,
            **extra,
        )
        s._db = db
        return s

    if not any(data.values()):
        return _new_state()

    state = _new_state(
        session_records={
            record["session_id"]: SessionRecord(**record)
            for record in data.get("sessions", [])
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
            for record in data.get("documents", [])
            if isinstance(record, dict) and isinstance(record.get("document_id"), str)
        },
        branch_records={
            record["branch_id"]: BranchRecord(**record)
            for record in data.get("branches", [])
            if isinstance(record, dict) and isinstance(record.get("branch_id"), str)
        },
        runtime_records={
            record["runtime_id"]: RuntimeRecord(**record)
            for record in data.get("runtimes", [])
            if isinstance(record, dict) and isinstance(record.get("runtime_id"), str)
        },
        run_records={
            record["run_id"]: RunRecord(**record)
            for record in data.get("runs", [])
            if isinstance(record, dict) and isinstance(record.get("run_id"), str)
        },
        activity_records=[
            ActivityEventRecord(**record)
            for record in data.get("activity", [])
            if isinstance(record, dict) and isinstance(record.get("event_id"), str)
        ],
        execution_records={
            record["execution_id"]: record
            for record in data.get("executions", [])
            if isinstance(record, dict) and isinstance(record.get("execution_id"), str)
        },
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
    for execution in state.execution_records.values():
        if execution.get("status") == "running":
            execution["status"] = "interrupted"
            execution["updated_at"] = now
    state._recompute_counts()
