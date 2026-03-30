"""Execution-ledger helpers for runtime and run truth."""
from __future__ import annotations

import os
import time
from http import HTTPStatus
from typing import Any


class ExecutionLedgerService:
    """Own runtime-bound run records and queue promotion rules."""

    ACTIVE_RUN_STATUSES = {"queued", "running"}
    MAX_NOTEBOOK_EXECUTION_RECORDS = 200

    def __init__(self, state: Any, *, run_record_type: type[Any]):
        self.state = state
        self.run_record_type = run_record_type

    def recompute_counts(self) -> None:
        self.state.documents = len(self.state.document_records)
        self.state.sessions = len(self.state.session_records)
        self.state.runs = sum(1 for record in self.state.run_records.values() if record.status in self.ACTIVE_RUN_STATUSES)

    def list_runs_payload(self) -> dict[str, Any]:
        self.recompute_counts()
        return {
            "status": "ok",
            "runs": [record.payload() for record in self.state.run_records.values()],
            "count": len(self.state.run_records),
            "active_count": self.state.runs,
            "workspace_root": self.state.workspace_root,
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
        self.state._reap_expired_runtimes()
        runtime = self.state.runtime_records.get(runtime_id)
        if runtime is None:
            return {"error": f"Unknown runtime_id: {runtime_id}"}, HTTPStatus.BAD_REQUEST
        if runtime.status in {"stopped", "reaped", "failed"}:
            return {"error": f"Runtime is not runnable: {runtime_id}"}, HTTPStatus.BAD_REQUEST
        if runtime.status == "recovery-needed":
            return {"error": f"Runtime requires recovery: {runtime_id}"}, HTTPStatus.BAD_REQUEST
        if runtime.status == "degraded":
            return {"error": f"Runtime is degraded and must be recovered before new runs: {runtime_id}"}, HTTPStatus.BAD_REQUEST
        if target_type == "document" and target_ref not in self.state.document_records:
            return {"error": f"Unknown document target_ref: {target_ref}"}, HTTPStatus.BAD_REQUEST
        if target_type == "branch" and target_ref not in self.state.branch_records:
            return {"error": f"Unknown branch target_ref: {target_ref}"}, HTTPStatus.BAD_REQUEST

        now = time.time()
        has_active_run = runtime.status == "busy" or any(
            item.runtime_id == runtime_id and item.status in self.ACTIVE_RUN_STATUSES
            for item in self.state.run_records.values()
        ) or self._runtime_has_live_execution(runtime)
        record = self.run_record_type(
            run_id=run_id,
            runtime_id=runtime_id,
            target_type=target_type,
            target_ref=target_ref,
            kind=kind,
            status="queued" if has_active_run else "running",
            queue_position=0 if has_active_run else None,
            created_at=now,
            updated_at=now,
        )
        self.state.run_records[run_id] = record
        self._normalize_queue_positions(runtime_id)
        self.state._transition_runtime_record(runtime, "busy", health="healthy", reason=f"run-start:{run_id}")
        self.recompute_counts()
        self.state.persist()
        return {
            "status": "ok",
            "run": record.payload(),
            "workspace_root": self.state.workspace_root,
        }, HTTPStatus.OK

    def finish_run(self, run_id: str, status: str) -> tuple[dict[str, Any], HTTPStatus]:
        record = self.state.run_records.get(run_id)
        if record is None:
            return {"error": f"Unknown run_id: {run_id}"}, HTTPStatus.NOT_FOUND
        if status not in {"completed", "failed", "interrupted"}:
            return {"error": f"Invalid run status: {status}"}, HTTPStatus.BAD_REQUEST

        now = time.time()
        record.status = status
        record.queue_position = None
        record.updated_at = now
        runtime = self.state.runtime_records.get(record.runtime_id)
        if runtime is not None:
            promoted = self._promote_next_queued_run(runtime)
            if promoted is not None:
                self._emit_queue_promotion_event(promoted)
            active_runtime_runs = sum(
                1
                for item in self.state.run_records.values()
                if item.runtime_id == record.runtime_id and item.status in self.ACTIVE_RUN_STATUSES
            )
            target_status = "busy" if (active_runtime_runs or self._runtime_has_live_execution(runtime)) else "idle"
            target_health = "degraded" if status == "failed" else runtime.health
            self.state._transition_runtime_record(runtime, target_status, health=target_health, reason=f"run-finish:{run_id}")
        self._normalize_queue_positions(record.runtime_id)
        self.recompute_counts()
        self.state.persist()
        return {
            "status": "ok",
            "run": record.payload(),
            "workspace_root": self.state.workspace_root,
        }, HTTPStatus.OK

    def notebook_status(self, *, runtime: Any, runtime_record: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        running = [record.payload() for record in self._runs_for_runtime(runtime_record.runtime_id, status="running")]
        if not running and runtime and runtime.current_execution:
            running.append(dict(runtime.current_execution))
        queued = [record.payload() for record in self._runs_for_runtime(runtime_record.runtime_id, status="queued")]
        return running, queued

    def start_notebook_execution(
        self,
        *,
        execution_id: str,
        path: str,
        runtime_id: str,
        cell_id: str,
        cell_index: int,
        source_preview: str,
        owner: str,
        session_id: str | None,
        operation: str,
    ) -> dict[str, Any]:
        record = {
            "execution_id": execution_id,
            "status": "running",
            "path": path,
            "runtime_id": runtime_id,
            "cell_id": cell_id,
            "cell_index": cell_index,
            "source_preview": source_preview,
            "owner": owner,
            "session_id": session_id,
            "operation": operation,
            "created_at": time.time(),
            "updated_at": time.time(),
        }
        self.state.execution_records[execution_id] = record
        self._trim_notebook_execution_records()
        return dict(record)

    def finish_notebook_execution(
        self,
        execution_id: str,
        *,
        status: str,
        outputs: list[Any],
        execution_count: int | None,
        error: str | None,
    ) -> dict[str, Any] | None:
        record = self.state.execution_records.get(execution_id)
        if record is None:
            return None
        record["status"] = status
        record["outputs"] = self.state._canonical_outputs(outputs)
        record["execution_count"] = execution_count
        record["updated_at"] = time.time()
        if error:
            record["error"] = error
        else:
            record.pop("error", None)
        return dict(record)

    def notebook_execution(self, execution_id: str) -> dict[str, Any] | None:
        record = self.state.execution_records.get(execution_id)
        if record is None:
            return None
        return dict(record)

    def _runs_for_runtime(self, runtime_id: str, *, status: str | None = None) -> list[Any]:
        records = [
            record
            for record in self.state.run_records.values()
            if record.runtime_id == runtime_id and (status is None or record.status == status)
        ]
        records.sort(key=lambda record: (record.queue_position is None, record.queue_position or 0, record.created_at, record.run_id))
        return records

    def _normalize_queue_positions(self, runtime_id: str) -> None:
        queued_runs = [
            record
            for record in self.state.run_records.values()
            if record.runtime_id == runtime_id and record.status == "queued"
        ]
        queued_runs.sort(key=lambda record: (record.created_at, record.run_id))
        for index, record in enumerate(queued_runs, start=1):
            record.queue_position = index

    def _promote_next_queued_run(self, runtime: Any) -> Any | None:
        runtime_id = runtime.runtime_id
        has_running = any(record.status == "running" for record in self._runs_for_runtime(runtime_id))
        if has_running or self._runtime_has_live_execution(runtime):
            return None
        queued_runs = self._runs_for_runtime(runtime_id, status="queued")
        if not queued_runs:
            return None
        next_run = queued_runs[0]
        next_run.status = "running"
        next_run.queue_position = None
        next_run.updated_at = time.time()
        return next_run

    def _emit_queue_promotion_event(self, promoted_run: Any) -> None:
        path = promoted_run.target_ref if promoted_run.target_type == "document" else None
        if path and hasattr(self.state, "_append_activity_event"):
            self.state._append_activity_event(
                path=path,
                event_type="queue-promotion",
                detail=f"Promoted run {promoted_run.run_id} from queued to running",
                runtime_id=promoted_run.runtime_id,
            )

    def _runtime_has_live_execution(self, runtime: Any) -> bool:
        document_path = getattr(runtime, "document_path", None)
        if not document_path:
            return False
        real_path = os.path.realpath(
            os.path.join(self.state.workspace_root, document_path)
            if not os.path.isabs(document_path)
            else document_path
        )
        live_runtime = self.state.headless_runtimes.get(real_path)
        return bool(
            live_runtime is not None
            and live_runtime.runtime_id == runtime.runtime_id
            and (live_runtime.busy or live_runtime.current_execution)
        )

    def _trim_notebook_execution_records(self) -> None:
        overflow = len(self.state.execution_records) - self.MAX_NOTEBOOK_EXECUTION_RECORDS
        if overflow <= 0:
            return
        ordered = sorted(
            self.state.execution_records.items(),
            key=lambda item: (item[1].get("status") == "running", item[1].get("updated_at", 0.0), item[0]),
        )
        for execution_id, _record in ordered[:overflow]:
            self.state.execution_records.pop(execution_id, None)
