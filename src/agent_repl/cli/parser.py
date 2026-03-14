"""CLI argument parser definition."""
from __future__ import annotations

import argparse

from agent_repl.core.models import DEFAULT_EXEC_TIMEOUT, DEFAULT_TIMEOUT
from agent_repl.cli.helpers import (
    _add_cell_selector, _add_live_target_selection, _add_path_positional, _add_server_selection,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="agent-repl: CLI for AI agents to work with live Jupyter notebook kernels.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # --- servers ---
    servers_parser = subparsers.add_parser("servers", help="List discovered running Jupyter servers.")
    servers_parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    servers_parser.add_argument("--pretty", action="store_true")

    # --- notebooks (ls) ---
    notebooks_parser = subparsers.add_parser("notebooks", aliases=["ls"], help="List live notebook sessions.")
    _add_server_selection(notebooks_parser)
    notebooks_parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    notebooks_parser.add_argument("--pretty", action="store_true")

    # --- contents (cat) ---
    contents_parser = subparsers.add_parser("contents", aliases=["cat"], help="Fetch saved notebook contents.")
    _add_server_selection(contents_parser)
    _add_path_positional(contents_parser)
    contents_parser.add_argument("--cells", help="Cell indexes: 0,2,5 or 0-2,4,7-")
    contents_parser.add_argument("--cell-type", choices=["code", "markdown", "raw"])
    contents_parser.add_argument("--detail", choices=["minimal", "brief", "full"], default="brief")
    contents_parser.add_argument("--include-outputs", action="store_true", help="Alias for --detail full")
    contents_parser.add_argument("--raw", action="store_true")
    contents_parser.add_argument("--raw-output", action="store_true", help="Disable media stripping")
    contents_parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    contents_parser.add_argument("--pretty", action="store_true")

    # --- execute (exec) ---
    execute_parser = subparsers.add_parser("execute", aliases=["exec"], help="Execute code in a live notebook kernel.")
    _add_server_selection(execute_parser)
    _add_path_positional(execute_parser)
    _add_live_target_selection(execute_parser)
    execute_parser.add_argument("-c", "--code")
    execute_parser.add_argument("--code-file")
    execute_parser.add_argument("--transport", choices=["auto", "websocket", "zmq"], default="auto")
    execute_parser.add_argument("--timeout", type=float, default=DEFAULT_EXEC_TIMEOUT)
    execute_parser.add_argument("--save-outputs", action="store_true")
    execute_parser.add_argument("--no-save-outputs", action="store_true")
    execute_parser.add_argument("--cell-id")
    execute_parser.add_argument("--raw-output", action="store_true", help="Disable media stripping")
    execute_parser.add_argument("--stream", action="store_true", help="Output events as JSONL in real-time")
    execute_parser.add_argument("--pretty", action="store_true")

    # --- edit ---
    edit_parser = subparsers.add_parser("edit", help="Edit saved notebook cells.")
    _add_server_selection(edit_parser)
    edit_parser.add_argument("path", metavar="PATH", help="Notebook path")
    edit_parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    edit_subparsers = edit_parser.add_subparsers(dest="edit_command", required=True)

    replace_parser = edit_subparsers.add_parser("replace-source", help="Replace cell source.")
    _add_cell_selector(replace_parser)
    replace_parser.add_argument("-s", "--source")
    replace_parser.add_argument("--source-file")
    replace_parser.add_argument("--pretty", action="store_true")

    insert_parser = edit_subparsers.add_parser("insert", help="Insert a new cell.")
    location_group = insert_parser.add_mutually_exclusive_group(required=True)
    location_group.add_argument("--at-index", type=int)
    location_group.add_argument("--before", type=int)
    location_group.add_argument("--after", type=int)
    insert_parser.add_argument("-t", "--cell-type", choices=["code", "markdown", "raw"], required=True)
    insert_parser.add_argument("-s", "--source")
    insert_parser.add_argument("--source-file")
    insert_parser.add_argument("--pretty", action="store_true")

    delete_parser = edit_subparsers.add_parser("delete", help="Delete a cell.")
    _add_cell_selector(delete_parser)
    delete_parser.add_argument("--pretty", action="store_true")

    move_parser = edit_subparsers.add_parser("move", help="Move a cell to a different index.")
    _add_cell_selector(move_parser)
    move_parser.add_argument("--to-index", type=int, required=True)
    move_parser.add_argument("--pretty", action="store_true")

    clear_outputs_parser = edit_subparsers.add_parser("clear-outputs", help="Clear cell outputs.")
    clear_outputs_group = clear_outputs_parser.add_mutually_exclusive_group(required=False)
    clear_outputs_group.add_argument("--all", action="store_true")
    clear_outputs_group.add_argument("--index", type=int)
    clear_outputs_group.add_argument("--cell-id")
    clear_outputs_parser.add_argument("--pretty", action="store_true")

    batch_parser = edit_subparsers.add_parser("batch", help="Apply multiple edit operations atomically.")
    batch_parser.add_argument("--operations")
    batch_parser.add_argument("--operations-file")
    batch_parser.add_argument("--pretty", action="store_true")

    # --- new ---
    new_parser = subparsers.add_parser("new", help="Create a new notebook.")
    _add_server_selection(new_parser)
    _add_path_positional(new_parser)
    new_parser.add_argument("--kernel-name", default="python3")
    new_parser.add_argument("--cells", dest="cells_json", help="JSON array of {type, source}")
    new_parser.add_argument("--cells-file")
    new_parser.add_argument("--no-start-kernel", action="store_true")
    new_parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    new_parser.add_argument("--pretty", action="store_true")

    # --- insert-execute (ix) ---
    ix_parser = subparsers.add_parser("insert-execute", aliases=["ix"], help="Insert a cell and execute it.")
    _add_server_selection(ix_parser)
    _add_path_positional(ix_parser)
    _add_live_target_selection(ix_parser)
    ix_parser.add_argument("--at-index", type=int, default=-1)
    ix_parser.add_argument("-s", "--source")
    ix_parser.add_argument("--source-file")
    ix_parser.add_argument("-t", "--cell-type", default="code", choices=["code", "markdown", "raw"])
    ix_parser.add_argument("--transport", choices=["auto", "websocket", "zmq"], default="auto")
    ix_parser.add_argument("--timeout", type=float, default=DEFAULT_EXEC_TIMEOUT)
    ix_parser.add_argument("--raw-output", action="store_true")
    ix_parser.add_argument("--pretty", action="store_true")

    # --- kernels ---
    kernels_parser = subparsers.add_parser("kernels", help="List available kernelspecs and running kernels.")
    _add_server_selection(kernels_parser)
    kernels_parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    kernels_parser.add_argument("--pretty", action="store_true")

    # --- restart ---
    restart_parser = subparsers.add_parser("restart", help="Restart a live notebook kernel.")
    _add_server_selection(restart_parser)
    _add_path_positional(restart_parser)
    _add_live_target_selection(restart_parser)
    restart_parser.add_argument("--timeout", type=float, default=DEFAULT_EXEC_TIMEOUT)
    restart_parser.add_argument("--pretty", action="store_true")

    # --- run-all ---
    run_all_parser = subparsers.add_parser("run-all", help="Execute all notebook code cells.")
    _add_server_selection(run_all_parser)
    _add_path_positional(run_all_parser)
    _add_live_target_selection(run_all_parser)
    run_all_parser.add_argument("--transport", choices=["auto", "websocket", "zmq"], default="auto")
    run_all_parser.add_argument("--timeout", type=float, default=DEFAULT_EXEC_TIMEOUT)
    run_all_parser.add_argument("--save-outputs", action="store_true")
    run_all_parser.add_argument("--raw-output", action="store_true")
    run_all_parser.add_argument("--skip-tags", help="Skip cells with these tags (comma-separated)")
    run_all_parser.add_argument("--only-tags", help="Only run cells with these tags (comma-separated)")
    run_all_parser.add_argument("--pretty", action="store_true")

    # --- restart-run-all ---
    rra_parser = subparsers.add_parser("restart-run-all", help="Restart kernel and run all cells.")
    _add_server_selection(rra_parser)
    _add_path_positional(rra_parser)
    _add_live_target_selection(rra_parser)
    rra_parser.add_argument("--transport", choices=["auto", "websocket", "zmq"], default="auto")
    rra_parser.add_argument("--timeout", type=float, default=DEFAULT_EXEC_TIMEOUT)
    rra_parser.add_argument("--save-outputs", action="store_true")
    rra_parser.add_argument("--raw-output", action="store_true")
    rra_parser.add_argument("--skip-tags", help="Skip cells with these tags (comma-separated)")
    rra_parser.add_argument("--only-tags", help="Only run cells with these tags (comma-separated)")
    rra_parser.add_argument("--pretty", action="store_true")

    # --- variables (vars) ---
    variables_parser = subparsers.add_parser("variables", aliases=["vars"], help="Inspect live Python-kernel variables.")
    _add_server_selection(variables_parser)
    variables_parser.add_argument("path", metavar="PATH", help="Notebook path")
    _add_live_target_selection(variables_parser)
    variables_parser.add_argument("--transport", choices=["auto", "websocket", "zmq"], default="auto")
    variables_parser.add_argument("--timeout", type=float, default=DEFAULT_EXEC_TIMEOUT)
    variables_subparsers = variables_parser.add_subparsers(dest="variables_command", required=True)

    variables_list_parser = variables_subparsers.add_parser("list", help="List live variables.")
    variables_list_parser.add_argument("--limit", type=int, default=25)
    variables_list_parser.add_argument("--include-private", action="store_true")
    variables_list_parser.add_argument("--include-callables", action="store_true")
    variables_list_parser.add_argument("--pretty", action="store_true")

    variables_preview_parser = variables_subparsers.add_parser("preview", help="Preview a live variable.")
    variables_preview_parser.add_argument("--name", required=True)
    variables_preview_parser.add_argument("--max-chars", type=int, default=400)
    variables_preview_parser.add_argument("--pretty", action="store_true")

    # --- start ---
    start_parser = subparsers.add_parser("start", help="Launch JupyterLab with no-auth flags.")
    start_parser.add_argument("--dir", default=".", help="Root directory for JupyterLab")
    start_parser.add_argument("--foreground", action="store_true", help="Don't background the process")
    start_parser.add_argument("--port", type=int, help="Port to run on")

    # --- prompts ---
    prompts_parser = subparsers.add_parser("prompts", help="List agent prompt cells in a notebook.")
    _add_server_selection(prompts_parser)
    _add_path_positional(prompts_parser)
    prompts_parser.add_argument("--all", action="store_true", help="Include answered prompts")
    prompts_parser.add_argument("--context", type=int, default=1, help="Number of context cells above/below")
    prompts_parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    prompts_parser.add_argument("--pretty", action="store_true")

    # --- respond ---
    respond_parser = subparsers.add_parser("respond", help="Respond to an agent prompt cell.")
    _add_server_selection(respond_parser)
    _add_path_positional(respond_parser)
    _add_live_target_selection(respond_parser)
    respond_parser.add_argument("--to", required=True, dest="prompt_cell_id", help="Cell ID of the prompt to respond to")
    respond_parser.add_argument("-s", "--source")
    respond_parser.add_argument("--source-file")
    respond_parser.add_argument("-t", "--cell-type", default="code", choices=["code", "markdown", "raw"])
    respond_parser.add_argument("--transport", choices=["auto", "websocket", "zmq"], default="auto")
    respond_parser.add_argument("--timeout", type=float, default=DEFAULT_EXEC_TIMEOUT)
    respond_parser.add_argument("--raw-output", action="store_true")
    respond_parser.add_argument("--pretty", action="store_true")

    # --- watch ---
    watch_parser = subparsers.add_parser("watch", help="Watch notebook for new agent prompts.")
    _add_server_selection(watch_parser)
    _add_path_positional(watch_parser)
    watch_parser.add_argument("--interval", type=float, default=2.0, help="Poll interval in seconds")
    watch_parser.add_argument("--once", action="store_true", help="Check once and exit")
    watch_parser.add_argument("--context", type=int, default=1, help="Context cells above/below each prompt")
    watch_parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)

    # --- context ---
    context_parser = subparsers.add_parser("context", help="Snapshot kernel + notebook state for agents.")
    _add_server_selection(context_parser)
    _add_path_positional(context_parser)
    _add_live_target_selection(context_parser)
    context_parser.add_argument("--include-outputs", action="store_true")
    context_parser.add_argument("--transport", choices=["auto", "websocket", "zmq"], default="auto")
    context_parser.add_argument("--timeout", type=float, default=DEFAULT_EXEC_TIMEOUT)
    context_parser.add_argument("--pretty", action="store_true")

    # --- clean ---
    clean_parser = subparsers.add_parser("clean", help="Strip outputs for clean git diffs.")
    _add_server_selection(clean_parser)
    _add_path_positional(clean_parser)
    clean_parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)

    # --- git-setup ---
    git_setup_parser = subparsers.add_parser("git-setup", help="Configure git filters for notebooks.")

    return parser
