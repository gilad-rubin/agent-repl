"""CLI entry point — argparse-based subcommands talking to the bridge."""
from __future__ import annotations

import argparse
import json
import os
import sys
import tomllib
from importlib.metadata import PackageNotFoundError, version as package_version
from pathlib import Path
from typing import Any

from agent_repl.client import BridgeClient
from agent_repl.v2.client import DEFAULT_START_TIMEOUT, V2Client
from agent_repl.v2.server import serve_forever


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


def _v2_client(workspace_hint: str | None = None, runtime_dir: str | None = None) -> V2Client:
    return V2Client.discover(workspace_hint=workspace_hint, runtime_dir=runtime_dir)


def _workspace_root() -> str:
    return os.path.realpath(os.getcwd())


def _notebook_client(path: str) -> V2Client | BridgeClient:
    workspace_root = _workspace_root()
    try:
        V2Client.start(workspace_root)
        return _v2_client(path)
    except Exception:
        return _client(path)


# ------------------------------------------------------------------
# Subcommand handlers
# ------------------------------------------------------------------

def cmd_cat(args: argparse.Namespace) -> int:
    client = _notebook_client(args.path)
    if hasattr(client, "notebook_contents"):
        result = client.notebook_contents(args.path)
    else:
        result = client.contents(args.path)
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
    if hasattr(client, "notebook_status"):
        result = client.notebook_status(args.path)
    else:
        result = client.status(args.path)
    _out(result, args.pretty)
    return 0


def cmd_edit(args: argparse.Namespace) -> int:
    client = _notebook_client(args.path)
    op: dict[str, Any] = {"op": args.edit_command}

    if args.edit_command == "replace-source":
        op["source"] = _read_source(args)
        if args.cell_id:
            op["cell_id"] = args.cell_id
        if args.index is not None:
            op["cell_index"] = args.index
    elif args.edit_command == "insert":
        op["source"] = _read_source(args)
        op["cell_type"] = args.cell_type
        op["at_index"] = args.at_index
    elif args.edit_command == "delete":
        if args.cell_id:
            op["cell_id"] = args.cell_id
        if args.index is not None:
            op["cell_index"] = args.index
    elif args.edit_command == "move":
        if args.cell_id:
            op["cell_id"] = args.cell_id
        if args.index is not None:
            op["cell_index"] = args.index
        op["to_index"] = args.to_index
    elif args.edit_command == "clear-outputs":
        if getattr(args, "all", False):
            op["all"] = True
        elif args.cell_id:
            op["cell_id"] = args.cell_id
        elif args.index is not None:
            op["cell_index"] = args.index

    if hasattr(client, "notebook_edit"):
        result = client.notebook_edit(args.path, [op])
    else:
        result = client.edit(args.path, [op])
    _out(result, args.pretty)
    return 0


def cmd_exec(args: argparse.Namespace) -> int:
    client = _notebook_client(args.path)
    wait = not getattr(args, "no_wait", False)
    timeout = getattr(args, "timeout", 30)
    if args.code:
        if hasattr(client, "notebook_insert_execute"):
            result = client.notebook_insert_execute(args.path, args.code, wait=wait, timeout=timeout)
        else:
            result = client.insert_and_execute(args.path, args.code, wait=wait, timeout=timeout)
    elif args.cell_id:
        if hasattr(client, "notebook_execute_cell"):
            result = client.notebook_execute_cell(args.path, cell_id=args.cell_id, wait=wait, timeout=timeout)
        else:
            result = client.execute_cell(args.path, cell_id=args.cell_id, wait=wait, timeout=timeout)
    else:
        print(json.dumps({"error": "Provide --cell-id or -c/--code"}, indent=2), file=sys.stderr)
        return 1
    _out(result, args.pretty)
    return 0


def cmd_ix(args: argparse.Namespace) -> int:
    source = _read_source(args)
    wait = not getattr(args, "no_wait", False)
    timeout = getattr(args, "timeout", 30)
    at_index = getattr(args, "at_index", -1)
    client = _notebook_client(args.path)
    if hasattr(client, "notebook_insert_execute"):
        result = client.notebook_insert_execute(args.path, source, at_index=at_index, wait=wait, timeout=timeout)
    else:
        result = client.insert_and_execute(args.path, source, at_index=at_index, wait=wait, timeout=timeout)
    _out(result, args.pretty)
    return 0


