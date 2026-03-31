"""Write-side notebook service helpers for the core daemon."""
from __future__ import annotations

import os
import time
from collections.abc import Callable
from http import HTTPStatus
from pathlib import Path
from typing import Any

from agent_repl.core.collaboration import CollaborationConflictError


class NotebookWriteService:
    """Serve mutation/execution notebook APIs on top of CoreState internals."""

    def __init__(self, state: Any):
        self.state = state

    def create(
        self,
        path: str,
        *,
        cells: list[dict[str, Any]] | None,
        kernel_id: str | None,
    ) -> tuple[dict[str, Any], HTTPStatus]:
        real_path, relative_path = self.state._resolve_document_path(path)
        with self.state._notebook_lock(real_path):
            payload = self.state._headless_notebook_create(real_path, relative_path, cells=cells, kernel_id=kernel_id)
        self.state._sync_document_record(real_path, relative_path)
        return payload, HTTPStatus.OK

    def edit(
        self,
        path: str,
        operations: list[dict[str, Any]],
        *,
        owner_session_id: str | None = None,
    ) -> tuple[dict[str, Any], HTTPStatus]:
        return self._run_locked_mutation(
            path,
            lambda real_path, relative_path: self.state._headless_notebook_edit(
                real_path,
                relative_path,
                operations,
                owner_session_id=owner_session_id,
            ),
        )

    def execute_cell(
        self,
        path: str,
        *,
        cell_id: str | None,
        cell_index: int | None,
        owner_session_id: str | None = None,
    ) -> tuple[dict[str, Any], HTTPStatus]:
        real_path, relative_path = self.state._resolve_document_path(path)
        try:
            payload = self.state._headless_notebook_execute_cell(
                real_path,
                relative_path,
                cell_id=cell_id,
                cell_index=cell_index,
                owner_session_id=owner_session_id,
            )
        except CollaborationConflictError as err:
            return err.payload, HTTPStatus.CONFLICT
        self.state._sync_document_record(real_path, relative_path)
        return payload, HTTPStatus.OK

    def enqueue_execute_cell(
        self,
        path: str,
        *,
        cell_id: str | None,
        cell_index: int | None,
        owner_session_id: str | None = None,
    ) -> tuple[dict[str, Any], HTTPStatus]:
        real_path, relative_path = self.state._resolve_document_path(path)
        try:
            payload = self.state._headless_notebook_enqueue_execute_cell(
                real_path,
                relative_path,
                cell_id=cell_id,
                cell_index=cell_index,
                owner_session_id=owner_session_id,
            )
        except CollaborationConflictError as err:
            return err.payload, HTTPStatus.CONFLICT
        self.state._sync_document_record(real_path, relative_path)
        return payload, HTTPStatus.OK

    def insert_execute(
        self,
        path: str,
        *,
        source: str,
        cell_type: str,
        at_index: int,
        owner_session_id: str | None = None,
    ) -> tuple[dict[str, Any], HTTPStatus]:
        return self._run_locked_mutation(
            path,
            lambda real_path, relative_path: self.state._headless_notebook_insert_execute(
                real_path,
                relative_path,
                source=source,
                cell_type=cell_type,
                at_index=at_index,
                owner_session_id=owner_session_id,
            ),
        )

    def execute_all(
        self,
        path: str,
        *,
        owner_session_id: str | None = None,
    ) -> tuple[dict[str, Any], HTTPStatus]:
        return self._run_locked_mutation(
            path,
            lambda real_path, relative_path: self.state._headless_notebook_execute_all(
                real_path,
                relative_path,
                owner_session_id=owner_session_id,
            ),
        )

    def interrupt(self, path: str) -> tuple[dict[str, Any], HTTPStatus]:
        real_path, relative_path = self.state._resolve_document_path(path)
        runtime = self.state.headless_runtimes.get(real_path)
        if runtime is None:
            return {"status": "ok", "interrupted": False, "reason": "no-runtime"}, HTTPStatus.OK
        current_execution = runtime.current_execution
        if not current_execution:
            return {"status": "ok", "interrupted": False, "reason": "idle"}, HTTPStatus.OK
        try:
            runtime.manager.interrupt_kernel()
        except Exception as err:
            return {"error": f"Failed to interrupt execution: {err}"}, HTTPStatus.INTERNAL_SERVER_ERROR
        self.state._append_activity_event(
            path=relative_path,
            event_type="execution-interrupt-requested",
            detail="Interrupt requested for current execution",
            actor="agent",
            session_id=None,
            runtime_id=runtime.runtime_id,
            cell_id=current_execution.get("cell_id"),
            cell_index=current_execution.get("cell_index"),
        )
        self.state.persist()
        return {
            "status": "ok",
            "interrupted": True,
            "current_execution": current_execution,
        }, HTTPStatus.OK

    def select_kernel(
        self,
        path: str,
        *,
        kernel_id: str | None,
    ) -> tuple[dict[str, Any], HTTPStatus]:
        real_path, relative_path = self.state._resolve_document_path(path)
        python_path = self.state._resolve_python_path(kernel_id)
        venv_path = os.path.join(self.state.workspace_root, ".venv", "bin", "python")
        source_hint = venv_path if not kernel_id and os.path.exists(venv_path) else None
        self.state._ensure_kernel_capable_python(python_path, source_hint=source_hint)
        existing = self.state.headless_runtimes.get(real_path)
        if existing is not None and existing.python_path == python_path:
            existing.last_used_at = time.time()
        else:
            if existing is not None:
                self.state._shutdown_headless_runtime(real_path)
            self.state._ensure_headless_runtime(real_path, kernel_id=python_path)
        runtime = self.state.headless_runtimes.get(real_path)
        if runtime is not None:
            self.state._sync_headless_runtime_record(relative_path=relative_path, runtime=runtime)
            self.state.persist()
        return {
            "status": "ok",
            "path": relative_path,
            "kernel": {
                "id": python_path,
                "label": Path(python_path).name,
                "python": python_path,
                "type": "headless",
            },
            "message": f"Selected kernel: {python_path}",
            "mode": "headless",
        }, HTTPStatus.OK

    def trust(self, path: str) -> tuple[dict[str, Any], HTTPStatus]:
        return self._run_locked_mutation(
            path,
            lambda real_path, relative_path: self.state._headless_notebook_trust(real_path, relative_path),
        )

    def project_visible(
        self,
        path: str,
        *,
        cells: list[dict[str, Any]],
        owner_session_id: str | None = None,
    ) -> tuple[dict[str, Any], HTTPStatus]:
        return self._run_locked_mutation(
            path,
            lambda real_path, relative_path: self.state._headless_notebook_project_visible(
                real_path,
                relative_path,
                cells=cells,
                owner_session_id=owner_session_id,
            ),
            ensure_runtime=True,
            sync_runtime_record=True,
            persist=True,
        )

    def execute_visible_cell(
        self,
        path: str,
        *,
        cell_index: int,
        source: str,
        owner_session_id: str | None = None,
    ) -> tuple[dict[str, Any], HTTPStatus]:
        return self._run_locked_mutation(
            path,
            lambda real_path, relative_path: self.state._headless_notebook_execute_visible_cell(
                real_path,
                relative_path,
                cell_index=cell_index,
                source=source,
                owner_session_id=owner_session_id,
            ),
            ensure_runtime=True,
            sync_runtime_record=True,
            persist=True,
        )

    def restart(self, path: str) -> tuple[dict[str, Any], HTTPStatus]:
        real_path, relative_path = self.state._resolve_document_path(path)
        payload = self.state._headless_notebook_restart(real_path, relative_path)
        self.state._sync_document_record(real_path, relative_path)
        return payload, HTTPStatus.OK

    def restart_and_run_all(
        self,
        path: str,
        *,
        owner_session_id: str | None = None,
    ) -> tuple[dict[str, Any], HTTPStatus]:
        return self._run_locked_mutation(
            path,
            lambda real_path, relative_path: self.state._headless_notebook_restart_and_run_all(
                real_path,
                relative_path,
                owner_session_id=owner_session_id,
            ),
        )

    def _run_locked_mutation(
        self,
        path: str,
        mutation: Callable[[str, str], dict[str, Any]],
        *,
        ensure_runtime: bool = False,
        sync_runtime_record: bool = False,
        persist: bool = False,
    ) -> tuple[dict[str, Any], HTTPStatus]:
        real_path, relative_path = self.state._resolve_document_path(path)
        runtime = self.state.headless_runtimes.get(real_path)
        if ensure_runtime and runtime is None:
            runtime = self.state._ensure_headless_runtime(real_path)
        try:
            with self.state._notebook_lock(real_path):
                payload = mutation(real_path, relative_path)
        except CollaborationConflictError as err:
            return err.payload, HTTPStatus.CONFLICT
        self.state._sync_document_record(real_path, relative_path)
        if sync_runtime_record and runtime is not None:
            self.state._sync_headless_runtime_record(relative_path=relative_path, runtime=runtime)
        if persist:
            self.state.persist()
        return payload, HTTPStatus.OK
