"""Checkpoint service for snapshotting and restoring notebook state."""
from __future__ import annotations

import json
import time
import uuid
from http import HTTPStatus
from typing import Any

import nbformat


class CheckpointService:
    """Create, list, restore, and delete notebook checkpoints."""

    def __init__(self, state: Any) -> None:
        self.state = state

    def create_checkpoint(
        self,
        path: str,
        label: str | None = None,
        session_id: str | None = None,
    ) -> tuple[dict[str, Any], HTTPStatus]:
        from agent_repl.core.server import CheckpointRecord

        real_path, relative_path = self.state._resolve_document_path(path)

        with self.state._notebook_lock(real_path):
            notebook, changed = self.state._load_notebook(real_path)
            if changed:
                self.state._save_notebook(real_path, notebook)

        snapshot_nbformat = nbformat.writes(notebook)
        snapshot_ydoc = self.state._ydoc_service.get_update(relative_path)

        now = time.time()
        checkpoint_id = str(uuid.uuid4())
        record = CheckpointRecord(
            checkpoint_id=checkpoint_id,
            path=relative_path,
            label=label,
            snapshot_nbformat=snapshot_nbformat,
            snapshot_ydoc=snapshot_ydoc,
            metadata=None,
            created_by_session_id=session_id,
            created_at=now,
        )

        with self.state._lock:
            self.state.checkpoint_records[checkpoint_id] = record

        actor = self.state._collaboration_service.session_actor(session_id, fallback="system")
        self.state._append_activity_event(
            path=relative_path,
            event_type="checkpoint-created",
            detail=f"{actor} created checkpoint{f' ({label})' if label else ''}",
            actor=actor,
            session_id=session_id,
            data={"checkpoint_id": checkpoint_id, "label": label},
        )
        self.state.persist()

        return {
            "status": "ok",
            "checkpoint": record.payload(),
            "workspace_root": self.state.workspace_root,
        }, HTTPStatus.OK

    def restore_checkpoint(self, checkpoint_id: str) -> tuple[dict[str, Any], HTTPStatus]:
        with self.state._lock:
            record = self.state.checkpoint_records.get(checkpoint_id)
        if record is None:
            return {"error": f"Unknown checkpoint_id: {checkpoint_id}"}, HTTPStatus.NOT_FOUND

        real_path, relative_path = self.state._resolve_document_path(record.path)

        # Refuse if active/queued executions exist for this path
        if self._has_active_executions(relative_path):
            return {
                "error": "Cannot restore checkpoint while executions are active or queued. Drain executions first.",
                "checkpoint_id": checkpoint_id,
                "path": relative_path,
            }, HTTPStatus.CONFLICT

        # Validate snapshot before touching live state
        try:
            validated_nb = nbformat.reads(record.snapshot_nbformat, as_version=4)
        except Exception as exc:
            return {
                "error": f"Checkpoint snapshot is invalid: {exc}",
                "checkpoint_id": checkpoint_id,
            }, HTTPStatus.UNPROCESSABLE_ENTITY

        with self.state._notebook_lock(real_path):
            # Write validated snapshot to disk
            self.state._save_notebook(real_path, validated_nb)
            # Close the live YDoc so it will be recreated from the file
            self.state._ydoc_service.close(relative_path)
            # Reload into YDoc from the freshly-written file
            self.state._sync_notebook_to_ydoc(relative_path, validated_nb)

        self.state._append_activity_event(
            path=relative_path,
            event_type="checkpoint-restored",
            detail=f"Restored checkpoint {checkpoint_id}{f' ({record.label})' if record.label else ''}",
            data={"checkpoint_id": checkpoint_id, "label": record.label},
        )
        self.state.persist()

        return {
            "status": "ok",
            "restored": True,
            "checkpoint": record.payload(),
            "workspace_root": self.state.workspace_root,
        }, HTTPStatus.OK

    def list_checkpoints(self, path: str) -> tuple[dict[str, Any], HTTPStatus]:
        _real_path, relative_path = self.state._resolve_document_path(path)

        with self.state._lock:
            matching = [
                r.payload()
                for r in self.state.checkpoint_records.values()
                if r.path == relative_path
            ]
        matching.sort(key=lambda c: c["created_at"], reverse=True)

        return {
            "status": "ok",
            "checkpoints": matching,
            "count": len(matching),
            "path": relative_path,
            "workspace_root": self.state.workspace_root,
        }, HTTPStatus.OK

    def delete_checkpoint(self, checkpoint_id: str) -> tuple[dict[str, Any], HTTPStatus]:
        with self.state._lock:
            record = self.state.checkpoint_records.pop(checkpoint_id, None)
        if record is None:
            return {"error": f"Unknown checkpoint_id: {checkpoint_id}"}, HTTPStatus.NOT_FOUND
        self.state.persist()
        return {
            "status": "ok",
            "deleted": True,
            "checkpoint_id": checkpoint_id,
            "workspace_root": self.state.workspace_root,
        }, HTTPStatus.OK

    def _has_active_executions(self, relative_path: str) -> bool:
        """Check whether there are active (running/queued) executions for a path."""
        active_statuses = {"running", "queued"}
        for record in self.state.execution_records.values():
            if record.get("path") == relative_path and record.get("status") in active_statuses:
                return True
        for run in self.state.run_records.values():
            if run.status in active_statuses:
                if run.target_type == "document" and run.target_ref == relative_path:
                    return True
        return False
