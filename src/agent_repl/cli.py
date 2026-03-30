"""CLI entry point — argparse-based subcommands talking to the bridge."""
from __future__ import annotations

import asyncio
import argparse
import json
import os
import socket
import shutil
import sys
import tomllib
import subprocess
from importlib.metadata import PackageNotFoundError, version as package_version
from pathlib import Path
from typing import Any

from agent_repl.client import BridgeClient
from agent_repl.core.client import DEFAULT_START_TIMEOUT, CoreClient
from agent_repl.core.server import serve_forever
from agent_repl.http_api import ApiError
from agent_repl.notebook_runtime_client import (
    NotebookRuntimeClient,
    call_with_owner_session,
    resolve_owner_session_id,
)


def _out(data: Any, pretty: bool = False) -> None:
    print(json.dumps(data, indent=2 if pretty else None))


def _app_version() -> str:
    try:
        return package_version("agent-repl")
    except PackageNotFoundError:
        pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
        return tomllib.loads(pyproject.read_text())["project"]["version"]


def _client(workspace_hint: str | None = None) -> BridgeClient:
    return BridgeClient.discover(workspace_hint=workspace_hint)


def _core_client(workspace_hint: str | None = None, runtime_dir: str | None = None) -> CoreClient:
    """Get a fresh core client, ensuring the daemon is up to date."""
    workspace_root = os.path.realpath(workspace_hint or os.getcwd())
    CoreClient.start(workspace_root, runtime_dir=runtime_dir)
    return CoreClient.discover(workspace_hint=workspace_hint, runtime_dir=runtime_dir)


def _core_client_raw(workspace_hint: str | None = None, runtime_dir: str | None = None) -> CoreClient:
    """Bare discover for core stop/status diagnostics."""
    return CoreClient.discover(workspace_hint=workspace_hint, runtime_dir=runtime_dir)


def _workspace_root() -> str:
    return os.path.realpath(os.getcwd())


def _workspace_root_from_arg(workspace_root: str | None = None) -> str:
    return os.path.realpath(workspace_root or os.getcwd())


def _notebook_client(path: str) -> NotebookRuntimeClient:
    workspace_root = _workspace_root()
    CoreClient.start(workspace_root)
    return CoreClient.discover(workspace_hint=path)


def _workspace_settings_path(workspace_root: str) -> Path:
    return Path(workspace_root) / ".vscode" / "settings.json"


def _read_workspace_settings(workspace_root: str) -> dict[str, Any]:
    settings_path = _workspace_settings_path(workspace_root)
    if not settings_path.exists():
        return {}
    try:
        payload = json.loads(settings_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Workspace settings file is not valid JSON: {settings_path}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"Workspace settings file must contain a JSON object: {settings_path}")
    return payload


