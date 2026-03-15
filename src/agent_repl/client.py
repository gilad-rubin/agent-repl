"""BridgeClient — discover and talk to the agent-repl VS Code bridge."""
from __future__ import annotations

import glob
import json
import os
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
        """Scan runtime dir for connection files, ping health, return first live client."""
        runtime = _runtime_dir()
        pattern = os.path.join(runtime, "agent-repl-bridge-*.json")
        for path in sorted(glob.glob(pattern), key=os.path.getmtime, reverse=True):
            try:
                info = json.loads(Path(path).read_text())
                url = f"http://127.0.0.1:{info['port']}"
                client = cls(url, info["token"])
                client.health()  # ping
                return client
            except Exception:
                continue
        raise RuntimeError("No running agent-repl bridge found")

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
        self, path: str, *, cell_id: str | None = None, cell_index: int | None = None
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"path": path}
        if cell_id is not None:
            body["cell_id"] = cell_id
        if cell_index is not None:
            body["cell_index"] = cell_index
        return self._post("/api/notebook/execute-cell", body)

    def insert_and_execute(
        self, path: str, source: str, cell_type: str = "code", at_index: int = -1
    ) -> dict[str, Any]:
        return self._post("/api/notebook/insert-and-execute", {
            "path": path, "source": source, "cell_type": cell_type, "at_index": at_index
        })

    def execute_all(self, path: str) -> dict[str, Any]:
        return self._post("/api/notebook/execute-all", {"path": path})

    def restart_kernel(self, path: str) -> dict[str, Any]:
        return self._post("/api/notebook/restart-kernel", {"path": path})

    def restart_and_run_all(self, path: str) -> dict[str, Any]:
        return self._post("/api/notebook/restart-and-run-all", {"path": path})

    def create(
        self, path: str, cells: list[dict[str, Any]] | None = None
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"path": path}
        if cells is not None:
            body["cells"] = cells
        return self._post("/api/notebook/create", body)

    def prompt(self, path: str, instruction: str) -> dict[str, Any]:
        return self._post("/api/notebook/prompt", {"path": path, "instruction": instruction})

    def prompt_status(self, path: str, cell_id: str, status: str) -> dict[str, Any]:
        return self._post("/api/notebook/prompt-status", {
            "path": path, "cell_id": cell_id, "status": status
        })

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

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
