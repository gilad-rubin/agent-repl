"""Execution-focused notebook helpers for the core daemon."""
from __future__ import annotations

import json
import time
import uuid
from typing import Any

import nbformat


class NotebookExecutionService:
    """Own the private execution and restart flows for headless notebooks."""

    def __init__(self, state: Any):
        self.state = state

    def execute_source(
        self,
        runtime: Any,
        source: str,
        *,
        cell_id: str,
        cell_index: int,
        owner_session_id: str | None = None,
        execution_id: str | None = None,
        operation: str = "execute-cell",
    ) -> tuple[list[Any], int | None, str | None]:
        with runtime.lock:
            relative_path = self._relative_path(runtime.path)
            actor = self.state._session_actor(owner_session_id, "agent")
            execution_id = execution_id or str(uuid.uuid4())
            current_execution = self.state._execution_ledger_service.start_notebook_execution(
                execution_id=execution_id,
                path=relative_path,
                runtime_id=runtime.runtime_id,
                cell_id=cell_id,
                cell_index=cell_index,
                source_preview=source.splitlines()[0][:80] if source else "",
                owner=actor,
                session_id=owner_session_id,
                operation=operation,
            )
            runtime.busy = True
            runtime.current_execution = current_execution
            runtime.last_used_at = time.time()
            self.state._sync_headless_runtime_record(relative_path=relative_path, runtime=runtime, status="busy")
            self.state._append_activity_event(
                path=relative_path,
                event_type="execution-started",
                detail=f"Executing cell {cell_index + 1}",
                actor=actor,
                session_id=owner_session_id,
                runtime_id=runtime.runtime_id,
                cell_id=cell_id,
                cell_index=cell_index,
            )
            self.state.persist()
            try:
                msg_id = runtime.client.execute(source, store_history=True, allow_stdin=False, stop_on_error=True)
                outputs: list[Any] = []
                execution_count: int | None = None
                error_text: str | None = None
                idle_seen = False
                while not idle_seen:
                    message = runtime.client.get_iopub_msg(timeout=60)
                    if message.get("parent_header", {}).get("msg_id") != msg_id:
                        continue
                    msg_type = message.get("msg_type") or message.get("header", {}).get("msg_type")
                    content = message.get("content", {})
                    if msg_type == "status" and content.get("execution_state") == "idle":
                        idle_seen = True
                        continue
                    if msg_type == "execute_input":
                        execution_count = content.get("execution_count")
                        self.state._append_activity_event(
                            path=relative_path,
                            event_type="cell-execution-updated",
                            detail=f"Execution count advanced for cell {cell_index + 1}",
                            actor=actor,
                            session_id=owner_session_id,
                            runtime_id=runtime.runtime_id,
                            cell_id=cell_id,
                            cell_index=cell_index,
                            data={"execution_count": execution_count},
                        )
                        continue
                    if msg_type == "stream":
                        output_payload = nbformat.v4.new_output(
                            output_type="stream",
                            name=content.get("name", "stdout"),
                            text=content.get("text", ""),
                        )
                        outputs.append(output_payload)
                        self._append_output_event(
                            relative_path=relative_path,
                            detail=f"Stream output for cell {cell_index + 1}",
                            output_payload=output_payload,
                            outputs=outputs,
                            execution_count=execution_count,
                            source=source,
                            runtime=runtime,
                            actor=actor,
                            owner_session_id=owner_session_id,
                            cell_id=cell_id,
                            cell_index=cell_index,
                        )
                        continue
                    if msg_type == "execute_result":
                        output_payload = nbformat.v4.new_output(
                            output_type="execute_result",
                            data=content.get("data", {}),
                            metadata=content.get("metadata", {}),
                            execution_count=content.get("execution_count"),
                        )
                        outputs.append(output_payload)
                        execution_count = content.get("execution_count", execution_count)
                        self._append_output_event(
                            relative_path=relative_path,
                            detail=f"Execution result for cell {cell_index + 1}",
                            output_payload=output_payload,
                            outputs=outputs,
                            execution_count=execution_count,
                            source=source,
                            runtime=runtime,
                            actor=actor,
                            owner_session_id=owner_session_id,
                            cell_id=cell_id,
                            cell_index=cell_index,
                        )
                        continue
                    if msg_type == "display_data":
                        output_payload = nbformat.v4.new_output(
                            output_type="display_data",
                            data=content.get("data", {}),
                            metadata=content.get("metadata", {}),
                        )
                        outputs.append(output_payload)
                        self._append_output_event(
                            relative_path=relative_path,
                            detail=f"Display output for cell {cell_index + 1}",
                            output_payload=output_payload,
                            outputs=outputs,
                            execution_count=execution_count,
                            source=source,
                            runtime=runtime,
                            actor=actor,
                            owner_session_id=owner_session_id,
                            cell_id=cell_id,
                            cell_index=cell_index,
                        )
                        continue
                    if msg_type == "error":
                        output_payload = nbformat.v4.new_output(
                            output_type="error",
                            ename=content.get("ename"),
                            evalue=content.get("evalue"),
                            traceback=content.get("traceback", []),
                        )
                        outputs.append(output_payload)
                        error_text = content.get("evalue") or content.get("ename") or "Execution failed"
                        self._append_output_event(
                            relative_path=relative_path,
                            detail=f"Error output for cell {cell_index + 1}",
                            output_payload=output_payload,
                            outputs=outputs,
                            execution_count=execution_count,
                            source=source,
                            runtime=runtime,
                            actor=actor,
                            owner_session_id=owner_session_id,
                            cell_id=cell_id,
                            cell_index=cell_index,
                        )
                runtime.client.get_shell_msg(timeout=60)
                self.state._execution_ledger_service.finish_notebook_execution(
                    execution_id,
                    status="error" if error_text else "ok",
                    outputs=outputs,
                    execution_count=execution_count,
                    error=error_text,
                )
                return outputs, execution_count, error_text
            except Exception as err:
                self.state._execution_ledger_service.finish_notebook_execution(
                    execution_id,
                    status="error",
                    outputs=[],
                    execution_count=None,
                    error=str(err),
                )
                raise
            finally:
                runtime.busy = False
                runtime.current_execution = None
                runtime.last_used_at = time.time()
                self.state._sync_headless_runtime_record(relative_path=relative_path, runtime=runtime, status="idle")
                self.state.persist()

    def execute_cell(
        self,
        real_path: str,
        relative_path: str,
        *,
        cell_id: str | None,
        cell_index: int | None,
        owner_session_id: str | None = None,
    ) -> dict[str, Any]:
        notebook, _changed = self.state._load_notebook(real_path)
        index = self.state._find_cell_index(notebook, cell_id=cell_id, cell_index=cell_index)
        cell = notebook.cells[index]
        if cell.cell_type != "code":
            raise RuntimeError("Only code cells can be executed")
        runtime = self.state._ensure_headless_runtime(real_path)
        stable_cell_id = self.state._cell_id(cell, index)
        execution_id = str(uuid.uuid4())
        self.state._assert_cell_not_leased(
            relative_path=relative_path,
            cell_id=stable_cell_id,
            owner_session_id=owner_session_id,
            operation="execute-cell",
        )
        if owner_session_id is not None:
            self.state.acquire_cell_lease(
                session_id=owner_session_id,
                path=relative_path,
                cell_id=stable_cell_id,
                kind="edit",
            )
        outputs, execution_count, error_text = self.state._execute_source(
            runtime,
            cell.source,
            cell_id=stable_cell_id,
            cell_index=index,
            owner_session_id=owner_session_id,
            execution_id=execution_id,
            operation="execute-cell",
        )
        actor = self.state._session_actor(owner_session_id, "agent")
        final_index = index
        try:
            latest_notebook, _ = self.state._load_notebook(real_path)
            try:
                final_index = self.state._find_cell_index(latest_notebook, cell_id=stable_cell_id)
            except RuntimeError:
                final_index = -1
            if final_index >= 0:
                latest_cell = latest_notebook.cells[final_index]
                latest_cell.outputs = outputs
                latest_cell.execution_count = execution_count
                self.state._set_cell_runtime_provenance(
                    latest_cell,
                    runtime_id=runtime.runtime_id,
                    kernel_generation=runtime.kernel_generation,
                    status="error" if error_text else "ok",
                )
                self.state._save_notebook(real_path, latest_notebook)
                self.state._append_activity_event(
                    path=relative_path,
                    event_type="cell-outputs-updated",
                    detail=f"Updated outputs for cell {final_index + 1}",
                    actor=actor,
                    session_id=owner_session_id,
                    runtime_id=runtime.runtime_id,
                    cell_id=stable_cell_id,
                    cell_index=final_index,
                    data={"cell": self.state._cell_payload(latest_cell, final_index)},
                )
        finally:
            self.state._append_activity_event(
                path=relative_path,
                event_type="execution-finished",
                detail=f"Finished cell {(final_index if final_index >= 0 else index) + 1}",
                actor=actor,
                session_id=owner_session_id,
                runtime_id=runtime.runtime_id,
                cell_id=stable_cell_id,
                cell_index=final_index if final_index >= 0 else index,
            )
            self.state.persist()
        return {
            "status": "error" if error_text else "ok",
            "path": relative_path,
            "execution_id": execution_id,
            "cell_id": stable_cell_id,
            "cell_index": final_index if final_index >= 0 else index,
            "outputs": outputs,
            "execution_count": execution_count,
            "operation": "execute-cell",
            "execution_mode": "headless-runtime",
            "execution_preference": "headless",
            **({"error": error_text} if error_text else {}),
        }

    def insert_execute(
        self,
        real_path: str,
        relative_path: str,
        *,
        source: str,
        cell_type: str,
        at_index: int,
        owner_session_id: str | None = None,
    ) -> dict[str, Any]:
        self.state._assert_structure_not_leased(
            relative_path=relative_path,
            owner_session_id=owner_session_id,
            operation="insert-execute",
        )
        if cell_type == "code":
            self.state._ensure_headless_runtime(real_path)

        notebook, _ = self.state._load_notebook(real_path)
        index = self.state._normalize_insert_index(notebook, at_index)
        cell = nbformat.v4.new_code_cell(source=source) if cell_type == "code" else nbformat.v4.new_markdown_cell(source=source)
        notebook.cells.insert(index, cell)
        for position, current in enumerate(notebook.cells):
            self.state._ensure_cell_identity(current, position)
        self.state._save_notebook(real_path, notebook)
        self.state._append_activity_event(
            path=relative_path,
            event_type="cell-inserted",
            detail=f"Inserted {cell_type} cell at index {index}",
            actor=self.state._session_actor(owner_session_id, "agent"),
            session_id=owner_session_id,
            runtime_id=self._selected_runtime_id(relative_path),
            cell_id=self.state._cell_id(cell, index),
            cell_index=index,
            data={"cell": self.state._cell_payload(cell, index)},
        )
        if cell_type != "code":
            return {
                "status": "ok",
                "path": relative_path,
                "cell_id": self.state._cell_id(cell, index),
                "cell_index": index,
                "operation": "insert-execute",
                "outputs": [],
                "execution_mode": "headless-runtime",
                "execution_preference": "headless",
            }
        inserted_cell_id = self.state._cell_id(cell, index)
        if owner_session_id is not None:
            self.state.acquire_cell_lease(
                session_id=owner_session_id,
                path=relative_path,
                cell_id=inserted_cell_id,
                kind="edit",
            )
        try:
            result = self.execute_cell(
                real_path,
                relative_path,
                cell_id=inserted_cell_id,
                cell_index=None,
                owner_session_id=owner_session_id,
            )
        except Exception as exc:
            self.state._rollback_inserted_cell(real_path, inserted_cell_id)
            raise RuntimeError(
                f"ix failed and the inserted cell was rolled back (notebook unchanged). "
                f"Cause: {exc}"
            ) from exc
        return {**result, "operation": "insert-execute"}

    def execute_all(
        self,
        real_path: str,
        relative_path: str,
        *,
        owner_session_id: str | None = None,
    ) -> dict[str, Any]:
        notebook, _ = self.state._load_notebook(real_path)
        identity_changed = False
        for index, cell in enumerate(notebook.cells):
            identity_changed = self.state._ensure_cell_identity(cell, index) or identity_changed
        if identity_changed:
            self.state._save_notebook(real_path, notebook)
        executions = []
        stopped_on_error = False
        failed_cell_id: str | None = None
        for index, cell in enumerate(notebook.cells):
            if cell.cell_type != "code":
                continue
            result = self.execute_cell(
                real_path,
                relative_path,
                cell_id=None,
                cell_index=index,
                owner_session_id=owner_session_id,
            )
            executions.append(result)
            if result.get("status") == "error":
                stopped_on_error = True
                failed_cell_id = result.get("cell_id")
                break
        payload: dict[str, Any] = {
            "status": "error" if stopped_on_error else "ok",
            "path": relative_path,
            "executions": executions,
            "mode": "headless",
        }
        if stopped_on_error:
            payload["stopped_on_error"] = True
            if failed_cell_id is not None:
                payload["failed_cell_id"] = failed_cell_id
        return payload

    def execute_visible_cell(
        self,
        real_path: str,
        relative_path: str,
        *,
        cell_index: int,
        source: str,
        owner_session_id: str | None = None,
    ) -> dict[str, Any]:
        notebook, changed = self.state._load_notebook(real_path)
        index = self.state._find_cell_index(notebook, cell_id=None, cell_index=cell_index)
        cell = notebook.cells[index]
        if cell.cell_type != "code":
            raise RuntimeError("Only code cells can be executed")
        stable_cell_id = self.state._cell_id(cell, index)
        self.state._assert_cell_not_leased(
            relative_path=relative_path,
            cell_id=stable_cell_id,
            owner_session_id=owner_session_id,
            operation="execute-visible-cell",
        )
        if owner_session_id is not None:
            self.state.acquire_cell_lease(
                session_id=owner_session_id,
                path=relative_path,
                cell_id=stable_cell_id,
                kind="edit",
            )
        if cell.source != source:
            cell.source = source
            cell.outputs = []
            cell.execution_count = None
            self.state._clear_cell_runtime_provenance(cell)
            changed = True
        if changed:
            self.state._save_notebook(real_path, notebook)
            self.state._append_activity_event(
                path=relative_path,
                event_type="cell-source-updated",
                detail=f"Updated source for cell {index + 1}",
                actor=self.state._session_actor(owner_session_id, "human"),
                session_id=owner_session_id,
                runtime_id=self._selected_runtime_id(relative_path),
                cell_id=stable_cell_id,
                cell_index=index,
                data={"cell": self.state._cell_payload(cell, index)},
            )
        return self.execute_cell(
            real_path,
            relative_path,
            cell_id=self.state._cell_id(cell, index),
            cell_index=None,
            owner_session_id=owner_session_id,
        )

    def restart(self, real_path: str, relative_path: str) -> dict[str, Any]:
        runtime = self.state.headless_runtimes.get(real_path)
        kernel_id = runtime.python_path if runtime is not None else None
        next_generation = (runtime.kernel_generation + 1) if runtime is not None else 1
        if runtime is not None:
            self.state._shutdown_headless_runtime(real_path)
        restarted = self.state._ensure_headless_runtime(real_path, kernel_id=kernel_id)
        restarted.kernel_generation = max(restarted.kernel_generation, next_generation)
        self.state._sync_headless_runtime_record(relative_path=relative_path, runtime=restarted, status="idle")
        self.state._append_activity_event(
            path=relative_path,
            event_type="kernel-restarted",
            detail=f"Restarted kernel generation {restarted.kernel_generation}",
            runtime_id=restarted.runtime_id,
        )
        self.state.persist()
        return {
            "status": "ok",
            "path": relative_path,
            "kernel_state": "idle",
            "busy": False,
            "mode": "headless",
            "kernel_generation": restarted.kernel_generation,
        }

    def restart_and_run_all(
        self,
        real_path: str,
        relative_path: str,
        *,
        owner_session_id: str | None = None,
    ) -> dict[str, Any]:
        restarted = self.restart(real_path, relative_path)
        executed = self.execute_all(
            real_path,
            relative_path,
            owner_session_id=owner_session_id,
        )
        return {
            **executed,
            "restart": restarted,
        }

    def _append_output_event(
        self,
        *,
        relative_path: str,
        detail: str,
        output_payload: Any,
        outputs: list[Any],
        execution_count: int | None,
        source: str,
        runtime: Any,
        actor: str,
        owner_session_id: str | None,
        cell_id: str,
        cell_index: int,
    ) -> None:
        self.state._append_activity_event(
            path=relative_path,
            event_type="cell-output-appended",
            detail=detail,
            actor=actor,
            session_id=owner_session_id,
            runtime_id=runtime.runtime_id,
            cell_id=cell_id,
            cell_index=cell_index,
            data={
                "output": json.loads(json.dumps(output_payload)),
                "cell": {
                    "index": cell_index,
                    "display_number": cell_index + 1,
                    "cell_id": cell_id,
                    "cell_type": "code",
                    "source": source,
                    "outputs": self.state._canonical_outputs(outputs),
                    "execution_count": execution_count,
                    "metadata": {"custom": {"agent-repl": {"cell_id": cell_id}}},
                },
            },
        )

    def _relative_path(self, real_path: str) -> str:
        return self.state._resolve_document_path(real_path)[1]

    def _selected_runtime_id(self, relative_path: str) -> str | None:
        record = self.state._selected_runtime_record_for_notebook(relative_path)
        return record.runtime_id if record is not None else None
