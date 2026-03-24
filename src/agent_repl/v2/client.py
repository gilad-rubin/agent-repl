"""Client and discovery helpers for the experimental v2 core daemon."""
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

import requests

from agent_repl.client import _bridge_error_message


RUNTIME_FILE_PREFIX = "agent-repl-v2-core-"
DEFAULT_START_TIMEOUT = 5.0


class V2Client:
    """HTTP client for the experimental v2 core daemon."""

    def __init__(self, base_url: str, token: str):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self._session = requests.Session()
        self._session.headers["Authorization"] = f"token {token}"

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
        env["AGENT_REPL_V2_RUNTIME_DIR"] = runtime_dir
        subprocess.Popen(
            [
                sys.executable,
                "-m",
                "agent_repl",
                "v2",
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

        raise RuntimeError("Timed out waiting for agent-repl v2 core daemon to start")

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
    ) -> "V2Client":
        runtime = os.path.realpath(runtime_dir or _runtime_dir())
        pattern = os.path.join(runtime, f"{RUNTIME_FILE_PREFIX}*.json")
        files = sorted(glob.glob(pattern), key=os.path.getmtime, reverse=True)

        cwd = os.path.realpath(os.getcwd())
        workspace_target = _resolve_workspace_hint(workspace_hint, cwd)
        candidates: list[tuple[int, float, V2Client]] = []

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
            f"No running agent-repl v2 core daemon matched '{workspace_target}'"
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

    def touch_session(self, session_id: str) -> dict[str, Any]:
        return self._post("/api/sessions/touch", {"session_id": session_id})

    def detach_session(self, session_id: str) -> dict[str, Any]:
        return self._post("/api/sessions/detach", {"session_id": session_id})

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

    def list_runtimes(self) -> dict[str, Any]:
        return self._get("/api/runtimes")

    def start_runtime(
        self,
        *,
        mode: str,
        label: str | None = None,
        runtime_id: str | None = None,
        environment: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "mode": mode,
            "runtime_id": runtime_id or str(uuid.uuid4()),
        }
        if label:
            body["label"] = label
        if environment:
            body["environment"] = environment
        return self._post("/api/runtimes/start", body)

    def stop_runtime(self, runtime_id: str) -> dict[str, Any]:
        return self._post("/api/runtimes/stop", {"runtime_id": runtime_id})

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

    def _get(self, endpoint: str) -> dict[str, Any]:
        response = self._session.get(f"{self.base_url}{endpoint}", timeout=10)
        self._raise_for_status(response)
        return response.json()

    def _post(self, endpoint: str, body: dict[str, Any]) -> dict[str, Any]:
        response = self._session.post(f"{self.base_url}{endpoint}", json=body, timeout=10)
        self._raise_for_status(response)
        return response.json()

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


def _runtime_dir() -> str:
    override = os.environ.get("AGENT_REPL_V2_RUNTIME_DIR")
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
