"""Session, presence, and lease helpers for the core daemon."""
from __future__ import annotations

import time
import uuid
from http import HTTPStatus
from typing import Any

from agent_repl.core.collaboration import (
    CELL_LEASE_TTL_SECONDS,
    SESSION_CLIENT_RANK,
    SESSION_STALE_AFTER_SECONDS,
    SESSION_STATUS_RANK,
    CollaborationConflictError,
)
from agent_repl.recovery import lease_conflict_recovery


class CollaborationService:
    """Own lease, presence, and preferred-session collaboration flows."""

    def __init__(
        self,
        state: Any,
        *,
        session_record_type: type,
        cell_lease_record_type: type,
        notebook_presence_record_type: type,
        branch_record_type: type,
        default_session_capabilities: Any,
    ):
        self.state = state
        self.session_record_type = session_record_type
        self.cell_lease_record_type = cell_lease_record_type
        self.notebook_presence_record_type = notebook_presence_record_type
        self.branch_record_type = branch_record_type
        self.default_session_capabilities = default_session_capabilities

    def list_sessions_payload(self) -> dict[str, Any]:
        self.refresh_session_liveness()
        self.state._recompute_counts()
        return {
            "status": "ok",
            "sessions": [record.payload() for record in self.state.session_records.values()],
            "count": self.state.sessions,
            "workspace_root": self.state.workspace_root,
        }

    def clear_session_presence(self, session_id: str) -> None:
        with self.state._lock:
            self.state.notebook_presence.pop(session_id, None)

    def refresh_session_leases(self, session_id: str) -> None:
        with self.state._lock:
            now = time.time()
            changed = False
            for lease in list(self.state.cell_leases.values()):
                if lease.session_id != session_id:
                    continue
                lease.updated_at = now
                lease.expires_at = now + CELL_LEASE_TTL_SECONDS
                changed = True
        if changed:
            self.state.persist()

    def clear_session_leases(self, session_id: str) -> None:
        with self.state._lock:
            removed = [key for key, lease in list(self.state.cell_leases.items()) if lease.session_id == session_id]
            for key in removed:
                self.state.cell_leases.pop(key, None)

    def reap_expired_cell_leases(self) -> None:
        with self.state._lock:
            now = time.time()
            expired = [key for key, lease in list(self.state.cell_leases.items()) if lease.expires_at <= now]
            for key in expired:
                self.state.cell_leases.pop(key, None)

    def leases_payload_for_path(self, relative_path: str) -> list[dict[str, Any]]:
        self.reap_expired_cell_leases()
        with self.state._lock:
            leases = [lease for lease in list(self.state.cell_leases.values()) if lease.path == relative_path]
            sessions = {session_id: record.payload() for session_id, record in self.state.session_records.items()}
        items: list[dict[str, Any]] = []
        for lease in leases:
            payload = lease.payload()
            payload["session"] = sessions.get(lease.session_id)
            items.append(payload)
        items.sort(key=lambda item: item.get("updated_at", 0), reverse=True)
        return items

    def conflicting_lease(
        self,
        *,
        relative_path: str,
        cell_id: str,
        owner_session_id: str | None,
        kinds: set[str] | None = None,
    ) -> Any | None:
        self.reap_expired_cell_leases()
        with self.state._lock:
            lease = self.state.cell_leases.get(self.state._lease_key(relative_path, cell_id))
        if lease is None:
            return None
        if owner_session_id is not None and lease.session_id == owner_session_id:
            return None
        if kinds is not None and lease.kind not in kinds:
            return None
        return lease

    def active_structure_leases(self, relative_path: str, owner_session_id: str | None) -> list[Any]:
        self.reap_expired_cell_leases()
        with self.state._lock:
            return [
                lease
                for lease in list(self.state.cell_leases.values())
                if lease.path == relative_path and lease.kind == "structure"
                and not (owner_session_id is not None and lease.session_id == owner_session_id)
            ]

    def assert_structure_not_leased(
        self,
        *,
        relative_path: str,
        owner_session_id: str | None,
        operation: str,
    ) -> None:
        structure_leases = self.active_structure_leases(relative_path, owner_session_id)
        if not structure_leases:
            return
        lease = structure_leases[0]
        raise CollaborationConflictError(
            f"Operation '{operation}' is blocked by an active structure lease",
            payload=self.lease_conflict_payload(
                relative_path=relative_path,
                lease=lease,
                operation=operation,
                owner_session_id=owner_session_id,
            ),
        )

    def lease_conflict_payload(
        self,
        *,
        relative_path: str,
        lease: Any,
        operation: str,
        owner_session_id: str | None = None,
    ) -> dict[str, Any]:
        with self.state._lock:
            session = self.state.session_records.get(lease.session_id)
            document = next(
                (record for record in list(self.state.document_records.values()) if record.relative_path == relative_path),
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
            "recovery": lease_conflict_recovery(has_suggested_branch=suggested_branch is not None),
        }

    def assert_cell_not_leased(
        self,
        *,
        relative_path: str,
        cell_id: str,
        owner_session_id: str | None,
        operation: str,
        kinds: set[str] | None = None,
    ) -> None:
        lease = self.conflicting_lease(
            relative_path=relative_path,
            cell_id=cell_id,
            owner_session_id=owner_session_id,
            kinds=kinds,
        )
        if lease is None:
            return
        raise CollaborationConflictError(
            f"Operation '{operation}' is blocked by an active lease",
            payload=self.lease_conflict_payload(
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
        with self.state._lock:
            session = self.state.session_records.get(session_id)
        if session is None:
            return {"error": f"Unknown session_id: {session_id}"}, HTTPStatus.NOT_FOUND
        real_path, relative_path = self.state._resolve_document_path(path)
        with self.state._notebook_lock(real_path):
            notebook, changed = self.state._load_notebook(real_path)
            if changed:
                self.state._save_notebook(real_path, notebook)
            resolved_cell_id = cell_id
            if resolved_cell_id is None and cell_index is not None:
                index = self.state._find_cell_index(notebook, cell_id=None, cell_index=cell_index)
                resolved_cell_id = self.state._cell_id(notebook.cells[index], index)
        if not resolved_cell_id:
            return {"error": "Missing cell_id or cell_index"}, HTTPStatus.BAD_REQUEST
        conflict = self.conflicting_lease(
            relative_path=relative_path,
            cell_id=resolved_cell_id,
            owner_session_id=session_id,
        )
        if conflict is not None:
            return self.lease_conflict_payload(
                relative_path=relative_path,
                lease=conflict,
                operation="lease-acquire",
                owner_session_id=session_id,
            ), HTTPStatus.CONFLICT
        key = self.state._lease_key(relative_path, resolved_cell_id)
        now = time.time()
        expires_at = now + (ttl_seconds if ttl_seconds is not None else CELL_LEASE_TTL_SECONDS)
        with self.state._lock:
            existing = self.state.cell_leases.get(key)
            created = existing is None
            if existing is None:
                lease = self.cell_lease_record_type(
                    lease_id=str(uuid.uuid4()),
                    session_id=session_id,
                    path=relative_path,
                    cell_id=resolved_cell_id,
                    kind=kind,
                    created_at=now,
                    updated_at=now,
                    expires_at=expires_at,
                )
                self.state.cell_leases[key] = lease
            else:
                existing.session_id = session_id
                existing.kind = kind
                existing.updated_at = now
                existing.expires_at = expires_at
                lease = existing
        self.state._append_activity_event(
            path=relative_path,
            event_type="lease-acquired",
            detail=f"{session.actor} acquired {kind} lease",
            actor=session.actor,
            session_id=session_id,
            cell_id=resolved_cell_id,
            cell_index=cell_index,
        )
        self.state.persist()
        payload = lease.payload()
        payload["session"] = session.payload()
        return {"status": "ok", "created": created, "lease": payload, "workspace_root": self.state.workspace_root}, HTTPStatus.OK

    def release_cell_lease(
        self,
        *,
        session_id: str,
        path: str,
        cell_id: str | None = None,
        cell_index: int | None = None,
    ) -> tuple[dict[str, Any], HTTPStatus]:
        with self.state._lock:
            session = self.state.session_records.get(session_id)
        if session is None:
            return {"error": f"Unknown session_id: {session_id}"}, HTTPStatus.NOT_FOUND
        real_path, relative_path = self.state._resolve_document_path(path)
        with self.state._notebook_lock(real_path):
            notebook, changed = self.state._load_notebook(real_path)
            if changed:
                self.state._save_notebook(real_path, notebook)
            resolved_cell_id = cell_id
            if resolved_cell_id is None and cell_index is not None:
                index = self.state._find_cell_index(notebook, cell_id=None, cell_index=cell_index)
                resolved_cell_id = self.state._cell_id(notebook.cells[index], index)
        if not resolved_cell_id:
            return {"error": "Missing cell_id or cell_index"}, HTTPStatus.BAD_REQUEST
        key = self.state._lease_key(relative_path, resolved_cell_id)
        with self.state._lock:
            lease = self.state.cell_leases.get(key)
            if lease is None or lease.session_id != session_id:
                return {"status": "ok", "released": False, "workspace_root": self.state.workspace_root}, HTTPStatus.OK
            removed = self.state.cell_leases.pop(key)
        self.state._append_activity_event(
            path=relative_path,
            event_type="lease-released",
            detail=f"{session.actor} released {removed.kind} lease",
            actor=session.actor,
            session_id=session_id,
            cell_id=resolved_cell_id,
            cell_index=cell_index,
        )
        self.state.persist()
        return {"status": "ok", "released": True, "workspace_root": self.state.workspace_root}, HTTPStatus.OK

    def presence_payload_for_path(self, relative_path: str) -> list[dict[str, Any]]:
        with self.state._lock:
            presence_records = [record.payload() for record in self.state.notebook_presence.values() if record.path == relative_path]
            session_payloads = {session_id: record.payload() for session_id, record in self.state.session_records.items()}
        items: list[dict[str, Any]] = []
        for payload in presence_records:
            payload["session"] = session_payloads.get(payload["session_id"])
            items.append(payload)
        items.sort(key=lambda item: item.get("updated_at", 0), reverse=True)
        return items

    def resolve_preferred_session(self, actor: str = "human") -> dict[str, Any]:
        self.refresh_session_liveness()
        best_record = None
        best_key: tuple[int, int, int, float, float] | None = None
        with self.state._lock:
            sessions = list(self.state.session_records.values())

        for record in sessions:
            if record.actor != actor:
                continue
            status_rank = SESSION_STATUS_RANK.get(record.status, 0)
            if status_rank == 0:
                continue
            client_rank = SESSION_CLIENT_RANK.get(record.client, 0)
            editor_rank = 1 if ("editor" in record.capabilities or record.client == "vscode") else 0
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
            "workspace_root": self.state.workspace_root,
        }

    def refresh_session_liveness(self) -> None:
        with self.state._lock:
            now = time.time()
            changed = False
            for record in self.state.session_records.values():
                if record.status == "attached" and (now - record.last_seen_at) > SESSION_STALE_AFTER_SECONDS:
                    record.status = "stale"
                    changed = True
        if changed:
            self.state.persist()

    def start_session(
        self,
        actor: str,
        client: str,
        label: str | None,
        session_id: str,
        capabilities: list[str] | None = None,
    ) -> dict[str, Any]:
        now = time.time()
        resolved_capabilities = capabilities or self.default_session_capabilities(client)
        existing = self.state.session_records.get(session_id)
        if existing is None:
            record = self.session_record_type(
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
            self.state.session_records[session_id] = record
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
        self.state.sessions = len(self.state.session_records)
        self.state.persist()
        return {
            "status": "ok",
            "created": created,
            "session": record.payload(),
            "workspace_root": self.state.workspace_root,
        }

    def touch_session(self, session_id: str) -> tuple[dict[str, Any], HTTPStatus]:
        record = self.state.session_records.get(session_id)
        if record is None:
            return {"error": f"Unknown session_id: {session_id}"}, HTTPStatus.NOT_FOUND
        record.status = "attached"
        record.last_seen_at = time.time()
        self.refresh_session_leases(session_id)
        self.state.persist()
        return {
            "status": "ok",
            "session": record.payload(),
            "workspace_root": self.state.workspace_root,
        }, HTTPStatus.OK

    def detach_session(self, session_id: str) -> tuple[dict[str, Any], HTTPStatus]:
        record = self.state.session_records.get(session_id)
        if record is None:
            return {"error": f"Unknown session_id: {session_id}"}, HTTPStatus.NOT_FOUND
        record.status = "detached"
        record.last_seen_at = time.time()
        self.clear_session_presence(session_id)
        self.clear_session_leases(session_id)
        self.state.persist()
        return {
            "status": "ok",
            "session": record.payload(),
            "workspace_root": self.state.workspace_root,
        }, HTTPStatus.OK

    def end_session(self, session_id: str) -> tuple[dict[str, Any], HTTPStatus]:
        record = self.state.session_records.pop(session_id, None)
        self.state.sessions = len(self.state.session_records)
        if record is None:
            return {"error": f"Unknown session_id: {session_id}"}, HTTPStatus.NOT_FOUND
        self.clear_session_presence(session_id)
        self.clear_session_leases(session_id)
        self.state.persist()
        return {
            "status": "ok",
            "ended": True,
            "session_id": session_id,
            "workspace_root": self.state.workspace_root,
        }, HTTPStatus.OK

    def upsert_notebook_presence(
        self,
        *,
        session_id: str,
        path: str,
        activity: str,
        cell_id: str | None = None,
        cell_index: int | None = None,
    ) -> tuple[dict[str, Any], HTTPStatus]:
        with self.state._lock:
            session = self.state.session_records.get(session_id)
        if session is None:
            return {"error": f"Unknown session_id: {session_id}"}, HTTPStatus.NOT_FOUND
        _real_path, relative_path = self.state._resolve_document_path(path)
        now = time.time()
        with self.state._lock:
            existing = self.state.notebook_presence.get(session_id)
            changed = (
                existing is None
                or existing.path != relative_path
                or existing.activity != activity
                or existing.cell_id != cell_id
                or existing.cell_index != cell_index
            )
            if existing is None:
                record = self.notebook_presence_record_type(
                    session_id=session_id,
                    path=relative_path,
                    activity=activity,
                    cell_id=cell_id,
                    cell_index=cell_index,
                    created_at=now,
                    updated_at=now,
                )
                self.state.notebook_presence[session_id] = record
            else:
                existing.path = relative_path
                existing.activity = activity
                existing.cell_id = cell_id
                existing.cell_index = cell_index
                existing.updated_at = now
                record = existing
        if changed:
            self.state._append_activity_event(
                path=relative_path,
                event_type="presence-updated",
                detail=f"{session.actor} {activity}",
                actor=session.actor,
                session_id=session_id,
                cell_id=cell_id,
                cell_index=cell_index,
            )
        self.state.persist()
        payload = record.payload()
        payload["session"] = session.payload()
        return {
            "status": "ok",
            "presence": payload,
            "workspace_root": self.state.workspace_root,
        }, HTTPStatus.OK

    def clear_notebook_presence(self, *, session_id: str, path: str | None = None) -> tuple[dict[str, Any], HTTPStatus]:
        with self.state._lock:
            session = self.state.session_records.get(session_id)
            existing = self.state.notebook_presence.get(session_id)
        if existing is None:
            return {"status": "ok", "cleared": False, "workspace_root": self.state.workspace_root}, HTTPStatus.OK
        if path is not None:
            _real_path, relative_path = self.state._resolve_document_path(path)
            if existing.path != relative_path:
                return {"status": "ok", "cleared": False, "workspace_root": self.state.workspace_root}, HTTPStatus.OK
        with self.state._lock:
            removed = self.state.notebook_presence.pop(session_id, None)
        if removed is None:
            return {"status": "ok", "cleared": False, "workspace_root": self.state.workspace_root}, HTTPStatus.OK
        self.state._append_activity_event(
            path=removed.path,
            event_type="presence-cleared",
            detail=f"{session.actor if session is not None else 'session'} left notebook",
            actor=session.actor if session is not None else None,
            session_id=session_id,
            cell_id=removed.cell_id,
            cell_index=removed.cell_index,
        )
        self.state.persist()
        return {"status": "ok", "cleared": True, "workspace_root": self.state.workspace_root}, HTTPStatus.OK

    def session_actor(self, session_id: str | None, fallback: str | None = None) -> str | None:
        if session_id is None:
            return fallback
        session = self.state.session_records.get(session_id)
        if session is None:
            return fallback
        return session.actor

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
        if branch_id in self.state.branch_records:
            return {"error": f"Duplicate branch_id: {branch_id}"}, HTTPStatus.BAD_REQUEST
        if document_id not in self.state.document_records:
            return {"error": f"Unknown document_id: {document_id}"}, HTTPStatus.BAD_REQUEST
        if owner_session_id is not None and owner_session_id not in self.state.session_records:
            return {"error": f"Unknown owner_session_id: {owner_session_id}"}, HTTPStatus.BAD_REQUEST
        if parent_branch_id is not None and parent_branch_id not in self.state.branch_records:
            return {"error": f"Unknown parent_branch_id: {parent_branch_id}"}, HTTPStatus.BAD_REQUEST
        now = time.time()
        record = self.branch_record_type(
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
        self.state.branch_records[branch_id] = record
        self.state.persist()
        return {
            "status": "ok",
            "branch": record.payload(),
            "workspace_root": self.state.workspace_root,
        }, HTTPStatus.OK

    def finish_branch(self, branch_id: str, status: str) -> tuple[dict[str, Any], HTTPStatus]:
        if status not in {"merged", "rejected", "abandoned"}:
            return {"error": f"Invalid branch status: {status}"}, HTTPStatus.BAD_REQUEST
        record = self.state.branch_records.get(branch_id)
        if record is None:
            return {"error": f"Unknown branch_id: {branch_id}"}, HTTPStatus.NOT_FOUND
        record.status = status
        record.updated_at = time.time()
        self.state.persist()
        return {
            "status": "ok",
            "branch": record.payload(),
            "workspace_root": self.state.workspace_root,
        }, HTTPStatus.OK

    def request_branch_review(
        self,
        *,
        branch_id: str,
        requested_by_session_id: str,
        note: str | None = None,
    ) -> tuple[dict[str, Any], HTTPStatus]:
        branch = self.state.branch_records.get(branch_id)
        if branch is None:
            return {"error": f"Unknown branch_id: {branch_id}"}, HTTPStatus.NOT_FOUND
        if branch.status != "active":
            return {"error": f"Branch is not reviewable in status '{branch.status}'"}, HTTPStatus.BAD_REQUEST
        session = self.state.session_records.get(requested_by_session_id)
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
        document = self.state.document_records.get(branch.document_id)
        if document is not None:
            self.state._append_activity_event(
                path=document.relative_path,
                event_type="review-requested",
                detail=f"{session.actor} requested review for branch {branch_id}",
                actor=session.actor,
                session_id=requested_by_session_id,
            )
        self.state.persist()
        return {
            "status": "ok",
            "branch": branch.payload(),
            "workspace_root": self.state.workspace_root,
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
        branch = self.state.branch_records.get(branch_id)
        if branch is None:
            return {"error": f"Unknown branch_id: {branch_id}"}, HTTPStatus.NOT_FOUND
        if branch.review_status != "requested":
            return {"error": f"Branch review is not pending for {branch_id}"}, HTTPStatus.BAD_REQUEST
        session = self.state.session_records.get(resolved_by_session_id)
        if session is None:
            return {"error": f"Unknown resolved_by_session_id: {resolved_by_session_id}"}, HTTPStatus.BAD_REQUEST
        now = time.time()
        branch.review_status = "resolved"
        branch.review_resolved_by_session_id = resolved_by_session_id
        branch.review_resolved_at = now
        branch.review_resolution = resolution
        branch.review_note = note or branch.review_note
        branch.updated_at = now
        document = self.state.document_records.get(branch.document_id)
        if document is not None:
            self.state._append_activity_event(
                path=document.relative_path,
                event_type="review-resolved",
                detail=f"{session.actor} resolved branch {branch_id} review as {resolution}",
                actor=session.actor,
                session_id=resolved_by_session_id,
            )
        self.state.persist()
        return {
            "status": "ok",
            "branch": branch.payload(),
            "workspace_root": self.state.workspace_root,
        }, HTTPStatus.OK
