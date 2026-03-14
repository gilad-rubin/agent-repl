"""Server discovery, session listing, and workspace management."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests
from jupyter_server.serverapp import list_running_servers

from agent_repl.core import CommandError, HTTPCommandError, ProbeResult, ServerClient, ServerInfo, DEFAULT_TIMEOUT


def _server_from_raw(raw: dict[str, Any]) -> ServerInfo:
    return ServerInfo(
        url=raw["url"],
        base_url=raw.get("base_url", "/"),
        root_dir=raw.get("root_dir") or raw.get("notebook_dir") or "",
        token=raw.get("token", ""),
        pid=raw.get("pid"),
        port=raw.get("port"),
        version=raw.get("version"),
        raw=raw,
    )


def _running_server_infos() -> list[ServerInfo]:
    return [_server_from_raw(raw) for raw in list_running_servers()]


def probe_server(server: ServerInfo, timeout: float = DEFAULT_TIMEOUT) -> ProbeResult:
    client = ServerClient(server, timeout=timeout)
    try:
        sessions = client.request("GET", "api/sessions", timeout=timeout)
    except HTTPCommandError as exc:
        return ProbeResult(reachable=True, auth_ok=exc.status_code not in {401, 403}, error=str(exc))
    except requests.RequestException as exc:
        return ProbeResult(reachable=False, auth_ok=False, error=str(exc))

    lab_available = False
    try:
        client.request("GET", "lab/api/workspaces", timeout=timeout)
        lab_available = True
    except (CommandError, requests.RequestException):
        pass

    return ProbeResult(
        reachable=True, auth_ok=True,
        sessions_count=len(sessions) if isinstance(sessions, list) else None,
        lab_workspaces_available=lab_available,
    )


def discover_servers(timeout: float = DEFAULT_TIMEOUT) -> list[dict[str, Any]]:
    discovered: list[dict[str, Any]] = []
    for server in _running_server_infos():
        probe = probe_server(server, timeout=timeout)
        discovered.append({"server": server.summary(), "probe": probe.as_dict()})
    discovered.sort(key=lambda item: (not item["probe"]["reachable"], item["server"]["url"]))
    return discovered


def select_server(*, server_url: str | None, port: int | None, timeout: float) -> ServerInfo:
    if server_url or port:
        for server in _running_server_infos():
            if port and server.port != port:
                continue
            if server_url and server.root_url.rstrip("/") != server_url.rstrip("/"):
                continue
            probe = probe_server(server, timeout=timeout)
            if probe.reachable and probe.auth_ok:
                return server
            criterion = server_url or f"port {port}"
            if probe.reachable and not probe.auth_ok:
                raise CommandError(
                    f"Jupyter server matched {criterion}, but authentication failed (403). "
                    "Tokens are read automatically from Jupyter runtime files. "
                    "If the server uses a custom token, pass --server-url with the token. "
                    "Or restart with: agent-repl start"
                )
        criterion = server_url or f"port {port}"
        raise CommandError(f"No reachable Jupyter server matched {criterion}.")

    reachable_raw: list[ServerInfo] = []
    for server in _running_server_infos():
        probe = probe_server(server, timeout=timeout)
        if probe.reachable and probe.auth_ok:
            reachable_raw.append(server)

    if not reachable_raw:
        raise CommandError(
            "No reachable Jupyter servers found. "
            "Start one with: agent-repl start"
        )
    if len(reachable_raw) > 1:
        urls = ", ".join(server.root_url for server in reachable_raw)
        raise CommandError(f"Multiple reachable Jupyter servers were discovered. Pass --server-url or --port. Candidates: {urls}")
    return reachable_raw[0]


def list_sessions(server: ServerInfo, timeout: float = DEFAULT_TIMEOUT) -> list[dict[str, Any]]:
    client = ServerClient(server, timeout=timeout)
    sessions = client.request("GET", "api/sessions")
    result: list[dict[str, Any]] = []
    for session in sessions:
        kernel = session.get("kernel") or {}
        result.append({
            "id": session.get("id"), "path": session.get("path"),
            "type": session.get("type"), "name": session.get("name"),
            "kernel": {
                "id": kernel.get("id"), "name": kernel.get("name"),
                "execution_state": kernel.get("execution_state"),
                "last_activity": kernel.get("last_activity"),
                "connections": kernel.get("connections"),
            },
        })
    return result


def _extract_notebook_paths(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for child in value.values():
            found.update(_extract_notebook_paths(child))
    elif isinstance(value, list):
        for child in value:
            found.update(_extract_notebook_paths(child))
    elif isinstance(value, str) and value.startswith("notebook:"):
        found.add(value.split(":", 1)[1])
    return found


def _path_exists_in_server_root(
    server: ServerInfo, path: str, *, client: ServerClient | None = None, timeout: float = DEFAULT_TIMEOUT,
) -> bool:
    if server.root_dir and os.path.isdir(server.root_dir):
        return (Path(server.root_dir) / path).exists()
    client = client or ServerClient(server, timeout=timeout)
    try:
        client.request("GET", f"api/contents/{quote(path, safe='/')}", params={"content": 0}, timeout=timeout)
        return True
    except (CommandError, requests.RequestException):
        return False


def list_workspaces(server: ServerInfo, timeout: float = DEFAULT_TIMEOUT) -> list[dict[str, Any]]:
    client = ServerClient(server, timeout=timeout)
    try:
        payload = client.request("GET", "lab/api/workspaces")
    except HTTPCommandError as exc:
        if exc.status_code == 404:
            return []
        raise

    values = payload.get("workspaces", {}).get("values", [])
    result: list[dict[str, Any]] = []
    for workspace in values:
        data = workspace.get("data", {})
        notebooks = sorted(
            path for path in _extract_notebook_paths(data.get("layout-restorer:data", data))
            if _path_exists_in_server_root(server, path, client=client, timeout=timeout)
        )
        if notebooks:
            result.append({"id": workspace.get("metadata", {}).get("id"), "notebooks": notebooks, "raw": workspace})
    return result


def combined_open_notebooks(server: ServerInfo, timeout: float = DEFAULT_TIMEOUT) -> dict[str, Any]:
    sessions = [item for item in list_sessions(server, timeout=timeout) if item.get("type") == "notebook"]
    workspaces = list_workspaces(server, timeout=timeout)
    combined: dict[str, dict[str, Any]] = {}

    for session in sessions:
        path = session.get("path")
        if not path:
            continue
        item = combined.setdefault(path, {"path": path, "live": False, "session_ids": [], "kernel_ids": [], "workspace_ids": []})
        item["live"] = True
        if session["id"]:
            item["session_ids"].append(session["id"])
        kernel_id = (session.get("kernel") or {}).get("id")
        if kernel_id:
            item["kernel_ids"].append(kernel_id)

    for workspace in workspaces:
        workspace_id = workspace.get("id")
        for path in workspace.get("notebooks", []):
            item = combined.setdefault(path, {"path": path, "live": False, "session_ids": [], "kernel_ids": [], "workspace_ids": []})
            if workspace_id:
                item["workspace_ids"].append(workspace_id)

    open_notebooks = []
    for path in sorted(combined):
        item = combined[path]
        item["session_ids"] = sorted(set(item["session_ids"]))
        item["kernel_ids"] = sorted(set(item["kernel_ids"]))
        item["workspace_ids"] = sorted(set(item["workspace_ids"]))
        open_notebooks.append(item)

    return {
        "server": server.summary(),
        "sessions": sessions,
        "workspaces": [{"id": ws["id"], "notebooks": ws["notebooks"]} for ws in workspaces],
        "open_notebooks": open_notebooks,
    }
