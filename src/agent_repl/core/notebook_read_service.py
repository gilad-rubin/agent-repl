"""Read-side notebook service helpers for the core daemon."""
from __future__ import annotations

from http import HTTPStatus
from typing import Any


class NotebookReadService:
    """Serve read/projection notebook APIs on top of CoreState internals."""

    def __init__(self, state: Any):
        self.state = state

    def contents(self, path: str) -> tuple[dict[str, Any], HTTPStatus]:
        real_path, relative_path = self.state._resolve_document_path(path)
        # Readers should be able to observe the last committed notebook snapshot
        # even while a long-running execution still holds the per-notebook lock.
        payload = self.state._headless_notebook_contents(real_path, relative_path)
        self.state._sync_document_record(real_path, relative_path)
        return payload, HTTPStatus.OK

    def shared_model(self, path: str) -> tuple[dict[str, Any], HTTPStatus]:
        real_path, relative_path = self.state._resolve_document_path(path)
        payload = self.state._headless_notebook_shared_model(real_path, relative_path)
        self.state._sync_document_record(real_path, relative_path)
        return payload, HTTPStatus.OK

    def status(self, path: str) -> tuple[dict[str, Any], HTTPStatus]:
        real_path, relative_path = self.state._resolve_document_path(path)
        payload = self.state._headless_notebook_status(real_path, relative_path)
        self.state._sync_document_record(real_path, relative_path)
        return payload, HTTPStatus.OK

    def runtime(self, path: str) -> tuple[dict[str, Any], HTTPStatus]:
        self.state._reap_expired_runtimes()
        real_path, relative_path = self.state._resolve_document_path(path)
        runtime = self.state.headless_runtimes.get(real_path)
        reattach_policy = self.state._notebook_reattach_policy(real_path=real_path, relative_path=relative_path)
        runtime_record = self.state._runtime_record_for_notebook(relative_path)
        return {
            "status": "ok",
            "path": relative_path,
            "active": runtime is not None,
            "mode": "headless" if (runtime is not None or reattach_policy.get("action") != "none") else None,
            "runtime": runtime.payload() if runtime is not None else None,
            "runtime_record": runtime_record.payload() if runtime_record is not None else None,
            "reattach_policy": reattach_policy,
        }, HTTPStatus.OK

    def projection(self, path: str) -> tuple[dict[str, Any], HTTPStatus]:
        self.state._reap_expired_runtimes()
        real_path, relative_path = self.state._resolve_document_path(path)
        runtime = self.state.headless_runtimes.get(real_path)
        runtime_record = self.state._runtime_record_for_notebook(relative_path)
        return {
            "status": "ok",
            "path": relative_path,
            "active": runtime is not None,
            "mode": "headless" if runtime is not None else None,
            "runtime": runtime.payload() if runtime is not None else None,
            "runtime_record": runtime_record.payload() if runtime_record is not None else None,
            "contents": self.state._headless_notebook_contents(real_path, relative_path) if runtime is not None else None,
        }, HTTPStatus.OK

    def activity(self, path: str, *, since: float | None = None) -> tuple[dict[str, Any], HTTPStatus]:
        self.state._refresh_session_liveness()
        real_path, relative_path = self.state._resolve_document_path(path)
        runtime = self.state.headless_runtimes.get(real_path)
        runtime_record = self.state._runtime_record_for_notebook(relative_path)
        running: list[dict[str, Any]] = []
        queued: list[dict[str, Any]] = []
        if runtime_record is not None:
            running, queued = self.state._execution_ledger_service.notebook_status(
                runtime=runtime,
                runtime_record=runtime_record,
                path=relative_path,
            )
        with self.state._lock:
            events = [
                record.payload()
                for record in self.state.activity_records
                if record.path == relative_path and (since is None or record.timestamp > since)
            ]
        cursor = max((event["timestamp"] for event in events), default=since or 0.0)
        return {
            "status": "ok",
            "path": relative_path,
            "presence": self.state._presence_payload_for_path(relative_path),
            "leases": self.state._leases_payload_for_path(relative_path),
            "recent_events": events,
            "cursor": cursor,
            "runtime": runtime.payload() if runtime is not None else None,
            "runtime_record": runtime_record.payload() if runtime_record is not None else None,
            "current_execution": runtime.current_execution if runtime is not None else None,
            "running": running,
            "queued": queued,
        }, HTTPStatus.OK
