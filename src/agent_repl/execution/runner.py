"""High-level execution: execute_code, run-all, restart, insert-execute."""
from __future__ import annotations

import hashlib
import json
from typing import Any
from urllib.parse import quote

from agent_repl.core import CommandError, ServerClient, ServerInfo, ExecuteRequest, KernelTarget
from agent_repl.execution.transport import _sanitize_error_text, execute_request_with_target
from agent_repl.notebook.cells import apply_insert
from agent_repl.notebook.io import load_notebook_model, save_notebook_content, save_run_all_outputs
from agent_repl.output.filtering import strip_media_from_event
from agent_repl.output.formatting import events_to_notebook_outputs
from agent_repl.server.discovery import list_sessions
from agent_repl.server.kernels import wait_for_kernel_idle


# --- Session / kernel target resolution ---

def _resolve_session(server: ServerInfo, *, path: str | None, session_id: str | None, kernel_id: str | None, timeout: float) -> dict[str, Any]:
    sessions = list_sessions(server, timeout=timeout)
    if session_id:
        for s in sessions:
            if s.get("id") == session_id:
                _validate_target_consistency(s, path=path, kernel_id=kernel_id)
                return s
        raise CommandError(f"No session matched id {session_id}.")
    if kernel_id:
        for s in sessions:
            if (s.get("kernel") or {}).get("id") == kernel_id:
                _validate_target_consistency(s, path=path, kernel_id=kernel_id)
                return s
        raise CommandError(f"No live session matched kernel id {kernel_id}.")
    if path:
        matches = [s for s in sessions if s.get("path") == path]
        if not matches:
            raise CommandError(f"No live session matched notebook path {path}.")
        if len(matches) > 1:
            ids = ", ".join(s["id"] for s in matches if s.get("id"))
            raise CommandError(f"Multiple live sessions matched notebook path {path}. Pass --session-id. Sessions: {ids}")
        return matches[0]
    raise CommandError("Pass one of --path, --session-id, or --kernel-id.")


def _validate_target_consistency(session: dict[str, Any], *, path: str | None, kernel_id: str | None) -> None:
    sp = session.get("path")
    sk = (session.get("kernel") or {}).get("id")
    if path and sp and sp != path:
        raise CommandError(f"Conflicting live target selectors: session resolves to path {sp!r}, not {path!r}.")
    if kernel_id and sk and sk != kernel_id:
        raise CommandError(f"Conflicting live target selectors: session resolves to kernel {sk!r}, not {kernel_id!r}.")


def resolve_kernel_target(server: ServerInfo, *, path: str | None, session_id: str | None, kernel_id: str | None, timeout: float) -> KernelTarget:
    session = _resolve_session(server, path=path, session_id=session_id, kernel_id=kernel_id, timeout=timeout)
    resolved_kernel_id = kernel_id or (session.get("kernel") or {}).get("id")
    if not resolved_kernel_id:
        raise CommandError("Could not determine a kernel id for execution.")
    return KernelTarget(
        kernel_id=resolved_kernel_id, kernel_name=(session.get("kernel") or {}).get("name"),
        session_id=session.get("id"), path=session.get("path"),
    )


def _execute_request(server: ServerInfo, *, path: str | None, session_id: str | None, kernel_id: str | None, request: ExecuteRequest, transport: str, timeout: float):
    target = resolve_kernel_target(server, path=path, session_id=session_id, kernel_id=kernel_id, timeout=timeout)
    return execute_request_with_target(server, target=target, request=request, transport=transport, timeout=timeout)


# --- execute_code ---

def execute_code(
    server: ServerInfo, *, path: str | None, session_id: str | None, kernel_id: str | None,
    code: str, transport: str, timeout: float,
    save_outputs: bool = False, cell_id: str | None = None, strip_media: bool = True,
) -> dict[str, Any]:
    result = _execute_request(server, path=path, session_id=session_id, kernel_id=kernel_id, request=ExecuteRequest(code=code), transport=transport, timeout=timeout)
    result_dict = result.as_dict()

    if strip_media:
        result_dict["events"] = [strip_media_from_event(e) for e in result_dict["events"]]

    outputs_saved = False
    if save_outputs and path and cell_id:
        model = load_notebook_model(server, path, timeout=timeout)
        for cell in model["content"].get("cells", []):
            if cell.get("id") == cell_id and cell.get("cell_type") == "code":
                cell_outputs, exec_count = events_to_notebook_outputs(result_dict.get("events") or [])
                cell["outputs"] = cell_outputs
                cell["execution_count"] = exec_count
                save_notebook_content(server, path, model["content"], timeout=timeout, expected_last_modified=model.get("last_modified"))
                outputs_saved = True
                break

    result_dict["outputs_saved"] = outputs_saved
    return result_dict


