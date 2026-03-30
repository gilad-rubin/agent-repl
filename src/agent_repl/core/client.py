"""Client and discovery helpers for the core daemon."""
from __future__ import annotations

import glob
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

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
from agent_repl.http_api import JsonApiClient, poll_execution_until_complete


RUNTIME_FILE_PREFIX = "agent-repl-core-"
DEFAULT_START_TIMEOUT = 5.0


class CoreClient(JsonApiClient):
    """HTTP client for the core daemon."""

    @classmethod
    def start(
        cls,
        workspace_root: str,
        *,
        timeout: float = DEFAULT_START_TIMEOUT,
        runtime_dir: str | None = None,
    ) -> dict[str, Any]:
        workspace_root = os.path.realpath(workspace_root)
        runtime_dir = os.path.realpath(runtime_dir or _runtime_dir())

        try:
            client = cls.discover(workspace_root, runtime_dir=runtime_dir)
        except RuntimeError:
            client = None

        if client is not None:
            result = client.status()
            result["already_running"] = True
            return result

        Path(runtime_dir).mkdir(parents=True, exist_ok=True)
        env = os.environ.copy()
        env["AGENT_REPL_RUNTIME_DIR"] = runtime_dir
        subprocess.Popen(
            [
                sys.executable,
                "-m",
                "agent_repl",
                "core",
                "serve",
                "--workspace-root",
                workspace_root,
                "--runtime-dir",
                runtime_dir,
            ],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                client = cls.discover(workspace_root, runtime_dir=runtime_dir)
                result = client.status()
                result["already_running"] = False
                return result
            except RuntimeError:
                time.sleep(0.1)

        raise RuntimeError("Timed out waiting for agent-repl core daemon to start")

    @classmethod
    def attach(
        cls,
        workspace_root: str,
        *,
        actor: str,
        client: str,
        label: str | None = None,
        capabilities: list[str] | None = None,
        session_id: str | None = None,
        timeout: float = DEFAULT_START_TIMEOUT,
        runtime_dir: str | None = None,
    ) -> dict[str, Any]:
        daemon = cls.start(workspace_root, timeout=timeout, runtime_dir=runtime_dir)
        attached_client = cls.discover(workspace_root, runtime_dir=runtime_dir)
        session_result = attached_client.start_session(
            actor=actor,
            client=client,
            label=label,
            capabilities=capabilities,
            session_id=session_id,
        )
        return {
            "status": "ok",
            "attached": True,
            "workspace_root": daemon["workspace_root"],
            "daemon": daemon,
            "session": session_result["session"],
        }

    @classmethod
    def discover(
        cls,
        workspace_hint: str | None = None,
        *,
        runtime_dir: str | None = None,
    ) -> "CoreClient":
        runtime = os.path.realpath(runtime_dir or _runtime_dir())
        pattern = os.path.join(runtime, f"{RUNTIME_FILE_PREFIX}*.json")
        files = sorted(glob.glob(pattern), key=os.path.getmtime, reverse=True)

        cwd = os.path.realpath(os.getcwd())
        workspace_target = _resolve_workspace_hint(workspace_hint, cwd)
        candidates: list[tuple[int, float, CoreClient]] = []

        for fpath in files:
            try:
                info = json.loads(Path(fpath).read_text())
                pid = info.get("pid")
                if pid and not _pid_alive(pid):
                    try:
                        os.unlink(fpath)
                    except OSError:
                        pass
                    continue

                workspace_root = os.path.realpath(info["workspace_root"])
                if not _path_within(workspace_target, workspace_root):
                    continue

                url = f"http://127.0.0.1:{info['port']}"
                client = cls(url, info["token"])
                client.health()
                specificity = len(Path(workspace_root).parts)
                candidates.append((specificity, os.path.getmtime(fpath), client))
            except Exception:
                continue

        if candidates:
            candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
            return candidates[0][2]

        raise RuntimeError(
            f"No running agent-repl core daemon matched '{workspace_target}'"
        )

    def health(self) -> dict[str, Any]:
        return self._get("/api/health")

    def status(self) -> dict[str, Any]:
        return self._get("/api/status")

    def shutdown(self) -> dict[str, Any]:
        return self._post("/api/shutdown", {})

    def list_sessions(self) -> dict[str, Any]:
        return self._get("/api/sessions")

    def start_session(
        self,
        *,
        actor: str,
        client: str,
        label: str | None = None,
        capabilities: list[str] | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "actor": actor,
            "client": client,
            "session_id": session_id or str(uuid.uuid4()),
        }
        if label:
            body["label"] = label
        if capabilities:
            body["capabilities"] = capabilities
        return self._post("/api/sessions/start", body)

    def resolve_preferred_session(self, *, actor: str = "human") -> dict[str, Any]:
        return self._post("/api/sessions/resolve", {"actor": actor})

    def touch_session(self, session_id: str) -> dict[str, Any]:
        return self._post("/api/sessions/touch", {"session_id": session_id})

    def detach_session(self, session_id: str) -> dict[str, Any]:
        return self._post("/api/sessions/detach", {"session_id": session_id})

    def session_presence_upsert(
        self,
        session_id: str,
        *,
        path: str,
        activity: str,
        cell_id: str | None = None,
        cell_index: int | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"session_id": session_id, "path": path, "activity": activity}
        if cell_id is not None:
            body["cell_id"] = cell_id
        if cell_index is not None:
            body["cell_index"] = cell_index
        return self._post("/api/sessions/presence/upsert", body)

    def session_presence_clear(self, session_id: str, *, path: str | None = None) -> dict[str, Any]:
        body: dict[str, Any] = {"session_id": session_id}
        if path is not None:
            body["path"] = path
        return self._post("/api/sessions/presence/clear", body)

    def end_session(self, session_id: str) -> dict[str, Any]:
        return self._post("/api/sessions/end", {"session_id": session_id})

    def list_documents(self) -> dict[str, Any]:
        return self._get("/api/documents")

    def open_document(self, path: str) -> dict[str, Any]:
        return self._post("/api/documents/open", {"path": path})

    def refresh_document(self, document_id: str) -> dict[str, Any]:
        return self._post("/api/documents/refresh", {"document_id": document_id})

    def rebind_document(self, document_id: str) -> dict[str, Any]:
        return self._post("/api/documents/rebind", {"document_id": document_id})

    def notebook_contents(self, path: str) -> dict[str, Any]:
        return self._post("/api/notebooks/contents", NotebookPathRequest(path=path).to_payload())

    def notebook_status(self, path: str) -> dict[str, Any]:
        return self._post("/api/notebooks/status", NotebookPathRequest(path=path).to_payload())

    def notebook_create(
        self,
        path: str,
        *,
        cells: list[dict[str, Any]] | None = None,
        kernel_id: str | None = None,
    ) -> dict[str, Any]:
        body = NotebookCreateRequest(path=path, cells=cells, kernel_id=kernel_id).to_payload()
        return self._post("/api/notebooks/create", body, timeout=60)

    def notebook_select_kernel(
        self,
        path: str,
        *,
        kernel_id: str | None = None,
    ) -> dict[str, Any]:
        body = NotebookSelectKernelRequest(path=path, kernel_id=kernel_id).to_payload()
        return self._post("/api/notebooks/select-kernel", body, timeout=60)

    def notebook_edit(
        self,
        path: str,
        operations: list[dict[str, Any]],
        *,
        owner_session_id: str | None = None,
    ) -> dict[str, Any]:
        body = NotebookEditRequest(path=path, operations=operations, owner_session_id=owner_session_id).to_payload()
        return self._post("/api/notebooks/edit", body)

    def notebook_execute_cell(
        self,
        path: str,
        *,
        cell_id: str | None = None,
        cell_index: int | None = None,
        wait: bool = True,
        timeout: float = 30,
        owner_session_id: str | None = None,
    ) -> dict[str, Any]:
        body = NotebookExecuteCellRequest(
            path=path,
            cell_id=cell_id,
            cell_index=cell_index,
            owner_session_id=owner_session_id,
            wait=wait,
        ).to_payload()
        result = self._post("/api/notebooks/execute-cell", body, timeout=30)
        if wait and result.get("execution_id"):
            return self._poll_execution(result, timeout)
        return result

    def notebook_insert_execute(
        self,
        path: str,
        source: str,
        *,
        cell_type: str = "code",
        at_index: int = -1,
        wait: bool = True,
        timeout: float = 30,
        owner_session_id: str | None = None,
    ) -> dict[str, Any]:
        body = NotebookInsertExecuteRequest(
            path=path,
            source=source,
            cell_type=cell_type,
            at_index=at_index,
            owner_session_id=owner_session_id,
            wait=wait,
        ).to_payload()
        result = self._post(
            "/api/notebooks/insert-and-execute",
            body,
            timeout=30,
        )
        if wait and result.get("execution_id"):
            return self._poll_execution(result, timeout)
        return result

    def notebook_execution(self, execution_id: str) -> dict[str, Any]:
        return self._post("/api/notebooks/execution", NotebookExecutionLookupRequest(execution_id=execution_id).to_payload())

    def notebook_execute_all(
        self,
        path: str,
        *,
        owner_session_id: str | None = None,
    ) -> dict[str, Any]:
        body = NotebookSessionPathRequest(path=path, owner_session_id=owner_session_id).to_payload()
        return self._post("/api/notebooks/execute-all", body, timeout=120)

    def notebook_interrupt(self, path: str) -> dict[str, Any]:
        return self._post("/api/notebooks/interrupt", NotebookPathRequest(path=path).to_payload(), timeout=30)

    def notebook_runtime(self, path: str) -> dict[str, Any]:
        return self._post("/api/notebooks/runtime", NotebookPathRequest(path=path).to_payload(), timeout=120)

    def notebook_projection(self, path: str) -> dict[str, Any]:
        return self._post("/api/notebooks/projection", NotebookPathRequest(path=path).to_payload(), timeout=120)

    def notebook_activity(self, path: str, *, since: float | None = None) -> dict[str, Any]:
        body = NotebookActivityRequest(path=path, since=since).to_payload()
        return self._post("/api/notebooks/activity", body, timeout=120)

    def notebook_project_visible(
        self,
        path: str,
        *,
        cells: list[dict[str, Any]],
        owner_session_id: str | None = None,
    ) -> dict[str, Any]:
        body = NotebookProjectVisibleRequest(path=path, cells=cells, owner_session_id=owner_session_id).to_payload()
        return self._post("/api/notebooks/project-visible", body, timeout=120)

    def notebook_execute_visible_cell(
        self,
        path: str,
        *,
        cell_index: int,
        source: str,
        owner_session_id: str | None = None,
    ) -> dict[str, Any]:
        body = NotebookExecuteVisibleCellRequest(
            path=path,
            cell_index=cell_index,
            source=source,
            owner_session_id=owner_session_id,
        ).to_payload()
        return self._post("/api/notebooks/execute-visible-cell", body, timeout=120)

    def acquire_cell_lease(
        self,
        path: str,
        *,
        session_id: str,
        cell_id: str | None = None,
        cell_index: int | None = None,
        kind: str = "edit",
        ttl_seconds: float | None = None,
    ) -> dict[str, Any]:
        body = NotebookLeaseAcquireRequest(
            path=path,
            session_id=session_id,
            cell_id=cell_id,
            cell_index=cell_index,
            kind=kind,
            ttl_seconds=ttl_seconds,
        ).to_payload()
        return self._post("/api/notebooks/lease/acquire", body, timeout=120)

    def release_cell_lease(
        self,
        path: str,
        *,
        session_id: str,
        cell_id: str | None = None,
        cell_index: int | None = None,
    ) -> dict[str, Any]:
        body = NotebookLeaseReleaseRequest(
            path=path,
            session_id=session_id,
            cell_id=cell_id,
            cell_index=cell_index,
        ).to_payload()
        return self._post("/api/notebooks/lease/release", body, timeout=120)

    def notebook_restart(self, path: str) -> dict[str, Any]:
        return self._post("/api/notebooks/restart", NotebookPathRequest(path=path).to_payload(), timeout=120)

    def notebook_restart_and_run_all(
        self,
        path: str,
        *,
        owner_session_id: str | None = None,
    ) -> dict[str, Any]:
        body = NotebookSessionPathRequest(path=path, owner_session_id=owner_session_id).to_payload()
        return self._post("/api/notebooks/restart-and-run-all", body, timeout=120)

    def list_branches(self) -> dict[str, Any]:
        return self._get("/api/branches")

    def start_branch(
        self,
        *,
        document_id: str,
        owner_session_id: str | None = None,
        parent_branch_id: str | None = None,
        title: str | None = None,
        purpose: str | None = None,
        branch_id: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "branch_id": branch_id or str(uuid.uuid4()),
            "document_id": document_id,
        }
        if owner_session_id:
            body["owner_session_id"] = owner_session_id
        if parent_branch_id:
            body["parent_branch_id"] = parent_branch_id
        if title:
            body["title"] = title
        if purpose:
            body["purpose"] = purpose
        return self._post("/api/branches/start", body)

    def finish_branch(self, branch_id: str, *, status: str) -> dict[str, Any]:
        return self._post("/api/branches/finish", {"branch_id": branch_id, "status": status})

    def request_branch_review(
        self,
        branch_id: str,
        *,
        requested_by_session_id: str,
        note: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "branch_id": branch_id,
            "requested_by_session_id": requested_by_session_id,
        }
        if note:
            body["note"] = note
        return self._post("/api/branches/review-request", body)

    def resolve_branch_review(
        self,
        branch_id: str,
        *,
        resolved_by_session_id: str,
        resolution: str,
        note: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "branch_id": branch_id,
            "resolved_by_session_id": resolved_by_session_id,
            "resolution": resolution,
        }
        if note:
            body["note"] = note
        return self._post("/api/branches/review-resolve", body)

    def list_runtimes(self) -> dict[str, Any]:
        return self._get("/api/runtimes")

    def start_runtime(
        self,
        *,
        mode: str,
        label: str | None = None,
        runtime_id: str | None = None,
        environment: str | None = None,
        document_path: str | None = None,
        ttl_seconds: int | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "mode": mode,
            "runtime_id": runtime_id or str(uuid.uuid4()),
        }
        if label:
            body["label"] = label
        if environment:
            body["environment"] = environment
        if document_path:
            body["document_path"] = document_path
        if ttl_seconds is not None:
            body["ttl_seconds"] = ttl_seconds
        return self._post("/api/runtimes/start", body)

    def stop_runtime(self, runtime_id: str) -> dict[str, Any]:
        return self._post("/api/runtimes/stop", {"runtime_id": runtime_id})

    def recover_runtime(self, runtime_id: str) -> dict[str, Any]:
        return self._post("/api/runtimes/recover", {"runtime_id": runtime_id})

    def promote_runtime(self, runtime_id: str, *, mode: str = "shared") -> dict[str, Any]:
        return self._post("/api/runtimes/promote", {"runtime_id": runtime_id, "mode": mode})

    def discard_runtime(self, runtime_id: str) -> dict[str, Any]:
        return self._post("/api/runtimes/discard", {"runtime_id": runtime_id})

    def list_runs(self) -> dict[str, Any]:
        return self._get("/api/runs")

    def start_run(
        self,
        *,
        runtime_id: str,
        target_type: str,
        target_ref: str,
        kind: str = "execute",
        run_id: str | None = None,
    ) -> dict[str, Any]:
        return self._post("/api/runs/start", {
            "run_id": run_id or str(uuid.uuid4()),
            "runtime_id": runtime_id,
            "target_type": target_type,
            "target_ref": target_ref,
            "kind": kind,
        })

    def finish_run(self, run_id: str, *, status: str) -> dict[str, Any]:
        return self._post("/api/runs/finish", {"run_id": run_id, "status": status})

    def _poll_execution(self, initial: dict[str, Any], timeout: float) -> dict[str, Any]:
        return poll_execution_until_complete(
            initial,
            timeout=timeout,
            fetch_execution=self.notebook_execution,
            in_progress_statuses={"running", "queued", "started"},
        )

    def _get(self, endpoint: str, timeout: float = 10) -> dict[str, Any]:
        return super()._get(endpoint, timeout=timeout)

    def _post(self, endpoint: str, body: dict[str, Any], timeout: float = 10) -> dict[str, Any]:
        return super()._post(endpoint, body, timeout=timeout)


def _runtime_dir() -> str:
    override = os.environ.get("AGENT_REPL_RUNTIME_DIR")
    if override:
        return os.path.realpath(override)
    if sys.platform == "darwin":
        return os.path.realpath(os.path.join(os.path.expanduser("~"), "Library", "Jupyter", "runtime"))
    return os.path.realpath(os.path.join(os.path.expanduser("~"), ".local", "share", "jupyter", "runtime"))


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def _resolve_workspace_hint(workspace_hint: str | None, cwd: str) -> str:
    if not workspace_hint:
        return cwd
    if os.path.isabs(workspace_hint):
        return os.path.realpath(workspace_hint)
    return os.path.realpath(os.path.join(cwd, workspace_hint))


def _path_within(candidate: str, root: str) -> bool:
    try:
        common = os.path.commonpath([os.path.realpath(candidate), os.path.realpath(root)])
        return common == os.path.realpath(root)
    except ValueError:
        return False
