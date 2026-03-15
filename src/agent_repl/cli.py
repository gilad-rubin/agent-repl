"""CLI entry point — argparse-based subcommands talking to the bridge."""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from agent_repl.client import BridgeClient


def _out(data: Any, pretty: bool = False) -> None:
    print(json.dumps(data, indent=2 if pretty else None))


def _client() -> BridgeClient:
    return BridgeClient.discover()


# ------------------------------------------------------------------
# Subcommand handlers
# ------------------------------------------------------------------

def cmd_cat(args: argparse.Namespace) -> int:
    result = _client().contents(args.path)
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


def cmd_reload(_args: argparse.Namespace) -> int:
    _client().reload()
    print("Extension host restarting...")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    result = _client().status(args.path)
    _out(result, args.pretty)
    return 0


def cmd_edit(args: argparse.Namespace) -> int:
    client = _client()
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

    result = client.edit(args.path, [op])
    _out(result, args.pretty)
    return 0


def cmd_exec(args: argparse.Namespace) -> int:
    client = _client()
    if args.code:
        result = client.insert_and_execute(args.path, args.code)
    elif args.cell_id:
        result = client.execute_cell(args.path, cell_id=args.cell_id)
    else:
        print(json.dumps({"error": "Provide --cell-id or -c/--code"}, indent=2), file=sys.stderr)
        return 1
    _out(result, args.pretty)
    return 0


def cmd_ix(args: argparse.Namespace) -> int:
    source = _read_source(args)
    result = _client().insert_and_execute(args.path, source)
    _out(result, args.pretty)
    return 0


def cmd_run_all(args: argparse.Namespace) -> int:
    result = _client().execute_all(args.path)
    _out(result, args.pretty)
    return 0


def cmd_restart(args: argparse.Namespace) -> int:
    result = _client().restart_kernel(args.path)
    _out(result, args.pretty)
    return 0


def cmd_restart_run_all(args: argparse.Namespace) -> int:
    result = _client().restart_and_run_all(args.path)
    _out(result, args.pretty)
    return 0


def cmd_new(args: argparse.Namespace) -> int:
    cells = None
    if args.cells_json:
        cells = json.loads(args.cells_json)
    result = _client().create(args.path, cells=cells)
    _out(result, args.pretty)
    return 0


def cmd_prompts(args: argparse.Namespace) -> int:
    result = _client().contents(args.path)
    cells = result.get("cells", [])
    prompts = [
        c for c in cells
        if (c.get("metadata") or {}).get("custom", {}).get("agent-repl", {}).get("type") == "prompt"
    ]
    _out({"prompts": prompts}, args.pretty)
    return 0


def cmd_respond(args: argparse.Namespace) -> int:
    client = _client()
    source = _read_source(args)
    # Mark prompt as in-progress
    client.prompt_status(args.path, args.to, "in-progress")
    # Insert response cell and execute
    result = client.insert_and_execute(args.path, source)
    # Mark prompt as answered
    client.prompt_status(args.path, args.to, "answered")
    _out(result, args.pretty)
    return 0


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

    # ix
    p = sub.add_parser("ix", help="Insert and execute code")
    p.add_argument("path")
    p.add_argument("-s", "--source")
    p.add_argument("--source-file")

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

    # prompts
    p = sub.add_parser("prompts", help="List prompt cells")
    p.add_argument("path")

    # respond
    p = sub.add_parser("respond", help="Respond to a prompt cell")
    p.add_argument("path")
    p.add_argument("--to", required=True, help="Prompt cell ID")
    p.add_argument("-s", "--source")
    p.add_argument("--source-file")

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
        "prompts": cmd_prompts,
        "respond": cmd_respond,
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
