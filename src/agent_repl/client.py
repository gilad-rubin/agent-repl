"""BridgeClient — discover and talk to the agent-repl VS Code bridge."""
from __future__ import annotations

import glob
import json
import os
import time
from pathlib import Path
from typing import Any

import requests


def _bridge_error_message(response: requests.Response) -> str | None:
    try:
        payload = response.json()
    except ValueError:
        return None

    if not isinstance(payload, dict):
        return None

    error = payload.get("error")
    if isinstance(error, str) and error.strip():
        return error.strip()
    return None


class BridgeClient:
    """HTTP client for the agent-repl bridge server."""

    def __init__(self, base_url: str, token: str):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self._session = requests.Session()
        self._session.headers["Authorization"] = f"token {token}"

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    @classmethod
    def discover(cls, workspace_hint: str | None = None) -> "BridgeClient":
        """Scan runtime dir for connection files and require a workspace match."""
        runtime = _runtime_dir()
        pattern = os.path.join(runtime, "agent-repl-bridge-*.json")
        files = sorted(glob.glob(pattern), key=os.path.getmtime, reverse=True)

        cwd = os.path.realpath(os.getcwd())
        workspace_target = _resolve_workspace_hint(workspace_hint, cwd)
        live: list[tuple[dict, "BridgeClient", dict[str, Any]]] = []

        for fpath in files:
            try:
                info = json.loads(Path(fpath).read_text())
                # Remove connection files from dead processes
                pid = info.get("pid")
                if pid and not _pid_alive(pid):
                    try:
                        os.unlink(fpath)
                    except OSError:
                        pass
                    continue
                url = f"http://127.0.0.1:{info['port']}"
                client = cls(url, info["token"])
                health = client.health()  # ping
                live.append((info, client, health))
            except Exception:
                continue

        if not live:
            raise RuntimeError("No running agent-repl bridge found")

        # Require a bridge whose workspace_folders contains the target path.
        targets = [workspace_target]
        if workspace_target != cwd:
            targets.append(cwd)

        for target in targets:
            for info, client, health in live:
                for folder in info.get("workspace_folders", []):
                    real_folder = os.path.realpath(folder)
                    if _path_within(target, real_folder):
                        return client
                for notebook_path in health.get("open_notebooks", []):
                    if os.path.realpath(notebook_path) == target:
                        return client

        raise RuntimeError(_workspace_mismatch_message(workspace_target, cwd, live))

    # ------------------------------------------------------------------
    # Endpoints
    # ------------------------------------------------------------------

    def health(self) -> dict[str, Any]:
        return self._get("/api/health")

    def reload(self) -> dict[str, Any]:
        return self._post("/api/reload", {})

    def contents(self, path: str) -> dict[str, Any]:
        return self._get("/api/notebook/contents", params=self._path_params(path))

    def status(self, path: str) -> dict[str, Any]:
        return self._get("/api/notebook/status", params=self._path_params(path))

    def edit(self, path: str, operations: list[dict[str, Any]]) -> dict[str, Any]:
        body = self._path_body(path)
        body["operations"] = operations
        return self._post("/api/notebook/edit", body)

    def execute_cell(
        self, path: str, *, cell_id: str | None = None, cell_index: int | None = None,
        wait: bool = True, timeout: float = 30,
    ) -> dict[str, Any]:
        body = self._path_body(path)
        if cell_id is not None:
            body["cell_id"] = cell_id
        if cell_index is not None:
            body["cell_index"] = cell_index
        result = self._post("/api/notebook/execute-cell", body)
        if wait and result.get("execution_id"):
            return self._poll_execution(result, timeout)
        return result

    def insert_and_execute(
        self, path: str, source: str, cell_type: str = "code", at_index: int = -1,
        wait: bool = True, timeout: float = 30,
    ) -> dict[str, Any]:
        body = self._path_body(path)
        body.update({"source": source, "cell_type": cell_type, "at_index": at_index})
        result = self._post("/api/notebook/insert-and-execute", body)
        if wait and result.get("execution_id"):
            return self._poll_execution(result, timeout)
        return result

    def execute_all(self, path: str) -> dict[str, Any]:
        return self._post("/api/notebook/execute-all", self._path_body(path))

    def restart_kernel(self, path: str) -> dict[str, Any]:
        return self._post("/api/notebook/restart-kernel", self._path_body(path))

    def restart_and_run_all(self, path: str) -> dict[str, Any]:
        return self._post("/api/notebook/restart-and-run-all", self._path_body(path))

    def create(
        self, path: str, cells: list[dict[str, Any]] | None = None,
        kernel_id: str | None = None,
    ) -> dict[str, Any]:
        body = self._path_body(path)
        if cells is not None:
            body["cells"] = cells
        if kernel_id is not None:
            body["kernel_id"] = kernel_id
        return self._post("/api/notebook/create", body)

    def kernels(self) -> dict[str, Any]:
        return self._get("/api/notebook/kernels", params={"cwd": os.getcwd()})

    def select_kernel(
        self,
        path: str,
        kernel_id: str | None = None,
        extension: str | None = None,
        interactive: bool = False,
    ) -> dict[str, Any]:
        body = self._path_body(path)
        if kernel_id is not None:
            body["kernel_id"] = kernel_id
        if extension is not None:
            body["extension"] = extension
        if interactive:
            body["interactive"] = True
        return self._post("/api/notebook/select-kernel", body)

    def prompt(self, path: str, instruction: str) -> dict[str, Any]:
        body = self._path_body(path)
        body["instruction"] = instruction
        return self._post("/api/notebook/prompt", body)

    def prompt_status(self, path: str, cell_id: str, status: str) -> dict[str, Any]:
        body = self._path_body(path)
        body.update({"cell_id": cell_id, "status": status})
        return self._post("/api/notebook/prompt-status", body)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _poll_execution(
        self, initial: dict[str, Any], timeout: float,
    ) -> dict[str, Any]:
        """Poll until execution completes or timeout expires."""
        exec_id = initial["execution_id"]
        deadline = time.monotonic() + timeout
        interval = 0.2
        while time.monotonic() < deadline:
            time.sleep(interval)
            result = self._get("/api/notebook/execution", params={"id": exec_id})
            status = result.get("status")
            if status not in ("running", "queued"):
                # Carry forward cell_id and cell_index from the initial response
                for key in ("cell_id", "cell_index", "operation"):
                    if key in initial and key not in result:
                        result[key] = initial[key]
                return result
            interval = min(interval * 1.5, 1.0)
        return {**initial, "status": "timeout", "timeout_seconds": timeout}

    def _get(self, endpoint: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        r = self._session.get(f"{self.base_url}{endpoint}", params=params, timeout=10)
        self._raise_for_status(r)
        return r.json()

    def _post(self, endpoint: str, body: dict[str, Any]) -> dict[str, Any]:
        r = self._session.post(f"{self.base_url}{endpoint}", json=body, timeout=30)
        self._raise_for_status(r)
        return r.json()

    def _raise_for_status(self, response: requests.Response) -> None:
        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            detail = _bridge_error_message(response)
            if detail:
                status = getattr(response, "status_code", "HTTP error")
                reason = getattr(response, "reason", "") or "HTTP error"
                url = getattr(response, "url", None)
                location = f" for url: {url}" if url else ""
                raise RuntimeError(f"{status} {reason}{location}: {detail}") from exc
            raise

    def _path_params(self, path: str) -> dict[str, str]:
        return {"path": path, "cwd": os.getcwd()}

    def _path_body(self, path: str) -> dict[str, Any]:
        return {"path": path, "cwd": os.getcwd()}


def _runtime_dir() -> str:
    import sys
    if sys.platform == "darwin":
        return os.path.join(os.path.expanduser("~"), "Library", "Jupyter", "runtime")
    return os.path.join(os.path.expanduser("~"), ".local", "share", "jupyter", "runtime")


def _pid_alive(pid: int) -> bool:
    """Check if a process is still running."""
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
    return os.path.realpath(os.path.abspath(os.path.join(cwd, workspace_hint)))


def _path_within(target: str, folder: str) -> bool:
    return target == folder or target.startswith(folder + os.sep)


def _workspace_mismatch_message(
    workspace_target: str,
    cwd: str,
    live: list[tuple[dict[str, Any], BridgeClient, dict[str, Any]]],
) -> str:
    active = []
    for info, _client, health in live:
        folders = info.get("workspace_folders") or ["<no workspace>"]
        open_notebooks = health.get("open_notebooks") or []
        extra = f"; open notebooks: {', '.join(open_notebooks)}" if open_notebooks else ""
        active.append(f"port {info.get('port')}: {', '.join(folders)}{extra}")
    joined = "; ".join(active)
    return (
        f"No running agent-repl bridge matched '{workspace_target}' or cwd '{cwd}'. "
        f"Active bridges: {joined}. "
        "Open this workspace in VS Code and wait for Agent REPL to start, or run 'Agent REPL: Start Bridge' in that window."
    )