# --- restart ---

def restart_kernel(server: ServerInfo, *, path: str | None, session_id: str | None, kernel_id: str | None, timeout: float) -> dict[str, Any]:
    target = resolve_kernel_target(server, path=path, session_id=session_id, kernel_id=kernel_id, timeout=timeout)
    client = ServerClient(server, timeout=timeout)
    client.request("POST", f"api/kernels/{quote(target.kernel_id, safe='')}/restart", timeout=timeout)
    model = wait_for_kernel_idle(server, target.kernel_id, timeout=timeout)
    return {
        "operation": "restart-kernel", "kernel_id": target.kernel_id,
        "kernel_name": target.kernel_name, "session_id": target.session_id, "path": target.path,
        "kernel": {
            "id": model.get("id"), "name": model.get("name"),
            "execution_state": model.get("execution_state"),
            "last_activity": model.get("last_activity"), "connections": model.get("connections"),
        },
    }


# --- run-all ---

def _require_notebook_session(target: KernelTarget, feature: str) -> None:
    if not target.path:
        raise CommandError(f"{feature} requires a notebook path.")
    if not target.session_id:
        raise CommandError(f"{feature} requires a live notebook session, not just a bare kernel id.")


def _source_preview(source: str, limit: int = 120) -> str:
    single_line = " ".join(source.split())
    return single_line if len(single_line) <= limit else f"{single_line[:limit - 3]}..."


def _should_execute_cell(cell: dict[str, Any], *, skip_tags: set[str] | None, only_tags: set[str] | None) -> tuple[bool, str | None]:
    """Check if a cell should be executed based on directives. Returns (should_run, skip_reason)."""
    from agent_repl.notebook.directives import extract_tags, has_skip_directive
    if has_skip_directive(cell):
        return False, "agent-skip"
    cell_tags = extract_tags(cell)
    if only_tags and not (cell_tags & only_tags):
        return False, "not-in-only-tags"
    if skip_tags and (cell_tags & skip_tags):
        return False, "in-skip-tags"
    return True, None


def run_all_cells(
    server: ServerInfo, *, path: str | None, session_id: str | None, kernel_id: str | None,
    transport: str, timeout: float, save_outputs: bool = False, strip_media: bool = True,
    skip_tags: set[str] | None = None, only_tags: set[str] | None = None,
) -> dict[str, Any]:
    target = resolve_kernel_target(server, path=path, session_id=session_id, kernel_id=kernel_id, timeout=timeout)
    _require_notebook_session(target, "Run-all")
    notebook_path = path or target.path

    model = load_notebook_model(server, notebook_path, timeout=timeout)
    snapshot_sha256 = hashlib.sha256(json.dumps(model["content"], sort_keys=True).encode("utf-8")).hexdigest()
    results: list[dict[str, Any]] = []
    executed_cell_count = skipped_cell_count = 0
    overall_status = "ok"
    failed_cell: dict[str, Any] | None = None

    for cell_index, cell in enumerate(model["content"]["cells"]):
        if cell.get("cell_type") != "code":
            continue
        source = cell.get("source", "")
        if not source.strip():
            skipped_cell_count += 1
            results.append({"index": cell_index, "cell_id": cell.get("id"), "status": "skipped", "reason": "empty-source", "source_preview": _source_preview(source)})
            continue

        # Directive-based filtering
        should_run, skip_reason = _should_execute_cell(cell, skip_tags=skip_tags, only_tags=only_tags)
        if not should_run:
            skipped_cell_count += 1
            results.append({"index": cell_index, "cell_id": cell.get("id"), "status": "skipped", "reason": skip_reason, "source_preview": _source_preview(source)})
            continue

        executed_cell_count += 1
        result = execute_request_with_target(server, target=target, request=ExecuteRequest(code=source), transport=transport, timeout=timeout).as_dict()

        if save_outputs:
            cell_outputs, exec_count = events_to_notebook_outputs(result.get("events") or [])
            cell["outputs"] = cell_outputs
            cell["execution_count"] = exec_count

        events = [strip_media_from_event(e) for e in (result.get("events") or [])] if strip_media else result.get("events") or []
        cell_result = {
            "index": cell_index, "cell_id": cell.get("id"), "source_preview": _source_preview(source),
            "status": result.get("status"), "transport": result.get("transport"),
            "reply": result.get("reply"), "events": events,
        }
        results.append(cell_result)

        if result.get("status") != "ok":
            overall_status = "error"
            failed_cell = cell_result
            break

    outputs_saved = False
    if save_outputs:
        save_run_all_outputs(server, notebook_path, executed_model=model["content"], timeout=timeout)
        outputs_saved = True

    return {
        "operation": "run-all", "path": notebook_path,
        "kernel_id": target.kernel_id, "kernel_name": target.kernel_name, "session_id": target.session_id,
        "snapshot_last_modified": model.get("last_modified"), "snapshot_sha256": snapshot_sha256,
        "transport_requested": transport, "timeout_per_cell_seconds": timeout,
        "status": overall_status, "executed_cell_count": executed_cell_count,
        "skipped_cell_count": skipped_cell_count, "failed_cell": failed_cell,
        "cells": results, "outputs_saved": outputs_saved,
        "note": "Run-all executed and saved outputs back to the notebook file." if outputs_saved
                else "Run-all executes against the live kernel for verification and does not persist notebook outputs.",
    }