def cmd_run_all(args: argparse.Namespace) -> int:
    client = _notebook_client(args.path)
    if hasattr(client, "notebook_execute_all"):
        result = client.notebook_execute_all(args.path)
    else:
        result = client.execute_all(args.path)
    _out(result, args.pretty)
    return 0


def cmd_restart(args: argparse.Namespace) -> int:
    result = _client(args.path).restart_kernel(args.path)
    _out(result, args.pretty)
    return 0


def cmd_restart_run_all(args: argparse.Namespace) -> int:
    result = _client(args.path).restart_and_run_all(args.path)
    _out(result, args.pretty)
    return 0


def cmd_new(args: argparse.Namespace) -> int:
    cells = None
    if args.cells_json:
        cells = json.loads(args.cells_json)
    kernel_id = getattr(args, "kernel", None)
    client = _notebook_client(args.path)
    if hasattr(client, "notebook_create"):
        result = client.notebook_create(args.path, cells=cells, kernel_id=kernel_id)
    else:
        result = client.create(args.path, cells=cells, kernel_id=kernel_id)
    _out(result, args.pretty)
    return 0


def cmd_kernels(args: argparse.Namespace) -> int:
    result = _client().kernels()
    _out(result, args.pretty)
    return 0


def cmd_select_kernel(args: argparse.Namespace) -> int:
    kernel_id = getattr(args, "kernel_id", None)
    extension = getattr(args, "extension", None)
    interactive = getattr(args, "interactive", False)
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
    if hasattr(client, "notebook_contents"):
        result = client.notebook_contents(args.path)
    else:
        result = client.contents(args.path)
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


