"""BridgeClient — discover and talk to the agent-repl VS Code bridge."""
from __future__ import annotations

import glob
import json
import os
import time
from pathlib import Path
from typing import Any

import requests


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
    def discover(cls) -> "BridgeClient":
        """Scan runtime dir for connection files, prefer the bridge whose workspace matches cwd."""
        runtime = _runtime_dir()
        pattern = os.path.join(runtime, "agent-repl-bridge-*.json")
        files = sorted(glob.glob(pattern), key=os.path.getmtime, reverse=True)

        cwd = os.path.realpath(os.getcwd())
        live: list[tuple[dict, "BridgeClient"]] = []

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
                client.health()  # ping
                live.append((info, client))
            except Exception:
                continue

        if not live:
            raise RuntimeError("No running agent-repl bridge found")

        # Prefer the bridge whose workspace_folders contains cwd
        for info, client in live:
            for folder in info.get("workspace_folders", []):
                real_folder = os.path.realpath(folder)
                if cwd == real_folder or cwd.startswith(real_folder + os.sep):
                    return client

        # No workspace match — fall back to most recent
        return live[0][1]

    # ------------------------------------------------------------------
    # Endpoints
    # ------------------------------------------------------------------

    def health(self) -> dict[str, Any]:
        return self._get("/api/health")

    def reload(self) -> dict[str, Any]:
        return self._post("/api/reload", {})

    def contents(self, path: str) -> dict[str, Any]:
        return self._get("/api/notebook/contents", params={"path": path})

    def status(self, path: str) -> dict[str, Any]:
        return self._get("/api/notebook/status", params={"path": path})

    def edit(self, path: str, operations: list[dict[str, Any]]) -> dict[str, Any]:
        return self._post("/api/notebook/edit", {"path": path, "operations": operations})

    def execute_cell(
        self, path: str, *, cell_id: str | None = None, cell_index: int | None = None,
        wait: bool = True, timeout: float = 30,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"path": path}
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
        result = self._post("/api/notebook/insert-and-execute", {
            "path": path, "source": source, "cell_type": cell_type, "at_index": at_index
        })
        if wait and result.get("execution_id"):
            return self._poll_execution(result, timeout)
        return result

    def execute_all(self, path: str) -> dict[str, Any]:
        return self._post("/api/notebook/execute-all", {"path": path})

    def restart_kernel(self, path: str) -> dict[str, Any]:
        return self._post("/api/notebook/restart-kernel", {"path": path})

    def restart_and_run_all(self, path: str) -> dict[str, Any]:
        return self._post("/api/notebook/restart-and-run-all", {"path": path})

    def create(
        self, path: str, cells: list[dict[str, Any]] | None = None,
        kernel_id: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"path": path, "cwd": os.getcwd()}
        if cells is not None:
            body["cells"] = cells
        if kernel_id is not None:
            body["kernel_id"] = kernel_id
        return self._post("/api/notebook/create", body)

    def kernels(self) -> dict[str, Any]:
        return self._get("/api/notebook/kernels", params={"cwd": os.getcwd()})

    def select_kernel(
        self, path: str, kernel_id: str | None = None, extension: str | None = None
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"path": path}
        if kernel_id is not None:
            body["kernel_id"] = kernel_id
        if extension is not None:
            body["extension"] = extension
        return self._post("/api/notebook/select-kernel", body)

    def prompt(self, path: str, instruction: str) -> dict[str, Any]:
        return self._post("/api/notebook/prompt", {"path": path, "instruction": instruction})

    def prompt_status(self, path: str, cell_id: str, status: str) -> dict[str, Any]:
        return self._post("/api/notebook/prompt-status", {
            "path": path, "cell_id": cell_id, "status": status
        })

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
        r.raise_for_status()
        return r.json()

    def _post(self, endpoint: str, body: dict[str, Any]) -> dict[str, Any]:
        r = self._session.post(f"{self.base_url}{endpoint}", json=body, timeout=30)
        r.raise_for_status()
        return r.json()


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