def restart_and_run_all(
    server: ServerInfo, *, path: str | None, session_id: str | None, kernel_id: str | None,
    transport: str, timeout: float, save_outputs: bool = False, strip_media: bool = True,
    skip_tags: set[str] | None = None, only_tags: set[str] | None = None,
) -> dict[str, Any]:
    target = resolve_kernel_target(server, path=path, session_id=session_id, kernel_id=kernel_id, timeout=timeout)
    _require_notebook_session(target, "Restart-run-all")
    restart = restart_kernel(server, path=target.path, session_id=target.session_id, kernel_id=target.kernel_id, timeout=timeout)
    run_all = run_all_cells(server, path=target.path, session_id=target.session_id, kernel_id=target.kernel_id, transport=transport, timeout=timeout, save_outputs=save_outputs, strip_media=strip_media, skip_tags=skip_tags, only_tags=only_tags)
    return {"operation": "restart-run-all", "restart": restart, "run_all": run_all, "note": "Restart-run-all is explicit verification mode; prefer incremental execute/edit unless the user asked for a fresh run."}


# --- insert-and-execute ---

def insert_and_execute(
    server: ServerInfo, *, path: str, cell_type: str = "code", source: str,
    at_index: int = -1, session_id: str | None = None, kernel_id: str | None = None,
    transport: str, timeout: float, strip_media: bool = True,
) -> dict[str, Any]:
    """Insert a new cell and immediately execute it."""
    model = load_notebook_model(server, path, timeout=timeout)
    insert_result = apply_insert(model["content"]["cells"], cell_type=cell_type, source=source, at_index=at_index)
    save_notebook_content(server, path, model["content"], timeout=timeout, expected_last_modified=model.get("last_modified"))

    exec_result = execute_code(
        server, path=path, session_id=session_id, kernel_id=kernel_id,
        code=source, transport=transport, timeout=timeout,
        save_outputs=True, cell_id=insert_result["cell_id"], strip_media=strip_media,
    )
    return {"operation": "insert-execute", "insert": {"path": path, "operation": "insert-cell", **insert_result}, "execute": exec_result}


# --- respond to prompt ---

def respond_to_prompt(
    server: ServerInfo, *, path: str, prompt_cell_id: str, source: str,
    cell_type: str = "code", session_id: str | None = None, kernel_id: str | None = None,
    transport: str, timeout: float, strip_media: bool = True,
) -> dict[str, Any]:
    """Insert response cell after a prompt cell, execute it, link via metadata."""
    import time as _time
    from agent_repl.notebook.cells import resolve_cell_index
    from agent_repl.notebook.directives import extract_prompt

    model = load_notebook_model(server, path, timeout=timeout)
    cells = model["content"]["cells"]

    # Find the prompt cell
    prompt_index = resolve_cell_index(cells, index=None, cell_id=prompt_cell_id)
    instruction = extract_prompt(cells[prompt_index])

    # Insert response after prompt with linking metadata
    response_metadata = {"agent-repl": {"responds_to": prompt_cell_id, "type": "response", "timestamp": _time.time()}}
    insert_result = apply_insert(cells, cell_type=cell_type, source=source, at_index=prompt_index + 1, metadata=response_metadata)
    save_notebook_content(server, path, model["content"], timeout=timeout, expected_last_modified=model.get("last_modified"))

    # Execute if code cell
    exec_result = None
    if cell_type == "code":
        exec_result = execute_code(
            server, path=path, session_id=session_id, kernel_id=kernel_id,
            code=source, transport=transport, timeout=timeout,
            save_outputs=True, cell_id=insert_result["cell_id"], strip_media=strip_media,
        )

    return {
        "operation": "respond",
        "prompt": {"cell_id": prompt_cell_id, "index": prompt_index, "instruction": instruction},
        "insert": {"path": path, "operation": "insert-cell", **insert_result},
        "execute": exec_result,
    }