def _write_workspace_settings(workspace_root: str, settings: dict[str, Any]) -> Path:
    settings_path = _workspace_settings_path(workspace_root)
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(json.dumps(settings, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return settings_path


def _workspace_editor_config_status(workspace_root: str) -> dict[str, Any]:
    settings_path = _workspace_settings_path(workspace_root)
    exists = settings_path.exists()
    settings = _read_workspace_settings(workspace_root) if exists else {}
    associations = settings.get("workbench.editorAssociations", {})
    if associations is not None and not isinstance(associations, dict):
        raise RuntimeError(
            "Workspace setting 'workbench.editorAssociations' must be a JSON object in "
            f"{settings_path}"
        )
    association_value = associations.get("*.ipynb") if isinstance(associations, dict) else None
    return {
        "settings_path": str(settings_path),
        "exists": exists,
        "association": association_value,
        "default_canvas_configured": association_value == "agent-repl.canvasEditor",
    }


def _configure_workspace_editor_defaults(workspace_root: str) -> dict[str, Any]:
    settings = _read_workspace_settings(workspace_root)
    associations_raw = settings.get("workbench.editorAssociations")
    if associations_raw is None:
        associations: dict[str, Any] = {}
    elif isinstance(associations_raw, dict):
        associations = dict(associations_raw)
    else:
        raise RuntimeError(
            "Workspace setting 'workbench.editorAssociations' must be a JSON object before "
            "Agent REPL can update it."
        )
    previous = associations.get("*.ipynb")
    associations["*.ipynb"] = "agent-repl.canvasEditor"
    settings["workbench.editorAssociations"] = associations
    settings_path = _write_workspace_settings(workspace_root, settings)
    return {
        "status": "ok",
        "settings_path": str(settings_path),
        "previous_association": previous,
        "association": associations["*.ipynb"],
        "changed": previous != associations["*.ipynb"],
    }


def _workspace_python_candidates(workspace_root: str) -> list[str]:
    root = Path(workspace_root)
    candidates = [
        root / ".venv" / "bin" / "python",
        root / ".venv" / "Scripts" / "python.exe",
    ]
    return [str(candidate) for candidate in candidates if candidate.exists()]


def _probe_kernel_capability(python_path: str) -> dict[str, Any]:
    result = subprocess.run(
        [python_path, "-c", "import ipykernel"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return {
        "python_path": python_path,
        "kernel_capable": result.returncode == 0,
        "install_hint": (
            f"{python_path} -m pip install ipykernel"
            if result.returncode != 0
            else None
        ),
    }


def _detect_cli_executable() -> str | None:
    executable = shutil.which("agent-repl")
    if executable:
        return os.path.realpath(executable)
    argv0 = sys.argv[0] if sys.argv else ""
    if argv0:
        return os.path.realpath(argv0)
    return None


def _detect_install_method() -> str:
    module_path = str(Path(__file__).resolve()).lower()
    executable = (_detect_cli_executable() or "").lower()
    combined = f"{module_path} {executable}"
    if "pipx" in combined:
        return "pipx"
    if "/uv/" in combined or "\\uv\\" in combined:
        return "uv"
    if sys.prefix != getattr(sys, "base_prefix", sys.prefix):
        return "pip"
    return "unknown"


def _detect_editor_clis() -> dict[str, Any]:
    return {
        name: {"available": bool(path), "path": path}
        for name, path in {
            "vscode": shutil.which("code"),
            "cursor": shutil.which("cursor"),
            "windsurf": shutil.which("windsurf"),
        }.items()
    }


def _detect_installed_extensions() -> dict[str, Any]:
    homes = {
        "vscode": Path.home() / ".vscode" / "extensions",
        "cursor": Path.home() / ".cursor" / "extensions",
        "windsurf": Path.home() / ".windsurf" / "extensions",
    }
    result: dict[str, Any] = {}
    for editor, root in homes.items():
        matches: list[str] = []
        if root.exists():
            for pattern in ("giladrubin.agent-repl-*", "GiladRubin.agent-repl-*"):
                matches.extend(str(path) for path in sorted(root.glob(pattern)))
        result[editor] = {"extensions_root": str(root), "installed": matches}
    return result


def _doctor_payload(
    *,
    workspace_root: str,
    runtime_dir: str | None = None,
    probe_mcp: bool = False,
) -> dict[str, Any]:
    python_candidates = _workspace_python_candidates(workspace_root)
    kernel_probe = _probe_kernel_capability(python_candidates[0]) if python_candidates else None
    editor_config = _workspace_editor_config_status(workspace_root)
    cli_executable = _detect_cli_executable()

    checks = [
        {
            "name": "cli-executable",
            "status": "ok" if cli_executable else "warn",
            "detail": cli_executable or "agent-repl executable not detected on PATH",
        },
        {
            "name": "workspace-venv",
            "status": "ok" if python_candidates else "warn",
            "detail": python_candidates[0] if python_candidates else "No workspace .venv detected",
        },
        {
            "name": "workspace-kernel",
            "status": (
                "skip"
                if not python_candidates
                else ("ok" if kernel_probe and kernel_probe["kernel_capable"] else "warn")
            ),
            "detail": (
                "No workspace .venv detected"
                if not python_candidates
                else (
                    python_candidates[0]
                    if kernel_probe and kernel_probe["kernel_capable"]
                    else (kernel_probe or {}).get("install_hint")
                )
            ),
        },
        {
            "name": "workspace-canvas-default",
            "status": "ok" if editor_config["default_canvas_configured"] else "warn",
            "detail": editor_config["settings_path"],
        },
    ]

    payload: dict[str, Any] = {
        "status": "ok",
        "workspace_root": workspace_root,
        "cli": {
            "version": _app_version(),
            "executable": cli_executable,
            "python_executable": sys.executable,
        },
        "install": {
            "method": _detect_install_method(),
            "available_installers": {
                "uv": bool(shutil.which("uv")),
                "pipx": bool(shutil.which("pipx")),
                "pip": True,
            },
        },
        "workspace": {
            "python_candidates": python_candidates,
            "recommended_python": python_candidates[0] if python_candidates else None,
            "kernel_probe": kernel_probe,
        },
        "editor": {
            "workspace": editor_config,
            "clis": _detect_editor_clis(),
            "installed_extensions": _detect_installed_extensions(),
        },
        "checks": checks,
        "recommendations": [],
    }

    if not python_candidates:
        payload["recommendations"].append(
            "Create a workspace .venv or pass --kernel <python-path> when creating notebooks."
        )
    elif kernel_probe and not kernel_probe["kernel_capable"]:
        payload["recommendations"].append(
            f"Install ipykernel into the workspace environment with `{kernel_probe['install_hint']}`."
        )
    if not editor_config["default_canvas_configured"]:
        payload["recommendations"].append(
            "Run `agent-repl editor configure --default-canvas` to open *.ipynb files in the Agent REPL canvas by default for this workspace."
        )

    if probe_mcp:
        mcp = _mcp_connection_payload(workspace_root=workspace_root, runtime_dir=runtime_dir)
        payload["mcp"] = mcp["mcp"]
        payload["checks"].append({
            "name": "mcp-endpoint",
            "status": "ok",
            "detail": mcp["mcp"]["url"],
        })

    if any(check["status"] == "warn" for check in payload["checks"]):
        payload["status"] = "warn"
    return payload


def _default_smoke_test_path() -> str:
    return f"tmp/agent-repl-smoke-{os.getpid()}.ipynb"


def _run_notebook_smoke_test(
    *,
    workspace_root: str,
    path: str,
    runtime_dir: str | None = None,
    kernel_id: str | None = None,
) -> dict[str, Any]:
    client = _core_client(workspace_root, runtime_dir=runtime_dir)
    create = client.notebook_create(path, kernel_id=kernel_id)
    execute = call_with_owner_session(
        client,
        client.notebook_insert_execute,
        path,
        'print("agent-repl is working")',
        timeout=30,
        client_type="cli",
        label="Setup",
    )
    return {
        "status": "ok",
        "path": path,
        "create": create,
        "execute": execute,
    }


def _mcp_server_config(*, server_name: str, url: str, token: str) -> dict[str, Any]:
    return {
        "mcpServers": {
            server_name: {
                "transport": "streamable-http",
                "url": url,
                "headers": {
                    "Authorization": f"token {token}",
                },
            }
        }
    }


def _mcp_connection_payload(
    *,
    workspace_root: str,
    runtime_dir: str | None = None,
    server_name: str = "agent-repl",
) -> dict[str, Any]:
    start_result = CoreClient.start(workspace_root, runtime_dir=runtime_dir)
    client = CoreClient.discover(workspace_hint=workspace_root, runtime_dir=runtime_dir)
    canonical_url = f"{client.base_url}/mcp"
    return {
        "status": "ok",
        "workspace_root": start_result["workspace_root"],
        "already_running": start_result.get("already_running", False),
        "daemon": {
            key: value
            for key, value in start_result.items()
            if key
            in {
                "mode",
                "pid",
                "started_at",
                "version",
                "documents",
                "sessions",
                "runs",
                "runtime_dir",
                "capabilities",
            }
        },
        "mcp": {
            "transport": "streamable-http",
            "url": canonical_url,
            "legacy_url": f"{client.base_url}/mcp/mcp",
            "authorization_header": f"token {client.token}",
        },
        "config": _mcp_server_config(
            server_name=server_name,
            url=canonical_url,
            token=client.token,
        ),
    }


def _mcp_token_auth(token: str):
    import httpx

    class TokenAuth(httpx.Auth):
        def __init__(self, inner_token: str):
            self._token = inner_token

        def auth_flow(self, request):
            request.headers["Authorization"] = f"token {self._token}"
            yield request

    return TokenAuth(token)


async def _run_mcp_smoke_test(url: str, token: str) -> list[dict[str, Any]]:
    import json as _json

    from fastmcp import Client as FastMcpClient

    checks: list[dict[str, Any]] = []
    async with FastMcpClient(url, auth=_mcp_token_auth(token)) as client:
        tools = await client.list_tools()
        checks.append({
            "name": "list-tools",
            "status": "ok",
            "tool_count": len(tools),
        })

        resources = await client.list_resources()
        checks.append({
            "name": "list-resources",
            "status": "ok",
            "resource_count": len(resources),
        })

        status_resource = await client.read_resource("agent-repl://status")
        status_payload = _json.loads(status_resource[0].text)
        checks.append({
            "name": "read-status-resource",
            "status": "ok",
            "workspace_root": status_payload.get("workspace_root"),
        })

    return checks


def _mcp_smoke_test_payload(
    *,
    workspace_root: str,
    runtime_dir: str | None = None,
) -> dict[str, Any]:
    connection = _mcp_connection_payload(workspace_root=workspace_root, runtime_dir=runtime_dir)
    checks = [
        {
            "name": "core-status",
            "status": "ok",
            "documents": connection["daemon"].get("documents"),
            "sessions": connection["daemon"].get("sessions"),
            "runs": connection["daemon"].get("runs"),
        },
        *asyncio.run(
            _run_mcp_smoke_test(
                connection["mcp"]["url"],
                connection["mcp"]["authorization_header"].removeprefix("token "),
            )
        ),
    ]
    return {
        "status": "ok",
        "workspace_root": connection["workspace_root"],
        "mcp": connection["mcp"],
        "checks": checks,
    }

# ------------------------------------------------------------------
# Subcommand handlers
# ------------------------------------------------------------------

def cmd_cat(args: argparse.Namespace) -> int:
    client = _notebook_client(args.path)
    result = client.notebook_contents(args.path)
    include_outputs = not getattr(args, "no_outputs", False)
    # Clean up cells — only show what the agent needs
    clean_cells = []
    for c in result.get("cells", []):
        clean: dict[str, Any] = {
            "index": c["index"],
            "cell_id": c["cell_id"],
            "cell_type": c["cell_type"],
            "source": c["source"],
        }
        if c.get("display_number") is not None:
            clean["display_number"] = c["display_number"]
        if include_outputs:
            if c.get("execution_count") is not None:
                clean["execution_count"] = c["execution_count"]
            if c.get("outputs"):
                clean["outputs"] = c["outputs"]
        # Show prompt status if it's a prompt cell
        ar = (c.get("metadata") or {}).get("custom", {}).get("agent-repl", {})
        if ar.get("type"):
            clean["agent_repl"] = {k: v for k, v in ar.items() if k != "cell_id"}
        clean_cells.append(clean)
    _out({"path": result["path"], "cells": clean_cells}, args.pretty)
    return 0


def cmd_reload(args: argparse.Namespace) -> int:
    try:
        result = _client().reload()
    except Exception:
        # Bridge unreachable (port changed after VS Code reload) — re-discover
        client = BridgeClient.discover()
        result = client.reload()
    _out(result, args.pretty)
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    client = _notebook_client(args.path)
    result = client.notebook_status(args.path)
    _out(result, args.pretty)
    return 0


def cmd_edit(args: argparse.Namespace) -> int:
    client = _notebook_client(args.path)
    ops: list[dict[str, Any]] = []

    if args.edit_command == "replace-source":
        op: dict[str, Any] = {"op": args.edit_command}
        op["source"] = _read_source(args)
        if args.cell_id:
            op["cell_id"] = args.cell_id
        if args.index is not None:
            op["cell_index"] = args.index
        ops.append(op)
    elif args.edit_command == "insert":
        cells = _read_cells(args, default_cell_type=getattr(args, "cell_type", "code"))
        ops = _build_insert_ops(cells, at_index=args.at_index)
    elif args.edit_command == "delete":
        op = {"op": args.edit_command}
        if args.cell_id:
            op["cell_id"] = args.cell_id
        if args.index is not None:
            op["cell_index"] = args.index
        ops.append(op)
    elif args.edit_command == "move":
        op = {"op": args.edit_command}
        if args.cell_id:
            op["cell_id"] = args.cell_id
        if args.index is not None:
            op["cell_index"] = args.index
        op["to_index"] = args.to_index
        ops.append(op)
    elif args.edit_command == "clear-outputs":
        op = {"op": args.edit_command}
        if getattr(args, "all", False):
            op["all"] = True
        elif args.cell_id:
            op["cell_id"] = args.cell_id
        elif args.index is not None:
            op["cell_index"] = args.index
        ops.append(op)

    result = call_with_owner_session(
        client,
        client.notebook_edit,
        args.path,
        ops,
        explicit_session_id=getattr(args, "session_id", None),
    )
    _out(result, args.pretty)
    return 0


def cmd_exec(args: argparse.Namespace) -> int:
    client = _notebook_client(args.path)
    wait = not getattr(args, "no_wait", False)
    timeout = getattr(args, "timeout", 30)
    if args.code:
        result = call_with_owner_session(
            client,
            client.notebook_insert_execute,
            args.path,
            args.code,
            explicit_session_id=getattr(args, "session_id", None),
            cell_type="code",
            wait=wait,
            timeout=timeout,
        )
    elif args.cell_id:
        result = call_with_owner_session(
            client,
            client.notebook_execute_cell,
            args.path,
            explicit_session_id=getattr(args, "session_id", None),
            cell_id=args.cell_id,
            wait=wait,
            timeout=timeout,
        )
    else:
        print(json.dumps({"error": "Provide --cell-id or -c/--code"}, indent=2), file=sys.stderr)
        return 1
    _out(result, args.pretty)
    return 0


def cmd_ix(args: argparse.Namespace) -> int:
    wait = not getattr(args, "no_wait", False)
    timeout = getattr(args, "timeout", 30)
    at_index = getattr(args, "at_index", -1)
    client = _notebook_client(args.path)
    cells = _read_cells(args, default_cell_type="code")
    explicit_session_id = getattr(args, "session_id", None)
    if len(cells) == 1 and not _has_cells_payload(args):
        source = cells[0]["source"]
        result = call_with_owner_session(
            client,
            client.notebook_insert_execute,
            args.path,
            source,
            explicit_session_id=explicit_session_id,
            at_index=at_index,
            cell_type="code",
            wait=wait,
            timeout=timeout,
        )
        _out(result, args.pretty)
        return 0

    if getattr(args, "no_wait", False):
        raise SystemExit("Error: batch ix does not support --no-wait")

    session_id = resolve_owner_session_id(client, explicit_session_id=explicit_session_id)
    results: list[dict[str, Any]] = []
    current_index = at_index
    stopped_on_error = False

    for cell in cells:
        cell_type = cell["cell_type"]
        source = cell["source"]
        if cell_type == "code":
            kwargs = {"at_index": current_index, "cell_type": "code", "wait": wait, "timeout": timeout}
            if session_id:
                kwargs["owner_session_id"] = session_id
            item_result = client.notebook_insert_execute(args.path, source, **kwargs)
            results.append({**item_result, "cell_type": cell_type})
            if item_result.get("status") == "error":
                stopped_on_error = True
                break
        else:
            op = _build_insert_ops([cell], at_index=current_index)[0]
            if session_id:
                edit_result = client.notebook_edit(args.path, [op], owner_session_id=session_id)
            else:
                edit_result = client.notebook_edit(args.path, [op])
            entry = dict(edit_result.get("results", [{}])[0])
            entry["cell_type"] = cell_type
            results.append(entry)

        if current_index != -1:
            current_index += 1

    result: dict[str, Any] = {
        "status": "error" if stopped_on_error else "ok",
        "path": args.path,
        "results": results,
        "operation": "batch-insert-execute",
    }
    if stopped_on_error:
        failed = results[-1]
        result["stopped_on_error"] = True
        if failed.get("cell_id") is not None:
            result["failed_cell_id"] = failed["cell_id"]
    _out(result, args.pretty)
    return 0


def cmd_run_all(args: argparse.Namespace) -> int:
    client = _notebook_client(args.path)
    result = call_with_owner_session(
        client,
        client.notebook_execute_all,
        args.path,
        explicit_session_id=getattr(args, "session_id", None),
    )
    _out(result, args.pretty)
    return 0


def cmd_restart(args: argparse.Namespace) -> int:
    client = _notebook_client(args.path)
    result = client.notebook_restart(args.path)
    _out(result, args.pretty)
    return 0


def cmd_restart_run_all(args: argparse.Namespace) -> int:
    client = _notebook_client(args.path)
    result = call_with_owner_session(
        client,
        client.notebook_restart_and_run_all,
        args.path,
        explicit_session_id=getattr(args, "session_id", None),
    )
    _out(result, args.pretty)
    return 0


def cmd_new(args: argparse.Namespace) -> int:
    cells = None
    if args.cells_json:
        cells = json.loads(args.cells_json)
    kernel_id = getattr(args, "kernel", None)
    client = _notebook_client(args.path)
    result = client.notebook_create(args.path, cells=cells, kernel_id=kernel_id)
    if getattr(args, "open", False):
        result["open"] = _client(args.path).open(
            args.path,
            editor=getattr(args, "editor", "canvas"),
            target=getattr(args, "target", "vscode"),
            browser_url=getattr(args, "browser_url", None),
        )
    _out(result, args.pretty)
    return 0


def cmd_open(args: argparse.Namespace) -> int:
    result = _client(args.path).open(
        args.path,
        editor=getattr(args, "editor", "canvas"),
        target=getattr(args, "target", "vscode"),
        browser_url=getattr(args, "browser_url", None),
    )
    _out(result, args.pretty)
    return 0


def _find_extension_root() -> Path:
    """Locate the extension/ directory relative to the installed package."""
    # In dev: repo_root/extension/
    repo_root = Path(__file__).resolve().parents[2]
    candidate = repo_root / "extension"
    if (candidate / "scripts" / "preview-webview.mjs").exists():
        return candidate
    raise FileNotFoundError(
        "Cannot find extension/scripts/preview-webview.mjs — "
        "browse requires a source checkout of agent-repl"
    )


STANDALONE_PREVIEW_PROTOCOL_VERSION = "standalone-preview-v1"
STANDALONE_PREVIEW_REQUIRED_ROUTES = {
    "/api/standalone/health",
    "/api/standalone/workspace-tree",
    "/api/standalone/notebook/contents",
    "/api/standalone/notebook/status",
    "/api/standalone/notebook/runtime",
    "/api/standalone/notebook/execute-cell-async",
}


def _preview_server_health(host: str, port: int) -> dict[str, Any] | None:
    import urllib.request

    try:
        with urllib.request.urlopen(f"http://{host}:{port}/api/standalone/health", timeout=1) as response:
            payload = json.loads(response.read().decode("utf-8") or "{}")
            return payload if isinstance(payload, dict) else None
    except Exception:
        return None


def _preview_server_is_compatible(health: dict[str, Any] | None, *, workspace_root: str) -> bool:
    if not isinstance(health, dict):
        return False
    if health.get("protocol_version") != STANDALONE_PREVIEW_PROTOCOL_VERSION:
        return False
    reported_root = health.get("workspace_root")
    if not isinstance(reported_root, str) or os.path.realpath(reported_root) != workspace_root:
        return False
    api_routes = health.get("api_routes")
    if not isinstance(api_routes, list):
        return False
    return STANDALONE_PREVIEW_REQUIRED_ROUTES.issubset({route for route in api_routes if isinstance(route, str)})


def _port_in_use(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.2)
        return sock.connect_ex((host, port)) == 0


def _find_available_preview_port(host: str, start_port: int) -> int:
    for candidate in range(start_port, start_port + 50):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind((host, candidate))
            except OSError:
                continue
            return candidate
    raise RuntimeError(f"No free preview port found starting at {start_port}")


def _wait_for_preview_server(host: str, port: int, *, workspace_root: str, timeout_seconds: float = 6.0) -> bool:
    import time

    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if _preview_server_is_compatible(_preview_server_health(host, port), workspace_root=workspace_root):
            return True
        time.sleep(0.2)
    return False


def cmd_browse(args: argparse.Namespace) -> int:
    import subprocess
    import webbrowser

    host = "127.0.0.1"
    requested_port = int(getattr(args, "port", None) or os.environ.get("AGENT_REPL_PREVIEW_PORT", 4173))
    workspace_root = os.path.realpath(os.getcwd())
    existing_health = _preview_server_health(host, requested_port)

    if _preview_server_is_compatible(existing_health, workspace_root=workspace_root):
        url = f"http://{host}:{requested_port}/preview.html"
        webbrowser.open(url)
        _out({"status": "ok", "url": url, "server": "already_running"}, args.pretty)
        return 0

    extension_root = _find_extension_root()
    launch_port = requested_port
    warning = None
    if existing_health is not None or _port_in_use(host, requested_port):
        launch_port = _find_available_preview_port(host, requested_port + 1)
        warning = (
            f"Preview port {requested_port} is already serving an incompatible or stale server; "
            f"started a fresh preview on port {launch_port} instead."
        )

    env = {**os.environ, "AGENT_REPL_PREVIEW_PORT": str(launch_port), "AGENT_REPL_STANDALONE_WORKSPACE": workspace_root}
    proc = subprocess.Popen(
        ["node", "scripts/preview-webview.mjs"],
        cwd=str(extension_root),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    if proc.poll() is not None:
        print(f"Preview server exited with code {proc.returncode}", file=sys.stderr)
        return 1
    if not _wait_for_preview_server(host, launch_port, workspace_root=workspace_root):
        proc.terminate()
        print(json.dumps({
            "error": "Preview server did not become healthy in time",
            "recovery": {
                "reason": "preview-start-timeout",
                "summary": "The browser preview server did not finish booting or failed its health checks.",
                "suggestions": [
                    "Run `cd extension && npm run preview:webview` and inspect the server logs.",
                    "If another process is already bound to the requested port, try again with `agent-repl browse --port <fresh-port>`.",
                ],
            },
        }, indent=2), file=sys.stderr)
        return 1

    url = f"http://{host}:{launch_port}/preview.html"
    webbrowser.open(url)
    payload: dict[str, Any] = {"status": "ok", "url": url, "pid": proc.pid, "port": launch_port}
    if warning:
        payload["warning"] = warning
    _out(payload, args.pretty)
    return 0


def cmd_kernels(args: argparse.Namespace) -> int:
    result = _client().kernels()
    _out(result, args.pretty)
    return 0


def cmd_select_kernel(args: argparse.Namespace) -> int:
    kernel_id = getattr(args, "kernel_id", None)
    extension = getattr(args, "extension", None)
    interactive = getattr(args, "interactive", False)
    # Route through core runtime for headless kernel selection
    if not interactive:
        client = _notebook_client(args.path)
        result = client.notebook_select_kernel(args.path, kernel_id=kernel_id)
        _out(result, args.pretty)
        return 0
    # Fall back to bridge for interactive picker
    result = _client(args.path).select_kernel(
        args.path,
        kernel_id=kernel_id,
        extension=extension,
        interactive=interactive,
    )
    _out(result, args.pretty)
    return 0


def cmd_prompts(args: argparse.Namespace) -> int:
    client = _notebook_client(args.path)
    result = client.notebook_contents(args.path)
    cells = result.get("cells", [])
    prompts = [
        c for c in cells
        if (c.get("metadata") or {}).get("custom", {}).get("agent-repl", {}).get("type") == "prompt"
    ]
    _out({"prompts": prompts}, args.pretty)
    return 0


def cmd_respond(args: argparse.Namespace) -> int:
    client = _client(args.path)
    source = _read_source(args)
    # Mark prompt as in-progress
    client.prompt_status(args.path, args.to, "in-progress")
    # Insert response cell and execute
    result = client.insert_and_execute(args.path, source)
    # Mark prompt as answered
    client.prompt_status(args.path, args.to, "answered")
    _out(result, args.pretty)
    return 0


def cmd_core(args: argparse.Namespace) -> int:
    workspace_root = os.path.realpath(getattr(args, "workspace_root", None) or os.getcwd())
    runtime_dir = getattr(args, "runtime_dir", None)

    if args.core_command == "start":
        result = CoreClient.start(
            workspace_root,
            timeout=getattr(args, "timeout", DEFAULT_START_TIMEOUT),
            runtime_dir=runtime_dir,
        )
        _out(result, args.pretty)
        return 0

    if args.core_command == "attach":
        result = CoreClient.attach(
            workspace_root,
            actor=args.actor,
            client=args.client_type,
            label=getattr(args, "label", None),
            capabilities=getattr(args, "capability", None),
            session_id=getattr(args, "session_id", None),
            timeout=getattr(args, "timeout", DEFAULT_START_TIMEOUT),
            runtime_dir=runtime_dir,
        )
        _out(result, args.pretty)
        return 0

    if args.core_command == "status":
        result = _core_client_raw(workspace_root, runtime_dir=runtime_dir).status()
        _out(result, args.pretty)
        return 0

    if args.core_command == "stop":
        result = _core_client_raw(workspace_root, runtime_dir=runtime_dir).shutdown()
        _out(result, args.pretty)
        return 0

    if args.core_command == "sessions":
        result = _core_client(workspace_root, runtime_dir=runtime_dir).list_sessions()
        _out(result, args.pretty)
        return 0

    if args.core_command == "session-start":
        result = _core_client(workspace_root, runtime_dir=runtime_dir).start_session(
            actor=args.actor,
            client=args.client_type,
            label=getattr(args, "label", None),
            capabilities=getattr(args, "capability", None),
            session_id=getattr(args, "session_id", None),
        )
        _out(result, args.pretty)
        return 0

    if args.core_command == "session-resolve":
        result = _core_client(workspace_root, runtime_dir=runtime_dir).resolve_preferred_session(
            actor=args.actor,
        )
        _out(result, args.pretty)
        return 0

    if args.core_command == "session-touch":
        result = _core_client(workspace_root, runtime_dir=runtime_dir).touch_session(args.session_id)
        _out(result, args.pretty)
        return 0

    if args.core_command == "session-detach":
        result = _core_client(workspace_root, runtime_dir=runtime_dir).detach_session(args.session_id)
        _out(result, args.pretty)
        return 0

    if args.core_command == "session-presence-upsert":
        result = _core_client(workspace_root, runtime_dir=runtime_dir).session_presence_upsert(
            args.session_id,
            path=args.path,
            activity=args.activity,
            cell_id=getattr(args, "cell_id", None),
            cell_index=getattr(args, "cell_index", None),
        )
        _out(result, args.pretty)
        return 0

    if args.core_command == "session-presence-clear":
        result = _core_client(workspace_root, runtime_dir=runtime_dir).session_presence_clear(
            args.session_id,
            path=getattr(args, "path", None),
        )
        _out(result, args.pretty)
        return 0

    if args.core_command == "session-end":
        result = _core_client(workspace_root, runtime_dir=runtime_dir).end_session(args.session_id)
        _out(result, args.pretty)
        return 0

    if args.core_command == "documents":
        result = _core_client(workspace_root, runtime_dir=runtime_dir).list_documents()
        _out(result, args.pretty)
        return 0

    if args.core_command == "document-open":
        result = _core_client(workspace_root, runtime_dir=runtime_dir).open_document(args.path)
        _out(result, args.pretty)
        return 0

    if args.core_command == "document-refresh":
        result = _core_client(workspace_root, runtime_dir=runtime_dir).refresh_document(args.document_id)
        _out(result, args.pretty)
        return 0

    if args.core_command == "document-rebind":
        result = _core_client(workspace_root, runtime_dir=runtime_dir).rebind_document(args.document_id)
        _out(result, args.pretty)
        return 0

    if args.core_command == "notebook-runtime":
        result = _core_client(workspace_root, runtime_dir=runtime_dir).notebook_runtime(args.path)
        _out(result, args.pretty)
        return 0

    if args.core_command == "notebook-projection":
        result = _core_client(workspace_root, runtime_dir=runtime_dir).notebook_projection(args.path)
        _out(result, args.pretty)
        return 0

    if args.core_command == "notebook-activity":
        result = _core_client(workspace_root, runtime_dir=runtime_dir).notebook_activity(
            args.path,
            since=getattr(args, "since", None),
        )
        _out(result, args.pretty)
        return 0

    if args.core_command == "project-visible-notebook":
        client = _core_client(workspace_root, runtime_dir=runtime_dir)
        result = call_with_owner_session(
            client,
            client.notebook_project_visible,
            args.path,
            explicit_session_id=getattr(args, "session_id", None),
            cells=_read_json_payload(args, field_name="cells"),
        )
        _out(result, args.pretty)
        return 0

    if args.core_command == "execute-visible-cell":
        client = _core_client(workspace_root, runtime_dir=runtime_dir)
        result = call_with_owner_session(
            client,
            client.notebook_execute_visible_cell,
            args.path,
            explicit_session_id=getattr(args, "session_id", None),
            cell_index=args.cell_index,
            source=_read_source(args),
        )
        _out(result, args.pretty)
        return 0

    if args.core_command == "cell-lease-acquire":
        result = _core_client(workspace_root, runtime_dir=runtime_dir).acquire_cell_lease(
            args.path,
            session_id=args.session_id,
            cell_id=getattr(args, "cell_id", None),
            cell_index=getattr(args, "cell_index", None),
            kind=getattr(args, "kind", "edit"),
            ttl_seconds=getattr(args, "ttl_seconds", None),
        )
        _out(result, args.pretty)
        return 0

    if args.core_command == "cell-lease-release":
        result = _core_client(workspace_root, runtime_dir=runtime_dir).release_cell_lease(
            args.path,
            session_id=args.session_id,
            cell_id=getattr(args, "cell_id", None),
            cell_index=getattr(args, "cell_index", None),
        )
        _out(result, args.pretty)
        return 0

    if args.core_command == "branches":
        result = _core_client(workspace_root, runtime_dir=runtime_dir).list_branches()
        _out(result, args.pretty)
        return 0

    if args.core_command == "branch-start":
        result = _core_client(workspace_root, runtime_dir=runtime_dir).start_branch(
            document_id=args.document_id,
            owner_session_id=getattr(args, "owner_session_id", None),
            parent_branch_id=getattr(args, "parent_branch_id", None),
            title=getattr(args, "title", None),
            purpose=getattr(args, "purpose", None),
            branch_id=getattr(args, "branch_id", None),
        )
        _out(result, args.pretty)
        return 0

    if args.core_command == "branch-finish":
        result = _core_client(workspace_root, runtime_dir=runtime_dir).finish_branch(
            args.branch_id,
            status=args.status_value,
        )
        _out(result, args.pretty)
        return 0

    if args.core_command == "branch-review-request":
        result = _core_client(workspace_root, runtime_dir=runtime_dir).request_branch_review(
            args.branch_id,
            requested_by_session_id=args.requested_by_session_id,
            note=getattr(args, "note", None),
        )
        _out(result, args.pretty)
        return 0

    if args.core_command == "branch-review-resolve":
        result = _core_client(workspace_root, runtime_dir=runtime_dir).resolve_branch_review(
            args.branch_id,
            resolved_by_session_id=args.resolved_by_session_id,
            resolution=args.resolution,
            note=getattr(args, "note", None),
        )
        _out(result, args.pretty)
        return 0

    if args.core_command == "runtimes":
        result = _core_client(workspace_root, runtime_dir=runtime_dir).list_runtimes()
        _out(result, args.pretty)
        return 0

    if args.core_command == "runtime-start":
        result = _core_client(workspace_root, runtime_dir=runtime_dir).start_runtime(
            mode=args.mode,
            label=getattr(args, "label", None),
            runtime_id=getattr(args, "runtime_id", None),
            environment=getattr(args, "environment", None),
            document_path=getattr(args, "document_path", None),
            ttl_seconds=getattr(args, "ttl_seconds", None),
        )
        _out(result, args.pretty)
        return 0

    if args.core_command == "runtime-stop":
        result = _core_client(workspace_root, runtime_dir=runtime_dir).stop_runtime(args.runtime_id)
        _out(result, args.pretty)
        return 0

    if args.core_command == "runtime-recover":
        result = _core_client(workspace_root, runtime_dir=runtime_dir).recover_runtime(args.runtime_id)
        _out(result, args.pretty)
        return 0

    if args.core_command == "runtime-promote":
        result = _core_client(workspace_root, runtime_dir=runtime_dir).promote_runtime(
            args.runtime_id,
            mode=args.mode,
        )
        _out(result, args.pretty)
        return 0

    if args.core_command == "runtime-discard":
        result = _core_client(workspace_root, runtime_dir=runtime_dir).discard_runtime(args.runtime_id)
        _out(result, args.pretty)
        return 0

    if args.core_command == "runs":
        result = _core_client(workspace_root, runtime_dir=runtime_dir).list_runs()
        _out(result, args.pretty)
        return 0

    if args.core_command == "run-start":
        result = _core_client(workspace_root, runtime_dir=runtime_dir).start_run(
            runtime_id=args.runtime_id,
            target_type=args.target_type,
            target_ref=args.target_ref,
            kind=args.kind,
            run_id=getattr(args, "run_id", None),
        )
        _out(result, args.pretty)
        return 0

    if args.core_command == "run-finish":
        result = _core_client(workspace_root, runtime_dir=runtime_dir).finish_run(
            args.run_id,
            status=args.status_value,
        )
        _out(result, args.pretty)
        return 0

    if args.core_command == "serve":
        serve_forever(workspace_root, runtime_dir=args.runtime_dir)
        return 0

    raise RuntimeError("Unknown core command")


def cmd_mcp(args: argparse.Namespace) -> int:
    workspace_root = os.path.realpath(getattr(args, "workspace_root", None) or os.getcwd())
    runtime_dir = getattr(args, "runtime_dir", None)

    if args.mcp_command == "setup":
        result = _mcp_connection_payload(
            workspace_root=workspace_root,
            runtime_dir=runtime_dir,
            server_name=args.server_name,
        )
        _out(result, args.pretty)
        return 0

    if args.mcp_command == "status":
        result = _mcp_connection_payload(
            workspace_root=workspace_root,
            runtime_dir=runtime_dir,
        )
        result.pop("config", None)
        _out(result, args.pretty)
        return 0

    if args.mcp_command == "config":
        result = _mcp_connection_payload(
            workspace_root=workspace_root,
            runtime_dir=runtime_dir,
            server_name=args.server_name,
        )
        _out(result["config"], args.pretty)
        return 0

    if args.mcp_command == "smoke-test":
        result = _mcp_smoke_test_payload(
            workspace_root=workspace_root,
            runtime_dir=runtime_dir,
        )
        _out(result, args.pretty)
        return 0

    raise RuntimeError("Unknown mcp command")


def cmd_doctor(args: argparse.Namespace) -> int:
    workspace_root = _workspace_root_from_arg(getattr(args, "workspace_root", None))
    payload = _doctor_payload(
        workspace_root=workspace_root,
        runtime_dir=getattr(args, "runtime_dir", None),
        probe_mcp=getattr(args, "probe_mcp", False),
    )
    if getattr(args, "smoke_test", False):
        smoke_path = getattr(args, "smoke_test_path", None) or _default_smoke_test_path()
        payload["smoke_test"] = _run_notebook_smoke_test(
            workspace_root=workspace_root,
            path=smoke_path,
            runtime_dir=getattr(args, "runtime_dir", None),
        )
    _out(payload, args.pretty)
    return 0


def cmd_setup(args: argparse.Namespace) -> int:
    workspace_root = _workspace_root_from_arg(getattr(args, "workspace_root", None))
    payload: dict[str, Any] = {
        "status": "ok",
        "workspace_root": workspace_root,
        "actions": [],
    }

    if getattr(args, "configure_editor_default", False):
        payload["actions"].append({
            "name": "editor-configure",
            "result": _configure_workspace_editor_defaults(workspace_root),
        })

    if getattr(args, "with_mcp", False):
        payload["actions"].append({
            "name": "mcp-setup",
            "result": _mcp_connection_payload(
                workspace_root=workspace_root,
                runtime_dir=getattr(args, "runtime_dir", None),
                server_name=args.server_name,
            ),
        })
        if getattr(args, "mcp_smoke_test", True):
            payload["actions"].append({
                "name": "mcp-smoke-test",
                "result": _mcp_smoke_test_payload(
                    workspace_root=workspace_root,
                    runtime_dir=getattr(args, "runtime_dir", None),
                ),
            })

    if getattr(args, "smoke_test", False):
        smoke_path = getattr(args, "smoke_test_path", None) or _default_smoke_test_path()
        payload["actions"].append({
            "name": "notebook-smoke-test",
            "result": _run_notebook_smoke_test(
                workspace_root=workspace_root,
                path=smoke_path,
                runtime_dir=getattr(args, "runtime_dir", None),
            ),
        })

    payload["doctor"] = _doctor_payload(
        workspace_root=workspace_root,
        runtime_dir=getattr(args, "runtime_dir", None),
        probe_mcp=getattr(args, "with_mcp", False),
    )
    payload["recommendations"] = list(payload["doctor"].get("recommendations", []))
    _out(payload, args.pretty)
    return 0


def cmd_editor(args: argparse.Namespace) -> int:
    workspace_root = _workspace_root_from_arg(getattr(args, "workspace_root", None))
    if args.editor_command == "configure":
        if not getattr(args, "default_canvas", False):
            raise SystemExit("Error: provide --default-canvas")
        result = _configure_workspace_editor_defaults(workspace_root)
        _out(result, args.pretty)
        return 0
    raise RuntimeError("Unknown editor command")


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _read_source(args: argparse.Namespace) -> str:
    src = getattr(args, "source", None)
    if src is not None:
        return src
    src_file = getattr(args, "source_file", None)
    if src_file:
        with open(src_file) as f:
            return f.read()
    # Read from stdin if neither provided
    if not sys.stdin.isatty():
        return sys.stdin.read()
    raise SystemExit("Error: provide --source/-s or pipe to stdin")


def _has_cells_payload(args: argparse.Namespace) -> bool:
    return getattr(args, "cells_json", None) is not None or getattr(args, "cells_file", None) is not None


def _read_cells(args: argparse.Namespace, *, default_cell_type: str) -> list[dict[str, str]]:
    if _has_cells_payload(args):
        if getattr(args, "source", None) is not None or getattr(args, "source_file", None) is not None:
            raise SystemExit("Error: provide either --source/--source-file or --cells-json/--cells-file, not both")
        cells = _read_json_payload(args, field_name="cells")
        normalized: list[dict[str, str]] = []
        for index, cell in enumerate(cells):
            source = cell.get("source")
            if not isinstance(source, str):
                raise SystemExit(f"Error: cell {index} is missing a string 'source'")
            raw_type = cell.get("cell_type", cell.get("type", default_cell_type))
            if not isinstance(raw_type, str) or not raw_type:
                raise SystemExit(f"Error: cell {index} is missing a string 'type' or 'cell_type'")
            normalized.append({"cell_type": raw_type, "source": source})
        return normalized
    return [{"cell_type": default_cell_type, "source": _read_source(args)}]


def _build_insert_ops(cells: list[dict[str, str]], *, at_index: int) -> list[dict[str, Any]]:
    ops: list[dict[str, Any]] = []
    current_index = at_index
    for cell in cells:
        ops.append({
            "op": "insert",
            "source": cell["source"],
            "cell_type": cell["cell_type"],
            "at_index": current_index,
        })
        if current_index != -1:
            current_index += 1
    return ops


def _read_json_payload(args: argparse.Namespace, *, field_name: str) -> list[dict[str, Any]]:
    inline = getattr(args, f"{field_name}_json", None)
    if inline is not None:
        payload = json.loads(inline)
    else:
        payload_file = getattr(args, f"{field_name}_file", None)
        if not payload_file:
            raise SystemExit(f"Error: provide --{field_name}-json or --{field_name}-file")
        with open(payload_file, encoding="utf-8") as handle:
            payload = json.load(handle)
    if not isinstance(payload, list):
        raise SystemExit(f"Error: --{field_name}-json / --{field_name}-file must contain a JSON array")
    return [item for item in payload if isinstance(item, dict)]


# ------------------------------------------------------------------
# Parser
# ------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agent-repl", description="Agent REPL bridge CLI")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output")
    parser.add_argument("--version", action="version", version=_app_version())
    sub = parser.add_subparsers(dest="command")

    # reload
    sub.add_parser("reload", help="Hot-reload installed extension routes")

    # cat
    p = sub.add_parser("cat", help="Read notebook contents")
    p.add_argument("path", help="Path to the .ipynb notebook file")
    p.add_argument("--no-outputs", action="store_true", help="Show cell sources only, without outputs")

    # status
    p = sub.add_parser("status", help="Notebook execution status")
    p.add_argument("path", help="Path to the .ipynb notebook file")

    # edit
    p = sub.add_parser("edit", help="Edit notebook cells")
    p.add_argument("path", help="Path to the .ipynb notebook file")
    p.add_argument("--session-id", help="Override session (default: reuses active human session)")
    esub = p.add_subparsers(dest="edit_command")

    ep = esub.add_parser("replace-source", help="Replace the source of an existing cell")
    ep.add_argument("-s", "--source", help="Cell source code (or pipe to stdin)")
    ep.add_argument("--source-file", help="Read source from this file")
    ep.add_argument("--cell-id", help="ID of the cell to update")
    ep.add_argument("-i", "--index", type=int, help="Index of the cell to update")

    ep = esub.add_parser("insert", help="Insert one or more new cells")
    ep.add_argument("-s", "--source", help="Source for a single cell (or pipe to stdin)")
    ep.add_argument("--source-file", help="Read source from this file")
    ep.add_argument("--cells-json", help="JSON array of cells: [{\"type\":\"code\",\"source\":\"...\"}]")
    ep.add_argument("--cells-file", help="Read cells JSON array from this file")
    ep.add_argument("--cell-type", default="code", help="Cell type when using -s/--source (default: code)")
    ep.add_argument("--at-index", type=int, default=-1, help="Insert at this cell index (-1 = end)")

    ep = esub.add_parser("delete", help="Delete a cell by ID or index")
    ep.add_argument("--cell-id", help="ID of the cell to delete")
    ep.add_argument("-i", "--index", type=int, help="Index of the cell to delete")

    ep = esub.add_parser("move", help="Move a cell to a new index")
    ep.add_argument("--cell-id", help="ID of the cell to move")
    ep.add_argument("-i", "--index", type=int, help="Current index of the cell to move")
    ep.add_argument("--to-index", type=int, required=True, help="Destination index")

    ep = esub.add_parser("clear-outputs", help="Clear outputs for a cell or all cells")
    ep.add_argument("--cell-id", help="ID of the cell to clear")
    ep.add_argument("-i", "--index", type=int, help="Index of the cell to clear")
    ep.add_argument("--all", action="store_true", help="Clear outputs for all cells")

    # exec
    p = sub.add_parser("exec", help="Execute a cell by ID or run code")
    p.add_argument("path", help="Path to the .ipynb notebook file")
    p.add_argument("--session-id", help="Override session (default: reuses active human session)")
    p.add_argument("--cell-id", help="ID of cell to execute")
    p.add_argument("-c", "--code", help="Inline code to insert and execute (prefer ix for most cases)")
    p.add_argument("--no-wait", action="store_true", help="Return immediately without waiting for output")
    p.add_argument("--timeout", type=float, default=30, help="Seconds to wait for completion (default: 30)")

    # ix
    p = sub.add_parser("ix", help="Insert and execute code (recommended default)")
    p.add_argument("path", help="Path to the .ipynb notebook file")
    p.add_argument("--session-id", help="Override session (default: reuses active human session)")
    p.add_argument("-s", "--source", help="Code to execute (or pipe to stdin)")
    p.add_argument("--source-file", help="Read source from this file")
    p.add_argument("--cells-json", help="JSON array of cells for batch insert+execute")
    p.add_argument("--cells-file", help="Read cells JSON array from this file")
    p.add_argument("--at-index", type=int, default=-1, help="Insert at this cell index (-1 = end)")
    p.add_argument("--no-wait", action="store_true", help="Return immediately without waiting for output")
    p.add_argument("--timeout", type=float, default=30, help="Seconds to wait for completion (default: 30)")

    # run-all
    p = sub.add_parser("run-all", help="Execute all cells")
    p.add_argument("path", help="Path to the .ipynb notebook file")

    # restart
    p = sub.add_parser("restart", help="Restart kernel")
    p.add_argument("path", help="Path to the .ipynb notebook file")

    # restart-run-all
    p = sub.add_parser("restart-run-all", help="Restart kernel and run all cells")
    p.add_argument("path", help="Path to the .ipynb notebook file")

    # new
    p = sub.add_parser("new", help="Create a new notebook")
    p.add_argument("path", help="Path for the new .ipynb notebook file")
    p.add_argument("--cells-json", help="JSON array of starter cells (created, not auto-executed)")
    p.add_argument("--kernel", help="Python executable path (e.g. /opt/miniconda3/bin/python3 or python3)")
    p.add_argument("--open", action="store_true", help="Open the notebook after creating it")
    p.add_argument("--target", choices=["vscode", "browser"], default="vscode", help="Where to open with --open (default: vscode)")
    p.add_argument("--editor", choices=["canvas", "jupyter"], default="canvas", help="Editor to use with --open (default: canvas)")
    p.add_argument("--browser-url", help="Standalone browser canvas URL to use when --target browser")

    # open
    p = sub.add_parser("open", help="Open an existing notebook")
    p.add_argument("path", help="Path to the .ipynb notebook file")
    p.add_argument("--target", choices=["vscode", "browser"], default="vscode", help="Where to open it (default: vscode)")
    p.add_argument("--editor", choices=["canvas", "jupyter"], default="canvas", help="Editor to use (default: canvas)")
    p.add_argument("--browser-url", help="Standalone browser canvas URL to use when --target browser")

    # browse
    p = sub.add_parser("browse", help="Open the notebook explorer in a browser")
    p.add_argument("--port", type=int, help="Preview server port (default: 4173 or AGENT_REPL_PREVIEW_PORT)")

    # kernels
    sub.add_parser("kernels", help="List available notebook kernels")

    # select-kernel
    p = sub.add_parser("select-kernel", help="Select kernel for a notebook")
    p.add_argument("path", help="Path to the .ipynb notebook file")
    p.add_argument("--kernel-id", help="Python executable path or name (e.g. /opt/miniconda3/bin/python3 or python3)")
    p.add_argument("--interactive", action="store_true", help="Open the VS Code kernel picker instead of defaulting to the workspace .venv")
    p.add_argument("--extension", default="ms-toolsai.jupyter", help="Extension ID")

    # prompts
    p = sub.add_parser("prompts", help="List prompt cells")
    p.add_argument("path", help="Path to the .ipynb notebook file")

    # respond
    p = sub.add_parser("respond", help="Respond to a prompt cell")
    p.add_argument("path", help="Path to the .ipynb notebook file")
    p.add_argument("--to", required=True, help="Prompt cell ID")
    p.add_argument("-s", "--source", help="Response code (or pipe to stdin)")
    p.add_argument("--source-file", help="Read source from this file")

    # setup
    p = sub.add_parser("setup", help="Run onboarding checks and optional workspace setup actions")
    p.add_argument("--workspace-root", help="Workspace root to inspect and configure (default: cwd)")
    p.add_argument("--configure-editor-default", action="store_true", help="Set *.ipynb to open in the Agent REPL canvas for this workspace")
    p.add_argument("--with-mcp", action="store_true", help="Run the public MCP onboarding flow and include connection details")
    p.add_argument("--mcp-smoke-test", action=argparse.BooleanOptionalAction, default=True, help="When --with-mcp is set, also run the MCP smoke test (default: enabled)")
    p.add_argument("--server-name", default="agent-repl", help="Server name for generated MCP config when --with-mcp is used")
    p.add_argument("--smoke-test", action="store_true", help="Create and execute a notebook smoke test in this workspace")
    p.add_argument("--smoke-test-path", help="Notebook path to use with --smoke-test (default: tmp/agent-repl-smoke-<pid>.ipynb)")
    p.add_argument("--runtime-dir", help=argparse.SUPPRESS)

    # doctor
    p = sub.add_parser("doctor", help="Inspect CLI, workspace, editor, and optional MCP readiness")
    p.add_argument("--workspace-root", help="Workspace root to inspect (default: cwd)")
    p.add_argument("--probe-mcp", action="store_true", help="Start or reuse the workspace daemon and report the canonical MCP endpoint")
    p.add_argument("--smoke-test", action="store_true", help="Create and execute a notebook smoke test in this workspace")
    p.add_argument("--smoke-test-path", help="Notebook path to use with --smoke-test (default: tmp/agent-repl-smoke-<pid>.ipynb)")
    p.add_argument("--runtime-dir", help=argparse.SUPPRESS)

    # editor
    p = sub.add_parser("editor", help="Configure editor-facing workspace defaults")
    esub = p.add_subparsers(dest="editor_command")

    ep = esub.add_parser("configure", help="Update workspace settings for VS Code-family editors")
    ep.add_argument("--workspace-root", help="Workspace root to configure (default: cwd)")
    ep.add_argument("--default-canvas", action="store_true", help="Set *.ipynb to open in the Agent REPL canvas for this workspace")

    # mcp
    p = sub.add_parser("mcp", help="Use agent-repl as an MCP server")
    mcpsub = p.add_subparsers(dest="mcp_command")

    vp = mcpsub.add_parser("setup", help="Start or reuse the workspace MCP server and print connection details")
    vp.add_argument("--workspace-root", help="Workspace root to bind the MCP server to (default: cwd)")
    vp.add_argument("--server-name", default="agent-repl", help="Server name for the generated MCP config (default: agent-repl)")
    vp.add_argument("--runtime-dir", help=argparse.SUPPRESS)

    vp = mcpsub.add_parser("status", help="Show the current MCP endpoint and daemon status for this workspace")
    vp.add_argument("--workspace-root", help="Workspace root to inspect (default: cwd)")
    vp.add_argument("--runtime-dir", help=argparse.SUPPRESS)

    vp = mcpsub.add_parser("config", help="Print a standard mcpServers config block for this workspace")
    vp.add_argument("--workspace-root", help="Workspace root to bind the MCP server to (default: cwd)")
    vp.add_argument("--server-name", default="agent-repl", help="Server name for the generated MCP config (default: agent-repl)")
    vp.add_argument("--runtime-dir", help=argparse.SUPPRESS)

    vp = mcpsub.add_parser("smoke-test", help="Verify the MCP endpoint with a real FastMCP client round-trip")
    vp.add_argument("--workspace-root", help="Workspace root to bind the MCP server to (default: cwd)")
    vp.add_argument("--runtime-dir", help=argparse.SUPPRESS)

    # core
    p = sub.add_parser("core", help=argparse.SUPPRESS, description="Internal core daemon diagnostics")
    coresub = p.add_subparsers(dest="core_command")

    vp = coresub.add_parser("start", help="Start the core daemon for this workspace")
    vp.add_argument("--workspace-root", help="Workspace root to bind the daemon to (default: cwd)")
    vp.add_argument("--timeout", type=float, default=DEFAULT_START_TIMEOUT, help="Seconds to wait for the daemon to become reachable")
    vp.add_argument("--runtime-dir", help=argparse.SUPPRESS)

    vp = coresub.add_parser("attach", help="Ensure the core daemon is running and attach or resume a client session")
    vp.add_argument("--workspace-root", help="Workspace root to bind the daemon to (default: cwd)")
    vp.add_argument("--actor", required=True, choices=["human", "agent", "system"])
    vp.add_argument("--client-type", required=True, choices=["cli", "vscode", "browser", "worker"])
    vp.add_argument("--label")
    vp.add_argument("--capability", action="append", dest="capability")
    vp.add_argument("--session-id")
    vp.add_argument("--timeout", type=float, default=DEFAULT_START_TIMEOUT, help="Seconds to wait for the daemon to become reachable")
    vp.add_argument("--runtime-dir", help=argparse.SUPPRESS)

    vp = coresub.add_parser("status", help="Show core daemon status for this workspace")
    vp.add_argument("--workspace-root", help="Workspace root to inspect (default: cwd)")
    vp.add_argument("--runtime-dir", help=argparse.SUPPRESS)

    vp = coresub.add_parser("stop", help="Stop the core daemon for this workspace")
    vp.add_argument("--workspace-root", help="Workspace root to stop (default: cwd)")
    vp.add_argument("--runtime-dir", help=argparse.SUPPRESS)

    vp = coresub.add_parser("sessions", help="List active sessions for this workspace")
    vp.add_argument("--workspace-root", help="Workspace root to inspect (default: cwd)")
    vp.add_argument("--runtime-dir", help=argparse.SUPPRESS)

    vp = coresub.add_parser("session-start", help="Start or resume a core session for this workspace")
    vp.add_argument("--workspace-root", help="Workspace root to inspect (default: cwd)")
    vp.add_argument("--actor", required=True, choices=["human", "agent", "system"])
    vp.add_argument("--client-type", required=True, choices=["cli", "vscode", "browser", "worker"])
    vp.add_argument("--label")
    vp.add_argument("--capability", action="append", dest="capability")
    vp.add_argument("--session-id")
    vp.add_argument("--runtime-dir", help=argparse.SUPPRESS)

    vp = coresub.add_parser("session-resolve", help="Resolve the preferred reusable session for this workspace")
    vp.add_argument("--workspace-root", help="Workspace root to inspect (default: cwd)")
    vp.add_argument("--actor", default="human", choices=["human", "agent", "system"])
    vp.add_argument("--runtime-dir", help=argparse.SUPPRESS)

    vp = coresub.add_parser("session-touch", help="Refresh liveness for an attached session")
    vp.add_argument("--workspace-root", help="Workspace root to inspect (default: cwd)")
    vp.add_argument("--session-id", required=True)
    vp.add_argument("--runtime-dir", help=argparse.SUPPRESS)

    vp = coresub.add_parser("session-detach", help="Detach a core session without deleting its continuity record")
    vp.add_argument("--workspace-root", help="Workspace root to inspect (default: cwd)")
    vp.add_argument("--session-id", required=True)
    vp.add_argument("--runtime-dir", help=argparse.SUPPRESS)

    vp = coresub.add_parser("session-presence-upsert", help="Update notebook presence for an attached session")
    vp.add_argument("path")
    vp.add_argument("--workspace-root", help="Workspace root to inspect (default: cwd)")
    vp.add_argument("--session-id", required=True)
    vp.add_argument("--activity", required=True)
    vp.add_argument("--cell-id")
    vp.add_argument("--cell-index", type=int)
    vp.add_argument("--runtime-dir", help=argparse.SUPPRESS)

    vp = coresub.add_parser("session-presence-clear", help="Clear notebook presence for an attached session")
    vp.add_argument("--workspace-root", help="Workspace root to inspect (default: cwd)")
    vp.add_argument("--session-id", required=True)
    vp.add_argument("--path")
    vp.add_argument("--runtime-dir", help=argparse.SUPPRESS)

    vp = coresub.add_parser("session-end", help="End a core session for this workspace")
    vp.add_argument("--workspace-root", help="Workspace root to inspect (default: cwd)")
    vp.add_argument("--session-id", required=True)
    vp.add_argument("--runtime-dir", help=argparse.SUPPRESS)

    vp = coresub.add_parser("documents", help="List documents registered in this workspace")
    vp.add_argument("--workspace-root", help="Workspace root to inspect (default: cwd)")
    vp.add_argument("--runtime-dir", help=argparse.SUPPRESS)

    vp = coresub.add_parser("document-open", help="Register a canonical document for this workspace")
    vp.add_argument("path")
    vp.add_argument("--workspace-root", help="Workspace root to inspect (default: cwd)")
    vp.add_argument("--runtime-dir", help=argparse.SUPPRESS)

    vp = coresub.add_parser("document-refresh", help="Refresh the observed file state for a registered document")
    vp.add_argument("--document-id", required=True)
    vp.add_argument("--workspace-root", help="Workspace root to inspect (default: cwd)")
    vp.add_argument("--runtime-dir", help=argparse.SUPPRESS)

    vp = coresub.add_parser("document-rebind", help="Explicitly accept the current file snapshot as the canonical bound state")
    vp.add_argument("--document-id", required=True)
    vp.add_argument("--workspace-root", help="Workspace root to inspect (default: cwd)")
    vp.add_argument("--runtime-dir", help=argparse.SUPPRESS)

    vp = coresub.add_parser("notebook-runtime", help="Inspect whether a notebook currently has an active headless runtime")
    vp.add_argument("path")
    vp.add_argument("--workspace-root", help="Workspace root to inspect (default: cwd)")
    vp.add_argument("--runtime-dir", help=argparse.SUPPRESS)

    vp = coresub.add_parser("notebook-projection", help="Fetch the runtime-owned snapshot for an actively projected headless notebook")
    vp.add_argument("path")
    vp.add_argument("--workspace-root", help="Workspace root to inspect (default: cwd)")
    vp.add_argument("--runtime-dir", help=argparse.SUPPRESS)

    vp = coresub.add_parser("notebook-activity", help="Fetch live presence and recent activity for a notebook")
    vp.add_argument("path")
    vp.add_argument("--since", type=float)
    vp.add_argument("--workspace-root", help="Workspace root to inspect (default: cwd)")
    vp.add_argument("--runtime-dir", help=argparse.SUPPRESS)

    vp = coresub.add_parser("project-visible-notebook", help=argparse.SUPPRESS)
    vp.add_argument("path")
    vp.add_argument("--cells-json")
    vp.add_argument("--cells-file")
    vp.add_argument("--session-id")
    vp.add_argument("--workspace-root", help="Workspace root to inspect (default: cwd)")
    vp.add_argument("--runtime-dir", help=argparse.SUPPRESS)

    vp = coresub.add_parser("execute-visible-cell", help="Execute the current visible source for a notebook cell against the bound headless runtime")
    vp.add_argument("path")
    vp.add_argument("--cell-index", type=int, required=True)
    vp.add_argument("-s", "--source")
    vp.add_argument("--source-file")
    vp.add_argument("--session-id")
    vp.add_argument("--workspace-root", help="Workspace root to inspect (default: cwd)")
    vp.add_argument("--runtime-dir", help=argparse.SUPPRESS)

    vp = coresub.add_parser("cell-lease-acquire", help="Acquire or refresh a short-lived lease on a notebook cell")
    vp.add_argument("path")
    vp.add_argument("--workspace-root", help="Workspace root to inspect (default: cwd)")
    vp.add_argument("--session-id", required=True)
    vp.add_argument("--cell-id")
    vp.add_argument("--cell-index", type=int)
    vp.add_argument("--kind", choices=["edit", "structure"], default="edit")
    vp.add_argument("--ttl-seconds", type=float)
    vp.add_argument("--runtime-dir", help=argparse.SUPPRESS)

    vp = coresub.add_parser("cell-lease-release", help="Release a notebook cell lease for a session")
    vp.add_argument("path")
    vp.add_argument("--workspace-root", help="Workspace root to inspect (default: cwd)")
    vp.add_argument("--session-id", required=True)
    vp.add_argument("--cell-id")
    vp.add_argument("--cell-index", type=int)
    vp.add_argument("--runtime-dir", help=argparse.SUPPRESS)

    vp = coresub.add_parser("branches", help="List collaboration branches for this workspace")
    vp.add_argument("--workspace-root", help="Workspace root to inspect (default: cwd)")
    vp.add_argument("--runtime-dir", help=argparse.SUPPRESS)

    vp = coresub.add_parser("branch-start", help="Create a core collaboration branch for a document")
    vp.add_argument("--workspace-root", help="Workspace root to inspect (default: cwd)")
    vp.add_argument("--document-id", required=True)
    vp.add_argument("--owner-session-id")
    vp.add_argument("--parent-branch-id")
    vp.add_argument("--title")
    vp.add_argument("--purpose")
    vp.add_argument("--branch-id")
    vp.add_argument("--runtime-dir", help=argparse.SUPPRESS)

    vp = coresub.add_parser("branch-finish", help="Move a collaboration branch to a terminal review outcome")
    vp.add_argument("--workspace-root", help="Workspace root to inspect (default: cwd)")
    vp.add_argument("--branch-id", required=True)
    vp.add_argument("--status-value", required=True, choices=["merged", "rejected", "abandoned"])
    vp.add_argument("--runtime-dir", help=argparse.SUPPRESS)

    vp = coresub.add_parser("branch-review-request", help="Request review for a collaboration branch")
    vp.add_argument("--workspace-root", help="Workspace root to inspect (default: cwd)")
    vp.add_argument("--branch-id", required=True)
    vp.add_argument("--requested-by-session-id", required=True)
    vp.add_argument("--note")
    vp.add_argument("--runtime-dir", help=argparse.SUPPRESS)

    vp = coresub.add_parser("branch-review-resolve", help="Resolve a pending branch review")
    vp.add_argument("--workspace-root", help="Workspace root to inspect (default: cwd)")
    vp.add_argument("--branch-id", required=True)
    vp.add_argument("--resolved-by-session-id", required=True)
    vp.add_argument("--resolution", required=True, choices=["approved", "changes-requested", "rejected"])
    vp.add_argument("--note")
    vp.add_argument("--runtime-dir", help=argparse.SUPPRESS)

    vp = coresub.add_parser("runtimes", help="List runtimes registered in this workspace")
    vp.add_argument("--workspace-root", help="Workspace root to inspect (default: cwd)")
    vp.add_argument("--runtime-dir", help=argparse.SUPPRESS)

    vp = coresub.add_parser("runtime-start", help="Register or resume a core runtime in this workspace")
    vp.add_argument("--workspace-root", help="Workspace root to inspect (default: cwd)")
    vp.add_argument("--mode", required=True, choices=["interactive", "shared", "headless", "pinned", "ephemeral"])
    vp.add_argument("--label")
    vp.add_argument("--environment")
    vp.add_argument("--document-path")
    vp.add_argument("--ttl-seconds", type=int)
    vp.add_argument("--runtime-id")
    vp.add_argument("--runtime-dir", help=argparse.SUPPRESS)

    vp = coresub.add_parser("runtime-stop", help="Mark a core runtime as stopped")
    vp.add_argument("--workspace-root", help="Workspace root to inspect (default: cwd)")
    vp.add_argument("--runtime-id", required=True)
    vp.add_argument("--runtime-dir", help=argparse.SUPPRESS)

    vp = coresub.add_parser("runtime-recover", help="Recover a notebook-bound runtime that lost live continuity")
    vp.add_argument("--workspace-root", help="Workspace root to inspect (default: cwd)")
    vp.add_argument("--runtime-id", required=True)
    vp.add_argument("--runtime-dir", help=argparse.SUPPRESS)

    vp = coresub.add_parser("runtime-promote", help="Promote an ephemeral runtime into shared or pinned mode")
    vp.add_argument("--workspace-root", help="Workspace root to inspect (default: cwd)")
    vp.add_argument("--runtime-id", required=True)
    vp.add_argument("--mode", choices=["shared", "pinned"], default="shared")
    vp.add_argument("--runtime-dir", help=argparse.SUPPRESS)

    vp = coresub.add_parser("runtime-discard", help="Discard an ephemeral runtime and mark it terminal")
    vp.add_argument("--workspace-root", help="Workspace root to inspect (default: cwd)")
    vp.add_argument("--runtime-id", required=True)
    vp.add_argument("--runtime-dir", help=argparse.SUPPRESS)

    vp = coresub.add_parser("runs", help="List runs for this workspace")
    vp.add_argument("--workspace-root", help="Workspace root to inspect (default: cwd)")
    vp.add_argument("--runtime-dir", help=argparse.SUPPRESS)

    vp = coresub.add_parser("run-start", help="Register a running run")
    vp.add_argument("--workspace-root", help="Workspace root to inspect (default: cwd)")
    vp.add_argument("--runtime-id", required=True)
    vp.add_argument("--target-type", required=True, choices=["document", "node", "branch"])
    vp.add_argument("--target-ref", required=True)
    vp.add_argument("--kind", default="execute")
    vp.add_argument("--run-id")
    vp.add_argument("--runtime-dir", help=argparse.SUPPRESS)

    vp = coresub.add_parser("run-finish", help="Finish a core run with a terminal status")
    vp.add_argument("--workspace-root", help="Workspace root to inspect (default: cwd)")
    vp.add_argument("--run-id", required=True)
    vp.add_argument("--status-value", required=True, choices=["completed", "failed", "interrupted"])
    vp.add_argument("--runtime-dir", help=argparse.SUPPRESS)

    vp = coresub.add_parser("serve", help=argparse.SUPPRESS)
    vp.add_argument("--workspace-root", required=True)
    vp.add_argument("--runtime-dir", required=True)

    public_commands = [
        "reload",
        "cat",
        "status",
        "edit",
        "exec",
        "ix",
        "run-all",
        "restart",
        "restart-run-all",
        "new",
        "open",
        "browse",
        "kernels",
        "select-kernel",
        "prompts",
        "respond",
        "setup",
        "doctor",
        "editor",
        "mcp",
    ]
    sub.metavar = "{" + ",".join(public_commands) + "}"
    sub._choices_actions = [action for action in sub._choices_actions if action.dest != "core"]

    return parser


# ------------------------------------------------------------------
# Entry point
# ------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    # Support --pretty anywhere by extracting it before argparse
    raw = argv if argv is not None else sys.argv[1:]
    pretty = "--pretty" in raw
    if pretty:
        raw = [a for a in raw if a != "--pretty"]

    parser = build_parser()
    args = parser.parse_args(raw)
    args.pretty = pretty

    if not args.command:
        parser.print_help()
        return 1

    handlers = {
        "reload": cmd_reload,
        "cat": cmd_cat,
        "status": cmd_status,
        "edit": cmd_edit,
        "exec": cmd_exec,
        "ix": cmd_ix,
        "run-all": cmd_run_all,
        "restart": cmd_restart,
        "restart-run-all": cmd_restart_run_all,
        "new": cmd_new,
        "open": cmd_open,
        "browse": cmd_browse,
        "kernels": cmd_kernels,
        "select-kernel": cmd_select_kernel,
        "prompts": cmd_prompts,
        "respond": cmd_respond,
        "setup": cmd_setup,
        "doctor": cmd_doctor,
        "editor": cmd_editor,
        "mcp": cmd_mcp,
        "core": cmd_core,
    }

    handler = handlers.get(args.command)
    if not handler:
        parser.print_help()
        return 1

    try:
        return handler(args)
    except ApiError as exc:
        print(json.dumps(exc.to_payload(), indent=2), file=sys.stderr)
        return 1
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, indent=2), file=sys.stderr)
        return 1


def main_entry() -> None:
    raise SystemExit(main())
