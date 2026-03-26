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
from agent_repl.core.client import DEFAULT_START_TIMEOUT, CoreClient
from agent_repl.core.server import serve_forever


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
    result = CoreClient.start(workspace_root, runtime_dir=runtime_dir)
    if result.get("stale_restart"):
        print(
            f"Restarted stale daemon (PID {result.get('stale_pid')}, code updated)",
            file=sys.stderr,
        )
    return CoreClient.discover(workspace_hint=workspace_hint, runtime_dir=runtime_dir)


def _core_client_raw(workspace_hint: str | None = None, runtime_dir: str | None = None) -> CoreClient:
    """Bare discover without freshness check — only for core stop/status diagnostics."""
    return CoreClient.discover(workspace_hint=workspace_hint, runtime_dir=runtime_dir, allow_stale=True)


def _workspace_root() -> str:
    return os.path.realpath(os.getcwd())


def _notebook_client(path: str) -> CoreClient | BridgeClient:
    workspace_root = _workspace_root()
    result = CoreClient.start(workspace_root)
    if result.get("stale_restart"):
        print(
            f"Restarted stale daemon (PID {result.get('stale_pid')}, code updated)",
            file=sys.stderr,
        )
    return CoreClient.discover(workspace_hint=path)


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
        session_id = getattr(args, "session_id", None)
        if session_id:
            result = client.notebook_edit(args.path, [op], owner_session_id=session_id)
        else:
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
            session_id = getattr(args, "session_id", None)
            kwargs: dict[str, Any] = {"wait": wait, "timeout": timeout}
            if session_id:
                kwargs["owner_session_id"] = session_id
            result = client.notebook_insert_execute(args.path, args.code, **kwargs)
        else:
            result = client.insert_and_execute(args.path, args.code, wait=wait, timeout=timeout)
    elif args.cell_id:
        if hasattr(client, "notebook_execute_cell"):
            session_id = getattr(args, "session_id", None)
            kwargs = {"cell_id": args.cell_id, "wait": wait, "timeout": timeout}
            if session_id:
                kwargs["owner_session_id"] = session_id
            result = client.notebook_execute_cell(args.path, **kwargs)
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
        session_id = getattr(args, "session_id", None)
        kwargs = {"at_index": at_index, "wait": wait, "timeout": timeout}
        if session_id:
            kwargs["owner_session_id"] = session_id
        result = client.notebook_insert_execute(args.path, source, **kwargs)
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
    client = _notebook_client(args.path)
    if hasattr(client, "notebook_restart"):
        result = client.notebook_restart(args.path)
    else:
        result = client.restart_kernel(args.path)
    _out(result, args.pretty)
    return 0


def cmd_restart_run_all(args: argparse.Namespace) -> int:
    client = _notebook_client(args.path)
    if hasattr(client, "notebook_restart_and_run_all"):
        result = client.notebook_restart_and_run_all(args.path)
    else:
        result = client.restart_and_run_all(args.path)
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
    # Route through core runtime for headless kernel selection
    if not interactive:
        client = _notebook_client(args.path)
        if hasattr(client, "notebook_select_kernel"):
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
        kwargs: dict[str, Any] = {"cells": _read_json_payload(args, field_name="cells")}
        if getattr(args, "session_id", None):
            kwargs["owner_session_id"] = args.session_id
        result = _core_client(workspace_root, runtime_dir=runtime_dir).notebook_project_visible(args.path, **kwargs)
        _out(result, args.pretty)
        return 0

    if args.core_command == "execute-visible-cell":
        kwargs = {"cell_index": args.cell_index, "source": _read_source(args)}
        if getattr(args, "session_id", None):
            kwargs["owner_session_id"] = args.session_id
        result = _core_client(workspace_root, runtime_dir=runtime_dir).notebook_execute_visible_cell(args.path, **kwargs)
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
    p.add_argument("--session-id", help="Collaboration session to attribute the edit to")
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
    p.add_argument("--session-id", help="Collaboration session to attribute the execution to")
    p.add_argument("--cell-id", help="ID of cell to execute")
    p.add_argument("-c", "--code", help="Code to insert and execute")
    p.add_argument("--no-wait", action="store_true", help="Return immediately without waiting for output")
    p.add_argument("--timeout", type=float, default=30, help="Seconds to wait for completion (default: 30)")

    # ix
    p = sub.add_parser("ix", help="Insert and execute code")
    p.add_argument("path")
    p.add_argument("--session-id", help="Collaboration session to attribute the insert/execute to")
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
    p.add_argument("--kernel", help="Python executable path (e.g. /opt/miniconda3/bin/python3 or python3)")

    # kernels
    sub.add_parser("kernels", help="List available notebook kernels")

    # select-kernel
    p = sub.add_parser("select-kernel", help="Select kernel for a notebook")
    p.add_argument("path")
    p.add_argument("--kernel-id", help="Python executable path or name (e.g. /opt/miniconda3/bin/python3 or python3)")
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
        "kernels",
        "select-kernel",
        "prompts",
        "respond",
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
        "kernels": cmd_kernels,
        "select-kernel": cmd_select_kernel,
        "prompts": cmd_prompts,
        "respond": cmd_respond,
        "core": cmd_core,
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