def cmd_v2(args: argparse.Namespace) -> int:
    workspace_root = os.path.realpath(getattr(args, "workspace_root", None) or os.getcwd())
    runtime_dir = getattr(args, "runtime_dir", None)

    if args.v2_command == "start":
        result = V2Client.start(
            workspace_root,
            timeout=getattr(args, "timeout", DEFAULT_START_TIMEOUT),
            runtime_dir=runtime_dir,
        )
        _out(result, args.pretty)
        return 0

    if args.v2_command == "attach":
        result = V2Client.attach(
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

    if args.v2_command == "status":
        result = _v2_client(workspace_root, runtime_dir=runtime_dir).status()
        _out(result, args.pretty)
        return 0

    if args.v2_command == "stop":
        result = _v2_client(workspace_root, runtime_dir=runtime_dir).shutdown()
        _out(result, args.pretty)
        return 0

    if args.v2_command == "sessions":
        result = _v2_client(workspace_root, runtime_dir=runtime_dir).list_sessions()
        _out(result, args.pretty)
        return 0

    if args.v2_command == "session-start":
        result = _v2_client(workspace_root, runtime_dir=runtime_dir).start_session(
            actor=args.actor,
            client=args.client_type,
            label=getattr(args, "label", None),
            capabilities=getattr(args, "capability", None),
            session_id=getattr(args, "session_id", None),
        )
        _out(result, args.pretty)
        return 0

    if args.v2_command == "session-touch":
        result = _v2_client(workspace_root, runtime_dir=runtime_dir).touch_session(args.session_id)
        _out(result, args.pretty)
        return 0

    if args.v2_command == "session-detach":
        result = _v2_client(workspace_root, runtime_dir=runtime_dir).detach_session(args.session_id)
        _out(result, args.pretty)
        return 0

    if args.v2_command == "session-end":
        result = _v2_client(workspace_root, runtime_dir=runtime_dir).end_session(args.session_id)
        _out(result, args.pretty)
        return 0

    if args.v2_command == "documents":
        result = _v2_client(workspace_root, runtime_dir=runtime_dir).list_documents()
        _out(result, args.pretty)
        return 0

    if args.v2_command == "document-open":
        result = _v2_client(workspace_root, runtime_dir=runtime_dir).open_document(args.path)
        _out(result, args.pretty)
        return 0

    if args.v2_command == "document-refresh":
        result = _v2_client(workspace_root, runtime_dir=runtime_dir).refresh_document(args.document_id)
        _out(result, args.pretty)
        return 0

    if args.v2_command == "document-rebind":
        result = _v2_client(workspace_root, runtime_dir=runtime_dir).rebind_document(args.document_id)
        _out(result, args.pretty)
        return 0

    if args.v2_command == "branches":
        result = _v2_client(workspace_root, runtime_dir=runtime_dir).list_branches()
        _out(result, args.pretty)
        return 0

    if args.v2_command == "branch-start":
        result = _v2_client(workspace_root, runtime_dir=runtime_dir).start_branch(
            document_id=args.document_id,
            owner_session_id=getattr(args, "owner_session_id", None),
            parent_branch_id=getattr(args, "parent_branch_id", None),
            title=getattr(args, "title", None),
            purpose=getattr(args, "purpose", None),
            branch_id=getattr(args, "branch_id", None),
        )
        _out(result, args.pretty)
        return 0

    if args.v2_command == "branch-finish":
        result = _v2_client(workspace_root, runtime_dir=runtime_dir).finish_branch(
            args.branch_id,
            status=args.status_value,
        )
        _out(result, args.pretty)
        return 0

    if args.v2_command == "runtimes":
        result = _v2_client(workspace_root, runtime_dir=runtime_dir).list_runtimes()
        _out(result, args.pretty)
        return 0

    if args.v2_command == "runtime-start":
        result = _v2_client(workspace_root, runtime_dir=runtime_dir).start_runtime(
            mode=args.mode,
            label=getattr(args, "label", None),
            runtime_id=getattr(args, "runtime_id", None),
            environment=getattr(args, "environment", None),
        )
        _out(result, args.pretty)
        return 0

    if args.v2_command == "runtime-stop":
        result = _v2_client(workspace_root, runtime_dir=runtime_dir).stop_runtime(args.runtime_id)
        _out(result, args.pretty)
        return 0

    if args.v2_command == "runs":
        result = _v2_client(workspace_root, runtime_dir=runtime_dir).list_runs()
        _out(result, args.pretty)
        return 0

    if args.v2_command == "run-start":
        result = _v2_client(workspace_root, runtime_dir=runtime_dir).start_run(
            runtime_id=args.runtime_id,
            target_type=args.target_type,
            target_ref=args.target_ref,
            kind=args.kind,
            run_id=getattr(args, "run_id", None),
        )
        _out(result, args.pretty)
        return 0

    if args.v2_command == "run-finish":
        result = _v2_client(workspace_root, runtime_dir=runtime_dir).finish_run(
            args.run_id,
            status=args.status_value,
        )
        _out(result, args.pretty)
        return 0

    if args.v2_command == "serve":
        serve_forever(workspace_root, runtime_dir=args.runtime_dir)
        return 0

    raise RuntimeError("Unknown v2 command")


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


# ------------------------------------------------------------------
# Parser
# ------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agent-repl", description="Agent REPL bridge CLI")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output")
    parser.add_argument("--version", action="version", version=_app_version())
    sub = parser.add_subparsers(dest="command")

    # reload
    sub.add_parser("reload", help="Restart the VS Code extension host")

    # cat
    p = sub.add_parser("cat", help="Read notebook contents")
    p.add_argument("path")
    p.add_argument("--no-outputs", action="store_true", help="Show cell sources only, without outputs")

    # status
    p = sub.add_parser("status", help="Notebook execution status")
    p.add_argument("path")

    # edit
    p = sub.add_parser("edit", help="Edit notebook cells")
    p.add_argument("path")
    esub = p.add_subparsers(dest="edit_command")

    ep = esub.add_parser("replace-source")
    ep.add_argument("-s", "--source")
    ep.add_argument("--source-file")
    ep.add_argument("--cell-id")
    ep.add_argument("-i", "--index", type=int)

    ep = esub.add_parser("insert")
    ep.add_argument("-s", "--source")
    ep.add_argument("--source-file")
    ep.add_argument("--cell-type", default="code")
    ep.add_argument("--at-index", type=int, default=-1)

    ep = esub.add_parser("delete")
    ep.add_argument("--cell-id")
    ep.add_argument("-i", "--index", type=int)

    ep = esub.add_parser("move")
    ep.add_argument("--cell-id")
    ep.add_argument("-i", "--index", type=int)
    ep.add_argument("--to-index", type=int, required=True)

    ep = esub.add_parser("clear-outputs")
    ep.add_argument("--cell-id")
    ep.add_argument("-i", "--index", type=int)
    ep.add_argument("--all", action="store_true")

    # exec
    p = sub.add_parser("exec", help="Execute a cell by ID or run code")
    p.add_argument("path")
    p.add_argument("--cell-id", help="ID of cell to execute")
    p.add_argument("-c", "--code", help="Code to insert and execute")
    p.add_argument("--no-wait", action="store_true", help="Return immediately without waiting for output")
    p.add_argument("--timeout", type=float, default=30, help="Seconds to wait for completion (default: 30)")

    # ix
    p = sub.add_parser("ix", help="Insert and execute code")
    p.add_argument("path")
    p.add_argument("-s", "--source")
    p.add_argument("--source-file")
    p.add_argument("--at-index", type=int, default=-1, help="Insert at this cell index (-1 = end)")
    p.add_argument("--no-wait", action="store_true", help="Return immediately without waiting for output")
    p.add_argument("--timeout", type=float, default=30, help="Seconds to wait for completion (default: 30)")

    # run-all
    p = sub.add_parser("run-all", help="Execute all cells")
    p.add_argument("path")

    # restart
    p = sub.add_parser("restart", help="Restart kernel")
    p.add_argument("path")

    # restart-run-all
    p = sub.add_parser("restart-run-all", help="Restart kernel and run all cells")
    p.add_argument("path")

    # new
    p = sub.add_parser("new", help="Create a new notebook")
    p.add_argument("path")
    p.add_argument("--cells-json")
    p.add_argument("--kernel", help="Kernel ID to auto-select (skips interactive picker)")

    # kernels
    sub.add_parser("kernels", help="List available notebook kernels")

    # select-kernel
    p = sub.add_parser("select-kernel", help="Select kernel for a notebook")
    p.add_argument("path")
    p.add_argument("--kernel-id", help="Kernel ID to select programmatically")
    p.add_argument("--interactive", action="store_true", help="Open the VS Code kernel picker instead of defaulting to the workspace .venv")
    p.add_argument("--extension", default="ms-toolsai.jupyter", help="Extension ID")

    # prompts
    p = sub.add_parser("prompts", help="List prompt cells")
    p.add_argument("path")

    # respond
    p = sub.add_parser("respond", help="Respond to a prompt cell")
    p.add_argument("path")
    p.add_argument("--to", required=True, help="Prompt cell ID")
    p.add_argument("-s", "--source")
    p.add_argument("--source-file")

    # v2
    p = sub.add_parser("v2", help=argparse.SUPPRESS, description="Internal core daemon diagnostics")
    v2sub = p.add_subparsers(dest="v2_command")

    vp = v2sub.add_parser("start", help="Start the experimental v2 core daemon for this workspace")
    vp.add_argument("--workspace-root", help="Workspace root to bind the daemon to (default: cwd)")
    vp.add_argument("--timeout", type=float, default=DEFAULT_START_TIMEOUT, help="Seconds to wait for the daemon to become reachable")
    vp.add_argument("--runtime-dir", help=argparse.SUPPRESS)

    vp = v2sub.add_parser("attach", help="Ensure the v2 daemon is running and attach or resume a client session")
    vp.add_argument("--workspace-root", help="Workspace root to bind the daemon to (default: cwd)")
    vp.add_argument("--actor", required=True, choices=["human", "agent", "system"])
    vp.add_argument("--client-type", required=True, choices=["cli", "vscode", "browser", "worker"])
    vp.add_argument("--label")
    vp.add_argument("--capability", action="append", dest="capability")
    vp.add_argument("--session-id")
    vp.add_argument("--timeout", type=float, default=DEFAULT_START_TIMEOUT, help="Seconds to wait for the daemon to become reachable")
    vp.add_argument("--runtime-dir", help=argparse.SUPPRESS)

    vp = v2sub.add_parser("status", help="Show v2 core daemon status for this workspace")
    vp.add_argument("--workspace-root", help="Workspace root to inspect (default: cwd)")
    vp.add_argument("--runtime-dir", help=argparse.SUPPRESS)

    vp = v2sub.add_parser("stop", help="Stop the v2 core daemon for this workspace")
    vp.add_argument("--workspace-root", help="Workspace root to stop (default: cwd)")
    vp.add_argument("--runtime-dir", help=argparse.SUPPRESS)

    vp = v2sub.add_parser("sessions", help="List active v2 sessions for this workspace")
    vp.add_argument("--workspace-root", help="Workspace root to inspect (default: cwd)")
    vp.add_argument("--runtime-dir", help=argparse.SUPPRESS)

    vp = v2sub.add_parser("session-start", help="Start or resume a v2 session for this workspace")
    vp.add_argument("--workspace-root", help="Workspace root to inspect (default: cwd)")
    vp.add_argument("--actor", required=True, choices=["human", "agent", "system"])
    vp.add_argument("--client-type", required=True, choices=["cli", "vscode", "browser", "worker"])
    vp.add_argument("--label")
    vp.add_argument("--capability", action="append", dest="capability")
    vp.add_argument("--session-id")
    vp.add_argument("--runtime-dir", help=argparse.SUPPRESS)

    vp = v2sub.add_parser("session-touch", help="Refresh liveness for an attached v2 session")
    vp.add_argument("--workspace-root", help="Workspace root to inspect (default: cwd)")
    vp.add_argument("--session-id", required=True)
    vp.add_argument("--runtime-dir", help=argparse.SUPPRESS)

    vp = v2sub.add_parser("session-detach", help="Detach a v2 session without deleting its continuity record")
    vp.add_argument("--workspace-root", help="Workspace root to inspect (default: cwd)")
    vp.add_argument("--session-id", required=True)
    vp.add_argument("--runtime-dir", help=argparse.SUPPRESS)

    vp = v2sub.add_parser("session-end", help="End a v2 session for this workspace")
    vp.add_argument("--workspace-root", help="Workspace root to inspect (default: cwd)")
    vp.add_argument("--session-id", required=True)
    vp.add_argument("--runtime-dir", help=argparse.SUPPRESS)

    vp = v2sub.add_parser("documents", help="List v2 documents registered in this workspace")
    vp.add_argument("--workspace-root", help="Workspace root to inspect (default: cwd)")
    vp.add_argument("--runtime-dir", help=argparse.SUPPRESS)

    vp = v2sub.add_parser("document-open", help="Register a canonical v2 document for this workspace")
    vp.add_argument("path")
    vp.add_argument("--workspace-root", help="Workspace root to inspect (default: cwd)")
    vp.add_argument("--runtime-dir", help=argparse.SUPPRESS)

    vp = v2sub.add_parser("document-refresh", help="Refresh the observed file state for a registered v2 document")
    vp.add_argument("--document-id", required=True)
    vp.add_argument("--workspace-root", help="Workspace root to inspect (default: cwd)")
    vp.add_argument("--runtime-dir", help=argparse.SUPPRESS)

    vp = v2sub.add_parser("document-rebind", help="Explicitly accept the current file snapshot as the canonical bound state")
    vp.add_argument("--document-id", required=True)
    vp.add_argument("--workspace-root", help="Workspace root to inspect (default: cwd)")
    vp.add_argument("--runtime-dir", help=argparse.SUPPRESS)

    vp = v2sub.add_parser("branches", help="List v2 collaboration branches for this workspace")
    vp.add_argument("--workspace-root", help="Workspace root to inspect (default: cwd)")
    vp.add_argument("--runtime-dir", help=argparse.SUPPRESS)

    vp = v2sub.add_parser("branch-start", help="Create a v2 collaboration branch for a document")
    vp.add_argument("--workspace-root", help="Workspace root to inspect (default: cwd)")
    vp.add_argument("--document-id", required=True)
    vp.add_argument("--owner-session-id")
    vp.add_argument("--parent-branch-id")
    vp.add_argument("--title")
    vp.add_argument("--purpose")
    vp.add_argument("--branch-id")
    vp.add_argument("--runtime-dir", help=argparse.SUPPRESS)

    vp = v2sub.add_parser("branch-finish", help="Move a collaboration branch to a terminal review outcome")
    vp.add_argument("--workspace-root", help="Workspace root to inspect (default: cwd)")
    vp.add_argument("--branch-id", required=True)
    vp.add_argument("--status-value", required=True, choices=["merged", "rejected", "abandoned"])
    vp.add_argument("--runtime-dir", help=argparse.SUPPRESS)

    vp = v2sub.add_parser("runtimes", help="List v2 runtimes registered in this workspace")
    vp.add_argument("--workspace-root", help="Workspace root to inspect (default: cwd)")
    vp.add_argument("--runtime-dir", help=argparse.SUPPRESS)

    vp = v2sub.add_parser("runtime-start", help="Register or resume a v2 runtime in this workspace")
    vp.add_argument("--workspace-root", help="Workspace root to inspect (default: cwd)")
    vp.add_argument("--mode", required=True, choices=["interactive", "shared", "headless", "pinned", "ephemeral"])
    vp.add_argument("--label")
    vp.add_argument("--environment")
    vp.add_argument("--runtime-id")
    vp.add_argument("--runtime-dir", help=argparse.SUPPRESS)

    vp = v2sub.add_parser("runtime-stop", help="Mark a v2 runtime as stopped")
    vp.add_argument("--workspace-root", help="Workspace root to inspect (default: cwd)")
    vp.add_argument("--runtime-id", required=True)
    vp.add_argument("--runtime-dir", help=argparse.SUPPRESS)

    vp = v2sub.add_parser("runs", help="List v2 runs for this workspace")
    vp.add_argument("--workspace-root", help="Workspace root to inspect (default: cwd)")
    vp.add_argument("--runtime-dir", help=argparse.SUPPRESS)

    vp = v2sub.add_parser("run-start", help="Register a running v2 run")
    vp.add_argument("--workspace-root", help="Workspace root to inspect (default: cwd)")
    vp.add_argument("--runtime-id", required=True)
    vp.add_argument("--target-type", required=True, choices=["document", "node", "branch"])
    vp.add_argument("--target-ref", required=True)
    vp.add_argument("--kind", default="execute")
    vp.add_argument("--run-id")
    vp.add_argument("--runtime-dir", help=argparse.SUPPRESS)

    vp = v2sub.add_parser("run-finish", help="Finish a v2 run with a terminal status")
    vp.add_argument("--workspace-root", help="Workspace root to inspect (default: cwd)")
    vp.add_argument("--run-id", required=True)
    vp.add_argument("--status-value", required=True, choices=["completed", "failed", "interrupted"])
    vp.add_argument("--runtime-dir", help=argparse.SUPPRESS)

    vp = v2sub.add_parser("serve", help=argparse.SUPPRESS)
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
        "kernels",
        "select-kernel",
        "prompts",
        "respond",
    ]
    sub.metavar = "{" + ",".join(public_commands) + "}"
    sub._choices_actions = [action for action in sub._choices_actions if action.dest != "v2"]

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
        "kernels": cmd_kernels,
        "select-kernel": cmd_select_kernel,
        "prompts": cmd_prompts,
        "respond": cmd_respond,
        "v2": cmd_v2,
    }

    handler = handlers.get(args.command)
    if not handler:
        parser.print_help()
        return 1

    try:
        return handler(args)
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, indent=2), file=sys.stderr)
        return 1


def main_entry() -> None:
    raise SystemExit(main())
